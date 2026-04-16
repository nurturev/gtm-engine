"""Platform access service client.

Calls the main nRev platform's user-management (UM) service to answer:
"Does this email have access to this tenant?" Used by the console browser
auth flow so that tenant authorization is delegated to UM instead of the
local `users` table.

Reuses the same base URL + service token as the credit service (same
deployment, same auth). See server/billing/platform_credit_service.py.
"""

from __future__ import annotations

import hashlib
import logging

import httpx

from server.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
_CACHE_TTL_SECONDS = 60


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.PLATFORM_CREDIT_SERVICE_TOKEN}"}


def _cache_key(email: str, tenant_id: str) -> str:
    digest = hashlib.sha256(f"{email}|{tenant_id}".encode()).hexdigest()
    return f"auth:access:{digest}"


def _get_redis():
    """Get the shared Redis connection from the app module."""
    from server.app import redis_pool

    return redis_pool


async def has_tenant_access(email: str, tenant_id: str) -> bool:
    """Return True if UM says *email* has access to *tenant_id*.

    Cache-through Redis for 60s (both True and False). Fails closed on
    timeout, 5xx, or any unexpected error — returns False and logs.
    """
    if not email or not tenant_id:
        return False

    if not settings.PLATFORM_CREDIT_SERVICE_URL:
        logger.warning(
            "PLATFORM_CREDIT_SERVICE_URL not configured — denying console access"
        )
        return False

    redis = _get_redis()
    key = _cache_key(email, tenant_id)

    if redis is not None:
        cached = await redis.get(key)
        if cached is not None:
            return cached == "1" or cached == b"1"

    url = f"{settings.PLATFORM_CREDIT_SERVICE_URL}/user/tenant_access"
    params = {"email": email, "tenant_id": tenant_id}

    logger.info(
        "UM access check: url=%s email=%s tenant_id=%s",
        url, email, tenant_id,
    )
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=_headers())
            logger.info(
                "UM access response: status=%s body=%s",
                resp.status_code, resp.text[:300],
            )
            if resp.status_code == 404:
                result = False
            else:
                resp.raise_for_status()
                body = resp.json()
                result = bool(body.get("has_access", False))
    except Exception:
        logger.exception(
            "Platform access check failed for tenant %s — denying (fail-closed)",
            tenant_id,
        )
        return False

    if redis is not None:
        try:
            await redis.set(key, "1" if result else "0", ex=_CACHE_TTL_SECONDS)
        except Exception:
            logger.exception("Failed to cache access result")

    return result
