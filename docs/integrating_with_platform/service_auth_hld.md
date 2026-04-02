# HLD: Service-to-Service Authentication for GTM Engine

**Date:** April 1, 2026
**Status:** Draft
**Scope:** Enable authenticated calls from workflow_studio (consultant agent) to gtm-engine execution endpoints
**Parent:** [auth_migration_research.md](./auth_migration_research.md), [CONSULTANT_AGENT_GTM_ENGINE_CONTRACT.md](workflow_studio contract)

---

## 1. Problem Statement

The consultant agent in workflow_studio needs to call gtm-engine's execution endpoints (`POST /api/v1/execute`, `POST /api/v1/execute/batch`, `GET /api/v1/credits`). Today, these endpoints require a gtm-engine JWT (issued via Google OAuth or Supabase exchange). The consultant agent has neither — it's a backend service acting on behalf of a user within a tenant.

The contract doc (Section 2.1) defines a phased auth evolution:

| Phase | Mechanism |
|-------|-----------|
| Dev (Phase 1-2) | No auth — GTM Engine accepts unauthenticated calls |
| **Before release** | **Service token — `Authorization: Bearer {GTM_ENGINE_SERVICE_TOKEN}`** |
| V2 | JWT exchange (full auth unification) |

**This HLD covers the "before release" phase — adding service token authentication as an alternative auth path to gtm-engine, so the consultant agent's calls become authenticated and start succeeding in staging/production.**

---

## 2. What Already Exists

### gtm-engine (this repo)

| Component | State |
|-----------|-------|
| `get_tenant_from_token()` | Decodes a gtm-engine JWT, extracts `tenant_id`, sets RLS. Used by `/execute`, `/execute/batch`, `/credits`. |
| `POST /api/v1/auth/exchange` | Exchanges Supabase JWT for gtm-engine JWT. **Already implemented.** |
| `SUPABASE_JWT_SECRET` config | Present. Used by exchange endpoint. |
| `PLATFORM_CREDIT_SERVICE_TOKEN` | Static token pattern exists for outbound calls (gtm-engine → platform credit service). |
| `/private/` endpoints | **Do not exist yet.** No service-scoped internal routes. |
| `X-Tenant-Id` header reading | **Not implemented.** Tenant always comes from JWT claims. |

### workflow_studio

| Component | State |
|-----------|-------|
| `UserManagementWS` client | Existing service-to-service client. Sync `httpx.Client`, SingletonBorg, static Bearer token via `USER_MANAGEMENT_WS_AUTH_TOKEN` env var. Calls `/private/tenant/*` endpoints. |
| GTM Engine client | **Does not exist.** Needs to be created at `infrastructure/gtm_engine_client.py`. |
| `GTM_ENGINE_SERVICE_TOKEN` env var | Referenced in contract doc. Not yet used. |

---

## 3. Architectural Decision: Service Token (Not JWT Exchange)

The consultant agent acts **on behalf of a user within a tenant**. This is the "Scenario A: User-Scoped Calls" from auth_migration_research.md §17. That document recommends JWT exchange for user-scoped calls.

**However, for V1 before release, we use a simpler approach — service token + `X-Tenant-Id` header:**

| Factor | JWT Exchange | Service Token + Header |
|--------|-------------|----------------------|
| Complexity | Requires Supabase JWT propagation from frontend → orchestrator → gtm-engine | Static token, set once at deployment |
| Dependencies | Needs Supabase JWT available in the consultant agent's context | Needs one env var per service |
| Trust model | Cryptographic (Supabase JWT proves user identity) | Network boundary (same VPC) + shared secret |
| Tenant context | From JWT claims | From `X-Tenant-Id` request header |
| Implementation effort | Moderate — need to propagate Supabase JWT through session context | Small — ~50 lines middleware on gtm-engine side |
| Matches existing pattern | New pattern for gtm-engine | Matches `workflow_studio → user_management` pattern exactly |

**Decision: Service token for V1.** The JWT exchange endpoint already exists and will be used in V2 (full CLI auth migration). For now, the service token gets us authenticated calls with minimal effort, matching the established platform pattern.

**Trust justification:** Same VPC, security groups restrict access to known service IPs. The service token proves "this is a trusted internal service." The `X-Tenant-Id` header provides tenant context for RLS. This is the same trust model as workflow_studio calling user_management's `/private/` endpoints today.

---

## 4. Data Flow

### 4.1 Consultant Agent → GTM Engine (Service Token Auth)

```mermaid
sequenceDiagram
    participant User
    participant CA as Consultant Agent<br/>(workflow_studio)
    participant GE as GTM Engine

    User->>CA: "Find companies hiring VP Sales"
    Note over CA: Has tenant_id from session context

    CA->>GE: POST /api/v1/execute<br/>Authorization: Bearer {GTM_ENGINE_SERVICE_TOKEN}<br/>X-Tenant-Id: 137<br/>X-Agent-Type: consultant<br/>X-Thread-Id: {thread_id}

    Note over GE: Service token auth middleware:<br/>1. Validate Bearer token matches config<br/>2. Read X-Tenant-Id header<br/>3. SET LOCAL app.current_tenant = '137'<br/>4. Proceed to execution

    GE-->>CA: 200 OK {execution_id, result, credits_charged}
    CA-->>User: "Found 13 companies..."
```

### 4.2 Auth Resolution Order (gtm-engine side)

The modified `get_tenant_from_token` dependency needs to support **two auth paths** on the same endpoints:

```
Incoming request to /api/v1/execute
    │
    ├─ Authorization header present?
    │   │
    │   ├─ Bearer token == GTM_ENGINE_SERVICE_TOKEN? ──► Service token path
    │   │   ├─ Read X-Tenant-Id header (required, 400 if missing)
    │   │   ├─ Set RLS context
    │   │   └─ Return TenantRef(id=tenant_id)
    │   │
    │   └─ Otherwise ──► JWT path (existing)
    │       ├─ Decode JWT with JWT_SECRET_KEY
    │       ├─ Extract tenant_id from claims
    │       ├─ Set RLS context
    │       └─ Return TenantRef(id=tenant_id)
    │
    └─ No Authorization header ──► 401 Unauthorized
```

**Both paths produce the same output: a `TenantRef` with RLS context set.** Downstream code (execution, billing, caching) is completely unaware of which auth path was used.

---

## 5. Changes Required

### 5.1 gtm-engine — Config (`server/core/config.py`)

**Add one new field:**

```
GTM_ENGINE_SERVICE_TOKEN: str | None = None
```

When set, enables the service token auth path. When unset, only JWT auth works (existing behavior preserved, no regression).

### 5.2 gtm-engine — Auth Dependencies (`server/auth/dependencies.py`)

**Modify `get_tenant_from_token()` to support dual auth:**

Current signature (unchanged):
```python
async def get_tenant_from_token(
    authorization: Annotated[str, Header()],
    db: AsyncSession = Depends(get_db),
) -> TenantRef:
```

New internal logic:

1. Extract the Bearer token from `Authorization` header (existing).
2. **New:** If `settings.GTM_ENGINE_SERVICE_TOKEN` is set and the token matches it → service token path.
   - Read `X-Tenant-Id` from request headers (new `Header()` dependency).
   - Validate `X-Tenant-Id` is present and non-empty.
   - Set RLS context via `set_tenant_context(db, tenant_id)`.
   - Return `TenantRef(id=tenant_id)`.
3. **Else:** Decode as JWT (existing logic, unchanged).

**Why modify `get_tenant_from_token` instead of creating a new dependency:**
- All execution endpoints already depend on `get_tenant_from_token`.
- The contract says both auth paths should work on the **same endpoints** (`/api/v1/execute`, etc.).
- A new dependency would require changing every endpoint's signature or adding a "combiner" dependency — more code for the same result.
- The output is identical: `TenantRef(id=tenant_id)` with RLS set. Downstream code doesn't care.

### 5.3 gtm-engine — Token Comparison Security

The service token comparison must be **constant-time** to prevent timing attacks:

```python
import hmac

def _is_service_token(token: str) -> bool:
    if not settings.GTM_ENGINE_SERVICE_TOKEN:
        return False
    return hmac.compare_digest(token, settings.GTM_ENGINE_SERVICE_TOKEN)
```

### 5.4 gtm-engine — Audit Logging

When a request authenticates via service token, log the auth method and requesting agent type for observability:

```
INFO: Service token auth: tenant=137 agent_type=consultant thread_id=abc-123
```

The `X-Agent-Type` and `X-Thread-Id` headers are read for logging only — they are **not** part of the auth decision.

### 5.5 workflow_studio — GTM Engine Client (`infrastructure/gtm_engine_client.py`)

New async HTTP client following the `UserManagementWS` pattern:

| Aspect | Choice |
|--------|--------|
| Pattern | SingletonBorg (matches existing clients) |
| HTTP library | `httpx.AsyncClient` (consultant agent is async) |
| Auth | `Authorization: Bearer {GTM_ENGINE_SERVICE_TOKEN}` |
| Tenant context | `X-Tenant-Id: {tenant_id}` header on every request |
| Correlation | `X-Agent-Type: consultant`, `X-Thread-Id: {thread_id}`, `X-Workflow-Id: {thread_id}` |
| Connection pool | `max_connections=20, max_keepalive_connections=10` |
| Timeout | 120s total, 10s connect |
| Retry | 3 simple retries on 429/5xx |

**Environment variables consumed:**

| Variable | Required | Default |
|----------|----------|---------|
| `GTM_ENGINE_BASE_URL` | No | `http://gtm-engine:8000` |
| `GTM_ENGINE_SERVICE_TOKEN` | Yes (for authenticated calls) | None |
| `GTM_ENGINE_TIMEOUT_SECONDS` | No | `120` |

### 5.6 No Schema Changes

No database tables are created or modified. No migrations needed.

- `tenant_id` already comes from the JWT claims path → now also from `X-Tenant-Id` header. Same value, same type (`str`).
- RLS is unaffected — it reads `current_setting('app.current_tenant')` regardless of how it was set.
- Credit billing reads `tenant_id` from the `TenantRef` output — auth-path agnostic.

---

## 6. API Contract: Service Token Auth on Existing Endpoints

No new endpoints are created. The existing endpoints gain an alternative auth mechanism.

### 6.1 Request Headers (Service Token Path)

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer {GTM_ENGINE_SERVICE_TOKEN}` |
| `X-Tenant-Id` | Yes | Tenant ID as string (e.g., `"137"`) — used for RLS context |
| `X-Agent-Type` | No | Caller identity for logging (e.g., `"consultant"`) |
| `X-Thread-Id` | No | Session correlation for run logs |
| `X-Workflow-Id` | No | Same as `X-Thread-Id` — for credit ledger entries |
| `X-Request-ID` | No | Request correlation (echoed in response) |
| `Content-Type` | Yes | `application/json` |

### 6.2 Error Responses (New/Modified)

| HTTP Status | Condition | Detail |
|-------------|-----------|--------|
| `400` | Service token valid but `X-Tenant-Id` missing/empty | `"X-Tenant-Id header is required for service token auth"` |
| `401` | Token doesn't match service token AND fails JWT decode | `"Invalid token: {jwt_error}"` (existing) |
| `401` | No `Authorization` header | `"Authorization header must use Bearer scheme"` (existing) |

All other error responses (402 insufficient credits, 429 rate limit, 502 provider error) are unchanged.

### 6.3 Endpoints Affected

These endpoints use `get_tenant_from_token` and will automatically support service token auth:

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/execute` | Single operation execution |
| `POST /api/v1/execute/batch` | Batch operation execution |
| `GET /api/v1/execute/batch/{batch_id}` | Batch status polling |
| `POST /api/v1/execute/cost` | Cost estimation |
| `GET /api/v1/credits` | Credit balance check |
| `GET /api/v1/search/patterns` | Search pattern discovery |

Endpoints that use `get_current_user` (e.g., `GET /api/v1/auth/me`) are **NOT affected** — they require a full user session. The consultant agent does not call these.

---

## 7. Security Considerations

| Concern | Mitigation |
|---------|------------|
| Token leakage | Service token is env var only — never in code, logs, or responses. Token value never logged (only auth method + tenant_id). |
| Timing attacks | Constant-time comparison via `hmac.compare_digest`. |
| Tenant spoofing via `X-Tenant-Id` | Only accepted when service token is valid. JWT auth path ignores `X-Tenant-Id` entirely (reads from claims). |
| Network boundary | Same VPC. Security groups restrict which services can reach gtm-engine. |
| Token rotation | Rotate by updating env var in both services and redeploying. No DB or migration needed. |
| Accidental exposure | Token is **not** accepted on non-execution endpoints. `get_current_user` still requires JWT — no service token bypass for user profile, admin, or console endpoints. |

---

## 8. Backward Compatibility

| Scenario | Impact |
|----------|--------|
| CLI users | No change. CLI sends gtm-engine JWT → `get_tenant_from_token` takes JWT path as before. |
| Console dashboard | No change. Console uses cookie → `get_current_user` → not affected by this change. |
| Supabase exchange | No change. `POST /api/v1/auth/exchange` is untouched. |
| `GTM_ENGINE_SERVICE_TOKEN` not set | Service token path is disabled. `_is_service_token()` returns `False`. All requests go through JWT path. **Zero behavioral change.** |

---

## 9. Implementation Sequence

### Step 1: gtm-engine — Add Service Token Auth (~30 min)

1. Add `GTM_ENGINE_SERVICE_TOKEN` to `Settings` in `server/core/config.py`.
2. Modify `get_tenant_from_token()` in `server/auth/dependencies.py`:
   - Add `x_tenant_id: str | None = Header(None)` parameter.
   - Before JWT decode, check `_is_service_token(token)`.
   - If service token: validate `x_tenant_id` present → set RLS → return `TenantRef`.
   - If not service token: existing JWT decode path.
3. Add `_is_service_token()` helper with `hmac.compare_digest`.
4. Add logging for service token auth events.

### Step 2: Test with curl (~10 min)

```bash
# Set env var
export GTM_ENGINE_SERVICE_TOKEN="test-service-token-abc123"

# Test service token auth
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Authorization: Bearer test-service-token-abc123" \
  -H "X-Tenant-Id: test-tenant" \
  -H "X-Agent-Type: consultant" \
  -H "Content-Type: application/json" \
  -d '{"operation": "enrich_company", "params": {"domain": "sully.ai"}}'

# Verify JWT auth still works
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Authorization: Bearer {existing-jwt-token}" \
  -H "Content-Type: application/json" \
  -d '{"operation": "enrich_company", "params": {"domain": "sully.ai"}}'

# Verify missing X-Tenant-Id returns 400
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Authorization: Bearer test-service-token-abc123" \
  -H "Content-Type: application/json" \
  -d '{"operation": "enrich_company", "params": {"domain": "sully.ai"}}'
```

### Step 3: workflow_studio — Create GTM Engine Client (~1 hour)

1. Create `infrastructure/gtm_engine_client.py` with SingletonBorg pattern.
2. Implement `execute()`, `execute_batch()`, `get_credit_balance()` methods.
3. Wire headers: `Authorization`, `X-Tenant-Id`, `X-Agent-Type`, `X-Thread-Id`, `X-Workflow-Id`.
4. Add retry logic for 429/5xx (3 simple retries).

### Step 4: Wire into Consultant Agent Tools

1. Inject `GTMEngineClient` instance into consultant agent tool functions.
2. Tool functions call client methods with `tenant_id` from session context and `thread_id` from conversation thread.

### Step 5: Deploy and Verify

1. Set `GTM_ENGINE_SERVICE_TOKEN` env var in both services (same value).
2. Deploy gtm-engine first (accepts both JWT and service token).
3. Deploy workflow_studio (starts sending service token).
4. Verify consultant agent execution calls succeed with proper tenant isolation.

---

## 10. What This HLD Does NOT Cover

| Item | Reason |
|------|--------|
| Full CLI auth migration to Supabase | Phase 3 in auth_migration_research.md. Separate effort. |
| JWT exchange flow for consultant agent | V2 enhancement. Service token sufficient for release. |
| `/private/` endpoint prefix | Not needed — service token works on existing `/api/v1/` endpoints. Private endpoints are for service-scoped calls (no user context, e.g., credit provisioning). Consultant calls are user-scoped. |
| CORS configuration | Service-to-service calls are same-VPC HTTP, not browser-origin. No CORS needed. |
| Console auth migration | Kept as-is per auth_migration_research.md §9. |
| Credit system integration details | Existing credit flow works unchanged — `require_credits` uses `TenantRef.id` which is auth-path agnostic. |
| Rate limiting changes | GTM Engine's existing per-provider-per-tenant rate limiting applies regardless of auth method. |
