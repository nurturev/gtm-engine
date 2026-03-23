"""Scheduled workflows router: register and manage workflow schedules."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.models import Tenant
from server.core.config import settings
from server.core.database import get_db, set_tenant_context
from server.execution.schedule_models import ScheduledWorkflow

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])

_COOKIE_NAME = "nrev_session"


async def _get_tenant(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> tuple[Tenant, AsyncSession]:
    """Flexible auth — Bearer, cookie, or query param."""
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

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    await set_tenant_context(db, tenant.id)
    return tenant, db


class RegisterScheduleRequest(BaseModel):
    name: str
    description: str | None = None
    schedule: str | None = None
    cron_expression: str | None = None
    workflow_label: str | None = None
    prompt: str | None = None


@router.post("")
async def register_schedule(
    request: Request,
    body: RegisterScheduleRequest,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Register a scheduled workflow (called when Claude Code's scheduler is set up)."""
    tenant, db = await _get_tenant(request, token=token, db=db)

    sw = ScheduledWorkflow(
        tenant_id=tenant.id,
        name=body.name,
        description=body.description,
        schedule=body.schedule,
        cron_expression=body.cron_expression,
        workflow_label=body.workflow_label,
        prompt=body.prompt,
    )
    db.add(sw)
    await db.commit()
    await db.refresh(sw)

    return {
        "id": str(sw.id),
        "name": sw.name,
        "schedule": sw.schedule,
        "status": "registered",
    }


@router.get("")
async def list_schedules(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all scheduled workflows for the tenant."""
    tenant, db = await _get_tenant(request, token=token, db=db)

    result = await db.execute(
        select(ScheduledWorkflow)
        .where(ScheduledWorkflow.tenant_id == tenant.id)
        .order_by(ScheduledWorkflow.updated_at.desc())
    )
    schedules = result.scalars().all()

    return {
        "schedules": [
            {
                "id": str(sw.id),
                "name": sw.name,
                "description": sw.description,
                "schedule": sw.schedule,
                "cron_expression": sw.cron_expression,
                "enabled": sw.enabled,
                "next_run_at": sw.next_run_at.isoformat() if sw.next_run_at else None,
                "last_run_at": sw.last_run_at.isoformat() if sw.last_run_at else None,
                "run_count": sw.run_count,
            }
            for sw in schedules
        ],
    }


class UpdateScheduleRequest(BaseModel):
    enabled: bool | None = None


@router.patch("/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    body: UpdateScheduleRequest,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update a scheduled workflow (enable/disable)."""
    tenant, db = await _get_tenant(request, token=token, db=db)
    result = await db.execute(
        select(ScheduledWorkflow).where(
            ScheduledWorkflow.id == schedule_id,
            ScheduledWorkflow.tenant_id == tenant.id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if body.enabled is not None:
        schedule.enabled = body.enabled
    await db.commit()
    await db.refresh(schedule)
    return {"status": "ok", "enabled": schedule.enabled}
