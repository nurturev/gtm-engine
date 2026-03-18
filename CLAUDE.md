# nrv — Agent-Native GTM Execution Platform

You are working on nrv, a cloud-first GTM (Go-To-Market) platform by nRev.

## Architecture

nrv uses a split architecture:
- **Client (this repo, `src/nrv/`)**: Thin CLI + Claude Code skills with GTM intelligence
- **Server (`server/`)**: FastAPI API gateway, provider proxy, credit billing, PostgreSQL database
- **Infrastructure**: AWS (Aurora Serverless v2, ECS Fargate, Redis, S3, KMS)

## Project Structure

```
src/nrv/           → Python package installed by users (CLI + skills)
  cli/             → Click CLI commands (nrv auth, nrv enrich, etc.)
  client/          → HTTP client that talks to nrv server
  skills/          → Claude Code skills (GTM intelligence)
  mcp/             → MCP server (tools for Claude Code integration)
  utils/           → Display helpers, config management

server/            → FastAPI server (deployed to AWS)
  auth/            → Auth models, JWT, router
  billing/         → Credit system (1 credit/op, BYOK free, ~$0.08/credit)
  console/         → Tenant dashboard (HTML/JS SPA served by FastAPI)
  core/            → Config, database, middleware
  data/            → Data tables + persistent datasets (JSONB document store)
  dashboards/      → Dashboard management
  execution/       → Workflow execution, run logs, schedules, providers
  vault/           → BYOK key encryption (Fernet dev / KMS prod)

migrations/        → SQL migration files for PostgreSQL (001-007)
infra/             → AWS CDK infrastructure (future)
docs/              → Architecture documentation

.claude/skills/    → Claude Code skills (GTM knowledge base)
.claude/rules/     → Security + enrichment rules
```

## Key Principles

1. The CLI NEVER calls external APIs directly — all provider calls go through the server
2. Skills contain GTM knowledge and workflow logic — they decide WHAT to do
3. The server handles HOW — routing, rate limits, retries, pagination, caching
4. Every tenant's data is isolated via PostgreSQL Row-Level Security
5. API keys are either BYOK (encrypted with KMS) or platform-managed (Secrets Manager)
6. Credits are the billing unit — BYOK calls are free, platform key calls cost credits

## Local Development

```bash
# Start PostgreSQL + Redis
docker-compose up -d postgres redis

# Run the API server
cd server && uvicorn server.app:app --reload

# Install CLI in dev mode
pip install -e ".[dev]"

# Test CLI
nrv auth login
nrv enrich person --email test@example.com
```

## MCP Tools (23 tools)

| Tool | What It Does |
|------|-------------|
| `nrv_health` | Quick health check — verifies server + auth are working |
| `nrv_new_workflow` | Start a new workflow within the session (for run log grouping) |
| `nrv_search_web` | Google web search via RapidAPI |
| `nrv_scrape_page` | Extract content from URLs via Parallel Web |
| `nrv_google_search` | Google SERP with all operators, tbs, site, bulk queries |
| `nrv_search_patterns` | **Call BEFORE Google search** — get platform-specific query patterns |
| `nrv_search_people` | People search via Apollo/RocketReach (titles, companies, alumni) |
| `nrv_enrich_person` | Person enrichment (email/name/LinkedIn) |
| `nrv_enrich_company` | Company enrichment (domain/name) |
| `nrv_query_table` | Query data tables with filters |
| `nrv_list_tables` | List available tables |
| `nrv_create_dataset` | Create a persistent dataset for workflow data accumulation |
| `nrv_append_rows` | Append/upsert rows to a persistent dataset |
| `nrv_query_dataset` | Query rows from a persistent dataset |
| `nrv_list_datasets` | List all persistent datasets |
| `nrv_estimate_cost` | Estimate credit cost before executing (call before large batches) |
| `nrv_get_run_log` | Read back workflow run logs with results and column metadata |
| `nrv_credit_balance` | Check credit balance and spend |
| `nrv_provider_status` | Check provider availability |
| `nrv_list_connections` | List active OAuth connections |
| `nrv_list_actions` | Discover available actions for a connected app |
| `nrv_get_action_schema` | Get parameter schema for a specific action |
| `nrv_execute_action` | Execute an action on a connected app |

### Troubleshooting

If any tool returns an error:
- `"Not authenticated"` → Run `nrv auth login` in the terminal
- `"Cannot connect to nrv server"` → Start the server: `cd server && uvicorn server.app:app --reload`
- `"No active connection for 'gmail'"` → User must connect the app at the dashboard
- `"Session expired"` → Run `nrv auth login` again

## Google Search — Dynamic Pattern Discovery

**NEVER guess Google search query patterns for specific platforms.** Always discover dynamically:

1. `nrv_search_patterns(platform="linkedin_jobs")` — get exact site: prefix, query templates, tips
2. `nrv_search_patterns(use_case="hiring_signals")` — get GTM-optimized query patterns
3. `nrv_google_search(query=..., tbs=..., site=...)` — execute with the correct patterns

### Key Parameters
- **tbs**: Time filter. Friendly: `hour`, `day`, `week`, `month`, `year`. Raw: `qdr:h2` (2 hours), `qdr:d3` (3 days), `qdr:m3` (3 months). Custom: `cdr:1,cd_min:MM/DD/YYYY,cd_max:MM/DD/YYYY`
- **site**: Convenience site restriction (e.g. `linkedin.com/jobs/view`)
- **queries**: Bulk search — multiple queries run concurrently

### Why This Matters
Each platform has specific URL structure nuances (e.g. `linkedin.com/jobs/view` not `/jobs/search`, `x.com/*/status` for tweets). These patterns live on the server and evolve without client updates.

## Connected Apps (via Composio)

Tenants can OAuth-connect apps through the dashboard or CLI (`nrv connect <app>`).

### How to Execute Actions (Dynamic Discovery)

**Do NOT hardcode action names or params.** Always discover dynamically:

1. `nrv_list_connections` — check which apps are connected
2. `nrv_list_actions(app_id)` — discover available actions for that app
3. `nrv_get_action_schema(action_name)` — get exact parameter names, types, and required flags. **This is non-optional** — param names are NOT guessable (e.g. `text_to_insert` not `text`, `markdown_text` not `content`, `ranges` must be an array not a string)
4. `nrv_execute_action(app_id, action, params)` — execute with the correct params

Available app_ids: gmail, slack, google_sheets, google_docs, hubspot, salesforce, linear, notion, clickup, asana, airtable, google_calendar, calendly, attio, google_drive

### Error Handling for Connected Apps

1. If `nrv_list_connections` shows no active connection → tell the user to connect via the dashboard
2. If action returns `status: error` → check the `error` field for details
3. If action returns `"Following fields are missing"` → you skipped `nrv_get_action_schema`. Go back and check exact param names.
4. Common failure: app connected but missing required OAuth scopes → user must reconnect

## Persistent Datasets

Datasets are long-lived JSONB document stores that workflows write to over time. They support scheduled workflow accumulation (e.g., daily LinkedIn monitoring appends new posts without duplicating old ones).

- **Create**: `nrv_create_dataset(name, columns, dedup_key)` — idempotent, returns existing if slug matches
- **Append**: `nrv_append_rows(dataset_ref, rows)` — upserts via SHA256 hash of dedup_key value
- **Query**: `nrv_query_dataset(dataset_ref, filters, limit, offset)`
- **Dedup**: Set `dedup_key` (e.g., `"url"` for posts, `"email"` for contacts) to prevent duplicates across scheduled runs
- **Schema**: `datasets` table (metadata) + `dataset_rows` table (JSONB data), both RLS-protected

## Scheduled Workflows

Execution uses Claude Code's built-in scheduler (`create_scheduled_task` MCP tool). nRev stores schedule metadata in `scheduled_workflows` table for dashboard display.

- **Register**: `POST /api/v1/schedules` — called when a schedule is set up
- **List**: `GET /api/v1/schedules` — dashboard reads this to show scheduled workflows
- Schedules appear in the Runs tab of the tenant dashboard

## Credit System

- **1 credit per operation** (search, enrich, scrape, etc.)
- **BYOK calls are always free** — no credits charged when using user's own API keys
- **Conversion**: ~$0.08 per credit (Growth tier midpoint)
- **Packages**: Starter 100/$9.99, Growth 500/$39.99, Scale 2000/$129.99
- Credit consumption bar shown in dashboard topbar across all tabs

## Security Rules

- NEVER log or expose API keys (platform or BYOK)
- NEVER bypass RLS — always set tenant context before queries
- NEVER store plaintext keys in the database
- JWT tokens should have short expiry (24h access, 30d refresh)
- All BYOK keys encrypted with KMS encryption context including tenant_id

## Database Migrations

Run in order against PostgreSQL (local Docker or AWS RDS):
```bash
psql -U nrv -d nrv -f migrations/001_tenants.sql
psql -U nrv -d nrv -f migrations/002_vault.sql
psql -U nrv -d nrv -f migrations/003_credits.sql
psql -U nrv -d nrv -f migrations/004_run_logs.sql
psql -U nrv -d nrv -f migrations/005_datasets.sql
psql -U nrv -d nrv -f migrations/006_scheduled_workflows.sql
psql -U nrv -d nrv -f migrations/007_dashboard_datasets.sql
```

All tables use RLS with tenant isolation. The `nrv_api` role has appropriate grants.

## Dashboard

Tenant dashboard at `/console/{tenant_slug}` — 6 tabs:
- **Keys**: BYOK key management with encrypted storage
- **Connections**: OAuth app connections via Composio
- **Usage**: Credit balance, consumption bar, per-operation costs, transaction ledger
- **Runs**: Workflow run logs with step-level data viewer + scheduled workflows section
- **Datasets**: Persistent dataset cards with column badges, row counts, data preview
- **Dashboards**: Create/view/share dashboards backed by datasets, with inline builder UI

### Hosted Dashboards

Dashboards are server-rendered HTML from dataset data + widget config. No S3 deployment needed.

- **Create**: Select a dataset, pick columns, name it → `POST /api/v1/dashboards`
- **View**: `/console/{tenant_id}/dashboards/{dashboard_id}` (authenticated)
- **Share**: `/d/{read_token}` (public, optional password protection)
- **Widgets**: `table` (data table), `metric` (count/sum/avg aggregation)
- Token-based access: `read_token` generated on creation, shareable without auth

### CLI Commands (17 command groups)

| Command | What It Does |
|---------|-------------|
| `nrv init` | One-command onboarding (auth + MCP registration) |
| `nrv auth` | Login, logout, status |
| `nrv status` | Account health check — auth, keys, credits, providers |
| `nrv enrich` | Person/company/batch enrichment with --dry-run |
| `nrv search` | People and company search |
| `nrv web` | Google search, scrape, crawl, extract |
| `nrv query` | SQL queries against data tables |
| `nrv table` | List/describe/modify data tables |
| `nrv keys` | BYOK API key management |
| `nrv credits` | Balance, history, topup |
| `nrv config` | Configuration management |
| `nrv dashboard` | Deploy/list/remove dashboards |
| `nrv datasets` | List, describe, query, export persistent datasets |
| `nrv schedules` | List, enable, disable scheduled workflows |
| `nrv feedback` | Submit feedback, bug reports, feature requests |
| `nrv setup-claude` | Install skills + CLAUDE.md for Claude Code |
| `nrv mcp` | Start MCP server on stdio |
