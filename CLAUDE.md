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
  mcp/             → Optional MCP server
  utils/           → Display helpers, config management

server/            → FastAPI server (deployed to AWS)
  api/             → Route handlers (auth, execute, tables, credits, keys)
  models/          → SQLAlchemy models
  services/        → Business logic (auth, credits, execution)

migrations/        → SQL migration files for PostgreSQL
infra/             → AWS CDK infrastructure (future)
docs/              → Architecture documentation
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

## MCP Tools (17 tools)

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

## Security Rules

- NEVER log or expose API keys (platform or BYOK)
- NEVER bypass RLS — always set tenant context before queries
- NEVER store plaintext keys in the database
- JWT tokens should have short expiry (24h access, 30d refresh)
- All BYOK keys encrypted with KMS encryption context including tenant_id
