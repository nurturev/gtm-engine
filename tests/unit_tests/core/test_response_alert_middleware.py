"""End-to-end tests for response_alert_middleware via Starlette TestClient.

A minimal FastAPI app is wired with only the pieces under test:
request_id_middleware, tenant_context_middleware, response_alert_middleware,
plus a handful of routes exercising happy / 4xx / 5xx / streaming /
excluded paths. Alerts are observed by swapping ``alerting._sns_client`` for
a recording double — no boto3, no network.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.testclient import TestClient

from server.core import alerting
from server.core.alerting import reset_dispatcher_for_tests
from server.core.config import settings
from server.core.middleware import (
    request_id_middleware,
    response_alert_middleware,
    tenant_context_middleware,
)


class _RecordingSNSClient:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._raises = raises

    def publish(self, topic_arn: str, message: str) -> None:
        self.calls.append((topic_arn, message))
        if self._raises is not None:
            raise self._raises


def _build_app() -> FastAPI:
    """Minimal FastAPI app registering only the middlewares we care about."""
    app = FastAPI()

    # Registration order mirrors production: last-added = outermost.
    app.middleware("http")(request_id_middleware)
    app.middleware("http")(tenant_context_middleware)
    app.middleware("http")(response_alert_middleware)

    @app.get("/ok")
    async def ok() -> dict:
        return {"ok": True}

    @app.get("/redirect")
    async def redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/ok", status_code=302)

    @app.get("/http400")
    async def http400() -> dict:
        raise HTTPException(status_code=400, detail="bad input")

    @app.get("/jsonresp403")
    async def jsonresp403() -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    @app.get("/boom")
    async def boom() -> dict:
        raise ValueError("unhandled explosion")

    @app.get("/stream500")
    async def stream500() -> StreamingResponse:
        async def gen():
            yield b"partial\n"

        return StreamingResponse(gen(), status_code=500)

    @app.get("/health")
    async def health() -> dict:
        # Force a 500 on health — we must still NOT alert.
        raise HTTPException(status_code=500, detail="probe fail")

    return app


@pytest.fixture
def sns(monkeypatch: pytest.MonkeyPatch) -> _RecordingSNSClient:
    reset_dispatcher_for_tests()
    recording = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", recording)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:aws:sns:test:topic")
    # Disable dedup so independent tests don't collide on path/status keys.
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 0)
    yield recording
    reset_dispatcher_for_tests()


@pytest.fixture
def client() -> TestClient:
    # raise_server_exceptions=False so a handler raising ValueError surfaces
    # as a 500 response to the client (matching ASGI/Starlette production
    # behaviour) rather than being re-raised by TestClient.
    return TestClient(_build_app(), raise_server_exceptions=False)


def test_2xx_does_not_alert(sns: _RecordingSNSClient, client: TestClient) -> None:
    r = client.get("/ok")
    assert r.status_code == 200
    assert sns.calls == []


def test_3xx_does_not_alert(sns: _RecordingSNSClient, client: TestClient) -> None:
    r = client.get("/redirect", follow_redirects=False)
    assert r.status_code == 302
    assert sns.calls == []


def test_http_exception_alerts(sns: _RecordingSNSClient, client: TestClient) -> None:
    r = client.get("/http400")
    assert r.status_code == 400
    assert len(sns.calls) == 1
    _, body = sns.calls[0]
    assert '"Status": 400' in body
    assert '"Path": "/http400"' in body


def test_jsonresponse_403_alerts(
    sns: _RecordingSNSClient, client: TestClient,
) -> None:
    """This is the case workflow_studio's exception-only alerter misses."""
    r = client.get("/jsonresp403")
    assert r.status_code == 403
    assert len(sns.calls) == 1
    assert '"Status": 403' in sns.calls[0][1]


def test_raised_exception_alerts_and_client_gets_500(
    sns: _RecordingSNSClient, client: TestClient,
) -> None:
    r = client.get("/boom")
    assert r.status_code == 500
    assert len(sns.calls) == 1
    body = sns.calls[0][1]
    assert '"Status": 500' in body
    assert "ValueError" in body or "unhandled explosion" in body


def test_streaming_500_alerts_without_body_preview(
    sns: _RecordingSNSClient, client: TestClient,
) -> None:
    r = client.get("/stream500")
    assert r.status_code == 500
    assert len(sns.calls) == 1
    body = sns.calls[0][1]
    assert '"Response_body": null' in body


def test_health_failure_is_not_alerted(
    sns: _RecordingSNSClient, client: TestClient,
) -> None:
    r = client.get("/health")
    assert r.status_code == 500
    assert sns.calls == []


def test_options_is_not_alerted(
    sns: _RecordingSNSClient, client: TestClient,
) -> None:
    r = client.options("/http400")
    # CORS / method not allowed — status may be 405, but we must not alert.
    assert sns.calls == []


def test_dedup_window_suppresses_repeats(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    reset_dispatcher_for_tests()
    recording = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", recording)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 60)

    client.get("/http400")
    client.get("/http400")
    client.get("/http400")

    assert len(recording.calls) == 1
    reset_dispatcher_for_tests()


def test_sns_publish_failure_does_not_break_response(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    reset_dispatcher_for_tests()
    failing = _RecordingSNSClient(raises=RuntimeError("sns unreachable"))
    monkeypatch.setattr(alerting, "_sns_client", failing)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 0)

    r = client.get("/http400")

    # Client still receives the expected 400 even though SNS blew up.
    assert r.status_code == 400
    reset_dispatcher_for_tests()


def test_request_id_header_preserved_on_failing_response(
    sns: _RecordingSNSClient, client: TestClient,
) -> None:
    r = client.get("/http400")
    assert "x-request-id" in {k.lower() for k in r.headers.keys()}


def test_contextvar_ids_flow_into_payload(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    reset_dispatcher_for_tests()
    recording = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", recording)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 0)

    r = client.get(
        "/http400",
        headers={"X-Tenant-Id": "t_abc", "X-User-Id": "u_xyz"},
    )
    assert r.status_code == 400
    assert len(recording.calls) == 1
    body = recording.calls[0][1]
    assert '"Tenant ID": "t_abc"' in body
    assert '"User ID": "u_xyz"' in body
    reset_dispatcher_for_tests()
