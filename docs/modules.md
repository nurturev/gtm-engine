# nrev-lite Server Module Boundary Documentation

This document describes the modular architecture of the nrev-lite server. Each module
is a self-contained package under `server/` with clear boundaries. The **Core**
module is the only shared dependency — other modules must not import from each
other directly.

---

## 1. Core Module — `server/core/`

Shared infrastructure used by every other module.

| File | Responsibility |
|------|---------------|
| `config.py` | `Settings` (Pydantic-settings) and the singleton `settings` instance |
| `database.py` | Async SQLAlchemy engine, session factory, `get_db` dependency, `set_tenant_context` RLS helper |
| `security.py` | JWT creation / verification (`create_access_token`, `verify_token`), refresh-token generation, password / token hashing |
| `exceptions.py` | Custom exception hierarchy: `InsufficientCredits`, `ProviderError`, `AuthError`, `NotFoundError`, `ForbiddenError` |
| `middleware.py` | ASGI middleware: request-ID injection, tenant-context extraction from JWT, rate-limit response headers |

**Import rule:** Every module may import from `server.core`.

---

## 2. Auth Module — `server/auth/`

Authentication, user management, and Google OAuth.

| File | Responsibility |
|------|---------------|
| `router.py` | API endpoints: `/api/v1/auth/google`, `/callback`, `/refresh`, `/device/code`, `/device/token`, `/me` |
| `service.py` | Business logic: Google OAuth code exchange, `find_or_create_user`, `generate_tokens` |
| `models.py` | SQLAlchemy models: `User`, `Tenant`, `RefreshToken` |
| `schemas.py` | Pydantic v2 request / response schemas for all auth endpoints |
| `dependencies.py` | FastAPI dependencies: `get_current_user`, `get_current_tenant`, `require_credits` |

**Exports:** `get_current_user`, `get_current_tenant`, `require_credits`, `User`, `Tenant`.

---

## 3. Execution Module — `server/execution/`

Provider proxy and execution engine for enrichment / search operations.

| File | Responsibility |
|------|---------------|
| `router.py` | API endpoints: `/api/v1/execute`, `/execute/batch`, `/execute/batch/{id}` |
| `service.py` | Orchestration: parallel, waterfall, and single execution strategies |
| `providers/__init__.py` | Provider registry |
| `providers/base.py` | `BaseProvider` abstract base class |
| `providers/apollo.py` | Apollo provider implementation |
| `providers/rocketreach.py` | RocketReach provider (stub) |
| `providers/rapidapi_google.py` | RapidAPI / Google provider (stub) |
| `providers/parallel_web.py` | Parallel web provider (stub) |
| `rate_limiter.py` | Redis token-bucket rate limiter |
| `retry.py` | Exponential backoff retry helper |
| `cache.py` | Redis response cache |
| `normalizer.py` | Response normalisation to nrev-lite schema |
| `schemas.py` | Pydantic v2 schemas for execute requests / responses |

**Exports:** router, `ExecuteRequest`, `ExecuteResponse`.

---

## 4. Data Module — `server/data/`

Interactive tables and raw-query access to tenant data.

| File | Responsibility |
|------|---------------|
| `router.py` | API endpoints: `/api/v1/tables`, `/tables/{table}`, `/query` |
| `service.py` | Query execution, table management logic |
| `models.py` | SQLAlchemy models: `Contact`, `Company`, `SearchResult`, `EnrichmentLog` |
| `schemas.py` | Pydantic v2 schemas for table / query requests and responses |

**Exports:** router, ORM models.

---

## 5. Billing Module — `server/billing/`

Credits and payments.

| File | Responsibility |
|------|---------------|
| `router.py` | API endpoints: `/api/v1/credits`, `/credits/history`, `/credits/topup` |
| `service.py` | Credit ledger logic: `check_and_hold`, `confirm_debit`, `release_hold`, `add_credits`, `get_balance`, `get_history` |
| `models.py` | SQLAlchemy models: `CreditLedger`, `CreditBalance`, `Payment` |
| `schemas.py` | Pydantic v2 schemas |
| `stripe_handler.py` | Stripe checkout session creation and webhook handling |

**Exports:** router, `get_balance`, `check_and_hold`.

---

## 6. Vault Module — `server/vault/`

BYOK key management with encryption at rest.

| File | Responsibility |
|------|---------------|
| `router.py` | API endpoints: `/api/v1/keys` (POST, GET, DELETE) |
| `service.py` | Encrypt / decrypt via KMS (prod) or simple encoding (dev) |
| `models.py` | SQLAlchemy model: `TenantKey` |
| `schemas.py` | Pydantic v2 schemas |

**Exports:** router, `TenantKey`.

---

## 7. Dashboard Module — `server/dashboards/`

Dashboard hosting and deployment.

| File | Responsibility |
|------|---------------|
| `router.py` | API endpoints: `/api/v1/dashboards` (CRUD) |
| `service.py` | Build, deploy to S3, manage dashboards |
| `models.py` | SQLAlchemy model: `Dashboard` |
| `schemas.py` | Pydantic v2 schemas |

**Exports:** router, `Dashboard`.

---

## Dependency Graph

```
                    ┌──────────┐
                    │   core   │
                    └────┬─────┘
          ┌──────┬───────┼────────┬──────────┬──────────┐
          ▼      ▼       ▼        ▼          ▼          ▼
       ┌──────┐ ┌─────┐ ┌──────┐ ┌────────┐ ┌───────┐ ┌──────────┐
       │ auth │ │exec │ │ data │ │billing │ │ vault │ │dashboards│
       └──────┘ └─────┘ └──────┘ └────────┘ └───────┘ └──────────┘
```

All arrows point **down** from `core`. No horizontal arrows between feature
modules. The `auth` module's `dependencies.py` (which provides
`get_current_user` / `get_current_tenant`) is an exception — it references
`billing.models.CreditBalance` for the `require_credits` check. This single
cross-module import is acceptable because it is a thin dependency-injection
function, not deep coupling.

---

## Conventions

1. Every module directory contains an `__init__.py`.
2. Each `router.py` defines its own `APIRouter` with the appropriate `prefix`
   and `tags`.
3. Pydantic schemas use **v2** `BaseModel`.
4. ORM models inherit from `server.core.database.Base` (re-exported from
   `server.models.base`).
5. `server/app.py` is the composition root — it imports routers and wires
   middleware from `core`.
