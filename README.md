# nrev-lite

**Agent-native GTM execution for Claude Code.**

`nrev-lite` turns Claude Code into a go-to-market co-pilot: search for prospects, enrich people and companies through multiple providers, trigger connected apps (Gmail, Slack, HubSpot, Salesforce, Instantly, and more), and keep persistent datasets and dashboards — all from a single CLI and a companion MCP server.

```bash
pip install nrev-lite
nrev-lite init
```

That's it. `nrev-lite init` handles authentication, gives you 200 free credits, and wires up the MCP server into Claude Code. Restart Claude Code and you're done.

---

## What you get

- **20+ CLI commands** for enrichment, search, web scraping, data tables, persistent datasets, OAuth app connections, credits, dashboards, scripts, and schedules.
- **MCP server for Claude Code** with tools for people/company enrichment, bulk Google search, Composio-powered app actions, persistent datasets, and workflow logging.
- **Multi-provider enrichment** via Apollo, RocketReach, PredictLeads, Parallel Web, RapidAPI Google, and more — automatic provider selection per use case.
- **OAuth app connections** (free, no credits) via Composio — Gmail, Slack, Microsoft Teams, Google Calendar, Zoom, HubSpot, Salesforce, Attio, Instantly, Linear, Notion, and dozens more.
- **BYOK support** — bring your own API keys and calls are free; otherwise pay in credits (~$0.08/credit).
- **Persistent datasets** — JSONB-backed storage with dedup keys, queryable and chainable across workflows.
- **Hosted dashboards & sites** — build HTML/CSS/JS sites backed by your datasets, deploy from Claude Code with one tool call.

---

## Quickstart

### 1. Install

```bash
pip install nrev-lite
```

### 2. Initialize

```bash
nrev-lite init
```

`init` opens your browser for Google OAuth, creates a tenant, drops **200 free credits** into your account, and registers the MCP server with Claude Code. Restart Claude Code to pick it up.

### 3. Verify

```bash
nrev-lite status
```

You should see your tenant, auth ✓, server online, and credit balance.

### 4. Try it

From the CLI:

```bash
# Enrich a person
nrev-lite enrich person --email john@stripe.com

# Search for people
nrev-lite search people --title "VP Sales" --company "Stripe" --limit 10

# Bulk enrich from a CSV
nrev-lite enrich batch --file contacts.csv

# Google search with bulk queries
nrev-lite web search --query "site:linkedin.com/jobs/view 'VP Engineering' fintech"
```

Or, from Claude Code, just ask in plain English:

> "Find 20 VP Sales leaders at Series B SaaS companies in California, enrich them with emails and phone numbers, and save to a dataset called `vp_sales_ca`."

Claude Code will call the right MCP tools, show you a plan with credit estimates, and execute once you approve.

---

## Bring your own API keys (free calls)

Platform keys are pre-configured — calls cost credits. Add your own and calls are free:

```bash
nrev-lite keys add apollo
nrev-lite keys add rocketreach
nrev-lite keys list
```

Keys are encrypted at rest (KMS in production, Fernet in dev) and scoped to your tenant via Row-Level Security.

---

## Connect your own apps (free actions)

```bash
# List connected apps
nrev-lite apps list

# Connect an app via OAuth (Gmail, Slack, HubSpot, Salesforce, etc.)
nrev-lite apps connect gmail

# Claude Code can then send emails, update CRMs, post to Slack, etc.
```

App actions are powered by Composio and are always free — no credits charged.

---

## CLI commands

| Command | Purpose |
|---------|---------|
| `nrev-lite init` | One-shot setup (auth + Claude Code MCP registration) |
| `nrev-lite auth` | `login`, `logout`, `status` |
| `nrev-lite status` | Account health check — auth, credits, providers |
| `nrev-lite enrich person/company/batch` | Row-level and bulk enrichment (supports `--dry-run`) |
| `nrev-lite search people/companies` | Title/company/domain/school/past-company filters |
| `nrev-lite web search/scrape/crawl/extract` | Google search, page scrape, crawl |
| `nrev-lite apps list/connect/disconnect` | OAuth app connections (Composio) |
| `nrev-lite keys add/list/remove` | BYOK API key management |
| `nrev-lite credits balance/history/topup` | Credit balance and spend |
| `nrev-lite datasets list/describe/query/export` | Persistent dataset management |
| `nrev-lite schedules list/enable/disable` | Scheduled workflow management |
| `nrev-lite scripts list/show/delete` | Saved workflow scripts |
| `nrev-lite tables list/describe` | Data tables |
| `nrev-lite query` | SQL-like queries against data tables |
| `nrev-lite dashboard` | Deploy, list, and remove hosted dashboards |
| `nrev-lite config` | Local CLI config |
| `nrev-lite feedback` | Submit bug reports and feature requests |
| `nrev-lite setup-claude` | Re-install Claude Code skills and MCP |
| `nrev-lite mcp` | Start the MCP server on stdio (used internally by Claude Code) |

Run `nrev-lite <command> --help` for per-command options.

---

## MCP tools for Claude Code

Claude Code gets a curated toolkit focused on workflow chaining: `nrev_search_people`, `nrev_enrich_person`, `nrev_enrich_company`, `nrev_google_search`, `nrev_search_patterns`, `nrev_scrape_page`, `nrev_create_and_populate_dataset`, `nrev_query_dataset`, `nrev_app_list/actions/execute`, `nrev_save_script`, `nrev_get_run_log`, `nrev_deploy_site`, and more.

Claude Code will show you a plan with per-step credit estimates and wait for your approval before running anything that costs credits.

---

## How credits work

- **1 credit per operation** (search, enrich, scrape, verify, research).
- **BYOK calls are always free.**
- **New accounts get 200 free credits.**
- Conversion: ~$0.08 per credit. Packages: Starter 100/$9.99, Growth 500/$39.99, Scale 2000/$129.99.
- Results are cached (7 days for enrichment, 1 hour for search) — cache hits don't re-charge.

Check balance and history:

```bash
nrev-lite credits balance
nrev-lite credits history
```

---

## Architecture

```
~/.nrev-lite/               → CLI config + credentials (created by `nrev-lite init`)
CLI (pip install nrev-lite) → thin Python client, MCP server, Claude Code skills
         │ HTTPS (JWT)
         ▼
nrev-lite API               → FastAPI server, provider proxy, credit billing,
                              BYOK vault, dataset store, dashboards
         │
         ├── PostgreSQL (Aurora) — RLS-isolated per tenant
         ├── Redis (ElastiCache)
         ├── KMS — BYOK key encryption
         └── External providers — Apollo, RocketReach, Composio, Parallel, etc.
```

The CLI never calls external providers directly. All routing, rate limiting, caching, and credit accounting happen server-side.

---

## Configuration

By default the CLI points at the nRev production API. Override for dev/staging:

```bash
export NREV_API_URL=https://nrev-lite-api.public.staging.nurturev.com
export NREV_PLATFORM_URL=https://staging.nrev.ai
# Optional — isolate credentials when switching environments:
export NREV_LITE_HOME=~/.nrev-lite-staging
```

Or persist in `~/.nrev-lite/config.toml`:

```toml
[server]
url = "https://nrev-lite-api.public.staging.nurturev.com"

[platform]
url = "https://staging.nrev.ai"
```

---

## Security

- Multi-tenant isolation via PostgreSQL Row-Level Security.
- BYOK keys encrypted at rest (AWS KMS in production, Fernet in dev) with tenant-scoped encryption context.
- JWT auth with short-lived access tokens (24h) and refresh tokens (30d).
- Platform provider keys live in AWS Secrets Manager — never in code, never in the database.
- API keys are never logged, returned by any endpoint, or exposed to the CLI.

---

## Requirements

- Python 3.10+
- Claude Code (for MCP integration — optional)
- A modern browser (for OAuth login)

---

## Links

- Homepage: https://nrev.ai
- Dashboard: https://app.nrev.ai
- API: https://nrev-lite-api.public.prod.nurturev.com

---

## License

MIT © nRev, Inc.
