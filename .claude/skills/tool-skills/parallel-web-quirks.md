# Parallel Web — Tool Skill Reference

## What Parallel Web Is

Parallel Web is your **primary web research and extraction tool** — the best available for AI-native web access. Use it any time you need to:
- Research a topic, company, person, or market from the open web
- Extract structured data from any webpage (including pages behind sign-up walls)
- Build unconventional prospect lists (businesses not in B2B databases)
- Enrich records with web-sourced intelligence
- Monitor the web for changes over time
- Discover entities matching complex criteria at scale

It is NOT limited to "non-standard" use cases. It replaces the traditional search → scrape → parse pipeline with AI-native APIs that return clean, structured, cited output.

## When to Use Which API

Parallel has **7 distinct APIs**. Picking the right one matters:

| I need to... | Use this API | Why |
|---|---|---|
| Find relevant web content for a question | **Search** | Returns ranked URLs with LLM-optimized excerpts |
| Get content from URLs I already have | **Extract** | Converts pages to clean markdown, cheapest API |
| Enrich structured records (company/person data) | **Task** | AI + web search, returns structured JSON with citations |
| Deep-dive research on a topic | **Task** (pro/ultra) | Multi-source synthesis with reasoning chain |
| Find all entities matching criteria | **FindAll** | "All bakeries in San Jose on Instagram" |
| Build a prospect list from scratch | **FindAll** → **Task** | FindAll discovers, Task enriches |
| Track ongoing changes (news, pricing, hiring) | **Monitor** | Scheduled queries with webhook notifications |
| Build a chat interface with web grounding | **Chat** | OpenAI-compatible, web-backed completions |

## API #1: Search

**When:** You need web content but don't have specific URLs yet.

**Endpoint:** `POST /v1beta/search`

**Key Parameters:**
| Param | Type | Notes |
|---|---|---|
| `objective` | string | Natural language — what you're looking for. **Always provide this.** |
| `search_queries` | string[] | Keyword queries. Best results when combined with objective. |
| `mode` | string | `"fast"` (~1s), `"agentic"` (concise, for multi-step loops), `"one-shot"` (comprehensive, default) |
| `max_results` | int | 1-40. Fewer = lower latency. |
| `source_policies` | object | Domain include/exclude, date filtering |
| `fetch_policy.max_age_seconds` | int | Freshness control (min 600s) |

**Returns:** Ranked results with `url`, `title`, `publish_date`, `excerpts` (markdown).

**Pricing:** `base` $4/1K requests (1-3s) | `pro` $9/1K (45-70s, deeper)

**Rate limit:** 600 req/min

**Best practices:**
- Provide BOTH `objective` AND `search_queries` (2-3 variations) for best results
- Use `"fast"` mode for real-time interactions, `"agentic"` for multi-step agent loops
- Excerpts are already LLM-optimized — no need to re-process
- NOT for discovery of known URLs — use Extract for that

## API #2: Extract

**When:** You already have URLs and need their content.

**Endpoint:** `POST /v1beta/extract`

**Key Parameters:**
| Param | Type | Notes |
|---|---|---|
| `urls` | string[] | **Required.** Public URLs to process. |
| `objective` | string | Guides excerpt selection — what info do you want from the page? |
| `excerpts` | bool | Focused content snippets aligned to objective |
| `full_content` | bool | Entire page as clean markdown |

**Returns:** Per-URL `title`, `publish_date`, `excerpts`, `full_content`.

**Pricing:** $0.001 per 1,000 URLs — extremely cheap.

**Rate limit:** 600 req/min

**When to use:**
- Converting web pages to markdown for LLM consumption
- Scraping Instagram profiles, Yelp listings, Google Maps pages — any page with data
- Getting specific info from known pages (set `objective` + `excerpts: true`)
- Full-page content capture (`full_content: true`)

**Critical quirk:** Extract does NOT search. It only processes URLs you give it. Use Search or Google to find URLs first, then Extract to get content.

## API #3: Task (Deep Research & Enrichment)

**When:** You need AI-powered research or structured enrichment with citations.

**Endpoint:** `POST /v1/tasks/runs`

**Key Parameters:**
| Param | Type | Notes |
|---|---|---|
| `input` | string or object | Question (text) or structured data to enrich. Max 15,000 chars. |
| `processor` | string | Tier — determines depth, cost, latency (see table below) |
| `task_spec.output_schema` | object | JSON Schema for structured output, `{"type": "text"}` for reports, `{"type": "auto"}` for automatic |
| `task_spec.input_schema` | object | Schema describing your input fields (for enrichment) |

**Processor Tiers:**
| Processor | Cost/1K | Latency | Best For |
|---|---|---|---|
| `lite` | $5 | 10-60s | Simple lookups, ~2 fields |
| `base` | $10 | 15-100s | Basic enrichment, ~5 fields |
| `core` | $25 | 1-5min | Multi-field enrichment, ~10 fields |
| `core2x` | $50 | 1-10min | Complex enrichment, ~10 fields |
| `pro` | $100 | 2-10min | Deep research, ~20 fields |
| `pro-fast` | $100 | Faster | Same quality as pro, 2-5x faster |
| `ultra` | $300 | 5-25min | Comprehensive analysis |
| `ultra-fast` | $300 | Faster | Same quality as ultra, 2-5x faster |
| `ultra2x` | $600 | 5-50min | Extended research |
| `ultra4x` | $1,200 | 5-90min | Extensive research |
| `ultra8x` | $2,400 | 5min-2hr | Maximum depth |

**Rate limit:** 2,000 req/min

**Output includes Basis framework:**
- `citations` — source URLs, titles, excerpts for each field
- `reasoning` — how the conclusion was reached
- `confidence` — `"high"`, `"medium"`, or `"low"` per field

**Result delivery:** Polling (`retrieve()` → `result()`), Webhooks, or SSE streaming.

**Enrichment pattern example:**
```json
{
  "input": {"company_name": "Acme Corp", "website": "acme.com"},
  "task_spec": {
    "input_schema": {
      "type": "object",
      "properties": {
        "company_name": {"type": "string"},
        "website": {"type": "string"}
      }
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "employee_count": {"type": "string"},
        "founded": {"type": "string"},
        "funding": {"type": "string"},
        "key_challenges": {"type": "string"}
      }
    }
  },
  "processor": "core"
}
```

**When to use Task vs Search+Extract:**
- Use **Task** when you need structured output, citations, and AI synthesis (enrichment, research reports)
- Use **Search+Extract** when you need raw web content or specific page data
- Task can access pages behind sign-up walls via authenticated page access

**Choosing a processor:**
- `lite`/`base` — simple fact lookups (founding year, employee count, 1-5 fields)
- `core` — standard GTM enrichment (company profile, tech stack, funding, ~10 fields)
- `pro`/`pro-fast` — competitive analysis, market research reports
- `ultra+` — comprehensive due diligence, multi-source deep dives
- Always prefer `-fast` variants for interactive use cases

## API #4: Task Group (Batch Processing)

**When:** Running hundreds or thousands of Task operations.

**Endpoints:**
- `POST /v1beta/tasks/groups` — create group
- `POST /v1beta/tasks/groups/{id}/runs` — add runs to group
- `GET /v1beta/tasks/groups/{id}/events` — stream results (SSE)

**Key capability:** Dynamic expansion — add new tasks to active groups mid-execution.

**When to use:** Bulk CRM enrichment, batch due diligence, competitive intelligence at scale. Don't manually loop Task API — use Task Groups for 10+ records.

## API #5: FindAll (Entity Discovery)

**When:** "Find me all X that match Y" — web-scale entity discovery.

**This is the key API for unconventional list building.** Examples:
- "All D2C skincare brands selling on Instagram in California"
- "All bakeries listed in San Jose on Instagram"
- "All Shopify stores selling pet products with over 10K followers"
- "All Series A fintech startups in NYC hiring engineers"

**Four-step workflow:**

**Step 1 — Ingest** (`POST /v1beta/findall/ingest`)
- Input: `objective` (natural language)
- Returns: `entity_type`, `match_conditions` (structured schema you can customize)

**Step 2 — Create Run** (`POST /v1beta/findall/runs`)
- `objective`, `entity_type`, `match_conditions` (from Step 1, customized)
- `generator`: `"preview"` (~10 candidates) | `"base"` | `"core"` | `"pro"` (most thorough)
- `match_limit`: max matched entities to return

**Step 3 — Poll** (`GET /v1beta/findall/runs/{id}`)
- Returns status and metrics (generated/matched counts)

**Step 4 — Results** (`GET /v1beta/findall/runs/{id}/result`)
- Candidates with: `name`, `url`, `description`, `match_status`, structured `output`, full `basis` with citations

**Pricing:**
| Generator | Fixed Cost | Per Match |
|---|---|---|
| `preview` | $0.10 | $0.00 |
| `base` | $0.25 | $0.03 |
| `core` | $2.00 | $0.15 |
| `pro` | $10.00 | $1.00 |

**Best practice:** Always start with `preview` to validate your schema (10 candidates, $0.10), then scale up with `core` or `pro`.

**Critical for GTM workflows:** When building lists of businesses NOT in Apollo/RocketReach (local businesses, D2C brands, niche verticals), FindAll discovers them and Task enriches them. Do NOT route these through `nrev_enrich_company` — Apollo/RocketReach won't have them.

## API #6: Chat (Web-Grounded Conversations)

**When:** Building chat interfaces that need web-backed answers.

**Endpoint:** `POST /v1beta/chat/completions` (OpenAI-compatible)

**Models:**
| Model | Cost/1K | Latency | Citations |
|---|---|---|---|
| `speed` | $5 | ~3s | No |
| `lite` | $5 | 10-60s | Yes |
| `base` | $10 | 15-100s | Yes |
| `core` | $25 | 1-5min | Yes |

**Rate limit:** 300 req/min

**Note:** `temperature`, `top_p`, `max_tokens` etc. are accepted but **ignored**.

## API #7: Monitor (Continuous Web Tracking)

**When:** Ongoing monitoring — not one-time research.

**Endpoint:** `POST /v1alpha/monitors` (Alpha)

**Key Parameters:**
| Param | Type | Notes |
|---|---|---|
| `query` | string | Intent-driven natural language (NOT keywords) |
| `frequency` | string | `"1h"`, `"1d"`, `"1w"`, up to `"30d"` |
| `webhook` | object | `{url, event_types}` for push notifications |
| `output_schema` | object | Flat JSON Schema, 3-5 string/enum properties max |

**Pricing:** $0.003 per 1,000 executions

**Use cases:** Competitor news, pricing changes, job posting signals, regulatory updates, deal watchlists.

**NOT for:** Historical research (use Task for that).

## Parallel vs Firecrawl

| Dimension | Parallel | Firecrawl |
|---|---|---|
| **Core strength** | AI web research & intelligence | Web scraping & crawling |
| **Search** | Built-in Search API with custom index | No native search |
| **Deep research** | Task API, 9 processor tiers, Basis citations | `/agent` endpoint, less depth |
| **Entity discovery** | FindAll API | No equivalent |
| **Monitoring** | Monitor API | No equivalent |
| **Authenticated/paywall pages** | Task API supports it | Browser sandbox |
| **Site-wide crawling** | No endpoint for this | Full crawl with sitemap discovery |
| **Anti-bot bypass** | Uses own index + crawlers | Handles JS rendering, anti-bot |
| **Self-hosted** | No | Open-source option |
| **Structured output** | JSON Schema on Task/Chat/Monitor/FindAll | JSON Schema on scrape/extract |
| **Citations** | Every output includes citations + confidence + reasoning | No citation framework |

**Bottom line:** Parallel is better for research, enrichment, discovery, and monitoring. Firecrawl is better for raw site crawling and anti-bot scraping.

## GTM Decision Tree — Which API for Which Workflow

```
Need to research a company/person/topic?
├── Have specific URLs → Extract (cheapest, fastest)
├── Need to find relevant content → Search
├── Need structured enrichment with citations → Task (core)
└── Need deep competitive/market analysis → Task (pro/ultra)

Need to build a prospect list?
├── Standard B2B companies → Apollo/RocketReach (via nrev_enrich)
├── Non-standard businesses (local, D2C, niche) → FindAll → Task
├── Alumni network → RocketReach previous_employer → Task for context
└── Hiring signals → Search (job board queries) → Extract

Need ongoing intelligence?
├── One-time snapshot → Task
└── Continuous tracking → Monitor

Need to process many records?
├── 1-9 records → Individual Task calls
└── 10+ records → Task Group (batch)
```

## Quirks & Gotchas

1. **Extract does NOT search** — it only processes URLs you provide. Search first, Extract second.
2. **Task is async** — it returns immediately, you must poll or use webhooks/SSE for results.
3. **FindAll has a 4-step workflow** — don't skip Ingest (Step 1), it generates your schema.
4. **Monitor output_schema is limited** — flat structure only, 3-5 properties, string/enum types only.
5. **Chat ignores most parameters** — `temperature`, `top_p`, `max_tokens` are accepted but have no effect.
6. **Search `max_age_seconds` minimum is 600** — you can't force fully fresh results faster than 10 minutes.
7. **Task `input` max is 15,000 chars** — for larger inputs, break into multiple runs.
8. **FindAll `preview` is free per-match** — always validate your schema before scaling up.
9. **`-fast` variants exist for pro and ultra** — same quality, 2-5x faster, same price. Always prefer these for interactive use.
10. **20K free requests to start** — no credit card required.

## Rate Limits Summary

| API | Rate Limit |
|---|---|
| Task | 2,000 req/min |
| Search | 600 req/min |
| Extract | 600 req/min |
| Chat | 300 req/min |

## Authentication

```
x-api-key: $PARALLEL_API_KEY
```
Or: `Authorization: Bearer $PARALLEL_API_KEY`

## nrev-lite Integration

In nrev-lite, Parallel Web is accessed through:
- `nrev_scrape_page` — wraps Extract API (provide URL + objective)
- `nrev_search_web` — wraps Search API
- For Task, FindAll, Monitor — use `nrev_execute_action` or direct API calls through the server proxy
