# PredictLeads API — Tool Quirks & Best Practices

## When to Use
- **Primary use case:** Company intelligence signals — job openings, tech stack, funding, news events, similar companies
- **Best when:** You need structured company signals for account-based prospecting or trigger-based outreach
- **Not for:** People data (no contacts/emails), email finding, email validation, or campaign management

## Critical Gotchas

### 1. Dual-key authentication — BOTH api_token AND api_key required
```
// WRONG — single key ❌
?api_key=abc123

// CORRECT — both credentials ✅
?api_token=YOUR_TOKEN&api_key=YOUR_KEY
```
PredictLeads requires two separate credentials on every request. The `api_token` identifies your account; the `api_key` authenticates it. Missing either one returns 401. These can also be passed as headers (recommended).

### 2. Domain-based lookups — not company name
```
// WRONG — searching by company name ❌
GET /api/v3/companies/Stripe/job_openings

// CORRECT — use domain ✅
GET /api/v3/companies/stripe.com/job_openings
```
All company endpoints use the company's domain as the identifier, not the company name. If you only have a company name, resolve it to a domain first (via Apollo or Google search).

### 3. Job data refreshes every 36 hours — not real-time
Job openings are sourced from company career sites and job boards, refreshed roughly every 36 hours. Don't expect to see a job posted today to appear in the API immediately. Historical job data goes back to 2018 with 220M+ records.

### 4. Response format uses JSON:API-style includes
```json
{
  "data": [
    {"id": "123", "type": "job_opening", "attributes": {...}, "relationships": {...}}
  ],
  "included": [
    {"id": "456", "type": "company", "attributes": {...}}
  ]
}
```
Related entities (company info, technology details) are in the `included` array, not nested in the main objects. You must match by `id` and `type` to assemble the full picture.

### 5. Technology detection tracks ~46,000 technologies across 980M+ detections
The tech stack endpoint returns WHERE a technology was detected (career page, main site, subpages) and WHEN. This is more accurate than self-reported data because it detects actual usage.

## API Patterns

| Detail | Value |
|--------|-------|
| **Auth** | Query params: `api_token` + `api_key` (or equivalent headers) |
| **Base URL** | `https://predictleads.com/api/v3` |
| **Format** | JSON:API style with `data[]` and `included[]` |

### Key Endpoints

| Endpoint | Method | Path | Returns |
|----------|--------|------|---------|
| Job Openings | GET | `/companies/{domain}/job_openings` | Active jobs with title, category, seniority, location, salary |
| Technology Detections | GET | `/companies/{domain}/technology_detections` | Tech stack with detection source and dates |
| News Events | GET | `/companies/{domain}/news_events` | Categorized business events (expansion, product launch, etc.) |
| Financing Events | GET | `/companies/{domain}/financing_events` | Funding rounds with investors, amount, type |
| Similar Companies | GET | `/companies/{domain}/similar_companies` | ML-scored similar companies by domain |
| Company Info | GET | `/companies/{domain}` | Normalized location, meta, ticker, description |
| Connections | GET | `/companies/{domain}/connections` | B2B relationships between companies |
| Website Evolution | GET | `/companies/{domain}/website_evolution` | Tracks when subpages (careers, API docs, blog) were added |
| Discover by Tech | GET | `/discover/technologies/{tech_id}/technology_detections` | Find companies using a specific technology |

### Job opening fields include
- `title`, `url`, `first_seen_at`, `last_seen_at`
- `location` (city, state, country)
- `category` (engineering, sales, marketing, HR, support, etc.)
- `seniority` (junior, mid, senior, lead, manager, director, VP, C-level)
- `description`, `salary`, `contract_type`
- O*NET job category codes

### News event categories
- Expansion, acquisition, partnership, product launch, leadership change
- IPO, layoff, office opening/closing, rebranding
- Each event is structured with date, category, summary, and source URL

## Rate Limits & Pricing

| Tier | Requests/mo | Price/mo |
|------|------------|----------|
| Free trial | 100 | $0 |
| Paid plans | Custom | From $500 |

- Enterprise pricing is usage-based and negotiated
- Data also available as flat file delivery (JSONL) for bulk analysis
- Webhook delivery available for real-time signal monitoring

## Common Errors

| Scenario | Cause |
|----------|-------|
| 401 Unauthorized | Missing `api_token` or `api_key` (both required) |
| 404 Not Found | Domain not in PredictLeads database (check spelling, use root domain) |
| Empty `data[]` | Company exists but has no data for that signal type |
| Stale job data | Normal — refresh cycle is ~36 hours |
| Wrong company returned | Use exact root domain (stripe.com not www.stripe.com) |

## Integration Pattern with nrev-lite
```
PredictLeads (company signals) → identify trigger events
  → Apollo search_people (find decision makers at that company)
  → Apollo/BetterContact enrich_person (get contact info)
  → ZeroBounce validate (verify emails)
  → Instantly campaign (send triggered outreach)
```
