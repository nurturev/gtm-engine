"""Tenant console router — serves the Jinja2 dashboard UI."""

from __future__ import annotations

import logging
from pathlib import Path
from string import Template
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.models import Tenant, User
from server.billing.models import CreditBalance, CreditLedger
from server.core.config import settings
from server.core.database import get_db, set_tenant_context
from server.dashboards.models import Dashboard as DashboardModel
from server.dashboards.service import render_dashboard_html, render_password_page, verify_password
from server.data.dataset_models import Dataset, DatasetRow
from server.execution.run_models import RunStep
from server.execution.schedule_models import ScheduledWorkflow
from server.vault.models import TenantKey

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Integration catalog (mirrored from vault/connections.py for the UI)
# ---------------------------------------------------------------------------

INTEGRATION_CATALOG = {
    # --- Communication ---
    "slack": {
        "name": "Slack",
        "category": "communication",
        "icon": "\U0001f4ac",
        "description": "Send messages, read channels, manage workflows",
        "composio_app": "SLACK",
    },
    "gmail": {
        "name": "Gmail",
        "category": "communication",
        "icon": "\U0001f4e7",
        "description": "Send and read emails, manage labels",
        "composio_app": "GMAIL",
    },
    # microsoft_teams: not available in Composio yet
    # --- Data & Sheets ---
    "google_sheets": {
        "name": "Google Sheets",
        "category": "data",
        "icon": "\U0001f4ca",
        "description": "Read/write spreadsheets, create reports",
        "composio_app": "GOOGLESHEETS",
    },
    "google_docs": {
        "name": "Google Docs",
        "category": "data",
        "icon": "\U0001f4c4",
        "description": "Create and edit documents",
        "composio_app": "GOOGLEDOCS",
    },
    "airtable": {
        "name": "Airtable",
        "category": "data",
        "icon": "\U0001f4cb",
        "description": "Flexible databases, views, and automations",
        "composio_app": "AIRTABLE",
    },
    "google_drive": {
        "name": "Google Drive",
        "category": "data",
        "icon": "\U0001f4c1",
        "description": "File storage, sharing, and collaboration",
        "composio_app": "GOOGLEDRIVE",
    },
    # --- CRM ---
    "hubspot": {
        "name": "HubSpot",
        "category": "crm",
        "icon": "\U0001f536",
        "description": "Manage contacts, deals, companies",
        "composio_app": "HUBSPOT",
    },
    "salesforce": {
        "name": "Salesforce",
        "category": "crm",
        "icon": "\u2601\ufe0f",
        "description": "Access CRM data, manage leads and opportunities",
        "composio_app": "SALESFORCE",
    },
    # pipedrive: no managed OAuth in Composio yet
    "attio": {
        "name": "Attio",
        "category": "crm",
        "icon": "\U0001f4ce",
        "description": "Relationship-first CRM for modern teams",
        "composio_app": "ATTIO",
    },
    # instantly, lemlist, smartlead: API-key based, not OAuth — managed via Keys tab
    # apollo, clearbit, zoominfo: API-key based — managed via Keys tab
    # --- Project Management ---
    "linear": {
        "name": "Linear",
        "category": "project",
        "icon": "\U0001f4d0",
        "description": "Issue tracking and project management",
        "composio_app": "LINEAR",
    },
    "notion": {
        "name": "Notion",
        "category": "project",
        "icon": "\U0001f4dd",
        "description": "Databases, pages, and workspace management",
        "composio_app": "NOTION",
    },
    "clickup": {
        "name": "ClickUp",
        "category": "project",
        "icon": "\u2705",
        "description": "Tasks, docs, goals, and whiteboards",
        "composio_app": "CLICKUP",
    },
    "asana": {
        "name": "Asana",
        "category": "project",
        "icon": "\U0001f3af",
        "description": "Project and task management",
        "composio_app": "ASANA",
    },
    # zapier, make: not available in Composio managed OAuth
    # --- Calendar ---
    "google_calendar": {
        "name": "Google Calendar",
        "category": "calendar",
        "icon": "\U0001f4c5",
        "description": "Schedule meetings and manage events",
        "composio_app": "GOOGLECALENDAR",
    },
    "calendly": {
        "name": "Calendly",
        "category": "calendar",
        "icon": "\U0001f553",
        "description": "Meeting scheduling and booking links",
        "composio_app": "CALENDLY",
    },
}

# Known enrichment providers that may have platform keys
from server.core.vendor_catalog import VENDOR_CATALOG, VENDOR_CATEGORIES, INTEGRATED_PROVIDERS, get_vendors_by_category

# Legacy list kept for backward compat; new code uses VENDOR_CATALOG
ENRICHMENT_PROVIDERS = list(VENDOR_CATALOG.keys())

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["console"])

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve favicon.ico from console static assets."""
    return FileResponse(_STATIC_DIR / "favicon.ico", media_type="image/x-icon")


# Reverse map: COMPOSIO_APP_NAME → catalog_key (e.g. "GMAIL" → "gmail")
_composio_to_catalog = {
    info["composio_app"]: key
    for key, info in INTEGRATION_CATALOG.items()
    if "composio_app" in info
}

# ---------------------------------------------------------------------------
# Auth helper — accepts JWT from query param or Authorization header
# ---------------------------------------------------------------------------


_COOKIE_NAME = "nrev_session"
_LOGIN_URL = "/api/v1/auth/login"


class _AuthFailRedirect(Exception):
    """Raised when browser auth fails and we should redirect to login."""


async def _authenticate_console(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    allow_redirect: bool = True,
) -> tuple[Tenant, "User | None", AsyncSession]:
    """Authenticate via cookie, query param, or Authorization header.

    Returns (Tenant, User | None, AsyncSession).  The User is resolved
    from the JWT ``sub`` claim when available.

    For browser page requests (allow_redirect=True), redirects to the
    login page instead of returning raw JSON errors.
    For API/fetch calls (allow_redirect=False), returns 401 JSON.
    """
    jwt_token: str | None = None

    # 1. Cookie (primary for browser sessions)
    jwt_token = request.cookies.get(_COOKIE_NAME)

    # 2. Query param (legacy / API)
    if not jwt_token and token:
        jwt_token = token

    # 3. Authorization header (API calls from JS)
    if not jwt_token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            jwt_token = auth_header.removeprefix("Bearer ")

    if not jwt_token:
        if allow_redirect:
            raise _AuthFailRedirect()
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
        if allow_redirect:
            raise _AuthFailRedirect()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
        )

    tenant_id: str | None = payload.get("tenant_id")
    if not tenant_id:
        if allow_redirect:
            raise _AuthFailRedirect()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # Resolve the User from JWT sub claim
    user: User | None = None
    user_id = payload.get("sub")
    if user_id:
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()

    await set_tenant_context(db, tenant.id)
    return tenant, user, db


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/console", response_class=HTMLResponse)
async def console_root(request: Request):
    """Redirect /console to the user's tenant dashboard using the session cookie."""
    jwt_token = request.cookies.get(_COOKIE_NAME)
    if not jwt_token:
        return RedirectResponse(url=_LOGIN_URL, status_code=302)
    try:
        payload = jwt.decode(
            jwt_token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
        )
        tenant_id = payload.get("tenant_id")
        if tenant_id:
            return RedirectResponse(url=f"/console/{tenant_id}", status_code=302)
    except JWTError:
        pass
    response = RedirectResponse(url=_LOGIN_URL, status_code=302)
    response.delete_cookie(_COOKIE_NAME, path="/")
    return response


@router.get("/console/{tenant_id}", response_class=HTMLResponse)
async def tenant_dashboard(
    request: Request,
    tenant_id: str,
    tab: str = Query("keys", pattern="^(keys|connections|usage|runs|datasets|dashboards|team)$"),
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Serve the tenant console dashboard."""

    try:
        tenant, current_user, db = await _authenticate_console(request, token=token, db=db, allow_redirect=True)
    except _AuthFailRedirect:
        response = RedirectResponse(url=_LOGIN_URL, status_code=302)
        response.delete_cookie(_COOKIE_NAME, path="/")
        return response

    # Verify the URL tenant_id matches the token
    if tenant.id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token tenant_id does not match URL",
        )

    # ------------------------------------------------------------------
    # API Keys tab data
    # ------------------------------------------------------------------
    keys_result = await db.execute(
        select(TenantKey).where(TenantKey.tenant_id == tenant.id)
    )
    tenant_keys = keys_result.scalars().all()
    byok_providers = {k.provider: k for k in tenant_keys}

    providers = []
    byok_count = 0
    platform_count = 0
    unavailable_count = 0

    for prov_name, vendor_info in VENDOR_CATALOG.items():
        tk = byok_providers.get(prov_name)
        if tk and tk.status == "active":
            using = "byok"
            prov_status = "active"
            fingerprint = f"...{tk.key_hint}" if tk.key_hint else "-"
            byok_count += 1
        elif _has_platform_key(prov_name):
            using = "platform"
            prov_status = "available"
            fingerprint = "-"
            platform_count += 1
        elif prov_name not in INTEGRATED_PROVIDERS:
            using = "coming_soon"
            prov_status = "coming_soon"
            fingerprint = "-"
        else:
            using = "none"
            prov_status = "unavailable"
            fingerprint = "-"
            unavailable_count += 1

        providers.append({
            "provider": prov_name,
            "display_name": vendor_info.get("name", prov_name),
            "category": vendor_info.get("category", "other"),
            "description": vendor_info.get("description", ""),
            "has_platform_key": vendor_info.get("platform_key", False),
            "using": using,
            "status": prov_status,
            "fingerprint": fingerprint,
        })

    key_summary = {
        "using_my_keys": byok_count,
        "using_platform": platform_count,
        "available_to_provision": sum(1 for p in providers if p["status"] == "unavailable"),
        "total_vendors": len(VENDOR_CATALOG),
    }

    # Group providers by category for template
    vendor_categories = {
        cat: {"name": meta["name"], "icon": meta["icon"], "description": meta["description"]}
        for cat, meta in VENDOR_CATEGORIES.items()
    }

    # ------------------------------------------------------------------
    # Connections tab data — check Composio for active connections
    # ------------------------------------------------------------------

    active_connections: dict[str, str] = {}  # catalog_key → status
    connection_users: dict[str, list[str]] = {}  # catalog_key → [user_emails]
    connection_accounts: dict[str, list[dict]] = {}  # catalog_key → [{connection_id, email, entity_id, status}]
    composio_key = settings.COMPOSIO_API_KEY

    # Check user_connections table for entity→email mapping + attribution
    entity_to_email: dict[str, str] = {}
    try:
        from server.connections.models import UserConnection
        uc_result = await db.execute(
            select(UserConnection).where(
                UserConnection.tenant_id == tenant.id,
                UserConnection.status == "active",
            )
        )
        for uc in uc_result.scalars().all():
            connection_users.setdefault(uc.app_id, []).append(uc.user_email)
            entity_to_email[uc.composio_entity_id] = uc.user_email
    except Exception:
        # user_connections table may not exist yet (migration 013)
        await db.rollback()

    if composio_key:
        try:
            entity_ids_to_check: set[str] = set()
            try:
                uc_entities_result = await db.execute(
                    select(UserConnection.composio_entity_id).where(
                        UserConnection.tenant_id == tenant.id,
                    ).distinct()
                )
                for (eid,) in uc_entities_result.all():
                    entity_ids_to_check.add(eid)
            except Exception:
                await db.rollback()
            entity_ids_to_check.add(f"nrev-{tenant.id}")
            entity_ids_to_check.add(f"nrv-{tenant.id}")

            seen_account_ids: set[str] = set()
            async with httpx.AsyncClient(timeout=10) as client:
                for check_eid in entity_ids_to_check:
                    try:
                        resp = await client.get(
                            f"{COMPOSIO_V3}/connected_accounts",
                            headers={"x-api-key": composio_key},
                            params={"entityId": check_eid},
                        )
                        if resp.status_code != 200:
                            continue
                        data = resp.json()
                        items = data.get("items", []) if isinstance(data, dict) else data
                        for item in items:
                            acct_id = item.get("id", "")
                            if acct_id in seen_account_ids:
                                continue
                            seen_account_ids.add(acct_id)

                            toolkit_slug = (item.get("toolkit") or {}).get("slug", "")
                            catalog_key = _composio_to_catalog.get(
                                toolkit_slug.upper(), toolkit_slug,
                            )
                            s = item.get("status", "").upper()
                            if s in ("EXPIRED", "FAILED", "REVOKED"):
                                continue
                            if s == "ACTIVE":
                                active_connections[catalog_key] = "active"
                            elif s in ("INITIATED", "PENDING"):
                                if active_connections.get(catalog_key) != "active":
                                    active_connections[catalog_key] = "initiated"

                            # Track per-account details for drawer
                            # Try multiple entity ID sources for email lookup:
                            # check_eid is the query param, item may have a
                            # different entityId; also check nrev-/nrv- variants.
                            item_eid = item.get("entityId", "")
                            acct_email = (
                                entity_to_email.get(check_eid, "")
                                or entity_to_email.get(item_eid, "")
                            )
                            # Fallback: try nrev-/nrv- prefix swap
                            if not acct_email:
                                for eid_variant in (check_eid, item_eid):
                                    if eid_variant.startswith("nrv-"):
                                        acct_email = entity_to_email.get("nrev-" + eid_variant[4:], "")
                                    elif eid_variant.startswith("nrev-"):
                                        acct_email = entity_to_email.get("nrv-" + eid_variant[5:], "")
                                    if acct_email:
                                        break
                            connection_accounts.setdefault(catalog_key, []).append({
                                "connection_id": acct_id,
                                "email": acct_email,
                                "entity_id": check_eid,
                                "status": s,
                            })
                    except Exception:
                        continue
        except Exception:
            logger.warning("Failed to fetch Composio connections for %s", tenant.id)

    connections = []
    conn_active = 0
    conn_pending = 0

    for app_id, info in INTEGRATION_CATALOG.items():
        composio_status = active_connections.get(app_id)
        if composio_status == "active":
            conn_status = "active"
        elif composio_status in ("initiated", "pending"):
            conn_status = "pending_oauth"
        else:
            conn_status = "not_connected"

        connections.append({
            "app_id": app_id,
            "name": info["name"],
            "icon": info["icon"],
            "category": info["category"],
            "description": info["description"],
            "status": conn_status,
            "total_calls": 0,
            "connected_by": connection_users.get(app_id, []),
            "accounts": connection_accounts.get(app_id, []),
        })
        if conn_status == "active":
            conn_active += 1
        elif conn_status in ("pending", "pending_oauth"):
            conn_pending += 1

    conn_summary = {
        "active": conn_active,
        "pending": conn_pending,
        "available": len(connections) - conn_active - conn_pending,
    }

    # ------------------------------------------------------------------
    # Usage & Billing tab data
    # ------------------------------------------------------------------
    balance_result = await db.execute(
        select(CreditBalance).where(CreditBalance.tenant_id == tenant.id)
    )
    balance_row = balance_result.scalar_one_or_none()

    ledger_result = await db.execute(
        select(CreditLedger)
        .where(CreditLedger.tenant_id == tenant.id)
        .order_by(CreditLedger.created_at.desc())
        .limit(200)
    )
    raw_ledger = ledger_result.scalars().all()

    usage = {
        "total_calls": 0,
        "spend_cap": None,
        "balance": float(balance_row.balance) if balance_row else 0.0,
        "spend_this_month": float(balance_row.spend_this_month) if balance_row else 0.0,
    }

    # Build vault_usage from ledger grouped by operation (provider)
    vault_usage: dict = {}
    for entry in raw_ledger:
        prov = entry.operation or "unknown"
        if prov not in vault_usage:
            vault_usage[prov] = {"platform_calls": 0, "byok_calls": 0, "total": 0}
        if entry.entry_type == "debit":
            vault_usage[prov]["platform_calls"] += 1
            vault_usage[prov]["total"] += 1
            usage["total_calls"] += 1

    # Group ledger entries by workflow for cleaner display
    # Only show holds and credits at the summary level; debits/releases are internal
    ledger_groups: list[dict] = []
    _wf_groups: dict[str, dict] = {}
    ungrouped: list = []
    for entry in raw_ledger:
        wf_id = getattr(entry, "workflow_id", None)
        if wf_id and entry.entry_type in ("hold", "debit"):
            if wf_id not in _wf_groups:
                _wf_groups[wf_id] = {
                    "workflow_id": wf_id,
                    "operations": {},
                    "total_credits": 0.0,
                    "entry_count": 0,
                    "first_at": entry.created_at,
                    "last_at": entry.created_at,
                    "balance_after": float(entry.balance_after),
                }
            g = _wf_groups[wf_id]
            # Only count holds to avoid double-counting (hold + debit = same op)
            if entry.entry_type == "hold":
                op = entry.operation or "unknown"
                g["operations"][op] = g["operations"].get(op, 0) + 1
                g["total_credits"] += float(entry.amount)
                g["entry_count"] += 1
            if entry.created_at < g["first_at"]:
                g["first_at"] = entry.created_at
            if entry.created_at > g["last_at"]:
                g["last_at"] = entry.created_at
            g["balance_after"] = min(g["balance_after"], float(entry.balance_after))
        elif entry.entry_type == "credit":
            ungrouped.append(entry)
        elif entry.entry_type == "debit" and not wf_id:
            # Debit without workflow_id — show as individual confirmed charge
            ungrouped.append(entry)

    # Build sorted list: workflow groups + credit top-ups, newest first
    for g in _wf_groups.values():
        ops_str = ", ".join(f"{op} x{cnt}" for op, cnt in g["operations"].items())
        ledger_groups.append({
            "type": "workflow",
            "date": g["last_at"],
            "operations": ops_str,
            "total_credits": g["total_credits"],
            "entry_count": g["entry_count"],
            "balance_after": g["balance_after"],
            "workflow_id": g["workflow_id"],
        })
    for entry in ungrouped:
        if entry.entry_type == "credit":
            grp_type = "credit"
            ops_label = entry.operation or "top-up"
        else:
            grp_type = "operation"
            ops_label = entry.operation or "unknown"
        ledger_groups.append({
            "type": grp_type,
            "date": entry.created_at,
            "operations": ops_label,
            "total_credits": float(entry.amount),
            "entry_count": 1,
            "balance_after": float(entry.balance_after),
            "workflow_id": None,
        })
    ledger_groups.sort(key=lambda x: x["date"], reverse=True)
    ledger_groups = ledger_groups[:30]
    # Keep raw entries for backward compatibility
    ledger_entries = raw_ledger[:50]

    conn_usage = {"total_calls": 0, "breakdown": {}}

    # ------------------------------------------------------------------
    # Runs tab data — recent workflows with step counts
    # ------------------------------------------------------------------
    from sqlalchemy import desc as sa_desc

    workflows_result = await db.execute(
        select(
            RunStep.workflow_id,
            func.count(RunStep.id).label("step_count"),
            func.sum(RunStep.credits_charged).label("total_credits"),
            func.sum(RunStep.duration_ms).label("total_duration_ms"),
            func.min(RunStep.created_at).label("started_at"),
            func.max(RunStep.created_at).label("last_step_at"),
            func.count(RunStep.id).filter(RunStep.status == "success").label("success_count"),
            func.count(RunStep.id).filter(RunStep.status == "failed").label("failed_count"),
            func.array_agg(func.distinct(RunStep.tool_name)).label("tools_used"),
        )
        .where(RunStep.tenant_id == tenant.id)
        .group_by(RunStep.workflow_id)
        .order_by(sa_desc("last_step_at"))
        .limit(20)
    )
    workflow_rows = workflows_result.all()

    workflows = []
    total_workflows = 0
    total_run_steps = 0
    for row in workflow_rows:
        total_workflows += 1
        steps = row.step_count or 0
        total_run_steps += steps
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
            "step_count": steps,
            "success_count": success,
            "failed_count": failed,
            "status": wf_status,
            "total_credits": float(row.total_credits or 0),
            "total_duration_ms": row.total_duration_ms or 0,
            "started_at": row.started_at,
            "last_step_at": row.last_step_at,
            "tools_used": row.tools_used or [],
        })

    runs_summary = {
        "total_workflows": total_workflows,
        "total_steps": total_run_steps,
    }

    # ------------------------------------------------------------------
    # Datasets tab data
    # ------------------------------------------------------------------
    datasets_result = await db.execute(
        select(Dataset)
        .where(Dataset.tenant_id == tenant.id, Dataset.status == "active")
        .order_by(Dataset.updated_at.desc())
    )
    tenant_datasets = datasets_result.scalars().all()

    datasets_list = []
    total_dataset_rows = 0
    for ds in tenant_datasets:
        total_dataset_rows += ds.row_count or 0
        datasets_list.append({
            "id": str(ds.id),
            "name": ds.name,
            "slug": ds.slug,
            "description": ds.description or "",
            "columns": ds.columns or [],
            "dedup_key": ds.dedup_key,
            "row_count": ds.row_count or 0,
            "created_at": ds.created_at,
            "updated_at": ds.updated_at,
        })

    datasets_summary = {
        "total_datasets": len(datasets_list),
        "total_rows": total_dataset_rows,
    }

    # ------------------------------------------------------------------
    # Scheduled Workflows data
    # ------------------------------------------------------------------
    schedules_result = await db.execute(
        select(ScheduledWorkflow)
        .where(ScheduledWorkflow.tenant_id == tenant.id)
        .order_by(ScheduledWorkflow.updated_at.desc())
    )
    scheduled_wfs = schedules_result.scalars().all()

    scheduled_workflows_list = [
        {
            "name": sw.name,
            "description": sw.description or "",
            "schedule": sw.schedule or "",
            "cron": sw.cron_expression or "",
            "enabled": sw.enabled,
            "next_run": sw.next_run_at.strftime('%b %d, %H:%M') if sw.next_run_at else None,
            "last_run": sw.last_run_at.strftime('%b %d, %H:%M') if sw.last_run_at else None,
            "run_count": sw.run_count or 0,
        }
        for sw in scheduled_wfs
    ]

    # ------------------------------------------------------------------
    # Dashboards tab data
    # ------------------------------------------------------------------
    dashboards_result = await db.execute(
        select(DashboardModel)
        .where(DashboardModel.tenant_id == tenant.id, DashboardModel.status == "active")
        .order_by(DashboardModel.updated_at.desc())
    )
    tenant_dashboards = dashboards_result.scalars().all()

    # Map dataset IDs to names for display
    dash_dataset_ids = [d.dataset_id for d in tenant_dashboards if d.dataset_id]
    dash_ds_names: dict[str, str] = {}
    if dash_dataset_ids:
        dash_ds_result = await db.execute(
            select(Dataset).where(Dataset.id.in_(dash_dataset_ids))
        )
        for ds in dash_ds_result.scalars().all():
            dash_ds_names[str(ds.id)] = ds.name

    dashboards_list = []
    for d in tenant_dashboards:
        config = d.config or {}
        widgets = config.get("widgets", [])
        dashboards_list.append({
            "id": str(d.id),
            "name": d.name,
            "dataset_id": str(d.dataset_id) if d.dataset_id else None,
            "dataset_name": dash_ds_names.get(str(d.dataset_id), "—") if d.dataset_id else "—",
            "widget_count": len(widgets),
            "read_token_hash": d.read_token_hash[:8] if d.read_token_hash else "",
            "read_token": d.read_token or "",
            "created_at": d.created_at,
            "updated_at": d.updated_at,
        })

    dashboards_summary = {
        "total_dashboards": len(dashboards_list),
    }

    # ------------------------------------------------------------------
    # Team tab data — list all users in the tenant
    # ------------------------------------------------------------------
    team_result = await db.execute(
        select(User)
        .where(User.tenant_id == tenant.id)
        .order_by(User.created_at)
    )
    team_members = [
        {
            "id": u.id,
            "email": u.email,
            "name": u.name or u.email.split("@")[0],
            "avatar_url": u.avatar_url,
            "role": u.role or "member",
            "created_at": u.created_at,
        }
        for u in team_result.scalars().all()
    ]

    # ------------------------------------------------------------------
    # Runs tab — add user attribution per workflow
    # ------------------------------------------------------------------
    # Get the user_id from the first step of each workflow for attribution
    workflow_user_ids = set()
    wf_user_map: dict[str, str | None] = {}
    if workflows:
        wf_ids = [w["workflow_id"] for w in workflows]
        from sqlalchemy import distinct
        wf_user_result = await db.execute(
            select(RunStep.workflow_id, RunStep.user_id)
            .where(RunStep.tenant_id == tenant.id, RunStep.workflow_id.in_(wf_ids))
            .distinct(RunStep.workflow_id)
            .order_by(RunStep.workflow_id, RunStep.created_at)
        )
        for row in wf_user_result.all():
            wf_user_map[row.workflow_id] = row.user_id
            if row.user_id:
                workflow_user_ids.add(row.user_id)

    # Resolve user emails for attribution
    user_email_map: dict[str, str] = {}
    if workflow_user_ids:
        ue_result = await db.execute(
            select(User.id, User.email, User.name)
            .where(User.id.in_(list(workflow_user_ids)))
        )
        for row in ue_result.all():
            user_email_map[row.id] = row.email

    # Attach user info to workflows
    for wf in workflows:
        uid = wf_user_map.get(wf["workflow_id"])
        wf["user_email"] = user_email_map.get(uid, "") if uid else ""

    # Usage tab — add user attribution to ledger groups
    ledger_user_ids = set()
    for entry in raw_ledger:
        uid = getattr(entry, "user_id", None)
        if uid:
            ledger_user_ids.add(uid)
    if ledger_user_ids - workflow_user_ids:
        lu_result = await db.execute(
            select(User.id, User.email)
            .where(User.id.in_(list(ledger_user_ids - workflow_user_ids)))
        )
        for row in lu_result.all():
            user_email_map[row.id] = row.email

    # Rebuild ledger_groups with user attribution
    # The workflow groups need user info from the first hold entry
    wf_group_users: dict[str, str] = {}
    for entry in raw_ledger:
        wf_id = getattr(entry, "workflow_id", None)
        uid = getattr(entry, "user_id", None)
        if wf_id and uid and wf_id not in wf_group_users:
            wf_group_users[wf_id] = user_email_map.get(uid, "")
    for lg in ledger_groups:
        wf_id = lg.get("workflow_id")
        if wf_id:
            lg["user_email"] = wf_group_users.get(wf_id, "")
        else:
            lg["user_email"] = ""

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------
    return templates.TemplateResponse(
        "tenant_dashboard.html",
        {
            "request": request,
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
            "status": "active",
            "plan": "platform",
            "active_tab": tab,
            "providers": providers,
            "vendor_categories": vendor_categories,
            "summary": key_summary,
            "connections": connections,
            "conn_summary": conn_summary,
            "conn_usage": conn_usage,
            "usage": usage,
            "vault_usage": vault_usage,
            "ledger_entries": ledger_entries,
            "ledger_groups": ledger_groups,
            "workflows": workflows,
            "runs_summary": runs_summary,
            "datasets": datasets_list,
            "datasets_summary": datasets_summary,
            "scheduled_workflows": scheduled_workflows_list,
            "dashboards": dashboards_list,
            "dashboards_summary": dashboards_summary,
            "team_members": team_members,
            "current_user": {
                "id": current_user.id,
                "email": current_user.email,
                "name": current_user.name,
                "avatar_url": current_user.avatar_url,
                "role": current_user.role,
            } if current_user else None,
        },
    )



@router.get("/console/{tenant_id}/dashboards/{dashboard_id}", response_class=HTMLResponse)
async def view_dashboard(
    request: Request,
    tenant_id: str,
    dashboard_id: str,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Render a dashboard view (authenticated)."""
    try:
        tenant, current_user, db = await _authenticate_console(request, token=token, db=db, allow_redirect=True)
    except _AuthFailRedirect:
        return RedirectResponse(url=_LOGIN_URL, status_code=302)

    if tenant.id != tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")

    result = await db.execute(
        select(DashboardModel).where(
            DashboardModel.id == dashboard_id,
            DashboardModel.tenant_id == tenant.id,
        )
    )
    dashboard = result.scalar_one_or_none()
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    ds_name = "Unknown"
    rows = []
    if dashboard.dataset_id:
        ds_result = await db.execute(select(Dataset).where(Dataset.id == dashboard.dataset_id))
        dataset = ds_result.scalar_one_or_none()
        if dataset:
            ds_name = dataset.name
            rows_result = await db.execute(
                select(DatasetRow).where(DatasetRow.dataset_id == dataset.id).order_by(DatasetRow.created_at.desc()).limit(500)
            )
            rows = [r.data for r in rows_result.scalars().all()]

    html = render_dashboard_html(
        dashboard_name=dashboard.name,
        dataset_name=ds_name,
        config=dashboard.config or {},
        rows=rows,
        back_url=f"/console/{tenant_id}?tab=dashboards",
    )
    return HTMLResponse(content=html)


@router.api_route("/d/{read_token}", methods=["GET", "POST"], response_class=HTMLResponse)
async def public_dashboard(
    request: Request,
    read_token: str,
    password: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Render a public/shareable dashboard (no auth required)."""
    import hashlib
    token_hash = hashlib.sha256(read_token.encode()).hexdigest()

    result = await db.execute(
        select(DashboardModel).where(DashboardModel.read_token_hash == token_hash)
    )
    dashboard = result.scalar_one_or_none()
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    # Accept password from query param (GET) or form body (POST)
    pwd = password
    if not pwd and request.method == "POST":
        form = await request.form()
        pwd = form.get("password")

    if dashboard.password_hash:
        if not pwd:
            return HTMLResponse(content=render_password_page(dashboard.name, read_token))
        if not verify_password(dashboard.password_hash, pwd):
            return HTMLResponse(content=render_password_page(dashboard.name, read_token))

    await set_tenant_context(db, dashboard.tenant_id)

    ds_name = "Unknown"
    rows = []
    if dashboard.dataset_id:
        ds_result = await db.execute(select(Dataset).where(Dataset.id == dashboard.dataset_id))
        dataset = ds_result.scalar_one_or_none()
        if dataset:
            ds_name = dataset.name
            rows_result = await db.execute(
                select(DatasetRow).where(DatasetRow.dataset_id == dataset.id).order_by(DatasetRow.created_at.desc()).limit(500)
            )
            rows = [r.data for r in rows_result.scalars().all()]

    html = render_dashboard_html(
        dashboard_name=dashboard.name,
        dataset_name=ds_name,
        config=dashboard.config or {},
        rows=rows,
    )
    return HTMLResponse(content=html)


def _has_platform_key(provider: str) -> bool:
    """Check whether a platform-managed key exists for a provider."""
    mapping = {
        "apollo": settings.APOLLO_API_KEY,
        "rocketreach": settings.ROCKETREACH_API_KEY or settings.ROCKETREACH_API,
        "predictleads": settings.PREDICTLEADS_API_KEY,
        "parallel": settings.PARALLEL_KEY,
        "rapidapi": settings.X_RAPIDAPI_KEY or settings.RAPIDAPI_KEY,
    }
    return bool(mapping.get(provider))


# ---------------------------------------------------------------------------
# Connections API — Composio OAuth integration
# ---------------------------------------------------------------------------

COMPOSIO_V3 = "https://backend.composio.dev/api/v3"

# HTML shown in the OAuth popup after Composio redirects back
_OAUTH_CALLBACK_HTML = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connection $status_title — nrev-lite</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
     background:#0a0a0a;color:#e0e0e0;display:flex;align-items:center;
     justify-content:center;min-height:100vh}
.card{background:#141414;border:1px solid #222;border-radius:16px;padding:48px;
      text-align:center;max-width:420px;width:90%}
.icon{font-size:48px;margin-bottom:16px}
h1{font-size:22px;margin-bottom:8px;color:#fff}
.msg{color:#888;margin-bottom:24px;font-size:14px;line-height:1.5}
.btn{display:inline-block;padding:10px 24px;background:#fff;color:#000;
     border:none;border-radius:8px;font-size:14px;font-weight:500;
     cursor:pointer;text-decoration:none;transition:opacity 0.2s}
.btn:hover{opacity:0.85}
</style>
</head>
<body>
<div class="card">
<div class="icon">$icon</div>
<h1>$heading</h1>
<p class="msg">$message</p>
<button class="btn" onclick="closeWindow()">Close this window</button>
</div>
<script>
// Refresh the parent dashboard so connection status updates
if (window.opener && !window.opener.closed) {
    try { window.opener.location.reload(); } catch(e) {}
}
// Auto-close after 2 seconds
setTimeout(function() { window.close(); }, 2000);
function closeWindow() { window.close(); }
</script>
</body>
</html>
""")


class ConnectRequest(BaseModel):
    app_id: str


class ActionExecuteRequest(BaseModel):
    """Execute a Composio action on a connected account."""
    app_id: str  # catalog key, e.g. "gmail", "google_sheets"
    action: str  # Composio action name, e.g. "GMAIL_SEND_EMAIL"
    params: dict  # action input parameters


async def _get_auth_config_id(
    client: httpx.AsyncClient, composio_key: str, app_name: str,
) -> str | None:
    """Look up the Composio-managed auth_config ID for an app.

    Composio v3 /auth_configs doesn't reliably filter by appName, so we
    fetch all configs and match by toolkit.slug (lowercase app identifier).
    """
    resp = await client.get(
        f"{COMPOSIO_V3}/auth_configs",
        headers={"x-api-key": composio_key},
    )
    if resp.status_code != 200:
        return None
    items = resp.json().get("items", [])
    # Match by toolkit.slug (e.g., "hubspot", "slack", "googledocs")
    slug = app_name.lower()
    matched = [
        item for item in items
        if (item.get("toolkit") or {}).get("slug", "").lower() == slug
    ]
    # Prefer Composio-managed configs among matches
    for item in matched:
        if item.get("is_composio_managed"):
            return item["id"]
    return matched[0]["id"] if matched else None


@router.post("/api/v1/connections/initiate")
async def initiate_connection(
    request: Request,
    body: ConnectRequest,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Initiate an OAuth connection via Composio v3 for a given app.

    Steps:
    1. Look up the auth_config ID for the app
    2. Create a connected_account with the auth_config + entity
    3. Return the redirect URL for OAuth authorization
    """
    tenant, current_user, db = await _authenticate_console(request, token=token, db=db, allow_redirect=False)

    composio_key = settings.COMPOSIO_API_KEY
    if not composio_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Composio integration not configured. Set COMPOSIO_API_KEY.",
        )

    # Per-user entity_id for multi-account support; fall back to tenant-level
    if current_user:
        entity_id = f"nrev-u-{current_user.id}"
    else:
        entity_id = f"nrev-{tenant.id}"

    # Resolve the Composio app name (uppercase) from catalog
    catalog_entry = INTEGRATION_CATALOG.get(body.app_id)
    composio_app = catalog_entry.get("composio_app") if catalog_entry else None
    if not composio_app:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown app: '{body.app_id}'",
        )

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Step 1: Get auth_config ID for this app
            auth_config_id = await _get_auth_config_id(
                client, composio_key, composio_app,
            )
            if not auth_config_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No Composio auth config found for '{composio_app}'. "
                           "This app may not be available for OAuth connection yet.",
                )

            # Build the callback URL Composio should redirect to after OAuth
            server_base = settings.GOOGLE_REDIRECT_URI.rsplit(
                "/api/v1/auth/callback", 1,
            )[0]
            oauth_callback = f"{server_base}/api/v1/connections/callback"

            # Step 2: Create connected account via v3
            resp = await client.post(
                f"{COMPOSIO_V3}/connected_accounts",
                headers={
                    "x-api-key": composio_key,
                    "Content-Type": "application/json",
                },
                json={
                    "connection": {
                        "appName": composio_app,
                        "entityId": entity_id,
                    },
                    "auth_config": {"id": auth_config_id},
                    "redirectUrl": oauth_callback,
                },
            )

            if resp.status_code in (200, 201):
                data = resp.json()
                redirect_url = (
                    data.get("redirect_url")
                    or data.get("redirect_uri")
                    or (data.get("connectionData", {})
                        .get("val", {}).get("redirectUrl"))
                )
                conn_id = data.get("id", "")

                # Record user-connection mapping (best-effort; table may not exist yet)
                if current_user:
                    try:
                        from server.connections.models import UserConnection
                        uc = UserConnection(
                            tenant_id=tenant.id,
                            user_id=current_user.id,
                            user_email=current_user.email,
                            app_id=body.app_id,
                            composio_entity_id=entity_id,
                            composio_account_id=conn_id,
                            status="active",
                        )
                        try:
                            db.add(uc)
                            await db.commit()
                        except Exception:
                            await db.rollback()
                            # May already exist — update instead
                            from sqlalchemy import update as sa_update
                            await db.execute(
                                sa_update(UserConnection)
                                .where(
                                    UserConnection.tenant_id == tenant.id,
                                    UserConnection.user_id == current_user.id,
                                    UserConnection.app_id == body.app_id,
                                )
                                .values(
                                    composio_entity_id=entity_id,
                                    composio_account_id=conn_id,
                                    status="active",
                                )
                            )
                            await db.commit()
                    except Exception:
                        # user_connections table may not exist (migration 013)
                        logger.warning("Could not record user-connection mapping: %s", body.app_id)
                        try:
                            await db.rollback()
                        except Exception:
                            pass

                if redirect_url:
                    return JSONResponse({
                        "status": "redirect",
                        "redirect_url": redirect_url,
                        "connection_id": conn_id,
                    })
                return JSONResponse({
                    "status": "connected",
                    "connection_id": conn_id,
                    "message": f"{body.app_id} connected successfully",
                })
            else:
                logger.warning(
                    "Composio v3 connection failed for %s/%s: %s %s",
                    entity_id, body.app_id, resp.status_code, resp.text,
                )
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Composio error: {resp.text[:200]}",
                )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to reach Composio: {exc}",
        ) from exc


@router.get("/api/v1/connections/callback", response_class=HTMLResponse)
async def oauth_connection_callback(
    status_param: str = Query("success", alias="status"),
):
    """Landing page after Composio OAuth completes.

    Composio redirects the popup here. We show a success/error message,
    refresh the parent dashboard window, and auto-close the popup.
    """
    if status_param == "success" or status_param not in ("error", "failed"):
        return HTMLResponse(_OAUTH_CALLBACK_HTML.safe_substitute(
            status_title="Complete",
            icon="\u2705",
            heading="Connected!",
            message="Your account has been connected successfully. This window will close automatically.",
        ))
    else:
        return HTMLResponse(_OAUTH_CALLBACK_HTML.safe_substitute(
            status_title="Failed",
            icon="\u274c",
            heading="Connection Failed",
            message="Something went wrong during authorization. Please close this window and try again.",
        ))


@router.get("/api/v1/connections")
async def list_connections(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List all active Composio connections for this tenant."""
    tenant, current_user, db = await _authenticate_console(request, token=token, db=db, allow_redirect=False)

    composio_key = settings.COMPOSIO_API_KEY
    if not composio_key:
        return JSONResponse({"connections": [], "error": "Composio not configured"})

    # Collect all entity IDs to query (per-user + tenant + legacy)
    entity_to_email: dict[str, str] = {}
    entity_ids_to_check: set[str] = set()
    try:
        from server.connections.models import UserConnection
        uc_result = await db.execute(
            select(UserConnection).where(
                UserConnection.tenant_id == tenant.id,
                UserConnection.status == "active",
            )
        )
        user_connections = uc_result.scalars().all()
        for uc in user_connections:
            entity_ids_to_check.add(uc.composio_entity_id)
            entity_to_email[uc.composio_entity_id] = uc.user_email
    except Exception:
        # user_connections table may not exist yet (migration 013)
        await db.rollback()

    # Always check tenant-level and legacy entity IDs
    entity_id = f"nrev-{tenant.id}"
    legacy_entity_id = f"nrv-{tenant.id}"
    entity_ids_to_check.add(entity_id)
    entity_ids_to_check.add(legacy_entity_id)
    valid_entity_ids = entity_ids_to_check

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            all_items: list[dict] = []
            for eid in entity_ids_to_check:
                try:
                    resp = await client.get(
                        f"{COMPOSIO_V3}/connected_accounts",
                        headers={"x-api-key": composio_key},
                        params={"entityId": eid},
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    items = data.get("items", []) if isinstance(data, dict) else data
                    for item in items:
                        item_entity = item.get("entityId") or (item.get("entity") or {}).get("id", "")
                        if item_entity and item_entity not in valid_entity_ids:
                            continue
                        item["_entity_id"] = item_entity or eid
                        all_items.append(item)
                except Exception:
                    continue

            connections = []
            seen: set[str] = set()  # dedup by composio account id
            for item in all_items:
                acct_id = item.get("id", "")
                if acct_id in seen:
                    continue
                seen.add(acct_id)

                s = (item.get("status") or "").upper()
                if s in ("EXPIRED", "FAILED", "REVOKED"):
                    continue
                toolkit_slug = (item.get("toolkit") or {}).get("slug", "")
                catalog_key = _composio_to_catalog.get(
                    toolkit_slug.upper(), toolkit_slug,
                )
                item_eid = item.get("_entity_id", "")
                connected_by = entity_to_email.get(item_eid)

                connections.append({
                    "id": acct_id,
                    "app_id": catalog_key,
                    "toolkit_slug": toolkit_slug,
                    "status": s,
                    "created_at": item.get("created_at", ""),
                    "connected_by": connected_by,
                    "entity_id": item_eid,
                })
            return JSONResponse({"connections": connections})
    except httpx.HTTPError as exc:
        return JSONResponse({"connections": [], "error": str(exc)})


@router.delete("/api/v1/connections/{connection_id}")
async def delete_connection(
    request: Request,
    connection_id: str,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect a Composio connection."""
    tenant, current_user, db = await _authenticate_console(request, token=token, db=db, allow_redirect=False)

    composio_key = settings.COMPOSIO_API_KEY
    if not composio_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Composio not configured",
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"{COMPOSIO_V3}/connected_accounts/{connection_id}",
                headers={"x-api-key": composio_key},
            )
            if resp.status_code in (200, 204):
                return JSONResponse({"status": "disconnected"})
            else:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Composio error: {resp.text[:200]}",
                )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to reach Composio: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Action discovery — list available actions & schemas from Composio
# ---------------------------------------------------------------------------

COMPOSIO_V2 = "https://backend.composio.dev/api/v2"

# Reverse map: catalog key → COMPOSIO slug (e.g. "gmail" → "GMAIL")
_catalog_to_composio = {
    key: info["composio_app"]
    for key, info in INTEGRATION_CATALOG.items()
    if "composio_app" in info
}


@router.get("/api/v1/connections/actions")
async def list_actions(
    request: Request,
    app_id: str = Query(..., description="Catalog app key, e.g. 'gmail'"),
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List all available actions for a connected app.

    Returns action names, display names, and descriptions from Composio.
    """
    tenant, current_user, db = await _authenticate_console(
        request, token=token, db=db, allow_redirect=False,
    )

    composio_key = settings.COMPOSIO_API_KEY
    if not composio_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Composio integration not configured.",
        )

    composio_slug = _catalog_to_composio.get(app_id)
    if not composio_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown app: '{app_id}'. Valid apps: {', '.join(sorted(_catalog_to_composio))}",
        )

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{COMPOSIO_V2}/actions",
                headers={"x-api-key": composio_key},
                params={"apps": composio_slug, "limit": 50},
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Composio error: {resp.text[:200]}",
                )
            items = resp.json().get("items", [])
            actions = [
                {
                    "name": item["name"],
                    "display_name": item.get("displayName") or item.get("display_name", ""),
                    "description": (item.get("description") or "")[:200],
                }
                for item in items
            ]
            return JSONResponse({"app_id": app_id, "actions": actions})
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to reach Composio: {exc}",
        ) from exc


@router.get("/api/v1/connections/actions/{action_name}/schema")
async def get_action_schema(
    request: Request,
    action_name: str,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get the parameter schema for a specific Composio action.

    Returns parameter names, types, descriptions, and required flags.
    """
    tenant, current_user, db = await _authenticate_console(
        request, token=token, db=db, allow_redirect=False,
    )

    composio_key = settings.COMPOSIO_API_KEY
    if not composio_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Composio integration not configured.",
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{COMPOSIO_V2}/actions/{action_name}",
                headers={"x-api-key": composio_key},
            )
            if resp.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Action '{action_name}' not found.",
                )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Composio error: {resp.text[:200]}",
                )
            data = resp.json()
            params = data.get("parameters", {})
            properties = params.get("properties", {})
            required = set(params.get("required", []))

            schema = {
                "action": data["name"],
                "display_name": data.get("displayName") or data.get("display_name", ""),
                "description": (data.get("description") or "")[:300],
                "parameters": {
                    k: {
                        "type": v.get("type", "string"),
                        "description": (v.get("description") or "")[:150],
                        "required": k in required,
                        **({"default": v["default"]} if "default" in v else {}),
                        **({"enum": v["enum"]} if "enum" in v else {}),
                    }
                    for k, v in properties.items()
                },
            }
            return JSONResponse(schema)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to reach Composio: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Action execution — run Composio tool actions via connected accounts
# ---------------------------------------------------------------------------


async def _find_connected_account(
    client: httpx.AsyncClient,
    composio_key: str,
    entity_id: str,
    app_slug: str,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> dict | None:
    """Find the ACTIVE connected account for an app and return id + v2 UUID.

    Priority order:
    1. Current user's per-user entity (nrev-u-{user_id})
    2. Tenant entity (nrev-{tenant_id})
    3. Legacy tenant entity (nrv-{tenant_id})
    """
    # Build ordered list of entity IDs to check
    entity_ids: list[str] = []

    # Prefer the current user's per-user entity
    if user_id:
        entity_ids.append(f"nrev-u-{user_id}")

    # Then the provided entity_id (may be tenant-level or per-user)
    if entity_id not in entity_ids:
        entity_ids.append(entity_id)

    # Legacy tenant entity
    if tenant_id:
        tenant_eid = f"nrev-{tenant_id}"
        if tenant_eid not in entity_ids:
            entity_ids.append(tenant_eid)
        legacy_eid = f"nrv-{tenant_id}"
        if legacy_eid not in entity_ids:
            entity_ids.append(legacy_eid)
    elif entity_id.startswith("nrev-"):
        legacy = "nrv-" + entity_id[5:]
        if legacy not in entity_ids:
            entity_ids.append(legacy)

    for eid in entity_ids:
        resp = await client.get(
            f"{COMPOSIO_V3}/connected_accounts",
            headers={"x-api-key": composio_key},
            params={"entityId": eid},
        )
        if resp.status_code != 200:
            continue
        items = resp.json().get("items", [])
        for item in items:
            toolkit_slug = (item.get("toolkit") or {}).get("slug", "")
            if toolkit_slug.lower() == app_slug.lower() and item.get("status") == "ACTIVE":
                return {
                    "id": item["id"],
                    "v2_uuid": (item.get("deprecated") or {}).get("uuid"),
                    "entity_id": eid,
                }
    return None


@router.post("/api/v1/connections/execute")
async def execute_action(
    request: Request,
    body: ActionExecuteRequest,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Execute a Composio tool action on a tenant's connected account.

    Resolves the connected account for the given app, bridges v3->v2 UUID,
    and executes the action via Composio v2 API.
    """
    tenant, current_user, db = await _authenticate_console(request, token=token, db=db, allow_redirect=False)

    composio_key = settings.COMPOSIO_API_KEY
    if not composio_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Composio integration not configured.",
        )

    entity_id = f"nrev-{tenant.id}"

    # Resolve the Composio toolkit slug from catalog
    catalog_entry = INTEGRATION_CATALOG.get(body.app_id)
    if not catalog_entry or "composio_app" not in catalog_entry:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown app: '{body.app_id}'",
        )
    composio_app = catalog_entry["composio_app"].lower()

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # Find the active connected account (prefer current user's)
            account = await _find_connected_account(
                client, composio_key, entity_id, composio_app,
                user_id=current_user.id if current_user else None,
                tenant_id=tenant.id,
            )
            if not account:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No active connection for '{body.app_id}'. "
                           "Connect it first from the dashboard.",
                )
            v2_uuid = account.get("v2_uuid")
            if not v2_uuid:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Connected account missing v2 identifier.",
                )

            # Execute the action via Composio v2 API
            resp = await client.post(
                f"{COMPOSIO_V2}/actions/{body.action}/execute",
                headers={"x-api-key": composio_key, "Content-Type": "application/json"},
                json={
                    "entityId": entity_id,
                    "connectedAccountId": v2_uuid,
                    "input": body.params,
                },
            )

            if resp.status_code == 200:
                data = resp.json()
                if data.get("successful") or data.get("successfull"):
                    logger.info(
                        "Action %s executed for %s/%s",
                        body.action, entity_id, body.app_id,
                    )
                    return JSONResponse({
                        "status": "success",
                        "data": data.get("data", {}),
                    })
                else:
                    return JSONResponse({
                        "status": "error",
                        "error": data.get("error") or data.get("message", "Action failed"),
                        "data": data.get("data", {}),
                    }, status_code=422)
            else:
                error_text = resp.text[:300]
                logger.warning(
                    "Composio action %s failed: %s %s",
                    body.action, resp.status_code, error_text,
                )
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Composio error: {error_text}",
                )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to reach Composio: {exc}",
        ) from exc
