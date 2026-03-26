# Enrichment Integration Research: Reusing Execution Module for Consultant Agent

## Status: Draft v2 — Decisions Incorporated

---

## 1. The Problem

The execution module (`server/execution/`) powers all data enrichment, search, and scraping operations for the CLI. The consultant agent (running inside app.nrev.ai) needs the same capabilities — same providers, same normalization, same caching. We want one execution engine, not two.

---

## 2. Current State: How Execution Works Today

### API Surface

| Endpoint | Method | What It Does |
|----------|--------|-------------|
| `/api/v1/execute` | POST | Single operation (enrich, search, scrape) |
| `/api/v1/execute/batch` | POST | Batch enrichment (max 25 items) |
| `/api/v1/execute/batch/{batch_id}` | GET | Poll batch status |
| `/api/v1/execute/cost` | POST | Estimate credit cost before execution |
| `/api/v1/search/patterns` | GET | Platform-specific Google search query patterns |

### Request/Response Schema

**Single execute:**
```json
// Request
{
    "operation": "enrich_person",       // enrich_person, enrich_company, search_people, search_companies, scrape_page, etc.
    "provider": "apollo",              // optional — auto-selected from DEFAULT_PROVIDERS if null
    "params": { "email": "john@acme.com" }
}

// Response
{
    "execution_id": "exec_a1b2c3d4",
    "status": "success",
    "credits_charged": 1.0,
    "result": { /* normalized enrichment data */ }
}
```

**Batch execute:**
```json
// Request
{
    "operations": [
        { "operation": "enrich_person", "provider": null, "params": { "email": "a@co.com" } },
        { "operation": "enrich_person", "provider": null, "params": { "email": "b@co.com" } }
    ]
}

// Response
{
    "batch_id": "batch_xyz",
    "total": 2,
    "status": "completed"
}
```

### Execution Pipeline (per call)

```
1. Resolve Provider
   └─ Use specified or lookup DEFAULT_PROVIDERS[operation]

2. Check Cache (Redis)
   └─ SHA256(tenant_id, operation, params) → hit = return immediately, no credits

3. Rate Limit (Redis token-bucket)
   └─ Per provider + per tenant: 10 burst, 2/sec refill (120/min steady)

4. Resolve API Key
   └─ BYOK first (encrypted via KMS) → platform key fallback
   └─ BYOK = free, platform key = credits charged

5. Execute with Retry
   └─ Exponential backoff: 1s base, 30s max, 3 retries, jitter

6. Normalize
   └─ Provider-specific → nrev-lite standard schema

7. Cache Store
   └─ Redis with operation-specific TTL (30min to 7 days)

8. Return
```

### Supported Operations & Default Providers

| Operation | Default Provider | Cache TTL |
|-----------|-----------------|-----------|
| `enrich_person` | apollo | 7 days |
| `enrich_company` | apollo | 7 days |
| `search_people` | apollo | 1 hour |
| `search_companies` | apollo | 1 hour |
| `bulk_enrich_people` | apollo | 7 days |
| `bulk_enrich_companies` | apollo | 7 days |
| `company_jobs` | predictleads | 1 day |
| `company_technologies` | predictleads | 3 days |
| `company_news` | predictleads | 1 hour |
| `company_financing` | predictleads | 1 day |
| `similar_companies` | predictleads | 7 days |
| `scrape_page` | parallel_web | 1 hour |
| `crawl_site` | parallel_web | 1 hour |
| `extract_structured` | parallel_web | 1 hour |
| `batch_extract` | parallel_web | 1 hour |
| `search_web` | rapidapi_google | 30 min |

### Credit Cost Model

| Operation Type | Cost |
|---------------|------|
| Single enrichment | 1 credit flat |
| Search (per page) | ceil(per_page / 25) credits |
| Bulk enrichment | 1 credit per record |
| Bulk queries | 1 credit per query |
| Cache hit | Free (always) |
| BYOK call | Free (always) |

### Auth Dependency Chain (Current)

```
Bearer JWT
  → get_current_user()      → decodes JWT, looks up User in local DB
    → get_current_tenant()   → looks up Tenant via user.tenant_id, sets RLS
      → require_credits()    → checks CreditBalance for tenant
```

Key issue: `get_current_user()` does a DB lookup against the local `users` table. After auth migration (see docs/auth_migration_research.md), this table will be dropped.

---

## 3. Consultant Agent Architecture

### How It Calls Execution APIs

```
User interacts with consultant agent on app.nrev.ai
          │
          ▼
Main App Frontend (Next.js)
          │
          ▼
Main App Backend (orchestrator, same VPC)
          │
          ▼  HTTP call: POST /api/v1/execute
          │  Headers: Authorization: Bearer {gtm-engine-jwt}
          │           X-Workflow-Id: {consultant-session-id}
          │           X-Tool-Name: {operation-label}
          ▼
gtm-engine server (FastAPI, same VPC)
          │
          ▼
Execution pipeline (unchanged)
```

**The consultant agent's orchestrator runs in the main app backend, same VPC as gtm-engine.** This means:
- No CORS needed — backend-to-backend HTTP call
- Low latency — single-digit milliseconds network overhead
- The orchestrator authenticates the user via Supabase, then calls gtm-engine with a gtm-engine JWT

### How the Orchestrator Gets a gtm-engine JWT

Follows the token exchange flow from `docs/auth_migration_research.md`:

1. User is authenticated in main app via Supabase
2. Orchestrator calls `POST /api/v1/auth/exchange` with Supabase JWT + tenant info
3. Gets back a gtm-engine JWT with `tenant_id` claim
4. Uses this JWT for all subsequent execution API calls within the session
5. Caches the gtm-engine JWT for the session duration (24h expiry)
6. On expiry, uses refresh token to get a new access token

**No new auth pathway needed.** The exchange endpoint (planned in auth migration) serves both CLI and consultant agent.

---

## 4. What Changes for Consultant Agent Integration

### 4.1 Auth Dependency — Post-Migration Adjustment

After auth migration drops the local `users` and `tenants` tables, `get_current_user()` and `get_current_tenant()` need to change.

**Current chain** (depends on local DB):
```python
get_current_user()   → SELECT * FROM users WHERE id = jwt.sub
get_current_tenant() → SELECT * FROM tenants WHERE id = user.tenant_id
```

**Post-migration chain** (JWT-only, no DB lookup for user/tenant identity):
```python
get_current_tenant_from_jwt()  → decode JWT, extract tenant_id, set RLS
                                → no User/Tenant table lookup needed
                                → tenant_id comes directly from JWT claims
```

The exchange endpoint already puts `tenant_id` in the JWT claims. The execution module only needs `tenant_id` for:
1. Setting RLS context (`SET LOCAL app.current_tenant`)
2. Resolving BYOK keys (`TenantKey.tenant_id`)
3. Credit operations (`CreditBalance.tenant_id`)

None of these require a `User` or `Tenant` ORM object — they just need the tenant ID string.

**Action item:** After auth migration, simplify `get_current_tenant` to extract `tenant_id` directly from JWT claims without DB lookup. The current `Tenant` ORM return type will need to become a lightweight dataclass or the dependency will just return the tenant_id string.

### 4.2 Credit Management — Dual System During Transition

**Decision: The main app's credit management microservice is the single source of truth for credit provisioning. gtm-engine's credit tables handle consumption tracking.**

How it works:

```
Main App Credit Service (source of truth)
  │
  ├─ Provisions credits to tenants (purchase, plans, trials)
  │  └─ Calls gtm-engine: POST /api/v1/credits/add (or direct DB)
  │
  ├─ Reads consumption data from gtm-engine
  │  └─ Calls gtm-engine: GET /api/v1/credits/balance
  │  └─ Calls gtm-engine: GET /api/v1/credits/history
  │
  └─ Presents unified credit view in main app UI
     └─ Balance, spend, transaction history — single window
```

**What stays in gtm-engine:**
- `CreditBalance` table — tracks current balance per tenant
- `CreditLedger` table — tracks hold/debit/release per operation
- `check_and_hold()`, `confirm_debit()`, `release_hold()` — atomic billing during execution
- `require_credits()` dependency — pre-flight check before execution

**What moves to the main app credit service:**
- Credit provisioning (signup bonuses, top-ups, plan allocations)
- Payment processing (Stripe integration, plan management)
- Unified credit dashboard across CLI and consultant

**Why keep consumption tracking in gtm-engine:**
The hold/debit/release cycle is tightly coupled with the execution pipeline. It must be atomic with the provider call — hold before, confirm/release after. Moving this across a network boundary would introduce distributed transaction complexity for no benefit.

**Interface between the two systems:**
- Main app provisions → calls `add_credits()` in gtm-engine (same VPC, direct API call)
- Main app reads consumption → calls balance/history endpoints in gtm-engine
- gtm-engine never provisions credits on its own (remove signup bonus logic per auth migration plan)

### 4.3 Workflow Tracking — X-Workflow-Id for Consultant Sessions

The execution router already reads `X-Workflow-Id` and `X-Tool-Name` headers for run logging via `RunStepMiddleware`. The consultant agent orchestrator should:

1. Generate a `workflow_id` per consultant session (UUID4)
2. Pass it as `X-Workflow-Id` on every execution call
3. Optionally pass `X-Tool-Name` (e.g., `consultant:enrich_person`)
4. Optionally pass `X-Workflow-Label` (e.g., user's query or session label)

This enables:
- The Runs tab in the dashboard to show consultant agent sessions alongside CLI workflows
- Per-session cost tracking
- Debugging and audit trails

**No changes needed to gtm-engine.** Headers are already handled.

### 4.4 BYOK Keys — Shared Across CLI and Consultant

BYOK keys are stored per tenant in the `tenant_keys` table. Since CLI and consultant agent share the same `tenant_id`, they automatically share BYOK keys.

- If a tenant adds an Apollo key via CLI (`nrev-lite keys add apollo`), the consultant agent uses it too
- If a tenant adds a key via the main app dashboard (future), the CLI uses it too
- BYOK calls are free in both modes — the `is_byok` flag in the execution pipeline handles this

**No changes needed.** BYOK works per-tenant, not per-client.

### 4.5 Rate Limiting — Per Tenant, Not Per Client

The rate limiter uses `provider_name + tenant_id` as the bucket key. CLI and consultant agent calls from the same tenant share the same rate limit bucket.

This is the correct behavior — upstream providers rate limit by API key, not by client. If a tenant is using BYOK, their key's rate limit is shared across all their clients.

**Consideration:** If a tenant uses both CLI and consultant agent heavily, they might hit rate limits more often. The current limits (10 burst, 120/min steady) are conservative. Monitor and adjust if needed.

### 4.6 Caching — Shared Across CLI and Consultant

Cache keys are `SHA256(tenant_id, operation, params)`. Same tenant, same query = same cache entry regardless of whether the call came from CLI or consultant.

Benefits:
- Consultant agent benefits from CLI cache hits and vice versa
- No duplicate API spend across channels
- Cache is tenant-isolated (different tenants never share cache)

**No changes needed.**

### 4.7 Composio Connectors — Excluded from Consultant Agent

**Decision: Composio actions are NOT exposed to the consultant agent. Strict no-go.**

**Full analysis: See `docs/integration_research/composio_isolation_research.md` for the detailed API boundary audit.**

Key findings:

1. **All 7 Composio endpoints** (connection CRUD + action discovery + action execution) live in `server/console/router.py` under `/api/v1/connections/**`
2. **Action execution through connections** (sending Gmail, posting to Slack, writing to Sheets) uses `POST /api/v1/connections/execute` — this is in the console router, NOT the execution module
3. **The execution module has zero Composio imports or dependencies.** The only cross-reference is `run_logger.py` which maps connection URLs to tool names for logging — purely observational
4. **Blocking `/api/v1/connections/**` blocks everything Composio** — both connection management and action execution

**For the consultant agent:** The orchestrator simply never calls `/api/v1/connections/*` endpoints. No code changes needed to gtm-engine. Orchestrator whitelist is sufficient for V1; JWT-based enforcement can be added in V2.

### 4.8 Batch Size Limits

Current limit: 25 records per batch. This was set for rolling deploy safety (25 records ≈ 10-15s).

For consultant agent:
- Same limit applies — the constraint is upstream provider rate limits and execution time, not the client
- For larger operations (>100 records), the main app should recommend the full nRev platform per existing enrichment rules

**No changes needed.**

---

## 5. Things That Just Work (Zero Changes)

| Component | Why It Works |
|-----------|-------------|
| Execution pipeline (service.py) | Operates on `tenant_id` string — doesn't care where it came from |
| Provider resolution | Stateless — picks provider by operation name |
| Response normalization | Stateless — maps provider schema to nrev standard |
| Redis cache | Keyed by `tenant_id + operation + params` |
| Redis rate limiter | Keyed by `provider + tenant_id` |
| BYOK key resolution | Queries `TenantKey` by `tenant_id` |
| Run step logging | Reads headers (`X-Workflow-Id`, `X-Tool-Name`) |
| Search patterns | Stateless reference data, no per-tenant state |
| Retry with backoff | Per-request, stateless |

---

## 6. What Needs to Change (Summary)

| # | Change | Where | Blocked By |
|---|--------|-------|-----------|
| 1 | Simplify `get_current_tenant` to JWT-only (no User/Tenant table lookup) | `server/auth/dependencies.py` | Auth migration (exchange endpoint + FK drops) |
| 2 | Credit provisioning API — allow main app to add credits to gtm-engine tenants | `server/billing/router.py` | Credit service design in main app |
| 3 | Credit read APIs — allow main app to query balance and history | `server/billing/router.py` | Already exists (`GET /api/v1/credits/balance`, `GET /api/v1/credits/history`) — may need auth adjustment for service-to-service calls |
| 4 | Remove signup credit logic from gtm-engine auth | `server/auth/service.py` | Auth migration |

---

## 7. Implementation Phases

### Phase 1: Auth Migration (prerequisite — see docs/auth_migration_research.md)
- Implement `POST /api/v1/auth/exchange`
- Drop FK constraints, simplify tenant resolution
- This unblocks both CLI migration and consultant agent

### Phase 2: Credit Bridge
- Main app credit service calls `add_credits()` in gtm-engine to provision
- Main app reads balance/history from gtm-engine APIs
- Remove signup credit logic from gtm-engine
- Present unified credit view in main app UI

### Phase 3: Consultant Agent Orchestrator
- Build orchestrator in main app backend
- On session start: exchange Supabase JWT for gtm-engine JWT (cache for session)
- On each tool call: `POST /api/v1/execute` with JWT + workflow headers
- Handle results: pass normalized data back to the consultant agent LLM

### Phase 4: Monitoring & Tuning
- Monitor rate limit hits across CLI + consultant for same tenant
- Adjust rate limits if dual-client usage causes issues
- Add consultant-specific analytics (operation mix, latency, credit consumption by channel)

---

## 8. Decisions Taken

| # | Topic | Decision |
|---|-------|---------|
| 1 | Execution API surface | No changes. Consultant agent calls the same endpoints as CLI. |
| 2 | Auth for consultant | Uses gtm-engine JWT obtained via exchange endpoint (same as CLI post-migration). |
| 3 | Network path | Backend-to-backend within VPC. No CORS needed. |
| 4 | Credit provisioning | Main app credit service is source of truth. Provisions to gtm-engine via API. |
| 5 | Credit consumption | Stays in gtm-engine. Hold/debit/release is atomic with execution. |
| 6 | BYOK keys | Shared per tenant across CLI and consultant. No changes. |
| 7 | Caching | Shared per tenant. CLI and consultant benefit from each other's cache. |
| 8 | Rate limiting | Shared per tenant. Monitor for dual-client pressure. |
| 9 | Workflow tracking | Consultant passes X-Workflow-Id per session. Existing middleware handles it. |
| 10 | Batch limits | Same 25-record cap. Larger operations go through full nRev platform. |
| 11 | Operation subset | All operations available to consultant (including scrape/crawl). No restrictions. |
| 12 | Composio connectors | Strict no-go for consultant agent. Main app has its own integrations. Already cleanly separated — Composio lives in console router, not execution module. |
| 13 | LLM inference cost | Handled by main app's credit management system. Not gtm-engine's concern. |

---

## 9. Resolved Questions

| # | Question | Resolution |
|---|----------|-----------|
| 1 | Operation subset for consultant | All operations exposed, including scrape/crawl. These give the system its power. |
| 2 | Composio for consultant | Strict no-go. Main app has its own integrations. Composio is already cleanly separated in console router. |
| 3 | LLM inference cost | Main app's credit system handles this independently. Not gtm-engine's scope. |

## 10. Remaining Open Questions

1. **Credit provisioning API auth:** ✅ **Resolved.** Static Bearer token via `GTM_ENGINE_SERVICE_TOKEN` env var, calling `/private/api/v1/credits/add`. This is a service-scoped call (no user context), so it uses the static token pattern — consistent with workflow_studio → user management service. Full design in `docs/integration_research/auth_migration_research.md` §17, Scenario B.

2. **Error UX:** When the execution API returns a 429 (rate limit) or 402 (insufficient credits), how does the consultant agent present this to the user? The orchestrator needs to translate these HTTP errors into user-friendly messages within the chat interface.
