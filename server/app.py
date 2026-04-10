"""Main FastAPI application for the nrev-lite API."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.core.config import settings

# Configure app-level logging for dev/local environments.
#
# Why this is needed: uvicorn configures its own loggers (`uvicorn`,
# `uvicorn.error`, `uvicorn.access`) but does NOT touch the root logger or
# our `server.*` loggers. Python's root logger defaults to WARNING, so every
# `logger.info(...)` we write in our app modules is silently dropped.
#
# `logging.basicConfig()` is unreliable here — if anything in the import
# chain has already attached a handler to the root logger, basicConfig is a
# no-op. So we explicitly attach our own StreamHandler and set levels.
#
# Gated on ENVIRONMENT so prod logging stays under whatever centralized
# config we ship there (currently nothing special — the dev config is fine
# for prod too, but we keep the gate so we can swap in JSON formatters etc).
print(">>> server.app module loaded", flush=True)
if settings.ENVIRONMENT in ("development", "local"):
    _log_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    _log_handler = logging.StreamHandler()
    _log_handler.setFormatter(_log_formatter)

    _root_logger = logging.getLogger()
    _root_logger.setLevel(logging.INFO)
    # Avoid duplicate handlers on uvicorn --reload
    if not any(getattr(h, "_nrev_dev_handler", False) for h in _root_logger.handlers):
        _log_handler._nrev_dev_handler = True  # type: ignore[attr-defined]
        _root_logger.addHandler(_log_handler)

    # Force our server.* loggers to INFO and propagate to root
    _server_logger = logging.getLogger("server")
    _server_logger.setLevel(logging.INFO)
    _server_logger.propagate = True

    # Quiet noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
from server.core.database import engine
from server.core.middleware import request_id_middleware, tenant_context_middleware

# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown
# ---------------------------------------------------------------------------

redis_pool: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown resources.

    On startup: verify the database connection pool and connect to Redis.
    On shutdown: dispose of the engine and close the Redis connection.
    """
    global redis_pool

    # Startup
    # Verify DB connectivity (creates the connection pool)
    async with engine.begin() as conn:
        await conn.execute(__import__("sqlalchemy").text("SELECT 1"))

    # Connect to Redis
    redis_pool = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    await redis_pool.ping()

    # Load dynamic search patterns from DB into memory cache
    try:
        from sqlalchemy import select as sa_select
        from server.execution.learning_models import DynamicKnowledge
        from server.execution.search_patterns import load_dynamic_patterns
        from server.core.database import async_session_factory

        async with async_session_factory() as db:
            result = await db.execute(
                sa_select(DynamicKnowledge).where(
                    DynamicKnowledge.category == "search_pattern",
                    DynamicKnowledge.enabled == True,  # noqa: E712
                )
            )
            rows = result.scalars().all()
            patterns = {row.key: row.knowledge for row in rows}
            load_dynamic_patterns(patterns)
    except Exception:
        pass  # Table may not exist yet during initial setup

    yield

    # Shutdown
    if redis_pool:
        await redis_pool.aclose()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="nrev-lite API",
    version="0.1.0",
    description="Agent-native GTM execution platform",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# CORS - allow all origins in development, restrict to configured origins in production
if settings.ENVIRONMENT == "development":
    _cors_origins: list[str] = ["*"]
else:
    _cors_origins = [
        o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(request_id_middleware)
app.middleware("http")(tenant_context_middleware)

# Run step logging — records every MCP tool call for workflow tracking
from server.execution.run_logger import RunStepMiddleware  # noqa: E402

app.add_middleware(RunStepMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from server.auth.router import router as auth_router  # noqa: E402
from server.auth.tenant_router import router as tenant_router  # noqa: E402
from server.billing.router import router as credits_router  # noqa: E402
from server.console.router import router as console_router  # noqa: E402
from server.dashboards.router import router as dashboards_router  # noqa: E402
from server.data.dataset_router import router as datasets_router  # noqa: E402
from server.data.router import router as tables_router  # noqa: E402
from server.execution.router import router as execute_router  # noqa: E402
from server.execution.runs_router import router as runs_router  # noqa: E402
from server.execution.schedule_router import router as schedules_router  # noqa: E402
from server.feedback.router import router as feedback_router  # noqa: E402
from server.vault.router import router as keys_router  # noqa: E402
from server.apps.router import router as apps_router  # noqa: E402
from server.execution.script_router import router as scripts_router  # noqa: E402
from server.execution.learning_router import router as learning_router  # noqa: E402
from server.connections.models import (
    UserConnection,
)  # noqa: E402, F401 — register model
from server.admin.router import router as admin_router  # noqa: E402

app.include_router(auth_router)
app.include_router(tenant_router)
app.include_router(execute_router)
app.include_router(runs_router)
app.include_router(tables_router)
app.include_router(keys_router)
app.include_router(credits_router)
app.include_router(dashboards_router)
app.include_router(datasets_router)
app.include_router(schedules_router)
app.include_router(scripts_router)
app.include_router(learning_router)
app.include_router(feedback_router)
app.include_router(admin_router)
app.include_router(apps_router)
app.include_router(console_router)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok", "version": "0.1.0"}
