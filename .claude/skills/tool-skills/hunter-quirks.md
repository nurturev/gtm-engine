# Hunter.io API — Tool Quirks & Best Practices

## When to Use
- **Primary use case:** Finding business emails by domain, verifying email deliverability
- **Best when:** You have a domain and need to discover all associated emails, or need to verify an email before sending
- **Not for:** Phone number enrichment (no phone data), company firmographics, people search by title/seniority

## Critical Gotchas

### 1. Domain Search, Email Finder, and Email Verifier are DIFFERENT endpoints with different costs
```
Domain Search  → GET /v2/domain-search?domain=stripe.com     → 1 credit per query (returns up to 100 emails)
Email Finder   → GET /v2/email-finder?domain=stripe.com&first_name=Patrick&last_name=Collison → 1 credit
Email Verifier → GET /v2/email-verifier?email=patrick@stripe.com → 1 credit
```
Domain Search finds ALL emails at a domain. Email Finder guesses ONE email from name+domain. Email Verifier checks deliverability. Most workflows need Finder then Verifier — two separate calls.

### 2. Confidence score interpretation matters
```
score >= 90  → Very likely valid, safe to send
score 70-89  → Probably valid, verify before sending
score < 70   → Risky, do NOT send without verification
```
The Email Finder returns a `score` (0-100). A common mistake is treating score=60 as "good enough." Always verify emails with score < 90 through the Email Verifier endpoint.

### 3. Free tier is EXTREMELY limited
```
Free plan: 25 searches + 50 verifications per month
Domain Search on free plan: max 10 emails returned (limit + offset <= 10)
```
Free tier is only useful for testing. Any real workflow requires a paid plan.

### 4. Email Verifier returns 202 when verification is in progress
```json
// HTTP 202 — NOT an error, just means "still checking"
{"data": null, "meta": {"params": {"email": "test@example.com"}}}
```
Poll again after a few seconds. A 200 response means verification is complete.

### 5. Verification status values are specific
| Status | Meaning | Safe to send? |
|--------|---------|---------------|
| `valid` | Deliverable | Yes |
| `invalid` | Will bounce | No |
| `accept_all` | Catch-all domain, can't confirm | Risky |
| `webmail` | Free provider (Gmail, Yahoo) | Usually yes |
| `disposable` | Temporary email | No |
| `unknown` | Could not verify | Risky |

## API Patterns

| Detail | Value |
|--------|-------|
| **Auth** | `api_key` query param, `X-API-KEY` header, or `Authorization: Bearer <key>` |
| **Base URL** | `https://api.hunter.io/v2` |
| **Domain Search** | `GET /domain-search?domain={domain}` |
| **Email Finder** | `GET /email-finder?domain={domain}&first_name={fn}&last_name={ln}` |
| **Email Verifier** | `GET /email-verifier?email={email}` |
| **Email Count** | `GET /email-count?domain={domain}` — **FREE**, no credits |
| **Account Info** | `GET /account` — **FREE** |
| **Test key** | Use `test-api-key` for parameter validation without consuming credits |

### Useful response fields
- `emails[].type` — `personal` or `generic` (info@, support@, etc.)
- `emails[].confidence` — 0-100 score in domain search results
- `emails[].sources` — where Hunter found the email (web pages, databases)
- `emails[].verification.status` — inline verification status

## Rate Limits

| Endpoint | Per Second | Per Minute |
|----------|-----------|------------|
| Domain Search / Email Finder | 15 | 500 |
| Email Verifier | 10 | 300 |
| Discover API | 5 | 50 |

Rate limits enforced per API key AND per IP — whichever hits first. On 429, use exponential backoff (1s, 2s, 4s, ...).

## Pricing

| Plan | Searches/mo | Verifications/mo | Price/mo |
|------|------------|-------------------|----------|
| Free | 25 | 50 | $0 |
| Starter | 500 | 1,000 | $49 |
| Growth | 5,000 | 10,000 | $149 |
| Business | 50,000 | 100,000 | $499 |

Email Enrichment and Company Enrichment cost 0.2 credits per call. Combined Enrichment returns both in one request (more efficient).

## Common Errors

| Code | Meaning |
|------|---------|
| 400 | Missing required parameter (domain, email, or name) |
| 401 | Invalid API key |
| 403 | Rate limit reached (per-second cap) |
| 404 | Resource not found |
| 429 | Monthly usage limit exceeded |
| 451 | Unavailable for legal reasons (GDPR) |
