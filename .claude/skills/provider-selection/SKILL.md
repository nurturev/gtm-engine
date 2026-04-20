---
name: provider-selection
description: Decision engine for choosing the right enrichment/search provider for any GTM task
---

# Provider Selection Guide

This is the decision engine for choosing the right provider for any GTM task.
Use this guide BEFORE making any API call to pick the optimal provider.

## Quick Decision Matrix

| I need to... | Provider | Operation | Why |
|---|---|---|---|
| Find people by email/name | **Apollo** | enrich_person | Best email match rate, includes company context |
| Find people by school/university | **RocketReach** | search_people | Only provider with working `school` filter. 3 credits/call |
| Find alumni of a company (past employees) | **RocketReach** | search_people | `previous_employer` filter actually works. 3 credits/call |
| Search people by title + company | **Apollo** | search_people | Largest B2B database, best filters |
| Search people by department | **Apollo** | search_people | `person_department_or_subdepartments` filter |
| Get someone's phone number | **RocketReach** | enrich_person | Higher phone data coverage. 18 credits/call when phone is requested |
| Get LIVE person profile (current title, headline, follower count) | **LinkedIn Scraping** | get_profile_details | Live scrape — most recent source of truth |
| Get a person's skills, education, employment history | **LinkedIn Scraping** | get_profile_details | Fields not exposed by Apollo/RocketReach |
| Discover what a person engages with on LinkedIn (comments/reactions elsewhere) | **LinkedIn Scraping** | get_profile_posts (`type=comments` or `reactions`) | Interest mapping for personalisation |
| Find people who reacted/commented on a specific post | **LinkedIn Scraping** | get_post_reactions / get_post_comments | Warm engagement signal for outreach |
| Search posts by keyword/boolean (with company-mention filter) | **LinkedIn Scraping** | search_posts | Native LinkedIn search; needs integer company_id for mention filter |
| Enrich a company by domain | **Apollo** | enrich_company | Richest company profiles (tech, funding, size) |
| Get LIVE company data (follower count, current employee count) | **LinkedIn Scraping** | get_company_by_domain or get_company_by_url | Live LinkedIn data; only source of follower count |
| Resolve a company to its integer LinkedIn company_id | **LinkedIn Scraping** | get_company_by_domain or get_company_by_url | Required for `mentioning_company` array in Search Posts |
| Get posts authored by a company page | **LinkedIn Scraping** | get_company_posts | Competitor content monitoring, engagement mining |
| Find a company's job openings | **PredictLeads** | company_jobs | Dedicated jobs API, better than scraping |
| Find a company's tech stack | **PredictLeads** | company_technologies | Detects actual usage, not just marketing |
| Get company news/signals | **PredictLeads** | company_news | Categorized business events |
| Find funding/financing events | **PredictLeads** | company_financing | Structured round data with investors |
| Find similar companies | **PredictLeads** | similar_companies | ML-based similarity scoring |
| Google search for company intel | **RapidAPI Google** | search_web | Fast Google SERP, up to 300 results |
| Find recent news about a company | **RapidAPI Google** | search_web | Use tbs=qdr:w for time filter |
| Scrape a webpage for content | **Parallel Web** | extract | AI-native markdown, handles JS/PDFs/anti-bot |
| Scrape multiple URLs at scale | **Parallel Web** | extract | Pass URL array; cheap and fast |
| Lightweight web-grounded research (Task is overkill) | **Parallel Web** | chat | Task-lite — OpenAI-compatible, no schema/polling overhead |
| Structured enrichment with citations | **Parallel Web** | task | Multi-source AI synthesis with Basis citations + confidence |
| Backup SERP (when RapidAPI Google fails) | **Parallel Web** | search | Operational fallback only — RapidAPI is default for all SERP |
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
- `q_organization_name` is unreliable for search — always resolve to domain via Org Enrich first, then use `q_organization_domains`
- `organization_past_domains` (past company search) returns poor results
- People search returns obfuscated data — needs separate enrichment for emails

### RocketReach (provider: `rocketreach`)
**Best for:** Alumni searches, phone numbers, school-based filtering
**Cost:** 3 credits ($0.03) per call. **18 credits** per call when phone numbers are requested.
**Strengths:**
- `school` filter WORKS RELIABLY for university/education searches
- `previous_employer` filter WORKS for finding company alumni
- Higher phone number coverage than Apollo
- Email grading (A/A- grades are verified)
- LinkedIn URL lookups ~99% match rate
**Weaknesses:**
- Smaller overall database than Apollo
- No bulk enrichment in one call
- Company enrichment is less detailed than Apollo
- Async lookups: some requests return `status: "in_progress"` and need polling
- Phone requests are 6× more expensive (18 credits vs 3 for email-only)

**IMPORTANT for school searches:**
- Always pass BOTH variants of the school name:
  ```json
  {"school": ["IIT Kharagpur", "Indian Institute of Technology Kharagpur"]}
  ```
- Same for any school: `["MIT", "Massachusetts Institute of Technology"]`
- RocketReach matches on the school name as stored in LinkedIn profiles

### LinkedIn Scraping (provider: `linkedin_scraping` — Fresh LinkedIn Profile Data via RapidAPI)
**Best for:** Live profile/company data, follower/connection counts, skills/education, engagement mining, post discovery
**Strengths:**
- **Recency** — live LinkedIn scrape, beats cached B2B databases on freshness (current title, headline, current company)
- **Unique fields** — follower count, connection count, skills, education, specialties, **integer `company_id`** (required for post-mention filter)
- **Engagement axis** — only provider that gives access to commenters/reactors per post (warm intent signal)
- **Post search** — native LinkedIn post search by keyword/boolean, optional company-mention or author-title filter
- **No-email-needed flows** — if outreach is via LinkedIn DM/connect, no enrichment provider is required at all
- **Polymorphic profile activity endpoint** — one endpoint (`Get Profile's Posts`) covers what a person posts, comments on, and reacts to via a `type` enum
**Weaknesses:**
- **No emails or phone numbers** — must pair with Apollo/RocketReach/Waterfall if contact data is needed
- **Engagement explosion risk** — N posts × M engagers can balloon credits; recency filter and repost exclusion are mandatory
- **Rate-limited** — live scraping; 429s on overage, expect throttling on large batches
- **`mentioning_company` requires an ARRAY of INTEGER company IDs** — URLs and domains do NOT work; must resolve via Get Company first
- **`search_keywords` rejects comma-separated keywords** — split into separate queries upstream
- **Domain → company lookup can return empty** for niche/SMB/non-canonical domains (use Apollo→Parallel Web fallback)

**Decision: People Enrichment — LinkedIn vs Apollo vs RocketReach**

| Use LinkedIn Scraping when… | Use Apollo when… | Use RocketReach when… |
|---|---|---|
| Need the most recent profile (job changes within last few months) | Need email + cached firmographics in one call | Need school/alumni filter (`school`, `previous_employer`) |
| Need follower count, skills, education, headline | Bulk people search by title/company/location | Need phone numbers (better coverage than Apollo) |
| Outreach is LinkedIn DM/connect (no email needed) | Need employment history with `include_similar_titles` | Need years_of_experience, funding stage, dept growth filters |
| Mining post engagers as warm leads | Largest database, lowest cost per row | Async lookups acceptable |

**Decision: Company Enrichment — LinkedIn vs Apollo**

| Use LinkedIn Scraping when… | Use Apollo when… |
|---|---|
| Need live employee/follower count | Need cached firmographics, tech stack, funding |
| Need integer **company_id** (for post-mention filter in Search Posts) | Need company keywords/industry classifications used by Apollo's filter system |
| Need specialties, headline, current LinkedIn description | Bulk company enrichment by domain |
| Domain may have changed/rebranded — want current LinkedIn presence | Cost efficiency on large company lists |

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
**Best for:** Anti-bot scraping, structured enrichment with citations, lightweight web-grounded research, backup SERP
**APIs in scope (4):** Extract · Task · Chat · Search (backup)
**Strengths:**
- AI-native API by Parallel (parallel.ai) — purpose-built for agents
- Extract: clean markdown from any URL — handles JS, anti-bot, PDFs, paywalls
- Task: async structured extraction with LLM citations + confidence (Basis framework)
- Chat: Task-lite for cost-sensitive lightweight research (no JSON schema / polling overhead)
- Search: backup SERP when RapidAPI Google fails
- SOC-2 Type II certified
**Weaknesses:**
- Extract capped at 10 URLs per API call (auto-batched by our provider)
- fetch_policy.max_age_seconds minimum is 600 (10 min cache)
- Task is async — requires polling/webhooks
- Text-only output (no images)
- Chat ignores `temperature`, `top_p`, `max_tokens` parameters

**Out of scope for the consultant:** Task Group, FindAll, Monitor — Workflow Builder territory.

**Decision flow:** see `tool-skills/parallel-web-quirks.md` for the full when-to-use tree.

**Rate limits:** Extract 600/min, Search 600/min, Task 2,000/min, Chat 300/min. GET polling is FREE.
**20,000 free requests** before paid pricing.

## Waterfall Enrichment Principle (Don't Roll Your Own)

**Do NOT chain providers manually to fill enrichment gaps.** BetterContact already does this — it cascades through 20+ providers (Apollo, RocketReach, Hunter, etc.), only charges for verified data, and handles dedup + quality scoring.

**Instead:**
1. Use this guide to pick the **single best provider** for each data type
2. Send the enrichment request to that one provider
3. If coverage is insufficient, recommend BetterContact as the waterfall layer — don't simulate it manually

**Why not waterfall manually:**
- Wastes credits on redundant provider calls
- Each provider's rate limits compound the latency
- Dedup logic is non-trivial (same person, different email formats)
- BetterContact's verification pass (Bouncer) is more thorough than what you'd build inline

**The exception:** the LinkedIn → Apollo → Parallel Web fallback for resolving an empty `Get Company by Domain` result is a *resolution* chain (finding the right LinkedIn URL), not a waterfall *enrichment* chain. Resolution chains are fine; enrichment cascades belong to BetterContact.

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

1. **If school or past-company is specified** → auto-select RocketReach
2. **If operation starts with `company_`** → auto-select PredictLeads
3. **If operation is `search_web`** → auto-select RapidAPI Google. Fall back to Parallel Web `search` ONLY if RapidAPI is rate-limited or returns empty.
4. **If operation is `extract` (page-to-markdown), `task` (structured enrichment with citations), or `chat` (lightweight web-grounded research)** → auto-select Parallel Web. See `tool-skills/parallel-web-quirks.md` for the Extract vs Task vs Chat decision flow.
5. **If operation is `validate_email` or `batch_validate`** → auto-select ZeroBounce
6. **If operation is `domain_search` or `email_finder`** → auto-select Hunter
7. **If operation is `create_campaign`, `manage_leads`, or `campaign_analytics`** → auto-select Instantly
8. **If waterfall or max coverage is requested** → auto-select BetterContact for enrichment
9. **If user wants LIVE / RECENT profile or company data** (current title, follower count, skills, education) → auto-select LinkedIn Scraping
10. **If task involves LinkedIn engagement** (commenters, reactors, what someone reacted to) → auto-select LinkedIn Scraping
11. **If task involves post-mention filtering** → resolve `company_id` via LinkedIn Scraping (`get_company_by_*`) BEFORE calling Search Posts
12. **If LinkedIn `Get Company by Domain` returns empty** → fallback to Apollo `enrich_company` (returns linkedin_url) → retry LinkedIn `Get Company by URL`. If still empty, use RapidAPI Google `search_web` for `"{name} site:linkedin.com/company"` (Parallel Search as backup if RapidAPI fails)
13. **Everything else (enrich, search people/companies)** → default to Apollo

## LinkedIn-Specific Multi-Provider Workflow

### 9. LinkedIn Engagement Mining (Warm Leads from a Target's Content)
```
person/company URL
  → LinkedIn Scraping get_posts (recency filter mandatory, exclude reposts)
  → LinkedIn Scraping get_post_comments + get_post_reactions [parallel]
  → normalize column prefixes (commenter_/reactor_ → engager_)
  → merge → unified engager list
  → (if email needed) Apollo enrich_person OR BetterContact waterfall
```
**Guardrails:** ask user about recency window + repost exclusion BEFORE fan-out. Pilot one post first, show row count, confirm.

### 10. Competitor Mention Monitoring
```
competitor domain
  → LinkedIn Scraping get_company_by_domain → integer company_id
  → LinkedIn Scraping search_posts (mentioning_company=[company_id], date_posted="Past week")
  → LinkedIn Scraping get_post_comments / get_post_reactions
  → Apollo enrich (if email needed)
```
**Why LinkedIn (not Google search) here:** competitor mention filter requires the integer company_id; Google can't replicate it.

Note: Parallel Web's `search` is a **backup-only** SERP option. RapidAPI Google is the default for all SERP work; switch to Parallel Search only when RapidAPI is rate-limited or returning empty results.
