"""Shared FastAPI dependencies for authentication and authorisation."""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.core.config import settings
from server.core.database import get_db, set_tenant_context
from server.billing.models import CreditBalance
from server.auth.models import Tenant, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lightweight tenant reference (no DB lookup)
# ---------------------------------------------------------------------------


@dataclass
class TenantRef:
    """Lightweight tenant reference extracted directly from JWT claims.

    Used by execution endpoints that only need tenant_id for RLS, caching,
    rate limiting, and BYOK key resolution — no User or Tenant DB lookup.
    """

    id: str


# ---------------------------------------------------------------------------
# Service token check
# ---------------------------------------------------------------------------


def _is_service_token(token: str) -> bool:
    """Constant-time check whether token matches the configured service token."""
    if not settings.GTM_ENGINE_SERVICE_TOKEN:
        return False
    return hmac.compare_digest(token, settings.GTM_ENGINE_SERVICE_TOKEN)


# ---------------------------------------------------------------------------
# JWT-only tenant resolution (for execution endpoints)
# ---------------------------------------------------------------------------


def _decode_gtm_jwt(authorization: str) -> dict:
    """Decode and validate a gtm-engine JWT. Returns the payload dict."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must use Bearer scheme",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc
    return payload


async def get_tenant_from_token(
    request: Request,
    authorization: Annotated[str, Header()],
    db: AsyncSession = Depends(get_db),
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> TenantRef:
    """Extract tenant_id and set RLS context. Supports two auth paths:

    1. **Service token**: Bearer token matches GTM_ENGINE_SERVICE_TOKEN →
       tenant_id read from X-Tenant-Id header. Used by internal services
       (e.g. consultant agent in workflow_studio).
    2. **JWT** (default): Bearer token decoded as gtm-engine JWT →
       tenant_id read from JWT claims. Used by CLI and console.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must use Bearer scheme",
        )
    token = authorization.removeprefix("Bearer ")

    # Service token path — checked before JWT decode to avoid
    # unnecessary decode failures when a service token is sent.
    if _is_service_token(token):
        if not x_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Tenant-Id header is required for service token auth",
            )
        agent_type = request.headers.get("X-Agent-Type", "unknown")
        thread_id = request.headers.get("X-Thread-Id")
        logger.info(
            "Service token auth: tenant=%s agent_type=%s thread_id=%s",
            x_tenant_id, agent_type, thread_id,
        )
        await set_tenant_context(db, x_tenant_id)
        return TenantRef(id=x_tenant_id)

    # JWT path (existing behavior)
    payload = _decode_gtm_jwt(authorization)
    tenant_id: str | None = payload.get("tenant_id")
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing tenant_id claim",
        )
    await set_tenant_context(db, tenant_id)
    return TenantRef(id=tenant_id)


# ---------------------------------------------------------------------------
# Full user/tenant resolution (for CLI + console endpoints)
# ---------------------------------------------------------------------------


async def get_current_user(
    authorization: Annotated[str, Header()],
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate JWT from the Authorization header.

    Returns the authenticated User ORM object.
    Raises 401 if the token is missing, malformed, or expired.
    """
    payload = _decode_gtm_jwt(authorization)
    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


async def get_current_tenant(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """Resolve the tenant for the authenticated user and activate RLS context.

    Returns the Tenant ORM object.
    """
    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )
    await set_tenant_context(db, tenant.id)
    return tenant


# ---------------------------------------------------------------------------
# Credit pre-check dependency
# ---------------------------------------------------------------------------


def require_credits(amount: float):
    """Return a dependency that checks the tenant has at least *amount* credits.

    Raises HTTP 402 Payment Required when the balance is insufficient.

    Uses platform credit service when PLATFORM_CREDIT_SERVICE_URL is set,
    otherwise falls back to local DB check.

    Usage::

        @router.post("/execute", dependencies=[Depends(require_credits(1.0))])
        async def execute(...): ...
    """

    async def _check(
        tenant: TenantRef = Depends(get_tenant_from_token),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        if settings.PLATFORM_CREDIT_SERVICE_URL:
            from server.billing.platform_credit_service import check_platform_credits

            balance = await check_platform_credits(tenant.id)
            if balance < amount:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Insufficient credits: need {amount}, have {balance}",
                )
        else:
            result = await db.execute(
                select(CreditBalance).where(CreditBalance.tenant_id == tenant.id)
            )
            balance_row = result.scalar_one_or_none()
            current_balance = float(balance_row.balance) if balance_row else 0.0
            if current_balance < amount:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Insufficient credits: need {amount}, have {current_balance}",
                )

    return _check
