"""Scripts router: CRUD for reusable parameterized workflow scripts."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.flexible import get_tenant_flexible
from server.core.database import get_db
from server.execution.script_models import Script

router = APIRouter(prefix="/api/v1/scripts", tags=["scripts"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert a name to a URL-safe slug."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-")[:80]


def _script_to_dict(s: Script, include_steps: bool = False) -> dict[str, Any]:
    """Serialize a Script to a JSON-safe dict."""
    d: dict[str, Any] = {
        "id": str(s.id),
        "name": s.name,
        "slug": s.slug,
        "description": s.description,
        "parameter_count": len(s.parameters or []),
        "step_count": len(s.steps or []),
        "tags": s.tags or [],
        "run_count": s.run_count,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "source_workflow_id": s.source_workflow_id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }
    if include_steps:
        d["parameters"] = s.parameters or []
        d["steps"] = s.steps or []
    return d


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateScriptRequest(BaseModel):
    name: str
    description: str | None = None
    parameters: list[dict] = []
    steps: list[dict] = []
    source_workflow_id: str | None = None
    tags: list[str] = []


class UpdateScriptRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    parameters: list[dict] | None = None
    steps: list[dict] | None = None
    tags: list[str] | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("")
async def create_script(
    request: Request,
    body: CreateScriptRequest,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new script."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)

    slug = _slugify(body.name)
    if not slug:
        raise HTTPException(status_code=400, detail="Invalid script name")

    # Check for slug collision
    existing = await db.execute(
        select(Script).where(Script.tenant_id == tenant.id, Script.slug == slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Script with slug '{slug}' already exists. Choose a different name.",
        )

    script = Script(
        tenant_id=tenant.id,
        name=body.name,
        slug=slug,
        description=body.description,
        parameters=body.parameters,
        steps=body.steps,
        source_workflow_id=body.source_workflow_id,
        tags=body.tags or [],
    )
    db.add(script)
    await db.commit()
    await db.refresh(script)

    return {
        "status": "created",
        **_script_to_dict(script, include_steps=True),
    }


@router.get("")
async def list_scripts(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all scripts for the tenant."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)

    result = await db.execute(
        select(Script)
        .where(Script.tenant_id == tenant.id)
        .order_by(Script.updated_at.desc())
    )
    scripts = result.scalars().all()

    return {
        "scripts": [_script_to_dict(s) for s in scripts],
        "total": len(scripts),
    }


@router.get("/{id_or_slug}")
async def get_script(
    id_or_slug: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get full script definition by ID or slug."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)

    # Try UUID first, then slug
    script = None
    try:
        import uuid
        uuid.UUID(id_or_slug)
        result = await db.execute(
            select(Script).where(Script.id == id_or_slug, Script.tenant_id == tenant.id)
        )
        script = result.scalar_one_or_none()
    except ValueError:
        pass

    if script is None:
        result = await db.execute(
            select(Script).where(Script.slug == id_or_slug, Script.tenant_id == tenant.id)
        )
        script = result.scalar_one_or_none()

    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")

    return _script_to_dict(script, include_steps=True)


@router.patch("/{id_or_slug}")
async def update_script(
    id_or_slug: str,
    body: UpdateScriptRequest,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update a script's name, description, parameters, or steps."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)

    # Find the script
    script = None
    try:
        import uuid
        uuid.UUID(id_or_slug)
        result = await db.execute(
            select(Script).where(Script.id == id_or_slug, Script.tenant_id == tenant.id)
        )
        script = result.scalar_one_or_none()
    except ValueError:
        pass

    if script is None:
        result = await db.execute(
            select(Script).where(Script.slug == id_or_slug, Script.tenant_id == tenant.id)
        )
        script = result.scalar_one_or_none()

    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")

    if body.name is not None:
        script.name = body.name
        script.slug = _slugify(body.name)
    if body.description is not None:
        script.description = body.description
    if body.parameters is not None:
        script.parameters = body.parameters
    if body.steps is not None:
        script.steps = body.steps
    if body.tags is not None:
        script.tags = body.tags

    await db.commit()
    await db.refresh(script)

    return {"status": "updated", **_script_to_dict(script, include_steps=True)}


@router.delete("/{id_or_slug}")
async def delete_script(
    id_or_slug: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Delete a script."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)

    script = None
    try:
        import uuid
        uuid.UUID(id_or_slug)
        result = await db.execute(
            select(Script).where(Script.id == id_or_slug, Script.tenant_id == tenant.id)
        )
        script = result.scalar_one_or_none()
    except ValueError:
        pass

    if script is None:
        result = await db.execute(
            select(Script).where(Script.slug == id_or_slug, Script.tenant_id == tenant.id)
        )
        script = result.scalar_one_or_none()

    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")

    await db.delete(script)
    await db.commit()

    return {"status": "deleted", "slug": id_or_slug}


@router.post("/{id_or_slug}/run")
async def record_script_run(
    id_or_slug: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Record that a script was executed (bumps run_count, sets last_run_at)."""
    tenant, db = await get_tenant_flexible(request, token=token, db=db)

    script = None
    try:
        import uuid
        uuid.UUID(id_or_slug)
        result = await db.execute(
            select(Script).where(Script.id == id_or_slug, Script.tenant_id == tenant.id)
        )
        script = result.scalar_one_or_none()
    except ValueError:
        pass

    if script is None:
        result = await db.execute(
            select(Script).where(Script.slug == id_or_slug, Script.tenant_id == tenant.id)
        )
        script = result.scalar_one_or_none()

    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")

    script.run_count = (script.run_count or 0) + 1
    script.last_run_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(script)

    return {
        "status": "recorded",
        "run_count": script.run_count,
        "last_run_at": script.last_run_at.isoformat(),
    }
