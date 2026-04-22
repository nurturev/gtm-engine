"""Backward-compatibility tests for response_alert_middleware.

HLD §17 / LLD §16a mandate that the new middleware is **purely
observational** — status codes, response bodies, response headers, and the
existing X-Request-Id behaviour must all be byte-identical whether or not
the middleware is registered. These tests lock that invariant into CI.

Also includes a conservative perf-overhead check on the 2xx happy path.
"""

from __future__ import annotations

import time
from typing import Callable

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from server.core import alerting
from server.core.alerting import reset_dispatcher_for_tests
from server.core.config import settings
from server.core.middleware import (
    request_id_middleware,
    response_alert_middleware,
    tenant_context_middleware,
)


class _NoopSNS:
    def publish(self, topic_arn: str, message: str) -> None:  # noqa: D401
        return


def _register_routes(app: FastAPI) -> None:
    @app.get("/ok")
    async def ok() -> dict:
        return {"ok": True, "v": 1}

    @app.get("/http400")
    async def http400() -> dict:
        raise HTTPException(status_code=400, detail="bad input")

    @app.get("/jsonresp403")
    async def jsonresp403() -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    @app.get("/boom")
    async def boom() -> dict:
        raise ValueError("unhandled explosion")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}


def _build_app_without_alerter() -> FastAPI:
    """Baseline: only the pre-existing middlewares."""
    app = FastAPI()
    app.middleware("http")(request_id_middleware)
    app.middleware("http")(tenant_context_middleware)
    _register_routes(app)
    return app


def _build_app_with_alerter() -> FastAPI:
    """Production order: response_alert_middleware added last (outermost)."""
    app = FastAPI()
    app.middleware("http")(request_id_middleware)
    app.middleware("http")(tenant_context_middleware)
    app.middleware("http")(response_alert_middleware)
    _register_routes(app)
    return app


@pytest.fixture(autouse=True)
def _silence_sns(monkeypatch: pytest.MonkeyPatch):
    """Keep the alerter wired but route SNS to a no-op so we don't care about
    alerts in these tests — we're asserting the user-visible response."""
    reset_dispatcher_for_tests()
    monkeypatch.setattr(alerting, "_sns_client", _NoopSNS())
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 0)
    yield
    reset_dispatcher_for_tests()


def _clients() -> tuple[TestClient, TestClient]:
    baseline = TestClient(
        _build_app_without_alerter(), raise_server_exceptions=False,
    )
    alerted = TestClient(
        _build_app_with_alerter(), raise_server_exceptions=False,
    )
    return baseline, alerted


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/ok"),
        ("GET", "/http400"),
        ("GET", "/jsonresp403"),
        ("GET", "/boom"),
        ("GET", "/health"),
    ],
)
def test_status_body_and_headers_match_baseline(method: str, path: str) -> None:
    baseline, alerted = _clients()
    # Pin X-Request-ID so generated UUIDs don't cause spurious header diffs.
    headers = {"X-Request-ID": "fixed-req-id-for-compat-test"}

    r_baseline = baseline.request(method, path, headers=headers)
    r_alerted = alerted.request(method, path, headers=headers)

    assert r_baseline.status_code == r_alerted.status_code, (
        f"status differs for {method} {path}"
    )
    assert r_baseline.content == r_alerted.content, (
        f"body differs for {method} {path}"
    )

    # Header comparison is case-insensitive and ignores stateful server
    # headers (date, server) that legitimately vary between runs.
    def _normalise(headers) -> dict[str, str]:
        ignored = {"date", "server", "content-length"}
        return {
            k.lower(): v for k, v in headers.items()
            if k.lower() not in ignored
        }

    assert _normalise(r_baseline.headers) == _normalise(r_alerted.headers), (
        f"header set differs for {method} {path}"
    )


def test_x_request_id_header_present_on_both() -> None:
    baseline, alerted = _clients()
    r_baseline = baseline.get("/ok")
    r_alerted = alerted.get("/ok")
    assert "x-request-id" in {k.lower() for k in r_baseline.headers.keys()}
    assert "x-request-id" in {k.lower() for k in r_alerted.headers.keys()}


def test_x_request_id_supplied_by_client_is_echoed_back() -> None:
    _baseline, alerted = _clients()
    r = alerted.get("/ok", headers={"X-Request-ID": "req-xyz-42"})
    assert r.headers.get("X-Request-ID") == "req-xyz-42"


def test_exception_handling_unchanged_by_alerter() -> None:
    baseline, alerted = _clients()
    r_baseline = baseline.get("/boom")
    r_alerted = alerted.get("/boom")
    assert r_baseline.status_code == r_alerted.status_code == 500


def test_p99_overhead_on_2xx_happy_path_under_budget() -> None:
    """Latency guardrail: alerter adds <5 ms p99 on the 2xx path.

    Budget is generous vs the design target (<50 µs) because TestClient runs
    the full ASGI stack in-process — some jitter is expected on CI hosts.
    """
    baseline, alerted = _clients()
    iterations = 200

    def _p99(samples: list[float]) -> float:
        samples = sorted(samples)
        idx = max(0, int(len(samples) * 0.99) - 1)
        return samples[idx]

    def _measure(client: TestClient) -> float:
        times: list[float] = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            r = client.get("/ok")
            times.append((time.perf_counter() - t0) * 1000)
            assert r.status_code == 200
        return _p99(times)

    baseline_p99 = _measure(baseline)
    alerted_p99 = _measure(alerted)
    overhead_ms = alerted_p99 - baseline_p99

    # Guardrail: overhead MUST be below 5 ms. If this fails we have likely
    # introduced accidental blocking work on the hot path.
    assert overhead_ms < 5.0, (
        f"alerter overhead p99={overhead_ms:.2f}ms "
        f"(baseline={baseline_p99:.2f}ms, alerted={alerted_p99:.2f}ms)"
    )
