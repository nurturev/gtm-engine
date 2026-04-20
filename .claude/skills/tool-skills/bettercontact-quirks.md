# BetterContact API — Tool Quirks & Best Practices

## When to Use
- **Primary use case:** Waterfall enrichment — finding verified emails and mobile phones by cascading through 20+ providers automatically
- **Best when:** You need maximum coverage and don't want to build multi-provider fallback logic yourself
- **Not for:** Real-time single lookups (async model adds latency), company-only enrichment, or when you need a specific provider's data

## Critical Gotchas

### 1. Enrichment is ASYNC — you must poll or use webhooks
```
// WRONG — expecting immediate results
POST /api/v2/async → response contains enriched data

// CORRECT — submit then fetch
POST /api/v2/async → returns {"id": "request-id"}
GET  /api/v2/async/{request_id} → poll until status="terminated"
```
The POST returns a `request_id` only. You must either poll the GET endpoint or provide a `webhook` URL in the POST body. Results take seconds to minutes depending on batch size.

### 2. Phone credits are 10x email credits
```
Email enrichment = 1 credit
Phone enrichment = 10 credits
```
A batch of 100 contacts with both email + phone enabled burns 1,100 credits if all found. Always estimate cost before running large batches with phone enabled.

### 3. You only pay for FOUND + VERIFIED data
Unlike Apollo, BetterContact does NOT charge credits for failed lookups. No email found = no charge. This makes it safe to run speculative enrichments. However, valid catch-all emails DO consume 1 credit each.

### 4. Batch limit is 100 per request
Maximum 100 leads per POST. For larger lists, chunk into batches and submit sequentially, collecting `request_id` for each. Poll all request IDs for results.

### 5. Required fields depend on each other
```json
// MINIMUM — company name OR domain is required
{"first_name": "Jane", "last_name": "Doe", "company": "Acme Corp"}
{"first_name": "Jane", "last_name": "Doe", "company_domain": "acme.com"}

// BETTER — domain + LinkedIn URL maximizes match rate
{"first_name": "Jane", "last_name": "Doe", "company_domain": "acme.com", "linkedin_url": "https://linkedin.com/in/janedoe"}
```
`first_name` and `last_name` are always required. You must provide either `company` or `company_domain` (domain preferred for accuracy).

## API Patterns

| Detail | Value |
|--------|-------|
| **Auth** | `X-API-Key` header |
| **Base URL** | `https://app.bettercontact.rocks/api/v2` |
| **Submit enrichment** | `POST /async` — body: `{data: [...], enrich_email_address: true, enrich_phone_number: false}` |
| **Fetch results** | `GET /async/{request_id}` — poll until `status: "terminated"` |
| **Response format** | JSON with `status`, `credits_consumed`, `credits_left`, `summary`, `data[]` |

### Key response fields in `data[]`
- `contact_email_address` — the found email
- `contact_email_address_status` — `valid`, `catch-all`, `undeliverable`, `not-found`
- `contact_phone_number` — the found mobile (if phone enrichment enabled)
- `contact_first_name`, `contact_last_name`, `contact_job_title`

## Rate Limits & Pricing

| Plan | Credits/mo | Price/mo | Per-email cost |
|------|-----------|----------|----------------|
| Free trial | 50 | $0 | — |
| Starter | 1,000 | $49 | ~$0.049 |
| Pro 5K | 5,000 | $199 | ~$0.040 |
| Pro 10K | 10,000 | $399 | ~$0.040 |
| Pro 50K | 50,000 | $1,999 | ~$0.040 |

Credits roll over (capped at 2x your plan).

## Common Errors

| Status | Meaning |
|--------|---------|
| 401 | Invalid or missing API key |
| 400 | Missing required fields (`first_name`, `last_name`, `company` or `company_domain`) |
| 429 | Rate limited — back off and retry |
| `status: "in_progress"` | Results not ready yet — keep polling |
| `status: "terminated"` | Enrichment complete — read `data[]` |
