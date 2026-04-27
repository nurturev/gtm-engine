"""Unit tests for server.core.alerting.

Covers the pure predicates (``is_alertable``, ``is_excluded_path``),
the payload builder (truncations, streaming-body handling, contextvars),
the dedup state machine on ``AlertDispatcher``, and the env/topic gates on
``publish_alert``. No network, no FastAPI app — pure function tests plus
a recording double for SNS.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from server.core import alerting
from server.core.alerting import (
    AlertDispatcher,
    SlackMessageType,
    build_alert_payload,
    is_alertable,
    is_excluded_path,
    publish_alert,
    reset_dispatcher_for_tests,
)
from server.core.config import settings
from server.core.middleware import (
    request_id_var,
    tenant_id_var,
    trace_id_var,
    user_id_var,
)

# asyncio_mode=auto in pyproject.toml — async tests pick up the mark implicitly.


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


def _fake_request(
    method: str = "POST",
    path: str = "/api/v1/enrich/person",
    query: str = "id=abc",
) -> Any:
    """Minimal stand-in for starlette.Request used by the payload builder.

    ``build_alert_payload`` touches ``.method``, ``.url.path``, and
    ``.query_params`` — so we only ship those to keep tests independent of
    starlette internals.
    """
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        query_params=query,
    )


def _fake_response(status: int = 500, body: bytes | None = b'{"ok":false}') -> Any:
    return SimpleNamespace(status_code=status, body=body)


def _streaming_response(status: int = 500) -> Any:
    """Stand-in for ``starlette.responses.StreamingResponse`` — no ``.body``."""
    return SimpleNamespace(status_code=status, body_iterator=iter([b""]))


class _RecordingSNSClient:
    """SNSClient double — records ``publish`` calls, optionally raises."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._raises = raises

    def publish(self, topic_arn: str, message: str) -> None:
        self.calls.append((topic_arn, message))
        if self._raises is not None:
            raise self._raises


@pytest.fixture(autouse=True)
def _reset_context_and_dispatcher(monkeypatch: pytest.MonkeyPatch):
    """Ensure every test starts with a clean contextvar + dispatcher state."""
    reset_dispatcher_for_tests()
    # Contextvars persist across tests in the same event loop; explicit reset.
    request_id_var.set(None)
    trace_id_var.set(None)
    tenant_id_var.set(None)
    user_id_var.set(None)
    yield
    reset_dispatcher_for_tests()


# ---------------------------------------------------------------------------
# is_alertable
# ---------------------------------------------------------------------------


def test_is_alertable_2xx_false() -> None:
    assert is_alertable(200, None) is False


def test_is_alertable_3xx_false() -> None:
    assert is_alertable(302, None) is False
    assert is_alertable(304, None) is False


def test_is_alertable_4xx_true() -> None:
    assert is_alertable(400, None) is True
    assert is_alertable(404, None) is True
    assert is_alertable(422, None) is True


def test_is_alertable_5xx_true() -> None:
    assert is_alertable(500, None) is True
    assert is_alertable(503, None) is True


def test_is_alertable_exception_true() -> None:
    assert is_alertable(200, ValueError("boom")) is True


def test_is_excluded_path_hits_known_prefixes() -> None:
    for path in (
        "/health", "/healthz", "/docs", "/redoc", "/openapi.json",
        "/console/static/app.css", "/sites/abc/index.html", "/d/token",
    ):
        assert is_excluded_path(path) is True


def test_is_excluded_path_misses_normal_paths() -> None:
    assert is_excluded_path("/api/v1/enrich/person") is False
    assert is_excluded_path("/") is False


# ---------------------------------------------------------------------------
# build_alert_payload
# ---------------------------------------------------------------------------


def test_build_payload_minimal_fields_present() -> None:
    payload = build_alert_payload(
        request=_fake_request(), response=_fake_response(status=500),
        exc=None, duration_ms=42,
    )
    for key in (
        "Source", "Environment", "Method", "Path", "Query", "Status",
        "Duration_ms", "Error", "Origin", "Response_body",
        "Request ID", "Trace ID", "Tenant ID", "User ID", "Timestamp",
    ):
        assert key in payload
    assert payload["Source"] == "gtm-engine"
    assert payload["Method"] == "POST"
    assert payload["Status"] == 500
    assert payload["Duration_ms"] == 42


def test_build_payload_truncates_query() -> None:
    long_query = "x=" + "a" * 400
    payload = build_alert_payload(
        request=_fake_request(query=long_query), response=_fake_response(),
        exc=None, duration_ms=1,
    )
    assert len(payload["Query"]) == 250


def test_build_payload_truncates_error() -> None:
    exc = ValueError("err " + "z" * 400)
    payload = build_alert_payload(
        request=_fake_request(), response=None,
        exc=exc, duration_ms=1,
    )
    assert payload["Error"] is not None
    assert len(payload["Error"]) == 250


def test_build_payload_truncates_body_to_byte_cap() -> None:
    big_body = b"x" * 2000
    payload = build_alert_payload(
        request=_fake_request(), response=_fake_response(body=big_body),
        exc=None, duration_ms=1,
    )
    assert payload["Response_body"] is not None
    # Default cap is 500 bytes; ASCII → 500 chars after decode.
    assert len(payload["Response_body"]) == 500


def test_build_payload_streaming_omits_body() -> None:
    payload = build_alert_payload(
        request=_fake_request(), response=_streaming_response(status=500),
        exc=None, duration_ms=1,
    )
    assert payload["Response_body"] is None


def test_build_payload_non_utf8_body_replaces() -> None:
    payload = build_alert_payload(
        request=_fake_request(), response=_fake_response(body=b"\xff\xfe\xfd"),
        exc=None, duration_ms=1,
    )
    assert payload["Response_body"] is not None
    # Replacement character(s) present — no crash, no empty string.
    assert "�" in payload["Response_body"]


def test_build_payload_body_preview_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ALERT_BODY_PREVIEW_BYTES", 0)
    payload = build_alert_payload(
        request=_fake_request(), response=_fake_response(body=b"hello"),
        exc=None, duration_ms=1,
    )
    assert payload["Response_body"] is None


def test_build_payload_contextvars_propagate() -> None:
    request_id_var.set("req-123")
    tenant_id_var.set("t_abc")
    user_id_var.set("u_xyz")
    payload = build_alert_payload(
        request=_fake_request(), response=_fake_response(),
        exc=None, duration_ms=1,
    )
    assert payload["Request ID"] == "req-123"
    assert payload["Tenant ID"] == "t_abc"
    assert payload["User ID"] == "u_xyz"
    # trace_id falls back to request_id when trace_id_var is unset.
    assert payload["Trace ID"] == "req-123"


def test_build_payload_exception_origin_present() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        payload = build_alert_payload(
            request=_fake_request(), response=None,
            exc=exc, duration_ms=1,
        )
    assert payload["Origin"] is not None
    assert ":" in payload["Origin"]


def test_build_payload_status_defaults_to_500_when_no_response() -> None:
    payload = build_alert_payload(
        request=_fake_request(), response=None,
        exc=ValueError("x"), duration_ms=1,
    )
    assert payload["Status"] == 500


# ---------------------------------------------------------------------------
# AlertDispatcher — dedup
# ---------------------------------------------------------------------------


async def _dispatch_once(
    dispatcher: AlertDispatcher, sns: _RecordingSNSClient,
    monkeypatch: pytest.MonkeyPatch,
    status: int = 500, path: str = "/a",
) -> None:
    monkeypatch.setattr(alerting, "_sns_client", sns)
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:aws:sns:test:topic")
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    await dispatcher.schedule(
        request=_fake_request(path=path),
        response=_fake_response(status=status),
        exc=None, duration_ms=1,
    )
    # Drain the created task so the SNS recording call is visible.
    await dispatcher.close()


async def test_dedup_first_call_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher = AlertDispatcher()
    sns = _RecordingSNSClient()
    await _dispatch_once(dispatcher, sns, monkeypatch)
    assert len(sns.calls) == 1


async def test_dedup_second_call_within_window_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 60)
    dispatcher = AlertDispatcher()
    sns = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", sns)
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")

    await dispatcher.schedule(
        request=_fake_request(path="/a"),
        response=_fake_response(status=500),
        exc=None, duration_ms=1,
    )
    await dispatcher.schedule(
        request=_fake_request(path="/a"),
        response=_fake_response(status=500),
        exc=None, duration_ms=1,
    )
    await dispatcher.close()
    assert len(sns.calls) == 1


async def test_dedup_different_keys_both_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 60)
    dispatcher = AlertDispatcher()
    sns = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", sns)
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")

    await dispatcher.schedule(
        request=_fake_request(path="/a"), response=_fake_response(500),
        exc=None, duration_ms=1,
    )
    await dispatcher.schedule(
        request=_fake_request(path="/b"), response=_fake_response(500),
        exc=None, duration_ms=1,
    )
    await dispatcher.schedule(
        request=_fake_request(path="/a"), response=_fake_response(404),
        exc=None, duration_ms=1,
    )
    await dispatcher.close()
    assert len(sns.calls) == 3


async def test_dedup_disabled_always_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 0)
    dispatcher = AlertDispatcher()
    sns = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", sns)
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")

    for _ in range(3):
        await dispatcher.schedule(
            request=_fake_request(path="/a"),
            response=_fake_response(500), exc=None, duration_ms=1,
        )
    await dispatcher.close()
    assert len(sns.calls) == 3


async def test_dedup_evicts_expired_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ALERT_DEDUP_WINDOW_SECONDS", 60)
    dispatcher = AlertDispatcher()
    sns = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", sns)
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")

    # First dispatch populates the cache.
    await dispatcher.schedule(
        request=_fake_request(path="/a"), response=_fake_response(500),
        exc=None, duration_ms=1,
    )
    # Forge an expired entry by rewriting the stored timestamp.
    key = "/a|500|"
    assert key in dispatcher._seen
    dispatcher._seen[key] = -1e9  # pretend it was inserted ages ago

    await dispatcher.schedule(
        request=_fake_request(path="/a"), response=_fake_response(500),
        exc=None, duration_ms=1,
    )
    await dispatcher.close()
    assert len(sns.calls) == 2


# ---------------------------------------------------------------------------
# publish_alert — env / arn gating + failure swallowing
# ---------------------------------------------------------------------------


async def test_publish_alert_dev_environment_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sns = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", sns)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")

    await publish_alert({"k": "v"})
    assert sns.calls == []


async def test_publish_alert_empty_topic_arn_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sns = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", sns)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "")

    await publish_alert({"k": "v"})
    assert sns.calls == []


async def test_publish_alert_publishes_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sns = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", sns)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:aws:sns:prod:x")

    await publish_alert({"Path": "/a"}, title="custom-title")
    assert len(sns.calls) == 1
    topic, body = sns.calls[0]
    assert topic == "arn:aws:sns:prod:x"
    assert "custom-title" in body
    assert '"message_type": "ALERT"' in body


async def test_publish_alert_audit_type_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sns = _RecordingSNSClient()
    monkeypatch.setattr(alerting, "_sns_client", sns)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")

    await publish_alert({"k": "v"}, msg_type=SlackMessageType.AUDIT)
    assert len(sns.calls) == 1
    assert '"message_type": "AUDIT"' in sns.calls[0][1]


async def test_publish_alert_failure_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sns = _RecordingSNSClient(raises=RuntimeError("sns down"))
    monkeypatch.setattr(alerting, "_sns_client", sns)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "SLACK_ALERT_TOPIC_ARN", "arn:test")

    # Must not raise.
    await publish_alert({"k": "v"})

    assert any(
        "alerter.error phase=publish" in rec.getMessage()
        for rec in caplog.records
    )
