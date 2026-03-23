# Web Scraping via Parallel Web Systems (provider: parallel_web)

AI-native web intelligence API (parallel.ai). Extracts clean content
from any URL, runs structured extraction tasks, and processes bulk
URL batches at scale. Handles JS rendering, anti-bot, and PDFs.

## Operations

### scrape_page — Extract content from URLs (up to 10 per call)
```
nrev-lite web scrape https://acme.com/about
nrev-lite web scrape https://acme.com/pricing --objective "pricing information"
nrev-lite web scrape https://acme.com/pricing --full-content --json-output
```
Returns: markdown excerpts or full content for each URL.

**Params:**
- `url` (str) or `urls` (list): URLs to extract (max 10 per API call, auto-batched)
- `objective` (str): Focus extraction on this intent (natural language, max 3K chars)
- `full_content` (bool): Get full page content instead of excerpts
- `search_queries` (list[str]): Keywords to emphasize in extraction
- `max_age_seconds` (int): Max cache age in seconds (min 600 = 10 minutes)

**IMPORTANT:** Extract is capped at 10 URLs per API call. For larger sets,
the provider auto-batches into groups of 10 with concurrency control.

### search_web — AI-powered web search with objectives
```
nrev-lite execute search_web --provider parallel_web \
  --params '{"objective": "Find recent funding rounds for SaaS companies in India"}'
```
Returns: URLs with excerpts, ranked by relevance to objective.

**Params:**
- `objective` (str): Natural language search intent (max 5K chars)
- `search_queries` (list[str]): Keyword queries (max 200 chars each)
- `mode`: "fast" | "one-shot" | "agentic" (default: one-shot)
- `max_results` (int): Upper bound, max 20
- `include_domains` / `exclude_domains`: Domain filters
- `after_date` (str): YYYY-MM-DD — only results after this date

### extract_structured — Task API for LLM-powered extraction (async)
```
nrev-lite web extract https://acme.com/pricing \
    --prompt "Extract pricing tiers with name, price, features"
```
Returns: Structured output with citations and confidence scores.

**Params:**
- `input` (str|dict): What to process
- `processor`: "lite" | "base" | "core" | "pro" (affects quality + cost)
- `output_schema` (dict): JSON Schema for structured output
- `webhook_url` (str): Completion callback URL
- `poll` (bool): Wait for result (default: true)

### batch_extract — Task Groups for high-volume batch processing
```
nrev-lite execute batch_extract --provider parallel_web \
  --params '{"items": ["company1.com", "company2.com", ...], "processor": "base"}'
```
Returns: Results for all items. Batches of 500, polls until complete.

## Rate Limits & Bulk Architecture

| API | Limit | Notes |
|-----|-------|-------|
| Search | 600/min | POST only |
| Extract | 600/min | Max 10 URLs per request |
| Task/Groups | 2,000/min | GET polling is FREE |

**For bulk extract:** The provider auto-batches URLs into groups of 10 and
runs them concurrently with a semaphore (default 20 concurrent requests).
At 600 req/min with 10 URLs each = 6,000 URLs/min theoretical max.

**For bulk tasks:** Task Groups accept up to 1,000 runs per POST (recommended 500).
Results stream via SSE. Data persists indefinitely.

## Quirks
- `fetch_policy.max_age_seconds` minimum is 600 (10 minutes)
- `max_results` is NOT guaranteed — may return fewer
- GET requests (polling, status) do NOT count against rate limits
- Output is always markdown format
- Text-only: recognizes on-page images but doesn't return them

## Pricing
- 20,000 free requests
- Search: $0.004/req (base), $0.009/req (pro)
- Task: $5-$2,400 per 1K depending on processor tier

## API Key
Get from https://platform.parallel.ai
Stored as `PARALLEL_KEY` in .env or vault.
