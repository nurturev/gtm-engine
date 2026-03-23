# V1 Deployment Checklist

Last updated: 2026-03-19

---

## Code Changes

### Task 01: Move auth state to Redis
- [x] `_pending_auth` dict replaced with Redis GET/SET (`auth:pending:{state}`, 10-min TTL)
- [x] `_device_codes` dict replaced with Redis GET/SET (`auth:device:{device_code}`, 15-min TTL)
- [x] Redis helpers use existing `redis_pool` from `server/app.py`
- [x] JSON serialization for Redis values
- [x] TTL-based expiry (no stale entries)
- **File changed:** `server/auth/router.py`

### Task 02: Rate limit auth endpoints
- [x] Rate limiter added to `POST /api/v1/auth/device/token`
- [x] Uses Redis INCR with 60s window, 10 requests max per IP
- [x] Returns 429 with `Retry-After` header when exceeded
- **File changed:** `server/auth/router.py`

### Task 03: Cap batch size at 25 records
- [x] `MAX_BATCH_SIZE = 25` constant added
- [x] Validation in `execute_batch_endpoint()` returns 400 if exceeded
- [x] Error message suggests splitting batches
- **File changed:** `server/execution/router.py`

### Task 04: Add CORS allowed origins config
- [x] `CORS_ALLOWED_ORIGINS` setting added to `server/core/config.py`
- [x] `server/app.py` parses comma-separated origins in production
- [x] Development mode still allows all origins (`["*"]`)
- **Files changed:** `server/core/config.py`, `server/app.py`

### Task 05: Update default CLI server URL
- [x] `DEFAULT_API_BASE_URL` changed to `https://nrev-lite-api.public.prod.nurturev.com`
- **File changed:** `src/nrev_lite/utils/config.py`

### Task 06: Update Dockerfile for production
- [x] Added `COPY migrations/ migrations/`
- [x] Added `--workers 2` to CMD
- **File changed:** `Dockerfile.server`

### Task 07: Add schema_migrations tracking
- [x] `migrations/000_schema_migrations.sql` created
- [x] Creates `schema_migrations` table
- [x] Seeds all 8 existing migration records (idempotent)
- **File created:** `migrations/000_schema_migrations.sql`

---

## Infrastructure & Deployment

### Task 08: Create Helm chart
- [x] `helm-charts/nrev-lite-api/Chart.yaml` created
- [x] `helm-charts/nrev-lite-api/values-staging.yaml` created (with actual endpoints filled in)
- [x] `helm-charts/nrev-lite-api/values-prod.yaml` created
- [x] `helm-charts/nrev-lite-api/deploy-staging.sh` created and executable
- [x] `helm-charts/nrev-lite-api/deploy-prod.sh` created and executable
- [x] `helm dependency update .` succeeds
- [x] `helm template` renders valid manifests (requires `-n staging` or `-n prod` due to base-template whitespace issue)
- **Depends on:** Dockerfile finalized (Task 06 — done)
- **Remaining:** `DATABASE_URL` is placeholder `<RDS_ENDPOINT>` — needs actual RDS endpoint from Task 09

### Task 09: Provision infrastructure
#### ECR Repository
- [x] ECR repo `gtm-engine-staging` created in ap-south-1 (staging) — created manually
- [x] ECR repo created in us-east-1 (prod — deferred)

#### RDS PostgreSQL (NEW — created)
- [x] Security group `nrev-lite-rds-staging-sg` (`sg-05e906c533dde1640`) created in staging VPC
- [x] Inbound rule: allow TCP 5432 from 172.31.0.0/16 (VPC CIDR)
- [x] DB subnet group: reusing `default-vpc-06986ffdb4ac8e3c8`
- [x] RDS instance `nrev-lite-db-staging` created (db.t3.micro, PostgreSQL 15, 20GB gp3, encrypted, private)
- [x] RDS endpoint: `nrev-lite-db-staging.cbatxfojkdmv.ap-south-1.rds.amazonaws.com:5432`
- [x] Role `nrev_lite_api` created
- [x] `migrations/000_schema_migrations.sql` applied — tracking table with 8 records
- [x] Migrations 001-008 applied in order — all clean
- [x] RLS verified working (nrev_lite_api role + tenant context filtering)

#### ElastiCache Redis (EXISTING — reuse)
- [x] Confirmed staging ElastiCache endpoint accessible from EKS pods (other services already connect)
- [x] TLS connection string: `rediss://staging-cache-sooatg.serverless.aps1.cache.amazonaws.com:6379/0`

#### DNS
- [x] `nrev-lite-api.public.staging.nurturev.com` — covered by wildcard `*.public.staging.nurturev.com` → EKS ingress LB

#### Google OAuth
- [x] Google Cloud OAuth 2.0 credentials available (Client ID + Secret)
- [x] Client ID `284137211338-qgr6elq9h9gl11jqrt89f4parvrnjnlr` set in Helm values-staging.yaml
- [x] New OAuth client created in org's Google Cloud project (replaces Sayanta's personal project)
- [x] **You must do:** Add redirect URI in Google Cloud Console: `https://nrev-lite-api.public.staging.nurturev.com/api/v1/auth/callback`
- [x] **You must do:** Add redirect URI: `http://localhost:8000/api/v1/auth/callback`
- [x] OAuth consent screen configured: scopes `email`, `profile`, `openid` (already present in org project)

#### IAM Role (deferred to V2 — Fernet encryption used instead of KMS)
- [ ] IAM role `nrev-lite-api-staging-role` created with EKS OIDC trust policy
- [ ] KMS permissions attached (for BYOK encryption)

#### Kubernetes Secrets
- [x] `JWT_SECRET_KEY` generated (unique per environment)
- [x] `nrev-lite-api-secret-staging.yaml` created with all values filled in (JWT, Google, DB, all provider keys)
- [x] Secret applied to staging namespace

### Task 10: First deploy + verification
- [x] Docker image built and pushed to staging ECR (`gtm-engine-staging`)
- [x] Helm chart deployed to staging EKS namespace (Revision 2)
- [x] Pod is Running and Ready (1/1, single replica)
- [x] `curl https://nrev-lite-api.public.staging.nurturev.com/health` returns `{"status":"ok","version":"0.1.0"}`
- [x] `jinja2` dependency added to requirements-server.txt (was missing, caused startup failure)
- [x] Readiness probe tuned: initialDelay 10s, period 10s (was 30s/120s)
- [x] `nrev-lite auth login` completes successfully — logged in as nikhil@nurturev.com (tenant: nurturev-08435881)
- [x] `nrev-lite status` shows authenticated user, server online, providers listed
- [x] `nrev-lite credits balance` returns successfully
- [x] Redis connectivity confirmed (app startup connects + pings Redis; auth state stored in Redis)
- [x] Session survives pod restart — `nrv status` works after `kubectl rollout restart`

### Task 11: Environment management & CI/CD
#### Branches
- [x] `staging` branch created from `main` and pushed to origin
- [ ] (Later) `prod` branch created after staging verified

#### GitHub Actions
- [x] `.github/workflows/code-quality-tests.yml` created
- [x] `.github/workflows/deployment-k8s-staging.yml` created
- [x] `.github/workflows/deployment-k8s-prod.yml` created
- All workflows follow Workflow Studio pattern: OIDC `role-to-assume`, ECR push, `KUBECONFIG_STAGING`/`KUBECONFIG_PROD` secrets for kubectl

#### GitHub Secrets
- [x] `KUBECONFIG_STAGING` — added to gtm-engine repo
- [ ] `KUBECONFIG_PROD` — defer until prod deploy
- [x] OIDC: `github-action-role` trusts `nurturev/*` — covers gtm-engine

---

## Summary

| Task | Status |
|------|--------|
| 01 Auth state to Redis | **Done** |
| 02 Rate limit auth endpoints | **Done** |
| 03 Cap batch size | **Done** |
| 04 CORS config | **Done** |
| 05 Default server URL | **Done** |
| 06 Dockerfile update | **Done** |
| 07 Schema migrations tracking | **Done** |
| 08 Helm chart | **Done** — DATABASE_URL via secretKeyRef, ECR repo corrected to `gtm-engine-staging` |
| 09 Provision infrastructure | **Done** |
| 10 First deploy | **Done** — deployed, healthy, auth + CLI verified |
| 11 Environment & CI/CD | **Done** — branches, workflows, KUBECONFIG_STAGING, OIDC all set. Prod branch deferred. |
