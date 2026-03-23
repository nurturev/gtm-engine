"""Learning logs router: submit, review, approve, and merge workflow discoveries."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.flexible import get_tenant_flexible
from server.core.database import get_db
from server.execution.learning_models import DynamicKnowledge, LearningLog

router = APIRouter(prefix="/api/v1/learning-logs", tags=["learning"])

# Admin tenant IDs — comma-separated env var
_ADMIN_TENANT_IDS: set[str] = set()


def _get_admin_ids() -> set[str]:
    global _ADMIN_TENANT_IDS
    if not _ADMIN_TENANT_IDS:
        raw = os.environ.get("ADMIN_TENANT_IDS", "")
        _ADMIN_TENANT_IDS = {t.strip() for t in raw.split(",") if t.strip()}
    return _ADMIN_TENANT_IDS


def _is_admin(tenant_id: str) -> bool:
    return tenant_id in _get_admin_ids()


def _require_admin(tenant_id: str) -> None:
    if not _is_admin(tenant_id):
        raise HTTPException(status_code=403, detail="Admin access required")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SubmitLearningRequest(BaseModel):
    category: str
    subcategory: str | None = None
    platform: str | None = None
    tool_name: str | None = None
    discovery: dict
    evidence: list[dict] = []
    source_workflow_id: str | None = None
    confidence: float = 0.5
    user_prompt: str | None = None


class ReviewLearningRequest(BaseModel):
    status: str  # approved | rejected
    notes: str | None = None


# ---------------------------------------------------------------------------
# Tenant endpoints (submit learnings)
# ---------------------------------------------------------------------------

@router.post("")
async def submit_learning(
    request: Request,
    body: SubmitLearningRequest,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Submit a learning discovered during a workflow."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)

    valid_categories = {
        "search_pattern", "api_quirk", "enrichment_strategy",
        "scraping_pattern", "data_mapping", "provider_behavior",
    }
    if body.category not in valid_categories:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Must be one of: {sorted(valid_categories)}",
        )

    log = LearningLog(
        tenant_id=tenant.id,
        category=body.category,
        subcategory=body.subcategory,
        platform=body.platform,
        tool_name=body.tool_name,
        discovery=body.discovery,
        evidence=body.evidence,
        source_workflow_id=body.source_workflow_id,
        confidence=max(0.0, min(1.0, body.confidence)),
        user_prompt=body.user_prompt,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    return {
        "status": "submitted",
        "id": str(log.id),
        "category": log.category,
        "platform": log.platform,
    }


# ---------------------------------------------------------------------------
# Admin endpoints (review, approve, merge)
# ---------------------------------------------------------------------------

@router.get("")
async def list_learning_logs(
    request: Request,
    status_filter: str = Query("pending", alias="status"),
    category: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List learning logs (admin only)."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)
    _require_admin(tenant.id)

    stmt = select(LearningLog).order_by(LearningLog.created_at.desc()).limit(limit)

    if status_filter != "all":
        stmt = stmt.where(LearningLog.status == status_filter)
    if category:
        stmt = stmt.where(LearningLog.category == category)

    result = await db.execute(stmt)
    logs = result.scalars().all()

    return {
        "learning_logs": [
            {
                "id": str(l.id),
                "tenant_id": l.tenant_id,
                "category": l.category,
                "subcategory": l.subcategory,
                "platform": l.platform,
                "tool_name": l.tool_name,
                "discovery": l.discovery,
                "evidence_count": len(l.evidence or []),
                "confidence": l.confidence,
                "status": l.status,
                "source_workflow_id": l.source_workflow_id,
                "user_prompt": l.user_prompt,
                "notes": l.notes,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ],
        "total": len(logs),
    }


@router.get("/{log_id}")
async def get_learning_log(
    log_id: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get full learning log with evidence (admin only)."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)
    _require_admin(tenant.id)

    result = await db.execute(select(LearningLog).where(LearningLog.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Learning log not found")

    return {
        "id": str(log.id),
        "tenant_id": log.tenant_id,
        "category": log.category,
        "subcategory": log.subcategory,
        "platform": log.platform,
        "tool_name": log.tool_name,
        "discovery": log.discovery,
        "evidence": log.evidence,
        "confidence": log.confidence,
        "status": log.status,
        "source_workflow_id": log.source_workflow_id,
        "user_prompt": log.user_prompt,
        "reviewed_by": log.reviewed_by,
        "reviewed_at": log.reviewed_at.isoformat() if log.reviewed_at else None,
        "merged_at": log.merged_at.isoformat() if log.merged_at else None,
        "notes": log.notes,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


@router.patch("/{log_id}")
async def review_learning_log(
    log_id: str,
    body: ReviewLearningRequest,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Approve or reject a learning log (admin only)."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)
    _require_admin(tenant.id)

    if body.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

    result = await db.execute(select(LearningLog).where(LearningLog.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Learning log not found")

    log.status = body.status
    log.reviewed_by = tenant.id
    log.reviewed_at = datetime.now(timezone.utc)
    if body.notes:
        log.notes = body.notes

    await db.commit()
    await db.refresh(log)

    return {"status": log.status, "id": str(log.id)}


@router.post("/{log_id}/merge")
async def merge_learning(
    log_id: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Merge an approved learning into dynamic knowledge (admin only).

    For search_pattern category, the platform field is used as the key.
    For other categories, a key is derived from category + platform + subcategory.
    """
    tenant, db = await get_tenant_flexible(request, token=token, db=db)
    _require_admin(tenant.id)

    result = await db.execute(select(LearningLog).where(LearningLog.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Learning log not found")

    if log.status != "approved":
        raise HTTPException(status_code=400, detail="Only approved learnings can be merged")

    if log.merged_at:
        raise HTTPException(status_code=400, detail="Already merged")

    # Derive the knowledge key
    if log.category == "search_pattern" and log.platform:
        key = log.platform
    else:
        parts = [log.category]
        if log.platform:
            parts.append(log.platform)
        if log.subcategory:
            parts.append(log.subcategory)
        key = "_".join(parts)

    # Upsert into dynamic_knowledge
    existing = await db.execute(
        select(DynamicKnowledge).where(
            DynamicKnowledge.category == log.category,
            DynamicKnowledge.key == key,
        )
    )
    dk = existing.scalar_one_or_none()

    if dk:
        dk.knowledge = log.discovery
        dk.source_learning_id = log.id
        dk.enabled = True
        dk.updated_at = datetime.now(timezone.utc)
    else:
        dk = DynamicKnowledge(
            category=log.category,
            key=key,
            knowledge=log.discovery,
            source_learning_id=log.id,
        )
        db.add(dk)

    log.status = "merged"
    log.merged_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(dk)

    # Refresh in-memory cache for search patterns so they're immediately available
    if log.category == "search_pattern":
        try:
            from server.execution.search_patterns import load_dynamic_patterns

            all_dk = await db.execute(
                select(DynamicKnowledge).where(
                    DynamicKnowledge.category == "search_pattern",
                    DynamicKnowledge.enabled == True,  # noqa: E712
                )
            )
            rows = all_dk.scalars().all()
            load_dynamic_patterns({row.key: row.knowledge for row in rows})
        except Exception:
            pass  # Non-fatal — cache will refresh on next server restart

    return {
        "status": "merged",
        "knowledge_id": str(dk.id),
        "category": dk.category,
        "key": dk.key,
    }


# ---------------------------------------------------------------------------
# Knowledge lookup (available to all users)
# ---------------------------------------------------------------------------

@router.get("/knowledge/{category}/{key}")
async def get_knowledge(
    category: str,
    key: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Look up approved knowledge by category and key (any authenticated user)."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)

    result = await db.execute(
        select(DynamicKnowledge).where(
            DynamicKnowledge.category == category,
            DynamicKnowledge.key == key,
            DynamicKnowledge.enabled == True,  # noqa: E712
        )
    )
    dk = result.scalar_one_or_none()
    if not dk:
        return {"found": False, "category": category, "key": key}

    return {
        "found": True,
        "category": dk.category,
        "key": dk.key,
        "knowledge": dk.knowledge,
        "updated_at": dk.updated_at.isoformat() if dk.updated_at else None,
    }
