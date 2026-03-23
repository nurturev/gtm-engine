# nrev-lite — Architecture & Product Specification

> Version: 1.0.0-draft
> Date: 2026-03-15
> Author: Sayanta Ghosh / nRev
> Status: Pre-implementation

---

## Table of Contents

1. [Product Vision](#1-product-vision)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [Component Breakdown](#3-component-breakdown)
4. [Authentication & Onboarding](#4-authentication--onboarding)
5. [API Gateway Design](#5-api-gateway-design)
6. [Provider Proxy & Execution Engine](#6-provider-proxy--execution-engine)
7. [Database Architecture](#7-database-architecture)
8. [Credit & Billing System](#8-credit--billing-system)
9. [Key Vault & Security](#9-key-vault--security)
10. [CLI Package (nrev-lite)](#10-cli-package-nrv)
11. [Skills Architecture](#11-skills-architecture)
12. [MCP Strategy](#12-mcp-strategy)
13. [Dashboard & Interactive Tables](#13-dashboard--interactive-tables)
14. [AWS Infrastructure](#14-aws-infrastructure)
15. [API Reference](#15-api-reference)
16. [Database Schema](#16-database-schema)
17. [Phased Roadmap](#17-phased-roadmap)
18. [Open Questions & Decisions Log](#18-open-questions--decisions-log)

---

## 1. Product Vision

### What is nrev-lite?

nrev-lite is an agent-native GTM (Go-To-Market) execution platform. It gives GTM engineers, RevOps teams, and growth operators a single interface — through Claude Code — to enrich leads, search companies, validate contacts, sequence outreach, and build dashboards on their GTM data.

### Core Principles

1. **Intelligence on the client, execution on the cloud.** Claude Code + nrev-lite skills handle GTM reasoning, workflow orchestration, and data massaging. The nrev-lite cloud handles API calls, rate limits, pagination, key management, and billing.

2. **One command, many providers.** Users never call Apollo or RocketReach directly. They call nrev-lite, which routes to the best provider (or multiple in parallel/waterfall).

3. **BYOK + managed keys.** Users can bring their own API keys (free) or use nrev-lite's platform keys (credits). Both paths go through the same secure gateway.

4. **Data you can build on.** Every enrichment result, every API call, every workflow output writes to interactive tables in the user's tenant. Users build dashboards on this data using Claude Code, then optionally deploy them to nrev-lite cloud.

5. **Credit-based pricing with a free tier.** New users get free credits (~$2 worth) to try the platform. After that, top-up via one-time or recurring payments.

### How It Differs from Deepline

| Aspect | Deepline | nrev-lite |
|--------|----------|-----|
| Primary interface | CLI commands | Claude Code skills (MCP-native) |
| GTM intelligence | Optional skills | Core differentiator — deep GTM playbooks |
| Data layer | PostgreSQL per workspace | Interactive tables + user-deployable dashboards |
| Dashboard hosting | Not offered | Users build with Claude Code, deploy to nrev-lite cloud |
| Free tier | None for managed keys | $2 free credits on signup |
| MCP support | On roadmap | Day-one MCP support |
| Provider parallelization | Sequential waterfall | Parallel + waterfall (configurable) |
| Install | Node.js + Python + CLI | Pure Python pip install |

---

## 2. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      USER'S MACHINE                             │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Claude Code                                              │   │
│  │  ├─ nrev-lite MCP Skills (pip installed)                        │   │
│  │  │   ├─ GTM knowledge & reasoning                         │   │
│  │  │   ├─ Workflow orchestration                            │   │
│  │  │   ├─ Data massaging & transformation                   │   │
│  │  │   ├─ ICP scoring logic                                 │   │
│  │  │   └─ Dashboard generation                              │   │
│  │  │                                                        │   │
│  │  ├─ nrev-lite CLI (thin client)                                 │   │
│  │  │   ├─ nrev-lite auth login (Google OAuth)                     │   │
│  │  │   ├─ nrev-lite enrich <target>                               │   │
│  │  │   ├─ nrev-lite search <query>                                │   │
│  │  │   ├─ nrev-lite query <sql>                                   │   │
│  │  │   ├─ nrev-lite dashboard deploy <path>                       │   │
│  │  │   └─ nrev-lite keys add <provider>                           │   │
│  │  │                                                        │   │
│  │  ├─ Native MCP connectors (user's own)                    │   │
│  │  │   └─ HubSpot MCP, Slack MCP, etc.                      │   │
│  │  │                                                        │   │
│  │  └─ nrev-lite MCP Server (optional, for non-CLI MCP calls)      │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                       │
│                         │ HTTPS (JWT auth)                      │
└─────────────────────────┼───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      nrev-lite CLOUD (AWS)                            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  API Gateway (ALB + FastAPI on ECS Fargate)               │   │
│  │  ├─ POST /api/v1/auth/login                               │   │
│  │  ├─ POST /api/v1/auth/callback                            │   │
│  │  ├─ POST /api/v1/execute                                  │   │
│  │  ├─ POST /api/v1/execute/batch                            │   │
│  │  ├─ GET  /api/v1/tables/{table}                           │   │
│  │  ├─ POST /api/v1/query                                    │   │
│  │  ├─ POST /api/v1/keys                                     │   │
│  │  ├─ GET  /api/v1/credits                                  │   │
│  │  ├─ POST /api/v1/credits/topup                            │   │
│  │  ├─ POST /api/v1/dashboards/deploy                        │   │
│  │  └─ GET  /api/v1/usage                                    │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                       │
│  ┌──────────────────────┴───────────────────────────────────┐   │
│  │  Execution Engine                                         │   │
│  │  ├─ Provider Router (waterfall + parallel)                │   │
│  │  ├─ Rate Limiter (token bucket per provider per tenant)   │   │
│  │  ├─ Retry Manager (exponential backoff)                   │   │
│  │  ├─ Pagination Handler (auto-paginate, stream results)    │   │
│  │  ├─ Response Normalizer (unified schema per operation)    │   │
│  │  └─ Result Cache (dedup identical requests)               │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                       │
│  ┌──────────────────────┴───────────────────────────────────┐   │
│  │  Data Layer                                               │   │
│  │  ├─ Aurora Serverless v2 (PostgreSQL 15)                  │   │
│  │  │   ├─ Tenant isolation via Row-Level Security (RLS)     │   │
│  │  │   ├─ Interactive tables (contacts, companies, etc.)    │   │
│  │  │   ├─ Enrichment history & audit log                    │   │
│  │  │   ├─ Credit ledger                                     │   │
│  │  │   └─ User/tenant metadata                              │   │
│  │  │                                                        │   │
│  │  ├─ ElastiCache Redis                                     │   │
│  │  │   ├─ Rate limit counters                               │   │
│  │  │   ├─ Response cache (TTL-based)                        │   │
│  │  │   └─ Session tokens                                    │   │
│  │  │                                                        │   │
│  │  ├─ S3                                                    │   │
│  │  │   ├─ Dashboard hosting (per tenant)                    │   │
│  │  │   ├─ Export files (CSV, JSON)                          │   │
│  │  │   └─ Batch job results                                 │   │
│  │  │                                                        │   │
│  │  └─ AWS Secrets Manager + KMS                             │   │
│  │      ├─ Platform API keys (managed keys)                  │   │
│  │      └─ BYOK keys (encrypted with tenant-scoped KMS key) │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Async Workers (Lambda)                                   │   │
│  │  ├─ Batch enrichment jobs                                 │   │
│  │  ├─ Webhook delivery                                      │   │
│  │  ├─ Dashboard build & deploy                              │   │
│  │  └─ Credit reconciliation                                 │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  CloudFront CDN                                           │   │
│  │  ├─ Dashboard serving (tenant.dashboards.nrev_lite.TLD)         │   │
│  │  └─ Static assets                                         │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Web Application (Next.js on ECS or Amplify)              │   │
│  │  ├─ Signup / Login (Google OAuth)                         │   │
│  │  ├─ Usage dashboard                                       │   │
│  │  ├─ Credit balance & top-up                               │   │
│  │  ├─ API key management                                    │   │
│  │  ├─ Provider configuration                                │   │
│  │  └─ Interactive table browser                             │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Breakdown

### 3.1 Client-Side Components

| Component | What It Does | Technology |
|-----------|-------------|------------|
| **nrev-lite CLI** | Thin client — auth, API calls, local config | Python 3.10+, Click, httpx |
| **nrev-lite Skills** | GTM intelligence, workflow logic, data transformation | Python, CLAUDE.md skill format |
| **nrv MCP Server** | Exposes nrev-lite capabilities as MCP tools (optional) | Python, MCP SDK |
| **Local Config** | Stores auth token, tenant ID, preferences | `~/.nrev-lite/config.toml`, `~/.nrev-lite/credentials` |

### 3.2 Server-Side Components

| Component | What It Does | Technology |
|-----------|-------------|------------|
| **API Gateway** | Request routing, auth, rate limiting | FastAPI on ECS Fargate |
| **Execution Engine** | Provider routing, parallelization, retries | Python asyncio + aiohttp |
| **Data Layer** | Tenant data, interactive tables, audit logs | Aurora Serverless v2 PostgreSQL |
| **Cache** | Rate limit counters, response cache | ElastiCache Redis |
| **Key Vault** | Secure key storage (BYOK + platform) | AWS Secrets Manager + KMS |
| **Async Workers** | Batch jobs, webhook delivery, dashboard builds | AWS Lambda + SQS |
| **Dashboard CDN** | Serve user-built dashboards | S3 + CloudFront |
| **Web App** | User-facing dashboard, billing, config | Next.js on ECS or Amplify |

---

## 4. Authentication & Onboarding

### 4.1 Google OAuth Flow (CLI)

The CLI uses the **localhost loopback** OAuth flow — the same pattern used by `gh auth login`, `vercel login`, and `gcloud auth login`.

```
User runs: nrev-lite auth login

Step 1: CLI starts temporary HTTP server on localhost:PORT
Step 2: CLI opens browser → https://api.nrev_lite.TLD/auth/google
        (with redirect_uri=http://localhost:PORT/callback)
Step 3: User authenticates with Google in browser
Step 4: Google redirects to nrev-lite server with auth code
Step 5: nrev-lite server exchanges code for Google tokens
Step 6: nrev-lite server creates/finds user, creates tenant if new
Step 7: nrev-lite server issues JWT (access + refresh tokens)
Step 8: nrev-lite server redirects to http://localhost:PORT/callback?token=JWT
Step 9: CLI receives JWT, stores in ~/.nrev-lite/credentials
Step 10: CLI displays "✓ Authenticated as user@gmail.com (tenant: xyz)"
```

#### Fallback: Device Code Flow
For headless environments (SSH, containers) where a browser can't be opened:

```
User runs: nrev-lite auth login --headless

Step 1: CLI requests device code from nrev-lite server
Step 2: CLI displays: "Visit https://api.nrev_lite.TLD/device and enter code: ABCD-1234"
Step 3: User visits URL, authenticates with Google
Step 4: CLI polls nrev-lite server for token completion
Step 5: Once authenticated, CLI stores JWT
```

### 4.2 Google OAuth Flow (Web App)

Standard web OAuth:

```
Step 1: User visits app.nrev_lite.TLD → clicks "Sign in with Google"
Step 2: Redirects to Google OAuth consent screen
Step 3: Google redirects back to app.nrev_lite.TLD/auth/callback
Step 4: Server exchanges code for tokens, creates session
Step 5: User lands on dashboard
```

### 4.3 Onboarding Wizard (runs after first auth)

Whether from CLI or web, new users go through onboarding:

```
CLI version (interactive):
  $ nrev-lite auth login
  ✓ Authenticated as sayanta@nrev.com

  Welcome to nrev-lite! Let's set up your GTM workspace.

  Company name: nRev
  Company domain: nrev.com
  GTM stage: [1] Pre-PMF  [2] Early traction  [3] Scaling  [4] Enterprise
  → 2
  Primary goals: [1] Lead gen  [2] Enrichment  [3] Outreach  [4] Analytics
  → 1,2,3

  ✓ Workspace created: nrev-a8f3c2e1
  ✓ 200 free credits added to your account

  Next steps:
    nrev-lite keys add apollo     # Connect your Apollo key (free usage)
    nrev-lite keys add rocketreach # Connect your RocketReach key
    nrev-lite setup-claude         # Install Claude Code skills
```

Web version: Same fields, rendered as a multi-step form.

### 4.4 Credential Storage

```
~/.nrev-lite/
├── config.toml          # tenant_id, default provider prefs, MCP config
├── credentials          # JWT access token + refresh token (file permissions 600)
└── skills/              # Installed skill files
```

**config.toml example:**
```toml
[tenant]
id = "nrev-a8f3c2e1"
name = "nRev"

[auth]
email = "sayanta@nrev.com"

[providers]
# "nrev-lite" = use platform keys (costs credits)
# "byok" = use your own key
# "native" = use user's own MCP connector (free, no nrev-lite involvement)
apollo = "byok"
rocketreach = "nrev-lite"
google_search = "nrev-lite"
hubspot = "native"

[preferences]
default_enrichment_strategy = "parallel"  # or "waterfall"
spend_cap_monthly = 500  # credits
```

---

## 5. API Gateway Design

### 5.1 Base URL

```
https://api.nrev_lite.TLD/api/v1/
```

### 5.2 Authentication

All API requests (except auth endpoints) require a JWT Bearer token:

```
Authorization: Bearer eyJhbGciOiJSUzI1NiIs...
```

JWTs are issued by the nrev-lite server, signed with RS256, and contain:

```json
{
  "sub": "user_2a8f3c2e",
  "tenant_id": "nrev-a8f3c2e1",
  "email": "sayanta@nrev.com",
  "role": "owner",
  "iat": 1710460800,
  "exp": 1710547200
}
```

- Access tokens: 24-hour expiry
- Refresh tokens: 30-day expiry, rotated on use
- Stored in HttpOnly cookies (web) or `~/.nrev-lite/credentials` (CLI)

### 5.3 Rate Limiting

Two layers:

1. **API rate limit** (per tenant): 100 requests/minute to the gateway itself
2. **Provider rate limit** (per provider per tenant): Respects each provider's limits

Rate limit headers returned on every response:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1710461400
X-Credits-Remaining: 142
```

### 5.4 Error Format

```json
{
  "error": {
    "code": "INSUFFICIENT_CREDITS",
    "message": "This operation requires 3 credits. Your balance is 1.",
    "details": {
      "required": 3,
      "balance": 1,
      "topup_url": "https://app.nrev_lite.TLD/credits"
    }
  }
}
```

Standard error codes:

| Code | HTTP | Meaning |
|------|------|---------|
| `AUTH_REQUIRED` | 401 | Missing or invalid JWT |
| `AUTH_EXPIRED` | 401 | Token expired, use refresh |
| `FORBIDDEN` | 403 | No access to this tenant resource |
| `INSUFFICIENT_CREDITS` | 402 | Not enough credits |
| `SPEND_CAP_REACHED` | 402 | Monthly spend cap hit |
| `PROVIDER_ERROR` | 502 | Upstream provider returned error |
| `PROVIDER_RATE_LIMITED` | 429 | Provider rate limit hit, will retry |
| `INVALID_REQUEST` | 400 | Malformed request payload |
| `NOT_FOUND` | 404 | Resource not found |
| `INTERNAL_ERROR` | 500 | Server error |

---

## 6. Provider Proxy & Execution Engine

### 6.1 Core Concept

The execution engine is the heart of nrev-lite's cloud. It receives a standardized request from the CLI/skills, routes it to one or more providers, handles all the complexity (auth, rate limits, pagination, retries), normalizes the response, and writes results to the tenant's interactive tables.

### 6.2 Request Flow

```
CLI/Skill sends:
POST /api/v1/execute
{
  "operation": "enrich_person",
  "params": {
    "email": "john@acme.com",
    "fields": ["name", "title", "company", "phone", "linkedin"]
  },
  "strategy": "parallel",        // "parallel" | "waterfall" | "single"
  "providers": ["apollo", "rocketreach"],  // optional override
  "dry_run": false                // true = return cost estimate only
}
```

### 6.3 Execution Strategies

#### Parallel Strategy
All specified providers are called simultaneously. Results are merged — best value per field wins (based on confidence scoring and recency).

```
Request: enrich_person(email="john@acme.com")
  ├─ [parallel] Apollo API ──────→ {name: "John Doe", title: "VP Sales", phone: null}
  ├─ [parallel] RocketReach API ─→ {name: "John Doe", title: "VP of Sales", phone: "+1-555-0123"}
  └─ Merge ──────────────────────→ {name: "John Doe", title: "VP of Sales", phone: "+1-555-0123",
                                    _sources: {name: "apollo", title: "rocketreach", phone: "rocketreach"}}
```

#### Waterfall Strategy
Providers are called in sequence. If provider N returns a complete result, stop. Otherwise, fall through to provider N+1 for missing fields.

```
Request: enrich_person(email="john@acme.com")
  ├─ [1] Apollo API ──→ {name: "John Doe", title: "VP Sales", phone: null}
  │   └─ phone missing → continue
  ├─ [2] RocketReach ──→ {phone: "+1-555-0123"}
  │   └─ all fields filled → stop
  └─ Result ───────────→ {name: "John Doe", title: "VP Sales", phone: "+1-555-0123"}
```

#### Single Strategy
Call exactly one provider. Used when user specifies a specific provider.

### 6.4 Provider Abstraction

Each provider is implemented as a Python class with a standard interface:

```python
class BaseProvider(ABC):
    """Every provider implements this interface."""

    name: str                    # "apollo", "rocketreach", etc.
    operations: list[str]        # ["enrich_person", "search_company", ...]
    rate_limit: RateLimit        # requests per second/minute
    credit_cost: dict[str, int]  # {"enrich_person": 2, "search_company": 3}

    @abstractmethod
    async def execute(self, operation: str, params: dict) -> ProviderResult:
        """Execute an operation and return normalized results."""
        pass

    @abstractmethod
    def normalize(self, raw_response: dict, operation: str) -> dict:
        """Normalize provider-specific response to nrev-lite schema."""
        pass
```

### 6.5 Supported Operations (v1)

| Operation | Description | Providers | Credit Cost |
|-----------|-------------|-----------|-------------|
| `enrich_person` | Get person details by email/name/LinkedIn | Apollo, RocketReach | 2 |
| `enrich_company` | Get company details by domain/name | Apollo, RocketReach | 2 |
| `search_people` | Find people matching criteria | Apollo, RocketReach | 3 |
| `search_companies` | Find companies matching criteria | Apollo | 3 |
| `google_search` | Web search via Google (RapidAPI) | RapidAPI Google | 1 |
| `web_scrape` | Extract structured data from URLs | Parallel Web | 1 |
| `validate_email` | Check if email is valid/deliverable | Built-in (SMTP check) | 0.5 |

### 6.6 Rate Limiting Engine

Uses a **token bucket** algorithm per provider per tenant, stored in Redis:

```python
class RateLimiter:
    """
    Token bucket rate limiter.
    Each provider has a bucket with capacity and refill rate.
    """

    # Provider rate limits (requests per minute)
    LIMITS = {
        "apollo": {"rpm": 50, "burst": 10},
        "rocketreach": {"rpm": 30, "burst": 5},
        "rapidapi_google": {"rpm": 100, "burst": 20},
        "parallel_web": {"rpm": 60, "burst": 10},
    }

    async def acquire(self, provider: str, tenant_id: str) -> bool:
        """Try to acquire a token. Returns True if allowed, False if rate limited."""
        key = f"ratelimit:{tenant_id}:{provider}"
        # Redis token bucket implementation
        ...

    async def wait_and_acquire(self, provider: str, tenant_id: str) -> float:
        """Wait until a token is available. Returns wait time in seconds."""
        ...
```

### 6.7 Retry Logic

```python
RETRY_CONFIG = {
    "max_retries": 3,
    "backoff_base": 1.0,        # seconds
    "backoff_factor": 2.0,      # exponential
    "backoff_max": 30.0,        # max wait
    "retryable_status_codes": [429, 500, 502, 503, 504],
    "retryable_exceptions": [ConnectionError, TimeoutError],
}
```

### 6.8 Pagination Handler

For operations that return paginated results (e.g., `search_people` with 500 results across 10 pages):

```python
class PaginationHandler:
    """
    Automatically handles pagination for providers that return paginated results.
    Streams results back to client as they arrive.
    """

    async def paginate(self, provider: BaseProvider, operation: str, params: dict,
                       max_results: int = 100) -> AsyncIterator[dict]:
        """
        Yields results page by page.
        Handles cursor-based, offset-based, and page-number-based pagination.
        Respects rate limits between pages.
        """
        ...
```

### 6.9 Response Cache

To avoid duplicate API calls and save credits:

```python
class ResponseCache:
    """
    Cache enrichment results in Redis with TTL.
    Same request within TTL returns cached result at 0 credit cost.
    """

    TTL = {
        "enrich_person": 86400 * 7,     # 7 days
        "enrich_company": 86400 * 7,    # 7 days
        "search_people": 3600,          # 1 hour
        "google_search": 3600,          # 1 hour
        "web_scrape": 86400,            # 1 day
    }
```

---

## 7. Database Architecture

### 7.1 Technology Choice

**Aurora Serverless v2 (PostgreSQL 15)** with Row-Level Security (RLS).

Why:
- **AWS-native**: No additional vendor. Uses free credits.
- **Serverless v2**: Scales from 0.5 ACU to 128 ACU. Near-zero cost when idle, handles spikes automatically. 0.5 ACU increments for fine-grained scaling.
- **PostgreSQL 15**: Full SQL, JSONB for flexible data, RLS for tenant isolation, pg_cron for scheduled jobs.
- **RDS Data API**: HTTP-based SQL access — no persistent connections needed. Perfect for Lambda workers.
- **Pool model**: Single database, shared tables, RLS enforces tenant boundaries. Most cost-effective for a startup.

### 7.2 Multi-Tenant Isolation

Every table has a `tenant_id` column. RLS policies ensure tenants can only see their own data:

```sql
-- Enable RLS on all tenant tables
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;

-- Policy: tenants can only see their own rows
CREATE POLICY tenant_isolation ON contacts
    USING (tenant_id = current_setting('app.current_tenant')::text);

-- Force RLS even for table owners
ALTER TABLE contacts FORCE ROW LEVEL SECURITY;
```

Every API request sets the tenant context before any query:

```sql
SET LOCAL app.current_tenant = 'nrev-a8f3c2e1';
```

### 7.3 Interactive Tables

These are the core data tables that users can query, build dashboards on, and extend:

#### Core Tables (auto-created per tenant)

1. **contacts** — Enriched people
2. **companies** — Enriched organizations
3. **enrichment_log** — Audit trail of every enrichment
4. **search_results** — Saved search outputs
5. **sequences** — Outreach sequence definitions
6. **sequence_steps** — Individual steps in sequences
7. **workflows** — Saved workflow definitions

#### How Users Interact

Users query their tables through the CLI or skills:

```bash
# Direct SQL query
nrev-lite query "SELECT name, email, company FROM contacts WHERE icp_score > 80 ORDER BY created_at DESC LIMIT 20"

# Skill-driven (Claude Code)
"Show me all contacts from Series B companies in fintech that we enriched this week"
→ Skill translates to SQL → nrev-lite query → results displayed
```

#### Custom Columns

Users can add custom columns to any core table:

```bash
nrev-lite table contacts add-column "outreach_status" text default 'not_contacted'
nrev-lite table contacts add-column "deal_size" numeric
```

This is stored as JSONB in an `extensions` column, keeping the core schema stable while allowing per-tenant customization.

### 7.4 Read Access for Dashboards

Users need to query their tables to build dashboards. Two access methods:

1. **REST API** (primary): `GET /api/v1/tables/{table}?filter=...&sort=...&limit=...`
2. **SQL API**: `POST /api/v1/query` with raw SQL (read-only, RLS-enforced)

Both return JSON that Claude Code can use to generate charts, tables, and dashboards.

---

## 8. Credit & Billing System

### 8.1 Credit Model

Credits are the universal billing unit. 1 credit ≈ $0.01 USD.

#### Credit Costs

| Operation | Platform Key (credits) | BYOK (credits) |
|-----------|----------------------|-----------------|
| `enrich_person` | 2 | 0 |
| `enrich_company` | 2 | 0 |
| `search_people` (per page) | 3 | 0 |
| `search_companies` (per page) | 3 | 0 |
| `google_search` | 1 | 0 |
| `web_scrape` | 1 | 0 |
| `validate_email` | 0.5 | 0 |
| MCP action (CRM write, etc.) | 0.1 | 0 |
| Cached result (any operation) | 0 | 0 |
| Dashboard hosting (per month) | 10 | 10 |

#### Free Tier

New users receive **200 credits** ($2 value) on signup. This allows approximately:
- 100 person enrichments, or
- 200 Google searches, or
- 66 people searches, or
- Any combination

#### Top-Up Options

| Package | Credits | Price | Per Credit |
|---------|---------|-------|------------|
| Starter | 500 | $5 | $0.010 |
| Growth | 2,000 | $18 | $0.009 |
| Scale | 10,000 | $80 | $0.008 |
| Custom | Any | Custom | Negotiable |

#### Recurring Plans (optional)

| Plan | Monthly Credits | Price/mo | Per Credit |
|------|----------------|----------|------------|
| Free | 200 (one-time) | $0 | — |
| Pro | 2,000 | $15/mo | $0.0075 |
| Team | 10,000 | $60/mo | $0.006 |

### 8.2 Credit Ledger (Double-Entry)

Every credit movement is recorded as a ledger entry. This is immutable — no updates, only inserts.

```sql
CREATE TABLE credit_ledger (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    entry_type      TEXT NOT NULL,    -- 'credit' | 'debit' | 'hold' | 'release'
    amount          NUMERIC(10,2) NOT NULL,
    balance_after   NUMERIC(10,2) NOT NULL,
    operation       TEXT,             -- 'enrich_person', 'signup_bonus', 'topup', etc.
    reference_id    TEXT,             -- enrichment_log.id, payment.id, etc.
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

#### Credit Flow for an API Call

```
1. Request arrives: enrich_person via platform key → 2 credits needed
2. CHECK:  SELECT balance FROM credit_balances WHERE tenant_id = ?
           → balance = 47
3. HOLD:   INSERT INTO credit_ledger (entry_type='hold', amount=2, balance_after=45)
4. EXECUTE: Call Apollo API
5a. SUCCESS: INSERT INTO credit_ledger (entry_type='debit', amount=2, balance_after=45)
             DELETE the hold entry
5b. FAILURE: INSERT INTO credit_ledger (entry_type='release', amount=2, balance_after=47)
             Release the hold
```

### 8.3 Spend Caps

Users can set monthly or per-session spend caps:

```toml
# ~/.nrev-lite/config.toml
[preferences]
spend_cap_monthly = 500    # credits per month
spend_cap_session = 50     # credits per Claude Code session
```

Server enforces caps. When reached, returns `SPEND_CAP_REACHED` error with details.

### 8.4 Dry Run (Cost Estimation)

Every operation supports `"dry_run": true`:

```json
POST /api/v1/execute
{
  "operation": "search_people",
  "params": {"title": "VP Sales", "company_size": "51-200"},
  "strategy": "parallel",
  "dry_run": true
}

Response:
{
  "estimated_credits": 6,
  "breakdown": [
    {"provider": "apollo", "operation": "search_people", "credits": 3},
    {"provider": "rocketreach", "operation": "search_people", "credits": 3}
  ],
  "current_balance": 142,
  "balance_after": 136
}
```

### 8.5 Payment Integration

**Stripe** for payment processing:
- One-time credit top-ups (Stripe Checkout)
- Recurring plans (Stripe Subscriptions)
- Webhook handler for payment confirmation → credit ledger entry

---

## 9. Key Vault & Security

### 9.1 Platform Keys (Managed)

Stored in **AWS Secrets Manager**:

```
/nrev-lite/platform-keys/apollo      → Apollo API key
/nrev-lite/platform-keys/rocketreach → RocketReach API key
/nrev-lite/platform-keys/rapidapi    → RapidAPI key
/nrev-lite/platform-keys/parallel_web → Parallel Web key
```

- Only the Execution Engine (ECS Fargate task role) has IAM permission to read these secrets
- Keys are cached in memory for 5 minutes (Secrets Manager SDK handles this)
- Rotated periodically; rotation doesn't require client changes

### 9.2 BYOK (Bring Your Own Keys)

User-provided keys are encrypted with **AWS KMS** using a tenant-scoped encryption context:

```python
# Encryption
kms.encrypt(
    KeyId="alias/nrev-lite-byok",
    Plaintext=user_api_key,
    EncryptionContext={
        "tenant_id": "nrev-a8f3c2e1",
        "provider": "apollo"
    }
)
```

Stored in PostgreSQL (the encrypted blob, never plaintext):

```sql
CREATE TABLE tenant_keys (
    id          SERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    provider    TEXT NOT NULL,
    encrypted_key BYTEA NOT NULL,          -- KMS-encrypted
    key_hint    TEXT,                       -- last 4 chars for display: "...x7f2"
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, provider)
);
```

Decryption only happens in memory during execution, never logged, never written to disk.

### 9.3 Security Principles

1. **Zero plaintext at rest**: All keys encrypted via KMS. Platform keys in Secrets Manager. BYOK in PostgreSQL as KMS-encrypted blobs.
2. **Tenant isolation**: RLS on every query. KMS encryption context includes tenant_id — one tenant's key cannot be decrypted with another tenant's context.
3. **Minimal permissions**: ECS task roles have least-privilege IAM policies. Lambda workers have separate, even narrower roles.
4. **Audit trail**: Every key access logged in CloudTrail. Every API call logged in enrichment_log.
5. **No secrets on client**: CLI stores only JWT tokens. No API keys for providers ever touch the user's machine (unless BYOK, where user already has the key).
6. **Transport security**: TLS 1.3 everywhere. HSTS enforced.

---

## 10. CLI Package (nrev-lite)

### 10.1 Installation

```bash
pip install nrev-lite
# or
pip install git+https://github.com/sayanta-ghosh/nrev_lite.git
```

### 10.2 Package Structure

```
nrev-lite/
├── pyproject.toml
├── src/
│   └── nrev-lite/
│       ├── __init__.py
│       ├── __main__.py           # Entry point: python -m nrev_lite
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── main.py           # Click CLI group
│       │   ├── auth.py           # nrev-lite auth login/logout/status
│       │   ├── execute.py        # nrev-lite enrich, nrev-lite search
│       │   ├── keys.py           # nrev-lite keys add/remove/list
│       │   ├── query.py          # nrev-lite query <sql>
│       │   ├── tables.py         # nrev-lite table list/describe/add-column
│       │   ├── dashboard.py      # nrev-lite dashboard deploy/list/remove
│       │   ├── credits.py        # nrev-lite credits balance/history/topup
│       │   ├── config.py         # nrev-lite config get/set
│       │   └── setup.py          # nrev-lite setup-claude
│       │
│       ├── client/
│       │   ├── __init__.py
│       │   ├── http.py           # httpx-based API client (auth, retries)
│       │   └── auth.py           # Token storage, refresh logic
│       │
│       ├── skills/
│       │   ├── __init__.py
│       │   ├── gtm_enrich.py     # Person/company enrichment skill
│       │   ├── gtm_search.py     # People/company search skill
│       │   ├── gtm_score.py      # ICP scoring skill
│       │   ├── gtm_sequence.py   # Outreach sequence builder
│       │   ├── gtm_dashboard.py  # Dashboard builder skill
│       │   ├── gtm_workflow.py   # Multi-step workflow orchestrator
│       │   └── knowledge/
│       │       ├── icp_frameworks.md
│       │       ├── enrichment_playbooks.md
│       │       ├── scoring_models.md
│       │       └── outreach_templates.md
│       │
│       ├── mcp/
│       │   ├── __init__.py
│       │   └── server.py         # MCP server exposing nrev-lite tools
│       │
│       └── utils/
│           ├── __init__.py
│           ├── display.py        # Rich console output (tables, progress)
│           └── config.py         # Config file management
│
├── tests/
│   ├── test_cli/
│   ├── test_client/
│   ├── test_skills/
│   └── test_mcp/
│
└── CLAUDE.md                     # Claude Code instructions
```

### 10.3 CLI Commands

```
nrev-lite auth login              # Authenticate via Google OAuth (opens browser)
nrev-lite auth login --headless   # Authenticate via device code (for SSH)
nrev-lite auth logout             # Clear stored credentials
nrev-lite auth status             # Show current auth state

nrev-lite enrich person --email john@acme.com
nrev-lite enrich company --domain acme.com
nrev-lite enrich batch --file leads.csv --fields name,email,company

nrev-lite search people --title "VP Sales" --industry fintech --size 51-200
nrev-lite search companies --industry "AI" --funding "Series B"

nrev-lite query "SELECT * FROM contacts WHERE icp_score > 80"

nrev-lite table list
nrev-lite table describe contacts
nrev-lite table contacts add-column outreach_status text

nrev-lite keys add apollo         # Securely upload your Apollo key
nrev-lite keys add rocketreach
nrev-lite keys list               # Show configured keys (hints only)
nrev-lite keys remove apollo

nrev-lite credits balance         # Show current credit balance
nrev-lite credits history         # Show recent credit transactions
nrev-lite credits topup           # Open browser to top-up page

nrev-lite config get              # Show all config
nrev-lite config set providers.apollo=byok
nrev-lite config set preferences.spend_cap_monthly=500

nrev-lite dashboard deploy ./my-dashboard   # Deploy local dashboard to nrev-lite cloud
nrev-lite dashboard list                    # List deployed dashboards
nrev-lite dashboard remove <name>           # Remove a deployed dashboard

nrev-lite setup-claude            # Install/update Claude Code skills
```

### 10.4 What the CLI Does vs What the Server Does

| Responsibility | CLI (client) | Server (cloud) |
|---------------|-------------|----------------|
| Authentication | Opens browser, stores JWT | Validates OAuth, issues JWT |
| GTM reasoning | Skills decide what to call | — |
| Data transformation | Skills massage/format results | — |
| API key storage | Never touches keys | Secrets Manager / KMS |
| Provider calls | Never calls providers directly | Routes, calls, retries, paginates |
| Rate limiting | Shows remaining in output | Enforces per provider per tenant |
| Credit checks | Shows balance warnings | Enforces, deducts, caps |
| Caching | — | Deduplicates calls via Redis cache |
| Data storage | — | Writes to Aurora PostgreSQL |
| Dashboard build | Claude Code generates locally | — |
| Dashboard hosting | `nrev-lite dashboard deploy` uploads | S3 + CloudFront serves |

---

## 11. Skills Architecture

### 11.1 What Skills Do

Skills are the GTM intelligence layer. They live on the user's machine, are loaded by Claude Code, and contain:

1. **Domain knowledge** — ICP frameworks, enrichment strategies, outreach best practices
2. **Workflow logic** — How to orchestrate multi-step GTM tasks
3. **Data transformation** — How to take raw enrichment data and make it actionable
4. **Decision-making** — Which providers to use, which strategy to pick, when to stop

### 11.2 Skill File Format

Each skill is a Python file that Claude Code loads. It contains:

```python
"""
---
skill: gtm_enrich
description: |
  Enrich people and companies using nrev-lite's cloud execution engine.
  Handles single enrichments, batch enrichments, and waterfall strategies.
  Automatically scores results against the tenant's ICP.
triggers:
  - enrich
  - find email
  - find phone
  - company info
  - person lookup
---
"""

# The skill code follows — functions that Claude Code can call
# These functions use the nrev-lite client library to call the server

from nrev_lite.client import NrvClient

async def enrich_person(email: str = None, linkedin: str = None,
                        name: str = None, company: str = None,
                        strategy: str = "parallel") -> dict:
    """
    Enrich a person's profile.

    Returns unified contact data with source attribution.
    Automatically writes to the contacts table.
    Costs 2 credits per provider (0 if BYOK).
    """
    client = NrvClient()
    result = await client.execute(
        operation="enrich_person",
        params={"email": email, "linkedin": linkedin, "name": name, "company": company},
        strategy=strategy
    )
    return result
```

### 11.3 Knowledge Files

Skills reference knowledge files for GTM reasoning:

```
skills/knowledge/
├── icp_frameworks.md          # How to define and score ICPs
├── enrichment_playbooks.md    # When to use which provider, waterfall vs parallel
├── scoring_models.md          # Lead scoring methodologies
├── outreach_templates.md      # Email/LinkedIn message templates
├── provider_capabilities.md   # What each provider returns, accuracy, coverage
└── workflow_patterns.md       # Common GTM workflow patterns
```

### 11.4 How Skills Interact with Claude Code

```
User: "Find me 50 VP Sales at Series B fintech companies and score them against our ICP"

Claude Code loads: gtm_search skill, gtm_score skill

Step 1 (skill logic): Build search query
  → POST /api/v1/execute {operation: "search_people", params: {title: "VP Sales", ...}}

Step 2 (server): Execute against Apollo + RocketReach in parallel
  → Returns 50 raw results

Step 3 (skill logic): Score each result against ICP
  → Uses scoring_models.md knowledge
  → Calculates fit score per contact

Step 4 (skill logic): Format and present results
  → Rich table in terminal with name, company, score, email

Step 5 (skill logic): Suggest next steps
  → "I found 50 contacts. 12 scored above 80. Want me to enrich their emails
     and add them to a sequence?"

Step 6 (if user agrees):
  → POST /api/v1/execute/batch {operation: "enrich_person", items: [...]}
  → Results written to contacts table automatically
```

---

## 12. MCP Strategy

### 12.1 Three Modes of Operation

Users can configure how each provider's actions are handled:

#### Mode 1: `nrev-lite` (default for enrichment providers)
All calls route through nrev-lite's cloud. Full audit trail, credit billing, caching.

```toml
[providers]
apollo = "nrev-lite"       # Uses nrev-lite's platform keys, costs credits
rocketreach = "nrev-lite"
```

#### Mode 2: `byok` (user's keys, nrev-lite's infrastructure)
Calls still route through nrev-lite's cloud (rate limiting, caching, audit trail) but use the user's own API keys. No credit cost.

```toml
[providers]
apollo = "byok"      # User's Apollo key, stored encrypted in nrev-lite vault
```

#### Mode 3: `native` (user's own MCP connector)
nrev-lite skills detect that the user has a native MCP connector installed and route calls directly through it. nrev-lite is not involved — no audit trail, no credits, no caching.

```toml
[providers]
hubspot = "native"   # User's own HubSpot MCP connector
slack = "native"     # User's own Slack MCP
```

### 12.2 Detection Logic

When a skill needs to call a provider, it checks the config:

```python
async def route_action(provider: str, operation: str, params: dict):
    config = load_config()
    mode = config.providers.get(provider, "nrev-lite")

    if mode == "nrev-lite" or mode == "byok":
        # Route through nrev-lite cloud
        return await nrev_client.execute(operation, params)

    elif mode == "native":
        # Route through user's native MCP connector
        # Claude Code handles this natively — skill just returns
        # the instruction for Claude to use the native tool
        return {"action": "use_native_mcp", "provider": provider,
                "operation": operation, "params": params}
```

### 12.3 nrev-lite MCP Server (Optional)

For users who prefer MCP over CLI, nrev-lite ships an optional MCP server:

```bash
nrev-lite mcp start
# Starts MCP server on stdio, exposable to Claude Code
```

This exposes nrev-lite operations as MCP tools:

```json
{
  "tools": [
    {
      "name": "nrev_enrich_person",
      "description": "Enrich a person using nrev-lite's cloud (Apollo, RocketReach, etc.)",
      "inputSchema": {
        "type": "object",
        "properties": {
          "email": {"type": "string"},
          "linkedin": {"type": "string"},
          "strategy": {"type": "string", "enum": ["parallel", "waterfall"]}
        }
      }
    },
    ...
  ]
}
```

---

## 13. Dashboard & Interactive Tables

### 13.1 How Dashboards Work

The dashboard flow is unique to nrev-lite:

```
Step 1: User asks Claude Code to build a dashboard
  "Build me a dashboard showing this week's enriched contacts by ICP score"

Step 2: Claude Code (using gtm_dashboard skill):
  a) Queries nrev-lite tables: GET /api/v1/query
  b) Generates a self-contained HTML/JS dashboard locally
     (using Chart.js, or a lightweight React app)
  c) Saves to ./dashboards/contacts-icp-scores/

Step 3: User previews locally (opens in browser)

Step 4: User deploys to nrev-lite cloud
  $ nrev-lite dashboard deploy ./dashboards/contacts-icp-scores/
  ✓ Deployed: https://dashboards.nrev_lite.TLD/nrev-a8f3c2e1/contacts-icp-scores

Step 5: Dashboard is live
  - Served via CloudFront from S3
  - Fetches data from nrev-lite API (with embedded read-only token)
  - Auto-refreshes on configurable interval
  - Shareable URL (with optional password protection)
```

### 13.2 Dashboard Deployment Architecture

```
nrev-lite dashboard deploy ./my-dashboard/
  │
  ├─ 1. CLI bundles the directory (index.html + assets)
  ├─ 2. CLI uploads to: POST /api/v1/dashboards/deploy
  │     Body: multipart form data (zip of dashboard files)
  │
  ├─ 3. Server:
  │     a) Validates the bundle (security scan, size limits)
  │     b) Generates a read-only API token scoped to this dashboard
  │     c) Injects the token into the dashboard's config
  │     d) Uploads to S3: s3://nrev-lite-dashboards/{tenant_id}/{dashboard_name}/
  │     e) Invalidates CloudFront cache for this path
  │
  └─ 4. Returns URL: https://dashboards.nrev_lite.TLD/{tenant_id}/{dashboard_name}
```

### 13.3 Dashboard Data Access

Deployed dashboards fetch data from a read-only API endpoint:

```
GET /api/v1/dashboard-data/{dashboard_id}?token={read_only_token}
```

- Token is scoped: can only read tables/queries configured for that dashboard
- Token has no write access, no key access, no credit spending ability
- Rate limited separately (10 req/min per dashboard)

### 13.4 Interactive Table Features

Users can interact with their tables through Claude Code:

```bash
# List all tables
nrev-lite table list
→ contacts (2,847 rows), companies (412 rows), enrichment_log (8,291 rows)

# Describe a table
nrev-lite table describe contacts
→ id (uuid), tenant_id (text), email (text), name (text), title (text),
  company (text), company_domain (text), phone (text), linkedin (text),
  icp_score (numeric), enrichment_sources (jsonb), extensions (jsonb),
  created_at (timestamptz), updated_at (timestamptz)

# Query with filters
nrev-lite query "SELECT name, email, icp_score FROM contacts WHERE icp_score > 80 AND company_domain LIKE '%fintech%' ORDER BY icp_score DESC"

# Add custom column
nrev-lite table contacts add-column "campaign_status" text default 'pending'

# Update records
nrev-lite query "UPDATE contacts SET extensions = extensions || '{\"campaign_status\": \"contacted\"}' WHERE id = '...'"
```

---

## 14. AWS Infrastructure

### 14.1 Services Used

| Service | Purpose | Estimated Cost (early stage) |
|---------|---------|------------------------------|
| **ECS Fargate** | API server (FastAPI) | ~$15-30/mo (0.25 vCPU, 0.5GB) |
| **Aurora Serverless v2** | PostgreSQL database | ~$0/mo idle, ~$10-20/mo active |
| **ElastiCache Redis** | Rate limits, cache | ~$12/mo (cache.t4g.micro) |
| **Lambda** | Async workers | ~$0-5/mo (pay per invocation) |
| **S3** | Dashboard hosting, exports | ~$1-5/mo |
| **CloudFront** | CDN for dashboards | ~$1-5/mo |
| **Secrets Manager** | Platform key storage | ~$2/mo |
| **KMS** | BYOK encryption | ~$1/mo |
| **SQS** | Job queue for async tasks | ~$0/mo (free tier) |
| **ALB** | Load balancer for API | ~$16/mo |
| **ACM** | TLS certificates | Free |
| **Route 53** | DNS | ~$0.50/domain/mo |
| **Cognito** | (optional) User pool | Free tier covers thousands |
| **CloudWatch** | Logging, monitoring | ~$5/mo |
| **ECR** | Docker image registry | ~$1/mo |
| **Amplify** (or ECS) | Web app hosting | ~$0-10/mo |
| **Total estimated** | | **~$50-100/mo at launch** |

### 14.2 Architecture Diagram (AWS)

```
                        Internet
                           │
                    ┌──────┴──────┐
                    │  Route 53    │
                    │  DNS         │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
    ┌─────────┴──┐  ┌─────┴─────┐  ┌──┴──────────┐
    │ CloudFront  │  │    ALB     │  │  Amplify     │
    │ Dashboards  │  │ api.nrev_lite.* │  │  app.nrev_lite.*   │
    └─────┬──────┘  └─────┬─────┘  └──────────────┘
          │               │
    ┌─────┴──────┐  ┌─────┴──────────────────────┐
    │    S3       │  │  ECS Fargate Cluster        │
    │ Dashboard   │  │  ┌────────────────────────┐ │
    │ Buckets     │  │  │ FastAPI Service (x2)   │ │
    └────────────┘  │  │ ├─ Auth endpoints       │ │
                    │  │ ├─ Execute endpoints     │ │
                    │  │ ├─ Query endpoints       │ │
                    │  │ ├─ Dashboard endpoints   │ │
                    │  │ └─ Credit endpoints      │ │
                    │  └─────────┬──────────────┘ │
                    └────────────┼────────────────┘
                                 │
                    ┌────────────┼────────────┐
                    │            │            │
          ┌────────┴───┐  ┌────┴─────┐  ┌──┴──────────┐
          │ Aurora       │  │ Redis    │  │ Secrets Mgr  │
          │ Serverless   │  │ Cache    │  │ + KMS        │
          │ v2 (PG 15)  │  │          │  │              │
          └──────────────┘  └──────────┘  └──────────────┘
                    │
          ┌────────┴───────────┐
          │  SQS Queues        │
          │  ├─ batch-enrich   │
          │  ├─ webhook-out    │
          │  └─ dashboard-build│
          └────────┬───────────┘
                   │
          ┌────────┴───────────┐
          │  Lambda Functions   │
          │  ├─ batch_worker   │
          │  ├─ webhook_sender │
          │  └─ dash_builder   │
          └────────────────────┘
```

### 14.3 VPC Layout

```
VPC: 10.0.0.0/16
├── Public Subnets (2 AZs)
│   ├─ 10.0.1.0/24 (us-east-1a)  ← ALB, NAT Gateway
│   └─ 10.0.2.0/24 (us-east-1b)  ← ALB, NAT Gateway
├── Private Subnets (2 AZs)
│   ├─ 10.0.10.0/24 (us-east-1a) ← ECS Fargate, Lambda
│   └─ 10.0.20.0/24 (us-east-1b) ← ECS Fargate, Lambda
└── Isolated Subnets (2 AZs)
    ├─ 10.0.100.0/24 (us-east-1a) ← Aurora, Redis
    └─ 10.0.200.0/24 (us-east-1b) ← Aurora, Redis
```

### 14.4 Infrastructure as Code

All infrastructure defined in **AWS CDK (Python)**, stored in `infra/` directory:

```
infra/
├── app.py                  # CDK app entry point
├── stacks/
│   ├── network_stack.py    # VPC, subnets, security groups
│   ├── database_stack.py   # Aurora Serverless, Redis
│   ├── api_stack.py        # ECS Fargate, ALB, ECR
│   ├── auth_stack.py       # Cognito (if used), or custom auth
│   ├── storage_stack.py    # S3, CloudFront
│   ├── workers_stack.py    # Lambda, SQS
│   ├── secrets_stack.py    # Secrets Manager, KMS
│   └── monitoring_stack.py # CloudWatch, alarms
└── requirements.txt
```

---

## 15. API Reference

### 15.1 Authentication

#### `POST /api/v1/auth/google`
Initiates Google OAuth flow. Returns redirect URL.

```json
Request:
{
  "redirect_uri": "http://localhost:8742/callback",  // CLI callback
  "state": "random-state-string"
}

Response:
{
  "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=...&redirect_uri=...&state=..."
}
```

#### `POST /api/v1/auth/callback`
Exchanges OAuth code for nrev-lite JWT tokens.

```json
Request:
{
  "code": "google-auth-code",
  "state": "random-state-string"
}

Response:
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "expires_in": 86400,
  "user": {
    "id": "user_2a8f3c2e",
    "email": "sayanta@nrev.com",
    "tenant_id": "nrev-a8f3c2e1",
    "is_new": true
  }
}
```

#### `POST /api/v1/auth/refresh`
Refresh an expired access token.

```json
Request:
{
  "refresh_token": "eyJ..."
}

Response:
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",  // rotated
  "expires_in": 86400
}
```

#### `POST /api/v1/auth/device/code`
Request a device code for headless auth.

```json
Response:
{
  "device_code": "abc123",
  "user_code": "ABCD-1234",
  "verification_uri": "https://app.nrev_lite.TLD/device",
  "expires_in": 600,
  "interval": 5
}
```

#### `POST /api/v1/auth/device/token`
Poll for device auth completion.

```json
Request:
{
  "device_code": "abc123"
}

Response (pending):
{
  "error": "authorization_pending"
}

Response (success):
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  ...
}
```

### 15.2 Onboarding

#### `POST /api/v1/onboarding`
Complete the onboarding wizard (creates tenant).

```json
Request:
{
  "company_name": "nRev",
  "company_domain": "nrev.com",
  "gtm_stage": "early_traction",
  "goals": ["lead_gen", "enrichment", "outreach"]
}

Response:
{
  "tenant": {
    "id": "nrev-a8f3c2e1",
    "name": "nRev",
    "domain": "nrev.com"
  },
  "credits": {
    "balance": 200,
    "source": "signup_bonus"
  }
}
```

### 15.3 Execution

#### `POST /api/v1/execute`
Execute a single operation.

```json
Request:
{
  "operation": "enrich_person",
  "params": {
    "email": "john@acme.com",
    "fields": ["name", "title", "company", "phone", "linkedin"]
  },
  "strategy": "parallel",
  "providers": ["apollo", "rocketreach"],
  "dry_run": false,
  "write_to_table": true
}

Response:
{
  "id": "exec_8f3c2e1a",
  "operation": "enrich_person",
  "status": "completed",
  "result": {
    "email": "john@acme.com",
    "name": "John Doe",
    "title": "VP of Sales",
    "company": "Acme Corp",
    "company_domain": "acme.com",
    "phone": "+1-555-0123",
    "linkedin": "linkedin.com/in/johndoe",
    "_sources": {
      "name": "apollo",
      "title": "rocketreach",
      "phone": "rocketreach",
      "linkedin": "apollo"
    },
    "_confidence": {
      "name": 0.95,
      "title": 0.92,
      "phone": 0.88,
      "linkedin": 0.97
    }
  },
  "credits_used": 4,
  "credits_remaining": 138,
  "providers_called": ["apollo", "rocketreach"],
  "cached": false,
  "duration_ms": 1240
}
```

#### `POST /api/v1/execute/batch`
Execute operations in batch (async).

```json
Request:
{
  "operation": "enrich_person",
  "items": [
    {"email": "john@acme.com"},
    {"email": "jane@bigcorp.io"},
    {"email": "alex@startup.co"}
  ],
  "strategy": "waterfall",
  "providers": ["apollo", "rocketreach"],
  "write_to_table": true
}

Response:
{
  "batch_id": "batch_7c2e1a8f",
  "status": "processing",
  "total_items": 3,
  "estimated_credits": 12,
  "poll_url": "/api/v1/execute/batch/batch_7c2e1a8f"
}
```

#### `GET /api/v1/execute/batch/{batch_id}`
Poll batch status.

```json
Response:
{
  "batch_id": "batch_7c2e1a8f",
  "status": "completed",           // "processing" | "completed" | "failed" | "partial"
  "total_items": 3,
  "completed_items": 3,
  "failed_items": 0,
  "credits_used": 10,
  "results": [
    {"email": "john@acme.com", "status": "success", "result": {...}},
    {"email": "jane@bigcorp.io", "status": "success", "result": {...}},
    {"email": "alex@startup.co", "status": "success", "result": {...}}
  ]
}
```

### 15.4 Tables & Queries

#### `GET /api/v1/tables`
List all tables for the tenant.

```json
Response:
{
  "tables": [
    {"name": "contacts", "row_count": 2847, "last_updated": "2026-03-15T10:30:00Z"},
    {"name": "companies", "row_count": 412, "last_updated": "2026-03-15T09:15:00Z"},
    {"name": "enrichment_log", "row_count": 8291, "last_updated": "2026-03-15T10:30:00Z"}
  ]
}
```

#### `GET /api/v1/tables/{table}`
Query a table with filters.

```
GET /api/v1/tables/contacts?filter=icp_score.gt.80&sort=-icp_score&limit=20&offset=0
```

```json
Response:
{
  "data": [...],
  "meta": {
    "total": 142,
    "limit": 20,
    "offset": 0,
    "has_more": true
  }
}
```

#### `POST /api/v1/query`
Execute raw SQL (read-only by default, write with explicit flag).

```json
Request:
{
  "sql": "SELECT name, email, icp_score FROM contacts WHERE icp_score > 80 ORDER BY icp_score DESC LIMIT 20",
  "mode": "read"  // "read" | "write"
}

Response:
{
  "columns": ["name", "email", "icp_score"],
  "rows": [
    ["John Doe", "john@acme.com", 92],
    ["Jane Smith", "jane@bigcorp.io", 88],
    ...
  ],
  "row_count": 20,
  "execution_time_ms": 12
}
```

### 15.5 Keys

#### `POST /api/v1/keys`
Add a BYOK key.

```json
Request:
{
  "provider": "apollo",
  "api_key": "apollo_key_abc123..."
}

Response:
{
  "provider": "apollo",
  "key_hint": "...c123",
  "status": "active",
  "created_at": "2026-03-15T10:30:00Z"
}
```

Note: The API key is encrypted with KMS before storage. It never appears in logs or responses.

#### `GET /api/v1/keys`
List configured keys (hints only).

```json
Response:
{
  "keys": [
    {"provider": "apollo", "key_hint": "...c123", "mode": "byok", "status": "active"},
    {"provider": "rocketreach", "mode": "platform", "status": "active"}
  ]
}
```

#### `DELETE /api/v1/keys/{provider}`
Remove a BYOK key.

### 15.6 Credits

#### `GET /api/v1/credits`
Get current credit balance and usage.

```json
Response:
{
  "balance": 142,
  "spend_cap_monthly": 500,
  "spent_this_month": 58,
  "free_credits_remaining": 0
}
```

#### `GET /api/v1/credits/history`
Get credit transaction history.

```json
Response:
{
  "transactions": [
    {"type": "debit", "amount": 2, "operation": "enrich_person", "balance_after": 142, "at": "..."},
    {"type": "credit", "amount": 200, "operation": "signup_bonus", "balance_after": 200, "at": "..."}
  ]
}
```

#### `POST /api/v1/credits/topup`
Initiate a Stripe Checkout session.

```json
Request:
{
  "package": "growth"  // "starter" | "growth" | "scale"
}

Response:
{
  "checkout_url": "https://checkout.stripe.com/pay/cs_...",
  "session_id": "cs_..."
}
```

### 15.7 Dashboards

#### `POST /api/v1/dashboards/deploy`
Deploy a dashboard bundle.

```
Content-Type: multipart/form-data

Fields:
- name: "contacts-icp-scores"
- bundle: <zip file>
- data_queries: [{"name": "contacts", "sql": "SELECT ..."}]  // queries the dashboard uses
- refresh_interval: 3600  // seconds
- password_protected: false
```

```json
Response:
{
  "dashboard": {
    "name": "contacts-icp-scores",
    "url": "https://dashboards.nrev_lite.TLD/nrev-a8f3c2e1/contacts-icp-scores",
    "status": "deployed",
    "created_at": "2026-03-15T10:30:00Z"
  }
}
```

### 15.8 Usage

#### `GET /api/v1/usage`
Get usage analytics.

```json
Response:
{
  "period": "2026-03",
  "api_calls": 847,
  "enrichments": 423,
  "searches": 89,
  "credits_used": 1247,
  "by_provider": {
    "apollo": {"calls": 412, "credits": 824},
    "rocketreach": {"calls": 201, "credits": 402},
    "google_search": {"calls": 234, "credits": 21}
  },
  "by_day": [
    {"date": "2026-03-01", "calls": 45, "credits": 67},
    ...
  ]
}
```

---

## 16. Database Schema

### 16.1 Core Tables

```sql
-- ============================================================
-- TENANTS & USERS
-- ============================================================

CREATE TABLE tenants (
    id              TEXT PRIMARY KEY,           -- "nrev-a8f3c2e1"
    name            TEXT NOT NULL,
    domain          TEXT,
    gtm_stage       TEXT,
    goals           TEXT[],
    settings        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE users (
    id              TEXT PRIMARY KEY,           -- "user_2a8f3c2e"
    tenant_id       TEXT REFERENCES tenants(id),
    email           TEXT UNIQUE NOT NULL,
    name            TEXT,
    google_id       TEXT UNIQUE,
    avatar_url      TEXT,
    role            TEXT DEFAULT 'member',      -- "owner" | "admin" | "member"
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

CREATE TABLE refresh_tokens (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT REFERENCES users(id),
    token_hash      TEXT NOT NULL,              -- SHA-256 of refresh token
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- INTERACTIVE TABLES (tenant data)
-- ============================================================

CREATE TABLE contacts (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    email           TEXT,
    name            TEXT,
    first_name      TEXT,
    last_name       TEXT,
    title           TEXT,
    phone           TEXT,
    linkedin        TEXT,
    company         TEXT,
    company_domain  TEXT,
    location        TEXT,
    icp_score       NUMERIC(5,2),
    enrichment_sources JSONB DEFAULT '{}',      -- {"name": "apollo", "phone": "rocketreach"}
    extensions      JSONB DEFAULT '{}',          -- user-added custom columns
    tags            TEXT[],
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, email)
);

CREATE TABLE companies (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    domain          TEXT,
    name            TEXT,
    industry        TEXT,
    employee_count  INTEGER,
    employee_range  TEXT,                        -- "51-200"
    revenue_range   TEXT,                        -- "$10M-$50M"
    funding_stage   TEXT,                        -- "Series B"
    total_funding   NUMERIC,
    location        TEXT,
    description     TEXT,
    technologies    TEXT[],
    enrichment_sources JSONB DEFAULT '{}',
    extensions      JSONB DEFAULT '{}',
    tags            TEXT[],
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, domain)
);

CREATE TABLE search_results (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    query_hash      TEXT NOT NULL,               -- hash of search params for dedup
    operation       TEXT NOT NULL,
    params          JSONB NOT NULL,
    result_count    INTEGER,
    results         JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- ENRICHMENT LOG (audit trail)
-- ============================================================

CREATE TABLE enrichment_log (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    execution_id    TEXT NOT NULL,                -- "exec_8f3c2e1a"
    batch_id        TEXT,                         -- null for single, "batch_..." for batch
    operation       TEXT NOT NULL,
    provider        TEXT NOT NULL,
    key_mode        TEXT NOT NULL,                -- "platform" | "byok"
    params          JSONB NOT NULL,
    result          JSONB,
    status          TEXT NOT NULL,                -- "success" | "failed" | "cached"
    error_message   TEXT,
    credits_charged NUMERIC(10,2) DEFAULT 0,
    duration_ms     INTEGER,
    cached          BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- CREDIT LEDGER
-- ============================================================

CREATE TABLE credit_ledger (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    entry_type      TEXT NOT NULL,                -- "credit" | "debit" | "hold" | "release"
    amount          NUMERIC(10,2) NOT NULL,
    balance_after   NUMERIC(10,2) NOT NULL,
    operation       TEXT,                         -- "enrich_person", "signup_bonus", "topup", etc.
    reference_id    TEXT,                         -- enrichment_log.id, payment.id, etc.
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Materialized view for fast balance lookups
CREATE TABLE credit_balances (
    tenant_id       TEXT PRIMARY KEY REFERENCES tenants(id),
    balance         NUMERIC(10,2) NOT NULL DEFAULT 0,
    spend_this_month NUMERIC(10,2) NOT NULL DEFAULT 0,
    month_reset_at  TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- PAYMENTS
-- ============================================================

CREATE TABLE payments (
    id              TEXT PRIMARY KEY,             -- Stripe session ID
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    amount_usd      NUMERIC(10,2) NOT NULL,
    credits         NUMERIC(10,2) NOT NULL,
    package         TEXT,                         -- "starter", "growth", "scale"
    stripe_status   TEXT NOT NULL,                -- "pending", "completed", "failed"
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- ============================================================
-- TENANT KEYS (BYOK)
-- ============================================================

CREATE TABLE tenant_keys (
    id              SERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    provider        TEXT NOT NULL,
    encrypted_key   BYTEA NOT NULL,               -- KMS-encrypted
    key_hint        TEXT,                          -- "...x7f2"
    status          TEXT DEFAULT 'active',         -- "active" | "revoked"
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, provider)
);

-- ============================================================
-- DASHBOARDS
-- ============================================================

CREATE TABLE dashboards (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    name            TEXT NOT NULL,
    s3_path         TEXT NOT NULL,
    data_queries    JSONB,                        -- queries this dashboard uses
    read_token_hash TEXT NOT NULL,                 -- hashed read-only token
    refresh_interval INTEGER DEFAULT 3600,
    password_hash   TEXT,                          -- optional password protection
    status          TEXT DEFAULT 'active',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, name)
);

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

-- Enable RLS on all tenant-scoped tables
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE enrichment_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_balances ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE dashboards ENABLE ROW LEVEL SECURITY;

-- Create policies (same pattern for all)
CREATE POLICY tenant_isolation ON contacts
    USING (tenant_id = current_setting('app.current_tenant')::text);
CREATE POLICY tenant_isolation ON companies
    USING (tenant_id = current_setting('app.current_tenant')::text);
CREATE POLICY tenant_isolation ON search_results
    USING (tenant_id = current_setting('app.current_tenant')::text);
CREATE POLICY tenant_isolation ON enrichment_log
    USING (tenant_id = current_setting('app.current_tenant')::text);
CREATE POLICY tenant_isolation ON credit_ledger
    USING (tenant_id = current_setting('app.current_tenant')::text);
CREATE POLICY tenant_isolation ON credit_balances
    USING (tenant_id = current_setting('app.current_tenant')::text);
CREATE POLICY tenant_isolation ON payments
    USING (tenant_id = current_setting('app.current_tenant')::text);
CREATE POLICY tenant_isolation ON tenant_keys
    USING (tenant_id = current_setting('app.current_tenant')::text);
CREATE POLICY tenant_isolation ON dashboards
    USING (tenant_id = current_setting('app.current_tenant')::text);

-- Force RLS even for table owners
ALTER TABLE contacts FORCE ROW LEVEL SECURITY;
ALTER TABLE companies FORCE ROW LEVEL SECURITY;
ALTER TABLE search_results FORCE ROW LEVEL SECURITY;
ALTER TABLE enrichment_log FORCE ROW LEVEL SECURITY;
ALTER TABLE credit_ledger FORCE ROW LEVEL SECURITY;
ALTER TABLE credit_balances FORCE ROW LEVEL SECURITY;
ALTER TABLE payments FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE dashboards FORCE ROW LEVEL SECURITY;

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_contacts_tenant ON contacts(tenant_id);
CREATE INDEX idx_contacts_email ON contacts(tenant_id, email);
CREATE INDEX idx_contacts_company ON contacts(tenant_id, company_domain);
CREATE INDEX idx_contacts_icp ON contacts(tenant_id, icp_score DESC);
CREATE INDEX idx_companies_tenant ON companies(tenant_id);
CREATE INDEX idx_companies_domain ON companies(tenant_id, domain);
CREATE INDEX idx_enrichment_log_tenant ON enrichment_log(tenant_id, created_at DESC);
CREATE INDEX idx_enrichment_log_exec ON enrichment_log(execution_id);
CREATE INDEX idx_credit_ledger_tenant ON credit_ledger(tenant_id, created_at DESC);
CREATE INDEX idx_payments_tenant ON payments(tenant_id);
CREATE INDEX idx_dashboards_tenant ON dashboards(tenant_id);
```

---

## 17. Phased Roadmap

### Phase 1: Foundation (Weeks 1-3)
**Goal:** Auth working, one provider executing, credits deducting.

- [ ] AWS infrastructure (CDK): VPC, Aurora, Redis, ECS, ALB, S3
- [ ] FastAPI server: project structure, health check, middleware
- [ ] Google OAuth flow (CLI localhost callback + web)
- [ ] JWT issuance and refresh
- [ ] Tenant creation (onboarding wizard)
- [ ] Apollo provider implementation (enrich_person, search_people)
- [ ] Single execution endpoint (no parallelization yet)
- [ ] Credit ledger: debit on execute, balance check
- [ ] Signup bonus (200 credits)
- [ ] CLI: `nrev-lite auth login`, `nrev-lite enrich person`, `nrev-lite credits balance`
- [ ] Basic RLS setup

**Deliverable:** User can `nrev-lite auth login` → `nrev-lite enrich person --email x` → see credits deducted.

### Phase 2: Multi-Provider + Skills (Weeks 4-6)
**Goal:** Multiple providers with parallelization, skills working in Claude Code.

- [ ] RocketReach provider implementation
- [ ] RapidAPI Google Search provider
- [ ] Parallel Web provider
- [ ] Parallel execution strategy
- [ ] Waterfall execution strategy
- [ ] Rate limiter (Redis token bucket)
- [ ] Response cache (Redis)
- [ ] Retry logic with backoff
- [ ] BYOK key management (KMS encryption)
- [ ] CLI: `nrev-lite keys add`, `nrev-lite search`, `nrev-lite config`
- [ ] Skills: gtm_enrich, gtm_search, gtm_score
- [ ] Knowledge files for skills
- [ ] `nrev-lite setup-claude` command

**Deliverable:** Claude Code can run full enrichment workflows with skills.

### Phase 3: Interactive Tables + Queries (Weeks 7-9)
**Goal:** Users can query data, build dashboards.

- [ ] Table API: list, describe, query, filter
- [ ] SQL query endpoint (read-only)
- [ ] Custom columns (extensions JSONB)
- [ ] Batch execution (async via SQS + Lambda)
- [ ] Batch status polling
- [ ] CLI: `nrev-lite table`, `nrev-lite query`
- [ ] Dashboard skill (Claude Code generates dashboards)
- [ ] Pagination handler for provider responses

**Deliverable:** User can query enriched data and Claude Code can build local dashboards.

### Phase 4: Dashboard Hosting + Web App (Weeks 10-12)
**Goal:** Deployed dashboards, web dashboard for monitoring.

- [ ] Dashboard deploy pipeline (CLI → S3 → CloudFront)
- [ ] Read-only dashboard tokens
- [ ] CLI: `nrev-lite dashboard deploy/list/remove`
- [ ] Web application (Next.js)
  - [ ] Google OAuth login
  - [ ] Usage dashboard
  - [ ] Credit balance & history
  - [ ] Top-up flow (Stripe integration)
  - [ ] API key management UI
  - [ ] Table browser
  - [ ] Provider configuration

**Deliverable:** Full self-serve platform with web UI and dashboard hosting.

### Phase 5: Billing + Polish (Weeks 13-15)
**Goal:** Stripe payments, spend caps, production hardening.

- [ ] Stripe integration (Checkout + Webhooks)
- [ ] Credit top-up packages
- [ ] Recurring subscription plans
- [ ] Spend caps (monthly + session)
- [ ] Dry run (cost estimation)
- [ ] MCP server (optional alternative to CLI)
- [ ] Provider auto-routing (learn which providers hit best)
- [ ] Error handling polish
- [ ] Rate limit headers on all responses
- [ ] Monitoring & alerting (CloudWatch)
- [ ] Documentation site

**Deliverable:** Production-ready platform with billing.

### Phase 6: Scale (Ongoing)
- [ ] Additional providers (Hunter, PDL, ZoomInfo, Apify, etc.)
- [ ] Sequence builders (Instantly, Lemlist)
- [ ] CRM integrations (HubSpot, Salesforce)
- [ ] Team features (multi-user per tenant)
- [ ] Webhook integrations
- [ ] Advanced analytics
- [ ] SOC2 preparation

---

## 18. Open Questions & Decisions Log

### Open Questions

| # | Question | Status | Decision |
|---|----------|--------|----------|
| 1 | Domain name — nrev_lite.ai? nrev.lite? getnrev_lite.com? | Open | Decide when purchasing domain |
| 2 | AWS region — us-east-1 default? | Open | Depends on user base location |
| 3 | Free tier reset — one-time 200 credits or monthly? | Open | Start with one-time, add monthly plan later |
| 4 | Max dashboard storage per tenant | Open | Start with 50MB, increase on plan |
| 5 | Rate limit for free tier vs paid | Open | Same for now, differentiate later |
| 6 | Custom table creation (beyond core tables) | Open | Phase 3 or later |

### Decisions Made

| # | Decision | Date | Rationale |
|---|----------|------|-----------|
| 1 | AWS over Supabase/Railway | 2026-03-15 | Free credits, native services, full control |
| 2 | Aurora Serverless v2 over RDS | 2026-03-15 | Scales to zero, fine-grained scaling |
| 3 | Pool model with RLS over separate DBs | 2026-03-15 | Cost-effective, simpler management |
| 4 | FastAPI over Django/Flask | 2026-03-15 | Async-native, perfect for provider proxying |
| 5 | Google OAuth (not email/password) | 2026-03-15 | Frictionless, secure, no password management |
| 6 | Python throughout (CLI + server) | 2026-03-15 | One language, faster development for solo team |
| 7 | Next.js for web app | 2026-03-15 | Best React framework, SSR, good DX |
| 8 | CDK for infrastructure | 2026-03-15 | Python-native, AWS-native, version controlled |
| 9 | Name: nrev-lite | 2026-03-15 | Short, memorable, derived from nRev |

---

## Appendix A: Naming Note

The name "nrev-lite" can be changed later with minimal impact:

- **API URL**: Configured via environment variable / Route 53. Change DNS → done.
- **CLI package**: Rename on PyPI. Users `pip install new-name`.
- **Code references**: Find-and-replace in codebase.
- **Database**: No impact (internal IDs, not branded).
- **Dashboard URLs**: Update CloudFront distribution.

The only sticky point is PyPI package name — once published, the old name remains reserved. But you can publish under a new name and deprecate the old one.

**Recommendation:** Don't publish to PyPI until the name/domain is finalized. Use `pip install git+...` for now (which we're already doing).

---

## Appendix B: Comparable Products

| Product | Model | Key Difference from nrev-lite |
|---------|-------|------------------------|
| Deepline | CLI + cloud gateway | No dashboard hosting, no free tier, Node.js required |
| Clay | No-code spreadsheet | Expensive per-row pricing, vendor lock-in |
| SyncGTM | MCP server | MCP-only, no database/tables |
| Databar | API + webhook | No Claude Code integration |
| Apollo | Direct API | Single provider, no orchestration |

nrev-lite's unique position: **Claude Code-native GTM platform with interactive tables and deployable dashboards.**

---

*End of Architecture Document*
