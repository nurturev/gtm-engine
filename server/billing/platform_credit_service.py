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

FLEXPRICE_EVENT_NAME = "data_enrichment_api"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.PLATFORM_CREDIT_SERVICE_TOKEN}"}


def _validate_tenant_id(tenant_id: str) -> int | None:
    """Validate and cast tenant_id to int. Returns None if non-numeric."""
    try:
        return int(tenant_id)
    except (ValueError, TypeError):
        return None


async def check_platform_credits(
    tenant_id: str, required_amount: int | None = None
) -> float:
    """Check credit balance via the platform credit service.

    Returns the current balance as a float. Returns 0.0 if the platform
    returns null, returns 402, or if the request fails (fail-closed).
    """
    numeric_id = _validate_tenant_id(tenant_id)
    if numeric_id is None:
        logger.warning(
            "Tenant %s is not a platform tenant — cannot check credits",
            tenant_id,
        )
        return 0.0

    url = f"{settings.PLATFORM_CREDIT_SERVICE_URL}/tenant/credits"
    params: dict[str, int] = {"tenant_id": numeric_id}
    if required_amount is not None:
        params["credit_count"] = required_amount

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=_headers())
            if resp.status_code == 402:
                logger.debug(
                    "Platform credit check: tenant=%s insufficient credits (402)",
                    tenant_id,
                )
                return 0.0
            resp.raise_for_status()
            balance = resp.json()
            if balance is None:
                return 0.0
            logger.debug(
                "Platform credit check: tenant=%s balance=%s required=%s",
                tenant_id,
                balance,
                required_amount,
            )
            return float(balance)
    except Exception:
        logger.exception(
            "Platform credit check failed for tenant %s — returning 0.0 (fail-closed)",
            tenant_id,
        )
        return 0.0


async def debit_platform_credits(
    tenant_id: str,
    amount: int,
    event_type: str,
    agent_thread_id: str | None = None,
) -> None:
    """Fire-and-forget debit to the platform credit service.

    Logs errors but never raises — the caller has already received their
    execution response by the time this runs.
    """
    numeric_id = _validate_tenant_id(tenant_id)
    if numeric_id is None:
        logger.warning(
            "Tenant %s is not a platform tenant — skipping debit", tenant_id
        )
        return

    url = f"{settings.PLATFORM_CREDIT_SERVICE_URL}/tenant/credit/deduct"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                json={
                    "tenant_id": numeric_id,
                    "credit_count": amount,
                    "event_name": FLEXPRICE_EVENT_NAME,
                    "event_type": event_type,
                    "agent_thread_id": agent_thread_id,
                },
                headers=_headers(),
            )
            if resp.status_code in (200, 202):
                logger.info(
                    "Platform credit debit: tenant=%s amount=%d event_type=%s",
                    tenant_id,
                    amount,
                    event_type,
                )
            else:
                logger.warning(
                    "Platform credit debit returned %s for tenant %s: %s",
                    resp.status_code,
                    tenant_id,
                    resp.text[:200],
                )
    except Exception:
        logger.exception(
            "Platform credit debit failed: tenant=%s amount=%d event_type=%s agent_thread_id=%s",
            tenant_id,
            amount,
            event_type,
            agent_thread_id,
        )
