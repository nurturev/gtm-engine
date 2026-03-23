"""Admin router — learning logs review UI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.models import Tenant
from server.core.config import settings
from server.core.database import get_db
from server.execution.learning_models import DynamicKnowledge, LearningLog

router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_COOKIE_NAME = "nrev_session"


def _get_admin_ids() -> set[str]:
    raw = os.environ.get("ADMIN_TENANT_IDS", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


async def _authenticate_admin(
    request: Request,
    token: Optional[str] = None,
    db: AsyncSession = None,
) -> Tenant:
    """Authenticate and verify admin access."""
    jwt_token: str | None = None

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        jwt_token = auth_header.removeprefix("Bearer ")
    if not jwt_token:
        jwt_token = request.cookies.get(_COOKIE_NAME)
    if not jwt_token and token:
        jwt_token = token

    if not jwt_token:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        payload = jwt.decode(jwt_token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Session expired")

    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    if tenant_id not in _get_admin_ids():
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return tenant


@router.get("/learning-logs", response_class=HTMLResponse)
async def learning_logs_page(
    request: Request,
    status: str = Query("pending"),
    category: str = Query("all"),
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Admin page to review learning logs."""
    tenant = await _authenticate_admin(request, token=token, db=db)

    # Fetch logs
    stmt = select(LearningLog).order_by(LearningLog.created_at.desc()).limit(100)
    if status != "all":
        stmt = stmt.where(LearningLog.status == status)
    if category != "all":
        stmt = stmt.where(LearningLog.category == category)

    result = await db.execute(stmt)
    logs = result.scalars().all()

    # Status counts
    count_stmt = select(
        LearningLog.status, func.count(LearningLog.id)
    ).group_by(LearningLog.status)
    count_result = await db.execute(count_stmt)
    status_counts = dict(count_result.all())

    # Dynamic knowledge count
    dk_count = await db.scalar(select(func.count(DynamicKnowledge.id)).where(DynamicKnowledge.enabled == True))  # noqa: E712

    return templates.TemplateResponse(
        "learning_logs.html",
        {
            "request": request,
            "tenant": tenant,
            "logs": logs,
            "active_status": status,
            "active_category": category,
            "status_counts": status_counts,
            "knowledge_count": dk_count or 0,
        },
    )
