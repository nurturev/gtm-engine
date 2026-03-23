# Task 10: First Deploy and Verification

**Status:** Not Started
**Priority:** P0 — Final step
**Depends On:** All tasks 01-09, Task 11 (staging branch must exist)
**Deployment Doc Reference:** Sections 12, 13

---

## Goal

Execute the first deployment to staging, run the full verification checklist, then deploy to prod.

---

## Pre-Flight Check

Before deploying, confirm all prerequisites:

- [ ] Task 01 complete: Auth state moved to Redis
- [ ] Task 02 complete: Auth rate limiting added
- [ ] Task 03 complete: Batch size capped at 25
- [ ] Task 04 complete: CORS allowed origins configurable
- [ ] Task 05 complete: Default server URL updated (do this LAST, after staging verified)
- [ ] Task 11 partial: `staging` branch exists and code changes are merged into it
- [ ] Task 06 complete: Dockerfile updated
- [ ] Task 07 complete: Schema migrations tracking ready
- [ ] Task 08 complete: Helm chart created
- [ ] Task 09 complete: Infrastructure provisioned

---

## Staging Deployment Steps

```bash
# 1. Build and push Docker image
cd /Users/nikhilojha/Projects/gtm-engine
docker build -f Dockerfile.server -t 979176640062.dkr.ecr.ap-south-1.amazonaws.com/nrev-lite-api:latest .
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 979176640062.dkr.ecr.ap-south-1.amazonaws.com
docker push 979176640062.dkr.ecr.ap-south-1.amazonaws.com/nrev-lite-api:latest

# 2. Ensure infrastructure is ready (Task 09)
#    - RDS endpoint available and migrations applied
#    - ElastiCache (existing) connectivity confirmed
#    - DNS record created
#    - K8s Secrets applied

# 3. Deploy via Helm
cd /Users/nikhilojha/Projects/helm-charts/nrev-lite-api
chmod +x deploy-staging.sh
./deploy-staging.sh

# 4. Wait for pod to be ready
kubectl get pods -n staging -l appName=nrev-lite-api -w
# Wait until STATUS = Running, READY = 1/1
```

---

## Verification Checklist (Staging)

### Infrastructure connectivity

- [ ] `curl https://nrev-lite-api.public.staging.nurturev.com/health` returns `{"status": "ok"}`
- [ ] Pod logs show no DB connection errors
- [ ] Pod logs show no Redis connection errors

### Authentication

- [ ] `nrev-lite config set server.url https://nrev-lite-api.public.staging.nurturev.com`
- [ ] `nrev-lite auth login` opens browser, completes Google OAuth, saves credentials
- [ ] `nrev-lite status` shows authenticated user with correct tenant

### Core functionality

- [ ] `nrev-lite enrich person --email test@example.com` works (if Apollo key configured)
- [ ] `nrev-lite web search --query "test"` works (if RapidAPI key configured)
- [ ] `nrev-lite credits balance` returns balance

### Redis features (using existing org ElastiCache clusters)

- [ ] OAuth flow survives server pod restart (start auth → restart pod → complete auth)
- [ ] Repeated API calls show cache hits in logs
- [ ] Redis keys have `nrev-lite:`-prefixed patterns (no collision with other org services on shared cluster)

### Console dashboard

- [ ] `https://nrev-lite-api.public.staging.nurturev.com/console` loads login page
- [ ] Google login works, redirects to tenant dashboard
- [ ] Dashboard tabs render (Keys, Connections, Usage, Runs, Datasets, Dashboards)

### MCP integration

- [ ] Configure Claude Code with staging server URL
- [ ] `nrev_health` MCP tool returns success
- [ ] `nrev_credit_balance` MCP tool returns balance
- [ ] `nrev_enrich_person` MCP tool works end-to-end

---

## Production Deployment

After staging is verified:

1. Update `src/nrev_lite/utils/config.py` with prod URL (Task 05)
2. Build and push prod image (us-east-1 ECR)
3. Run `deploy-prod.sh`
4. Repeat verification checklist against prod domain
5. Publish CLI to PyPI (optional, can be a separate task)

```bash
# Prod deploy
cd /Users/nikhilojha/Projects/gtm-engine
docker build -f Dockerfile.server -t 979176640062.dkr.ecr.us-east-1.amazonaws.com/nrev-lite-api:latest .
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 979176640062.dkr.ecr.us-east-1.amazonaws.com
docker push 979176640062.dkr.ecr.us-east-1.amazonaws.com/nrev-lite-api:latest

cd /Users/nikhilojha/Projects/helm-charts/nrev-lite-api
chmod +x deploy-prod.sh
./deploy-prod.sh
```

---

## Rollback Plan

If deployment fails:

```bash
# Scale down the broken deployment
kubectl scale deploy nrev-lite-api --replicas=0 -n staging

# Check logs for the failure
kubectl logs -l appName=nrev-lite-api -n staging --previous

# Fix the issue, rebuild, redeploy
```

If the pod starts but the service is broken:

```bash
# Rollback to previous Helm release
helm rollback nrev-lite-api -n staging

# Or rollout to previous revision
kubectl rollout undo deploy nrev-lite-api -n staging
```

---

## Acceptance Criteria

- [ ] Staging deployment is running and passing all verification checks
- [ ] Prod deployment is running and passing all verification checks
- [ ] CLI works end-to-end against prod server without manual server URL config
- [ ] MCP tools work from Claude Code against prod server

---

## Post-Deploy Notes

After successful production deployment:
- Notify the team that nrev-lite-api is live
- Decide on PyPI publish timing (separate from server deploy)
- Set up GitHub Actions CI/CD for future deploys (V2 task)
- Monitor CloudWatch logs for the first 24-48 hours
