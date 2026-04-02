# GTM Engine — Enrichment API Integration Guide

**Audience**: Platform microservices (consultant agent, orchestrator, etc.) that need to call GTM Engine execution APIs.

---

## 1. Authentication

GTM Engine uses its own JWT tokens. Platform services exchange a Supabase JWT for a GTM Engine token before calling any execution endpoint.

### Get a GTM Engine Token

```
POST /api/v1/auth/exchange

Request:
{
    "supabase_jwt": "<user's Supabase access token>",
    "tenant_id": "<platform tenant ID, e.g. '137'>",
    "email": "<user's email, optional — for audit logging>",
    "channel": "consultant"
}

Response:
{
    "access_token": "ey...",
    "token_type": "bearer",
    "expires_in": 86400
}
```

**Notes:**
- `tenant_id` is passed explicitly — it is NOT inside the Supabase JWT. Your service gets it from the user management service (e.g., `attach_tenant` response).
- `channel` identifies the calling system. Use `"consultant"` for the consultant agent, or a descriptive label for your service. This appears in run logs and analytics.
- The returned `access_token` is a 24-hour GTM Engine JWT. **Cache it** for the session.
- **No refresh token is issued.** When the token expires (HTTP 401), call `/exchange` again with the current Supabase JWT.

### Using the Token

All subsequent API calls:
```
Authorization: Bearer <gtm-engine-access-token>
```

### Optional Headers

| Header | Purpose |
|--------|---------|
| `X-Workflow-Id` | Group related calls into a workflow for the run log. Pass a UUID per session/conversation. |
| `X-Tool-Name` | Label for the specific tool call (e.g., `consultant:enrich_person`). Appears in run logs. |
| `X-Workflow-Label` | Human-readable session label (e.g., user's query). |

---

## 2. Endpoints

### 2.1 Single Execution

```
POST /api/v1/execute
Authorization: Bearer <token>
```

**Request:**
```json
{
    "operation": "enrich_person",
    "provider": null,
    "params": {
        "email": "john@acme.com"
    }
}
```

**Response (200):**
```json
{
    "execution_id": "exec_a1b2c3d4",
    "status": "success",
    "credits_charged": 1.0,
    "result": {
        "full_name": "John Doe",
        "title": "VP Sales",
        "company": "Acme Corp",
        "linkedin_url": "https://linkedin.com/in/johndoe",
        "...": "..."
    }
}
```

**`provider`** is optional. When `null`, the default provider for the operation is used (see section 4). Override only when you need a specific provider.

### 2.2 Batch Execution

```
POST /api/v1/execute/batch
Authorization: Bearer <token>
```

**Request:**
```json
{
    "operations": [
        { "operation": "enrich_person", "provider": null, "params": { "email": "a@co.com" } },
        { "operation": "enrich_person", "provider": null, "params": { "email": "b@co.com" } }
    ]
}
```

**Response (200):**
```json
{
    "batch_id": "batch_xyz",
    "total": 2,
    "status": "completed"
}
```

**Constraints:**
- Maximum 25 records per batch.
- All operations in the batch must be the same type and provider.
- Records are processed concurrently (5 at a time).

### 2.3 Poll Batch Status

```
GET /api/v1/execute/batch/{batch_id}
Authorization: Bearer <token>
```

**Response (200):**
```json
{
    "batch_id": "batch_xyz",
    "total": 2,
    "completed": 2,
    "failed": 0,
    "status": "completed",
    "results": [
        { "execution_id": "exec_1", "status": "success", "data": { "..." : "..." } },
        { "execution_id": "exec_2", "status": "success", "data": { "..." : "..." } }
    ]
}
```

### 2.4 Cost Estimate (Free, No Credits Charged)

```
POST /api/v1/execute/cost
Authorization: Bearer <token>
```

**Request:**
```json
{
    "operation": "search_people",
    "params": { "per_page": 50 }
}
```

**Response (200):**
```json
{
    "operation": "search_people",
    "estimated_credits": 2.0,
    "breakdown": "Search: 50 results/page x 1 credit per 25 results = 2.0 credits (page 1)",
    "is_free_with_byok": true
}
```

### 2.5 Search Patterns (Free, No Credits Charged)

```
GET /api/v1/search/patterns?platform=linkedin_jobs
Authorization: Bearer <token>
```

Returns platform-specific Google search query templates and tips. Call this before constructing Google search queries.

---

## 3. Error Handling

| Status | Meaning | Action |
|--------|---------|--------|
| **401** | Token expired or invalid | Re-exchange via `POST /auth/exchange` |
| **402** | Insufficient credits | Show user a message. Detail: `"Insufficient credits: need X, have Y"` |
| **400** | Bad request (missing params, invalid operation) | Fix the request |
| **429** | Rate limited (per-tenant per-provider) | Retry after backoff. The `Retry-After` header may be present. |
| **502** | Upstream provider error | Retry with backoff, or try a different provider |
| **500** | Internal server error | Retry once, then report |

### Error Response Shape

```json
{
    "detail": "Insufficient credits: need 1.0, have 0.0"
}
```

The `detail` field is always a human-readable string.

---

## 4. Supported Operations

### Enrichment

| Operation | Default Provider | Params | Cost |
|-----------|-----------------|--------|------|
| `enrich_person` | apollo | `email` or `linkedin_url` or `name` + `company` | 1 credit |
| `enrich_company` | apollo | `domain` or `name` | 1 credit |

### People & Company Search

| Operation | Default Provider | Key Params | Cost |
|-----------|-----------------|------------|------|
| `search_people` | apollo | `titles`, `companies`, `locations`, `per_page` | ceil(per_page/25) credits |
| `search_companies` | apollo | `keywords`, `locations`, `per_page` | ceil(per_page/25) credits |

### Web Intelligence

| Operation | Default Provider | Params | Cost |
|-----------|-----------------|--------|------|
| `search_web` | rapidapi_google | `query`, `tbs` (time filter), `site` | 1 credit |
| `scrape_page` | parallel_web | `url` | 1 credit |
| `crawl_site` | parallel_web | `url`, `max_pages` | 1 credit |
| `extract_structured` | parallel_web | `url`, `schema` | 1 credit |

### Company Signals (PredictLeads)

| Operation | Default Provider | Params | Cost |
|-----------|-----------------|--------|------|
| `company_jobs` | predictleads | `domain` | 1 credit |
| `company_technologies` | predictleads | `domain` | 1 credit |
| `company_news` | predictleads | `domain` | 1 credit |
| `company_financing` | predictleads | `domain` | 1 credit |
| `similar_companies` | predictleads | `domain` | 1 credit |

### Bulk Operations

| Operation | Default Provider | Params | Cost |
|-----------|-----------------|--------|------|
| `bulk_enrich_people` | apollo | `details` (list of person params) | 1 credit per record |
| `bulk_enrich_companies` | apollo | `domains` (list of domains) | 1 credit per record |

---

## 5. Credit Behavior

- **Platform key calls**: Cost credits as listed above.
- **BYOK calls**: Free. If the tenant has added their own API key for a provider, no credits are charged.
- **Cache hits**: Free. If an identical operation was run recently (same tenant, same params), the cached result is returned at no cost.
- **`credits_charged`** in the response tells you the actual cost (0.0 for free calls).

---

## 6. Typical Integration Flow

```
1. User authenticates on app.nrev.ai via Supabase
2. Your service gets the Supabase JWT + tenant_id

3. Exchange token (once per session):
   POST /api/v1/auth/exchange
   { supabase_jwt, tenant_id, channel: "your-service-name" }
   → Cache the access_token (24h TTL)

4. For each enrichment request:
   POST /api/v1/execute
   Authorization: Bearer <cached-token>
   X-Workflow-Id: <session-uuid>
   { operation, params }

5. Handle errors:
   - 401 → re-exchange token
   - 402 → insufficient credits, surface to user
   - 502 → retry with backoff

6. On session end: discard the cached token (no logout needed)
```

---

## 7. Environment Configuration

Your service needs these environment variables to connect to GTM Engine:

| Variable | Example | Purpose |
|----------|---------|---------|
| `GTM_ENGINE_URL` | `http://gtm-engine.internal:8000` | Base URL for GTM Engine API |
| `SUPABASE_JWT_SECRET` | (shared secret) | Must match GTM Engine's `SUPABASE_JWT_SECRET` for exchange to work |

The exchange endpoint validates your Supabase JWT using this shared secret. Both your service and GTM Engine must have the same `SUPABASE_JWT_SECRET` configured.

---

## 8. Quick Reference

```bash
# 1. Get a token
TOKEN=$(curl -s -X POST "$GTM_ENGINE_URL/api/v1/auth/exchange" \
  -H "Content-Type: application/json" \
  -d '{"supabase_jwt":"'$SUPABASE_JWT'","tenant_id":"137","channel":"test"}' \
  | jq -r '.access_token')

# 2. Check cost (free)
curl -s -X POST "$GTM_ENGINE_URL/api/v1/execute/cost" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"operation":"enrich_person","params":{"email":"john@acme.com"}}'

# 3. Enrich a person
curl -s -X POST "$GTM_ENGINE_URL/api/v1/execute" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Workflow-Id: test-session-1" \
  -d '{"operation":"enrich_person","params":{"email":"john@acme.com"}}'

# 4. Batch enrich
curl -s -X POST "$GTM_ENGINE_URL/api/v1/execute/batch" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"operations":[
    {"operation":"enrich_person","params":{"email":"a@co.com"}},
    {"operation":"enrich_person","params":{"email":"b@co.com"}}
  ]}'
```
