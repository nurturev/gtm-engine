"""Run step logger — middleware that records every API call to run_steps.

The MCP client sends X-Workflow-Id and X-Tool-Name headers with every request.
This middleware intercepts API calls and logs them as run steps, capturing:
- Tool name, operation, provider
- Sanitized params (no secrets)
- Result summary (abbreviated)
- Duration, status, credits charged
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Request, Response
from jose import jwt as jose_jwt
from starlette.middleware.base import BaseHTTPMiddleware

from server.core.config import settings
from server.core.database import async_session_factory, set_tenant_context
from server.execution.run_models import RunStep

logger = logging.getLogger(__name__)

# Paths that should be logged as run steps
_LOGGED_PREFIXES = (
    "/api/v1/execute",
    "/api/v1/connections",
    "/api/v1/search/patterns",
    "/api/v1/keys",
    "/api/v1/credits",
    "/api/v1/tables",
)

# Paths to skip (health checks, auth, static, console pages)
_SKIP_PREFIXES = (
    "/health",
    "/api/v1/auth",
    "/api/v1/runs",
    "/console",
    "/api/v1/connections/initiate",
    "/api/v1/connections/callback",
)

# Map API paths to tool names when X-Tool-Name header is absent
_PATH_TO_TOOL: dict[str, str] = {
    "/api/v1/execute": "nrv_execute",
    "/api/v1/connections/execute": "nrv_execute_action",
    "/api/v1/connections/actions": "nrv_list_actions",
    "/api/v1/connections": "nrv_list_connections",
    "/api/v1/search/patterns": "nrv_search_patterns",
    "/api/v1/credits": "nrv_credit_balance",
    "/api/v1/keys": "nrv_provider_status",
    "/api/v1/tables": "nrv_query_table",
}


def _should_log(path: str, method: str) -> bool:
    """Check if this request should be logged as a run step."""
    # Skip non-API paths
    if any(path.startswith(p) for p in _SKIP_PREFIXES):
        return False
    # Only log specific API paths
    if any(path.startswith(p) for p in _LOGGED_PREFIXES):
        return True
    return False


def _infer_tool_name(path: str) -> str:
    """Infer the MCP tool name from the API path.

    Match longest prefix first so /connections/execute matches before /connections.
    """
    best_match = ""
    best_tool = "unknown"
    for prefix, tool in _PATH_TO_TOOL.items():
        if path.startswith(prefix) and len(prefix) > len(best_match):
            best_match = prefix
            best_tool = tool
    return best_tool


def _sanitize_params(body: dict[str, Any] | None) -> dict[str, Any]:
    """Extract a summary of params, never including secrets."""
    if not body:
        return {}
    summary: dict[str, Any] = {}
    # For execute calls
    if "operation" in body:
        summary["operation"] = body["operation"]
    if "provider" in body:
        summary["provider"] = body["provider"]
    if "params" in body and isinstance(body["params"], dict):
        params = body["params"]
        # Include query-like params, skip large data
        for key in ("q", "query", "email", "domain", "name", "linkedin_url",
                     "num", "site", "tbs", "gl", "url"):
            if key in params:
                val = params[key]
                if isinstance(val, str) and len(val) > 200:
                    val = val[:200] + "..."
                summary[key] = val
        # Bulk queries count
        if "queries" in params and isinstance(params["queries"], list):
            summary["queries_count"] = len(params["queries"])
    # For connection execute calls
    if "app_id" in body:
        summary["app_id"] = body["app_id"]
    if "action" in body:
        summary["action"] = body["action"]
    return summary


def _summarize_result(status_code: int, body: dict[str, Any] | None) -> dict[str, Any]:
    """Create a brief summary of the result."""
    summary: dict[str, Any] = {"http_status": status_code}
    if not body:
        return summary
    # For execute responses
    if "execution_id" in body:
        summary["execution_id"] = body["execution_id"]
    if "credits_charged" in body:
        summary["credits_charged"] = body["credits_charged"]
    # Result count
    result = body.get("result", {})
    if isinstance(result, dict):
        if "total" in result:
            summary["total_results"] = result["total"]
        if "results" in result and isinstance(result["results"], list):
            summary["result_count"] = len(result["results"])
    # For connection actions
    if "status" in body:
        summary["action_status"] = body["status"]
    # Error
    if "error" in body:
        err = body["error"]
        if isinstance(err, str) and len(err) > 200:
            err = err[:200] + "..."
        summary["error"] = err
    if "detail" in body:
        detail = body["detail"]
        if isinstance(detail, str) and len(detail) > 200:
            detail = detail[:200] + "..."
        summary["error"] = detail
    return summary


class RunStepMiddleware(BaseHTTPMiddleware):
    """Middleware that logs API calls as run steps when X-Workflow-Id is present."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only log when workflow_id is present (MCP client sends this)
        workflow_id = request.headers.get("X-Workflow-Id")
        if not workflow_id:
            return await call_next(request)

        workflow_label = request.headers.get("X-Workflow-Label", "")

        path = request.url.path
        method = request.method

        if not _should_log(path, method):
            return await call_next(request)

        # Get tool name from header or infer from path
        tool_name = request.headers.get("X-Tool-Name") or _infer_tool_name(path)

        # Extract tenant_id from JWT directly (can't rely on request.state
        # because our middleware may run before tenant_context_middleware)
        tenant_id: str | None = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                payload = jose_jwt.decode(
                    auth_header.removeprefix("Bearer "),
                    settings.JWT_SECRET_KEY,
                    algorithms=[settings.JWT_ALGORITHM],
                    options={"verify_exp": False},
                )
                tenant_id = payload.get("tenant_id")
            except Exception:
                pass

        # Try to read request body for param summary
        params_summary: dict[str, Any] = {}
        if method in ("POST", "PUT", "PATCH"):
            try:
                body_bytes = await request.body()
                import json
                req_body = json.loads(body_bytes)
                params_summary = _sanitize_params(req_body)
            except Exception:
                pass

        # Add query params for GET requests
        if method == "GET" and request.query_params:
            for key in ("platform", "use_case", "app_id", "table_name", "limit"):
                val = request.query_params.get(key)
                if val:
                    params_summary[key] = val

        start_time = time.monotonic()

        # Execute the request
        response: Response = await call_next(request)

        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Determine status
        if response.status_code < 400:
            step_status = "success"
            error_msg = None
        else:
            step_status = "failed"
            error_msg = f"HTTP {response.status_code}"

        # Extract result summary from response body
        result_summary: dict[str, Any] = {"http_status": response.status_code}
        # Note: We can't easily read streaming response body in middleware,
        # so we rely on the status code and headers for summary.
        # The detailed result is available in enrichment_log for execute calls.

        # Extract operation and provider from params_summary
        operation = params_summary.pop("operation", None)
        provider = params_summary.pop("provider", None)

        # Credits from response headers (if we add them)
        credits_str = response.headers.get("X-Credits-Charged", "0")
        try:
            credits_charged = float(credits_str)
        except (ValueError, TypeError):
            credits_charged = 0.0

        # Only log if we have a tenant_id
        if not tenant_id:
            return response

        # Log the run step asynchronously (don't block the response)
        try:
            async with async_session_factory() as session:
                await set_tenant_context(session, tenant_id)
                step = RunStep(
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    workflow_label=workflow_label or None,
                    tool_name=tool_name,
                    operation=operation,
                    provider=provider,
                    params_summary=params_summary,
                    result_summary=result_summary,
                    status=step_status,
                    error_message=error_msg,
                    credits_charged=credits_charged,
                    duration_ms=duration_ms,
                )
                session.add(step)
                await session.commit()
        except Exception:
            logger.warning(
                "Failed to log run step for workflow %s",
                workflow_id,
                exc_info=True,
            )

        return response
