"""Keys router: BYOK key management for tenant API keys."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.flexible import get_tenant_flexible as _get_tenant_flexible
from server.auth.models import Tenant
from server.core.database import get_db
from server.vault.models import TenantKey
from server.vault.schemas import AddKeyRequest, KeyInfoResponse, KeyListResponse
from server.vault.service import encrypt_key, key_hint

router = APIRouter(prefix="/api/v1", tags=["keys"])

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/keys", response_model=KeyInfoResponse, status_code=status.HTTP_201_CREATED)
async def add_key(
    body: AddKeyRequest,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> KeyInfoResponse:
    """Store an encrypted BYOK API key for a provider.

    The raw key is encrypted before storage and is never retrievable.
    Only a hint (last 4 characters) is kept for identification.
    """
    tenant, db = await _get_tenant_flexible(request, token, db)

    # Build hint from last 4 chars
    hint = key_hint(body.api_key)

    # Encrypt key
    encrypted = encrypt_key(body.api_key, tenant.id)

    # Upsert
    result = await db.execute(
        select(TenantKey).where(
            TenantKey.tenant_id == tenant.id,
            TenantKey.provider == body.provider,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.encrypted_key = encrypted
        existing.key_hint = hint
        existing.status = "active"
    else:
        key_record = TenantKey(
            tenant_id=tenant.id,
            provider=body.provider,
            encrypted_key=encrypted,
            key_hint=hint,
            status="active",
        )
        db.add(key_record)

    await db.commit()

    return KeyInfoResponse(
        provider=body.provider,
        key_hint=hint,
        status="active",
    )


@router.get("/keys", response_model=KeyListResponse)
async def list_keys(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> KeyListResponse:
    """List all BYOK keys for the tenant (hints only, never values)."""
    tenant, db = await _get_tenant_flexible(request, token, db)

    result = await db.execute(
        select(TenantKey).where(TenantKey.tenant_id == tenant.id)
    )
    keys = result.scalars().all()
    return KeyListResponse(
        keys=[
            KeyInfoResponse(
                provider=k.provider,
                key_hint=k.key_hint,
                status=k.status,
            )
            for k in keys
        ]
    )


@router.delete("/keys/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_key(
    provider: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a BYOK key for the given provider.

    After removal the platform key (if available) will be used as fallback.
    """
    tenant, db = await _get_tenant_flexible(request, token, db)

    result = await db.execute(
        delete(TenantKey).where(
            TenantKey.tenant_id == tenant.id,
            TenantKey.provider == provider,
        )
    )
    if result.rowcount == 0:  # type: ignore[union-attr]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No key found for provider '{provider}'",
        )
    await db.commit()
