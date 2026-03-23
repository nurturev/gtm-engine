"""App hosting service — deploy, serve, and manage hosted apps."""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.apps.models import HostedApp


def _slugify(name: str) -> str:
    """Convert name to URL-safe slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:60]


def _generate_app_token() -> tuple[str, str]:
    """Generate a secure app token and its SHA256 hash."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token, token_hash


async def deploy_app(
    db: AsyncSession,
    tenant_id: str,
    name: str,
    files: dict[str, str],
    dataset_ids: list[str],
    entry_point: str = "index.html",
) -> dict[str, Any]:
    """Deploy a new app or update an existing one."""
    slug = _slugify(name)

    # Check for existing app with same slug
    result = await db.execute(
        select(HostedApp).where(
            HostedApp.tenant_id == tenant_id,
            HostedApp.slug == slug,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing app
        existing.files = files
        existing.dataset_ids = [UUID(d) for d in dataset_ids]
        existing.entry_point = entry_point
        existing.status = "active"
        await db.commit()
        return {
            "id": str(existing.id),
            "name": existing.name,
            "slug": existing.slug,
            "app_token": existing.app_token,
            "app_url": f"/apps/{existing.app_token}/",
            "datasets_connected": len(dataset_ids),
            "files_count": len(files),
            "updated": True,
        }

    # Create new app
    token, token_hash = _generate_app_token()
    app = HostedApp(
        tenant_id=tenant_id,
        name=name,
        slug=slug,
        dataset_ids=[UUID(d) for d in dataset_ids],
        app_token=token,
        app_token_hash=token_hash,
        files=files,
        entry_point=entry_point,
    )
    db.add(app)
    await db.commit()

    return {
        "id": str(app.id),
        "name": name,
        "slug": slug,
        "app_token": token,
        "app_url": f"/apps/{token}/",
        "datasets_connected": len(dataset_ids),
        "files_count": len(files),
        "updated": False,
    }


async def get_app_by_token(db: AsyncSession, token: str) -> HostedApp | None:
    """Lookup app by raw token."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(
        select(HostedApp).where(
            HostedApp.app_token_hash == token_hash,
            HostedApp.status == "active",
        )
    )
    return result.scalar_one_or_none()


async def validate_app_token(
    db: AsyncSession, token: str, dataset_id: str
) -> tuple[bool, str | None]:
    """Validate that an app token can access a specific dataset.

    Returns (is_valid, tenant_id).
    """
    app = await get_app_by_token(db, token)
    if not app:
        return False, None

    # Check if the dataset is in the app's allowed list
    ds_uuid = UUID(dataset_id)
    if ds_uuid not in (app.dataset_ids or []):
        return False, None

    return True, app.tenant_id


async def list_apps(
    db: AsyncSession, tenant_id: str
) -> list[dict[str, Any]]:
    """List all hosted apps for a tenant."""
    result = await db.execute(
        select(HostedApp)
        .where(HostedApp.tenant_id == tenant_id, HostedApp.status == "active")
        .order_by(HostedApp.updated_at.desc())
    )
    apps = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "name": a.name,
            "slug": a.slug,
            "app_url": f"/apps/{a.app_token}/",
            "datasets_connected": len(a.dataset_ids or []),
            "files_count": len(a.files or {}),
            "entry_point": a.entry_point,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }
        for a in apps
    ]


async def delete_app(db: AsyncSession, tenant_id: str, app_id: str) -> bool:
    """Soft-delete an app."""
    result = await db.execute(
        select(HostedApp).where(
            HostedApp.id == app_id,
            HostedApp.tenant_id == tenant_id,
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        return False
    app.status = "deleted"
    await db.commit()
    return True


# ---- Content-type detection ----

_MIME_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain",
    ".xml": "application/xml",
    ".webp": "image/webp",
}


def get_content_type(path: str) -> str:
    """Detect content type from file extension."""
    for ext, mime in _MIME_TYPES.items():
        if path.lower().endswith(ext):
            return mime
    return "application/octet-stream"


def inject_nrev_context(html: str, app_token: str, api_base: str) -> str:
    """Inject NRV context variables into HTML files for client-side data access."""
    script = (
        f'<script>window.NRV_APP_TOKEN="{app_token}";'
        f'window.NRV_API_BASE="{api_base}";'
        f"window.NRV_DATASETS_URL=window.NRV_API_BASE+'/datasets';</script>"
    )
    # Inject before </head> or at the start of <body>
    if "</head>" in html:
        return html.replace("</head>", script + "</head>")
    if "<body" in html:
        idx = html.index("<body")
        end = html.index(">", idx) + 1
        return html[:end] + script + html[end:]
    return script + html
