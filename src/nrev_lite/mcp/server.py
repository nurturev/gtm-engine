"""
nrev-lite MCP Server — expose nrev-lite capabilities as tools for Claude.

Implements the Model Context Protocol (MCP) over stdin/stdout using JSON-RPC 2.0.
Tries to use the official `mcp` SDK if available; otherwise falls back to a
lightweight built-in implementation that speaks the same wire protocol.

Usage:
    python -m nrev_lite.mcp.server

Or register in .mcp.json:
    {
      "mcpServers": {
        "nrev-lite": {
          "command": "python3",
          "args": ["-m", "nrev_lite.mcp.server"]
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

from nrev_lite.client.auth import load_credentials, refresh_token_if_needed, force_refresh, get_token
from nrev_lite.utils.config import get_api_base_url

# ---------------------------------------------------------------------------
# Logging — write to ~/.nrev-lite/mcp_server.log so stdout stays clean for JSON-RPC
# ---------------------------------------------------------------------------

_LOG_DIR = Path.home() / ".nrev-lite"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("nrev_lite.mcp")
_handler = logging.FileHandler(_LOG_DIR / "mcp_server.log")
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERVER_NAME = "nrev-lite"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

# Unique ID for this MCP server session — groups all tool calls into one workflow.
# Can be reset mid-session via nrev_new_workflow to start a separate workflow.
WORKFLOW_ID = str(uuid.uuid4())
WORKFLOW_LABEL: str = ""  # Optional human-readable label for the current workflow

# ---------------------------------------------------------------------------
# HTTP helpers — thin wrapper around the nrev-lite server API
# ---------------------------------------------------------------------------


def _get_auth_headers() -> dict[str, str]:
    """Return Authorization header, refreshing the token if needed."""
    token = refresh_token_if_needed() or get_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _api_url(path: str) -> str:
    """Build a full API URL."""
    base = get_api_base_url().rstrip("/")
    return f"{base}/api/v1{path}"


_current_tool_name: str = ""  # Set by the dispatcher before each handler call


def _api_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    timeout: float = 60,
) -> dict[str, Any]:
    """Make an authenticated request to the nrev-lite server API.

    Returns the parsed JSON response or an error dict.
    Includes X-Workflow-Id and X-Tool-Name headers so the server can
    log this call as a run step in the workflow.
    """
    headers = _get_auth_headers()
    if not headers:
        return {"error": "Not authenticated. Run `nrev-lite auth login` first."}

    # Add tenant header for console endpoints (e.g. /connections/*) that
    # resolve tenant from X-Tenant-Id rather than JWT claims alone.
    creds = load_credentials()
    tenant_id = (creds or {}).get("user_info", {}).get("tenant", "")
    if tenant_id:
        headers["X-Tenant-Id"] = str(tenant_id)

    # Add workflow tracking headers
    headers["X-Workflow-Id"] = WORKFLOW_ID
    if WORKFLOW_LABEL:
        headers["X-Workflow-Label"] = WORKFLOW_LABEL
    if _current_tool_name:
        headers["X-Tool-Name"] = _current_tool_name

    url = _api_url(path)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
            )

        if resp.status_code == 401:
            # Force a refresh — the server rejected the token even if it
            # hasn't expired locally (e.g. secret rotation, server restart).
            new_token = force_refresh()
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                with httpx.Client(timeout=timeout) as client:
                    resp = client.request(
                        method,
                        url,
                        headers=headers,
                        json=json_body,
                        params=params,
                    )
            if resp.status_code == 401:
                # Force-refresh failed too — re-read credentials from disk
                # in case the user just ran `nrev-lite auth login` in another terminal.
                disk_token = get_token()
                if disk_token and disk_token != headers.get("Authorization", "").removeprefix("Bearer "):
                    headers["Authorization"] = f"Bearer {disk_token}"
                    with httpx.Client(timeout=timeout) as client:
                        resp = client.request(
                            method,
                            url,
                            headers=headers,
                            json=json_body,
                            params=params,
                        )
            if resp.status_code == 401:
                logger.warning("All auth recovery attempts failed (refresh + disk re-read)")
                return {"error": "Session expired. Run `nrev-lite auth login` to re-authenticate."}

        if resp.status_code == 204:
            return {"status": "ok"}

        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}

        if resp.status_code >= 400:
            msg = ""
            if isinstance(data, dict):
                msg = data.get("message") or data.get("detail") or data.get("error", "")
            return {"error": msg or f"HTTP {resp.status_code}", "status_code": resp.status_code}

        return data

    except httpx.ConnectError:
        return {"error": f"Cannot connect to nrev-lite server at {url}. Is the server running?"}
    except httpx.HTTPError as exc:
        return {"error": f"HTTP error: {exc}"}


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    # ---- Web Intelligence ----
    {
        "name": "nrev_search_web",
        "description": (
            "Search the web using nrev-lite's search providers. Returns organic results "
            "with titles, URLs, and snippets. Supports Google search operators."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Supports Google operators (site:, filetype:, etc.).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (1-100). Default: 10.",
                    "default": 10,
                },
                "mode": {
                    "type": "string",
                    "description": "Search mode. 'web' for general web search.",
                    "default": "web",
                    "enum": ["web"],
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "nrev_scrape_page",
        "description": (
            "Extract clean content from one or more web pages. Returns markdown text. "
            "Handles JavaScript-rendered pages and PDFs automatically."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Single URL to scrape.",
                },
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Multiple URLs to scrape in parallel (alternative to 'url').",
                },
                "objective": {
                    "type": "string",
                    "description": "Focus extraction on this intent (e.g. 'pricing information').",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "nrev_google_search",
        "description": (
            "Google search via RapidAPI for GTM intelligence. Supports all Google "
            "operators (site:, inurl:, intitle:, filetype:, -exclude, \"exact phrase\", OR). "
            "IMPORTANT: Before constructing queries for specific platforms (LinkedIn, Twitter, "
            "Reddit, Instagram, etc.), call nrev_search_patterns first to get the correct "
            "query patterns and site: prefixes for that platform. "
            "PREFER bulk queries: pass multiple queries via the 'queries' array to run them "
            "in parallel (much faster than sequential calls). Each query costs 1 credit — "
            "bulk saves time, not credits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Google search query. Supports operators: "
                        "site:domain.com, inurl:keyword, intitle:keyword, "
                        "filetype:pdf, -exclude, \"exact phrase\", term1 OR term2. "
                        "Example: site:linkedin.com/in \"VP Sales\" \"fintech\""
                    ),
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results (1-300). Default: 10.",
                    "default": 10,
                },
                "tbs": {
                    "type": "string",
                    "description": (
                        "Time-based search filter. Friendly names: hour, day, week, month, year. "
                        "Raw Google tbs values for fine control: qdr:h (1 hour), qdr:h2 (2 hours), "
                        "qdr:h6 (6 hours), qdr:d (1 day), qdr:d3 (3 days), qdr:w (1 week), "
                        "qdr:w2 (2 weeks), qdr:m (1 month), qdr:m3 (3 months), qdr:y (1 year). "
                        "Custom date range: cdr:1,cd_min:MM/DD/YYYY,cd_max:MM/DD/YYYY"
                    ),
                },
                "site": {
                    "type": "string",
                    "description": (
                        "Convenience: restrict to domain (auto-adds site: operator). "
                        "Example: 'linkedin.com/in' restricts to LinkedIn profiles."
                    ),
                },
                "country": {
                    "type": "string",
                    "description": "Country code for localized results: us, in, gb, de, ca, au.",
                },
                "language": {
                    "type": "string",
                    "description": "Language code for results: en, hi, fr, de, es, ja.",
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Multiple queries to run in parallel (bulk search). ALWAYS prefer "
                        "this over calling nrev_google_search in a loop — same cost but much "
                        "faster. Each query costs 1 credit. Returns results grouped by query."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    # ---- Enrichment ----
    {
        "name": "nrev_enrich_person",
        "description": (
            "Enrich a person by email, name+domain, or LinkedIn URL. Returns profile "
            "data including title, company, location, seniority, and contact info."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Email address to enrich.",
                },
                "name": {
                    "type": "string",
                    "description": "Full name (e.g. 'John Doe').",
                },
                "company": {
                    "type": "string",
                    "description": "Company name or domain (e.g. 'acme.com').",
                },
                "linkedin_url": {
                    "type": "string",
                    "description": "LinkedIn profile URL.",
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "Force a specific provider. Options: 'apollo' (default, general B2B), "
                        "'rocketreach' (phones, alumni), 'fresh_linkedin' (direct-from-LinkedIn, "
                        "preferred when linkedin_url is the primary identifier). "
                        "Omit for auto-selection."
                    ),
                },
            },
            "required": ["email"],
        },
    },
    {
        "name": "nrev_enrich_company",
        "description": (
            "Enrich a company by LinkedIn URL, domain, or name. Returns the canonical Company "
            "row (name, domain, linkedin_url, employee_count, industry, hq_location) plus "
            "`additional_data` with vendor extras (description, follower_count, specialties, "
            "affiliated companies, etc.). For LinkedIn-native fields, pass `provider=\"fresh_linkedin\"` "
            "with either `linkedin_url` or `domain` — domain lookup is fuzzy; inspect "
            "`additional_data.confident_score` before trusting the match."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "linkedin_url": {
                    "type": "string",
                    "description": (
                        "LinkedIn company URL (e.g. 'https://www.linkedin.com/company/stripe/'). "
                        "Used by `provider=\"fresh_linkedin\"`."
                    ),
                },
                "domain": {
                    "type": "string",
                    "description": "Company domain (e.g. 'stripe.com'). URLs are auto-cleaned.",
                },
                "name": {
                    "type": "string",
                    "description": "Company name (used by Apollo when domain is unknown).",
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "Force a specific provider. Omit for auto-selection (default: apollo). "
                        "Pass 'fresh_linkedin' for direct-from-LinkedIn firmographics."
                    ),
                },
            },
        },
    },
    # ---- Fresh LinkedIn posts (Phase 2.2 / 2.4) ----
    {
        "name": "nrev_fetch_linkedin_posts",
        "description": (
            "Fetch LinkedIn posts via Fresh LinkedIn. Modes: "
            "source_type='profile' (posts by a person's LinkedIn URL), "
            "'company' (posts by a company LinkedIn URL), "
            "'detail' (single post by bare activity URN), "
            "'search' (filter-driven search across LinkedIn — keyword, author URN, mentions). "
            "Returns normalized Post objects. Freshness guaranteed — no caching. 3 credits/call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_type": {
                    "type": "string",
                    "enum": ["profile", "company", "detail", "search"],
                    "description": (
                        "'profile' → recent posts by a person (requires linkedin_url). "
                        "'company' → recent posts by a company (requires linkedin_url). "
                        "'detail' → single post by URN (requires urn). "
                        "'search' → filter-driven post search (requires ≥1 filter)."
                    ),
                },
                "linkedin_url": {
                    "type": "string",
                    "description": (
                        "LinkedIn URL — /in/<slug> for source_type='profile', "
                        "/company/<slug> for source_type='company'."
                    ),
                },
                "urn": {
                    "type": "string",
                    "description": (
                        "Bare activity id (e.g. '7450415215956987904'). "
                        "Required when source_type='detail'. Harvest from a prior posts response."
                    ),
                },
                "cursor": {
                    "type": "string",
                    "description": "Pagination cursor from a prior response (profile / company only).",
                },
                "search_keywords": {
                    "type": "string",
                    "description": "Keyword filter (source_type='search').",
                },
                "sort_by": {
                    "type": "string",
                    "description": "'Latest' (default) or 'Relevance' (source_type='search').",
                },
                "date_posted": {
                    "type": "string",
                    "description": "Vendor-native time window (e.g. 'past-24h', 'past-week'; source_type='search').",
                },
                "content_type": {
                    "type": "string",
                    "description": "Vendor-native content filter (e.g. 'videos', 'images'; source_type='search').",
                },
                "from_member": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Member URNs (ACoAA... form, NOT profile URLs; source_type='search').",
                },
                "from_company": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Company URNs (source_type='search').",
                },
                "mentioning_member": {
                    "type": "array", "items": {"type": "string"},
                },
                "mentioning_company": {
                    "type": "array", "items": {"type": "string"},
                },
                "author_company": {
                    "type": "array", "items": {"type": "string"},
                },
                "author_industry": {
                    "type": "array", "items": {"type": "string"},
                },
                "author_keyword": {
                    "type": "string",
                    "description": "Author keyword filter (source_type='search').",
                },
                "page": {
                    "type": "integer", "minimum": 1,
                    "description": "Page number for search (1-indexed; source_type='search').",
                },
            },
            "required": ["source_type"],
        },
    },
    {
        "name": "nrev_fetch_post_engagement",
        "description": (
            "Fetch engagement on a LinkedIn post via Fresh LinkedIn. "
            "engagement_type='reactions' → reactors (name, headline, linkedin_url, reaction type). "
            "engagement_type='comments' → comments (text, commenter, created_at, pagination cursor). "
            "urn must be the bare numeric activity id. Reactor/commenter linkedin_url is URN-form — "
            "to fully enrich, pass to enrich_person. 3 credits/call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "engagement_type": {
                    "type": "string",
                    "enum": ["reactions", "comments"],
                    "description": "'reactions' or 'comments'.",
                },
                "urn": {
                    "type": "string",
                    "description": "Bare activity id (e.g. '7450415215956987904') from a posts response.",
                },
                "cursor": {
                    "type": "string",
                    "description": "Pagination cursor from a prior response.",
                },
            },
            "required": ["engagement_type", "urn"],
        },
    },
    # ---- Data Management ----
    {
        "name": "nrev_query_table",
        "description": (
            "Query a data table stored in nrev-lite. Returns rows matching the filters. "
            "Use nrev_list_tables first to see available tables."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Name of the table to query.",
                },
                "filters": {
                    "type": "object",
                    "description": "Key-value filters to apply (e.g. {\"industry\": \"SaaS\"}).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum rows to return. Default: 50.",
                    "default": 50,
                },
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "nrev_list_tables",
        "description": "List all data tables with row counts and column counts.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ---- Persistent Datasets ----
    {
        "name": "nrev_create_dataset",
        "description": (
            "Create an empty persistent dataset (table) for workflows to write to over time. "
            "If a dataset with the same slug already exists, returns the existing one (idempotent). "
            "PREFER nrev_create_and_populate_dataset if you already have rows to add — it creates "
            "and populates in a single call. Use this tool only when you need to create the dataset "
            "structure first and add data later."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable name (e.g. 'LinkedIn Posts to Comment').",
                },
                "description": {
                    "type": "string",
                    "description": "What this dataset is used for.",
                },
                "columns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                    "description": "Column definitions. Optional but helps with dashboards.",
                },
                "dedup_key": {
                    "type": "string",
                    "description": (
                        "Column name to use for deduplication. If a row with the same "
                        "value for this key exists, it will be updated instead of inserted. "
                        "E.g. 'url' for LinkedIn posts, 'email' for contacts."
                    ),
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "nrev_append_rows",
        "description": (
            "Append rows to an EXISTING persistent dataset. Each row is a JSON object. "
            "If the dataset has a dedup_key, rows with matching key values are updated (upsert). "
            "IMPORTANT: The 'dataset' parameter must be the slug (from nrev_create_dataset response's "
            "'slug' field) or the dataset UUID. If unsure of the slug, call nrev_list_datasets first. "
            "For creating a NEW dataset with initial data, use nrev_create_and_populate_dataset instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "description": "Dataset slug or UUID.",
                },
                "rows": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of row objects to append. Each object is a row.",
                },
            },
            "required": ["dataset", "rows"],
        },
    },
    {
        "name": "nrev_query_dataset",
        "description": (
            "Query rows from a persistent dataset with optional filters, sorting, and pagination. "
            "Filters are key-value equality matches on row data (e.g. {\"status\": \"pending\"}). "
            "Returns dataset metadata and matching rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "description": "Dataset slug or UUID.",
                },
                "filters": {
                    "type": "object",
                    "description": "Key-value filters on row data (e.g. {\"status\": \"pending\"}).",
                },
                "order_by": {
                    "type": "string",
                    "description": "Column to sort by. Prefix with '-' for descending. Default: -created_at.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum rows to return. Default: 50.",
                    "default": 50,
                },
                "offset": {
                    "type": "integer",
                    "description": "Number of rows to skip. Default: 0.",
                    "default": 0,
                },
            },
            "required": ["dataset"],
        },
    },
    {
        "name": "nrev_list_datasets",
        "description": (
            "List all persistent datasets stored in nrev-lite's internal database. "
            "Shows name, slug, row count, columns, and dedup config. "
            "Call this to find dataset slugs before using nrev_append_rows or nrev_query_dataset. "
            "NOTE: This is for nrev-lite's OWN data storage. To push data to Google Sheets, "
            "HubSpot, Salesforce, or any external app, use nrev_app_list → nrev_app_actions → "
            "nrev_app_action_schema → nrev_app_execute instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "nrev_create_and_populate_dataset",
        "description": (
            "Create a new persistent dataset AND immediately add rows to it in a single call. "
            "This is the RECOMMENDED way to save workflow results. If the dataset already exists "
            "(same slug), rows are appended/upserted to the existing dataset. Returns the dataset "
            "slug and row counts. ALWAYS prefer this over separate create + append calls. "
            "PROACTIVE: Suggest saving to a dataset whenever a workflow produces >5 structured "
            "results (contacts, companies, URLs, posts) that the user might want to track, "
            "query later, build a dashboard from, or use in scheduled workflows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable name (e.g. 'VP Sales SaaS Leads Q1').",
                },
                "description": {
                    "type": "string",
                    "description": "What this dataset is used for.",
                },
                "columns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                    "description": "Column definitions. Helps with dashboards and data preview.",
                },
                "dedup_key": {
                    "type": "string",
                    "description": (
                        "Column name for deduplication. Rows with matching values are updated "
                        "instead of duplicated. Use 'email' for contacts, 'url' for web results, "
                        "'domain' for companies, 'linkedin_url' for profiles."
                    ),
                },
                "rows": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of row objects to add. Each object is a row with key-value pairs.",
                },
            },
            "required": ["name", "rows"],
        },
    },
    {
        "name": "nrev_delete_dataset_rows",
        "description": (
            "Delete specific rows from a dataset by row ID, or delete all rows to reset it. "
            "Use nrev_query_dataset first to find row IDs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "description": "Dataset slug or UUID.",
                },
                "row_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Array of row UUIDs to delete. Use nrev_query_dataset to find IDs.",
                },
                "all_rows": {
                    "type": "boolean",
                    "description": "Set to true to delete ALL rows (reset the dataset). Default: false.",
                    "default": False,
                },
            },
            "required": ["dataset"],
        },
    },
    {
        "name": "nrev_update_dataset",
        "description": (
            "Update a dataset's metadata: name, description, columns, or dedup_key. "
            "Only the provided fields are changed. Use the slug or UUID to identify the dataset."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "description": "Dataset slug or UUID.",
                },
                "name": {
                    "type": "string",
                    "description": "New human-readable name. Also updates the slug.",
                },
                "description": {
                    "type": "string",
                    "description": "New description.",
                },
                "columns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                    "description": "Updated column definitions.",
                },
                "dedup_key": {
                    "type": "string",
                    "description": "Updated dedup key column name.",
                },
            },
            "required": ["dataset"],
        },
    },
    {
        "name": "nrev_delete_dataset",
        "description": (
            "Archive (soft-delete) a dataset. It will no longer appear in listings but "
            "data is preserved and can be restored by an admin. Use with caution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "description": "Dataset slug or UUID to archive.",
                },
            },
            "required": ["dataset"],
        },
    },
    {
        "name": "nrev_estimate_cost",
        "description": (
            "Estimate credit cost before executing operations. Two modes:\n"
            "  • Single: pass `operation` (+ optional `count`) for a quick estimate.\n"
            "  • Bulk: pass `operations` — a list of up to 50 {operation, params} "
            "items, mixed types allowed. Use this before any multi-step plan to "
            "show the user the full spend in one number."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "Single mode: the operation type to estimate.",
                    "enum": [
                        "enrich_person", "enrich_company", "search_people",
                        "search_web", "scrape_page", "google_search",
                        "company_signals", "find_email", "verify_email",
                        "domain_search", "ai_research", "batch_extract",
                    ],
                },
                "count": {
                    "type": "integer",
                    "description": "Single mode: number of records/URLs to process.",
                    "default": 1,
                },
                "operations": {
                    "type": "array",
                    "maxItems": 50,
                    "description": (
                        "Bulk mode: list of up to 50 {operation, params} items. "
                        "`params` is the same shape you would send to /execute "
                        "(e.g. {\"per_page\": 100} for search_people, "
                        "{\"details\": [...]} for bulk_enrich_people). "
                        "Mutually exclusive with `operation`."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation": {"type": "string"},
                            "params": {"type": "object"},
                        },
                        "required": ["operation"],
                    },
                },
            },
            "required": [],
        },
    },
    # ---- Account ----
    {
        "name": "nrev_credit_balance",
        "description": (
            "Check the current credit balance and monthly spend. "
            "BYOK (bring-your-own-key) calls are free; platform key calls cost credits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "nrev_provider_status",
        "description": (
            "Check available providers and their status. Shows which providers have "
            "BYOK keys vs platform keys, and whether they are operational."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ---- Search Intelligence ----
    {
        "name": "nrev_search_patterns",
        "description": (
            "Get platform-specific Google search query patterns and GTM use case playbooks. "
            "ALWAYS call this before constructing Google searches for specific platforms "
            "(LinkedIn, Twitter, Reddit, Instagram, etc.) or GTM use cases (hiring signals, "
            "funding news, competitor intel). Returns exact site: prefixes, query templates, "
            "operator usage, tbs recommendations, and tips. This data lives on the server "
            "and evolves without client updates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": (
                        "Get patterns for a specific platform. Options: "
                        "linkedin_profiles, linkedin_posts, linkedin_jobs, linkedin_companies, "
                        "twitter_posts, twitter_profiles, reddit_discussions, "
                        "instagram_businesses, youtube_content, github_repos, "
                        "g2_reviews, crunchbase_companies, local_businesses, glassdoor_company"
                    ),
                },
                "use_case": {
                    "type": "string",
                    "description": (
                        "Get patterns for a GTM use case. Options: "
                        "funding_news, hiring_signals, leadership_changes, "
                        "competitor_intelligence, tech_stack_discovery, "
                        "non_traditional_list_building, content_research, buying_intent"
                    ),
                },
            },
            "required": [],
        },
    },
    # ---- Connected Apps (Composio) ----
    {
        "name": "nrev_app_actions",
        "description": (
            "Discover what actions are available for a connected app. Use this for ANY interaction "
            "with external tools: send email, create spreadsheet, push data to Google Sheets, update CRM, "
            "create calendar event, post to Slack, add leads to campaigns, etc. "
            "**This is the tool for exporting/pushing data to external apps** — NOT nrev_list_datasets "
            "or nrev_query_dataset (those are for nrev-lite's internal storage). "
            "Call after nrev_app_list confirms the app is connected. **Required step** — "
            "never guess action names. "
            "Workflow: nrev_app_list → nrev_app_actions → nrev_app_action_schema → nrev_app_execute"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "string",
                    "description": (
                        "App key: gmail, slack, microsoft_teams, google_sheets, google_docs, "
                        "google_drive, airtable, hubspot, salesforce, attio, linear, notion, "
                        "clickup, asana, google_calendar, calendly, cal_com, zoom, fireflies, "
                        "fathom, instantly, posthog"
                    ),
                },
            },
            "required": ["app_id"],
        },
    },
    {
        "name": "nrev_app_action_schema",
        "description": (
            "Get the exact parameter schema for a specific action. Returns parameter names, "
            "types, descriptions, and which are required. **NEVER SKIP THIS STEP** — parameter "
            "names are not guessable (e.g., Google Docs uses 'text_to_insert' not 'text', "
            "Sheets 'ranges' is an array not a string). Call after nrev_app_actions, "
            "before nrev_app_execute."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_name": {
                    "type": "string",
                    "description": "Action name from nrev_app_actions (e.g. GMAIL_SEND_EMAIL).",
                },
            },
            "required": ["action_name"],
        },
    },
    {
        "name": "nrev_app_execute",
        "description": (
            "Execute an action on a connected app — this is how you push data to Google Sheets, "
            "send emails via Gmail, create CRM contacts in HubSpot, add leads to Instantly campaigns, "
            "post to Slack, create calendar events, and interact with any connected app. "
            "**Use this to export/push/send data to external tools.** "
            "The tenant must have an active OAuth connection. "
            "Requires: nrev_app_actions to find the action name, then nrev_app_action_schema "
            "to get exact parameters. App actions are free (no credits charged). "
            "NEVER use bash, CLI commands, or raw HTTP calls — always use this MCP tool."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "string",
                    "description": "Catalog app key (e.g. 'gmail', 'google_sheets').",
                },
                "action": {
                    "type": "string",
                    "description": "Action name from nrev_app_actions (e.g. GMAIL_SEND_EMAIL).",
                },
                "params": {
                    "type": "object",
                    "description": "Action parameters from nrev_app_action_schema.",
                },
            },
            "required": ["app_id", "action", "params"],
        },
    },
    {
        "name": "nrev_app_list",
        "description": (
            "List all connected apps for the current tenant (Gmail, Slack, Google Sheets, HubSpot, etc.). "
            "**Call this when the user wants to send data to, read from, or interact with ANY external tool** — "
            "including exporting to Google Sheets, sending emails, pushing to CRM, posting to Slack, etc. "
            "This tells you which apps are connected and ready to use. "
            "Next step after this: nrev_app_actions → nrev_app_action_schema → nrev_app_execute. "
            "If an app is NOT connected, use nrev_app_connect to set it up. "
            "If a system MCP tool exists for the same app (e.g., slack_send_message), prefer the system MCP. "
            "**Do NOT use bash, CLI commands, or raw HTTP calls to interact with apps — use these MCP tools.**"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "nrev_app_connect",
        "description": (
            "Connect a new app from within Claude Code. For OAuth apps (Gmail, Slack, etc.), "
            "returns an OAuth URL — show it to the user to authorize in their browser. "
            "For API key apps (Instantly, Fireflies, PostHog), first call without api_key "
            "to learn the required fields, then ask the user for their key and call again "
            "with the api_key parameter. NEVER log or display API keys. "
            "After connecting, call nrev_app_list to confirm."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "string",
                    "description": (
                        "App key to connect: gmail, slack, microsoft_teams, google_sheets, "
                        "google_docs, google_drive, airtable, hubspot, salesforce, attio, "
                        "linear, notion, clickup, asana, google_calendar, calendly, cal_com, "
                        "zoom, fireflies, instantly, posthog"
                    ),
                },
                "api_key": {
                    "type": "string",
                    "description": (
                        "API key for the app (only for API key apps like instantly, fireflies, posthog). "
                        "Get this from the user — NEVER guess or fabricate keys."
                    ),
                },
                "extra_fields": {
                    "type": "object",
                    "description": "Additional fields if required (e.g., subdomain for PostHog).",
                },
                "label": {
                    "type": "string",
                    "description": (
                        "User-friendly label for this connection (e.g., 'Production workspace', "
                        "'Nikhil Instantly account'). Ask the user for a label when connecting "
                        "API key apps so they can identify the key later."
                    ),
                },
            },
            "required": ["app_id"],
        },
    },
    {
        "name": "nrev_app_catalog",
        "description": (
            "Browse all available apps that can be connected to nrev-lite (22 apps across "
            "communication, CRM, calendar, data, outreach, project management, meetings, and analytics). "
            "Returns each app's name, category, connection type (OAuth or API key), and whether the "
            "user has already connected it. "
            "**Call this when the user asks:** 'What apps can I connect?', 'Show me available integrations', "
            "'What tools do you support?', 'Can I connect [app name]?'. "
            "This is different from nrev_app_list which only shows CONNECTED apps."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "nrev_open_console",
        "description": (
            "Open the nrev-lite web dashboard (console) in the user's browser. The console shows "
            "credit balance, connected apps, API keys, workflow runs, datasets, and dashboards. "
            "**Call this when the user asks:** 'Show me my dashboard', 'Open the console', "
            "'How many credits do I have?' (if they want the full view), 'Manage my API keys', "
            "'Show my connected apps', 'Where can I see my runs?', 'Open settings'. "
            "Pass a tab parameter to open a specific section directly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tab": {
                    "type": "string",
                    "description": (
                        "Which console tab to open. Options: "
                        "'usage' (credits & billing), "
                        "'keys' (API key management), "
                        "'apps' (connected apps), "
                        "'runs' (workflow history), "
                        "'datasets' (saved data), "
                        "'dashboards' (shared dashboards). "
                        "Default: main dashboard."
                    ),
                    "enum": ["usage", "keys", "apps", "runs", "datasets", "dashboards"],
                },
            },
            "required": [],
        },
    },
    {
        "name": "nrev_health",
        "description": (
            "Quick health check — verifies the nrev-lite server is reachable, the user is "
            "authenticated, and returns tenant info. Use this to diagnose connection issues."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ---- Workflow Management ----
    {
        "name": "nrev_new_workflow",
        "description": (
            "Start a new workflow within the current session. Call this when beginning "
            "a new use case, a new data set, or a new prospecting task. This creates a "
            "fresh workflow ID so that run logs are grouped separately in the dashboard. "
            "Every session starts with one workflow automatically — only call this when "
            "switching to a genuinely different task."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": (
                        "Human-readable label for this workflow "
                        "(e.g. 'Bakeries in San Jose', 'Competitor Deal Snatch — Acme Corp'). "
                        "Shows in the dashboard run logs."
                    ),
                },
            },
            "required": [],
        },
    },
    # ---- People Search ----
    {
        "name": "nrev_search_people",
        "description": (
            "Search for people/contacts using B2B databases (Apollo, RocketReach). "
            "Returns matching profiles with name, title, company, location. "
            "Does NOT return contact info directly — use nrev_enrich_person to get emails/phones. "
            "IMPORTANT: Check tool-skills for provider-specific quirks BEFORE calling."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "titles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Job titles to search for (e.g. ['VP Sales', 'Director Marketing']).",
                },
                "company_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Company domains to search within. "
                        "Apollo quirk: this is a NEWLINE-SEPARATED STRING in the API, "
                        "but nrev-lite handles the conversion automatically."
                    ),
                },
                "company_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Company names (free-text matching). Use for companies without known domains.",
                },
                "locations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Location filters (e.g. ['San Francisco, CA', 'United States']).",
                },
                "industries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Industry filters.",
                },
                "seniority_levels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Seniority: director, vp, c_suite, manager, senior, entry.",
                },
                "employee_ranges": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Employee count ranges. Apollo format: '1,10', '11,50', '51,200', '201,500', '501,1000', '1001,5000', '5001,10000'.",
                },
                "previous_employer": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "RocketReach only: search by previous employer (alumni search). "
                        "Free-text company names, NOT domains. Use multiple variations for "
                        "better matching (e.g. ['Yellow.ai', 'yellow.ai', 'Yellow AI'])."
                    ),
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "Force a specific provider: 'apollo' (standard B2B), "
                        "'rocketreach' (alumni/previous employer). "
                        "If omitted, auto-selects based on filters."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return. Default: 25.",
                    "default": 25,
                },
            },
            "required": ["person_titles"],
        },
    },
    {
        "name": "nrev_get_run_log",
        "description": (
            "Get the run log for a workflow — returns all steps with their results, "
            "params, status, and column metadata. Use this to answer user questions "
            "about workflow output, check data quality, or review what happened. "
            "Returns truncated results (20 rows max per step) plus column metadata "
            "(type, null%, unique count, sample values). Defaults to current workflow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "Workflow ID to fetch. If omitted, uses the current workflow.",
                },
                "step_index": {
                    "type": "integer",
                    "description": "Fetch only a specific step (0-indexed). Omit to get all steps.",
                },
                "include_metadata": {
                    "type": "boolean",
                    "description": "Include column metadata (type, null%, unique count). Default: true.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "nrev_deploy_site",
        "description": (
            "Deploy a static HTML/CSS/JS site hosted on nrev-lite, backed by persistent datasets. "
            "The site is served on a public URL and can CRUD its connected datasets "
            "using the auto-injected window.NRV_APP_TOKEN and window.NRV_DATASETS_URL. "
            "Pass all files as a dict of {path: content}. The entry_point HTML file "
            "gets NRV context variables injected automatically."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Site name (used to generate the slug)",
                },
                "files": {
                    "type": "object",
                    "description": (
                        "Dict of {file_path: file_content}. "
                        "Example: {'index.html': '<html>...', 'style.css': '...', 'app.js': '...'}"
                    ),
                },
                "dataset_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of dataset IDs this app can read/write. Use nrev_list_datasets to find IDs.",
                },
                "entry_point": {
                    "type": "string",
                    "description": "Main HTML file to serve (default: index.html)",
                    "default": "index.html",
                },
            },
            "required": ["name", "files", "dataset_ids"],
        },
    },

    # ---- Scripts ----
    {
        "name": "nrev_save_script",
        "description": (
            "Save a parameterized workflow script for later reuse. "
            "A script is an ordered list of tool call steps with declared parameters. "
            "Parameters use {{param_name}} placeholders in step params. "
            "Steps can reference previous step results via for_each: 'step_N.results' "
            "and {{item.field}} for iteration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable script name (e.g. 'CTO Search at Target Company').",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the script does.",
                },
                "parameters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Parameter name (used in {{name}} placeholders)."},
                            "type": {"type": "string", "description": "Parameter type: string, list, number, boolean."},
                            "description": {"type": "string", "description": "What this parameter controls."},
                            "default": {"description": "Optional default value."},
                        },
                        "required": ["name", "type", "description"],
                    },
                    "description": "Declared parameters that the user supplies at run time.",
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "order": {"type": "integer", "description": "Step execution order (1-based)."},
                            "tool_name": {"type": "string", "description": "nrev-lite MCP tool to call (e.g. nrev_search_people)."},
                            "description": {"type": "string", "description": "What this step does."},
                            "params": {"type": "object", "description": "Tool parameters. Use {{param}} for user inputs, {{item.field}} inside for_each loops."},
                            "for_each": {"type": "string", "description": "Iterate over a previous step's results (e.g. 'step_1.results'). Each item is available as {{item}}."},
                        },
                        "required": ["order", "tool_name", "params"],
                    },
                    "description": "Ordered list of tool calls to execute.",
                },
                "source_workflow_id": {
                    "type": "string",
                    "description": "The workflow_id this script was captured from (optional, for provenance).",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorization.",
                },
            },
            "required": ["name", "parameters", "steps"],
        },
    },
    {
        "name": "nrev_list_scripts",
        "description": (
            "List all saved scripts for the current tenant. "
            "Returns script names, descriptions, parameter count, step count, "
            "run count, and last run timestamp."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "nrev_get_script",
        "description": (
            "Load a saved script by name (slug) or ID. Returns the full definition "
            "including parameters and steps. Use this to inspect a script before "
            "running it or to show the user what it does."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "Script slug (URL-safe name) or UUID.",
                },
            },
            "required": ["script"],
        },
    },

    # ---- Learning System ----
    {
        "name": "nrev_log_learning",
        "description": (
            "Log a discovery made during a workflow for admin review. "
            "Call this whenever you discover something reusable: a URL structure "
            "for a new platform, an API quirk, enrichment hit rates, scraping patterns, etc. "
            "Admins review these and approved learnings become available to all users."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": [
                        "search_pattern", "api_quirk", "enrichment_strategy",
                        "scraping_pattern", "data_mapping", "provider_behavior",
                    ],
                    "description": "Type of learning.",
                },
                "platform": {
                    "type": "string",
                    "description": "Platform or provider involved (e.g. 'producthunt', 'apollo').",
                },
                "tool_name": {
                    "type": "string",
                    "description": "Which nrev-lite tool was being used when the discovery was made.",
                },
                "subcategory": {
                    "type": "string",
                    "description": "Finer classification (e.g. 'site_prefix', 'field_behavior', 'hit_rate').",
                },
                "discovery": {
                    "type": "object",
                    "description": (
                        "The actual learning. Structure varies by category. "
                        "For search_pattern: {site_prefix, url_structure, platform_name, description, sample_queries, recommended_params}. "
                        "For api_quirk: {provider, operation, finding, workaround}. "
                        "For enrichment_strategy: {provider, operation, data_type, hit_rate, sample_size, finding}."
                    ),
                },
                "evidence": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Supporting data points: URLs, queries, API responses that prove the discovery.",
                },
                "confidence": {
                    "type": "number",
                    "description": "How confident you are in this discovery (0.0 to 1.0). Default: 0.5.",
                    "default": 0.5,
                },
                "user_prompt": {
                    "type": "string",
                    "description": (
                        "A 1-2 sentence summary of what the user originally asked for. "
                        "Gives admins context when reviewing. "
                        "Example: 'Find all GTM tool launches on Product Hunt in the last 60 days'"
                    ),
                },
            },
            "required": ["category", "discovery", "user_prompt"],
        },
    },
    {
        "name": "nrev_get_knowledge",
        "description": (
            "Look up approved knowledge by category and key. Check this before making "
            "assumptions about unknown platforms, APIs, or providers. Returns the learning "
            "if it exists, or {found: false} if not."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Knowledge category (search_pattern, api_quirk, enrichment_strategy, etc.).",
                },
                "key": {
                    "type": "string",
                    "description": "Lookup key (e.g. platform name for search_pattern, 'apollo_title_filter' for api_quirk).",
                },
            },
            "required": ["category", "key"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _handle_nrev_search_web(args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query", "")
    if not query:
        return {"error": "Parameter 'query' is required."}

    params: dict[str, Any] = {"q": query, "num": args.get("max_results", 10)}
    result = _api_request("POST", "/execute", json_body={
        "operation": "search_web",
        "params": params,
        "provider": "rapidapi_google",
    })
    return result


def _handle_nrev_scrape_page(args: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url")
    urls = args.get("urls")
    if not url and not urls:
        return {"error": "Either 'url' or 'urls' parameter is required."}

    params: dict[str, Any] = {}
    if url:
        params["url"] = url
    if urls:
        params["urls"] = urls
    if args.get("objective"):
        params["objective"] = args["objective"]

    result = _api_request("POST", "/execute", json_body={
        "operation": "scrape_page",
        "params": params,
        "provider": "parallel_web",
    })
    return result


def _handle_nrev_google_search(args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query", "")
    queries = args.get("queries")

    if not query and not queries:
        return {"error": "Parameter 'query' is required (or 'queries' for bulk search)."}

    params: dict[str, Any] = {
        "num": args.get("num_results", 10),
    }

    # Single query or bulk queries
    if queries and isinstance(queries, list) and len(queries) > 1:
        params["queries"] = queries
        params["q"] = queries[0]  # server needs at least one q
    else:
        params["q"] = query

    # Site restriction (convenience)
    if args.get("site"):
        params["site"] = args["site"]

    # Country (gl param)
    if args.get("country"):
        params["gl"] = args["country"]

    # Language (hl param)
    if args.get("language"):
        params["hl"] = args["language"]

    # Time-based search — accept tbs directly or friendly names
    # Supports: hour, day, week, month, year, qdr:h2, qdr:d3, cdr:1,...
    tbs = args.get("tbs") or args.get("time_filter")
    if tbs:
        params["tbs"] = tbs

    result = _api_request("POST", "/execute", json_body={
        "operation": "search_web",
        "params": params,
        "provider": "rapidapi_google",
    })
    return result


def _handle_nrev_enrich_person(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if args.get("email"):
        params["email"] = args["email"].strip().lower()
    if args.get("name"):
        params["name"] = args["name"].strip()
    if args.get("company"):
        # Could be a domain or name
        company = args["company"].strip()
        if "." in company:
            params["domain"] = company
        else:
            params["organization_name"] = company
    if args.get("linkedin_url"):
        params["linkedin_url"] = args["linkedin_url"].strip()

    if not params:
        return {"error": "At least one identifier is required (email, name, company, or linkedin_url)."}

    body: dict[str, Any] = {
        "operation": "enrich_person",
        "params": params,
    }
    if args.get("provider"):
        body["provider"] = args["provider"]

    return _api_request("POST", "/execute", json_body=body)


def _handle_nrev_enrich_company(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if args.get("linkedin_url"):
        params["linkedin_url"] = args["linkedin_url"].strip()
    if args.get("domain"):
        domain = args["domain"].strip().lower()
        if domain.startswith(("http://", "https://")):
            from urllib.parse import urlparse
            parsed = urlparse(domain)
            domain = parsed.hostname or domain
        if domain.startswith("www."):
            domain = domain[4:]
        domain = domain.rstrip("/").rstrip(".")
        params["domain"] = domain
    if args.get("name"):
        params["name"] = args["name"].strip()

    if not params:
        return {"error": "At least one of 'linkedin_url', 'domain', or 'name' is required."}

    body: dict[str, Any] = {
        "operation": "enrich_company",
        "params": params,
    }
    if args.get("provider"):
        body["provider"] = args["provider"]

    return _api_request("POST", "/execute", json_body=body)


def _handle_nrev_fetch_linkedin_posts(args: dict[str, Any]) -> dict[str, Any]:
    source_type = (args.get("source_type") or "").strip()
    if source_type not in ("profile", "company", "detail", "search"):
        return {"error": "source_type must be one of: profile, company, detail, search"}

    params: dict[str, Any] = {}
    if source_type == "detail":
        urn = (args.get("urn") or "").strip()
        if not urn:
            return {"error": "source_type='detail' requires 'urn' (bare activity id)"}
        params["urn"] = urn
        operation = "fetch_post_details"
    elif source_type == "search":
        for key in (
            "search_keywords", "sort_by", "date_posted", "content_type",
            "from_member", "from_company", "mentioning_member", "mentioning_company",
            "author_company", "author_industry", "author_keyword", "page",
        ):
            if args.get(key) is not None:
                params[key] = args[key]
        operation = "search_posts"
    else:
        linkedin_url = (args.get("linkedin_url") or "").strip()
        if not linkedin_url:
            return {"error": f"source_type='{source_type}' requires 'linkedin_url'"}
        params["linkedin_url"] = linkedin_url
        if args.get("cursor"):
            params["cursor"] = str(args["cursor"])
        operation = (
            "fetch_profile_posts" if source_type == "profile" else "fetch_company_posts"
        )

    body = {
        "operation": operation,
        "provider": "fresh_linkedin",
        "params": params,
    }
    return _api_request("POST", "/execute", json_body=body)


def _handle_nrev_fetch_post_engagement(args: dict[str, Any]) -> dict[str, Any]:
    engagement_type = (args.get("engagement_type") or "").strip()
    if engagement_type not in ("reactions", "comments"):
        return {"error": "engagement_type must be one of: reactions, comments"}

    urn = (args.get("urn") or "").strip()
    if not urn:
        return {"error": "'urn' is required (bare activity id)"}

    params: dict[str, Any] = {"urn": urn}
    if args.get("cursor"):
        params["cursor"] = str(args["cursor"])

    operation = (
        "fetch_post_reactions" if engagement_type == "reactions" else "fetch_post_comments"
    )
    body = {
        "operation": operation,
        "provider": "fresh_linkedin",
        "params": params,
    }
    return _api_request("POST", "/execute", json_body=body)


def _handle_nrev_query_table(args: dict[str, Any]) -> dict[str, Any]:
    table_name = args.get("table_name", "")
    if not table_name:
        return {"error": "Parameter 'table_name' is required."}

    params: dict[str, Any] = {}
    filters = args.get("filters")
    if filters and isinstance(filters, dict):
        params.update(filters)
    limit = args.get("limit", 50)
    params["limit"] = limit

    return _api_request("GET", f"/tables/{table_name}", params=params)


def _handle_nrev_list_tables(args: dict[str, Any]) -> dict[str, Any]:
    return _api_request("GET", "/tables")


# ---- Persistent Datasets ----


def _handle_nrev_create_dataset(args: dict[str, Any]) -> dict[str, Any]:
    name = args.get("name", "").strip()
    if not name:
        return {"error": "Parameter 'name' is required."}

    body: dict[str, Any] = {"name": name}
    if args.get("description"):
        body["description"] = args["description"]
    if args.get("columns"):
        body["columns"] = args["columns"]
    if args.get("dedup_key"):
        body["dedup_key"] = args["dedup_key"]

    # Include workflow_id so we know which workflow created this dataset
    body["workflow_id"] = WORKFLOW_ID

    return _api_request("POST", "/datasets", json_body=body)


def _handle_nrev_append_rows(args: dict[str, Any]) -> dict[str, Any]:
    dataset = args.get("dataset", "").strip()
    if not dataset:
        return {"error": "Parameter 'dataset' is required (slug or UUID)."}

    rows = args.get("rows", [])
    if not rows or not isinstance(rows, list):
        return {"error": "Parameter 'rows' must be a non-empty array of objects."}

    body: dict[str, Any] = {
        "rows": rows,
        "workflow_id": WORKFLOW_ID,
    }

    return _api_request("POST", f"/datasets/{dataset}/rows", json_body=body)


def _handle_nrev_query_dataset(args: dict[str, Any]) -> dict[str, Any]:
    dataset = args.get("dataset", "").strip()
    if not dataset:
        return {"error": "Parameter 'dataset' is required (slug or UUID)."}

    params: dict[str, Any] = {}
    if args.get("limit"):
        params["limit"] = args["limit"]
    if args.get("offset"):
        params["offset"] = args["offset"]
    if args.get("order_by"):
        params["order_by"] = args["order_by"]
    if args.get("filters"):
        params["filters"] = json.dumps(args["filters"])

    return _api_request("GET", f"/datasets/{dataset}", params=params)


def _handle_nrev_list_datasets(args: dict[str, Any]) -> dict[str, Any]:
    return _api_request("GET", "/datasets")


def _handle_nrev_create_and_populate_dataset(args: dict[str, Any]) -> dict[str, Any]:
    name = args.get("name", "").strip()
    if not name:
        return {"error": "Parameter 'name' is required."}

    rows = args.get("rows", [])
    if not rows or not isinstance(rows, list):
        return {"error": "Parameter 'rows' must be a non-empty array of objects."}

    # Step 1: Create (or get existing) dataset
    create_body: dict[str, Any] = {"name": name, "workflow_id": WORKFLOW_ID}
    if args.get("description"):
        create_body["description"] = args["description"]
    if args.get("columns"):
        create_body["columns"] = args["columns"]
    if args.get("dedup_key"):
        create_body["dedup_key"] = args["dedup_key"]

    create_result = _api_request("POST", "/datasets", json_body=create_body)
    if "error" in create_result:
        return create_result

    slug = create_result.get("slug", "")
    if not slug:
        return {"error": "Dataset creation succeeded but no slug was returned."}

    # Step 2: Append rows
    append_body: dict[str, Any] = {"rows": rows, "workflow_id": WORKFLOW_ID}
    append_result = _api_request("POST", f"/datasets/{slug}/rows", json_body=append_body)
    if "error" in append_result:
        return {
            "dataset": create_result,
            "error": f"Dataset created but row append failed: {append_result['error']}",
        }

    # Merge results
    return {
        "dataset_id": create_result.get("id"),
        "dataset_slug": slug,
        "dataset_name": create_result.get("name"),
        "dataset_status": create_result.get("status"),
        "columns": create_result.get("columns"),
        "dedup_key": create_result.get("dedup_key"),
        "inserted": append_result.get("inserted", 0),
        "updated": append_result.get("updated", 0),
        "total_rows": append_result.get("total_rows", 0),
    }


def _handle_nrev_delete_dataset_rows(args: dict[str, Any]) -> dict[str, Any]:
    dataset = args.get("dataset", "").strip()
    if not dataset:
        return {"error": "Parameter 'dataset' is required (slug or UUID)."}

    row_ids = args.get("row_ids")
    all_rows = args.get("all_rows", False)

    if not row_ids and not all_rows:
        return {"error": "Provide 'row_ids' (array of row UUIDs) or set 'all_rows' to true."}

    body: dict[str, Any] = {}
    if row_ids:
        body["row_ids"] = row_ids
    if all_rows:
        body["all_rows"] = True

    return _api_request("DELETE", f"/datasets/{dataset}/rows", json_body=body)


def _handle_nrev_update_dataset(args: dict[str, Any]) -> dict[str, Any]:
    dataset = args.get("dataset", "").strip()
    if not dataset:
        return {"error": "Parameter 'dataset' is required (slug or UUID)."}

    body: dict[str, Any] = {}
    if args.get("name") is not None:
        body["name"] = args["name"]
    if args.get("description") is not None:
        body["description"] = args["description"]
    if args.get("columns") is not None:
        body["columns"] = args["columns"]
    if args.get("dedup_key") is not None:
        body["dedup_key"] = args["dedup_key"]

    if not body:
        return {"error": "Provide at least one field to update (name, description, columns, or dedup_key)."}

    return _api_request("PATCH", f"/datasets/{dataset}", json_body=body)


def _handle_nrev_delete_dataset(args: dict[str, Any]) -> dict[str, Any]:
    dataset = args.get("dataset", "").strip()
    if not dataset:
        return {"error": "Parameter 'dataset' is required (slug or UUID)."}

    return _api_request("DELETE", f"/datasets/{dataset}")


def _handle_nrev_credit_balance(args: dict[str, Any]) -> dict[str, Any]:
    result = _api_request("GET", "/credits/balance")
    if "error" not in result:
        # Add the console URL for easy topup
        creds = load_credentials()
        tenant_id = (creds or {}).get("user_info", {}).get("tenant", "")
        base_url = get_api_base_url()
        if tenant_id:
            result["topup_url"] = f"{base_url}/console/{tenant_id}?tab=usage"
            result["_tip"] = (
                f"To add credits, visit: {result['topup_url']}\n"
                f"Or add your own API keys (free): `nrev-lite keys add <provider>`"
            )
    return result


def _handle_nrev_provider_status(args: dict[str, Any]) -> dict[str, Any]:
    result = _api_request("GET", "/keys")
    if "error" in result:
        return result

    keys_data = result.get("keys", [])

    # Build a status overview
    providers_info = [
        ("apollo", "Person & company enrichment, people search"),
        ("rocketreach", "Person enrichment, school/alumni search"),
        ("fresh_linkedin", "LinkedIn profile enrichment — fresher data when LinkedIn URL is known"),
        ("pdl", "People Data Labs enrichment"),
        ("hunter", "Email finder and verifier"),
        ("leadmagic", "Lead enrichment"),
        ("zerobounce", "Email verification"),
        ("rapidapi_google", "Google web search"),
        ("parallel_web", "Web scraping and content extraction"),
        ("predictleads", "Company jobs, news, similar companies"),
    ]

    providers = []
    for prov_name, desc in providers_info:
        has_byok = any(k.get("provider") == prov_name for k in keys_data)
        providers.append({
            "provider": prov_name,
            "description": desc,
            "key_source": "byok" if has_byok else "platform",
            "status": "available",
        })

    return {"providers": providers, "byok_keys": len(keys_data)}


def _handle_nrev_search_patterns(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if args.get("platform"):
        params["platform"] = args["platform"]
    if args.get("use_case"):
        params["use_case"] = args["use_case"]
    return _api_request("GET", "/search/patterns", params=params or None)


def _handle_nrev_app_actions(args: dict[str, Any]) -> dict[str, Any]:
    app_id = args.get("app_id", "")
    if not app_id:
        return {"error": "Parameter 'app_id' is required (e.g. 'gmail', 'google_sheets')."}
    return _api_request("GET", "/connections/actions", params={"app_id": app_id})


def _handle_nrev_app_action_schema(args: dict[str, Any]) -> dict[str, Any]:
    action_name = args.get("action_name", "")
    if not action_name:
        return {"error": "Parameter 'action_name' is required (e.g. 'GMAIL_SEND_EMAIL')."}
    return _api_request("GET", f"/connections/actions/{action_name}/schema")


def _handle_nrev_app_execute(args: dict[str, Any]) -> dict[str, Any]:
    app_id = args.get("app_id", "")
    action = args.get("action", "")
    params = args.get("params", {})

    if not app_id:
        return {"error": "Parameter 'app_id' is required (e.g. 'gmail', 'google_sheets')."}
    if not action:
        return {"error": "Parameter 'action' is required (e.g. 'GMAIL_SEND_EMAIL')."}

    return _api_request("POST", "/connections/execute", json_body={
        "app_id": app_id,
        "action": action,
        "params": params,
    }, timeout=90)


def _handle_nrev_app_list(args: dict[str, Any]) -> dict[str, Any]:
    result = _api_request("GET", "/connections")
    if "error" in result:
        return result

    connections = result.get("connections", [])
    # Only surface ACTIVE connections as usable
    active = [c for c in connections if (c.get("status") or "").upper() == "ACTIVE"]

    # Always include the console apps URL
    creds = load_credentials()
    tenant_id = (creds or {}).get("user_info", {}).get("tenant", "")
    base_url = get_api_base_url()
    console_url = f"{base_url}/console/{tenant_id}?tab=apps" if tenant_id else ""

    if not active:
        return {
            "connections": [],
            "console_url": console_url,
            "message": (
                "No apps connected yet. Use nrev_app_catalog to browse all 22 available apps, "
                "then nrev_app_connect(app_id='...') to connect one. "
                f"Or manage apps in the console: {console_url}"
            ),
        }
    return {
        "connections": active,
        "console_url": console_url,
        "_next_step": (
            "To use a connected app: call nrev_app_actions(app_id='...') to discover actions, "
            "then nrev_app_action_schema(action_name='...') for exact params, "
            "then nrev_app_execute(app_id, action, params) to execute. "
            "NEVER use bash, CLI, or raw HTTP — always use these MCP tools."
        ),
    }


def _handle_nrev_app_connect(args: dict[str, Any]) -> dict[str, Any]:
    """Initiate a connection for an app from within Claude Code.

    For OAuth apps: returns a URL for the user to authorize in their browser.
    For API key apps: pass the user's api_key to connect directly.
    """
    app_id = args.get("app_id", "").strip().lower()
    if not app_id:
        return {"error": "Parameter 'app_id' is required (e.g. 'gmail', 'hubspot', 'slack')."}

    api_key = args.get("api_key", "")
    extra_fields = args.get("extra_fields", {})
    label = args.get("label", "")

    body: dict[str, Any] = {"app_id": app_id}
    if api_key:
        body["api_key"] = api_key
    if extra_fields:
        body["extra_fields"] = extra_fields
    if label:
        body["label"] = label

    result = _api_request("POST", "/connections/initiate", json_body=body)

    if "error" in result:
        return result

    # API key required — tell Claude to ask user for the key
    if result.get("status") == "api_key_required":
        fields = result.get("key_fields", [])
        field_desc = ", ".join(f['label'] for f in fields) if fields else "API key"
        return {
            "status": "api_key_required",
            "app_id": app_id,
            "key_fields": fields,
            "message": (
                f"{app_id} requires an API key to connect. "
                f"Ask the user for their {field_desc}, then call nrev_app_connect "
                f"again with the api_key parameter. "
                f"NEVER log or display the key — pass it directly."
            ),
        }

    if result.get("status") == "redirect":
        oauth_url = result.get("redirect_url", "")
        return {
            "status": "oauth_required",
            "app_id": app_id,
            "oauth_url": oauth_url,
            "connection_id": result.get("connection_id", ""),
            "message": (
                f"To connect {app_id}, the user needs to authorize via OAuth. "
                f"Show them this URL and ask them to open it in their browser:\n\n"
                f"  {oauth_url}\n\n"
                f"After they complete authorization, call nrev_app_list to confirm "
                f"the connection is active."
            ),
        }
    elif result.get("status") == "connected":
        return {
            "status": "connected",
            "app_id": app_id,
            "message": f"{app_id} is now connected and ready to use.",
        }

    return result


def _handle_nrev_app_catalog(args: dict[str, Any]) -> dict[str, Any]:
    """Browse all available apps (connected and not-yet-connected)."""
    result = _api_request("GET", "/connections/available")
    if "error" in result:
        # Fallback: return the hardcoded catalog if the endpoint isn't available
        creds = load_credentials()
        tenant_id = (creds or {}).get("user_info", {}).get("tenant", "")
        base_url = get_api_base_url()
        console_url = f"{base_url}/console/{tenant_id}?tab=apps" if tenant_id else ""

        catalog = [
            {"app_id": "gmail", "name": "Gmail", "category": "communication", "type": "oauth"},
            {"app_id": "slack", "name": "Slack", "category": "communication", "type": "oauth"},
            {"app_id": "microsoft_teams", "name": "Microsoft Teams", "category": "communication", "type": "oauth"},
            {"app_id": "google_sheets", "name": "Google Sheets", "category": "data", "type": "oauth"},
            {"app_id": "google_docs", "name": "Google Docs", "category": "data", "type": "oauth"},
            {"app_id": "google_drive", "name": "Google Drive", "category": "data", "type": "oauth"},
            {"app_id": "airtable", "name": "Airtable", "category": "data", "type": "oauth"},
            {"app_id": "hubspot", "name": "HubSpot", "category": "crm", "type": "oauth"},
            {"app_id": "salesforce", "name": "Salesforce", "category": "crm", "type": "oauth"},
            {"app_id": "attio", "name": "Attio", "category": "crm", "type": "oauth"},
            {"app_id": "instantly", "name": "Instantly", "category": "outreach", "type": "api_key"},
            {"app_id": "linear", "name": "Linear", "category": "project", "type": "oauth"},
            {"app_id": "notion", "name": "Notion", "category": "project", "type": "oauth"},
            {"app_id": "clickup", "name": "ClickUp", "category": "project", "type": "oauth"},
            {"app_id": "asana", "name": "Asana", "category": "project", "type": "oauth"},
            {"app_id": "google_calendar", "name": "Google Calendar", "category": "calendar", "type": "oauth"},
            {"app_id": "calendly", "name": "Calendly", "category": "calendar", "type": "oauth"},
            {"app_id": "cal_com", "name": "Cal.com", "category": "calendar", "type": "oauth"},
            {"app_id": "zoom", "name": "Zoom", "category": "meetings", "type": "oauth"},
            {"app_id": "fireflies", "name": "Fireflies.ai", "category": "meetings", "type": "api_key"},
            {"app_id": "posthog", "name": "PostHog", "category": "analytics", "type": "api_key"},
        ]
        return {
            "apps": catalog,
            "total": len(catalog),
            "console_url": console_url,
            "message": (
                "These are all available apps. Use nrev_app_connect(app_id='...') to connect one, "
                f"or manage them in the console: {console_url}"
            ),
        }

    # Server returned the catalog — enrich with console URL
    creds = load_credentials()
    tenant_id = (creds or {}).get("user_info", {}).get("tenant", "")
    base_url = get_api_base_url()
    if tenant_id:
        result["console_url"] = f"{base_url}/console/{tenant_id}?tab=apps"
        result["message"] = (
            "Use nrev_app_connect(app_id='...') to connect an app, "
            f"or manage them in the console: {result['console_url']}"
        )
    return result


def _handle_nrev_open_console(args: dict[str, Any]) -> dict[str, Any]:
    """Open the nrev-lite console dashboard in the user's browser."""
    import webbrowser

    creds = load_credentials()
    if creds is None:
        return {
            "error": "Not authenticated. Run `nrev-lite auth login` in your terminal first.",
        }

    tenant_id = (creds or {}).get("user_info", {}).get("tenant", "")
    if not tenant_id:
        return {"error": "No tenant ID found in credentials. Run `nrev-lite auth login` again."}

    base_url = get_api_base_url()
    tab = args.get("tab", "")
    console_url = f"{base_url}/console/{tenant_id}"
    if tab:
        console_url += f"?tab={tab}"

    tab_descriptions = {
        "usage": "Credits & Billing",
        "keys": "API Key Management (BYOK)",
        "apps": "Connected Apps",
        "runs": "Workflow Run History",
        "datasets": "Persistent Datasets",
        "dashboards": "Shared Dashboards",
    }

    try:
        webbrowser.open(console_url)
        tab_label = tab_descriptions.get(tab, "Dashboard")
        return {
            "status": "opened",
            "url": console_url,
            "tab": tab or "main",
            "message": f"Opened {tab_label} in your browser: {console_url}",
        }
    except Exception as e:
        return {
            "status": "url_only",
            "url": console_url,
            "message": f"Could not open browser automatically. Open this URL: {console_url}",
        }


def _handle_nrev_health(args: dict[str, Any]) -> dict[str, Any]:
    """Quick health check — server reachable + auth valid."""
    creds = load_credentials()
    if creds is None:
        return {
            "status": "error",
            "error": "Not authenticated. Run `nrev-lite auth login` in your terminal.",
        }

    tenant_id = (creds or {}).get("user_info", {}).get("tenant", "")
    base_url = get_api_base_url()

    # Use /credits/balance — lightweight auth (no User DB record needed)
    result = _api_request("GET", "/credits/balance")
    if "error" in result:
        return {"status": "error", "error": result["error"]}

    console_url = f"{base_url}/console/{tenant_id}" if tenant_id else ""
    return {
        "status": "ok",
        "server": base_url,
        "balance": result.get("balance"),
        "console_url": console_url,
    }


def _handle_nrev_new_workflow(args: dict[str, Any]) -> dict[str, Any]:
    """Start a new workflow within the current session."""
    global WORKFLOW_ID, WORKFLOW_LABEL
    old_id = WORKFLOW_ID
    WORKFLOW_ID = str(uuid.uuid4())
    WORKFLOW_LABEL = args.get("label", "")
    logger.info(
        "New workflow started: %s (label=%s), previous: %s",
        WORKFLOW_ID, WORKFLOW_LABEL, old_id,
    )
    return {
        "workflow_id": WORKFLOW_ID,
        "label": WORKFLOW_LABEL or "(unlabeled)",
        "message": "New workflow started. All subsequent tool calls will be grouped under this workflow in the run logs.",
        "previous_workflow_id": old_id,
    }


def _handle_nrev_search_people(args: dict[str, Any]) -> dict[str, Any]:
    """Search for people across B2B databases."""
    # Determine provider based on filters
    has_previous_employer = bool(args.get("previous_employer"))
    provider = args.get("provider")

    if has_previous_employer and not provider:
        provider = "rocketreach"  # Only RocketReach has previous_employer filter
    elif not provider:
        provider = "apollo"  # Default to Apollo for standard B2B search

    params: dict[str, Any] = {}

    if provider == "apollo":
        # Build Apollo-compatible params
        if args.get("titles"):
            params["person_titles"] = args["titles"]
        if args.get("company_domains"):
            # Apollo quirk: q_organization_domains is a newline-separated string
            params["q_organization_domains"] = "\n".join(args["company_domains"])
        if args.get("company_names"):
            params["q_organization_name"] = args["company_names"][0] if len(args["company_names"]) == 1 else args["company_names"]
        if args.get("locations"):
            params["person_locations"] = args["locations"]
        if args.get("industries"):
            params["organization_industry_tag_ids"] = args["industries"]
        if args.get("seniority_levels"):
            params["person_seniority"] = args["seniority_levels"]
        if args.get("employee_ranges"):
            params["organization_num_employees_ranges"] = args["employee_ranges"]
        params["per_page"] = min(args.get("limit", 25), 100)

    elif provider == "rocketreach":
        # Build RocketReach-compatible params
        query: dict[str, Any] = {}
        if args.get("titles"):
            query["current_title"] = args["titles"]
        if args.get("company_domains"):
            query["company_domain"] = args["company_domains"]
        if args.get("company_names"):
            query["current_employer"] = args["company_names"]
        if args.get("previous_employer"):
            query["previous_employer"] = args["previous_employer"]
        if args.get("locations"):
            query["location"] = args["locations"]
        if args.get("industries"):
            query["company_industry"] = args["industries"]
        if args.get("seniority_levels"):
            query["management_levels"] = args["seniority_levels"]
        params["query"] = query
        params["page_size"] = min(args.get("limit", 25), 100)

    body: dict[str, Any] = {
        "operation": "search_people",
        "params": params,
        "provider": provider,
    }

    return _api_request("POST", "/execute", json_body=body)


def _handle_nrev_estimate_cost(arguments: dict) -> dict:
    """Estimate credit cost for one or many operations.

    Bulk mode (`operations` list) delegates to /execute/cost/bulk so the server's
    `calculate_cost` is the source of truth. Single mode keeps the local pricing
    table for backward compatibility.
    """
    CREDIT_TO_USD = 0.08

    # ── Bulk mode: delegate to server endpoint ────────────────────────────
    operations = arguments.get("operations")
    if isinstance(operations, list) and operations:
        payload = {
            "operations": [
                {
                    "operation": op.get("operation", ""),
                    "params": op.get("params") or {},
                }
                for op in operations
            ]
        }
        result = _api_request("POST", "/execute/cost/bulk", json_body=payload)
        if "error" in result:
            return result
        result["estimated_usd"] = round(
            result.get("total_estimated_credits", 0) * CREDIT_TO_USD, 2
        )
        for item in result.get("items", []):
            item["estimated_usd"] = round(
                item.get("estimated_credits", 0) * CREDIT_TO_USD, 2
            )
        return result

    # ── Single mode: existing local-dict pricing ──────────────────────────
    operation = arguments.get("operation", "")
    count = arguments.get("count", 1)

    # Real per-operation credit costs (platform key pricing)
    OP_COSTS = {
        "search_people": 2,       # Apollo/RocketReach people search
        "enrich_person": 1,       # Person enrichment
        "enrich_company": 1,      # Company enrichment
        "search_web": 1,          # Google web search
        "google_search": 1,       # Google SERP search
        "scrape_page": 1,         # Web page extraction
        "company_signals": 1,     # PredictLeads signals
        "find_email": 1,          # Hunter email finder
        "verify_email": 1,        # ZeroBounce verification
        "domain_search": 1,       # Hunter domain search
        "ai_research": 1,         # Perplexity/OpenAI research per query
        "batch_extract": 1,       # Parallel Web batch extraction per URL
    }

    OP_PROVIDERS = {
        "enrich_person": "apollo",
        "enrich_company": "apollo",
        "search_people": "apollo",
        "search_web": "rapidapi",
        "scrape_page": "parallel",
        "google_search": "rapidapi",
        "company_signals": "predictleads",
        "find_email": "hunter",
        "verify_email": "zerobounce",
        "domain_search": "hunter",
        "ai_research": "perplexity",
        "batch_extract": "parallel",
    }

    cost_per_op = OP_COSTS.get(operation, 1)
    estimated_credits = cost_per_op * count
    estimated_usd = round(estimated_credits * CREDIT_TO_USD, 2)
    provider = OP_PROVIDERS.get(operation, "unknown")

    # Check if user has BYOK for this provider
    byok = False
    try:
        keys_resp = _api_request("GET", "/keys")
        # _api_request returns a dict like {"keys": [...]}
        for key_info in keys_resp.get("keys", []):
            if key_info.get("provider") == provider and key_info.get("source") == "byok":
                byok = True
                break
    except Exception:
        pass

    return {
        "operation": operation,
        "count": count,
        "provider": provider,
        "byok": byok,
        "estimated_credits": 0 if byok else estimated_credits,
        "estimated_usd": 0.0 if byok else round(estimated_usd, 2),
        "note": (
            f"Free — using your own {provider} API key"
            if byok
            else f"{estimated_credits} credits (~${estimated_usd:.2f})"
        ),
    }


# Handler dispatch table

def _compute_column_metadata(rows: list) -> dict:
    """Compute per-column metadata from row dicts. Pure Python, no deps."""
    import re as _re
    if not rows:
        return {}
    _url_re = _re.compile(r"^https?://", _re.IGNORECASE)
    _email_re = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    skip = {"id", "_created_at", "_workflow_id", "_updated_at", "dedup_hash"}
    all_cols = set()
    for r in rows:
        if isinstance(r, dict):
            all_cols.update(r.keys())
    cols = sorted(c for c in all_cols if c not in skip)
    total = len(rows)
    result = {}
    for col in cols:
        values = [r.get(col) if isinstance(r, dict) else None for r in rows]
        non_null = [v for v in values if v is not None and v != "" and v != "null"]
        null_pct = round((total - len(non_null)) / total * 100, 1) if total else 0.0
        unique_count = len(set(str(v) for v in non_null))
        col_type = "string"
        if non_null:
            nums = sum(1 for v in non_null if isinstance(v, (int, float)))
            urls = sum(1 for v in non_null if isinstance(v, str) and _url_re.match(v))
            emails = sum(1 for v in non_null if isinstance(v, str) and _email_re.match(v))
            n = len(non_null)
            if urls > n * 0.5: col_type = "url"
            elif emails > n * 0.5: col_type = "email"
            elif nums > n * 0.5: col_type = "number"
        col_min = col_max = None
        if col_type == "number":
            numeric = []
            for v in non_null:
                try: numeric.append(float(v))
                except: pass
            if numeric: col_min, col_max = min(numeric), max(numeric)
        samples = []
        seen_s = set()
        for v in non_null[:10]:
            s = str(v)[:100]
            if s not in seen_s and len(samples) < 3:
                samples.append(s); seen_s.add(s)
        result[col] = {"type": col_type, "null_pct": null_pct, "unique_count": unique_count,
                        "min": col_min, "max": col_max, "sample_values": samples}
    return result


def _handle_nrev_get_run_log(arguments: dict) -> dict:
    """Fetch run log steps with truncated results and column metadata."""
    wf_id = arguments.get("workflow_id") or WORKFLOW_ID
    step_idx = arguments.get("step_index")
    include_meta = arguments.get("include_metadata", True)
    try:
        resp = _api_request("GET", f"/runs/{wf_id}")
    except Exception as e:
        return {"error": f"Could not fetch run log: {e}"}
    steps = resp.get("steps", [])
    if step_idx is not None:
        if 0 <= step_idx < len(steps):
            steps = [steps[step_idx]]
        else:
            return {"error": f"Step index {step_idx} out of range (0-{len(steps)-1})"}
    output_steps = []
    for s in steps:
        results = s.get("result_summary", {}).get("results", [])
        total_rows = len(results)
        truncated = results[:20]
        step_out = {
            "tool_name": s.get("tool_name"), "operation": s.get("operation"),
            "status": s.get("status"), "credits_charged": s.get("credits_charged"),
            "total_rows": total_rows, "rows_shown": len(truncated), "results": truncated,
        }
        if include_meta and truncated:
            step_out["column_metadata"] = _compute_column_metadata(truncated)
        output_steps.append(step_out)
    return {"workflow_id": wf_id, "step_count": len(output_steps), "steps": output_steps}


def _auto_generate_label(tool_name: str, args: dict) -> str:
    """Generate a short meaningful workflow label from the first tool call."""
    if tool_name == "nrev_search_people":
        parts = []
        if args.get("person_titles"): parts.append(str(args["person_titles"]))
        if args.get("organization_name"): parts.append(f"at {args['organization_name']}")
        return f"Search: {' '.join(parts)}"[:50] if parts else "People Search"
    if tool_name == "nrev_google_search":
        q = args.get("query", "")
        if not q and isinstance(args.get("queries"), list) and args["queries"]:
            q = args["queries"][0]
        return f"Google: {q}"[:50] if q else "Google Search"
    if tool_name == "nrev_enrich_person":
        ident = args.get("email") or f"{args.get('first_name', '')} {args.get('last_name', '')}".strip() or "person"
        return f"Enrich: {ident}"[:50]
    if tool_name == "nrev_enrich_company":
        return f"Company: {args.get('domain') or args.get('name', 'company')}"[:50]
    if tool_name == "nrev_create_dataset":
        return f"Dataset: {args.get('name', 'data')}"[:50]
    if tool_name == "nrev_scrape_page":
        url = args.get("url") or (args["urls"][0] if isinstance(args.get("urls"), list) and args["urls"] else "")
        return f"Scrape: {url}"[:50] if url else "Web Scrape"
    if tool_name == "nrev_app_execute":
        return f"{args.get('app_id', '')}: {args.get('action', '')}"[:50]
    return tool_name.replace("nrev_", "").replace("_", " ").title()[:50]



def _handle_nrev_deploy_site(arguments: dict) -> dict:
    """Deploy a static site to nrev-lite hosting."""
    name = arguments.get("name", "")
    files = arguments.get("files", {})
    dataset_ids = arguments.get("dataset_ids", [])
    entry_point = arguments.get("entry_point", "index.html")

    if not name:
        return {"error": "name is required"}
    if not files:
        return {"error": "files dict is required (at least one HTML file)"}
    if entry_point not in files:
        return {"error": f"entry_point '{entry_point}' not found in files dict"}
    if not dataset_ids:
        return {"error": "At least one dataset_id is required"}

    body = {
        "name": name,
        "files": files,
        "dataset_ids": dataset_ids,
        "entry_point": entry_point,
    }
    return _api_request("POST", "/sites", json_body=body)


def _handle_nrev_save_script(args: dict[str, Any]) -> dict[str, Any]:
    """Save a parameterized workflow script."""
    name = args.get("name", "")
    if not name:
        return {"error": "Parameter 'name' is required."}

    steps = args.get("steps", [])
    if not steps:
        return {"error": "Parameter 'steps' is required (at least one step)."}

    body: dict[str, Any] = {
        "name": name,
        "parameters": args.get("parameters", []),
        "steps": steps,
    }
    if args.get("description"):
        body["description"] = args["description"]
    if args.get("source_workflow_id"):
        body["source_workflow_id"] = args["source_workflow_id"]
    if args.get("tags"):
        body["tags"] = args["tags"]

    return _api_request("POST", "/scripts", json_body=body)


def _handle_nrev_list_scripts(args: dict[str, Any]) -> dict[str, Any]:
    """List all saved scripts."""
    return _api_request("GET", "/scripts")


def _handle_nrev_get_script(args: dict[str, Any]) -> dict[str, Any]:
    """Load a saved script by slug or ID."""
    script = args.get("script", "")
    if not script:
        return {"error": "Parameter 'script' is required."}
    return _api_request("GET", f"/scripts/{script}")


def _handle_nrev_log_learning(args: dict[str, Any]) -> dict[str, Any]:
    """Log a workflow discovery for admin review."""
    category = args.get("category", "")
    discovery = args.get("discovery")
    if not category:
        return {"error": "Parameter 'category' is required."}
    if not discovery:
        return {"error": "Parameter 'discovery' is required."}

    body: dict[str, Any] = {
        "category": category,
        "discovery": discovery,
        "confidence": args.get("confidence", 0.5),
    }
    if args.get("platform"):
        body["platform"] = args["platform"]
    if args.get("tool_name"):
        body["tool_name"] = args["tool_name"]
    if args.get("subcategory"):
        body["subcategory"] = args["subcategory"]
    if args.get("evidence"):
        body["evidence"] = args["evidence"]
    if args.get("user_prompt"):
        body["user_prompt"] = args["user_prompt"]
    # Attach current workflow ID for provenance
    body["source_workflow_id"] = WORKFLOW_ID

    return _api_request("POST", "/learning-logs", json_body=body)


def _handle_nrev_get_knowledge(args: dict[str, Any]) -> dict[str, Any]:
    """Look up approved knowledge by category and key."""
    category = args.get("category", "")
    key = args.get("key", "")
    if not category or not key:
        return {"error": "Both 'category' and 'key' are required."}
    return _api_request("GET", f"/learning-logs/knowledge/{category}/{key}")


TOOL_HANDLERS: dict[str, Any] = {
    "nrev_search_web": _handle_nrev_search_web,
    "nrev_scrape_page": _handle_nrev_scrape_page,
    "nrev_google_search": _handle_nrev_google_search,
    "nrev_enrich_person": _handle_nrev_enrich_person,
    "nrev_enrich_company": _handle_nrev_enrich_company,
    "nrev_fetch_linkedin_posts": _handle_nrev_fetch_linkedin_posts,
    "nrev_fetch_post_engagement": _handle_nrev_fetch_post_engagement,
    "nrev_query_table": _handle_nrev_query_table,
    "nrev_list_tables": _handle_nrev_list_tables,
    "nrev_create_dataset": _handle_nrev_create_dataset,
    "nrev_append_rows": _handle_nrev_append_rows,
    "nrev_query_dataset": _handle_nrev_query_dataset,
    "nrev_list_datasets": _handle_nrev_list_datasets,
    "nrev_create_and_populate_dataset": _handle_nrev_create_and_populate_dataset,
    "nrev_delete_dataset_rows": _handle_nrev_delete_dataset_rows,
    "nrev_update_dataset": _handle_nrev_update_dataset,
    "nrev_delete_dataset": _handle_nrev_delete_dataset,
    "nrev_credit_balance": _handle_nrev_credit_balance,
    "nrev_provider_status": _handle_nrev_provider_status,
    "nrev_search_patterns": _handle_nrev_search_patterns,
    "nrev_app_actions": _handle_nrev_app_actions,
    "nrev_app_action_schema": _handle_nrev_app_action_schema,
    "nrev_app_execute": _handle_nrev_app_execute,
    "nrev_app_list": _handle_nrev_app_list,
    "nrev_app_connect": _handle_nrev_app_connect,
    "nrev_app_catalog": _handle_nrev_app_catalog,
    "nrev_open_console": _handle_nrev_open_console,
    "nrev_health": _handle_nrev_health,
    "nrev_new_workflow": _handle_nrev_new_workflow,
    "nrev_search_people": _handle_nrev_search_people,
    "nrev_estimate_cost": _handle_nrev_estimate_cost,
    "nrev_get_run_log": _handle_nrev_get_run_log,
    "nrev_deploy_site": _handle_nrev_deploy_site,
    "nrev_save_script": _handle_nrev_save_script,
    "nrev_list_scripts": _handle_nrev_list_scripts,
    "nrev_get_script": _handle_nrev_get_script,
    "nrev_log_learning": _handle_nrev_log_learning,
    "nrev_get_knowledge": _handle_nrev_get_knowledge,
}


# ---------------------------------------------------------------------------
# MCP JSON-RPC server
# ---------------------------------------------------------------------------


def _make_response(req_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _make_tool_result(req_id: Any, text: str, is_error: bool = False) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "content": [{"type": "text", "text": text}],
            **({"isError": True} if is_error else {}),
        },
    }


def handle_jsonrpc_request(request: dict) -> dict | None:
    """Handle a single JSON-RPC request and return the response (or None for notifications)."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    logger.info("Received method=%s id=%s", method, req_id)

    # --- initialize ---
    if method == "initialize":
        return _make_response(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    # --- notifications/initialized ---
    if method == "notifications/initialized":
        return None  # notification, no response

    # --- tools/list ---
    if method == "tools/list":
        return _make_response(req_id, {"tools": TOOLS})

    # --- tools/call ---
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return _make_error(req_id, -32601, f"Unknown tool: {tool_name}")

        # Check auth before executing
        creds = load_credentials()
        if creds is None:
            return _make_tool_result(
                req_id,
                json.dumps({
                    "error": "Not authenticated. Run `nrev-lite auth login` in your terminal first."
                }, indent=2),
                is_error=True,
            )

        try:
            global _current_tool_name, WORKFLOW_LABEL
            _current_tool_name = tool_name
            result = handler(tool_args)

            # Auto-name workflow from first meaningful tool call
            if not WORKFLOW_LABEL and tool_name not in (
                "nrev_health", "nrev_provider_status", "nrev_credit_balance",
                "nrev_new_workflow", "nrev_estimate_cost", "nrev_get_run_log",
                "nrev_list_tables", "nrev_list_datasets", "nrev_app_list", "nrev_app_connect",
                "nrev_app_catalog", "nrev_open_console",
                "nrev_save_script", "nrev_list_scripts", "nrev_get_script",
                "nrev_log_learning", "nrev_get_knowledge",
            ):
                WORKFLOW_LABEL = _auto_generate_label(tool_name, tool_args)

            _current_tool_name = ""
            text = json.dumps(result, indent=2, default=str)

            # Check if the result itself contains an error
            is_err = isinstance(result, dict) and "error" in result
            return _make_tool_result(req_id, text, is_error=is_err)

        except Exception as exc:
            logger.exception("Tool %s failed", tool_name)
            return _make_tool_result(
                req_id,
                json.dumps({"error": str(exc)}, indent=2),
                is_error=True,
            )

    # --- ping ---
    if method == "ping":
        return _make_response(req_id, {})

    # --- unknown method ---
    if req_id is not None:
        return _make_error(req_id, -32601, f"Unknown method: {method}")

    # Unknown notification — ignore
    return None


def run_stdio() -> None:
    """Run the MCP server reading JSON-RPC messages from stdin, writing to stdout."""
    logger.info("nrev-lite MCP server starting on stdio")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            error_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            sys.stdout.write(json.dumps(error_resp) + "\n")
            sys.stdout.flush()
            continue

        response = handle_jsonrpc_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

    logger.info("nrev-lite MCP server shutting down (stdin closed)")


# ---------------------------------------------------------------------------
# Try to use official MCP SDK if available, otherwise use raw JSON-RPC
# ---------------------------------------------------------------------------


def _try_mcp_sdk() -> bool:
    """Attempt to run using the official mcp Python SDK. Returns True if successful."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        import mcp.types as types
        import asyncio
    except ImportError:
        return False

    logger.info("Using official mcp SDK")

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in TOOLS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

        creds = load_credentials()
        if creds is None:
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": "Not authenticated. Run `nrev-lite auth login` first."}),
            )]

        try:
            global _current_tool_name
            _current_tool_name = name
            result = handler(arguments)
            _current_tool_name = ""
            return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
        except Exception as exc:
            _current_tool_name = ""
            logger.exception("Tool %s failed", name)
            return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Main entry point for the nrev-lite MCP server."""
    # Try official SDK first, fall back to raw JSON-RPC
    if not _try_mcp_sdk():
        logger.info("mcp SDK not available, using raw JSON-RPC over stdio")
        run_stdio()


if __name__ == "__main__":
    main()
