"""API tests for response_alert_middleware against the real server.app.

Uses ``httpx.AsyncClient(transport=ASGITransport(app=app))`` so the full
middleware stack — CORS, request_id, tenant_context, RunStep, and our new
response_alert — is exercised end-to-end without booting a separate
server or running the lifespan (no DB/Redis required).

Alerts are observed by swapping ``alerting._sns_client`` for a recording
double; the real boto3 SNS client is never constructed.
"""

from __future__ import annotations

import pytest

# server.app imports engine (asyncpg) at module load. Skip cleanly if the
# backend dep isn't installed in this env.
pytest.importorskip("asyncpg")

import httpx
from httpx import ASGITransport

from server.app import app
from server.core import alerting
from server.core.alerting import reset_dispatcher_for_tests
from server.core.config import settings
from server.core.middleware import response_alert_middleware


class _RecordingSNSClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def publish(self, topic_arn: str, message: str) -> None:
        self.calls.append((topic_arn, message))


@pytest.fixture(autouse=True)
def _wire_alerter(monkeypatch: pytest.MonkeyPatch):
    reset_dispatcher_for_tests()
    recording = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", recording)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:aws:sns:test:topic")
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 0)
    yield recording
    reset_dispatcher_for_tests()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


# ---------------------------------------------------------------------------
# Production wiring: the middleware must actually be registered on the real
# app object and in the correct position relative to existing middlewares.
# ---------------------------------------------------------------------------


def test_alerter_is_registered_on_real_app() -> None:
    dispatches = [
        m.kwargs.get("dispatch") for m in app.user_middleware
        if m.kwargs.get("dispatch") is not None
    ]
    assert response_alert_middleware in dispatches, (
        "response_alert_middleware is not registered on server.app"
    )


def test_alerter_is_outermost_http_middleware() -> None:
    """Last-added-first in user_middleware = outermost wrapper on egress.

    response_alert_middleware must be registered AFTER request_id and
    tenant_context so it observes the final response and can read request.state.
    """
    http_dispatches = [
        m.kwargs.get("dispatch") for m in app.user_middleware
        if m.kwargs.get("dispatch") is not None
    ]
    # Starlette inserts each new middleware at position 0, so last-registered
    # sits at index 0 of user_middleware.
    assert http_dispatches[0] is response_alert_middleware, (
        f"Expected response_alert_middleware at index 0, "
        f"got {http_dispatches[0].__name__}"
    )


# ---------------------------------------------------------------------------
# Live requests against the full ASGI stack
# ---------------------------------------------------------------------------


async def test_health_check_does_not_alert(
    _wire_alerter: _RecordingSNSClient,
) -> None:
    async with _client() as ac:
        r = await ac.get("/health")
    assert r.status_code == 200
    assert _wire_alerter.calls == []


async def test_unknown_path_triggers_single_alert(
    _wire_alerter: _RecordingSNSClient,
) -> None:
    async with _client() as ac:
        r = await ac.get("/this-path-does-not-exist-xyz")
    assert r.status_code == 404
    assert len(_wire_alerter.calls) == 1
    body = _wire_alerter.calls[0][1]
    assert '"Status": 404' in body
    assert '"Path": "/this-path-does-not-exist-xyz"' in body


async def test_x_request_id_header_present_on_404(
    _wire_alerter: _RecordingSNSClient,
) -> None:
    async with _client() as ac:
        r = await ac.get("/this-path-does-not-exist-abc")
    assert r.status_code == 404
    assert "x-request-id" in {k.lower() for k in r.headers.keys()}


async def test_x_request_id_echoed_back_when_supplied(
    _wire_alerter: _RecordingSNSClient,
) -> None:
    async with _client() as ac:
        r = await ac.get(
            "/health",
            headers={"X-Request-ID": "client-supplied-42"},
        )
    assert r.headers.get("X-Request-ID") == "client-supplied-42"


async def test_dedup_window_collapses_repeated_404s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With dedup on, three identical 404s should produce exactly one alert."""
    reset_dispatcher_for_tests()
    recording = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", recording)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 60)

    async with _client() as ac:
        await ac.get("/still-nope")
        await ac.get("/still-nope")
        await ac.get("/still-nope")

    assert len(recording.calls) == 1
    reset_dispatcher_for_tests()


async def test_dev_environment_suppresses_alerts_even_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: ENVIRONMENT=development must not publish to SNS."""
    reset_dispatcher_for_tests()
    recording = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", recording)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 0)

    async with _client() as ac:
        r = await ac.get("/nonexistent-dev-check")

    assert r.status_code == 404
    # Middleware still ran, dispatcher still scheduled, but publish_alert
    # short-circuited on the env gate.
    assert recording.calls == []
    reset_dispatcher_for_tests()


async def test_empty_topic_arn_suppresses_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_dispatcher_for_tests()
    recording = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", recording)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "")
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 0)

    async with _client() as ac:
        r = await ac.get("/nonexistent-empty-arn-check")

    assert r.status_code == 404
    assert recording.calls == []
    reset_dispatcher_for_tests()


async def test_tenant_header_flows_into_alert_payload(
    _wire_alerter: _RecordingSNSClient,
) -> None:
    async with _client() as ac:
        r = await ac.get(
            "/nonexistent-tenant-check",
            headers={"X-Tenant-Id": "t_integration", "X-User-Id": "u_integration"},
        )
    assert r.status_code == 404
    assert len(_wire_alerter.calls) == 1
    body = _wire_alerter.calls[0][1]
    assert '"Tenant ID": "t_integration"' in body
    assert '"User ID": "u_integration"' in body
