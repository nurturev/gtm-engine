"""Admin API for managing operation credit costs."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from server.admin.router import _authenticate_admin
from server.billing import cost_config_service as svc
from server.core.database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class OperationCostCreate(BaseModel):
    vendor: str
    operation: str
    base_cost: float
    description: str | None = None


class OperationCostUpdate(BaseModel):
    base_cost: float | None = None
    description: str | None = None


class OperationCostResponse(BaseModel):
    id: int
    vendor: str
    operation: str
    base_cost: float
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/operation-costs", response_model=list[OperationCostResponse])
async def list_operation_costs(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[OperationCostResponse]:
    await _authenticate_admin(request, db=db)
    rows = await svc.list_costs(db)
    return [OperationCostResponse.model_validate(r) for r in rows]


@router.post("/operation-costs", response_model=OperationCostResponse, status_code=201)
async def create_operation_cost(
    request: Request,
    body: OperationCostCreate,
    db: AsyncSession = Depends(get_db),
) -> OperationCostResponse:
    await _authenticate_admin(request, db=db)
    try:
        row = await svc.create_cost(
            db,
            vendor=body.vendor,
            operation=body.operation,
            base_cost=body.base_cost,
            description=body.description,
        )
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(
                status_code=409,
                detail=f"Cost entry for {body.vendor}:{body.operation} already exists",
            ) from exc
        raise
    return OperationCostResponse.model_validate(row)


@router.put("/operation-costs/{cost_id}", response_model=OperationCostResponse)
async def update_operation_cost(
    request: Request,
    cost_id: int,
    body: OperationCostUpdate,
    db: AsyncSession = Depends(get_db),
) -> OperationCostResponse:
    await _authenticate_admin(request, db=db)
    row = await svc.get_cost(db, cost_id)
    if not row:
        raise HTTPException(status_code=404, detail="Operation cost not found")
    updated = await svc.update_cost(
        db,
        row,
        base_cost=body.base_cost,
        description=body.description,
    )
    return OperationCostResponse.model_validate(updated)


@router.delete("/operation-costs/{cost_id}", status_code=204)
async def delete_operation_cost(
    request: Request,
    cost_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    await _authenticate_admin(request, db=db)
    row = await svc.get_cost(db, cost_id)
    if not row:
        raise HTTPException(status_code=404, detail="Operation cost not found")
    await svc.delete_cost(db, row)
