"""Run step logger — middleware that records every API call to run_steps.

The MCP client sends X-Workflow-Id and X-Tool-Name headers with every request.
For non-MCP callers (CLI, direct API), the middleware auto-generates a
workflow_id per tenant session so ALL API calls get logged.

Captures: tool name, operation, provider, sanitized params, result summary,
duration, status, credits charged.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import Request, Response
from jose import jwt as jose_jwt
from starlette.middleware.base import BaseHTTPMiddleware

from server.core.config import settings
from server.core.database import async_session_factory, set_tenant_context
from server.execution.run_models import RunStep

logger = logging.getLogger(__name__)

# Auto-generated workflow IDs for non-MCP callers, keyed by tenant_id
_auto_workflow_ids: dict[str, str] = {}

# Paths that should be logged as run steps
_LOGGED_PREFIXES = (
    "/api/v1/execute",
    "/api/v1/connections",
    "/api/v1/search/patterns",
    "/api/v1/keys",
    "/api/v1/credits",
    "/api/v1/tables",
    "/api/v1/datasets",
    "/api/v1/scripts",
    "/api/v1/learning-logs",
    "/api/v1/apps",
    "/api/v1/dashboards",
    "/api/v1/schedules",
)

# Paths to skip (health checks, auth, static, console pages)
_SKIP_PREFIXES = (
    "/health",
    "/api/v1/auth",
    "/api/v1/runs",
    "/console",
    "/admin",
    "/api/v1/connections/initiate",
    "/api/v1/connections/callback",
)

# Map API paths to tool names when X-Tool-Name header is absent
_PATH_TO_TOOL: dict[str, str] = {
    "/api/v1/execute": "nrev_execute",
    "/api/v1/connections/execute": "nrev_execute_action",
    "/api/v1/connections/actions": "nrev_list_actions",
    "/api/v1/connections": "nrev_list_connections",
    "/api/v1/search/patterns": "nrev_search_patterns",
    "/api/v1/credits": "nrev_credit_balance",
    "/api/v1/keys": "nrev_provider_status",
    "/api/v1/tables": "nrev_query_table",
    "/api/v1/datasets": "nrev_list_datasets",
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
    """Middleware that logs API calls as run steps.

    MCP clients send X-Workflow-Id; for direct API callers, a per-tenant
    auto-generated workflow_id ensures all calls are still tracked.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        workflow_id = request.headers.get("X-Workflow-Id")
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
        user_id: str | None = None
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
                user_id = payload.get("sub")
            except Exception:
                pass

        # Auto-generate workflow_id for non-MCP callers (CLI, direct API)
        if not workflow_id and tenant_id:
            workflow_id = _auto_workflow_ids.get(tenant_id)
            if not workflow_id:
                workflow_id = str(uuid.uuid4())
                _auto_workflow_ids[tenant_id] = workflow_id
            if not workflow_label:
                workflow_label = "Direct API"

        # If still no workflow_id (no auth), skip logging
        if not workflow_id:
            return await call_next(request)

        # Store workflow_id and user_id on request.state so downstream
        # handlers (e.g. execution router) can propagate to credit_ledger
        request.state.workflow_id = workflow_id
        request.state.user_id = user_id

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

        # Read response body so we can capture result data for run logs.
        # BaseHTTPMiddleware wraps the response as a StreamingResponse,
        # so we need to consume and re-wrap it.
        resp_body_bytes = b""
        async for chunk in response.body_iterator:  # type: ignore[union-attr]
            if isinstance(chunk, str):
                resp_body_bytes += chunk.encode("utf-8")
            else:
                resp_body_bytes += chunk

        # Determine status
        if response.status_code < 400:
            step_status = "success"
            error_msg = None
        else:
            step_status = "failed"
            error_msg = f"HTTP {response.status_code}"

        # Parse response body to extract result summary
        import json as _json
        result_summary: dict[str, Any] = {"http_status": response.status_code}
        resp_json: dict[str, Any] | None = None
        try:
            resp_json = _json.loads(resp_body_bytes)
        except Exception:
            pass

        if resp_json:
            result_summary = _summarize_result(response.status_code, resp_json)
            # Store the full result data (truncated for large responses)
            result_data = resp_json.get("result")
            if result_data and isinstance(result_data, dict):
                # For search results: store the results array
                results_list = result_data.get("results")
                if isinstance(results_list, list):
                    result_summary["result_count"] = len(results_list)
                    # Store up to 50 results (truncate for very large responses)
                    result_summary["results"] = results_list[:50]
                    result_summary["query"] = result_data.get("query", "")
                # For enrichment: store the enriched profile
                for key in ("person", "company", "profile", "data"):
                    if key in result_data and isinstance(result_data[key], dict):
                        result_summary[key] = result_data[key]
                        break
            # Capture error detail on failure
            if resp_json.get("detail"):
                result_summary["error"] = str(resp_json["detail"])[:500]

        # Extract operation and provider from params_summary
        operation = params_summary.pop("operation", None)
        provider = params_summary.pop("provider", None)

        # Credits from response body or headers
        credits_charged = 0.0
        if resp_json and "credits_charged" in resp_json:
            try:
                credits_charged = float(resp_json["credits_charged"])
            except (ValueError, TypeError):
                pass
        if credits_charged == 0.0:
            credits_str = response.headers.get("X-Credits-Charged", "0")
            try:
                credits_charged = float(credits_str)
            except (ValueError, TypeError):
                pass

        # Only log if we have a tenant_id
        if not tenant_id:
            # Re-wrap response body before returning
            from starlette.responses import Response as StarletteResponse
            return StarletteResponse(
                content=resp_body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        # Log the run step asynchronously (don't block the response)
        try:
            async with async_session_factory() as session:
                await set_tenant_context(session, tenant_id)
                step = RunStep(
                    tenant_id=tenant_id,
                    user_id=user_id,
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

        # Re-wrap response body since we consumed the iterator
        from starlette.responses import Response as StarletteResponse
        return StarletteResponse(
            content=resp_body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
