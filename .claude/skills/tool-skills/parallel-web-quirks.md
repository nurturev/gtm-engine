# Parallel Web — Tool Skill Reference

## What Parallel Web Is

Parallel Web is the **anti-bot / structured-research / SERP-backup** layer for the consultant. Use it for:
- Reading sites the agent's native web tool can't access (LinkedIn, Reddit, Twitter, anti-bot pages, paywalls)
- Converting any URL to clean markdown for LLM consumption
- Structured enrichment with citations and confidence scores
- Lightweight web-grounded research where Task is overkill
- Backup SERP when RapidAPI Google fails

**4 APIs are in scope for the consultant:** Extract · Task · Chat · Search (backup).
Other Parallel APIs (Task Group, FindAll, Monitor) are **Workflow Builder territory** — not used in the consultant's chat flow.

---

## Decision Flow — When to Use What

```
Need web data?
│
├── 1. Can the agent's native web tool access the site?
│   ├── YES (open web pages) → Agent's web tool (default)
│   └── NO (LinkedIn, Reddit, Twitter, anti-bot, paywall) → Parallel Web ↓
│
├── 2. SERP / discovery query?
│   ├── Default → RapidAPI Google (always first, all purposes)
│   └── Backup (RapidAPI 429 / empty) → Parallel Search
│
└── 3. Content retrieval / research?
    ├── Single page, just need visible data → Extract
    │   (e.g., "extract all company names listed on this ProductHunt URL")
    │
    ├── Single page, need to follow links from it → Task
    │   (e.g., "extract all companies on this ProductHunt URL with their websites")
    │
    ├── Multi-source synthesis, citations, structured enrichment → Task
    │
    ├── Lightweight / cost-sensitive research where Task is overkill → Chat
    │   (Task-lite — skip citations/schema overhead when you don't need them)
    │
    └── Output is qualitative / SERP filters can't express it → Task
        Heuristic: ≥5 keyword expansions needed, OR sentiment dimension,
        OR entity-relationship dimension
        (e.g., "find all criticisms of ABC company on LinkedIn")
```

---

## Quick API Selector

| I need to... | API | Why |
|---|---|---|
| Get content from URLs I already have (clean markdown, anti-bot) | **Extract** | Cheapest, fastest path from URL to text |
| Enrich structured records with cited, structured output | **Task** | AI + web search + Basis citations |
| Do deep research synthesizing multiple sources | **Task** (pro/ultra tiers) | Multi-source reasoning chain |
| Lightweight web-grounded answer where Task is too heavy | **Chat** | OpenAI-compatible, faster, cheaper |
| Find URLs (default SERP) | **RapidAPI Google** | Default — Parallel Search is backup only |
| Find URLs (when RapidAPI fails) | **Search** | Operational fallback |

---

## API #1: Extract

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
- Single-page list extraction where you don't need to follow links (e.g., "all companies listed on this ProductHunt URL")

**Critical quirk:** Extract does NOT search. It only processes URLs you give it. Use RapidAPI Google (or Parallel Search as backup) to find URLs first, then Extract to get content.

---

## API #2: Task (Deep Research & Enrichment)

**When:** You need AI-powered research, structured enrichment with citations, or to follow links from a single page.

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

**When to use Task vs Extract:**
- **Single page, just visible data** → Extract (cheaper, faster)
- **Single page but need URLs from it followed** → Task (e.g., ProductHunt list page where you also want each company's website)
- **Multi-page synthesis, citations needed, structured enrichment** → Task
- **Authenticated / paywall pages** → Task (supports it; Extract may not)

**When to use Task vs LinkedIn Search Posts:**
- **Concrete keyword + filter expression** (search_keywords, author_keyword, mentioning_company, date_posted) → LinkedIn Search Posts
- **Qualitative output that filters can't express** (≥5 keyword expansions, sentiment, entity-relationship) → Task
  - Example: "find all criticisms of ABC company on LinkedIn" → Task

**Choosing a processor:**
- `lite` / `base` — simple fact lookups (founding year, employee count, 1-5 fields)
- `core` — standard GTM enrichment (company profile, tech stack, funding, ~10 fields)
- `pro` / `pro-fast` — competitive analysis, market research reports
- `ultra+` — comprehensive due diligence, multi-source deep dives
- Always prefer `-fast` variants for interactive use cases

---

## API #3: Chat (Web-Grounded Conversations)

**When:** Lightweight, cost-sensitive web-grounded research where Task's full structure (JSON schema, citations, async polling) is overkill.

Think of Chat as **Task-lite** — same web grounding, no structural overhead.

**Endpoint:** `POST /v1beta/chat/completions` (OpenAI-compatible)

**Models:**
| Model | Cost/1K | Latency | Citations |
|---|---|---|---|
| `speed` | $5 | ~3s | No |
| `lite` | $5 | 10-60s | Yes |
| `base` | $10 | 15-100s | Yes |
| `core` | $25 | 1-5min | Yes |

**Rate limit:** 300 req/min

**When to use Chat over Task:**
- One-shot conversational lookups ("what's company X's pricing tier?")
- Cost-sensitive lightweight research
- Don't need structured JSON output or programmatic citations
- Don't want to deal with async polling

**When to switch to Task instead:**
- Need structured output for downstream parsing
- Need citation provenance per field
- Doing batch enrichment (Task's structured input/output beats Chat for repeatable patterns)

**Note:** `temperature`, `top_p`, `max_tokens` etc. are accepted but **ignored**.

---

## API #4: Search (BACKUP for SERP)

**When:** RapidAPI Google is your default for all SERP needs. Use Parallel Search ONLY when:
- RapidAPI is rate-limited (429) or returning empty results
- You explicitly need an objective-based agentic search loop (rare)

**For all primary SERP work, use RapidAPI Google.** This API is here as a safety net.

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

**Backup-mode best practices:**
- Provide BOTH `objective` AND `search_queries` (2-3 variations) for best results
- Use `"fast"` mode for real-time interactions, `"agentic"` for multi-step agent loops
- Excerpts are already LLM-optimized — no need to re-process

---

## Quirks & Gotchas

1. **Extract does NOT search** — it only processes URLs you provide. RapidAPI Google (or Parallel Search as backup) finds URLs; Extract pulls content.
2. **Task is async** — it returns immediately, you must poll or use webhooks/SSE for results.
3. **Chat ignores most parameters** — `temperature`, `top_p`, `max_tokens` are accepted but have no effect.
4. **Search `max_age_seconds` minimum is 600** — you can't force fully fresh results faster than 10 minutes.
5. **Task `input` max is 15,000 chars** — for larger inputs, break into multiple runs.
6. **`-fast` variants exist for pro and ultra Task tiers** — same quality, 2-5x faster, same price. Always prefer these for interactive use.
7. **20K free requests to start** — no credit card required.

---

## Rate Limits Summary

| API | Rate Limit |
|---|---|
| Task | 2,000 req/min |
| Search | 600 req/min |
| Extract | 600 req/min |
| Chat | 300 req/min |

---

## Authentication

```
x-api-key: $PARALLEL_API_KEY
```
Or: `Authorization: Bearer $PARALLEL_API_KEY`
