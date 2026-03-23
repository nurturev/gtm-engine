# Task 11: Environment Management & CI/CD Setup

**Status:** Not Started
**Priority:** P0 — Required before first deploy (need staging branch to deploy from)
**Deployment Doc Reference:** Sections 2, 12, 15

---

## Goal

Set up environment management following the org pattern from Workflow Studio: branch-based deployments with `staging` and `prod` branches triggering automated CI/CD to their respective environments.

---

## Current State

- **Branches:** Only `main` exists. No `staging` or `prod` branches.
- **CI/CD:** No `.github/workflows/` directory. Zero automation.
- **Environment config:** `ENVIRONMENT` var exists in `server/core/config.py` (default: `development`). Only used for CORS switching today.
- **No `.env` file:** Only `.env.example` exists (gitignored `.env` would be created locally).

---

## Org Pattern Reference (Workflow Studio)

Workflow Studio uses this branch → environment mapping:

| Branch | Environment | Region | Deploys To |
|--------|-------------|--------|------------|
| `main` | — | — | Code quality tests only, no deploy |
| `staging` | staging | ap-south-1 (Mumbai) | EKS staging namespace + Lambda staging |
| `prod` | prod | us-east-1 (N. Virginia) | EKS prod namespace + Lambda prod |

**CI/CD files in Workflow Studio:**
- `.github/workflows/code-quality-tests.yml` — runs on all branches + PRs
- `.github/workflows/deployment-k8s-staging.yml` — push to `staging` → build → push ECR → helm upgrade (staging)
- `.github/workflows/deployment-k8s-prod.yml` — push to `prod` → build → push ECR → helm upgrade (prod)
- `.github/workflows/deployment-staging.yml` — Lambda/Serverless staging deploy
- `.github/workflows/deployment-prod.yml` — Lambda/Serverless prod deploy

**Workflow:** Feature branches → PR to `main` → merge → PR to `staging` → merge (auto-deploys) → PR to `prod` → merge (auto-deploys).

Reference: `/Users/nikhilojha/Projects/workflow_studio/.github/workflows/`

---

## What to Create

### 1. Branches

```bash
# Create staging branch from main
git checkout main
git checkout -b staging
git push origin staging

# Prod branch — create later when staging is verified
# git checkout main
# git checkout -b prod
# git push origin prod
```

### 2. GitHub Actions — Code Quality Tests

File: `.github/workflows/code-quality-tests.yml`

Runs on: pushes to `main`, `staging`, `prod` + all PRs.

```yaml
name: Code Quality Tests

on:
  push:
    branches: [main, staging, prod]
  pull_request:
    branches: [main, staging, prod]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          pip install -r requirements-server.txt
          pip install ruff pytest pytest-asyncio

      - name: Lint with ruff
        run: |
          ruff check .
          ruff format --check .

      - name: Run tests
        run: pytest tests/ -v --asyncio-mode=auto
        env:
          JWT_SECRET_KEY: test-secret-key-for-ci-at-least-32-chars-long
          GOOGLE_CLIENT_ID: test-client-id
          GOOGLE_CLIENT_SECRET: test-client-secret
          ENVIRONMENT: development
```

### 3. GitHub Actions — Staging K8s Deployment

File: `.github/workflows/deployment-k8s-staging.yml`

Runs on: pushes to `staging` branch.

```yaml
name: Deploy to Staging (K8s)

on:
  push:
    branches: [staging]

env:
  AWS_REGION: ap-south-1
  ECR_REPOSITORY: nrev-lite-api
  EKS_CLUSTER: staging-eks
  K8S_NAMESPACE: staging
  HELM_CHART_PATH: ../helm-charts/nrev-lite-api

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Login to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push Docker image
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          IMAGE_TAG: ${{ github.sha }}
        run: |
          docker build -f Dockerfile.server -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG -t $ECR_REGISTRY/$ECR_REPOSITORY:latest .
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest

      - name: Update kubeconfig
        run: |
          aws eks update-kubeconfig --name $EKS_CLUSTER --region $AWS_REGION

      - name: Deploy to EKS via Helm
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          IMAGE_TAG: ${{ github.sha }}
        run: |
          # Checkout helm-charts repo (or use a separate step)
          # For now, use kubectl rollout which doesn't need helm-charts repo
          kubectl set image deployment/nrev-lite-api nrev-lite-api=$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG -n $K8S_NAMESPACE
          kubectl rollout status deployment/nrev-lite-api -n $K8S_NAMESPACE --timeout=120s

      - name: Verify deployment
        run: |
          kubectl get pods -n $K8S_NAMESPACE -l appName=nrev-lite-api
```

**Note:** The first deploy must be done manually via `deploy-staging.sh` (Helm install). Subsequent deploys can use `kubectl set image` (rolling update) as shown above, or a full `helm upgrade`. Decide based on how Workflow Studio's K8s deploy workflow works.

### 4. GitHub Actions — Prod K8s Deployment

File: `.github/workflows/deployment-k8s-prod.yml`

Same structure as staging with:
- Trigger: `push to prod`
- `AWS_REGION: us-east-1`
- `EKS_CLUSTER: prod-eks`
- `K8S_NAMESPACE: prod`

---

## GitHub Secrets Required

These must be set in the GitHub repo settings (Settings → Secrets and variables → Actions):

| Secret | Value | Notes |
|--------|-------|-------|
| `AWS_ACCESS_KEY_ID` | IAM user access key | Same as used by other org repos |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key | Same as used by other org repos |

These are likely already configured as org-level secrets if other repos (workflow_studio) use them. Check if this repo can inherit org secrets, or copy them.

---

## Credentials & API Keys for Staging

You mentioned you'll add the credentials. Here's the complete list of what's needed for the staging environment:

### Required (server won't start without these)

| Credential | Where to Get | Injected Via |
|------------|-------------|--------------|
| `JWT_SECRET_KEY` | Generate: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` | K8s Secret |
| `GOOGLE_CLIENT_ID` | Google Cloud Console → APIs & Services → Credentials | Helm values (plain) |
| `GOOGLE_CLIENT_SECRET` | Same as above | K8s Secret |
| `DATABASE_URL` | Constructed from RDS endpoint + nrev_lite_api password (Task 09) | K8s Secret |

### Required infrastructure (must exist before deploy)

| Resource | Value Needed |
|----------|-------------|
| RDS PostgreSQL endpoint | From Task 09 — `nrev-lite-db-staging.xxx.ap-south-1.rds.amazonaws.com` |
| RDS `nrev_lite_api` role password | You set during RDS setup |
| ElastiCache endpoint | Existing: `staging-cache-sooatg.serverless.aps1.cache.amazonaws.com` |
| DNS record | `nrev-lite-api.public.staging.nurturev.com` → EKS ingress LB |

### Optional (provider API keys — service runs without them)

| Credential | Where to Get | What It Enables |
|------------|-------------|-----------------|
| `APOLLO_API_KEY` | [apollo.io](https://apollo.io) → Settings → API Keys | Person/company enrichment & search |
| `ROCKETREACH_API_KEY` | [rocketreach.co](https://rocketreach.co) → Integrations | Contact finding, alumni search |
| `X_RAPIDAPI_KEY` | [rapidapi.com](https://rapidapi.com) → Dashboard → App Keys | Google web search |
| `PARALLEL_KEY` | [parallel.ai](https://parallel.ai) → API Keys | Web scraping, page extraction |
| `PREDICTLEADS_API_KEY` | [predictleads.com](https://predictleads.com) → Account | Company intelligence |
| `COMPOSIO_API_KEY` | [composio.dev](https://composio.dev) → Settings → API | OAuth app connections (Slack, Gmail, etc.) |
| `STRIPE_SECRET_KEY` | [stripe.com](https://stripe.com) → API Keys | Not needed for V1 (Stripe is stubbed) |

### Google OAuth Setup

You need a Google Cloud project with OAuth 2.0 credentials. If one already exists from the original developer:
- Get the Client ID and Client Secret
- Add redirect URI: `https://nrev-lite-api.public.staging.nurturev.com/api/v1/auth/callback`

If creating new:
1. Google Cloud Console → New Project (or existing)
2. APIs & Services → Credentials → Create OAuth 2.0 Client ID
3. Application type: Web application
4. Authorized redirect URIs: add staging + dev URLs
5. OAuth consent screen: scopes `email`, `profile`, `openid`

---

## Acceptance Criteria

- [ ] `staging` branch exists and is pushed to origin
- [ ] `.github/workflows/code-quality-tests.yml` runs on PRs
- [ ] `.github/workflows/deployment-k8s-staging.yml` triggers on push to staging
- [ ] GitHub Secrets (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) are configured
- [ ] Merging to `staging` branch builds Docker image, pushes to ECR, deploys to EKS
- [ ] `ENVIRONMENT=staging` is set in Helm values
- [ ] Staging deployment is reachable at `nrev-lite-api.public.staging.nurturev.com`

---

## Execution Order

1. Create `staging` branch and push to origin
2. Create `.github/workflows/` directory with the workflow files
3. Configure GitHub Secrets (AWS credentials)
4. First deploy is manual (Task 10), subsequent deploys are automated via CI/CD
5. Create `prod` branch only after staging is verified
