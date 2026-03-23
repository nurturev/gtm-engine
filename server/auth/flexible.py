"""Flexible authentication: accepts Bearer token, session cookie, or query param.

This is the shared implementation used by all routers that need to support
both MCP client (Bearer token) and dashboard browser (cookie) callers.
Import this instead of copy-pasting _get_tenant_flexible into each router.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Query, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.models import Tenant
from server.core.config import settings
from server.core.database import get_db, set_tenant_context

_COOKIE_NAME = "nrev_session"


async def get_tenant_flexible(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> tuple[Tenant, AsyncSession]:
    """Authenticate via Bearer token, session cookie, or query param.

    Supports both MCP client (Bearer) and dashboard (cookie) callers.
    Also accepts app_token for hosted app CRUD access.
    """
    jwt_token: str | None = None

    # 1. Authorization header (MCP client / API)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        jwt_token = auth_header.removeprefix("Bearer ")

    # 2. Cookie (dashboard browser sessions)
    if not jwt_token:
        jwt_token = request.cookies.get(_COOKIE_NAME)

    # 3. Query param (legacy / convenience)
    if not jwt_token and token:
        jwt_token = token

    # 4. Console path-based tenant (for console routes that embed tenant_id in URL)
    if not jwt_token:
        tenant_id_from_path = request.path_params.get("tenant_id")
        if tenant_id_from_path:
            result = await db.execute(
                select(Tenant).where(Tenant.id == tenant_id_from_path)
            )
            tenant = result.scalar_one_or_none()
            if tenant:
                await set_tenant_context(db, tenant.id)
                return tenant, db

    if not jwt_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    try:
        payload = jwt.decode(
            jwt_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
        )

    tenant_id: str | None = payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing tenant_id",
        )

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    await set_tenant_context(db, tenant.id)
    return tenant, db
