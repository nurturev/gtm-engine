# Task 09: Provision Infrastructure

**Status:** Not Started
**Priority:** P0 — Required before first deploy
**Deployment Doc Reference:** Sections 4, 7, 8, 10

---

## Goal

Provision all AWS infrastructure needed for the nrev-lite-api service. **RDS PostgreSQL is the only new resource to create.** ElastiCache Redis, EKS clusters, VPC/subnets, and NAT gateways are existing org infrastructure to reuse.

---

## What's New vs Reused

| Resource | Status | Action |
|----------|--------|--------|
| RDS PostgreSQL | **New** | Create via AWS CLI |
| ElastiCache Redis | **Existing** | Reuse org clusters, confirm connectivity |
| ECR Repository | **New** | Create via AWS CLI |
| EKS Cluster | **Existing** | Reuse staging-eks / prod-eks |
| VPC / Subnets | **Existing** | Same VPC as EKS |
| DNS Records | **New** | Create via Route53 or Terraform |
| IAM Role | **New** | Create for ServiceAccount (IRSA) |
| K8s Secrets | **New** | Create via kubectl |
| S3 | **Existing** | Reuse if needed; no new buckets for V1 |

---

## Checklist

### 1. ECR Repository

```bash
# Staging (ap-south-1)
aws ecr create-repository \
  --repository-name nrev-lite-api \
  --region ap-south-1 \
  --image-scanning-configuration scanOnPush=true \
  --tags Key=Service,Value=nrev-lite-api Key=Environment,Value=staging

# Prod (us-east-1)
aws ecr create-repository \
  --repository-name nrev-lite-api \
  --region us-east-1 \
  --image-scanning-configuration scanOnPush=true \
  --tags Key=Service,Value=nrev-lite-api Key=Environment,Value=prod
```

- [ ] ECR repo created in ap-south-1 (staging)
- [ ] ECR repo created in us-east-1 (prod)

### 2. RDS PostgreSQL (NEW — must create)

#### Org Pattern Reference

Workflow Studio uses Supabase-hosted PostgreSQL. nrev-lite uses self-managed RDS with SQLAlchemy async. The RDS approach is different from org but necessary because nrev-lite needs RLS (Row-Level Security) which requires direct PostgreSQL access, not a PostgREST proxy.

#### Staging (ap-south-1)

```bash
# 1. Find VPC ID and subnets (same VPC as EKS)
# Use the VPC where staging-eks runs
aws eks describe-cluster \
  --name staging-eks \
  --query 'cluster.resourcesVpcConfig.vpcId' \
  --output text \
  --region ap-south-1
# Note the VPC ID

# 2. Find existing DB subnet group (or create one)
aws rds describe-db-subnet-groups \
  --region ap-south-1 \
  --query 'DBSubnetGroups[*].DBSubnetGroupName' \
  --output text
# If none exists, create one using private subnets:
# aws rds create-db-subnet-group \
#   --db-subnet-group-name nrev-lite-db-subnet-group \
#   --db-subnet-group-description "nrev-lite RDS subnet group" \
#   --subnet-ids <private-subnet-1> <private-subnet-2> \
#   --region ap-south-1

# 3. Create security group for RDS
aws ec2 create-security-group \
  --group-name nrev-lite-rds-staging-sg \
  --description "Security group for nrev-lite RDS staging" \
  --vpc-id <vpc-id> \
  --region ap-south-1
# Note the security group ID

# 4. Allow inbound PostgreSQL from EKS pod CIDR
# Find the EKS pod CIDR:
aws eks describe-cluster \
  --name staging-eks \
  --query 'cluster.kubernetesNetworkConfig.serviceIpv4Cidr' \
  --output text \
  --region ap-south-1
# Also allow from VPC CIDR for broader access:
aws ec2 describe-vpcs \
  --vpc-ids <vpc-id> \
  --query 'Vpcs[0].CidrBlock' \
  --output text \
  --region ap-south-1

aws ec2 authorize-security-group-ingress \
  --group-id <rds-sg-id> \
  --protocol tcp \
  --port 5432 \
  --cidr <vpc-cidr> \
  --region ap-south-1

# 5. Create the RDS instance
aws rds create-db-instance \
  --db-instance-identifier nrev-lite-db-staging \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --engine-version 15 \
  --master-username postgres \
  --master-user-password '<generate-strong-password>' \
  --allocated-storage 20 \
  --storage-type gp3 \
  --db-name nrev-lite \
  --vpc-security-group-ids <rds-sg-id> \
  --db-subnet-group-name <subnet-group-name> \
  --backup-retention-period 7 \
  --no-multi-az \
  --no-publicly-accessible \
  --storage-encrypted \
  --region ap-south-1 \
  --tags Key=Environment,Value=staging Key=Service,Value=nrev-lite-api

# 6. Wait for instance to be available (~5-10 min)
aws rds wait db-instance-available \
  --db-instance-identifier nrev-lite-db-staging \
  --region ap-south-1

# 7. Get the endpoint
aws rds describe-db-instances \
  --db-instance-identifier nrev-lite-db-staging \
  --query 'DBInstances[0].Endpoint.Address' \
  --output text \
  --region ap-south-1
```

#### Prod (us-east-1)

```bash
# Same steps as staging with these differences:
# --db-instance-identifier nrev-lite-db-prod
# --db-instance-class db.t3.small (larger)
# --allocated-storage 50 (larger)
# --multi-az (high availability)
# --backup-retention-period 14 (longer)
# --region us-east-1
# Tags: Key=Environment,Value=prod

aws rds create-db-instance \
  --db-instance-identifier nrev-lite-db-prod \
  --db-instance-class db.t3.small \
  --engine postgres \
  --engine-version 15 \
  --master-username postgres \
  --master-user-password '<generate-strong-password>' \
  --allocated-storage 50 \
  --storage-type gp3 \
  --db-name nrev-lite \
  --vpc-security-group-ids <prod-rds-sg-id> \
  --db-subnet-group-name <prod-subnet-group-name> \
  --backup-retention-period 14 \
  --multi-az \
  --no-publicly-accessible \
  --storage-encrypted \
  --region us-east-1 \
  --tags Key=Environment,Value=prod Key=Service,Value=nrev-lite-api
```

#### Database Initialization

```bash
# Connect (may need bastion/port-forward if not publicly accessible)
psql -h <rds-staging-endpoint> -U postgres -d nrev_lite

# Create application role
CREATE ROLE nrev_lite_api WITH LOGIN PASSWORD '<strong-password>';

# Apply migration tracking
psql -h <rds-endpoint> -U postgres -d nrev-lite -f migrations/000_schema_migrations.sql

# Apply all migrations
for f in migrations/00[1-8]_*.sql; do
  echo "Applying $f..."
  psql -h <rds-endpoint> -U postgres -d nrev-lite -f "$f"
done

# Record applied migrations
psql -h <rds-endpoint> -U postgres -d nrev-lite -f migrations/000_schema_migrations.sql

# Verify RLS
psql -h <rds-endpoint> -U postgres -d nrev-lite -c "SET ROLE nrev_lite_api; SET app.current_tenant = 'test'; SELECT count(*) FROM contacts; RESET ROLE;"
```

- [ ] RDS instance created (staging)
- [ ] RDS instance created (prod)
- [ ] Security group allows EKS pods → RDS (staging)
- [ ] Security group allows EKS pods → RDS (prod)
- [ ] Role `nrev_lite_api` created with password (staging)
- [ ] Role `nrev_lite_api` created with password (prod)
- [ ] All 8 migrations + 000_schema_migrations applied (staging)
- [ ] All 8 migrations + 000_schema_migrations applied (prod)
- [ ] RLS verified working (staging)

### 3. ElastiCache Redis (EXISTING — reuse)

nrev-lite reuses existing org ElastiCache clusters. No new infrastructure to create.

| Environment | Existing Endpoint | Source |
|-------------|-------------------|--------|
| Staging | `staging-cache-sooatg.serverless.aps1.cache.amazonaws.com:6379` | From `helm-charts/user-management-ws/values-staging.yaml` |
| Prod | `prod-cache-msnit6.serverless.use1.cache.amazonaws.com:6379` | From `helm-charts/user-management-ws/values-prod.yaml` |

**Org Pattern Reference:** Workflow Studio connects using separate `REDIS_HOST` + `REDIS_PORT` with SSL enabled. nrev-lite uses `REDIS_URL` (`rediss://` for TLS). Both connect to the same ElastiCache clusters.

Connectivity should already work since other services in the same EKS cluster use these clusters. Verify:

```bash
# From any existing pod in the same EKS namespace
kubectl exec -it <any-pod> -n staging -- \
  python3 -c "import redis; r = redis.Redis(host='staging-cache-sooatg.serverless.aps1.cache.amazonaws.com', port=6379, ssl=True); print(r.ping())"
```

- [ ] ElastiCache endpoint confirmed accessible from staging EKS
- [ ] ElastiCache endpoint confirmed accessible from prod EKS
- [ ] TLS connection string noted (`rediss://` prefix)

### 4. DNS Records

Follow org DNS convention: `{service}.public.{env}.nurturev.com`

- [ ] `nrev-lite-api.public.staging.nurturev.com` → staging EKS nginx ingress load balancer
- [ ] `nrev-lite-api.public.prod.nurturev.com` → prod EKS nginx ingress load balancer

### 5. Google OAuth

- [ ] Google Cloud project has OAuth 2.0 Web Application credentials
- [ ] Redirect URI whitelisted: `https://nrev-lite-api.public.staging.nurturev.com/api/v1/auth/callback`
- [ ] Redirect URI whitelisted: `https://nrev-lite-api.public.prod.nurturev.com/api/v1/auth/callback`
- [ ] Redirect URI whitelisted: `http://localhost:8000/api/v1/auth/callback` (dev)
- [ ] OAuth consent screen configured: scopes `email`, `profile`, `openid`

### 6. IAM Role for ServiceAccount (IRSA)

Org pattern: ServiceAccount annotated with `eks.amazonaws.com/role-arn`. See: `helm-charts/user-management-ws/values-staging.yaml` → `iamrole` field.

nrev-lite needs KMS access for BYOK key encryption (Fernet in dev, KMS in prod).

```bash
# Create IAM role with trust policy for EKS OIDC
# Get OIDC provider:
aws eks describe-cluster --name staging-eks --query 'cluster.identity.oidc.issuer' --output text --region ap-south-1

# Create role (use console or CloudFormation — trust policy needs OIDC provider ARN)
# Attach policy: Allow kms:Encrypt, kms:Decrypt on alias/nrev-lite-tenant-keys
```

- [ ] IAM role `nrev-lite-api-staging-role` created with KMS permissions
- [ ] IAM role `nrev-lite-api-prod-role` created with KMS permissions
- [ ] Trust policy configured for EKS OIDC provider

### 7. Kubernetes Secrets

Follows org pattern: `{appName}-secret` via `kubectl apply -f`. Secret YAML never committed to git.

Create `nrev-lite-api-secret-staging.yaml`:

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

**Note:** `DATABASE_URL` is in the secret because it contains the DB password. In V2, when aligning with org pattern (separate `POSTGRES_*` vars), only `POSTGRES_PASSWORD` will be in the secret.

```bash
kubectl apply -f nrev-lite-api-secret-staging.yaml -n staging
kubectl apply -f nrev-lite-api-secret-prod.yaml -n prod
```

- [ ] Secret manifest created (staging) — stored securely, NOT in git
- [ ] Secret manifest created (prod) — stored securely, NOT in git
- [ ] Secrets applied to staging namespace
- [ ] Secrets applied to prod namespace

---

## Acceptance Criteria

- [ ] All checklist items above completed for staging
- [ ] Can connect to new RDS from an EKS pod in staging
- [ ] Can connect to existing ElastiCache from an EKS pod in staging
- [ ] DNS resolves correctly for staging domain
- [ ] Kubernetes Secret is accessible from staging namespace
- [ ] RLS is verified working on staging RDS

---

## Notes

- Provider API keys (Apollo, RocketReach, etc.) are optional — the service starts without them
- Prod infrastructure can be provisioned after staging is verified working
- RDS backup and recovery is managed by AWS — configured via `--backup-retention-period`
- ElastiCache is shared infrastructure — be careful with key namespacing to avoid collisions (nrev-lite uses `cache:exec:`, `ratelimit:`, `auth:` prefixes)
