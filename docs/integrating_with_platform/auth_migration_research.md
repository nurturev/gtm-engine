# Auth Migration Research: CLI to nRev Platform Authentication

## Status: Draft v3 — Decisions Incorporated

---

## 1. The Problem

gtm-engine (CLI + server) has its own auth system: its own Google OAuth, its own JWT issuance, its own user/tenant tables. The main nRev platform (app.nrev.ai) has a separate auth system backed by Supabase. These are two independent identity silos.

We want **one identity system**. A user who signs up via the CLI and a user who signs up via app.nrev.ai should be the same user, in the same tenant, with the same identity — exactly like how Claude Code and Claude web app share one account.

---

## 2. How Claude Code Does It (The Reference Model)

1. You run `claude` in terminal
2. It opens your browser to `claude.ai` (the web app)
3. You sign in with the normal web app SSO (Google, etc.)
4. The web app authenticates you, creates your account if needed
5. The browser redirects back to a localhost URL that the CLI is listening on
6. CLI receives tokens, stores them locally
7. You close the browser tab and continue in the terminal

**Key insight**: Claude Code doesn't have its own auth system. It piggybacks on the web app's auth entirely. The web app is the single source of truth for identity. The CLI is just another client of the same identity provider.

This is exactly what we want.

---

## 3. Current State (What Exists Today)

### gtm-engine — Self-Managed Auth
- Direct Google OAuth (server talks to Google APIs)
- Issues its own JWTs (HS256, 24h access, 30d refresh)
- Own `tenants`, `users`, `refresh_tokens` tables
- Domain-based tenant assignment logic in `server/auth/service.py:find_or_create_user()`
- CLI: opens browser → Google OAuth → localhost callback → saves tokens to `~/.nrev-lite/credentials`
- Console dashboard: cookie-based session with its own login page

### app.nrev.ai (Main Platform) — Supabase Auth
- Supabase manages Google + Microsoft SSO
- Supabase issues JWTs
- After SSO, frontend calls `POST /auth/user/attach_tenant` on the main backend
- Backend handles: domain extraction, tenant lookup/creation, user attachment
- Tenant IDs are integers (100, 121, 137, etc.)
- This user management service is a separate microservice, accessible within the VPC

### The Duplication
Both systems independently handle: OAuth, user creation, tenant assignment by email domain, JWT issuance, token refresh. This is the duplication we're eliminating.

---

## 4. Target Auth Flow

```
User runs: nrev-lite auth login
  │
  ├─ CLI starts a localhost HTTP server on a random free port
  │
  ├─ CLI opens browser to: app.nrev.ai/cli/auth?redirect_uri=http://localhost:{port}/callback
  │
  ├─ Browser lands on app.nrev.ai/cli/auth:
  │    ├─ This page stores redirect_uri in sessionStorage
  │    ├─ User is not logged in → Supabase SSO (Google/Microsoft)
  │    │   └─ Supabase handles: browser → Google/Microsoft → Supabase → back to app.nrev.ai
  │    ├─ User is already logged in → skip SSO, Supabase session already exists
  │    │
  │    ├─ (Post-SSO) Frontend calls POST /auth/user/attach_tenant with origin: "cli"
  │    │   └─ User gets created/attached to tenant based on email domain
  │    │   └─ Returns: { tenant_id, tenant_name, tenant_domain, user_name }
  │    │
  │    ├─ Frontend calls POST {gtm-engine}/api/v1/auth/exchange
  │    │   └─ Passes: Supabase JWT + tenant info from attach_tenant
  │    │   └─ gtm-engine validates Supabase JWT, issues its own JWT with tenant_id
  │    │   └─ Returns: { access_token, refresh_token, expires_in }
  │    │
  │    ├─ Shows: "You're authenticated! You can close this tab."
  │    │
  │    └─ Redirects to: http://localhost:{port}/callback?access_token=...&refresh_token=...
  │
  ├─ CLI receives tokens on localhost callback
  ├─ CLI saves to ~/.nrev-lite/credentials
  └─ Done. All subsequent API calls use the gtm-engine JWT.
```

---

## 5. Why gtm-engine Issues Its Own JWT (Authorization, Not Identity)

Supabase owns **identity** (who is this person). gtm-engine needs **authorization** (what can they access in this system).

**Why the CLI can't just hold a Supabase JWT:**

1. **Missing claims**. Supabase JWTs contain `sub` (Supabase user UUID), `aud`, `role`. They don't contain `tenant_id`. gtm-engine needs `tenant_id` in every request to set PostgreSQL RLS context (`SET LOCAL app.current_tenant = '{tenant_id}'`).

2. **Token refresh**. Supabase refresh requires the Supabase JS SDK or project anon key. The CLI is Python — it can't use the JS SDK. gtm-engine's existing `POST /api/v1/auth/refresh` is a simple HTTP call the CLI already knows how to make.

3. **Self-contained after login**. The Supabase JWT is only touched once — at login, during the exchange. After that, gtm-engine validates its own JWT on every request. No dependency on Supabase availability for ongoing operations.

### How gtm-engine JWT Authorization Works

**At login (one-time):**
```
Supabase JWT → POST /api/v1/auth/exchange → gtm-engine JWT
```
The exchange endpoint:
1. Decodes the Supabase JWT using `SUPABASE_JWT_SECRET` (confirms it's authentic)
2. Reads the `tenant_id` passed from the frontend (which got it from `attach_tenant`)
3. Issues a gtm-engine JWT with claims:
   ```json
   {
     "sub": "supabase-user-uuid-here",
     "tenant_id": "137",
     "email": "john@acme.com",
     "role": "member",
     "channel": "cli",
     "exp": 1711324800,
     "type": "access"
   }
   ```

**On every subsequent API request:**
```
CLI sends: Authorization: Bearer {gtm-engine-jwt}
                    ↓
gtm-engine middleware decodes JWT (using its own JWT_SECRET_KEY)
                    ↓
Extracts tenant_id from claims
                    ↓
SET LOCAL app.current_tenant = '137'
                    ↓
All SQL queries are now filtered by RLS to tenant 137's data
```

**Token refresh:**
```
CLI sends: POST /api/v1/auth/refresh { refresh_token: "abc..." }
                    ↓
Server validates refresh token hash in DB
                    ↓
Issues new access_token + refresh_token pair
                    ↓
CLI stores new tokens in ~/.nrev-lite/credentials
```

The gtm-engine JWT is purely an authorization mechanism. It says "this request is for tenant 137" so the database can enforce isolation. It doesn't manage identity — that's Supabase + the user management service's job.

---

## 6. Tenant/User Data — No Duplication, Application-Layer Joins

**Decision: gtm-engine does NOT maintain its own user or tenant management.**

The user management service (main app backend, same VPC) is the single source of truth. gtm-engine is just another microservice that consumes it.

### What about foreign keys?

Current state: `credit_balances`, `api_keys`, `datasets`, `run_logs`, `scripts` all have `tenant_id` FK references to a local `tenants` table. The `refresh_tokens` table has `user_id` FK to a local `users` table.

**Target state: drop the FK constraints. Use `tenant_id` as a plain value column. Joins happen at application layer.**

This is standard microservice architecture. Service A doesn't maintain a copy of Service B's tables just for FK integrity. It stores the ID and resolves it via API when needed.

**Concrete changes:**
- Drop `REFERENCES tenants(id)` FK constraints from all tables
- Drop `REFERENCES users(id)` FK constraint from `refresh_tokens`
- `tenant_id` remains as a `TEXT` column on every table (still used by RLS)
- `refresh_tokens` keys on `supabase_user_id` instead of `user_id` (see section 10)
- RLS is **unaffected** — it only checks `current_setting('app.current_tenant')`, not FK existence
- The `tenants` and `users` tables can be dropped (or kept for console until it migrates)

**When gtm-engine needs tenant info** (e.g., displaying tenant name in console): call the user management service. Same VPC, single-digit millisecond latency.

### Tenant ID Type Mismatch

**Decision: Convert gtm-engine to use integer tenant IDs.**

Main app uses integers (100, 121, 137). gtm-engine currently uses `TEXT`. Two options:

**Option A: Migrate gtm-engine columns from TEXT to INTEGER**
- Requires ALTER TABLE on all tenant_id columns
- Clean, no ambiguity
- Migration effort: moderate (need to update all existing data)

**Option B: Keep TEXT columns, store the integer as a string ("137")**
- No schema migration needed
- RLS works fine (it's a string comparison against `current_setting` which returns text anyway)
- Slightly ugly but zero migration effort

**Recommendation: Option B for V1.** Store `"137"` as text. The RLS `SET LOCAL app.current_tenant = '137'` already works with text. Migrate to INTEGER later if it becomes a problem. No reason to block on a type migration right now.

---

## 7. CLI User vs Platform User — DB Flag in Main App

**Decision: `user_origin` field in the user management service.**

### How it works:

1. User authenticates via CLI → `attach_tenant` is called with `origin: "cli"`
2. User management service stores `user_origin = "cli"` on the user record
3. Later, if this user visits app.nrev.ai directly:
   - Frontend checks `user_origin` from user profile
   - If `"cli"` → show CLI landing page: "You're using nRev via CLI. [Upgrade to Full Platform]"
   - If `"platform"` → show normal dashboard
4. User clicks "Upgrade to Full Platform" → update `user_origin = "platform"`, show dashboard

### Edge cases:

- **Platform user later uses CLI**: `user_origin` stays `"platform"`. CLI auth works fine; flag doesn't change.
- **User uses both**: Flag only affects app.nrev.ai routing. CLI doesn't care.
- **First visit is CLI, never upgrades**: They only ever see the CLI landing page on app.nrev.ai. Fine — they're a CLI user.

### Over-engineering check

Keep V1 simple. The CLI landing page on app.nrev.ai should be minimal:
- "You're using nRev via CLI."
- Link to CLI dashboard (existing console, until absorbed)
- CTA: "Upgrade to Full Platform"
- That's it. Don't build a separate CLI experience inside app.nrev.ai.

---

## 8. Device Code Flow — Remove for V1

**Decision: Remove it.**

### What it is:
For headless environments (SSH, containers) where no browser is available. Like pairing a smart TV: CLI shows a short code, user enters it on any browser, CLI polls until approved.

### What you lose:
Users over SSH/containers can't authenticate directly. Workaround: authenticate locally, copy `~/.nrev-lite/credentials` to the remote machine.

### Why remove:
- Current implementation uses gtm-engine's own Google OAuth — would need full rewrite for Supabase
- Small user base for headless CLI in early days
- Simplifies to one auth flow
- Can rebuild later using `app.nrev.ai/cli/device-verify` if demand appears

---

## 9. Console Dashboard — Keep As-Is

**Decision: Console keeps its own Google OAuth for now.**

When the dashboard migrates to nrev-ui-2 (per todo plan), it inherits Supabase auth naturally. No investment in migrating console auth.

---

## 10. Refresh Token Lifecycle

**Decision: Key refresh tokens on Supabase user ID.**

Current: `refresh_tokens.user_id` → FK to `users.id`

Target: `refresh_tokens.subject_id` (or similar) → stores Supabase user UUID as plain text. No FK constraint.

The exchange endpoint creates a refresh token keyed on the Supabase user UUID from the JWT's `sub` claim. The refresh endpoint validates the token hash, loads by `subject_id`, and issues new tokens.

---

## 11. Credit System

**Decision: Platform credit service is the single source of truth. GTM Engine calls platform APIs for credit checks and debits.**

### V1: Switchable Credit Backend (Implemented)

GTM Engine supports two credit modes, switched via `PLATFORM_CREDIT_SERVICE_URL`:

- **Platform mode** (env var set): GTM Engine calls platform credit APIs before/after each execution. No local credit tables used.
- **Local mode** (env var unset): Uses local `credit_balances`/`credit_ledger` tables. CLI keeps working as-is.

### Platform Credit API Contract

**Auth**: Fixed Bearer token (`PLATFORM_CREDIT_SERVICE_TOKEN` env var) in `Authorization` header. Internal microservice-to-microservice auth.

**Check balance:**
```
GET {PLATFORM_CREDIT_SERVICE_URL}/tenant/credits?tenant_id=<tenant_id>
Authorization: Bearer {PLATFORM_CREDIT_SERVICE_TOKEN}

Response: <Int> or null
```
- `null` treated as 0 credits → HTTP 402 returned to caller

**Debit (fire-and-forget):**
```
POST {PLATFORM_CREDIT_SERVICE_URL}/tenant/credit/deduct
Authorization: Bearer {PLATFORM_CREDIT_SERVICE_TOKEN}
{
    "tenant_id": "<tenant_id>",
    "credit_count": <int>,
    "agent_thread_id": "<workflow_id or null>"
}

Response: 202 Accepted
```
- Called after successful execution (non-BYOK, non-cached only)
- Fire-and-forget: execution response is not blocked on debit completion
- BYOK and cache hits skip the debit entirely

### Execution Credit Flow (Platform Mode)

```
1. require_credits dependency → GET /tenant/credits?tenant_id=X
   └─ If null or < needed → HTTP 402 "Insufficient credits"
2. Execute operation (call external provider)
3. If success AND not BYOK AND not cached:
   └─ Fire-and-forget POST /tenant/credit/deduct
4. Return result to caller
```

### Migration Notes
- The main app decides how many credits to assign based on `user_origin` ("cli" vs "platform")
- Signup credit logic will be removed from gtm-engine's `find_or_create_user()`
- Local credit tables (`credit_balances`, `credit_ledger`) remain for CLI backward compatibility until full migration

---

## 12. What Changes Where (Updated)

### nrev-ui-2 (main app frontend)

| Change | What |
|--------|------|
| New page: `/cli/auth` | Stores redirect_uri in sessionStorage, checks Supabase session, triggers SSO if needed |
| New route: `/cli/auth/callback` | Exchanges Supabase code, calls attach_tenant with origin:"cli", calls gtm-engine exchange, shows confirmation, redirects to CLI localhost |
| New routing logic | If user_origin == "cli" and visiting platform normally, show CLI landing page with upgrade CTA |

### Main app backend (user management service)

| Change | What |
|--------|------|
| Modify: `attach_tenant` endpoint | Accept `origin` parameter ("cli" / "platform"), store on user record |
| New column: user table | `user_origin` field |

### gtm-engine server

| Change | What |
|--------|------|
| New endpoint: `POST /api/v1/auth/exchange` | Validates Supabase JWT, issues gtm-engine JWT with tenant_id claim |
| New config: `SUPABASE_JWT_SECRET` | For one-time Supabase JWT validation at login |
| Schema: drop FK constraints | Remove `REFERENCES tenants(id)` and `REFERENCES users(id)` from all tables |
| Schema: refresh_tokens | Change `user_id` to `subject_id` (Supabase UUID, no FK) |
| Remove: device code endpoints | `POST /device/code`, `POST /device/token` |
| Remove: signup credit logic | From `find_or_create_user()` |

### gtm-engine CLI

| Change | What |
|--------|------|
| Modify: `_browser_oauth_flow()` | Point to `app.nrev.ai/cli/auth` instead of gtm-engine's Google OAuth |
| Remove: `_device_code_flow()` | And CLI flag for headless auth |
| No change | Localhost server, credential storage, token refresh, MCP auth |

---

## 13. The Token Exchange Endpoint (Design — Implemented)

**Key point:** Supabase JWTs do NOT contain `tenant_id`. They only carry `sub` (Supabase user UUID), `aud`, `role`. The `tenant_id` must be passed as a **request parameter** — the calling service knows it from the platform's user management service (e.g., from `attach_tenant` response).

```
POST /api/v1/auth/exchange

Request:
{
    "supabase_jwt": "ey...",          // Supabase access token (for validation only)
    "tenant_id": "137",              // From platform user management (NOT from Supabase JWT)
    "email": "john@acme.com",        // Optional, for audit logging
    "channel": "consultant"          // "cli" | "consultant" — identifies calling channel
}

Response:
{
    "access_token": "ey...",          // gtm-engine JWT (24h)
    "token_type": "bearer",
    "expires_in": 86400
}
```

**What the endpoint does:**
1. Decode `supabase_jwt` using `SUPABASE_JWT_SECRET` — confirms it's authentic, extracts `sub` (Supabase user UUID)
2. Issue gtm-engine JWT: `{ sub: supabase_user_uuid, tenant_id: "137", email, channel, type: "access" }`
3. Return token. **No refresh token** — services re-exchange when the 24h token expires.

**No User or Tenant DB records are created.** The execution endpoints use `get_tenant_from_token()` which reads `tenant_id` directly from JWT claims. Credit tables have no FK to tenants table.

**Token lifecycle for microservices:**
```
1. Call POST /api/v1/auth/exchange → get gtm-engine JWT (24h)
2. Cache the JWT in memory/Redis
3. Use for all /execute calls
4. On 401 (expired) → re-exchange using current Supabase JWT
```

**Security**: The Supabase JWT validation is the trust anchor. A valid Supabase JWT means the user genuinely authenticated via Supabase SSO. The `tenant_id` in the request is trusted because only a legitimately authenticated service within the VPC would have access to this endpoint and the Supabase JWT secret.

### Full CLI Auth Flow (Future — uses same exchange endpoint)

When CLI auth migrates to Supabase, the flow becomes:
1. CLI opens browser to `app.nrev.ai/cli/auth`
2. User authenticates via Supabase SSO
3. Frontend calls `attach_tenant` → gets `tenant_id`
4. Frontend calls `POST /api/v1/auth/exchange` with Supabase JWT + tenant_id
5. Gets gtm-engine JWT + optional refresh token (CLI variant may include refresh token)
6. Redirects to CLI localhost with tokens

---

## 14. Migration Phases

### Phase 1: Server — Exchange Endpoint + Schema Changes
- Add `SUPABASE_JWT_SECRET` to config
- Implement `POST /api/v1/auth/exchange`
- Drop FK constraints (migration script)
- Modify refresh_tokens to use `subject_id`
- Test with curl

### Phase 2: Frontend — CLI Auth Pages
- Build `/cli/auth` and `/cli/auth/callback` in nrev-ui-2
- Modify `attach_tenant` to accept `origin` param
- Add `user_origin` column to main app user table
- Test end-to-end in staging

### Phase 3: CLI Update
- Point `_browser_oauth_flow()` to `app.nrev.ai/cli/auth`
- Remove device code flow
- Test: `nrev-lite auth login` → browser → back to CLI

### Phase 4: Cleanup
- Remove device code server endpoints
- Remove signup credit logic from gtm-engine
- Mark Google OAuth as console-only (document, don't delete yet)

---

## 15. Decisions Taken

| # | Topic | Decision |
|---|-------|----------|
| 1 | Identity provider | Supabase via app.nrev.ai (single source of truth) |
| 2 | gtm-engine token | Issues its own JWT for authorization (tenant_id in claims for RLS) |
| 3 | User/tenant management | Main app's service is the source of truth. No duplication in gtm-engine. |
| 4 | FK constraints | Drop them. Store tenant_id as plain value. Application-layer joins. Standard microservice pattern. |
| 5 | Tenant ID format | Main app uses integers. gtm-engine stores as TEXT string ("137"). No type migration for V1. |
| 6 | CLI vs platform user | `user_origin` flag in main app's user table. Drives routing on app.nrev.ai. |
| 7 | Device code flow | Remove for V1. Rebuild if headless demand appears. |
| 8 | Console dashboard | Keeps its own auth. Migrates when absorbed into main app. |
| 9 | Refresh tokens | Key on Supabase user UUID (`sub` claim). No FK to users table. |
| 10 | Credit provisioning | Moves to main app. Main app decides credits based on user_origin. |
| 11 | Supabase JWT secret | Shared with gtm-engine as env var (same VPC). Standard microservice pattern. |

---

## 16. All Decisions Finalized

No remaining decisions. All resolved — see section 15 plus:

| # | Topic | Decision |
|---|-------|----------|
| 12 | Existing user data | No migration needed. CLI is not yet productionized — no existing users to worry about. |
| 13 | Redirect URI validation | Enforce localhost-only. Allowlist: `http://localhost:*` and `http://127.0.0.1:*`. |
| 14 | gtm-engine URL in frontend | Env var: `NEXT_PUBLIC_GTM_ENGINE_URL` in nrev-ui-2. Same pattern as existing `NEXT_PUBLIC_AUTHENTICATION_URL`. |
| 15 | Service-to-service auth | Two scenarios: (A) User-scoped calls use JWT exchange (same as CLI). (B) Service-scoped calls (no user context) use static Bearer token via env var (`GTM_ENGINE_SERVICE_TOKEN`), calling `/private/` endpoints. Matches workflow_studio pattern. |

---

## 17. Service-to-Service Authentication (Microservice → gtm-engine)

There are two distinct authentication scenarios when another microservice (e.g., consultant agent) calls gtm-engine APIs:

### Scenario A: User-Scoped Calls (Agent Acting on Behalf of a User)

**Examples:** Enrichment, dataset operations, search — anything that needs a `tenant_id` for RLS and credit billing.

**Approach: JWT exchange (already designed in sections 4-5 and 13).**

The calling microservice (e.g., consultant agent orchestrator) has access to the user's Supabase JWT from the active session. It exchanges that for a gtm-engine JWT:

```
User authenticated via Supabase on app.nrev.ai
  → Orchestrator calls: POST {gtm-engine}/api/v1/auth/exchange
    Body: { supabase_jwt, tenant_id, user_email, ... , channel: "consultant" }
  → Gets back: gtm-engine JWT with tenant_id + channel claims
  → Uses this JWT for all subsequent calls within the session
```

This is the same exchange endpoint the CLI uses. The `channel` claim (see `data_module_extension_plan.md` §4.3) distinguishes CLI vs consultant calls for run logging and analytics. The gtm-engine JWT carries `tenant_id`, so RLS, credit billing, BYOK key resolution, and caching all work identically to CLI calls.

**Token lifecycle:** The orchestrator caches the gtm-engine JWT for the session (24h expiry). On expiry, it uses the refresh token via `POST /api/v1/auth/refresh`. No Supabase dependency after the initial exchange.

### Scenario B: Service-Scoped Calls (No User Context)

**Examples:** Credit provisioning (`add_credits()`), health checks, admin operations where there is no active user session.

**Approach: Static Bearer token via environment variable — consistent with workflow_studio → user management service pattern.**

```
Calling service holds: GTM_ENGINE_SERVICE_TOKEN (env var)
  → Sends: Authorization: Bearer {GTM_ENGINE_SERVICE_TOKEN}
  → Calls: /private/api/v1/credits/add, /private/api/v1/health, etc.
  → Passes tenant_id explicitly in the request body (no JWT to extract it from)
```

**Design details:**

| Aspect | Detail |
|--------|--------|
| Token source | Environment variable (`GTM_ENGINE_SERVICE_TOKEN`) set at deployment |
| Endpoint prefix | `/private/api/v1/` — internal-only routes, not exposed to CLI or browser |
| Network boundary | Same VPC, security groups restrict access to known service IPs |
| Tenant context | Passed as request parameter, NOT extracted from JWT. The `/private/` middleware trusts the caller and sets RLS accordingly |
| Validation | gtm-engine validates the token against a hashed value stored in config (not DB). Simple string comparison, no JWT decode |
| Rotation | Rotate by updating the env var in both services and redeploying. No DB migration needed |

**Why not JWT exchange for service-scoped calls:**
- No user session exists — there's no Supabase JWT to exchange
- The calling service acts as itself, not on behalf of a user
- A static token is simpler, sufficient, and follows the established pattern across the platform (workflow_studio already does this)

**Reference implementation:** `workflow_studio/infrastructure/user_management_ws.py` — uses `USER_MANAGEMENT_WS_AUTH_TOKEN` env var with Bearer header to call `/private/` endpoints on the user management service. Same pattern, same VPC, same security model.

### Summary: Which Token for Which Scenario

| Caller | Scenario | Token Type | Tenant Context |
|--------|----------|-----------|---------------|
| CLI | User-scoped | gtm-engine JWT (via exchange) | From JWT claims |
| Consultant agent (enrichment, data) | User-scoped | gtm-engine JWT (via exchange) | From JWT claims |
| Main app credit service (provisioning) | Service-scoped | Static Bearer token (env var) | Passed in request body |
| Main app admin operations | Service-scoped | Static Bearer token (env var) | Passed in request body |
| Console dashboard | User-scoped | Own Google OAuth (temporary) | From session |

---

## 18. Gaps, Risks, and Implementation TODOs

### Gap: MCP server auth
The MCP server reads `~/.nrev-lite/credentials` and refreshes via `POST /api/v1/auth/refresh`. Unchanged by this migration — it uses gtm-engine tokens. No changes needed.

### Risk: Supabase session expiry during CLI auth
If Supabase session expires between SSO and the callback page, user needs to re-auth. The `/cli/auth` page should handle this gracefully.

### Risk: Console auth divergence
Console keeps its own Google OAuth while CLI moves to Supabase. Two auth paths coexist temporarily. Document clearly which endpoints serve which flow to avoid confusion during the transition.

### TODO: CORS for exchange endpoint
The `/cli/auth/callback` page (on `app.nrev.ai`) calls gtm-engine's API (different domain). gtm-engine needs CORS headers allowing `app.nrev.ai` as an origin for the `/api/v1/auth/exchange` endpoint. This must be configured per environment:
- Dev: `http://localhost:3000` (nrev-ui-2 local dev)
- Staging: `https://staging.nrev.ai`
- Prod: `https://app.nrev.ai`

Use an env var (`ALLOWED_ORIGINS`) to configure, and apply CORS middleware scoped to the exchange endpoint (not globally, to avoid opening other endpoints unnecessarily).
