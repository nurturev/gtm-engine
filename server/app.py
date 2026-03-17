"""Main FastAPI application for the nrv API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.core.config import settings
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

    yield

    # Shutdown
    if redis_pool:
        await redis_pool.aclose()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="nrv API",
    version="0.1.0",
    description="Agent-native GTM execution platform",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# CORS - allow all origins in development, restrict in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.ENVIRONMENT == "development" else [],
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
from server.billing.router import router as credits_router  # noqa: E402
from server.console.router import router as console_router  # noqa: E402
from server.dashboards.router import router as dashboards_router  # noqa: E402
from server.data.dataset_router import router as datasets_router  # noqa: E402
from server.data.router import router as tables_router  # noqa: E402
from server.execution.router import router as execute_router  # noqa: E402
from server.execution.runs_router import router as runs_router  # noqa: E402
from server.execution.schedule_router import router as schedules_router  # noqa: E402
from server.vault.router import router as keys_router  # noqa: E402

app.include_router(auth_router)
app.include_router(execute_router)
app.include_router(runs_router)
app.include_router(tables_router)
app.include_router(keys_router)
app.include_router(credits_router)
app.include_router(dashboards_router)
app.include_router(datasets_router)
app.include_router(schedules_router)
app.include_router(console_router)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok", "version": "0.1.0"}
