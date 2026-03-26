# Composio Isolation Research: API Boundary Analysis for Consultant Agent

## Status: Complete — No Issues Found

---

## 1. The Question

The consultant agent must use execution/enrichment APIs but must **never** interact with Composio connections. Is the API boundary clean enough that blocking `/api/v1/connections/**` is sufficient? Are there any hidden Composio dependencies in the execution path?

---

## 2. Findings: Complete API Surface Map

### Composio Endpoints (All in `server/console/router.py`)

| Endpoint | Method | Purpose | Composio API Version |
|----------|--------|---------|---------------------|
| `/api/v1/connections/initiate` | POST | Start OAuth flow | v3 |
| `/api/v1/connections/callback` | GET | OAuth callback | v3 |
| `/api/v1/connections` | GET | List active connections | v3 |
| `/api/v1/connections/{id}` | DELETE | Remove connection | v3 |
| `/api/v1/connections/actions` | GET | List actions for an app | v2 |
| `/api/v1/connections/actions/{name}/schema` | GET | Get action parameter schema | v2 |
| `/api/v1/connections/execute` | POST | **Execute action on connected app** | v2 |

### Execution Endpoints (All in `server/execution/router.py`)

| Endpoint | Method | Purpose | Composio involved? |
|----------|--------|---------|---------------------|
| `/api/v1/execute` | POST | Single enrichment/search/scrape | No |
| `/api/v1/execute/batch` | POST | Batch enrichment | No |
| `/api/v1/execute/batch/{id}` | GET | Poll batch status | No |
| `/api/v1/execute/cost` | POST | Cost estimation | No |
| `/api/v1/search/patterns` | GET | Google search query patterns | No |

---

## 3. Key Finding: Action Execution Is NOT in the Execution Module

This was the critical question. When a user does something like "send this data to Google Sheets" or "post a message to Slack", the call goes through:

```
POST /api/v1/connections/execute   ← console/router.py (Composio)
```

**NOT through:**

```
POST /api/v1/execute               ← execution/router.py (enrichment/search)
```

Despite similar naming, these are completely separate:

| Action | Endpoint | Router File | Code Path |
|--------|----------|-------------|-----------|
| Enrich a person | `POST /api/v1/execute` | `execution/router.py` | Provider SDK (Apollo, RocketReach, etc.) |
| Search people | `POST /api/v1/execute` | `execution/router.py` | Provider SDK |
| Scrape a page | `POST /api/v1/execute` | `execution/router.py` | Provider SDK (Parallel Web) |
| Send a Gmail | `POST /api/v1/connections/execute` | `console/router.py` | Composio v2 API |
| Post to Slack | `POST /api/v1/connections/execute` | `console/router.py` | Composio v2 API |
| Write to Sheets | `POST /api/v1/connections/execute` | `console/router.py` | Composio v2 API |

---

## 4. Cross-Reference Audit

### Does `server/execution/` import or call Composio?

**No.** Zero Composio imports or references in any execution module file.

The only mention of "connections" in the execution folder is in `server/execution/run_logger.py`, which is a **logging middleware** that maps URL paths to human-readable tool names for the workflow run history:

```python
# run_logger.py — purely observational, maps URLs to display names
_PATH_TO_TOOL = {
    "/api/v1/execute": "nrev_execute",
    "/api/v1/connections/execute": "nrev_execute_action",  # for logging only
    "/api/v1/connections/actions": "nrev_list_actions",     # for logging only
    "/api/v1/connections": "nrev_list_connections",         # for logging only
}
```

This middleware intercepts HTTP requests/responses to log them as run steps. It does not call Composio — it just recognizes Composio-related URLs when they pass through and labels them appropriately in the run log.

### Does `server/console/router.py` call execution?

**No.** The console router handles Composio directly via HTTP calls to `backend.composio.dev`. It does not route through the execution module's provider/retry/cache pipeline.

---

## 5. Consultant Agent: What to Block

**Simple rule: Block the `/api/v1/connections/**` prefix.**

This blocks:
- Connection management (create, list, delete OAuth connections)
- Action discovery (list actions, get schemas)
- Action execution (send Gmail, post Slack, write Sheets, etc.)

**No other blocking needed.** Enrichment, search, scrape, datasets, runs, credits — all safe.

### Enforcement Options

| Approach | Effort | Safety |
|----------|--------|--------|
| **Orchestrator whitelist (recommended for V1)** — orchestrator only calls known-safe endpoints | Zero changes to gtm-engine | Application-layer control |
| **JWT claim + middleware** — add `client_type: "consultant"` claim, middleware rejects `/connections/*` for consultant JWTs | Small change to auth + new middleware | Belt-and-suspenders |
| **Separate API gateway routes** — expose only safe prefixes to consultant's service mesh route | Infra-level | Strongest isolation |

**Recommendation:** Orchestrator whitelist for V1. The orchestrator is a controlled codebase — it calls specific endpoints, not arbitrary ones. Add JWT-based enforcement in V2 if the attack surface grows.

---

## 6. MCP Tool Mapping

For context, the CLI's MCP tools map to endpoints as follows:

| MCP Tool | Endpoint | Safe for Consultant? |
|----------|----------|---------------------|
| `nrev_execute` | `POST /api/v1/execute` | Yes |
| `nrev_estimate_cost` | `POST /api/v1/execute/cost` | Yes |
| `nrev_search_patterns` | `GET /api/v1/search/patterns` | Yes |
| `nrev_query_table` | `GET /api/v1/tables` | Yes |
| `nrev_create_dataset` | `POST /api/v1/datasets` | Yes |
| `nrev_append_rows` | `POST /api/v1/datasets/*/rows` | Yes |
| `nrev_query_dataset` | `GET /api/v1/datasets/*/rows` | Yes |
| `nrev_credit_balance` | `GET /api/v1/credits/balance` | Yes |
| `nrev_list_connections` | `GET /api/v1/connections` | **No — Composio** |
| `nrev_list_actions` | `GET /api/v1/connections/actions` | **No — Composio** |
| `nrev_get_action_schema` | `GET /api/v1/connections/actions/*/schema` | **No — Composio** |
| `nrev_execute_action` | `POST /api/v1/connections/execute` | **No — Composio** |

---

## 7. Conclusion

The API boundary between execution/enrichment and Composio is **completely clean**:

- Different router files (`execution/router.py` vs `console/router.py`)
- Different URL prefixes (`/api/v1/execute` vs `/api/v1/connections`)
- Zero shared code paths or imports
- The only cross-reference is the run logger, which is read-only observation

**No changes needed to gtm-engine for Composio isolation.** The consultant agent orchestrator simply never calls `/api/v1/connections/**`.
