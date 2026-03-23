"""Runs API — list workflows and their steps for the run logs dashboard."""

from __future__ import annotations

import csv
import io
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from jose import JWTError, jwt
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.models import Tenant
from server.core.config import settings
from server.core.database import get_db, set_tenant_context
from server.auth.flexible import get_tenant_flexible as _get_tenant_flexible
from server.execution.run_models import RunStep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["runs"])


@router.get("/runs")
async def list_workflows(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """List workflows (grouped run sessions) for this tenant.

    Each workflow represents a Claude Code session. Returns aggregated
    stats: step count, total credits, duration, status summary.
    """
    tenant, db = await _get_tenant_flexible(request, token=token, db=db)

    # Query: aggregate run_steps grouped by workflow_id
    stmt = (
        select(
            RunStep.workflow_id,
            func.max(RunStep.workflow_label).label("workflow_label"),
            func.count(RunStep.id).label("step_count"),
            func.sum(RunStep.credits_charged).label("total_credits"),
            func.sum(RunStep.duration_ms).label("total_duration_ms"),
            func.min(RunStep.created_at).label("started_at"),
            func.max(RunStep.created_at).label("last_step_at"),
            func.count(RunStep.id).filter(RunStep.status == "success").label("success_count"),
            func.count(RunStep.id).filter(RunStep.status == "failed").label("failed_count"),
            # Collect distinct tool names as a pseudo-summary
            func.array_agg(func.distinct(RunStep.tool_name)).label("tools_used"),
        )
        .where(RunStep.tenant_id == tenant.id)
        .group_by(RunStep.workflow_id)
        .order_by(desc(text("last_step_at")))
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(stmt)
    rows = result.all()

    workflows = []
    for row in rows:
        total = row.step_count or 0
        success = row.success_count or 0
        failed = row.failed_count or 0

        if failed > 0 and success == 0:
            wf_status = "failed"
        elif failed > 0:
            wf_status = "partial"
        else:
            wf_status = "success"

        workflows.append({
            "workflow_id": row.workflow_id,
            "workflow_label": row.workflow_label or None,
            "step_count": total,
            "success_count": success,
            "failed_count": failed,
            "status": wf_status,
            "total_credits": float(row.total_credits or 0),
            "total_duration_ms": row.total_duration_ms or 0,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "last_step_at": row.last_step_at.isoformat() if row.last_step_at else None,
            "tools_used": row.tools_used or [],
        })

    # Get total count for pagination
    count_stmt = (
        select(func.count(func.distinct(RunStep.workflow_id)))
        .where(RunStep.tenant_id == tenant.id)
    )
    total_count = await db.scalar(count_stmt) or 0

    return JSONResponse({
        "workflows": workflows,
        "total": total_count,
        "limit": limit,
        "offset": offset,
    })


@router.get("/runs/{workflow_id}")
async def get_workflow_steps(
    request: Request,
    workflow_id: str,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get all steps for a specific workflow, ordered chronologically."""
    tenant, db = await _get_tenant_flexible(request, token=token, db=db)

    stmt = (
        select(RunStep)
        .where(
            RunStep.tenant_id == tenant.id,
            RunStep.workflow_id == workflow_id,
        )
        .order_by(RunStep.created_at)
    )
    result = await db.execute(stmt)
    steps = result.scalars().all()

    if not steps:
        return JSONResponse(
            {"error": f"Workflow '{workflow_id}' not found"},
            status_code=404,
        )

    step_list = []
    total_credits = 0.0
    total_duration = 0

    for step in steps:
        credits = float(step.credits_charged or 0)
        duration = step.duration_ms or 0
        total_credits += credits
        total_duration += duration

        step_list.append({
            "id": str(step.id),
            "tool_name": step.tool_name,
            "operation": step.operation,
            "provider": step.provider,
            "params_summary": step.params_summary or {},
            "result_summary": step.result_summary or {},
            "status": step.status,
            "error_message": step.error_message,
            "credits_charged": credits,
            "duration_ms": duration,
            "created_at": step.created_at.isoformat() if step.created_at else None,
        })

    return JSONResponse({
        "workflow_id": workflow_id,
        "steps": step_list,
        "summary": {
            "step_count": len(step_list),
            "total_credits": total_credits,
            "total_duration_ms": total_duration,
            "started_at": step_list[0]["created_at"] if step_list else None,
            "last_step_at": step_list[-1]["created_at"] if step_list else None,
        },
    })


# ------------------------------------------------------------------
# Column metadata for a workflow's results
# ------------------------------------------------------------------

@router.get("/runs/{workflow_id}/metadata")
async def get_workflow_metadata(
    workflow_id: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get column metadata for all result arrays in a workflow's steps."""
    from server.execution.column_metadata import compute_column_metadata

    tenant, db = await _get_tenant_flexible(request, token, db)
    await set_tenant_context(db, tenant.id)

    result = await db.execute(
        select(RunStep)
        .where(RunStep.workflow_id == workflow_id, RunStep.tenant_id == tenant.id)
        .order_by(RunStep.created_at)
    )
    steps = result.scalars().all()
    if not steps:
        raise HTTPException(status_code=404, detail="Workflow not found")

    steps_metadata = []
    for s in steps:
        summary = s.result_summary or {}
        rows = summary.get("results", [])
        metadata = compute_column_metadata(rows) if rows else {}
        steps_metadata.append({
            "step_index": len(steps_metadata),
            "tool_name": s.tool_name,
            "operation": s.operation,
            "status": s.status,
            "row_count": len(rows),
            "columns": metadata,
        })

    return JSONResponse({"workflow_id": workflow_id, "steps": steps_metadata})


# ------------------------------------------------------------------
# CSV download for a workflow's results
# ------------------------------------------------------------------

@router.get("/runs/{workflow_id}/steps/{step_id}/csv")
async def download_step_csv(
    workflow_id: str,
    step_id: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Download result rows from a single workflow step as CSV."""
    tenant, db = await _get_tenant_flexible(request, token, db)
    await set_tenant_context(db, tenant.id)

    result = await db.execute(
        select(RunStep).where(
            RunStep.id == step_id,
            RunStep.workflow_id == workflow_id,
            RunStep.tenant_id == tenant.id,
        )
    )
    step = result.scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")

    summary = step.result_summary or {}
    rows = summary.get("results", [])

    # For single-record results (person/company enrichment), wrap in a list
    if not rows:
        for key in ("person", "company", "profile", "data"):
            if key in summary and isinstance(summary[key], dict):
                rows = [summary[key]]
                break

    if not rows:
        output = io.StringIO()
        output.write("No result data in this step\n")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=step-{step_id[:8]}.csv"},
        )

    # Union all column names preserving order
    all_cols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                all_cols.append(k)
                seen.add(k)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_cols, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    output.seek(0)
    tool = step.tool_name or "step"
    safe_tool = "".join(c if c.isalnum() or c in "-_" else "" for c in tool)[:30]

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={safe_tool}-{step_id[:8]}.csv"},
    )


@router.get("/runs/{workflow_id}/csv")
async def download_workflow_csv(
    workflow_id: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Download all result rows from a workflow as CSV."""
    tenant, db = await _get_tenant_flexible(request, token, db)
    await set_tenant_context(db, tenant.id)

    result = await db.execute(
        select(RunStep)
        .where(RunStep.workflow_id == workflow_id, RunStep.tenant_id == tenant.id)
        .order_by(RunStep.created_at)
    )
    steps = result.scalars().all()
    if not steps:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Collect all result rows across steps
    all_rows: list[dict] = []
    for s in steps:
        summary = s.result_summary or {}
        rows = summary.get("results", [])
        for row in rows:
            row_with_meta = {"_tool": s.tool_name, "_step": s.operation or "", **row}
            all_rows.append(row_with_meta)

    if not all_rows:
        # Return empty CSV with just a header
        output = io.StringIO()
        output.write("No result rows in this workflow\n")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=workflow-{workflow_id[:8]}.csv"},
        )

    # Union all column names
    all_cols: list[str] = []
    seen: set[str] = set()
    for row in all_rows:
        for k in row.keys():
            if k not in seen:
                all_cols.append(k)
                seen.add(k)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_cols, extrasaction="ignore")
    writer.writeheader()
    for row in all_rows:
        writer.writerow(row)

    output.seek(0)
    label = steps[0].workflow_label or workflow_id[:8]
    safe_label = "".join(c if c.isalnum() or c in "-_ " else "" for c in label).strip().replace(" ", "-")[:40]

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={safe_label}.csv"},
    )
