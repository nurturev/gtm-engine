"""Platform credit service client.

Calls the main nRev platform's credit management APIs for balance checks
and fire-and-forget debits. Used when PLATFORM_CREDIT_SERVICE_URL is set.
"""

from __future__ import annotations

import logging

import httpx

from server.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.PLATFORM_CREDIT_SERVICE_TOKEN}"}


async def check_platform_credits(tenant_id: str) -> float:
    """Check credit balance via the platform credit service.

    Returns the current balance as a float. Returns 0.0 if the platform
    returns null or if the request fails (fail-closed).
    """
    url = f"{settings.PLATFORM_CREDIT_SERVICE_URL}/tenant/credits"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                url,
                params={"tenant_id": tenant_id},
                headers=_headers(),
            )
            resp.raise_for_status()
            balance = resp.json()
            if balance is None:
                return 0.0
            return float(balance)
    except Exception:
        logger.exception(
            "Failed to check platform credits for tenant %s — failing open",
            tenant_id,
        )
        return float("inf")


async def debit_platform_credits(
    tenant_id: str,
    amount: int,
    operation: str | None = None,
    agent_thread_id: str | None = None,
) -> None:
    """Fire-and-forget debit to the platform credit service.

    Logs errors but never raises — the caller has already received their
    execution response by the time this runs.
    """
    url = f"{settings.PLATFORM_CREDIT_SERVICE_URL}/tenant/credit/deduct"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                json={
                    "tenant_id": tenant_id,
                    "credit_count": amount,
                    "agent_thread_id": agent_thread_id,
                },
                headers=_headers(),
            )
            if resp.status_code not in (200, 202):
                logger.warning(
                    "Platform credit debit returned %s for tenant %s: %s",
                    resp.status_code,
                    tenant_id,
                    resp.text[:200],
                )
    except Exception:
        logger.exception(
            "Failed to debit %d credits from platform for tenant %s",
            amount,
            tenant_id,
        )
