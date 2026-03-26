# nrev-lite — Agent-Native GTM Execution Platform

You are working on nrev-lite, a cloud-first GTM (Go-To-Market) platform by nRev.

## Architecture

nrev-lite uses a split architecture:
- **Client (this repo, `src/nrev_lite/`)**: Thin CLI + Claude Code skills with GTM intelligence
- **Server (`server/`)**: FastAPI API gateway, provider proxy, credit billing, PostgreSQL database
- **Infrastructure**: AWS (Aurora Serverless v2, ECS Fargate, Redis, S3, KMS)

## Project Structure

```
src/nrev_lite/           → Python package installed by users (CLI + skills)
  cli/             → Click CLI commands (nrev-lite auth, nrev-lite enrich, etc.)
  client/          → HTTP client that talks to nrev-lite server
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
nrev-lite auth login
nrev-lite enrich person --email test@example.com
```

## MCP Tools (33 tools)

| Tool | What It Does |
|------|-------------|
| `nrev_health` | Quick health check — verifies server + auth are working |
| `nrev_new_workflow` | Start a new workflow within the session (for run log grouping) |
| `nrev_search_web` | Google web search via RapidAPI |
| `nrev_scrape_page` | Extract content from URLs via Parallel Web |
| `nrev_google_search` | Google SERP with all operators, tbs, site, bulk queries |
| `nrev_search_patterns` | **Call BEFORE Google search** — get platform-specific query patterns |
| `nrev_search_people` | People search via Apollo/RocketReach (titles, companies, alumni) |
| `nrev_enrich_person` | Person enrichment (email/name/LinkedIn) |
| `nrev_enrich_company` | Company enrichment (domain/name) |
| `nrev_query_table` | Query data tables with filters |
| `nrev_list_tables` | List available tables |
| `nrev_create_and_populate_dataset` | **Preferred**: Create a dataset AND add rows in one call |
| `nrev_create_dataset` | Create an empty persistent dataset (use create_and_populate instead when you have data) |
| `nrev_append_rows` | Append/upsert rows to an existing persistent dataset |
| `nrev_query_dataset` | Query rows from a persistent dataset with filters |
| `nrev_list_datasets` | List all persistent datasets |
| `nrev_update_dataset` | Update dataset name, description, columns, or dedup_key |
| `nrev_delete_dataset_rows` | Delete specific rows or clear all rows from a dataset |
| `nrev_delete_dataset` | Archive (soft-delete) a dataset |
| `nrev_estimate_cost` | Estimate credit cost before executing (call before large batches) |
| `nrev_get_run_log` | Read back workflow run logs with results and column metadata |
| `nrev_deploy_site` | Deploy a static HTML/CSS/JS site backed by datasets |
| `nrev_credit_balance` | Check credit balance and spend |
| `nrev_provider_status` | Check provider availability |
| `nrev_app_list` | List connected apps (Gmail, Slack, HubSpot, CRM, etc.) |
| `nrev_app_connect` | Connect a new app via OAuth — returns URL for user to authorize in browser |
| `nrev_app_actions` | Discover available actions for a connected app |
| `nrev_app_action_schema` | Get parameter schema for a specific app action |
| `nrev_app_execute` | Execute an action on a connected app (free, no credits) |
| `nrev_save_script` | Save a parameterized workflow as a reusable script |
| `nrev_list_scripts` | List all saved scripts |
| `nrev_get_script` | Load a saved script by name/slug for inspection or execution |
| `nrev_log_learning` | Log a workflow discovery (URL pattern, API quirk, hit rate, etc.) for admin review |
| `nrev_get_knowledge` | Look up approved knowledge by category and key |

### ⛔ MANDATORY: Plan Approval Before Execution

**NEVER execute a multi-step nrev-lite workflow without showing a plan and getting user approval first.**

Before calling any nrev-lite tool that costs credits (search, enrich, scrape, etc.):
1. Call `nrev_credit_balance` first (silently — don't show this as a step)
2. Show a 3-5 bullet plan with estimated credits per step and total
3. Show balance check: "Balance: X credits ✓" or "⚠ Insufficient credits (have X, need ~Y)"
4. If insufficient: include the `topup_url` from the balance response so the user can add credits
5. Ask "Shall I proceed?" and WAIT
6. Only execute after the user confirms

This applies to every session, every workflow, no exceptions.

### Troubleshooting

If any tool returns an error:
- `"Not authenticated"` → Run `nrev-lite auth login` in the terminal
- `"Cannot connect to nrev-lite server"` → Start the server: `cd server && uvicorn server.app:app --reload`
- `"No active connection for 'gmail'"` → User must connect the app at the dashboard
- `"Session expired"` → Run `nrev-lite auth login` again

## Google Search — Dynamic Pattern Discovery

**NEVER guess Google search query patterns for specific platforms.** Always discover dynamically:

1. `nrev_search_patterns(platform="linkedin_jobs")` — get exact site: prefix, query templates, tips
2. `nrev_search_patterns(use_case="hiring_signals")` — get GTM-optimized query patterns
3. `nrev_google_search(query=..., tbs=..., site=...)` — execute with the correct patterns

### Key Parameters
- **tbs**: Time filter. Friendly: `hour`, `day`, `week`, `month`, `year`. Raw: `qdr:h2` (2 hours), `qdr:d3` (3 days), `qdr:m3` (3 months). Custom: `cdr:1,cd_min:MM/DD/YYYY,cd_max:MM/DD/YYYY`
- **site**: Convenience site restriction (e.g. `linkedin.com/jobs/view`)
- **queries**: Bulk search — multiple queries run concurrently

### Why This Matters
Each platform has specific URL structure nuances (e.g. `linkedin.com/jobs/view` not `/jobs/search`, `x.com/*/status` for tweets). These patterns live on the server and evolve without client updates.

## Apps vs Data Providers — Two Distinct Systems

nrev-lite has two types of external integrations. Understanding which to use is critical:

**Apps** (dashboard Apps tab) — Tools the user already uses that they connect via OAuth. Used to **fetch data from or write data to** the user's own tools (read emails, create calendar events, update CRM, send outreach). Actions are **free** (no credits). Powered by Composio.

**Data Providers** (dashboard Data Providers tab) — API services that nrev-lite calls for enrichment, search, scraping, verification, and AI research. Used when Claude needs **bulk data, row-level enrichment, or AI processing**. These cost credits (unless BYOK).

| Need | Use |
|------|-----|
| Read/send user's emails | **Apps** (gmail) |
| Enrich a list of contacts with phone numbers | **Data Providers** (apollo, rocketreach) |
| Check user's calendar | **Apps** (google_calendar) |
| Google search for company info | **Data Providers** (rapidapi) |
| Push leads to user's CRM | **Apps** (hubspot, salesforce) |
| Scrape a website | **Data Providers** (parallel) |
| Send cold email campaign | **Apps** (instantly, smartlead) |
| Verify email deliverability | **Data Providers** (zerobounce) |
| Summarize a meeting transcript | **Apps** (fireflies) |
| AI research on each row of data | **Data Providers** (perplexity, openai) |

## Apps (via Composio)

Tenants can OAuth-connect apps through the dashboard or CLI (`nrev-lite connect <app>`). App actions are **free** — no credits charged.

### Intent-to-App Mapping

**When the user mentions any of these keywords, they likely need a connected app.** Check for a system MCP tool first (e.g., `slack_send_message`), then fall back to nrev-lite Composio.

| User says... | app_id | Category |
|---|---|---|
| email, send email, inbox, mail, draft, Gmail | `gmail` | communication |
| Slack, message, channel, DM, send to Slack | `slack` | communication |
| Teams, Microsoft Teams, teams message | `microsoft_teams` | communication |
| calendar, meeting, schedule, events, free time | `google_calendar` | calendar |
| Calendly, booking link | `calendly` | calendar |
| Cal.com, scheduling link | `cal_com` | calendar |
| Zoom, video call, webinar, recording | `zoom` | meetings |
| meeting notes, transcript, Fireflies | `fireflies` | meetings |
| spreadsheet, Google Sheet, add row, update sheet | `google_sheets` | data |
| document, Google Doc, write doc | `google_docs` | data |
| Drive, upload file, Google Drive | `google_drive` | data |
| Airtable, base, airtable record | `airtable` | data |
| CRM, deal, contact record, pipeline, HubSpot | `hubspot` | crm |
| Salesforce, opportunity, lead, SFDC | `salesforce` | crm |
| Attio, CRM record | `attio` | crm |
| cold email, outreach campaign, Instantly | `instantly` | outreach |
| Smartlead, inbox rotation | *separate MCP* | outreach |
| task, ticket, issue, Linear | `linear` | project |
| Notion, notion page, notion doc | `notion` | project |
| ClickUp, clickup task | `clickup` | project |
| Asana, asana task | `asana` | project |
| product analytics, feature flags, PostHog | `posthog` | analytics |

### Proactive Routing Rule

When the user's request matches any keyword above:
1. **Check system MCP tools first** — look for tools like `slack_send_message`, `clickup_create_task`, etc. If available, use them directly (faster, already authenticated).
2. **If no system MCP tool**, call `nrev_app_list()` to check if the app is connected. If connected → proceed with the 5-step discovery flow below.
3. **If not connected**, call `nrev_app_connect(app_id)` to initiate OAuth — show the returned URL to the user, wait for them to complete authorization, then call `nrev_app_list` to confirm.

### How to Execute Actions (Dynamic Discovery)

**Do NOT hardcode action names or params.** Always discover dynamically:

1. `nrev_app_list` — check which apps are connected
2. **If not connected**: `nrev_app_connect(app_id)` — returns an OAuth URL; show it to the user, wait for them to authorize, then call `nrev_app_list` again to confirm
3. `nrev_app_actions(app_id)` — discover available actions for that app
4. `nrev_app_action_schema(action_name)` — get exact parameter names, types, and required flags. **This is non-optional** — param names are NOT guessable (e.g. `text_to_insert` not `text`, `markdown_text` not `content`, `ranges` must be an array not a string)
5. `nrev_app_execute(app_id, action, params)` — execute with the correct params

### Error Handling for Apps

1. If `nrev_app_list` shows no active connection → use `nrev_app_connect` to set it up in-session
2. If action returns `status: error` → check the `error` field for details
3. If action returns `"Following fields are missing"` → you skipped `nrev_app_action_schema`. Go back and check exact param names.
4. Common failure: app connected but missing required OAuth scopes → user must reconnect
5. If a **system MCP tool** exists for the same app (e.g., Slack MCP), prefer the system MCP — it's faster and already authenticated

## Persistent Datasets

Datasets are long-lived JSONB document stores that workflows write to over time. They support scheduled workflow accumulation (e.g., daily LinkedIn monitoring appends new posts without duplicating old ones).

- **Create + Populate**: `nrev_create_and_populate_dataset(name, columns, dedup_key, rows)` — **preferred** single-call method to create a dataset and add rows
- **Create (empty)**: `nrev_create_dataset(name, columns, dedup_key)` — idempotent, returns existing if slug matches
- **Append**: `nrev_append_rows(dataset_ref, rows)` — upserts via SHA256 hash of dedup_key value
- **Query**: `nrev_query_dataset(dataset_ref, filters, limit, offset)` — supports key-value filters, sorting, pagination
- **Update**: `nrev_update_dataset(dataset_ref, name, description, columns, dedup_key)` — update metadata
- **Delete rows**: `nrev_delete_dataset_rows(dataset_ref, row_ids, all_rows)` — remove specific rows or clear all
- **Delete dataset**: `nrev_delete_dataset(dataset_ref)` — soft-delete (archive)
- **Dedup**: Set `dedup_key` (e.g., `"url"` for posts, `"email"` for contacts) to prevent duplicates across scheduled runs
- **Schema**: `datasets` table (metadata) + `dataset_rows` table (JSONB data), both RLS-protected

### Dataset Proactive Recommendation Rules

**After ANY multi-step workflow (2+ tool calls) that produces structured results, ALWAYS offer to save as a dataset.**

Trigger conditions (offer dataset even if user doesn't ask):
- Workflow produced >5 structured records (contacts, companies, URLs, posts)
- User mentions: "save", "track", "monitor", "follow up", "ongoing", "compare", "later"
- Results will feed into another workflow (campaigns, CRM push, outreach)
- Data was scraped or searched and might need periodic refresh
- User is building any kind of list

How to offer:
> "Want me to save these [N] results to a persistent dataset? You'll be able to query them later, build a dashboard, or run scheduled workflows that add to them."

If yes, use `nrev_create_and_populate_dataset` (preferred single-call method).

This follows the same pattern as "offer to save as script" — both should be offered after successful workflows.

## Scheduled Workflows

Execution uses Claude Code's built-in scheduler (`create_scheduled_task` MCP tool). nRev stores schedule metadata in `scheduled_workflows` table for dashboard display.

- **Register**: `POST /api/v1/schedules` — called when a schedule is set up
- **List**: `GET /api/v1/schedules` — dashboard reads this to show scheduled workflows
- Schedules appear in the Runs tab of the tenant dashboard

## Scripts (Reusable Workflows)

Scripts are parameterized workflow definitions saved from successful workflow runs. They capture the exact tool call sequence with declared parameters that users can change at run time.

- **Save**: After a workflow completes, Claude offers to save it as a script via `nrev_save_script`
- **List**: `nrev_list_scripts` (MCP) or `nrev-lite scripts list` (CLI)
- **Load & Run**: `nrev_get_script(slug)` loads the definition; Claude executes each step using existing MCP tools
- **Parameters**: Use `{{param_name}}` placeholders; `for_each: "step_N.results"` for iteration over previous step output
- **Storage**: `scripts` table (JSONB steps + parameters), RLS-protected per tenant
- **API**: CRUD at `/api/v1/scripts`, run recording at `/api/v1/scripts/{slug}/run`

## Self-Learning System

When Claude encounters an unknown platform, API quirk, or data pattern during a workflow, it follows an **Experimental Protocol**: probe broadly, analyze results, refine the approach, then log the discovery.

- **Log**: `nrev_log_learning(category, discovery, evidence)` — submits a learning for admin review
- **Lookup**: `nrev_get_knowledge(category, key)` — checks if approved knowledge exists before guessing
- **Categories**: `search_pattern`, `api_quirk`, `enrichment_strategy`, `scraping_pattern`, `data_mapping`, `provider_behavior`
- **Admin review**: `/admin/learning-logs` — admins approve/reject/merge learnings
- **Dynamic patterns**: Approved `search_pattern` learnings are merged into `nrev_search_patterns` responses automatically
- **Storage**: `learning_logs` table (submissions) + `dynamic_knowledge` table (approved knowledge)
- **Admin auth**: Set `ADMIN_TENANT_IDS` env var (comma-separated tenant IDs)

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
psql -U nrev_lite -d nrev_lite -f migrations/001_tenants.sql
psql -U nrev_lite -d nrev_lite -f migrations/002_vault.sql
psql -U nrev_lite -d nrev_lite -f migrations/003_credits.sql
psql -U nrev_lite -d nrev_lite -f migrations/004_run_logs.sql
psql -U nrev_lite -d nrev_lite -f migrations/005_datasets.sql
psql -U nrev_lite -d nrev_lite -f migrations/006_scheduled_workflows.sql
psql -U nrev_lite -d nrev_lite -f migrations/007_dashboard_datasets.sql
psql -U nrev_lite -d nrev_lite -f migrations/008_hosted_apps.sql
psql -U nrev_lite -d nrev_lite -f migrations/009_scripts.sql
psql -U nrev_lite -d nrev_lite -f migrations/010_learning_logs.sql
psql -U nrev_lite -d nrev_lite -f migrations/011_learning_prompt.sql
```

All tables use RLS with tenant isolation. The `nrev_api` role has appropriate grants.

## Dashboard

Tenant dashboard at `/console/{tenant_slug}` — 6 tabs:
- **Data Providers**: BYOK API key management for enrichment, search, and AI services
- **Apps**: OAuth app connections via Composio (Gmail, Slack, Zoom, CRM, outreach, etc.)
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

### Hosted Sites

Users build HTML/CSS/JS sites in Claude Code using datasets as their database, then deploy to nrev-lite:

- **Deploy**: `nrev_deploy_site(name, files, dataset_ids)` MCP tool
- **Serve**: `/sites/{site_token}/` — public URL, no auth required
- **CRUD**: Site JS gets `window.NRV_APP_TOKEN` + `window.NRV_DATASETS_URL` injected for data access
- **Scoped**: Site tokens can only access their connected datasets

### CLI Commands (20 command groups)

| Command | What It Does |
|---------|-------------|
| `nrev-lite init` | One-command onboarding (auth + MCP registration) |
| `nrev-lite auth` | Login, logout, status |
| `nrev-lite apps` | List, connect, disconnect OAuth apps (Gmail, Slack, HubSpot, etc.) |
| `nrev-lite status` | Account health check — auth, keys, credits, providers |
| `nrev-lite enrich` | Person/company/batch enrichment with --dry-run |
| `nrev-lite search` | People and company search |
| `nrev-lite web` | Google search, scrape, crawl, extract |
| `nrev-lite query` | SQL queries against data tables |
| `nrev-lite table` | List/describe/modify data tables |
| `nrev-lite keys` | BYOK API key management |
| `nrev-lite credits` | Balance, history, topup |
| `nrev-lite config` | Configuration management |
| `nrev-lite dashboard` | Deploy/list/remove dashboards |
| `nrev-lite datasets` | List, describe, query, export persistent datasets |
| `nrev-lite schedules` | List, enable, disable scheduled workflows |
| `nrev-lite scripts` | List, show, delete saved workflow scripts |
| `nrev-lite feedback` | Submit feedback, bug reports, feature requests |
| `nrev-lite setup-claude` | Install skills + CLAUDE.md for Claude Code |
| `nrev-lite mcp` | Start MCP server on stdio |
