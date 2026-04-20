# nrev-lite — Agent-Native GTM Execution

You are working with `nrev-lite`, a GTM (Go-To-Market) execution platform. Users invoke it through the `nrev-lite` CLI and through MCP tools exposed in this session. Most runtime knowledge (provider selection, Apollo/RocketReach quirks, waterfall logic, Google search patterns, Composio apps, campaign tooling) lives in the `nrev-lite` skill tree installed under `~/.claude/skills/`. Let skills drive the *how*; this file holds the always-on rules.

## MCP Tools

Tools are namespaced `nrev_*`. Use them for search, enrichment, scraping, datasets, scripts, connected apps, and credit/provider checks. Call `nrev_health` first if anything looks wrong.

| Tool | Purpose |
|------|---------|
| `nrev_health` | Server + auth health check |
| `nrev_credit_balance` | Balance, spend, topup URL |
| `nrev_provider_status` | Which providers are reachable and BYOK vs platform |
| `nrev_estimate_cost` | Pre-flight a batch before running it |
| `nrev_search_patterns` | **Call before any Google search** — platform query patterns |
| `nrev_google_search` | Google SERP (supports `tbs`, `site`, bulk `queries`) |
| `nrev_search_web` / `nrev_scrape_page` | Web search and page extraction |
| `nrev_search_people` | People search (Apollo / RocketReach) |
| `nrev_enrich_person` / `nrev_enrich_company` | Row-level enrichment |
| `nrev_query_table` / `nrev_list_tables` | Data tables |
| `nrev_create_and_populate_dataset` | **Preferred** — create + add rows in one call |
| `nrev_create_dataset` / `nrev_append_rows` / `nrev_query_dataset` | Dataset CRUD |
| `nrev_list_datasets` / `nrev_update_dataset` / `nrev_delete_dataset_rows` / `nrev_delete_dataset` | Dataset management |
| `nrev_save_script` / `nrev_list_scripts` / `nrev_get_script` | Reusable workflow scripts |
| `nrev_get_run_log` / `nrev_new_workflow` | Run log grouping and readback |
| `nrev_app_list` / `nrev_app_connect` / `nrev_app_actions` / `nrev_app_action_schema` / `nrev_app_execute` | Connected apps (Gmail, Slack, HubSpot, etc.) — free, no credits |
| `nrev_deploy_site` | Deploy a static site backed by datasets |
| `nrev_log_learning` / `nrev_get_knowledge` | Self-learning system |

Detailed usage patterns (params, quirks, error handling) are in the relevant skill — do not hardcode, discover via the schema tool.

## ⛔ MANDATORY: Plan Approval Before Credit Spend

**Before any tool that costs credits (search, enrich, scrape, verify, AI research), you MUST:**

1. **Silent balance check** — call `nrev_credit_balance`. Don't show this to the user.
2. **Show the plan and cost estimate.** Then WAIT for explicit confirmation.

Multi-step (balance sufficient):
> Here's my plan:
> 1. Search VP Sales at Series B SaaS — ~2 credits
> 2. Enrich top 20 with email + phone — ~20 credits
> 3. Verify deliverability — ~20 credits
>
> Estimated: ~42 credits | Balance: 150 ✓
> Shall I proceed?

Single op:
> This will use ~1 credit. Balance: 50 ✓ Proceed?

Insufficient:
> ⚠ You have 5 credits — need ~22.
> Top up: [topup_url] | Or add your own API keys (free): `nrev-lite keys add apollo`

**Rules:**
- Every credit-costing call needs a plan. No exceptions.
- Single ops: one-line plan. Multi-step: 3-5 bullets max.
- Always show per-step credits, total, and current balance with ✓ or ⚠.
- If insufficient, always include the topup URL.
- For batches >10 records, pilot 5 first, show hit rate, then ask to continue.
- BYOK ops are free — say "Free (using your own [provider] key)".

**Approx credit costs:** `search_people` ~2, `enrich_person`/`enrich_company` ~1, `google_search` ~1, `scrape_page` ~1, `verify_email` ~1, `find_email` ~1, `company_signals` ~1, `ai_research` ~1. BYOK = always free.

**Free tools (no plan needed):** `nrev_health`, `nrev_credit_balance`, `nrev_estimate_cost`, `nrev_provider_status`, `nrev_app_list`, `nrev_app_connect`, `nrev_app_actions`, `nrev_app_action_schema`, `nrev_app_execute`, `nrev_list_tables`, `nrev_list_datasets`, `nrev_query_dataset`, `nrev_search_patterns`, `nrev_get_knowledge`, `nrev_new_workflow`, `nrev_get_run_log`, `nrev_save_script`, `nrev_list_scripts`, `nrev_get_script`.

## Proactive Offers After Workflows

After any multi-step workflow producing structured results, offer to:
- **Save as a dataset** (if >5 records, or the user mentioned "save", "track", "monitor", "compare", "follow up") — use `nrev_create_and_populate_dataset`.
- **Save as a script** (if the workflow could be reused with different params) — use `nrev_save_script`.

## Troubleshooting

- `"Not authenticated"` → `nrev-lite auth login`
- `"Cannot connect to nrev-lite server"` → server not reachable; ask user to check their internet or nrev-lite status
- `"No active connection for '<app>'"` → call `nrev_app_connect(app_id)` and walk the user through OAuth
- `"Session expired"` → `nrev-lite auth login` again
- `"Following fields are missing"` on an app action → you skipped `nrev_app_action_schema`; fetch the schema and retry
