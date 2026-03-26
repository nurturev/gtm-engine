# Data Module Analysis: Consultant Agent Integration

## Status: Final v1 (supersedes Draft v1)

---

## 1. Decision Summary

The consultant agent inside app.nrev.ai does **NOT** need the data management module (`server/data/`). The only gtm-engine surface area the consultant needs is:

- **Auth exchange** — `POST /api/v1/auth/exchange` (get gtm-engine JWT from Supabase JWT)
- **Execution endpoints** — `/api/v1/execute/*` (enrichment, search, scrape, cost estimation)

Everything else — datasets, tables, raw SQL, dashboards, scripts, schedules, hosted apps, Composio — stays CLI/console-only.

---

## 2. What the Data Module Actually Does

The `server/data/` module serves **5 distinct consumer groups**, not just the console:

| Consumer | Endpoints / Mechanism | Purpose |
|----------|----------------------|---------|
| **MCP tools** (Claude Code) | `/api/v1/tables/*`, `/api/v1/datasets/*` | `nrev_query_table`, `nrev_create_dataset`, `nrev_append_rows`, `nrev_query_dataset`, `nrev_list_datasets` |
| **CLI** (`nrev-lite datasets`) | `/api/v1/datasets/*` | list, describe, query, export |
| **Execution pipeline** | Direct DB writes via `persistence.py` | Writes to `contacts`, `companies`, `search_results`, `enrichment_log` after every enrich/search call |
| **Dashboards module** | References `Dataset` model | Dashboard widgets read from datasets |
| **Console UI** | `/api/v1/datasets/*` | Datasets tab display + CSV download |

### 2.1 Execution Pipeline Persistence (server/execution/persistence.py)

Every enrichment/search call triggers `persist_execution()` which writes to 4 internal tables:

**`enrichment_log`** — Immutable audit trail of every API call
- Records: provider, operation, params, result, status, cost, latency, cached flag
- Purpose: billing reconciliation, provider debugging, Runs tab in console
- **Consultant agent equivalent**: Main app's own audit/activity logging system + centralized credit management service

**`contacts`** — Progressive enrichment accumulator for people
- Dedup key: `tenant_id + email`
- Merge strategy: only fills NULL fields, never overwrites existing data
- Tracks `enrichment_sources` per provider (e.g. "Apollo gave us title, RocketReach gave us phone")
- Purpose: builds a single accumulated view of a contact across multiple enrichment runs, providers, and sessions
- **Consultant agent equivalent**: nRev tables in the main application will handle contact data persistence and progressive enrichment

**`companies`** — Progressive enrichment accumulator for organizations
- Dedup key: `tenant_id + domain`
- Same merge strategy as contacts
- Purpose: accumulated company profile across providers
- **Consultant agent equivalent**: nRev tables in the main application

**`search_results`** — Search dedup cache
- Hash: `sha256(operation + sorted params)` (first 16 chars)
- Purpose: detect "you already searched for this" to avoid wasting credits
- **Consultant agent equivalent**: caching layer within the main app or consultant orchestrator

### 2.2 Why These Are Valuable for CLI But Not for Consultant

The CLI operates in a **multi-session, iterative** model:
- User enriches `john@acme.com` via Apollo on Monday → gets name, title
- User enriches the same email via RocketReach on Wednesday → gets phone, LinkedIn
- The upsert merges both results into a single accumulated contact record
- User queries `nrev-lite datasets` or the console to see the full picture

The consultant agent operates within the **main app's data model**:
- The main app already has its own contact/company tables (nRev tables)
- Enrichment results are returned in the HTTP response and persisted by the main app
- Dedup, merge, and progressive enrichment are handled by the main app's own logic
- Audit/billing is handled by the centralized credit management microservice

---

## 3. Consultant Agent: Execution-Only Surface Area

### What the consultant uses

| Endpoint | Purpose | Credits |
|----------|---------|---------|
| `POST /api/v1/auth/exchange` | Get gtm-engine JWT from Supabase JWT | Free |
| `POST /api/v1/execute` | Single enrichment/search/scrape | Yes |
| `POST /api/v1/execute/cost` | Cost estimation before execution | Free |
| `POST /api/v1/execute/batch` | Batch enrichment | Yes |
| `GET /api/v1/execute/batch/{id}` | Poll batch status | Free |
| `GET /api/v1/search/patterns` | Google search query patterns | Free |

### What the consultant does NOT use

| Feature | Reason |
|---------|--------|
| Tables API (`/api/v1/tables/*`) | Main app has its own data stores |
| Datasets API (`/api/v1/datasets/*`) | CLI/MCP workflow feature for accumulating data across scheduled runs |
| Raw SQL (`/api/v1/query`) | CLI power-user feature |
| Dashboards (`/api/v1/dashboards/*`) | CLI deployment feature |
| Composio connectors (`/api/v1/connections/*`) | CLI-only in V1 |
| Scripts (`/api/v1/scripts/*`) | CLI workflow concept |
| Hosted apps (`/api/v1/apps/*`) | CLI deployment feature |
| Schedules (`/api/v1/schedules/*`) | CLI scheduling via Claude Code |
| BYOK keys (`/api/v1/keys/*`) | CLI-only key management |
| Credit billing (`/api/v1/credits/*`) | Main app's centralized credit management service owns this |

---

## 4. Execution Pipeline Side Effects When Called by Consultant

When the consultant agent calls `/api/v1/execute/enrich_person`, the execution pipeline will still run `persist_execution()` internally and write to `contacts`, `companies`, `enrichment_log`, and `search_results` tables in gtm-engine's database.

**This is harmless for V1:**
- The writes are non-blocking (wrapped in try/except, never fails the HTTP response)
- The data sits in gtm-engine's internal tables, unused by the consultant
- The consultant gets the enrichment result in the HTTP response body and hands it to the main app

**Future optimization (not V1):**
When the `channel` claim is added to the JWT, `persist_execution()` can skip the contact/company/search_result upserts for `channel == "consultant"` while still writing to `enrichment_log` (useful for provider analytics on the gtm-engine side). This is a ~10 line change gated on `channel`.

---

## 5. Credit Management: Dual System Transition

### Current state (CLI-only)
gtm-engine has its own credit system (`server/billing/`):
- `require_credits` dependency checks balance before execution
- `credit_ledger` records every deduction
- Users buy credits via dashboard or CLI

### Target state (with consultant agent)
- **Main app's centralized credit management microservice** = source of truth for provisioning, deduction, and balance
- **gtm-engine** = execution engine that makes the actual provider API calls
- Credit check/deduction happens at the main app level before or after calling gtm-engine

### Transition approach
The exact handoff between gtm-engine's internal credit system and the centralized credit management service will be designed in detail in a future iteration. The key principle: the consultant channel should not use gtm-engine's internal billing — the main app handles that independently.

For V1, the `channel` claim in the JWT can gate this:
- `channel == "cli"` → gtm-engine checks/deducts credits internally (existing behavior)
- `channel == "consultant"` → gtm-engine skips `require_credits` check; main app's credit service handles billing

---

## 6. Console: Stays Separate, No Changes

### Decision
The console (`server/console/`) and all related functionality continues to be served directly by the gtm-engine API service. There is no plan to move the console into the main application in this iteration.

### Future iframe option (not V1)
If we decide to show the console inside the nRev web app in a future release, we would iframe the console hosted on the gtm-engine API service. This requires:

| Change | Details |
|--------|---------|
| Cookie `samesite` | `"lax"` → `"none"` + `secure=True` (HTTPS required) |
| Fetch credentials | `'same-origin'` → `'include'` in template JS |
| CORS config | Add main app domain to `CORS_ALLOWED_ORIGINS` |
| **OR (recommended)**: Token relay via `postMessage` | Parent app sends gtm-engine JWT to iframe; iframe uses Bearer header instead of cookie. Avoids weakening cookie security. |

The flexible auth layer (`server/auth/flexible.py`) already supports Bearer tokens, so the `postMessage` approach works without server-side changes.

### Authentication gap to be aware of
- Console today: its own Google OAuth → gtm-engine JWT → cookie
- Main app: Supabase SSO → Supabase JWT
- When iframing, the user is already authenticated in the main app. Re-authenticating through Google OAuth inside the iframe would be poor UX. Solution: pass a gtm-engine JWT (obtained via the exchange endpoint) from the parent frame. This is a future concern, not V1.

**V1 accepted trade-off:** CLI/console users do a separate login. This avoids overcomplicating the system.

---

## 7. Future: nRev Tables Replacing Internal Tables

The `contacts` and `companies` tables inside gtm-engine serve as progressive enrichment accumulators for CLI users. In the main application, this functionality will be provided by **nRev tables** — the main app's own contact/company data model.

### What this means for gtm-engine
- `contacts` and `companies` tables in gtm-engine continue to serve CLI users as-is
- The consultant agent does NOT write to or read from these tables
- When a consultant agent enriches a contact, the result goes to nRev tables in the main app
- There is no need to sync data between gtm-engine's internal tables and nRev tables — they serve different user populations (CLI vs main app)

### Progressive enrichment in nRev tables
The merge-on-NULL-fields pattern currently in `persistence.py` (lines 149-156 for contacts, 234-239 for companies) is a useful reference for implementing the same in nRev tables:
- Only overwrite fields that are currently NULL
- Track `enrichment_sources` per provider to know which provider contributed which field
- Dedup by a natural key (email for people, domain for companies)

### Search result caching
The `search_results` table in gtm-engine caches search queries by hashing `operation + sorted params`. The main app should implement an equivalent caching layer for the consultant agent to avoid redundant credit-consuming searches.

---

## 8. No Changes Required in server/data/

**The data module requires zero changes for V1 consultant agent integration.** The consultant agent simply does not call any data endpoints. The execution pipeline's internal persistence continues to run as a harmless side effect.

### Changes required elsewhere for consultant integration

| Area | What | Where |
|------|------|-------|
| Auth exchange endpoint | Issue JWT with `channel` claim | `server/auth/` (per auth migration plan) |
| Credit gating by channel | Skip `require_credits` for consultant | `server/billing/` or execution router |
| Execution pipeline (future) | Optionally skip contact/company upsert for consultant | `server/execution/persistence.py` |

These changes are documented in their respective integration research documents (`auth_migration_research.md`, `enrichment_integration_research.md`).
