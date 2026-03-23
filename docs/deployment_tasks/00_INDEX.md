# V1 Deployment Tasks — Index

All tasks derived from `docs/DEPLOYMENT.md`. Execute in order — dependencies noted in each task.

## Org Pattern References

Each task includes **"Org Pattern Reference"** sections where the implementation approach differs from or aligns with existing patterns in Workflow Studio (`/Users/nikhilojha/Projects/workflow_studio`) and Helm charts (`/Users/nikhilojha/Projects/helm-charts`). V1 principle: **keep nrev-lite's existing patterns; align with org patterns in V2.**

## Code Changes (must complete before first deploy)

| # | Task | File(s) | Depends On |
|---|------|---------|------------|
| 01 | [Move auth state to Redis](./01_auth_state_to_redis.md) | `server/auth/router.py` | — |
| 02 | [Rate limit auth endpoints](./02_rate_limit_auth_endpoints.md) | `server/auth/router.py` | 01 |
| 03 | [Cap batch size at 25 records](./03_cap_batch_size.md) | `server/execution/router.py` | — |
| 04 | [Add CORS allowed origins config](./04_cors_allowed_origins.md) | `server/app.py`, `server/core/config.py` | — |
| 05 | [Update default CLI server URL](./05_update_default_server_url.md) | `src/nrev_lite/utils/config.py` | — |
| 06 | [Update Dockerfile for production](./06_update_dockerfile.md) | `Dockerfile.server` | — |
| 07 | [Add schema_migrations tracking](./07_schema_migrations_tracking.md) | `migrations/000_schema_migrations.sql` | — |

## Infrastructure & Deployment (execute after code changes)

| # | Task | Artifact(s) | Depends On |
|---|------|-------------|------------|
| 08 | [Create Helm chart](./08_create_helm_chart.md) | `helm-charts/nrev-lite-api/` | 06 |
| 09 | [Provision infrastructure](./09_provision_infrastructure.md) | RDS, ECR, DNS, Secrets (ElastiCache reused) | — |
| 10 | [First deploy + verification](./10_first_deploy_and_verify.md) | — | All above |
| 11 | [Environment management & CI/CD](./11_environment_management.md) | Branches, GitHub Actions | 10 (first deploy is manual) |

## Execution Order

Tasks 01-07 are code changes that can be done in parallel (except 02 depends on 01).
Tasks 08-09 can be done in parallel with each other and with 01-07.
Task 10 requires all of 01-09 to be complete.
Task 11 (branches + CI/CD) can start in parallel with 01-09 but CI/CD only works after Task 10.

```
01 ──→ 02
03 ────────┐
04 ────────┤
05 ────────┼──→ 10 ──→ 11 (CI/CD automated deploys)
06 ──→ 08 ─┤
07 ────────┤
09 ────────┤
11 (branches)─┘
```

**Note:** Task 11 branch creation (`staging` branch) should happen early — the first manual deploy (Task 10) will deploy FROM the staging branch. The CI/CD workflows are added after the first manual deploy is verified.
