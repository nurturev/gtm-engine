# nrev-lite API Server — Production Deployment Guide

> **Version:** 1.1
> **Date:** March 2026
> **Status:** V1 deployment plan — approved decisions documented

---

## Table of Contents

1. [Decisions & Constraints](#1-decisions--constraints)
2. [Org Pattern Alignment](#2-org-pattern-alignment)
3. [Infrastructure Overview](#3-infrastructure-overview)
4. [Pre-Deployment Checklist](#4-pre-deployment-checklist)
5. [Docker Image](#5-docker-image)
6. [Helm Chart Setup](#6-helm-chart-setup)
7. [Database (RDS PostgreSQL)](#7-database-rds-postgresql)
8. [Redis (ElastiCache)](#8-redis-elasticache)
9. [Authentication (Google OAuth)](#9-authentication-google-oauth)
10. [Secrets Management](#10-secrets-management)
11. [CLI Configuration](#11-cli-configuration)
12. [Deployment Steps](#12-deployment-steps)
13. [Post-Deployment Verification](#13-post-deployment-verification)
14. [V1 Accepted Constraints](#14-v1-accepted-constraints)
15. [V2 Roadmap](#15-v2-roadmap)

---

## 1. Decisions & Constraints

All decisions below were made during the gap analysis (March 2026).

| Area | Decision |
|------|----------|
| Deployment | EKS + Helm (same pattern as user-management-ws) |
| Database | AWS RDS PostgreSQL 15 (not Aurora — cost optimization). New instance to create. |
| Cache | AWS ElastiCache Redis — reuse existing clusters (staging + prod) |
| File storage | PostgreSQL JSONB for V1 (no S3 for hosted app files) |
| S3 | Reuse existing buckets if needed; no new buckets for V1 |
| Migrations | Raw SQL + schema_migrations tracking table |
| Auth state | Move in-memory `_pending_auth` to Redis (10-min TTL) |
| Device codes | Move in-memory `_device_codes` to Redis (15-min TTL) |
| Batch store | Keep in-memory for V1 (post-execution result cache only) |
| JWT signing | HS256 (symmetric, standard for single-service) |
| Token revocation | 24h access token expiry, no server-side revocation for V1 |
| Batch size | Cap at 25 records server-side for V1 |
| Stripe | Stubbed — manual credit grants for V1 |
| CI/CD | GitHub Actions (replicate from existing projects) |

---

## 2. Org Pattern Alignment

nrev-lite was built independently and has some patterns that differ from the established org patterns in Workflow Studio and other services. For V1, we keep nrv's existing patterns and align in V2.

### Reference Projects

- **Workflow Studio** (`/Users/nikhilojha/Projects/workflow_studio`): Redis client, logging, error handling, DB patterns, Docker
- **Helm Charts** (`/Users/nikhilojha/Projects/helm-charts`): Deployment, ingress, secrets, env var injection

### Pattern Comparison

| Area | Org Pattern (Workflow Studio) | nrev-lite Current (V1) | V2 Alignment |
|------|------|------|------|
| **Redis env vars** | `REDIS_HOST` + `REDIS_PORT` (separate) | `REDIS_URL` (single URL) | Migrate to separate vars |
| **Redis client** | `redis` lib, SingletonBorg, SSL, 5s timeout, health check interval 30s | `redis.asyncio` (aioredis), connected at app startup | Adopt SingletonBorg + SSL config |
| **Redis key prefixes** | Centralized in `constants/redis_constants.py` with TTLs | Inline in each module | Centralize constants |
| **DB env vars** | `POSTGRES_HOST/PORT/USER/PASSWORD/DATABASE` (separate) | `DATABASE_URL` (single connection string) | Migrate to separate vars |
| **DB client** | Supabase PostgREST + psycopg2 pool (SingletonBorg) | SQLAlchemy async + asyncpg | Keep SQLAlchemy (different ORM) |
| **Logging** | Structured: `asctime \| name.module.funcName \| request_id \| trace_id \| metadata \| level \| message`. Context via ContextVars + custom Filters | Basic Python `logging` module | Port WS logging system |
| **Error handling** | Exception hierarchy: `BaseAPIException` → `MissingResourceError`, `InvalidRequestError`, `RateLimitExceededError`, etc. Global handler in middleware | Basic `HTTPException` raises | Port WS exception hierarchy |
| **Docker** | Multi-stage build, gunicorn + uvicorn workers, port 8080, hash-verified deps, worker recycling (max-requests 1000) | Single-stage, direct uvicorn, port 8000 | Adopt multi-stage + gunicorn |
| **Health checks** | `/healthCheck` (204 No Content) + `/readiness` (204). Lifespan initializes all clients at startup. | `/health` (200 JSON body) | Add `/readiness`, consider 204 |
| **App initialization** | Lifespan context manager: `DBClient()`, `RedisClient()`, `SNS()`, `DuckDBClient()`, `AsyncRedisClient()` | Lifespan: DB pool verify + Redis connect | Align startup pattern |
| **Container port** | 8080 (all org services) | 8000 (FastAPI default) | Keep 8000 for V1 (Helm maps 80→8000) |
| **Secrets** | Kubernetes Secrets with `{APP}-secret` naming | Same pattern, `nrev-lite-api-secret` | Already aligned |
| **Ingress DNS** | `{service}.public.{env}.nurturev.com` | `nrev-lite-api.public.{env}.nurturev.com` | Already aligned |

### V1 Principle

> If nrev-lite already implements something differently, **keep it for V1**. Don't refactor working code to match org patterns during the first production deployment. Track alignment items in V2.

---

## 3. Infrastructure Overview

```
                    ┌──────────────────────────┐
                    │  User's Machine           │
                    │  Claude Code + nrev-lite CLI    │
                    └────────────┬─────────────┘
                                 │ HTTPS + JWT Bearer
                    ┌────────────▼─────────────┐
                    │  EKS Cluster (nginx)      │
                    │  Ingress → nrev-lite-api pods   │
                    └────────────┬─────────────┘
                                 │
          ┌──────────────────────┼──────────────────────┐
          │                      │                       │
  ┌───────▼────────┐   ┌───────▼────────┐   ┌─────────▼─────────┐
  │ RDS PostgreSQL  │   │  ElastiCache   │   │  External APIs     │
  │ (15, new)       │   │  Redis 7       │   │  Apollo, RR, etc.  │
  │                 │   │  (existing)    │   │                    │
  └────────────────┘   └────────────────┘   └───────────────────┘
```

**What's new vs reused:**
- **New**: RDS PostgreSQL instance (staging + prod), ECR repository, DNS records, K8s Secrets, IAM role
- **Reused**: ElastiCache Redis clusters, EKS clusters, nginx ingress controller, VPC/subnets, NAT gateway

**Networking:**
- Ingress (public) → nginx ingress controller → ClusterIP service → nrev-lite-api pods
- nrev-lite-api pods (private subnet) → RDS, ElastiCache (private subnet)
- nrev-lite-api pods → external provider APIs (outbound via NAT gateway)

---

## 4. Pre-Deployment Checklist

### Infrastructure (one-time setup)

- [ ] RDS PostgreSQL 15 instance created via AWS CLI (see [Section 7](#7-database-rds-postgresql))
- [ ] Existing ElastiCache Redis endpoint confirmed accessible (see [Section 8](#8-redis-elasticache))
- [ ] ECR repository created: `nrev-lite-api` (staging + prod regions)
- [ ] Google Cloud OAuth credentials created (see [Section 9](#9-authentication-google-oauth))
- [ ] Google OAuth redirect URI whitelisted: `https://{domain}/api/v1/auth/callback`
- [ ] DNS record created: `nrev-lite-api.public.{env}.nurturev.com` → EKS ingress
- [ ] Kubernetes namespace exists (`staging` / `prod`)
- [ ] Kubernetes Secrets created (see [Section 10](#10-secrets-management))
- [ ] IAM role created for ServiceAccount (KMS access if using BYOK encryption)
- [ ] Security groups allow: EKS pods → new RDS (5432)
- [ ] Security groups allow: EKS pods → existing ElastiCache (6379) — likely already in place

### Code changes required before first deploy

- [ ] Update default server URL in `src/nrev_lite/utils/config.py` (`DEFAULT_API_BASE_URL`)
- [ ] Move `_pending_auth` dict to Redis in `server/auth/router.py`
- [ ] Move `_device_codes` dict to Redis in `server/auth/router.py`
- [ ] Add batch size cap (25 records) in `server/execution/router.py`
- [ ] Add rate limiting on `/api/v1/auth/device/token` endpoint
- [ ] Set `CORS_ALLOWED_ORIGINS` env var support in `server/app.py`
- [ ] Add `schema_migrations` tracking table (see [Section 7](#7-database-rds-postgresql))
- [ ] Ensure `ENVIRONMENT=production` restricts CORS to allowed origins
- [ ] Ensure ALB/ingress terminates TLS (HTTPS enforcement)

---

## 5. Docker Image

### Org Pattern Reference (Workflow Studio)

Workflow Studio uses a multi-stage Dockerfile with gunicorn + uvicorn workers, hash-verified deps, port 8080, and worker recycling (max-requests 1000, jitter 100). See: `/Users/nikhilojha/Projects/workflow_studio/Dockerfile_server`.

### nrev-lite V1 Approach

For V1, keep the existing single-stage build with direct uvicorn on port 8000. The Helm chart maps port 80→8000, so external behavior is identical.

**V2**: Adopt multi-stage build, gunicorn with uvicorn workers, worker recycling, and hash-verified deps from Workflow Studio.

**Production Dockerfile:**
```dockerfile
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY server/ server/
COPY migrations/ migrations/

EXPOSE 8000

# Production: multiple workers, no reload
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

Changes from current:
- Add `--workers 2` (matches 500m CPU limit — 1 worker per ~250m)
- Include `migrations/` in image (for ad-hoc migration apply from within pods)

**Build and push:**
```bash
# Staging (ap-south-1)
docker build -f Dockerfile.server -t 979176640062.dkr.ecr.ap-south-1.amazonaws.com/nrev-lite-api:latest .
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 979176640062.dkr.ecr.ap-south-1.amazonaws.com
docker push 979176640062.dkr.ecr.ap-south-1.amazonaws.com/nrev-lite-api:latest

# Prod (us-east-1)
docker build -f Dockerfile.server -t 979176640062.dkr.ecr.us-east-1.amazonaws.com/nrev-lite-api:latest .
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 979176640062.dkr.ecr.us-east-1.amazonaws.com
docker push 979176640062.dkr.ecr.us-east-1.amazonaws.com/nrev-lite-api:latest
```

---

## 6. Helm Chart Setup

### Org Pattern Reference

All org services use the same Helm chart structure from `/Users/nikhilojha/Projects/helm-charts/base-templates/service/`. Key patterns:
- `Chart.yaml` declares dependency on base service template with `alias: appConf`
- All config nested under `appConf:` key
- Env vars: plain `value:` for non-secrets, `valueFrom.secretKeyRef` for secrets
- Secret naming: `{appName}-secret`
- Ingress DNS: `{service}.public.{env}.nurturev.com`
- Port mapping: service port 80 → container target_port
- Health probes: `httpGet` on container port
- Resources: 200m/500m CPU, 512Mi memory for small services
- Lifecycle: preStop sleep 30, terminationGracePeriodSeconds 60

### nrev-lite Differences from Org Pattern

| Setting | Org Standard | nrev-lite |
|---------|-------------|-----|
| Container port | 8080 | 8000 |
| Health endpoint | `/healthCheck` | `/health` |
| Readiness endpoint | `/readiness` | `/health` (same endpoint for V1) |
| Env var style | Separate DB/Redis vars | `DATABASE_URL`, `REDIS_URL` (connection strings) |

These are acceptable for V1. The base Helm templates are parameterized — they accept any port, any health path, any env var names.

### Chart.yaml

```yaml
apiVersion: v1
appVersion: "0.1.0"
description: Helm chart for nrev-lite GTM API server
name: nrev-lite-api
version: 1.0.0

dependencies:
- alias: appConf
  name: service
  repository: file://../base-templates/service
  version: '>=0.1.0'
```

### values-staging.yaml

```yaml
appConf:
  appName: nrev-lite-api
  env:
  # --- Core ---
  - name: ENVIRONMENT
    value: "staging"
  - name: LOG_LEVEL
    value: "INFO"
  - name: AWS_DEFAULT_REGION
    value: "ap-south-1"

  # --- Database (RDS PostgreSQL) ---
  # nrev-lite uses a single DATABASE_URL (differs from org pattern of separate vars).
  # DB password is embedded in the URL. For V2, split into separate vars matching
  # org pattern: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DATABASE, POSTGRES_USER,
  # POSTGRES_PASSWORD (via secretKeyRef).
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: nrev-lite-api-secret
        key: DATABASE_URL

  # --- Redis (ElastiCache — reuse existing cluster) ---
  # nrev-lite uses a single REDIS_URL (differs from org pattern of REDIS_HOST + REDIS_PORT).
  # Org pattern (Workflow Studio): REDIS_HOST, REDIS_PORT, REDIS_SSL as separate vars.
  # For V2, split to match org pattern.
  # Existing staging ElastiCache: staging-cache-sooatg.serverless.aps1.cache.amazonaws.com
  - name: REDIS_URL
    value: "rediss://staging-cache-sooatg.serverless.aps1.cache.amazonaws.com:6379/0"

  # --- Auth ---
  - name: GOOGLE_CLIENT_ID
    value: "<staging-google-client-id>"
  - name: GOOGLE_REDIRECT_URI
    value: "https://nrev-lite-api.public.staging.nurturev.com/api/v1/auth/callback"
  - name: CORS_ALLOWED_ORIGINS
    value: "https://nrev-lite-api.public.staging.nurturev.com"

  # --- Secrets (via Kubernetes Secrets) ---
  # Follows org pattern: {appName}-secret with secretKeyRef
  - name: JWT_SECRET_KEY
    valueFrom:
      secretKeyRef:
        name: nrev-lite-api-secret
        key: JWT_SECRET_KEY
  - name: GOOGLE_CLIENT_SECRET
    valueFrom:
      secretKeyRef:
        name: nrev-lite-api-secret
        key: GOOGLE_CLIENT_SECRET

  # --- Provider API Keys (all optional, via Kubernetes Secrets) ---
  - name: APOLLO_API_KEY
    valueFrom:
      secretKeyRef:
        name: nrev-lite-api-secret
        key: APOLLO_API_KEY
  - name: ROCKETREACH_API_KEY
    valueFrom:
      secretKeyRef:
        name: nrev-lite-api-secret
        key: ROCKETREACH_API_KEY
  - name: X_RAPIDAPI_KEY
    valueFrom:
      secretKeyRef:
        name: nrev-lite-api-secret
        key: X_RAPIDAPI_KEY
  - name: PARALLEL_KEY
    valueFrom:
      secretKeyRef:
        name: nrev-lite-api-secret
        key: PARALLEL_KEY
  - name: PREDICTLEADS_API_KEY
    valueFrom:
      secretKeyRef:
        name: nrev-lite-api-secret
        key: PREDICTLEADS_API_KEY
  - name: COMPOSIO_API_KEY
    valueFrom:
      secretKeyRef:
        name: nrev-lite-api-secret
        key: COMPOSIO_API_KEY

  # --- Image ---
  image:
    pullPolicy: Always
    repository: 979176640062.dkr.ecr.ap-south-1.amazonaws.com/nrev-lite-api
    tag: latest

  # --- IAM Role (org pattern: ServiceAccount with IRSA annotation) ---
  iamrole: arn:aws:iam::979176640062:role/nrev-lite-api-staging-role

  # --- Networking (org pattern: nginx ingress, {service}.public.{env}.nurturev.com) ---
  ingress:
    enabled: true
    annotations:
      kubernetes.io/ingress.class: nginx
      ingressClass: nginx
    rules:
    - host: nrev-lite-api.public.staging.nurturev.com
      port: 80

  labels:
    appName: nrev-lite-api
    env: staging

  # --- Ports (nrev-lite uses 8000, not org standard 8080) ---
  ports:
  - name: http-0
    port: 80
    protocol: TCP
    target_port: 8000

  # --- Health Checks (nrev-lite uses /health, not org standard /healthCheck) ---
  healthcheck: true
  healthProbe:
    failureThreshold: 3
    healthchecktype: http
    httpGet:
      path: /health
      port: 8000
      scheme: HTTP
    initialDelaySeconds: 30
    periodSeconds: 10
    successThreshold: 1
    timeoutSeconds: 10

  readiness: true
  readinessProbe:
    failureThreshold: 3
    httpGet:
      path: /health
      port: 8000
      scheme: HTTP
    initialDelaySeconds: 30
    periodSeconds: 10
    readinesschecktype: http
    successThreshold: 1
    timeoutSeconds: 10

  # --- Resources (matches org small-service tier: user-management-ws, alerts) ---
  replicaCount: 1
  resources:
    limits:
      cpu: 500m
      memory: 512Mi
    requests:
      cpu: 200m
      memory: 512Mi

  # --- Scaling (disabled for V1, org pattern: HPA optional per service) ---
  hpa:
    avgCPUUtilization: '90'
    avgMemoryUtilization: '90'
    enabled: false
    maxReplica: 3
    minReplica: 1

  # --- Deployment Strategy (org standard) ---
  deploymentStrategy:
    rollingUpdate:
      maxSurge: 25%
      maxUnavailable: 0%
    type: RollingUpdate

  # --- Graceful Shutdown (org standard: 30s preStop, 60s terminationGrace) ---
  lifecycle:
    preStop:
      exec:
        command:
        - /bin/sh
        - -c
        - sleep 30
    type: exec
```

### values-prod.yaml

Same structure as staging with these differences:

| Field | Staging | Prod |
|-------|---------|------|
| `ENVIRONMENT` | `staging` | `prod` |
| `AWS_DEFAULT_REGION` | `ap-south-1` | `us-east-1` |
| `DATABASE_URL` | staging RDS endpoint (via secret) | prod RDS endpoint (via secret) |
| `REDIS_URL` | `staging-cache-sooatg.serverless.aps1.cache.amazonaws.com` | `prod-cache-msnit6.serverless.use1.cache.amazonaws.com` |
| `GOOGLE_REDIRECT_URI` | `https://nrev-lite-api.public.staging.nurturev.com/...` | `https://nrev-lite-api.public.prod.nurturev.com/...` |
| `CORS_ALLOWED_ORIGINS` | staging domain | prod domain |
| `image.repository` | `...ecr.ap-south-1.../nrev-lite-api` | `...ecr.us-east-1.../nrev-lite-api` |
| `iamrole` | staging IAM role ARN | prod IAM role ARN |
| `ingress.rules[0].host` | `nrev-lite-api.public.staging.nurturev.com` | `nrev-lite-api.public.prod.nurturev.com` |
| `labels.env` | `staging` | `prod` |

### deploy-staging.sh

```bash
#!/bin/bash
SERVICE_NAME="nrev-lite-api"
NAMESPACE="staging"
VALUES_FILE="values-staging.yaml"
SECRET_FILE="nrev-lite-api-secret-staging.yaml"

echo "Configuring kubectl for staging cluster..."
aws eks update-kubeconfig --name staging-eks --kubeconfig ~/.kube/staging-eks
cp ~/.kube/staging-eks ~/.kube/config

echo "Applying secret..."
kubectl apply -f ${SECRET_FILE} -n ${NAMESPACE}

echo "Deploying ${SERVICE_NAME} to ${NAMESPACE}..."
helm upgrade --install ${SERVICE_NAME} . -n ${NAMESPACE} -f ${VALUES_FILE} --debug --set appConf.image.tag=latest

echo "Checking deployment status..."
kubectl get pods -n ${NAMESPACE} -l appName=${SERVICE_NAME}

echo "Deployment completed."
echo "kubectl logs -f \$(kubectl get pods -n ${NAMESPACE} -l appName=${SERVICE_NAME} -o jsonpath='{.items[0].metadata.name}') -n ${NAMESPACE}"
```

---

## 7. Database (RDS PostgreSQL)

### Org Pattern Reference

Workflow Studio uses Supabase-hosted PostgreSQL with separate env vars (`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`). nrev-lite uses self-managed RDS with a single `DATABASE_URL` connection string consumed by SQLAlchemy async. For V1, keep `DATABASE_URL`; split into separate vars in V2 to align with org pattern.

### RDS Instance Configuration

| Setting | Staging | Prod |
|---------|---------|------|
| Engine | PostgreSQL 15 | PostgreSQL 15 |
| Instance class | db.t3.micro | db.t3.small |
| Storage | 20 GB gp3 | 50 GB gp3 |
| Multi-AZ | No | Yes |
| Backup retention | 7 days | 14 days |
| VPC | Same VPC as EKS | Same VPC as EKS |
| DB subnet group | Existing private subnets | Existing private subnets |
| Security group | Allow 5432 from EKS pod CIDR | Allow 5432 from EKS pod CIDR |
| Master username | `postgres` | `postgres` |
| Database name | `nrev-lite` | `nrev-lite` |

### RDS Creation via AWS CLI

**Staging (ap-south-1):**
```bash
# 1. Create a security group for the RDS instance
aws ec2 create-security-group \
  --group-name nrev-lite-rds-staging-sg \
  --description "Security group for nrev-lite RDS staging" \
  --vpc-id <staging-vpc-id> \
  --region ap-south-1

# 2. Allow inbound PostgreSQL from EKS pod CIDR
aws ec2 authorize-security-group-ingress \
  --group-id <sg-id-from-step-1> \
  --protocol tcp \
  --port 5432 \
  --cidr <eks-pod-cidr>/16 \
  --region ap-south-1

# 3. Create the RDS instance
aws rds create-db-instance \
  --db-instance-identifier nrev-lite-db-staging \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --engine-version 15 \
  --master-username postgres \
  --master-user-password '<strong-password>' \
  --allocated-storage 20 \
  --storage-type gp3 \
  --db-name nrev-lite \
  --vpc-security-group-ids <sg-id-from-step-1> \
  --db-subnet-group-name <existing-db-subnet-group> \
  --backup-retention-period 7 \
  --no-multi-az \
  --no-publicly-accessible \
  --storage-encrypted \
  --region ap-south-1 \
  --tags Key=Environment,Value=staging Key=Service,Value=nrev-lite-api

# 4. Wait for instance to be available
aws rds wait db-instance-available \
  --db-instance-identifier nrev-lite-db-staging \
  --region ap-south-1

# 5. Get the endpoint
aws rds describe-db-instances \
  --db-instance-identifier nrev-lite-db-staging \
  --query 'DBInstances[0].Endpoint.Address' \
  --output text \
  --region ap-south-1
```

**Prod (us-east-1):**
```bash
aws ec2 create-security-group \
  --group-name nrev-lite-rds-prod-sg \
  --description "Security group for nrev-lite RDS prod" \
  --vpc-id <prod-vpc-id> \
  --region us-east-1

aws ec2 authorize-security-group-ingress \
  --group-id <sg-id> \
  --protocol tcp \
  --port 5432 \
  --cidr <eks-pod-cidr>/16 \
  --region us-east-1

aws rds create-db-instance \
  --db-instance-identifier nrev-lite-db-prod \
  --db-instance-class db.t3.small \
  --engine postgres \
  --engine-version 15 \
  --master-username postgres \
  --master-user-password '<strong-password>' \
  --allocated-storage 50 \
  --storage-type gp3 \
  --db-name nrev-lite \
  --vpc-security-group-ids <sg-id> \
  --db-subnet-group-name <existing-db-subnet-group> \
  --backup-retention-period 14 \
  --multi-az \
  --no-publicly-accessible \
  --storage-encrypted \
  --region us-east-1 \
  --tags Key=Environment,Value=prod Key=Service,Value=nrev-lite-api
```

### Initial Database Setup

After RDS instance is created:

```bash
# Connect to RDS (may need bastion/port-forward if not publicly accessible)
psql -h <rds-endpoint> -U postgres -d nrev_lite

# Create application role
CREATE ROLE nrev_lite_api WITH LOGIN PASSWORD '<strong-password>';

# Apply migration tracking first
psql -h <rds-endpoint> -U postgres -d nrev-lite -f migrations/000_schema_migrations.sql

# Apply all migrations in order
for f in migrations/001_*.sql migrations/002_*.sql migrations/003_*.sql \
         migrations/004_*.sql migrations/005_*.sql migrations/006_*.sql \
         migrations/007_*.sql migrations/008_*.sql; do
  echo "Applying $f..."
  psql -h <rds-endpoint> -U postgres -d nrev-lite -f "$f"
done

# Record all migrations
psql -h <rds-endpoint> -U postgres -d nrev-lite -f migrations/000_schema_migrations.sql
```

### Migration Version Tracking

```sql
-- migrations/000_schema_migrations.sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version, filename) VALUES
    ('001', '001_initial.sql'),
    ('002', '002_domain_index.sql'),
    ('003', '003_run_steps.sql'),
    ('004', '004_workflow_label.sql'),
    ('005', '005_datasets.sql'),
    ('006', '006_scheduled_workflows.sql'),
    ('007', '007_dashboard_datasets.sql'),
    ('008', '008_hosted_apps.sql')
ON CONFLICT (version) DO NOTHING;
```

### Verify RLS is Active

```sql
SET ROLE nrev_lite_api;
SET app.current_tenant = 'test-tenant-id';
SELECT * FROM contacts;  -- Should return empty
RESET ROLE;
```

---

## 8. Redis (ElastiCache)

### Org Pattern Reference

Workflow Studio connects to ElastiCache via separate `REDIS_HOST` + `REDIS_PORT` env vars with SSL enabled by default. The Redis client uses SingletonBorg pattern, 5s socket timeout, health check interval 30s. Key prefixes and TTLs are centralized in `constants/redis_constants.py`. See: `/Users/nikhilojha/Projects/workflow_studio/infrastructure/redis_client.py`.

### nrev-lite V1 Approach

nrev-lite uses a single `REDIS_URL` env var (e.g., `rediss://host:6379/0`) with `redis.asyncio`. For V1, keep this. For V2, adopt org pattern (separate vars, SingletonBorg, centralized key constants).

### Reusing Existing ElastiCache Clusters

nrev-lite will reuse the same ElastiCache clusters already used by other org services. No new Redis infrastructure to create.

| Environment | Endpoint (from existing Helm values) | TLS |
|-------------|--------------------------------------|-----|
| Staging | `staging-cache-sooatg.serverless.aps1.cache.amazonaws.com:6379` | Yes (`rediss://`) |
| Prod | `prod-cache-msnit6.serverless.use1.cache.amazonaws.com:6379` | Yes (`rediss://`) |

### What Uses Redis

| Feature | Key Pattern | TTL | Priority |
|---------|-------------|-----|----------|
| Response caching | `cache:exec:{tenant_id}:{op}:{hash}` | 1 hour | Existing |
| Rate limiting | `ratelimit:{provider}:{tenant_id}` | 1 hour | Existing |
| OAuth pending state | `auth:pending:{state}` | 10 min | **New — must implement** |
| Device codes | `auth:device:{device_code}` | 15 min | **New — must implement** |

### Confirm Connectivity

ElastiCache security groups must allow inbound 6379 from EKS pod CIDR. Since other org services in the same EKS cluster already connect to these clusters, this should already be in place. Verify:

```bash
# From an existing pod in the same namespace
kubectl exec -it <any-existing-pod> -n staging -- \
  python3 -c "import redis; r = redis.Redis(host='staging-cache-sooatg.serverless.aps1.cache.amazonaws.com', port=6379, ssl=True); print(r.ping())"
```

---

## 9. Authentication (Google OAuth)

### Google Cloud Console Setup

1. Go to Google Cloud Console → APIs & Services → Credentials
2. Create OAuth 2.0 Client ID (Web application)
3. Add authorized redirect URIs:
   - Staging: `https://nrev-lite-api.public.staging.nurturev.com/api/v1/auth/callback`
   - Prod: `https://nrev-lite-api.public.prod.nurturev.com/api/v1/auth/callback`
   - Dev: `http://localhost:8000/api/v1/auth/callback`
4. Configure OAuth consent screen:
   - Scopes: `email`, `profile`, `openid`
   - Submit for verification when going to production

### Code Changes Required

**1. Move `_pending_auth` to Redis** (`server/auth/router.py`)

Replace the in-memory `_pending_auth: dict` with Redis GET/SET:
- Key: `auth:pending:{state}` (state is the OAuth state parameter)
- Value: JSON `{cli_redirect, code_verifier, console_login}`
- TTL: 600 seconds (10 minutes)
- On callback: GET + DELETE from Redis

**2. Move `_device_codes` to Redis** (`server/auth/router.py`)

Replace the in-memory `_device_codes: dict` with Redis GET/SET:
- Key: `auth:device:{device_code}`
- Value: JSON `{user_code, expires_at, user_id, tenant_id, completed}`
- TTL: 900 seconds (15 minutes)
- On verification: update the Redis entry with `completed=True`
- On poll: GET from Redis, return 428 if not completed

**3. Rate limit auth endpoints**

Add rate limiting on `POST /api/v1/auth/device/token`:
- Reuse existing `server/execution/rate_limiter.py` infrastructure
- Key: `ratelimit:auth:device:{client_ip}`
- Limit: 10 requests per minute

---

## 10. Secrets Management

### Org Pattern Reference

All org services use Kubernetes Secrets with naming convention `{appName}-secret`. Secrets are applied via `kubectl apply -f` from YAML files that are NOT committed to git. Secret values are referenced in Helm values via `valueFrom.secretKeyRef`. See any `values-staging.yaml` in `/Users/nikhilojha/Projects/helm-charts/`.

### nrev-lite Secret Template

Create `nrev-lite-api-secret-staging.yaml` (NOT committed to git):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: nrev-lite-api-secret
  namespace: staging
type: Opaque
stringData:
  JWT_SECRET_KEY: "<generate: python3 -c 'import secrets; print(secrets.token_urlsafe(48))'>"
  GOOGLE_CLIENT_SECRET: "<from Google Cloud Console>"
  DATABASE_URL: "postgresql+asyncpg://nrev_lite_api:<password>@<rds-staging-endpoint>:5432/nrv"
  APOLLO_API_KEY: "<from Apollo.io>"
  ROCKETREACH_API_KEY: "<from RocketReach>"
  X_RAPIDAPI_KEY: "<from RapidAPI>"
  PARALLEL_KEY: "<from Parallel Web>"
  PREDICTLEADS_API_KEY: "<from PredictLeads>"
  COMPOSIO_API_KEY: "<from Composio>"
```

Same structure for `nrev-lite-api-secret-prod.yaml` with production values.

**Note:** `DATABASE_URL` is in the secret because it contains the DB password. This differs from the org pattern where `POSTGRES_PASSWORD` is the only DB-related secret. For V2, when we split into separate vars, only the password will be in the secret.

### Security Notes

- Secret YAML files must NEVER be committed to git
- Generate unique `JWT_SECRET_KEY` per environment
- Provider API keys are optional — without them, that provider is unavailable for platform-key usage (BYOK still works)
- Rotate secrets by updating the Secret manifest and restarting pods

---

## 11. CLI Configuration

### Default Server URL

Before publishing to PyPI, update `src/nrev_lite/utils/config.py`:

```python
# Change from:
DEFAULT_API_BASE_URL = "http://localhost:8000"

# Change to:
DEFAULT_API_BASE_URL = "https://nrev-lite-api.public.prod.nurturev.com"
```

Users can still override via:
```bash
nrev-lite config set server.url https://custom-url.com
# or
export NREV_LITE_SERVER_URL=https://custom-url.com
```

### CORS Configuration

Add `CORS_ALLOWED_ORIGINS` support in `server/app.py`:

```python
# Production: restrict to known origins
origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()] if settings.CORS_ALLOWED_ORIGINS else []

# Development: allow all
if settings.ENVIRONMENT == "development":
    origins = ["*"]
```

---

## 12. Deployment Steps

### First-Time Deployment (Staging)

```bash
# 1. Create ECR repository
aws ecr create-repository --repository-name nrev-lite-api --region ap-south-1

# 2. Build and push Docker image
docker build -f Dockerfile.server -t 979176640062.dkr.ecr.ap-south-1.amazonaws.com/nrev-lite-api:latest .
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 979176640062.dkr.ecr.ap-south-1.amazonaws.com
docker push 979176640062.dkr.ecr.ap-south-1.amazonaws.com/nrev-lite-api:latest

# 3. Set up database (RDS — see Section 7 for full AWS CLI steps)
# After RDS is available:
psql -h <rds-staging-endpoint> -U postgres -d nrev-lite -c "CREATE ROLE nrev_lite_api WITH LOGIN PASSWORD '<password>';"
psql -h <rds-staging-endpoint> -U postgres -d nrev-lite -f migrations/000_schema_migrations.sql
for f in migrations/00[1-8]_*.sql; do
  psql -h <rds-staging-endpoint> -U postgres -d nrev-lite -f "$f"
done
psql -h <rds-staging-endpoint> -U postgres -d nrev-lite -f migrations/000_schema_migrations.sql

# 4. Apply Kubernetes Secret
cd /Users/nikhilojha/Projects/helm-charts/nrev-lite-api
kubectl apply -f nrev-lite-api-secret-staging.yaml -n staging

# 5. Deploy via Helm
aws eks update-kubeconfig --name staging-eks --kubeconfig ~/.kube/staging-eks
cp ~/.kube/staging-eks ~/.kube/config
helm dependency update .
helm upgrade --install nrev-lite-api . -n staging -f values-staging.yaml --debug --set appConf.image.tag=latest

# 6. Verify
kubectl get pods -n staging -l appName=nrev-lite-api
curl https://nrev-lite-api.public.staging.nurturev.com/health
```

### Subsequent Deployments

```bash
# Build, push, restart
docker build -f Dockerfile.server -t 979176640062.dkr.ecr.ap-south-1.amazonaws.com/nrev-lite-api:latest .
docker push 979176640062.dkr.ecr.ap-south-1.amazonaws.com/nrev-lite-api:latest
kubectl rollout restart deploy nrev-lite-api -n staging
```

### Useful Commands

```bash
kubectl get pods -n staging -l appName=nrev-lite-api
kubectl logs -f $(kubectl get pods -n staging -l appName=nrev-lite-api -o jsonpath='{.items[0].metadata.name}') -n staging
kubectl rollout restart deploy nrev-lite-api -n staging
kubectl scale deploy nrev-lite-api --replicas=2 -n staging
```

---

## 13. Post-Deployment Verification

```bash
# 1. Health check
curl https://nrev-lite-api.public.{env}.nurturev.com/health
# Expected: {"status": "ok", "version": "0.1.0"}

# 2. Auth flow
nrev-lite config set server.url https://nrev-lite-api.public.{env}.nurturev.com
nrev-lite auth login

# 3. Status check
nrev-lite status

# 4. Test enrichment (if provider keys configured)
nrev-lite enrich person --email test@example.com

# 5. Redis connectivity (from pod — uses existing ElastiCache)
kubectl exec -it <pod-name> -n staging -- python3 -c "
import asyncio, redis.asyncio as aioredis
async def check():
    r = aioredis.from_url('rediss://staging-cache-sooatg.serverless.aps1.cache.amazonaws.com:6379/0')
    await r.set('nrev-lite:test', 'ok')
    print('Redis OK:', await r.get('nrev-lite:test'))
    await r.delete('nrev-lite:test')
    await r.close()
asyncio.run(check())
"
```

---

## 14. V1 Accepted Constraints

| Constraint | Impact | Mitigation |
|------------|--------|------------|
| Batch capped at 25 records | Large enrichment batches must be split | CLAUDE.md already advises "pilot on 5, use nRev for >100" |
| No Stripe integration | Users cannot self-purchase credits | Admin grants credits manually via DB |
| In-memory batch result store | Batch results lost on pod restart | Actual data persisted in PostgreSQL; re-query works |
| Single health endpoint (`/health`) | No separate readiness check for DB/Redis | Acceptable for single-replica V1 |
| Basic text logging | Harder to search/filter in CloudWatch | Functional for low-volume V1 |
| Console dashboard may be incomplete | Some tabs may have rough edges | Functional for internal use |
| Hosted app files in PostgreSQL JSONB | Not optimized for large files or CDN | Files are small HTML/JS (<100KB) |
| Env var pattern differs from org | `DATABASE_URL`/`REDIS_URL` vs separate vars | Works fine; align in V2 |
| Docker: single-stage, no gunicorn | Less optimized image, no worker recycling | Acceptable for V1 load |
| No structured logging | No request_id/trace_id in logs | Port Workflow Studio logging in V2 |

---

## 15. V2 Roadmap

### Org Pattern Alignment

- [ ] **Env vars → org pattern**: Split `DATABASE_URL` into `POSTGRES_HOST/PORT/USER/PASSWORD/DATABASE` and `REDIS_URL` into `REDIS_HOST/REDIS_PORT/REDIS_SSL`
- [ ] **Structured logging**: Port Workflow Studio logging system — format: `asctime | name.module.funcName | request_id | trace_id | metadata | level | message`. Use ContextVars + custom Filters from `workflow_studio/utils/logger.py` and `workflow_studio/constants/logger_constants.py`
- [ ] **Error handling**: Port Workflow Studio exception hierarchy — `BaseAPIException` → typed exceptions (`MissingResourceError`, `InvalidRequestError`, `RateLimitExceededError`, etc.) with global middleware handler
- [ ] **Docker**: Multi-stage build with gunicorn + uvicorn workers, hash-verified deps, worker recycling (max-requests 1000, jitter 100). Reference: `workflow_studio/Dockerfile_server`
- [ ] **Redis client**: Adopt SingletonBorg pattern with centralized key prefixes/TTLs in `constants/redis_constants.py`. Reference: `workflow_studio/infrastructure/redis_client.py`
- [ ] **Health checks**: Add `/readiness` endpoint (204 No Content) that checks DB + Redis connectivity. Reference: `workflow_studio/api_server.py` and `workflow_studio/lifespan.py`
- [ ] **App initialization**: Adopt lifespan pattern that initializes all clients at startup with explicit error handling. Reference: `workflow_studio/lifespan.py`

### Feature Work

- [ ] **Async batch execution**: Background job pattern (SQS + worker) for batches >25 records
- [ ] **Stripe credit purchase**: Implement `server/billing/stripe_service.py` for self-serve credit packages
- [ ] **S3 for hosted app files**: Move to S3 + CloudFront (or React/Next.js on Vercel)
- [ ] **Console dashboard polish**: Audit and complete all 6 dashboard tabs
- [ ] **Rate limiting response headers**: Return `X-RateLimit-Remaining`, `Retry-After`
- [ ] **Token revocation**: Redis-based JWT blacklist on logout (optional)

### Infrastructure

- [ ] **CI/CD pipeline**: GitHub Actions — lint → test → build Docker → push ECR → helm upgrade
- [ ] **Alembic migrations**: Replace raw SQL + tracking table with proper framework
- [ ] **Monitoring/APM**: Datadog or OpenTelemetry
- [ ] **IAM role for ServiceAccount**: Create `nrev-lite-api-{env}-role` with KMS permissions for BYOK key encryption (V1 uses Fernet fallback)
- [ ] **Multi-replica**: Enable HPA, move batch result store to Redis

---

## Appendix: File Changes Summary

Files that need modification before V1 deploy:

| File | Change |
|------|--------|
| `src/nrev_lite/utils/config.py:16` | Change `DEFAULT_API_BASE_URL` to prod URL |
| `server/auth/router.py` | Move `_pending_auth` and `_device_codes` to Redis |
| `server/auth/router.py` | Add rate limiting on device token endpoint |
| `server/execution/router.py` | Add batch size cap (25 records) |
| `server/app.py` | Add `CORS_ALLOWED_ORIGINS` env var support |
| `server/core/config.py` | Add `CORS_ALLOWED_ORIGINS` setting |
| `Dockerfile.server` | Add `--workers 2`, include migrations/ |

New files to create:

| File | Purpose |
|------|---------|
| `helm-charts/nrev-lite-api/Chart.yaml` | Helm chart definition |
| `helm-charts/nrev-lite-api/values-staging.yaml` | Staging environment values |
| `helm-charts/nrev-lite-api/values-prod.yaml` | Prod environment values |
| `helm-charts/nrev-lite-api/deploy-staging.sh` | Staging deploy script |
| `helm-charts/nrev-lite-api/deploy-prod.sh` | Prod deploy script |
| `migrations/000_schema_migrations.sql` | Migration tracking table |
| `.github/workflows/deploy.yml` | CI/CD pipeline (copy from existing projects) |
