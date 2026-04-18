---
name: provider-selection
description: Decision matrix for picking the optimal data provider (Apollo, RocketReach, Hunter, Parallel, Perplexity, ZeroBounce, BetterContact, PredictLeads, etc.) for any GTM task — enrichment, search, scraping, verification, or AI research. Use BEFORE making any provider API call to choose the right one and avoid wasted credits.
---

# Provider Selection Guide

This is the decision engine for choosing the right provider for any GTM task.
Use this guide BEFORE making any API call to pick the optimal provider.

## Quick Decision Matrix

| I need to... | Provider | Operation | Why |
|---|---|---|---|
| Find people by email/name | **Apollo** | enrich_person | Best email match rate, includes company context |
| **Enrich a person when I already have their LinkedIn URL** | **Fresh LinkedIn** | enrich_person | Direct-from-LinkedIn, richest profile data (experience, education, skills, about). 3 credits/call. Pass `provider="fresh_linkedin"` explicitly |
| Find people by school/university | **RocketReach** | search_people | Only provider with working `school` filter. 3 credits/call |
| Find alumni of a company (past employees) | **RocketReach** | search_people | `previous_employer` filter actually works. 3 credits/call |
| Search people by title + company | **Apollo** | search_people | Largest B2B database, best filters |
| Search people by department | **Apollo** | search_people | `person_department_or_subdepartments` filter |
| Get someone's phone number | **RocketReach** | enrich_person | Higher phone data coverage. 18 credits/call with phone |
| Enrich a company by domain | **Apollo** | enrich_company | Richest company profiles (tech, funding, size) |
| Find a company's job openings | **PredictLeads** | company_jobs | Dedicated jobs API, better than scraping |
| Find a company's tech stack | **PredictLeads** | company_technologies | Detects actual usage, not just marketing |
| Get company news/signals | **PredictLeads** | company_news | Categorized business events |
| Find funding/financing events | **PredictLeads** | company_financing | Structured round data with investors |
| Find similar companies | **PredictLeads** | similar_companies | ML-based similarity scoring |
| Google search for company intel | **RapidAPI Google** | search_web | Fast Google SERP, up to 300 results |
| Find recent news about a company | **RapidAPI Google** | search_web | Use tbs=qdr:w for time filter |
| Scrape a webpage for content | **Parallel Web** | scrape_page | AI-native markdown, handles JS/PDFs |
| Scrape multiple URLs at scale | **Parallel Web** | scrape_page | Auto-batches in groups of 10, concurrent |
| AI-powered web research | **Parallel Web** | search_web | Natural language objectives, agentic mode |
| Extract structured data from pages | **Parallel Web** | extract_structured | Task API with LLM + citations |
| Bulk web extraction (100+ URLs) | **Parallel Web** | batch_extract | Task Groups, up to 2K req/min |
| Waterfall enrich emails (max coverage) | **BetterContact** | enrich_person | Tries 20+ providers, only charges for found data |
| Waterfall enrich phone numbers | **BetterContact** | enrich_person | 10 credits/phone but 70-85% coverage |
| Find emails at a domain | **Hunter** | domain_search | Returns all known emails + confidence scores |
| Find one email from name+domain | **Hunter** | email_finder | Pattern-based guess with confidence score |
| Verify an email before sending | **ZeroBounce** | validate_email | 99.6% accuracy, catch-all detection, sub-statuses |
| Validate email list in bulk | **ZeroBounce** | batch_validate | File upload for 200+, credits never expire |
| Detect disposable/spam emails | **ZeroBounce** | validate_email | `do_not_mail` status with disposable/toxic sub-types |
| Create cold email campaign | **Instantly** | create_campaign | Full sequence builder, A/B testing, scheduling |
| Manage campaign leads | **Instantly** | manage_leads | Add, move, list leads across campaigns |
| Monitor email warmup | **Instantly** | warmup_analytics | Track warmup progress before launching |
| Get campaign analytics | **Instantly** | campaign_analytics | Opens, replies, bounces per campaign |

## Provider Deep-Dive: Strengths & Weaknesses

### Apollo (provider: `apollo`)
**Best for:** General people search, company enrichment, title/company filtering
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
**Cost:** 3 credits ($0.03) per call. 18 credits if phone numbers requested.
**Strengths:**
- `school` filter WORKS RELIABLY for university/education searches
- `previous_employer` filter WORKS for finding company alumni
- Higher phone number coverage than Apollo
- LinkedIn URL lookups ~99% match rate
**Weaknesses:**
- Smaller overall database than Apollo
- Company enrichment is less detailed than Apollo
- Async lookups: some requests return `status: "in_progress"` and need polling
- Phone requests are expensive (18 credits vs 3 for email-only)

**IMPORTANT for school searches:**
- Always pass BOTH variants of the school name:
  ```json
  {"school": ["IIT Kharagpur", "Indian Institute of Technology Kharagpur"]}
  ```
- Same for any school: `["MIT", "Massachusetts Institute of Technology"]`
- RocketReach matches on the school name as stored in LinkedIn profiles

### PredictLeads (provider: `predictleads`)
**Best for:** Company signals — jobs, tech, news, financing, similar companies
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

### BetterContact (provider: `bettercontact`)
**Best for:** Maximum email/phone coverage via waterfall enrichment (20+ providers)
**Strengths:**
- Cascades through 20+ data providers automatically (Apollo, RocketReach, Hunter, etc.)
- Only charges for found + verified data (no credits for misses)
- 87-95% email coverage vs 60-70% from a single provider
- Built-in email verification (Bouncer) and catch-all validation
- Phone number coverage 70-85% (vs ~40% from single providers)
**Weaknesses:**
- Async API — must poll for results or use webhooks (adds latency)
- Phone enrichment costs 10x email (10 credits vs 1)
- Cannot specify which underlying provider to use
- No company-only enrichment (people data only)
- Batch limit: 100 per request

### Hunter (provider: `hunter`)
**Best for:** Email discovery by domain, email verification, email pattern detection
**Strengths:**
- Domain Search finds ALL known emails at a company (up to 100)
- Email Finder generates likely email from name+domain with confidence score
- Email Verifier checks deliverability with specific status codes
- Free Email Count endpoint (no credits) — check coverage before spending
- Sources tracking shows WHERE emails were found
**Weaknesses:**
- No phone numbers at all
- No people search by title/seniority (different tool category than Apollo)
- Free tier extremely limited (25 searches/month)
- Email Finder is pattern-based guessing, not verified contacts
- Confidence scores below 70 are unreliable

### ZeroBounce (provider: `zerobounce`)
**Best for:** Email validation before campaigns — deliverability verification with detailed status codes
**Strengths:**
- 99.6% validation accuracy with detailed status + sub_status
- Catch-all detection distinguishes accept_all (safer) vs catch-all (risky)
- Disposable, spam trap, and toxic email detection
- Credits never expire — buy once, use anytime
- No credit consumed for unknown results (server timeouts)
- Regional endpoints (US/EU) for GDPR compliance
**Weaknesses:**
- Validation only — cannot find or enrich emails
- Single validation response time 1-30 seconds (varies by mail server)
- Batch endpoint limited to 200 emails, 5 requests/minute
- No phone validation

### Instantly (provider: `instantly`)
**Best for:** Cold email campaign creation, sending, warmup, and analytics
**Strengths:**
- Full campaign lifecycle via API (create, activate, pause, analytics)
- Unlimited email account connections with built-in warmup
- Lead management with campaign assignment and movement
- A/B testing and sequence building
- 6,000 req/min rate limit (generous)
**Weaknesses:**
- API access requires Hypergrowth plan ($97/mo) or above — Growth plan has NO API
- V2 only — V1 is deprecated and incompatible
- Lead listing uses POST (not GET) — non-standard REST
- Sequences array quirk: only first element is used despite being an array
- Warmup must run 30+ days before campaign launch
- Hard caps on monthly emails (no overage billing, just pauses sending)

### Parallel Web (provider: `parallel_web`)
**Best for:** Web scraping, content extraction, AI-powered web research at scale
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
  → PredictLeads company_jobs (verify they're hiring = budget available)
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
  + PredictLeads company_news (recent events)
  + PredictLeads company_jobs (hiring signals)
  + PredictLeads company_technologies (tech stack)
  + RapidAPI Google search_web (press coverage, tbs=qdr:m)
  + Parallel Web scrape_page (pricing page, about page)
```

### 4. Competitive Intelligence
```
RapidAPI Google search_web (find competitor URLs)
  → Parallel Web scrape_page (extract pricing, features from multiple URLs)
  → Parallel Web extract_structured (structured comparison via Task API)
  → PredictLeads similar_companies (find more competitors)
```

### 5. Event-Triggered Outreach
```
PredictLeads company_news (new funding, expansion, product launch)
  → Apollo search_people (find decision makers at that company)
  → Apollo enrich_person (get contact info)
```

### 6. Full Outbound Pipeline (Enrichment → Validation → Campaign)
```
Apollo search_people (find ICP matches by title + company)
  → BetterContact enrich_person (waterfall for max email coverage)
  → ZeroBounce validate_email (verify all emails, remove invalid/disposable/spamtrap)
  → Instantly create_campaign (load verified leads, set sequences, activate)
```

### 7. Domain Email Discovery + Verification
```
Hunter domain_search (find all emails at target domain)
  → ZeroBounce batch_validate (verify deliverability of all found emails)
  → Filter to valid + accept_all only
```

### 8. Signal-Triggered Outbound Campaign
```
PredictLeads company_news (detect funding, expansion, product launch)
  → Apollo search_people (find decision makers)
  → BetterContact enrich_person (waterfall for best email coverage)
  → ZeroBounce validate_email (verify before sending)
  → Instantly create_campaign (personalized trigger-based outreach)
```

## Auto-Selection Rules

The CLI and execution engine follow these rules:

1. **If `--school` or `--past-company` flag is used** → auto-select RocketReach
2. **If operation starts with `company_`** → auto-select PredictLeads
3. **If operation is `search_web`** → auto-select RapidAPI Google
4. **If operation is `scrape_page`, `crawl_site`, `extract_structured`, `batch_extract`** → auto-select Parallel Web
5. **If operation is `validate_email` or `batch_validate`** → auto-select ZeroBounce
6. **If operation is `domain_search` or `email_finder`** → auto-select Hunter
7. **If operation is `create_campaign`, `manage_leads`, or `campaign_analytics`** → auto-select Instantly
8. **If `--waterfall` flag is used or max coverage is requested** → auto-select BetterContact for enrichment
9. **Everything else (enrich, search people/companies)** → default to Apollo
10. **User can always override** with `--provider` flag

Note: Parallel Web also supports `search_web` (AI-powered objective search).
Use `--provider parallel_web` to get Parallel's agentic search instead of Google.
