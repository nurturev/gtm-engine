# nrev-lite — Engineering Handover Guide

> **Version:** 0.1.0 (Alpha)
> **Date:** March 2026
> **From:** Sayanta Ghosh
> **To:** Engineering Lead

This document contains everything you need to host the nrev-lite platform and publish the CLI package.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Repository Structure](#2-repository-structure)
3. [Environment Variables](#3-environment-variables)
4. [Local Development Setup](#4-local-development-setup)
5. [Database Setup & Migrations](#5-database-setup--migrations)
6. [Server Deployment (Production)](#6-server-deployment-production)
7. [CLI Publishing (PyPI)](#7-cli-publishing-pypi)
8. [Google OAuth Setup](#8-google-oauth-setup)
9. [Third-Party Service Setup](#9-third-party-service-setup)
10. [MCP Server & Claude Code Integration](#10-mcp-server--claude-code-integration)
11. [Monitoring & Operations](#11-monitoring--operations)
12. [Security Considerations](#12-security-considerations)
13. [Testing](#13-testing)
14. [Known Limitations & TODOs](#14-known-limitations--todos)

---

## 1. Architecture Overview

nrev-lite is a **split-architecture** GTM (Go-To-Market) platform:

```
                        ┌─────────────────────┐
                        │   Claude Code IDE    │
                        │  (User's Machine)    │
                        └──────────┬──────────┘
                                   │ MCP Protocol (stdio)
                        ┌──────────▼──────────┐
                        │   nrev-lite CLI + MCP      │  ← Published to PyPI
                        │   (src/nrev_lite/)         │
                        └──────────┬──────────┘
                                   │ HTTPS + JWT
                        ┌──────────▼──────────┐
                        │   nrev-lite API Server     │  ← Deployed to cloud
                        │   (server/)          │
                        └──────────┬──────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                     │
     ┌────────▼───────┐  ┌───────▼────────┐  ┌────────▼───────┐
     │  PostgreSQL     │  │    Redis       │  │  Providers     │
     │  (Aurora)       │  │  (ElastiCache) │  │  (Apollo, etc) │
     └────────────────┘  └────────────────┘  └────────────────┘
```

**Key design principles:**
- CLI never calls external APIs directly — all provider calls go through the server
- Multi-tenant isolation via PostgreSQL Row-Level Security (RLS)
- Credit-based billing: platform API key usage costs credits; BYOK (bring your own key) is free
- MCP server generates a unique `WORKFLOW_ID` per session; all tool calls are logged as run steps

---

## 2. Repository Structure

```
gtm-engine/
├── src/nrev_lite/                    # CLI package (published to PyPI)
│   ├── cli/                    # Click commands (auth, enrich, search, etc.)
│   ├── client/                 # HTTP client (auth.py, http.py)
│   ├── mcp/                    # MCP server (15 tools for Claude Code)
│   └── utils/                  # Display helpers, config
│
├── server/                     # FastAPI API server (deployed)
│   ├── app.py                  # FastAPI app entrypoint
│   ├── core/                   # Config, database, middleware, security
│   ├── auth/                   # Google OAuth, JWT, user/tenant management
│   ├── execution/              # Provider proxy, run logging, search patterns
│   │   └── providers/          # Apollo, RocketReach, RapidAPI, Parallel, PredictLeads
│   ├── data/                   # Table query engine (contacts, companies, search_results)
│   ├── billing/                # Credit ledger, Stripe integration
│   ├── vault/                  # BYOK encrypted key storage
│   ├── dashboards/             # S3-hosted dashboard builder
│   └── console/                # Admin console (Jinja2 HTML dashboard)
│       └── templates/          # tenant_dashboard.html
│
├── migrations/                 # PostgreSQL SQL migrations (run in order)
│   ├── 001_initial.sql         # Full schema + RLS + roles
│   ├── 002_domain_index.sql    # Tenant domain index
│   └── 003_run_steps.sql       # Workflow run logging table
│
├── tests/                      # Test suite (pytest)
├── docs/                       # Architecture & module docs
│
├── docker-compose.yml          # Local dev (PostgreSQL + Redis + API)
├── Dockerfile.server           # Server container image
├── pyproject.toml              # CLI package config (hatchling)
├── requirements-server.txt     # Server Python dependencies
├── CLAUDE.md                   # Claude Code integration guide
└── QUICKSTART.md               # Quick start guide
```

---

## 3. Environment Variables

Create a `.env` file in the project root. All variables are loaded by `server/core/config.py`.

### Required

```bash
# --- Database ---
DATABASE_URL=postgresql+asyncpg://nrev-lite:YOUR_PASSWORD@localhost:5432/nrv

# --- Redis ---
REDIS_URL=redis://localhost:6379/0

# --- JWT ---
JWT_SECRET_KEY=generate-a-strong-random-secret-at-least-32-chars
# JWT_ALGORITHM=HS256              # default
# JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440  # 24 hours, default
# JWT_REFRESH_TOKEN_EXPIRE_DAYS=30      # default

# --- Google OAuth ---
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=https://your-domain.com/api/v1/auth/callback
```

### Optional — Provider API Keys (Platform-Managed)

These are nrev-lite's own keys used when tenants don't have BYOK keys. Calls using these charge credits.

```bash
APOLLO_API_KEY=                    # Apollo.io (person/company enrichment)
ROCKETREACH_API_KEY=               # RocketReach (contact finding)
X_RAPIDAPI_KEY=                    # RapidAPI Google Search
RAPIDAPI_KEY=                      # RapidAPI (alias)
PARALLEL_KEY=                      # Parallel Web Systems (scraping)
PREDICTLEADS_API_KEY=              # PredictLeads (company intelligence)
PREDICTLEADS_API_TOKEN=            # PredictLeads (alias)
COMPOSIO_API_KEY=                  # Composio (OAuth connections)
```

### Optional — Payments & Cloud

```bash
STRIPE_SECRET_KEY=                 # Stripe (credit purchases)
STRIPE_WEBHOOK_SECRET=             # Stripe webhook verification
AWS_REGION=us-east-1               # AWS region for KMS, S3
ENVIRONMENT=production             # "development" or "production"
```

---

## 4. Local Development Setup

### Prerequisites
- Python 3.10+ (3.12 recommended)
- Docker & Docker Compose
- Git

### Step-by-step

```bash
# 1. Clone the repo
git clone <repo-url> gtm-engine
cd gtm-engine

# 2. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install CLI in editable mode
pip install -e ".[dev]"

# 4. Install server dependencies
pip install -r requirements-server.txt

# 5. Copy and configure environment
cp .env.example .env   # or create from Section 3 above
# Edit .env with your values (at minimum: JWT_SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)

# 6. Start infrastructure (PostgreSQL + Redis)
docker-compose up -d postgres redis
# Wait for health checks to pass:
docker-compose ps   # both should show "healthy"

# 7. Run database migrations
# Migrations are auto-applied on first postgres start via docker-entrypoint-initdb.d.
# For subsequent migrations (002, 003), run manually:
docker exec -i nrev-lite-postgres psql -U nrev-lite -d nrev-lite < migrations/002_domain_index.sql
docker exec -i nrev-lite-postgres psql -U nrev-lite -d nrev-lite < migrations/003_run_steps.sql

# 8. Start the API server
uvicorn server.app:app --reload --port 8000

# 9. Verify
curl http://localhost:8000/health
# → {"status": "ok", "version": "0.1.0"}

# 10. Authenticate (opens browser for Google OAuth)
nrev-lite auth login

# 11. Test a tool
nrev-lite status
```

### Running with Docker (all services)

```bash
# Start everything (Postgres + Redis + API)
docker-compose up -d

# Server is at http://localhost:8000
# Dashboard at http://localhost:8000/console
```

---

## 5. Database Setup & Migrations

### PostgreSQL Requirements

- **Version:** 15+ (uses `gen_random_uuid()`, RLS)
- **Extensions:** `pgcrypto` (created in migration 001)
- **Database role:** `nrev_lite_api` (created in migration 001 — used for RLS enforcement)

### Migration Order

Migrations must be applied in sequence:

```bash
# On a fresh database:
psql -U nrev-lite -d nrev-lite -f migrations/001_initial.sql
psql -U nrev-lite -d nrev-lite -f migrations/002_domain_index.sql
psql -U nrev-lite -d nrev-lite -f migrations/003_run_steps.sql
```

### What migration 001 creates:

| Table | Purpose |
|-------|---------|
| `tenants` | Multi-tenant registry (id, name, domain, plan, settings) |
| `users` | Users with Google OAuth (sub claim, email, tenant_id) |
| `refresh_tokens` | JWT refresh token storage |
| `contacts` | Enriched person data (email, phone, LinkedIn, etc.) |
| `companies` | Enriched company data (domain, employees, funding, etc.) |
| `search_results` | Cached search results |
| `enrichment_log` | Audit trail of all enrichment calls |
| `credit_ledger` | Credit transactions (debits, credits, holds) |
| `credit_balances` | Current balance + monthly spend per tenant |
| `payments` | Stripe payment records |
| `tenant_keys` | Encrypted BYOK API keys |
| `dashboards` | S3-hosted dashboard metadata |

**All tables have Row-Level Security (RLS)** with `tenant_id`-based isolation.

### Database Connection Setup

The application connects using the `nrev_lite_api` role for RLS enforcement. The connection flow:

1. Connect to PostgreSQL
2. For each request: `SET app.current_tenant = '<tenant_id>'`
3. RLS policies filter all queries to only that tenant's data

### Production Database (AWS Aurora)

For production, use Aurora Serverless v2 (PostgreSQL 15 compatible):

```bash
DATABASE_URL=postgresql+asyncpg://nrev_lite_api:PASSWORD@your-aurora-cluster.us-east-1.rds.amazonaws.com:5432/nrv
```

The `nrev_lite_api` role must be created and granted permissions as defined in migration 001.

---

## 6. Server Deployment (Production)

### Option A: AWS ECS Fargate (Recommended)

```bash
# 1. Build the Docker image
docker build -f Dockerfile.server -t nrev-lite-api:latest .

# 2. Push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
docker tag nrev-lite-api:latest <account>.dkr.ecr.us-east-1.amazonaws.com/nrev-lite-api:latest
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/nrev-lite-api:latest

# 3. Create ECS service with:
#    - Aurora Serverless v2 (PostgreSQL 15)
#    - ElastiCache Redis 7
#    - ALB with HTTPS (ACM certificate)
#    - Environment variables set via ECS task definition or Secrets Manager
```

**Infrastructure requirements:**
- **Compute:** ECS Fargate or EC2 (1 vCPU, 2GB RAM minimum)
- **Database:** PostgreSQL 15+ (Aurora Serverless v2 recommended)
- **Cache:** Redis 7+ (ElastiCache recommended)
- **Load Balancer:** ALB with HTTPS termination
- **DNS:** Point your domain to the ALB

### Option B: Railway / Render / Fly.io

```bash
# Railway example:
# 1. Connect repo
# 2. Set environment variables in dashboard
# 3. Set build command: pip install -r requirements-server.txt
# 4. Set start command: uvicorn server.app:app --host 0.0.0.0 --port $PORT
# 5. Add PostgreSQL and Redis plugins

# Render example:
# Create render.yaml:
```

```yaml
# render.yaml
services:
  - type: web
    name: nrev-lite-api
    runtime: python
    buildCommand: pip install -r requirements-server.txt
    startCommand: uvicorn server.app:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: nrev-lite-db
          property: connectionString
      - key: REDIS_URL
        fromService:
          name: nrev-lite-redis
          type: redis
          property: connectionString
      - key: JWT_SECRET_KEY
        generateValue: true
      - key: GOOGLE_CLIENT_ID
        sync: false
      - key: GOOGLE_CLIENT_SECRET
        sync: false
      - key: GOOGLE_REDIRECT_URI
        value: https://nrev-lite-api.onrender.com/api/v1/auth/callback

databases:
  - name: nrev-lite-db
    plan: starter
    postgresMajorVersion: 15
```

### Option C: Simple VPS (DigitalOcean, Hetzner, etc.)

```bash
# On the server:
sudo apt update && sudo apt install -y python3.12 python3.12-venv postgresql-15 redis-server nginx certbot

# Setup PostgreSQL
sudo -u postgres createuser nrev_lite_api
sudo -u postgres createdb nrev-lite -O nrev_lite_api
sudo -u postgres psql -d nrev-lite -f migrations/001_initial.sql
sudo -u postgres psql -d nrev-lite -f migrations/002_domain_index.sql
sudo -u postgres psql -d nrev-lite -f migrations/003_run_steps.sql

# Setup application
git clone <repo> /opt/nrev-lite
cd /opt/nrev-lite
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-server.txt

# Create .env file with production values
cp .env.example .env
nano .env

# Run with systemd
sudo tee /etc/systemd/system/nrev-lite-api.service << 'EOF'
[Unit]
Description=nrev-lite API Server
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/nrev-lite
Environment=PATH=/opt/nrev-lite/.venv/bin
ExecStart=/opt/nrev-lite/.venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable nrev-lite-api
sudo systemctl start nrev-lite-api

# Setup nginx reverse proxy with HTTPS
sudo tee /etc/nginx/sites-available/nrev-lite << 'EOF'
server {
    listen 80;
    server_name api.nrev.ai;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/nrev-lite /etc/nginx/sites-enabled/
sudo certbot --nginx -d api.nrev.ai
sudo systemctl restart nginx
```

### Post-Deployment Checklist

- [ ] `curl https://your-domain/health` returns `{"status": "ok"}`
- [ ] `GOOGLE_REDIRECT_URI` matches your actual domain (`https://your-domain/api/v1/auth/callback`)
- [ ] Google OAuth consent screen has the redirect URI whitelisted
- [ ] `ENVIRONMENT=production` is set (restricts CORS)
- [ ] `JWT_SECRET_KEY` is a strong random value (not the dev default)
- [ ] PostgreSQL RLS is active (test with `SHOW app.current_tenant`)
- [ ] Redis is accessible from the API server
- [ ] Migrations 001, 002, 003 have all been applied

---

## 7. CLI Publishing (PyPI)

The CLI package (`nrev-lite`) is what end users install. It's defined in `pyproject.toml`.

### Prerequisites

```bash
pip install build twine
```

### Build & Publish

```bash
# 1. Update version in pyproject.toml if needed
#    version = "0.1.0"

# 2. Build the package
python -m build
# Creates:
#   dist/nrev-lite-0.1.0.tar.gz
#   dist/nrev-lite-0.1.0-py3-none-any.whl

# 3. Test on TestPyPI first
twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ nrev-lite

# 4. Publish to PyPI
twine upload dist/*
```

### What Gets Published

Only `src/nrev_lite/` is included in the wheel (configured in `pyproject.toml`):

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/nrev_lite"]
```

The `server/` directory, `migrations/`, `tests/`, etc. are **NOT** included in the CLI package.

### User Installation

```bash
# End users install with:
pip install nrev-lite

# Or with pipx (recommended for CLI tools):
pipx install nrev-lite

# Then:
nrev-lite auth login              # Authenticate with Google OAuth
nrev-lite status                   # Check connection status
nrev-lite enrich person --email user@example.com
```

### CLI Configuration

After `nrev-lite auth login`, credentials are stored at `~/.nrev-lite/credentials.json`.

The CLI needs to know the server URL. By default it uses `http://localhost:8000`. For production, users configure via:

```bash
nrev-lite config set server_url https://api.nrev.ai
```

Or set the environment variable:
```bash
export NREV_LITE_SERVER_URL=https://api.nrev.ai
```

**Important:** Before publishing, update the default server URL in `src/nrev_lite/client/http.py` to point to your production server, or ensure all documentation tells users to configure it.

### MCP Server Registration

After installing the CLI, users register the MCP server with Claude Code:

```bash
nrev-lite setup claude
# This writes ~/.claude/mcp.json with the nrev-lite MCP server config
```

Or manually add to Claude Code's MCP configuration:

```json
{
  "mcpServers": {
    "nrev-lite": {
      "command": "python3",
      "args": ["-m", "nrev_lite.mcp.server"],
      "env": {
        "PYTHONPATH": "<path-to-nrev-lite-package>"
      }
    }
  }
}
```

---

## 8. Google OAuth Setup

nrev-lite uses Google OAuth for authentication. You need a Google Cloud project with OAuth credentials.

### Step-by-step

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Navigate to **APIs & Services > Credentials**
4. Click **Create Credentials > OAuth 2.0 Client ID**
5. Application type: **Web application**
6. Add authorized redirect URIs:
   - Development: `http://localhost:8000/api/v1/auth/callback`
   - Production: `https://your-domain.com/api/v1/auth/callback`
7. Copy the Client ID and Client Secret to your `.env`

### OAuth Consent Screen

1. Navigate to **APIs & Services > OAuth consent screen**
2. User type: **External** (or Internal if Google Workspace only)
3. Add scopes: `email`, `profile`, `openid`
4. Add test users during development
5. Submit for verification when ready for production

### Auth Flow

```
User → nrev-lite auth login → Opens browser → Google OAuth → Callback to server
→ Server creates JWT (access + refresh) → Stored in ~/.nrev-lite/credentials.json
→ CLI sends JWT with every API call
→ Dashboard stores JWT as nrev_session cookie
```

---

## 9. Third-Party Service Setup

### Composio (OAuth App Connections)

Composio enables tenants to connect their apps (Slack, Gmail, HubSpot, etc.) via OAuth.

1. Sign up at [composio.dev](https://composio.dev)
2. Get your API key
3. Set `COMPOSIO_API_KEY` in `.env`
4. Apps are connected via the tenant dashboard (Connections tab)

### Stripe (Credit Purchases)

1. Create a Stripe account
2. Get API keys from the Stripe Dashboard
3. Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`
4. Create a webhook endpoint pointing to `https://your-domain/api/v1/credits/webhook`

### Provider API Keys

Each enrichment provider needs its own API key if you want to offer platform-managed access:

| Provider | Env Var | Sign Up |
|----------|---------|---------|
| Apollo.io | `APOLLO_API_KEY` | [apollo.io](https://apollo.io) |
| RocketReach | `ROCKETREACH_API_KEY` | [rocketreach.co](https://rocketreach.co) |
| RapidAPI (Google Search) | `X_RAPIDAPI_KEY` | [rapidapi.com](https://rapidapi.com) |
| Parallel Web | `PARALLEL_KEY` | [parallel.ai](https://parallel.ai) |
| PredictLeads | `PREDICTLEADS_API_KEY` | [predictleads.com](https://predictleads.com) |

**Note:** All provider keys are optional. Without them, that provider simply won't be available for platform-key usage. Tenants can still use BYOK (bring your own key) for any provider.

---

## 10. MCP Server & Claude Code Integration

### How It Works

1. User installs CLI: `pip install nrev-lite`
2. User runs: `nrev-lite setup claude` (registers MCP server with Claude Code)
3. When Claude Code starts a session, it spawns the MCP server process
4. The MCP server generates a unique `WORKFLOW_ID` (UUID) for that session
5. Every tool call sends the `WORKFLOW_ID` + `X-Tool-Name` as HTTP headers
6. The server's `RunStepMiddleware` logs each call to `run_steps` table
7. The dashboard Runs tab shows workflows and their steps

### 15 MCP Tools

| Tool | Purpose |
|------|---------|
| `nrev_health` | Health check |
| `nrev_search_web` | Google web search |
| `nrev_scrape_page` | Web page scraping |
| `nrev_google_search` | Advanced Google SERP (operators, filters, bulk) |
| `nrev_search_patterns` | Get platform-specific search query patterns |
| `nrev_enrich_person` | Person enrichment (email, name, LinkedIn) |
| `nrev_enrich_company` | Company enrichment (domain, name) |
| `nrev_query_table` | Query stored data tables |
| `nrev_list_tables` | List available tables |
| `nrev_credit_balance` | Check credit balance |
| `nrev_provider_status` | Check provider availability |
| `nrev_list_connections` | List OAuth-connected apps |
| `nrev_list_actions` | Discover actions for a connected app |
| `nrev_get_action_schema` | Get parameter schema for an action |
| `nrev_execute_action` | Execute an action on a connected app |

---

## 11. Monitoring & Operations

### Health Check

```bash
curl https://your-domain/health
# → {"status": "ok", "version": "0.1.0"}
```

### Key API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | None | Health check |
| POST | `/api/v1/auth/google` | None | Start Google OAuth |
| GET | `/api/v1/auth/callback` | None | OAuth callback |
| POST | `/api/v1/execute` | Bearer | Execute enrichment/search |
| GET | `/api/v1/tables` | Bearer | List data tables |
| POST | `/api/v1/tables/query` | Bearer | Query data |
| GET | `/api/v1/keys` | Bearer | List BYOK keys |
| GET | `/api/v1/credits` | Bearer | Credit balance |
| GET | `/api/v1/runs` | Bearer/Cookie | List workflows |
| GET | `/api/v1/runs/{id}` | Bearer/Cookie | Get workflow steps |
| GET | `/console` | Cookie | Admin dashboard |
| GET | `/console/{tenant_id}` | Cookie | Tenant dashboard |

### Logs

The server uses Python's `logging` module. In production, configure structured logging:

```bash
# uvicorn access logs are on by default
# Application logs go to stderr
uvicorn server.app:app --host 0.0.0.0 --port 8000 --workers 4 --log-level info
```

### Database Monitoring

```sql
-- Check active tenants
SELECT id, name, domain, created_at FROM tenants ORDER BY created_at DESC;

-- Check credit balances
SELECT tenant_id, balance, spend_this_month FROM credit_balances;

-- Check recent workflows
SELECT workflow_id, COUNT(*) as steps, MAX(created_at) as last_activity
FROM run_steps
GROUP BY workflow_id
ORDER BY last_activity DESC
LIMIT 20;

-- Check RLS is working
SET app.current_tenant = 'tenant-id-here';
SELECT * FROM contacts;  -- Should only return this tenant's data
RESET app.current_tenant;
```

---

## 12. Security Considerations

### Critical

- **JWT_SECRET_KEY:** Must be a strong random value in production (minimum 32 characters). Generate with: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
- **RLS is mandatory:** Never bypass Row-Level Security. All queries must go through `set_tenant_context()` first.
- **BYOK keys are encrypted:** Using Fernet symmetric encryption in dev, KMS in production.
- **Platform API keys:** Stored as environment variables, never exposed to users.
- **CORS:** Restricted in production (`ENVIRONMENT=production`).

### API Key Security

- Platform keys (in env vars) are NEVER returned to users
- BYOK keys are encrypted at rest and only decrypted server-side when making provider calls
- Key hints (last 4 chars) are stored for identification without exposing the key

### Authentication

- Access tokens expire in 24 hours
- Refresh tokens expire in 30 days
- Refresh tokens are hashed before storage (SHA-256)
- Console uses HTTP-only cookies (`nrev_session`)

---

## 13. Testing

```bash
# Run all tests
pytest

# Run specific test
pytest tests/test_multi_tenant.py -v

# Run with async support
pytest --asyncio-mode=auto

# Lint
ruff check .
ruff format --check .
```

### Test Coverage

| Test File | What It Tests |
|-----------|--------------|
| `test_cli.py` | CLI commands |
| `test_vault_security.py` | Encryption, key isolation |
| `test_multi_tenant.py` | RLS, tenant isolation |
| `test_composio_connection.py` | OAuth connections |
| `test_gtm_research_workflow.py` | End-to-end GTM workflows |

---

## 14. Known Limitations & TODOs

### Immediate

- [ ] **Default server URL in CLI:** Currently defaults to `localhost:8000`. Update `src/nrev_lite/client/http.py` before publishing to PyPI.
- [ ] **Migration tooling:** Using raw SQL files. Consider Alembic for version tracking.
- [ ] **docker-compose.yml:** Only mounts migration 001 for auto-init. Migrations 002 and 003 must be run manually.
- [ ] **CORS in production:** Currently blocks all origins when `ENVIRONMENT=production`. Add your frontend domain.

### Future

- [ ] **Tenant-based knowledge:** Custom knowledge bases per tenant
- [ ] **Pricing plans:** Implement tier-based plan enforcement
- [ ] **BYOK token management:** CLI command `nrev-lite keys add` improvements
- [ ] **`nrev-lite connect <app>` CLI:** OAuth connections from the CLI (currently dashboard-only)
- [ ] **Marketing website:** Product landing page
- [ ] **Rate limiting:** Redis-based per-tenant rate limits (code exists but not fully wired)
- [ ] **Alembic migrations:** Replace raw SQL with proper migration framework
- [ ] **CI/CD pipeline:** GitHub Actions for testing, building, and deploying
- [ ] **Monitoring:** Add APM (Datadog, New Relic, or OpenTelemetry)

---

## Quick Reference: Common Commands

```bash
# --- Development ---
docker-compose up -d postgres redis     # Start infrastructure
uvicorn server.app:app --reload         # Start server (dev mode)
pip install -e ".[dev]"                 # Install CLI in dev mode

# --- Database ---
docker exec -i nrev-lite-postgres psql -U nrev-lite -d nrev-lite < migrations/003_run_steps.sql

# --- CLI ---
nrev-lite auth login                          # Authenticate
nrev-lite status                              # Check everything
nrev-lite enrich person --email user@co.com   # Enrich a person
nrev-lite setup claude                        # Register MCP with Claude Code

# --- Build & Publish ---
python -m build                         # Build CLI package
twine upload dist/*                     # Publish to PyPI

# --- Docker (production) ---
docker build -f Dockerfile.server -t nrev-lite-api .
docker run -p 8000:8000 --env-file .env nrev-lite-api
```

---

*For detailed architecture documentation, see `docs/architecture.md` and `docs/modules.md`.*
