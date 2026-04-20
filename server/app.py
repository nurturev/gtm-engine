"""Main FastAPI application for the nrev-lite API."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.core.config import settings

# Configure app-level logging. Uvicorn configures its own loggers but leaves
# the root logger at WARNING, which would silently drop every `logger.info(...)`
# from our `server.*` modules. We attach a JSON StreamHandler so logs are
# structured for aggregators (CloudWatch, Loki, Datadog) in every environment.
_STD_LOGRECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Minimal base payload — always present even if enrichment below fails.
        try:
            timestamp = (
                datetime.fromtimestamp(record.created, tz=timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        except Exception:
            timestamp = ""
        payload: dict[str, object] = {
            "timestamp": timestamp,
            "level": getattr(record, "levelname", "INFO"),
            "logger": getattr(record, "name", ""),
            "message": record.getMessage() if record.args else str(record.msg),
        }

        # Per-request identifiers. request_id is the primary tracer — if the
        # ContextVar lookup fails for any reason we still try to surface it
        # from the LogRecord's extras below, so it's never silently lost.
        try:
            from server.core.middleware import (
                agent_type_var,
                internal_service_var,
                request_id_var,
                tenant_id_var,
                thread_id_var,
                user_id_var,
                workflow_id_var,
            )
            for key, var in (
                ("request_id", request_id_var),
                ("tenant_id", tenant_id_var),
                ("user_id", user_id_var),
                ("internal_service", internal_service_var),
                ("agent_type", agent_type_var),
                ("thread_id", thread_id_var),
                ("workflow_id", workflow_id_var),
            ):
                try:
                    value = var.get()
                except Exception:
                    continue
                if value is None or value == "":
                    continue
                payload[key] = value
        except Exception:
            pass

        try:
            for key, value in record.__dict__.items():
                if key in _STD_LOGRECORD_ATTRS or key.startswith("_"):
                    continue
                payload.setdefault(key, value)
        except Exception:
            pass

        if record.exc_info:
            try:
                payload["exception"] = self.formatException(record.exc_info)
            except Exception:
                pass
        if record.stack_info:
            try:
                payload["stack"] = self.formatStack(record.stack_info)
            except Exception:
                pass

        try:
            return json.dumps(payload, default=str)
        except Exception:
            # Last-resort fallback: never let a log line crash the handler.
            safe = {
                "timestamp": payload.get("timestamp", ""),
                "level": payload.get("level", "ERROR"),
                "logger": payload.get("logger", ""),
                "message": str(payload.get("message", ""))[:1000],
                "format_error": "json_serialization_failed",
            }
            if (rid := payload.get("request_id")) is not None:
                safe["request_id"] = str(rid)
            return json.dumps(safe, default=str)


print(">>> server.app module loaded", flush=True)
_log_formatter = _JsonFormatter()
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(_log_formatter)

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
# Avoid duplicate handlers on uvicorn --reload
if not any(getattr(h, "_nrev_dev_handler", False) for h in _root_logger.handlers):
    _log_handler._nrev_dev_handler = True  # type: ignore[attr-defined]
    _root_logger.addHandler(_log_handler)

_server_logger = logging.getLogger("server")
_server_logger.setLevel(logging.INFO)
_server_logger.propagate = True

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

    # Load operation credit costs into memory cache
    try:
        from server.billing.cost_config_service import load_cost_cache
        from server.core.database import async_session_factory

        async with async_session_factory() as db:
            await load_cost_cache(db)
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
    version="2.0.0",
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
from server.admin.cost_router import router as cost_admin_router  # noqa: E402

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
app.include_router(cost_admin_router)
app.include_router(apps_router)
app.include_router(console_router)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok", "version": "0.1.0"}
