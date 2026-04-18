# Instantly.ai API — Tool Quirks & Best Practices

## When to Use
- **Primary use case:** Cold email campaign management — creating campaigns, managing leads, sending sequences
- **Best when:** You need to programmatically create/manage email campaigns with warmup, rotation, and analytics
- **Not for:** Email finding (use Apollo/Hunter), email validation (use ZeroBounce), CRM functionality

## Critical Gotchas

### 1. API access requires Hypergrowth plan ($97/mo) or above
```
Growth plan ($37/mo)    → NO API access
Hypergrowth ($97/mo)    → API + webhooks
Light Speed ($358/mo)   → API + webhooks + SISR
```
The Growth plan explicitly excludes API and webhook access. Do not attempt API calls on a Growth subscription.

### 2. V1 API is deprecated — use V2 only
```
// DEPRECATED
https://api.instantly.ai/api/v1/...

// CORRECT
https://api.instantly.ai/api/v2/...
```
V1 and V2 are NOT compatible. All new integrations must use V2. V1 will be fully removed.

### 3. Lead list endpoint is POST, not GET
```
// WRONG — standard REST assumption
GET /api/v2/leads?campaign_id=xxx

// CORRECT — POST because of complex filter arguments
POST /api/v2/leads/list
```
This is a deliberate deviation from REST. The list/search endpoint uses POST to support complex query bodies.

### 4. Campaign sequences array quirk — only first element is used
```json
// WRONG — multiple sequence objects
"sequences": [{"steps": [...]}, {"steps": [...]}]

// CORRECT — single sequence with multiple steps
"sequences": [{"steps": [{"subject": "...", "body": "..."}, {"subject": "...", "body": "..."}]}]
```
Even though `sequences` is an array, only the FIRST element is read. Put all your email steps inside that single sequence object.

### 5. Warmup must run 30+ days before campaigns
New email accounts MUST warm up for at least 30 days before cold outreach. Ramp from 5 sends/day toward 30/day max. Start campaigns before warmup completes and you risk domain blacklisting.

### 6. Lead custom variable values must be primitives
```json
// WRONG — objects/arrays in custom fields
{"company_info": {"name": "Acme", "size": 50}}

// CORRECT — string, number, boolean, or null only
{"company_name": "Acme", "company_size": 50, "is_enterprise": false}
```

## API Patterns

| Detail | Value |
|--------|-------|
| **Auth** | `Authorization: Bearer <api_key>` header |
| **Base URL** | `https://api.instantly.ai/api/v2` |
| **Rate limit** | 6,000 requests/minute (shared across all keys in workspace) |

### Key Endpoints

| Endpoint | Method | Path | Notes |
|----------|--------|------|-------|
| Create campaign | POST | `/campaigns` | Requires `campaign_schedule` |
| List campaigns | GET | `/campaigns` | Pagination supported |
| Get campaign | GET | `/campaigns/{id}` | |
| Pause campaign | POST | `/campaigns/{id}/pause` | |
| Activate campaign | POST | `/campaigns/{id}/activate` | |
| Campaign analytics | GET | `/campaigns/analytics?id={id}` | Omit `id` for all campaigns |
| Create lead | POST | `/leads` | |
| List leads | POST | `/leads/list` | POST not GET |
| Move leads | POST | `/leads/move` | Between campaigns/lists |
| Enable warmup | POST | `/accounts/warmup/enable` | |
| Disable warmup | POST | `/accounts/warmup/disable` | |
| Warmup analytics | POST | `/accounts/warmup-analytics` | |
| Account list | GET | `/accounts` | Connected email accounts |

### Campaign creation flow
```
1. Connect email accounts (manual in UI or POST /accounts)
2. Enable warmup → POST /accounts/warmup/enable
3. Wait 30 days (monitor via POST /accounts/warmup-analytics)
4. Create campaign → POST /campaigns (with schedule, sequences, settings)
5. Add leads → POST /leads (with campaign_id)
6. Activate → POST /campaigns/{id}/activate
7. Monitor → GET /campaigns/analytics
```

## Rate Limits & Pricing

| Plan | Emails/mo | Contacts | API Access | Price/mo |
|------|----------|----------|------------|----------|
| Growth | 5,000 | 1,000 | No | $37 |
| Hypergrowth | 100,000 | 25,000 | Yes | $97 |
| Light Speed | 500,000 | 100,000 | Yes | $358 |

- Rate limit: 6,000 req/min workspace-wide (shared between V1 and V2)
- Hard cap on monthly emails — sending pauses when limit reached (no overage billing)
- Warmup emails don't count against monthly send limits
- Best practice: 50-100 email accounts, each sending 30-50 emails/day

## Common Errors

| Code | Meaning |
|------|---------|
| 401 | Invalid or expired API key (keys shown only once at creation) |
| 403 | Missing required scope (e.g., `campaigns:create`) |
| 429 | Rate limit exceeded (6,000/min) — implement exponential backoff |
| 400 | Invalid field type (e.g., object in custom variable instead of primitive) |
| Campaign not sending | Warmup incomplete, account not connected, or monthly limit reached |
