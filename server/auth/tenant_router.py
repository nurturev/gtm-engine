"""Service-token-only endpoints for platform tenant sync."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import require_service_token
from server.auth.models import Tenant
from server.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/tenants",
    tags=["tenants"],
    dependencies=[Depends(require_service_token)],
)

_NUMERIC_RE = re.compile(r"^\d+$")


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class CreateTenantRequest(BaseModel):
    id: str
    name: str
    domain: str

    @field_validator("id")
    @classmethod
    def id_must_be_numeric(cls, v: str) -> str:
        if not _NUMERIC_RE.match(v):
            raise ValueError("id must be a numeric string")
        return v

    @field_validator("name", "domain")
    @classmethod
    def must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field must not be empty")
        return v


class UpdateTenantRequest(BaseModel):
    name: str | None = None
    domain: str | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> UpdateTenantRequest:
        if self.name is None and self.domain is None:
            raise ValueError("At least one field required")
        return self

    @field_validator("name", "domain", mode="before")
    @classmethod
    def must_be_non_empty_if_provided(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("field must not be empty")
        return v


class TenantResponse(BaseModel):
    id: str
    name: str
    domain: str | None
    created_at: str

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TenantResponse)
async def create_tenant(
    body: CreateTenantRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    logger.info(
        "create_tenant called: id=%s name=%s domain=%s",
        body.id, body.name, body.domain,
    )
    tenant = Tenant(id=body.id, name=body.name, domain=body.domain)
    db.add(tenant)
    try:
        await db.commit()
        await db.refresh(tenant)
        logger.info("Tenant created: id=%s domain=%s", tenant.id, tenant.domain)
        return _to_response(tenant)
    except IntegrityError:
        await db.rollback()
        logger.info("Tenant %s already exists, returning existing", body.id)
        result = await db.execute(select(Tenant).where(Tenant.id == body.id))
        existing = result.scalar_one()
        response.status_code = status.HTTP_200_OK
        return _to_response(existing)


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    body: UpdateTenantRequest,
    tenant_id: str = Path(pattern=r"^\d+$"),
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant {tenant_id} not found",
        )

    updated_fields = []
    if body.name is not None:
        tenant.name = body.name
        updated_fields.append("name")
    if body.domain is not None:
        tenant.domain = body.domain
        updated_fields.append("domain")

    await db.commit()
    await db.refresh(tenant)
    logger.info("Tenant updated: id=%s fields=%s", tenant_id, updated_fields)
    return _to_response(tenant)


def _to_response(tenant: Tenant) -> TenantResponse:
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        domain=tenant.domain,
        created_at=tenant.created_at.isoformat(),
    )
