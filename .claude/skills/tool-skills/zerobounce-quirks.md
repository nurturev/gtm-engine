# ZeroBounce API — Tool Quirks & Best Practices

## When to Use
- **Primary use case:** Email validation — verify deliverability before sending campaigns
- **Best when:** You have emails from Apollo/Hunter/BetterContact and need to validate before loading into Instantly
- **Not for:** Finding emails (use Hunter/Apollo for that), phone validation, company enrichment

## Critical Gotchas

### 1. Status + sub_status together determine the action
```
status="valid"                          → Safe to send
status="invalid", sub_status="mailbox_not_found" → Hard bounce, remove
status="catch-all"                      → Domain accepts everything, risky
status="do_not_mail", sub_status="disposable"    → Temporary email, remove
status="do_not_mail", sub_status="role_based"    → info@, sales@ — avoid
status="spamtrap"                       → NEVER send, remove immediately
status="unknown"                        → Server unresponsive, retry later
```
Always check BOTH `status` and `sub_status`. A `do_not_mail` status has 7 different sub_statuses with different implications.

### 2. Credits are NOT consumed for unknown results
ZeroBounce only charges credits for definitive results (valid, invalid, catch-all, etc.). If the mail server times out and returns `unknown`, no credit is deducted. This is unique among validation providers.

### 3. Batch endpoint has strict limits
```
Single validation: GET /v2/validate         → no rate limit (effectively)
Batch validation:  POST /v2/validatebatch   → max 200 emails, max 5 calls/minute
Bulk file upload:  POST /v2/sendfile        → unlimited size, async processing
```
For 200+ emails, use the file upload endpoint (`/v2/sendfile`) instead of batch. The batch endpoint's 5/min limit means you can only validate 1,000 emails per minute.

### 4. Catch-all vs accept_all are DIFFERENT
```
status="catch-all"  → Domain catches all emails, ZeroBounce can't confirm deliverability
sub_status="accept_all" → Domain is on ZeroBounce's vetted allow-list, historically delivers
```
`accept_all` (returned as sub_status on valid emails) is safer than `catch-all` status. Segment catch-all emails separately — expect 10-30% bounce rate.

### 5. Response times vary wildly (1-30 seconds per email)
Some mail servers respond in 1 second, others greylist and take 30. Set `timeout` parameter (3-60 seconds) to control this. Default timeout can cause your application to hang on slow domains.

### 6. Regional endpoints matter for latency and compliance
```
US: https://api.zerobounce.net/v2/validate
US: https://api-us.zerobounce.net/v2/validate
EU: https://api-eu.zerobounce.net/v2/validate   ← Use for GDPR compliance
Bulk: https://bulkapi.zerobounce.net/v2/sendfile
```

## API Patterns

| Detail | Value |
|--------|-------|
| **Auth** | `api_key` query parameter on every request |
| **Base URL** | `https://api.zerobounce.net/v2` |
| **Single validate** | `GET /validate?api_key={key}&email={email}` |
| **Batch validate** | `POST /validatebatch` — body: array of `{email_address, ip_address}` |
| **Check credits** | `GET /getcredits?api_key={key}` — **FREE** |
| **File upload** | `POST /sendfile` — multipart form, async processing |
| **File status** | `GET /filestatus?api_key={key}&file_id={id}` |
| **File results** | `GET /getfile?api_key={key}&file_id={id}` |

### Key do_not_mail sub_statuses to watch for
| Sub-status | Meaning | Action |
|------------|---------|--------|
| `disposable` | Temporary/throwaway email (Mailinator, etc.) | Remove |
| `role_based` | info@, sales@, support@ | Avoid for cold outreach |
| `toxic` | Known for abuse/spam complaints | Remove immediately |
| `possible_trap` | May be a spam trap | Remove immediately |
| `global_suppression` | On known suppression lists | Remove |

## Rate Limits & Pricing

- Single validation: no hard rate limit (but 80,000/hour cap on credit-check endpoints)
- Batch validation: 200 emails max, 5 requests/minute
- Bad API key: >200 failed auth attempts in 1 hour triggers 24-hour block
- Credits NEVER expire

| Credits | Price | Per-validation cost |
|---------|-------|---------------------|
| 2,000 | Free (on signup) | $0 |
| 10,000 | ~$40 | ~$0.004 |
| 100,000 | ~$225 | ~$0.00225 |
| 1,000,000 | ~$1,500 | ~$0.0015 |

## Common Errors

| Scenario | Response |
|----------|----------|
| Invalid API key | `{"Credits": "-1"}` on getcredits, or error on validate |
| Missing email param | 400 error |
| Batch > 200 emails | 400 error |
| Batch > 5 calls/min | 429 rate limit |
| >200 bad auth/hour | 24-hour IP block |
| Server timeout | `status: "unknown"`, `sub_status: "timeout_exceeded"` — no credit charged |
