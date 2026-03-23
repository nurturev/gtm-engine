# Task 08: Create Helm Chart

**Status:** Not Started
**Priority:** P0 — Required for EKS deployment
**Depends On:** Task 06 (Dockerfile must be finalized)
**Deployment Doc Reference:** Section 5

---

## Goal

Create a Helm chart for the nrev-lite-api service in the `helm-charts` repo, following the exact same pattern as `user-management-ws`.

### Org Pattern Reference

All org services share the base template at `/Users/nikhilojha/Projects/helm-charts/base-templates/service/`. Key patterns to follow:
- `Chart.yaml` declares dependency on base service template with `alias: appConf`
- All config nested under `appConf:` key
- Env vars: plain `value:` for non-secrets, `valueFrom.secretKeyRef` for secrets
- Secret naming: `{appName}-secret` → `nrev-lite-api-secret`
- Ingress DNS: `{service}.public.{env}.nurturev.com` → `nrev-lite-api.public.{env}.nurturev.com`
- Resources: 200m/500m CPU, 512Mi memory (small-service tier, same as user-management-ws, alerts)
- Lifecycle: preStop sleep 30, terminationGracePeriodSeconds 60
- Deployment: RollingUpdate, 25% maxSurge, 0% maxUnavailable

Reference charts: `helm-charts/user-management-ws/`, `helm-charts/alerts/`, `helm-charts/workflow-studio/`

---

## Files to Create

All files created in `/Users/nikhilojha/Projects/helm-charts/nrev-lite-api/`:

### 1. Chart.yaml

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

### 2. values-staging.yaml

Full content in `docs/DEPLOYMENT.md` Section 5. Key points:
- `appConf.appName: nrev-lite-api`
- `image.repository: 979176640062.dkr.ecr.ap-south-1.amazonaws.com/nrev-lite-api`
- `ingress.rules[0].host: nrev-lite-api.public.staging.nurturev.com`
- `ports[0].target_port: 8000` (FastAPI, not 8080)
- Health checks on `/health` port 8000
- All secrets via `secretKeyRef` referencing `nrev-lite-api-secret`
- Environment variables: DATABASE_URL, REDIS_URL, GOOGLE_CLIENT_ID, GOOGLE_REDIRECT_URI, CORS_ALLOWED_ORIGINS, ENVIRONMENT

### 3. values-prod.yaml

Same structure as staging with prod-specific values:
- Region: `us-east-1`
- ECR: `979176640062.dkr.ecr.us-east-1.amazonaws.com/nrev-lite-api`
- Host: `nrev-lite-api.public.prod.nurturev.com`
- Prod RDS and ElastiCache endpoints

### 4. deploy-staging.sh

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

### 5. deploy-prod.sh

Same as staging with `NAMESPACE="prod"`, `VALUES_FILE="values-prod.yaml"`, `SECRET_FILE="nrev-lite-api-secret-prod.yaml"`, and `prod-eks` cluster name.

---

## Key Differences from user-management-ws

| Setting | Org Standard (user-management-ws) | nrev-lite-api | Why Different |
|---------|-------------------|---------|---------------|
| Container port | 8080 | 8000 | FastAPI default, keep for V1 |
| Health endpoint | `/healthCheck` | `/health` | Already implemented, keep for V1 |
| Readiness endpoint | `/readiness` | `/health` (same for V1) | V2: add separate `/readiness` |
| DB env vars | `POSTGRES_HOST/PORT/USER/PASSWORD` (separate) | `DATABASE_URL` (via secret) | V2: split into separate vars |
| Redis env vars | `REDIS_HOST` + `REDIS_PORT` (separate) | `REDIS_URL` (single) | V2: split into separate vars |
| Secret name | `user-management-ws-secret` | `nrev-lite-api-secret` | Follows `{appName}-secret` pattern |
| Ingress host | `umws.public.{env}.nurturev.com` | `nrev-lite-api.public.{env}.nurturev.com` | Follows org DNS convention |

**Note:** `DATABASE_URL` goes into the Kubernetes Secret (not plain value) because it contains the DB password. This differs from the org pattern where only `POSTGRES_PASSWORD` is in the secret. V2 alignment will fix this.

---

## Acceptance Criteria

- [ ] `Chart.yaml` exists with correct dependency on base-templates
- [ ] `values-staging.yaml` has all required env vars, secrets, probes, resources
- [ ] `values-prod.yaml` has prod-specific values
- [ ] `deploy-staging.sh` and `deploy-prod.sh` are executable
- [ ] `helm dependency update .` succeeds
- [ ] `helm template nrev-lite-api . -f values-staging.yaml` renders valid manifests
- [ ] Port 8000 is correctly mapped throughout (not 8080)

---

## Testing

```bash
cd /Users/nikhilojha/Projects/helm-charts/nrev-lite-api

# Validate chart
helm dependency update .
helm lint . -f values-staging.yaml

# Dry run
helm template nrev-lite-api . -f values-staging.yaml > /tmp/nrev-lite-api-manifests.yaml
# Review the output for correctness

# Check port mapping
grep -A2 "containerPort" /tmp/nrev-lite-api-manifests.yaml
# Should show 8000, not 8080
```
