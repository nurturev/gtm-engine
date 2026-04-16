"""Credits router: balance, history, and top-up endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import TenantRef, get_tenant_from_token
from server.auth.flexible import get_tenant_flexible as _get_tenant_flexible
from server.auth.models import Tenant
from server.core.config import settings
from server.core.database import get_db
from server.billing.schemas import (
    CreditBalanceResponse,
    CreditHistoryResponse,
    LedgerEntryResponse,
    TopupRequest,
    TopupResponse,
)
from server.billing.platform_credit_service import check_platform_credits
from server.billing.service import get_balance, get_history

router = APIRouter(prefix="/api/v1", tags=["credits"])

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/credits/balance", response_model=CreditBalanceResponse)
async def get_credit_balance_lightweight(
    tenant: TenantRef = Depends(get_tenant_from_token),
    db: AsyncSession = Depends(get_db),
) -> CreditBalanceResponse:
    """Return credit balance using lightweight auth (JWT or service token).

    Unlike GET /credits, this does not require a full User DB lookup.
    Used by internal services (e.g. consultant agent) that authenticate
    via service token + X-Tenant-Id header.
    """
    if settings.PLATFORM_CREDIT_SERVICE_URL:
        balance = await check_platform_credits(tenant.id)
        return CreditBalanceResponse(
            tenant_id=tenant.id,
            balance=balance,
            spend_this_month=0.0,
        )

    balance_info = await get_balance(db, tenant.id)
    return CreditBalanceResponse(
        tenant_id=tenant.id,
        balance=balance_info["balance"],
        spend_this_month=balance_info["spend_this_month"],
    )


@router.get("/credits", response_model=CreditBalanceResponse)
async def get_credit_balance(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> CreditBalanceResponse:
    """Return the current credit balance for the tenant."""
    tenant, db = await _get_tenant_flexible(request, token, db)
    if settings.PLATFORM_CREDIT_SERVICE_URL:
        balance = await check_platform_credits(tenant.id)
        return CreditBalanceResponse(
            tenant_id=tenant.id,
            balance=balance,
            spend_this_month=0.0,
        )
    balance_info = await get_balance(db, tenant.id)
    return CreditBalanceResponse(
        tenant_id=tenant.id,
        balance=balance_info["balance"],
        spend_this_month=balance_info["spend_this_month"],
    )


@router.get("/credits/history", response_model=CreditHistoryResponse)
async def get_credit_history(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> CreditHistoryResponse:
    """Return the credit transaction history for the tenant."""
    tenant, db = await _get_tenant_flexible(request, token, db)
    entries, total = await get_history(db, str(tenant.id), limit=limit, offset=offset)
    return CreditHistoryResponse(
        entries=[
            LedgerEntryResponse(
                id=e.id,
                entry_type=e.entry_type,
                amount=float(e.amount),
                balance_after=float(e.balance_after),
                operation=e.operation,
                reference_id=e.reference_id,
                description=e.description,
                created_at=e.created_at,
            )
            for e in entries
        ],
        total=total,
    )


@router.post("/credits/topup", response_model=TopupResponse)
async def initiate_topup(
    body: TopupRequest,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> TopupResponse:
    """Initiate a Stripe checkout session for credit top-up.

    This is a stub that returns a mock checkout URL.
    Real Stripe integration will be added when STRIPE_SECRET_KEY is configured.
    """
    # Stub - return mock checkout data
    return TopupResponse(
        checkout_url=f"https://checkout.stripe.com/mock/{body.package}",
        session_id=f"cs_mock_{body.package}",
    )
