"""ASGI middleware: request-ID injection, tenant context, rate-limit headers."""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar

from fastapi import Request, Response
from jose import jwt

from server.core.config import settings

logger = logging.getLogger(__name__)

# Per-request identifiers, read by the JSON log formatter so every log line
# carries tenant/user/request context automatically.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
# Defined now but populated only once the async request/response flow lands —
# today the alerter falls back to request_id when trace_id is None.
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
internal_service_var: ContextVar[str | None] = ContextVar("internal_service", default=None)
agent_type_var: ContextVar[str | None] = ContextVar("agent_type", default=None)
thread_id_var: ContextVar[str | None] = ContextVar("thread_id", default=None)
workflow_id_var: ContextVar[str | None] = ContextVar("workflow_id", default=None)


async def request_id_middleware(request: Request, call_next) -> Response:
    """Attach a unique X-Request-ID header to every response.

    If the client supplies an X-Request-ID header it is reused; otherwise
    a new UUID4 is generated. Also emits an entry/exit log for every request
    so we can confirm requests are reaching the app even if a downstream
    handler hangs or never responds.
    """
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    request_id_var.set(request_id)

    path = request.url.path
    is_health_probe = path.startswith("/health")
    client_host = request.client.host if request.client else "unknown"
    if not is_health_probe:
        logger.info(
            "Request received: %s %s from=%s request_id=%s",
            request.method, path, client_host, request_id,
        )
    start = time.monotonic()
    try:
        response: Response = await call_next(request)
    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.exception(
            "Request raised: %s %s request_id=%s duration_ms=%d",
            request.method, path, request_id, duration_ms,
        )
        raise
    duration_ms = int((time.monotonic() - start) * 1000)
    # Skip the completed-log for successful health probes — they're every-few-
    # seconds noise. Non-2xx/3xx still logs so failing probes are visible.
    if not (is_health_probe and 200 <= response.status_code < 400):
        logger.info(
            "Request completed: %s %s status=%d request_id=%s duration_ms=%d",
            request.method, path, response.status_code,
            request_id, duration_ms,
        )
    response.headers["X-Request-ID"] = request_id
    return response


async def tenant_context_middleware(request: Request, call_next) -> Response:
    """Extract tenant_id from the JWT bearer token and store it on request.state.

    This does **not** enforce authentication (that is handled by the
    ``get_current_user`` dependency).  It simply makes the tenant_id
    available early in the request lifecycle for logging and tracing.
    """
    tenant_id: str | None = None
    user_id: str | None = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ")
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
                options={"verify_exp": False},
            )
            tenant_id = payload.get("tenant_id")
            user_id = payload.get("sub")
        except Exception:
            # Not a JWT — service-token path; identifiers come from headers.
            pass

    # Header values always win for service-to-service calls (X-Internal-Service
    # is set) and serve as a fallback for auth paths that didn't populate them.
    tenant_id = request.headers.get("X-Tenant-Id") or tenant_id
    user_id = request.headers.get("X-User-Id") or user_id

    request.state.tenant_id = tenant_id
    request.state.user_id = user_id
    tenant_id_var.set(tenant_id)
    user_id_var.set(user_id)

    internal_service_var.set(request.headers.get("X-Internal-Service"))
    agent_type_var.set(request.headers.get("X-Agent-Type"))
    thread_id_var.set(request.headers.get("X-Thread-Id"))
    workflow_id_var.set(request.headers.get("X-Workflow-Id"))

    response: Response = await call_next(request)
    return response


async def response_alert_middleware(request: Request, call_next) -> Response:
    """Emit a Slack alert (via SNS) for any non-2xx response or raised exception.

    Observational only — never modifies the response, never blocks on the
    publish. Alert dispatch is off-loop via ``asyncio.create_task``; any
    alerter failure is swallowed and logged. See ``docs/hld_response_alert_middleware.md``
    and ``docs/lld_response_alert_middleware.md`` for the full contract.
    """
    # Local imports: `alerting` imports ContextVars from this module, so we
    # defer its import to avoid a circular load during module init.
    from server.core.alerting import (
        get_dispatcher,
        is_alertable,
        is_excluded_path,
    )

    if request.method == "OPTIONS" or is_excluded_path(request.url.path):
        return await call_next(request)

    start = time.monotonic()
    dispatcher = get_dispatcher()
    try:
        response: Response = await call_next(request)
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        try:
            await dispatcher.schedule(
                request=request, response=None, exc=exc,
                duration_ms=duration_ms,
            )
        except Exception as alerter_exc:
            logger.error(
                "alerter.error phase=middleware error.type=%s error.message=%s",
                type(alerter_exc).__name__, str(alerter_exc),
            )
        raise
    duration_ms = int((time.monotonic() - start) * 1000)
    if is_alertable(response.status_code, None):
        try:
            await dispatcher.schedule(
                request=request, response=response, exc=None,
                duration_ms=duration_ms,
            )
        except Exception as alerter_exc:
            logger.error(
                "alerter.error phase=middleware error.type=%s error.message=%s",
                type(alerter_exc).__name__, str(alerter_exc),
            )
    return response
