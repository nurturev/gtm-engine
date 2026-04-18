---
name: provider-selection
description: Decision matrix for picking the optimal data provider (Apollo, RocketReach, PredictLeads, RapidAPI Google, Parallel Web) for any GTM task — enrichment, search, scraping, or signals. Use BEFORE making any provider API call to choose the right one and avoid wasted credits.
---

# Provider Selection Guide

This is the decision engine for choosing the right provider for any GTM task.
Use this guide BEFORE making any API call to pick the optimal provider.

Only five providers are currently integrated into nrev-lite with server-side handlers: **Apollo, RocketReach, PredictLeads, RapidAPI Google, Parallel Web**. Others may appear in the dashboard catalog as "Coming Soon" but cannot be invoked through nrev-lite — do not recommend them.

## Quick Decision Matrix

| I need to... | Provider | Operation | Credits | Why |
|---|---|---|---|---|
| Find people by email/name | **Apollo** | enrich_person | 1 (free BYOK) | Best email match rate, includes company context |
| Find people by school/university | **RocketReach** | search_people | 2 (free BYOK) | Only provider with working `school` filter |
| Find alumni of a company (past employees) | **RocketReach** | search_people | 2 (free BYOK) | `previous_employer` filter actually works |
| Search people by title + company | **Apollo** | search_people | 2 (free BYOK) | Largest B2B database, best filters |
| Search people by department | **Apollo** | search_people | 2 (free BYOK) | `person_department_or_subdepartments` filter |
| Get someone's phone number | **RocketReach** | enrich_person | 2 (free BYOK) | Higher phone data coverage |
| Enrich a company by domain | **Apollo** | enrich_company | 1 (free BYOK) | Richest company profiles (tech, funding, size) |
| Find a company's job openings | **PredictLeads** | company_signals | 1 (free BYOK) | Dedicated jobs API, better than scraping |
| Find a company's tech stack | **PredictLeads** | company_signals | 1 (free BYOK) | Detects actual usage, not just marketing |
| Get company news/signals | **PredictLeads** | company_signals | 1 (free BYOK) | Categorized business events |
| Find funding/financing events | **PredictLeads** | company_signals | 1 (free BYOK) | Structured round data with investors |
| Find similar companies | **PredictLeads** | company_signals | 1 (free BYOK) | ML-based similarity scoring |
| Google search for company intel | **RapidAPI Google** | search_web | 1 (free BYOK) | Fast Google SERP, up to 300 results |
| Find recent news about a company | **RapidAPI Google** | search_web | 1 (free BYOK) | Use tbs=qdr:w for time filter |
| Scrape a webpage for content | **Parallel Web** | scrape_page | 1 (free BYOK) | AI-native markdown, handles JS/PDFs |
| Scrape multiple URLs at scale | **Parallel Web** | scrape_page | 1/URL (free BYOK) | Auto-batches in groups of 10, concurrent |
| AI-powered web research | **Parallel Web** | search_web | 1 (free BYOK) | Natural language objectives, agentic mode |
| Extract structured data from pages | **Parallel Web** | extract_structured | 1 (free BYOK) | Task API with LLM + citations |
| Bulk web extraction (100+ URLs) | **Parallel Web** | batch_extract | 1/URL (free BYOK) | Task Groups, up to 2K req/min |

Cost notes: "1 (free BYOK)" means 1 credit when using the platform key, 0 credits when the user has configured their own API key. `search_people` costs 2 credits on either provider.

## Out-of-Scope Capabilities

If the user asks for any of the below, nrev-lite cannot serve it today. Tell them so plainly; do not invent a workaround that uses an unintegrated provider.

| Capability | Status |
|---|---|
| Waterfall / multi-provider enrichment (BetterContact, Clearbit, Lusha) | Not integrated |
| Email verification / deliverability (ZeroBounce) | Not integrated |
| Standalone email finder by domain (Hunter) | Not integrated |
| Cold email sending / campaign management (Instantly) | Use the **Instantly App** via Composio OAuth (Apps tab), not a data provider |
| LLM-based row enrichment (OpenAI, Anthropic, Perplexity) | Not integrated |

For email verification specifically: if the user absolutely needs it, the honest answer is "nrev-lite does not verify emails yet — Apollo/RocketReach already return verified grades on most contacts; use those quality flags as a proxy."

## Provider Deep-Dive: Strengths & Weaknesses

### Apollo (provider: `apollo`)
**Best for:** General people search, company enrichment, title/company filtering
**Cost:** `search_people` 2 credits · `enrich_person` 1 credit · `enrich_company` 1 credit (all free under BYOK)
**Strengths:**
- Largest B2B database (270M+ contacts)
- Rich company profiles (tech stack, funding, employee count)
- Best title + company + location filtering
- Bulk enrichment (up to 10 per call)
**Weaknesses:**
- School/education filter (`person_education_school_names`) is UNRELIABLE — returns generic results
- `q_keywords` free-text search is too broad for specific filtering
- `organization_past_domains` (past company search) returns poor results
- People search returns obfuscated data — needs separate enrichment for emails

### RocketReach (provider: `rocketreach`)
**Best for:** Alumni searches, phone numbers, school-based filtering
**Cost:** `search_people` 2 credits · `enrich_person` 2 credits (free under BYOK)
**Strengths:**
- `school` filter WORKS RELIABLY for university/education searches
- `previous_employer` filter WORKS for finding company alumni
- Higher phone number coverage than Apollo
- Email grading (A/A- grades are verified)
**Weaknesses:**
- Smaller overall database than Apollo
- No bulk enrichment in one call
- Company enrichment is less detailed than Apollo
- Async lookups: some requests return `status: "in_progress"` and need polling

**IMPORTANT for school searches:**
- Always pass BOTH variants of the school name:
  ```json
  {"school": ["IIT Kharagpur", "Indian Institute of Technology Kharagpur"]}
  ```
- Same for any school: `["MIT", "Massachusetts Institute of Technology"]`
- RocketReach matches on the school name as stored in LinkedIn profiles

### PredictLeads (provider: `predictleads`)
**Best for:** Company signals — jobs, tech, news, financing, similar companies
**Cost:** `company_signals` 1 credit (free under BYOK)
**Strengths:**
- Real-time company signal data (jobs refresh every 36 hours)
- Structured, categorized events (not raw text)
- Similar companies uses ML scoring
- Financing data includes investors and round types
**Weaknesses:**
- Company-only (no people data)
- Requires dual-key auth (token + key)
- Coverage varies: strong for US/EU companies, weaker for emerging markets

### RapidAPI Google (provider: `rapidapi_google`)
**Best for:** Google search results for research, news monitoring, competitive intel
**Cost:** `search_web` / `google_search` 1 credit (free under BYOK)
**Strengths:**
- Real-time Google SERP results via RapidAPI (OpenWeb Ninja)
- Up to 300 results per query (no pagination needed)
- Google operators work in query: site:, filetype:, inurl:, intitle:, -keyword
- Time filters (qdr:h/d/w/m/y) for recent results
- Bulk search with concurrent execution
**Weaknesses:**
- Single endpoint (web search only — news/images/maps are separate RapidAPI products)
- Failed requests still consume quota
- It's Google search — results are broad, not structured B2B data
- Need to craft good queries for useful results

**Rate limits:** 10-30 req/sec depending on tier ($25-$150/mo)
**Adaptive throttling:** Response headers x-ratelimit-remaining, x-ratelimit-reset

**Query patterns that work well:**
- Funding: `"{company}" "raised" OR "series" OR "funding"`
- Hiring: `site:linkedin.com/jobs "{company}"`
- Tech stack: `"{company}" "powered by" OR "built with" OR "uses"`
- Leadership: `"{company}" "appointed" OR "new hire" OR "joins as"`
- LinkedIn: `site:linkedin.com/in "{name}" "{company}"`

### Parallel Web (provider: `parallel_web`)
**Best for:** Web scraping, content extraction, AI-powered web research at scale
**Cost:** `scrape_page` / `search_web` / `extract_structured` / `batch_extract` 1 credit per URL (free under BYOK)
**Strengths:**
- AI-native API by Parallel (parallel.ai) — purpose-built for agents
- Search API with natural language objectives + keyword queries
- Extract API: clean markdown from any URL (JS, anti-bot, PDFs)
- Task API: async structured extraction with LLM (citations + confidence)
- Task Groups: batch processing up to 2,000 req/min
- Auto-batches >10 URLs with concurrent execution
- SOC-2 Type II certified
**Weaknesses:**
- Extract capped at 10 URLs per API call (auto-batched by our provider)
- fetch_policy.max_age_seconds minimum is 600 (10 min cache)
- max_results not guaranteed on search
- Text-only output (no images)

**Rate limits:** Search/Extract 600/min, Tasks 2,000/min. GET polling is FREE.
**20,000 free requests** before paid pricing.

## Common GTM Workflows (Multi-Provider)

### 1. ICP List Building
```
Apollo search_people (title + company size + industry)
  → Apollo enrich_person (get emails for top matches)
  → PredictLeads company_signals (verify they're hiring = budget available)
```

### 2. Alumni Network Mining
```
RocketReach search_people (school="IIT Kharagpur", title filters)
  → Filter to people at target companies
  → Apollo enrich_person (get emails + company details)
```

### 3. Company Research Brief
```
Apollo enrich_company (firmographics)
  + PredictLeads company_signals (news, jobs, tech stack, financing)
  + RapidAPI Google search_web (press coverage, tbs=qdr:m)
  + Parallel Web scrape_page (pricing page, about page)
```

### 4. Competitive Intelligence
```
RapidAPI Google search_web (find competitor URLs)
  → Parallel Web scrape_page (extract pricing, features from multiple URLs)
  → Parallel Web extract_structured (structured comparison via Task API)
  → PredictLeads company_signals (similar companies, signals)
```

### 5. Event-Triggered Outreach
```
PredictLeads company_signals (new funding, expansion, product launch)
  → Apollo search_people (find decision makers at that company)
  → Apollo enrich_person (get contact info)
```

## Auto-Selection Rules

The CLI and execution engine follow these rules:

1. **If `--school` or `--past-company` flag is used** → auto-select RocketReach
2. **If operation is `company_signals`** → auto-select PredictLeads
3. **If operation is `search_web`** → auto-select RapidAPI Google
4. **If operation is `scrape_page`, `crawl_site`, `extract_structured`, `batch_extract`** → auto-select Parallel Web
5. **Everything else (enrich, search people/companies)** → default to Apollo
6. **User can always override** with `--provider` flag

Note: Parallel Web also supports `search_web` (AI-powered objective search).
Use `--provider parallel_web` to get Parallel's agentic search instead of Google.
