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
- [x] `DEFAULT_API_BASE_URL` changed to `https://nrv-api.public.prod.nurturev.com`
- **File changed:** `src/nrv/utils/config.py`

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
- [ ] `helm-charts/nrv-api/Chart.yaml` created
- [ ] `helm-charts/nrv-api/values-staging.yaml` created (with actual endpoints filled in)
- [ ] `helm-charts/nrv-api/values-prod.yaml` created
- [ ] `helm-charts/nrv-api/deploy-staging.sh` created and executable
- [ ] `helm-charts/nrv-api/deploy-prod.sh` created and executable
- [ ] `helm dependency update .` succeeds
- [ ] `helm template` renders valid manifests
- **Depends on:** Dockerfile finalized (Task 06 — done)
- **Blocked by:** Need actual RDS endpoint, ElastiCache endpoint, Google Client ID to fill in values

### Task 09: Provision infrastructure
#### ECR Repository
- [ ] ECR repo `nrv-api` created in ap-south-1 (staging)
- [ ] ECR repo `nrv-api` created in us-east-1 (prod — can defer)

#### RDS PostgreSQL (NEW — must create)
- [ ] Security group created for RDS in staging VPC
- [ ] Inbound rule: allow 5432 from EKS pod CIDR
- [ ] DB subnet group identified or created
- [ ] RDS instance `nrv-db-staging` created (db.t3.micro, PostgreSQL 15, 20GB gp3)
- [ ] RDS instance available and endpoint noted
- [ ] Role `nrv_api` created with password
- [ ] `migrations/000_schema_migrations.sql` applied
- [ ] Migrations 001-008 applied in order
- [ ] `migrations/000_schema_migrations.sql` re-applied (records migrations)
- [ ] RLS verified working

#### ElastiCache Redis (EXISTING — reuse)
- [ ] Confirmed staging ElastiCache endpoint accessible from EKS pods
- [ ] TLS connection string noted (`rediss://` prefix)

#### DNS
- [ ] `nrv-api.public.staging.nurturev.com` DNS record created → EKS ingress LB

#### Google OAuth
- [ ] Google Cloud OAuth 2.0 credentials available (Client ID + Secret)
- [ ] Redirect URI added: `https://nrv-api.public.staging.nurturev.com/api/v1/auth/callback`
- [ ] Redirect URI added: `http://localhost:8000/api/v1/auth/callback` (dev)
- [ ] OAuth consent screen configured: scopes `email`, `profile`, `openid`

#### IAM Role
- [ ] IAM role `nrv-api-staging-role` created with EKS OIDC trust policy
- [ ] KMS permissions attached (for BYOK encryption)

#### Kubernetes Secrets
- [ ] `JWT_SECRET_KEY` generated (unique per environment)
- [ ] `nrv-api-secret-staging.yaml` created with all values filled in
- [ ] Secret applied: `kubectl apply -f nrv-api-secret-staging.yaml -n staging`

### Task 10: First deploy + verification
- [ ] Docker image built and pushed to staging ECR
- [ ] Helm chart deployed to staging EKS namespace
- [ ] Pod is Running and Ready
- [ ] `curl https://nrv-api.public.staging.nurturev.com/health` returns OK
- [ ] `nrv auth login` completes successfully against staging
- [ ] `nrv status` shows authenticated user
- [ ] Redis connectivity verified from pod
- [ ] OAuth flow survives pod restart (state in Redis)

### Task 11: Environment management & CI/CD
#### Branches
- [ ] `staging` branch created from `main` and pushed to origin
- [ ] (Later) `prod` branch created after staging verified

#### GitHub Actions
- [ ] `.github/workflows/code-quality-tests.yml` created
- [ ] `.github/workflows/deployment-k8s-staging.yml` created
- [ ] `.github/workflows/deployment-k8s-prod.yml` created (can defer)

#### GitHub Secrets
- [ ] `AWS_ACCESS_KEY_ID` configured in repo settings
- [ ] `AWS_SECRET_ACCESS_KEY` configured in repo settings

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
| 08 Helm chart | **Pending — needs your input** |
| 09 Provision infrastructure | **Pending — needs your manual work** |
| 10 First deploy | **Pending — blocked by 08, 09** |
| 11 Environment & CI/CD | **Pending — branches + workflows** |
