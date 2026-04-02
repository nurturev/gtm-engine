"""ASGI middleware: request-ID injection, tenant context, rate-limit headers."""

from __future__ import annotations

import uuid

from fastapi import Request, Response
from jose import jwt

from server.core.config import settings


async def request_id_middleware(request: Request, call_next) -> Response:
    """Attach a unique X-Request-ID header to every response.

    If the client supplies an X-Request-ID header it is reused; otherwise
    a new UUID4 is generated.
    """
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


async def tenant_context_middleware(request: Request, call_next) -> Response:
    """Extract tenant_id from the JWT bearer token and store it on request.state.

    This does **not** enforce authentication (that is handled by the
    ``get_current_user`` dependency).  It simply makes the tenant_id
    available early in the request lifecycle for logging and tracing.
    """
    tenant_id: str | None = None
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
        except Exception:
            # Not a JWT — check for X-Tenant-Id (service token path)
            tenant_id = request.headers.get("X-Tenant-Id")
    request.state.tenant_id = tenant_id
    response: Response = await call_next(request)
    return response
