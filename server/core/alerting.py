"""Failure alerting: SNS → Slack.

Ingress middleware dispatches here whenever a response is non-2xx or a
handler raises. We build a structured payload, apply a short in-process
dedup window, and publish asynchronously to an SNS topic. The topic's
Slack subscription fans the message out to the ops channel.

The publish path never blocks the user's request and never propagates
errors back to the middleware — any alerter failure is logged via the
structured JSON formatter and swallowed.

Field naming in the payload intentionally matches the workflow_studio
convention (``Method``, ``Path``, ``Query``, ``Error``, ``Origin``,
``Request ID``, ``Trace ID``) so the existing SNS→Slack formatter on the
subscriber side works unchanged.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import time
import traceback
from collections import OrderedDict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from server.core.config import settings

if TYPE_CHECKING:
    from fastapi import Request, Response

logger = logging.getLogger(__name__)

_SOURCE = "gtm-engine"
_ENVELOPE_TITLE = "gtm-engine API error"
_ENVS_THAT_ALERT = {"staging", "production"}
_EXCLUDED_PATH_PREFIXES = (
    "/health",
    "/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/console/static",
    "/sites/",
    "/d/",
)
_QUERY_TRUNC = 250
_ERROR_TRUNC = 250
_DEDUP_MAX_ENTRIES = 1000
_CLOSE_DRAIN_TIMEOUT_S = 2.0


class SlackMessageType(str, enum.Enum):
    """SNS envelope ``message_type`` values.

    ``AUDIT`` is reserved for the upcoming async request/response flow; only
    ``ALERT`` is used by this middleware today.
    """

    ALERT = "ALERT"
    AUDIT = "AUDIT"


# ---------------------------------------------------------------------------
# SNS client — lazy per-worker init so gunicorn --preload doesn't share a
# boto3 socket pool across forked workers.
# ---------------------------------------------------------------------------


class SNSClient:
    """Thin boto3 SNS wrapper with lazy first-use init per worker."""

    _client = None

    def publish(self, topic_arn: str, message: str) -> None:
        client = self._get_or_init_client()
        if client is None:
            return
        client.publish(TopicArn=topic_arn, Message=message)

    def _get_or_init_client(self):  # type: ignore[no-untyped-def]
        if SNSClient._client is not None:
            return SNSClient._client
        try:
            import boto3  # local import keeps boto3 out of cold-start path
            SNSClient._client = boto3.client(
                "sns", region_name=settings.AWS_REGION,
            )
        except Exception as exc:
            logger.error(
                "alerter.error phase=init error.type=%s error.message=%s",
                type(exc).__name__, str(exc),
            )
            return None
        return SNSClient._client


_sns_client = SNSClient()


def _is_alerting_enabled() -> bool:
    return (
        settings.ENVIRONMENT in _ENVS_THAT_ALERT
        and bool(settings.SLACK_ALERT_TOPIC_ARN)
    )


async def publish_alert(
    message: dict,
    title: str = _ENVELOPE_TITLE,
    msg_type: SlackMessageType = SlackMessageType.ALERT,
) -> None:
    """JSON-encode the SNS envelope and publish off the event loop.

    Short-circuits silently when the environment is not one that alerts or
    when the topic ARN is unset. Swallows every publish error after logging
    so the caller (a done-callback) never raises into the loop.
    """
    if not _is_alerting_enabled():
        return
    envelope = {
        "message_type": msg_type.value,
        "message_title": title,
        "message": message,
    }
    try:
        body = json.dumps(envelope, indent=4, default=str)
    except Exception as exc:
        logger.error(
            "alerter.error phase=build error.type=%s error.message=%s",
            type(exc).__name__, str(exc),
        )
        return
    topic_arn = settings.SLACK_ALERT_TOPIC_ARN
    try:
        await asyncio.to_thread(_sns_client.publish, topic_arn, body)
    except Exception as exc:
        logger.error(
            "alerter.error phase=publish error.type=%s error.message=%s",
            type(exc).__name__, str(exc),
        )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def is_alertable(status: int, exc: BaseException | None) -> bool:
    """Return True for non-2xx/3xx responses or any exception."""
    if exc is not None:
        return True
    return not (200 <= status < 400)


def is_excluded_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _EXCLUDED_PATH_PREFIXES)


def build_alert_payload(
    request: "Request",
    response: "Response | None",
    exc: BaseException | None,
    duration_ms: int,
) -> dict:
    """Build the SNS payload dict per HLD §7 / LLD §4.1.

    Reads contextvars for request/trace/tenant/user IDs. Never raises —
    callers should treat any exception here as a build-phase alerter error.
    """
    # Late import to avoid a circular reference with middleware.py, which
    # imports this module to register response_alert_middleware.
    from server.core.middleware import (
        request_id_var,
        tenant_id_var,
        trace_id_var,
        user_id_var,
    )

    # Starlette's BaseHTTPMiddleware runs each wrapper in its own anyio task,
    # so ContextVars set inside a nested middleware DO NOT propagate back to
    # an outer middleware. request.state, however, is attached to the Request
    # object itself and survives the chain — so we read state first and fall
    # back to contextvars for anything the existing middlewares don't populate.
    state = getattr(request, "state", None)
    request_id = getattr(state, "request_id", None)
    if request_id is None:
        try:
            request_id = request_id_var.get()
        except Exception:
            request_id = None
    try:
        trace_id = trace_id_var.get() or request_id
    except Exception:
        trace_id = request_id
    tenant_id = getattr(state, "tenant_id", None)
    if tenant_id is None:
        try:
            tenant_id = tenant_id_var.get()
        except Exception:
            tenant_id = None
    user_id = getattr(state, "user_id", None)
    if user_id is None:
        try:
            user_id = user_id_var.get()
        except Exception:
            user_id = None

    status = response.status_code if response is not None else 500
    error_text: str | None = None
    origin: str | None = None
    if exc is not None:
        error_text = str(exc)[:_ERROR_TRUNC]
        try:
            tb = traceback.extract_tb(exc.__traceback__)
            if tb:
                frame = tb[-1]
                origin = f"{frame.filename}:{frame.lineno}"
        except Exception:
            origin = None

    body_preview = _extract_body_preview(response)

    return {
        "Source": _SOURCE,
        "Environment": settings.ENVIRONMENT,
        "Method": request.method,
        "Path": request.url.path,
        "Query": repr(request.query_params)[:_QUERY_TRUNC],
        "Status": status,
        "Duration_ms": duration_ms,
        "Error": error_text,
        "Origin": origin,
        "Response_body": body_preview,
        "Request ID": request_id,
        "Trace ID": trace_id,
        "Tenant ID": tenant_id,
        "User ID": user_id,
        "Timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


def _extract_body_preview(response: "Response | None") -> str | None:
    """Return a truncated, UTF-8-safe preview of the response body.

    Returns None for ``StreamingResponse`` (no ``.body`` attribute) or when
    body capture is disabled via ``ALERT_BODY_PREVIEW_BYTES=0``.
    """
    if response is None:
        return None
    cap = settings.ALERT_BODY_PREVIEW_BYTES
    if cap <= 0:
        return None
    body = getattr(response, "body", None)
    if not isinstance(body, (bytes, bytearray)):
        return None
    return bytes(body[:cap]).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Dispatcher — dedup + async hand-off
# ---------------------------------------------------------------------------


class AlertDispatcher:
    """Buffers alerts for a short dedup window and fires them off-loop."""

    def __init__(self) -> None:
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    async def schedule(
        self,
        *,
        request: "Request",
        response: "Response | None",
        exc: BaseException | None,
        duration_ms: int,
    ) -> None:
        """Build the payload, check dedup, create a publish task.

        Never awaits the publish itself. Returns as soon as the task is
        scheduled so the response path is unblocked.
        """
        try:
            status = response.status_code if response is not None else 500
            exc_type = type(exc).__name__ if exc is not None else ""
            dedup_key = f"{request.url.path}|{status}|{exc_type}"

            if await self._is_duplicate(dedup_key):
                logger.debug(
                    "alerter.dropped_dedup dedup_key=%s", dedup_key,
                )
                return

            payload = build_alert_payload(
                request=request, response=response, exc=exc,
                duration_ms=duration_ms,
            )
            task = asyncio.create_task(publish_alert(payload))
            self._tasks.add(task)
            task.add_done_callback(self._on_task_done)
            logger.debug(
                "alerter.dispatched path=%s status=%d duration_ms=%d",
                request.url.path, status, duration_ms,
            )
        except Exception as build_exc:
            # Anything other than the publish itself failing counts as a
            # build-phase error. Swallow — the request must not fail.
            logger.error(
                "alerter.error phase=build error.type=%s error.message=%s",
                type(build_exc).__name__, str(build_exc),
            )

    async def _is_duplicate(self, key: str) -> bool:
        window = settings.ALERT_DEDUP_WINDOW_SECONDS
        if window <= 0:
            return False
        now = time.monotonic()
        async with self._lock:
            # Evict expired entries from the front (OrderedDict keeps
            # insertion order; entries inserted earlier have smaller ts).
            while self._seen:
                oldest_key, oldest_ts = next(iter(self._seen.items()))
                if now - oldest_ts > window:
                    self._seen.popitem(last=False)
                else:
                    break
            if key in self._seen:
                return True
            # Bound the cache. Drop oldest if we've hit the cap.
            if len(self._seen) >= _DEDUP_MAX_ENTRIES:
                self._seen.popitem(last=False)
            self._seen[key] = now
            return False

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "alerter.error phase=publish error.type=%s error.message=%s",
                type(exc).__name__, str(exc),
            )

    async def close(self) -> None:
        """Best-effort drain of in-flight publish tasks on shutdown."""
        if not self._tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True),
                timeout=_CLOSE_DRAIN_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            pass


_dispatcher: AlertDispatcher | None = None


def get_dispatcher() -> AlertDispatcher:
    """Return the per-process dispatcher singleton (lazy)."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = AlertDispatcher()
    return _dispatcher


def reset_dispatcher_for_tests() -> None:
    """Drop the dispatcher singleton — tests only."""
    global _dispatcher
    _dispatcher = None
