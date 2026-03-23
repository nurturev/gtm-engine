"""Execution orchestration: resolve keys, rate-limit, cache, retry, normalize.

This is the core execution pipeline.  Every provider call flows through:

    resolve_provider → check_cache → rate_limit → retry(provider.execute) → normalize → cache_store

Each step is production-grade:
- **Rate limiter**: Redis token-bucket per provider per tenant (prevents upstream bans)
- **Cache**: Redis response cache with deterministic keys (avoids duplicate API spend)
- **Retry**: Exponential backoff with jitter (handles transient failures gracefully)
- **Normalizer**: Maps provider-specific schemas to the nrev-lite standard schema
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.core.exceptions import ProviderError
from server.vault.models import TenantKey
from server.vault.service import decrypt_key

# Import providers so they register themselves
import server.execution.providers.apollo  # noqa: F401
import server.execution.providers.rocketreach  # noqa: F401
import server.execution.providers.predictleads  # noqa: F401
import server.execution.providers.parallel_web  # noqa: F401
import server.execution.providers.rapidapi_google  # noqa: F401

from server.execution.providers import get_provider, list_providers
from server.execution.retry import retry_with_backoff
from server.execution.normalizer import normalize_person, normalize_company, normalize_predictleads

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singletons for rate limiter and cache (initialized lazily from Redis pool)
# ---------------------------------------------------------------------------

_rate_limiter = None
_response_cache = None


def _get_rate_limiter():
    """Get or create the rate limiter singleton."""
    global _rate_limiter
    if _rate_limiter is None:
        from server.app import redis_pool
        if redis_pool is not None:
            from server.execution.rate_limiter import TokenBucketRateLimiter
            _rate_limiter = TokenBucketRateLimiter(
                redis=redis_pool,
                max_tokens=10,       # 10 requests burst
                refill_rate=2.0,     # 2 tokens/sec = 120/min steady state
                prefix="ratelimit",
            )
    return _rate_limiter


def _get_cache():
    """Get or create the response cache singleton."""
    global _response_cache
    if _response_cache is None:
        from server.app import redis_pool
        if redis_pool is not None:
            from server.execution.cache import ResponseCache
            _response_cache = ResponseCache(
                redis=redis_pool,
                default_ttl=3600,    # 1 hour default
                prefix="cache:exec",
            )
    return _response_cache


# Cache TTLs per operation type (seconds)
CACHE_TTLS: dict[str, int] = {
    "enrich_person": 86400 * 7,     # 7 days — enrichment data changes slowly
    "enrich_company": 86400 * 7,
    "search_people": 3600,          # 1 hour — search results may update
    "search_companies": 3600,
    "search_web": 1800,             # 30 min — web results change fast
    "scrape_page": 3600,            # 1 hour — page content changes slowly
    "crawl_site": 3600,
    "extract_structured": 3600,
    "batch_extract": 3600,
    # PredictLeads signals — jobs refresh every 36h, news more frequently
    "company_jobs": 86400,          # 1 day — jobs refresh every 36h
    "company_technologies": 86400 * 3,  # 3 days — tech stacks change slowly
    "company_news": 3600,           # 1 hour — news events stream in
    "company_financing": 86400,     # 1 day — financing events are infrequent
    "similar_companies": 86400 * 7, # 7 days — similarity scores stable
}

# Default provider for each operation when none is specified
DEFAULT_PROVIDERS: dict[str, str] = {
    "enrich_person": "apollo",
    "enrich_company": "apollo",
    "search_people": "apollo",
    "search_companies": "apollo",
    "bulk_enrich_people": "apollo",
    "bulk_enrich_companies": "apollo",
    # PredictLeads signal operations
    "company_jobs": "predictleads",
    "company_technologies": "predictleads",
    "company_news": "predictleads",
    "company_financing": "predictleads",
    "similar_companies": "predictleads",
    # Web intelligence (Parallel Web Systems — parallel.ai)
    "scrape_page": "parallel_web",
    "crawl_site": "parallel_web",
    "extract_structured": "parallel_web",
    "batch_extract": "parallel_web",
    # Google Search (RapidAPI Real-Time Web Search by OpenWeb Ninja)
    "search_web": "rapidapi_google",
}

# ---------------------------------------------------------------------------
# Credit cost model
# ---------------------------------------------------------------------------
# Base costs per operation (for the simplest case: 1 record, 1 page)
BASE_COSTS: dict[str, float] = {
    "enrich_person": 1.0,
    "enrich_company": 1.0,
    "search_people": 1.0,       # base cost per search page
    "search_companies": 1.0,    # base cost per search page
    "bulk_enrich_people": 1.0,  # per-record in bulk
    "bulk_enrich_companies": 1.0,
    # PredictLeads signals — 1 credit per API call
    "company_jobs": 1.0,
    "company_technologies": 1.0,
    "company_news": 1.0,
    "company_financing": 1.0,
    "similar_companies": 1.0,
    # Web intelligence (Parallel) — 1 credit per API call
    "scrape_page": 1.0,
    "crawl_site": 1.0,         # base cost; actual scales with URLs extracted
    "extract_structured": 1.0,
    "batch_extract": 1.0,      # per-item in batch
    # Google Search (RapidAPI) — 1 credit per query
    "search_web": 1.0,
}

# Search operations: cost scales with results requested
# Formula: base + (per_page / 25) — requesting 100 results costs 4x requesting 25
SEARCH_OPERATIONS = {"search_people", "search_companies"}

# Bulk operations: cost scales with number of records
BULK_OPERATIONS = {"bulk_enrich_people", "bulk_enrich_companies"}

# Legacy flat lookup (used by router for minimum hold estimate)
OPERATION_COSTS = BASE_COSTS


def calculate_cost(operation: str, params: dict[str, Any]) -> float:
    """Calculate the credit cost for an operation based on its parameters.

    Pricing model:
    - Enrichment (single):     1 credit flat
    - Search (per page):       1 credit per 25 results requested
                               e.g., per_page=25 → 1 credit, per_page=100 → 4 credits
                               Pages beyond page 1 cost the same per page
    - Search (bulk queries):   1 credit per query in the queries array
                               e.g., queries=["a","b","c"] → 3 credits
                               Each query is a separate API call to the provider
    - Bulk enrichment:         1 credit per record in the batch
                               e.g., 10 records → 10 credits

    BYOK calls are always free regardless of this calculation.
    Cache hits are always free regardless of this calculation.
    """
    base = BASE_COSTS.get(operation, 1.0)

    if operation in SEARCH_OPERATIONS:
        # Scale with results per page: 1 credit per 25 results
        per_page = int(params.get("per_page") or params.get("limit") or 25)
        per_page = max(1, min(per_page, 100))  # clamp to 1-100
        import math
        page_cost = math.ceil(per_page / 25) * base

        # Bulk queries: each query is a separate API call, charge per query
        queries = params.get("queries")
        if queries and isinstance(queries, list) and len(queries) > 1:
            return page_cost * len(queries)

        return page_cost

    if operation in BULK_OPERATIONS:
        # Scale with batch size
        if operation == "bulk_enrich_people":
            count = len(params.get("details", []))
        else:
            count = len(params.get("domains", []))
        return max(1.0, count * base)

    # Bulk queries on any operation: charge per query
    queries = params.get("queries")
    if queries and isinstance(queries, list) and len(queries) > 1:
        return base * len(queries)

    return base

# Platform API keys loaded from environment (fallback when no BYOK key).
# These are the nrev-lite platform keys — used when a tenant hasn't added their own.
# NEVER logged, NEVER returned to users, NEVER exposed in any response.
_PLATFORM_KEYS: dict[str, str] = {}


def _load_platform_keys() -> None:
    """Load platform API keys from settings (which reads .env + env vars)."""
    from server.core.config import settings
    _key_map = {
        "apollo": settings.APOLLO_API_KEY,
        # Accept both ROCKETREACH_API_KEY and ROCKETREACH_API
        "rocketreach": settings.ROCKETREACH_API_KEY or settings.ROCKETREACH_API,
        "rapidapi": settings.RAPIDAPI_KEY,
        "parallel_web": settings.PARALLEL_KEY,
        "rapidapi_google": settings.X_RAPIDAPI_KEY,
    }
    for provider, val in _key_map.items():
        if val and val.strip():
            _PLATFORM_KEYS[provider] = val.strip()
            logger.info("Platform key loaded for provider: %s", provider)

    # PredictLeads uses dual-key auth: pack token:::key into one string
    pl_token = settings.PREDICTLEADS_API_TOKEN
    pl_key = settings.PREDICTLEADS_API_KEY
    if pl_token and pl_token.strip() and pl_key and pl_key.strip():
        _PLATFORM_KEYS["predictleads"] = f"{pl_token.strip()}:::{pl_key.strip()}"
        logger.info("Platform key loaded for provider: predictleads (dual-key)")


_load_platform_keys()

# Operations that should be normalized
PERSON_OPERATIONS = {"enrich_person", "search_people", "bulk_enrich_people"}
COMPANY_OPERATIONS = {"enrich_company", "search_companies", "bulk_enrich_companies"}
PREDICTLEADS_OPERATIONS = {
    "company_jobs", "company_technologies", "company_news",
    "company_financing", "similar_companies",
}


async def resolve_api_key(
    db: AsyncSession,
    tenant_id: str,
    provider_name: str,
) -> tuple[str, bool]:
    """Look up the API key for a provider.

    Returns (api_key, is_byok). Checks BYOK keys first, then platform keys.
    Raises ProviderError if no key is available.
    """
    # Check BYOK first
    result = await db.execute(
        select(TenantKey).where(
            TenantKey.tenant_id == tenant_id,
            TenantKey.provider == provider_name,
            TenantKey.status == "active",
        )
    )
    byok = result.scalar_one_or_none()
    if byok is not None:
        api_key = decrypt_key(byok.encrypted_key, tenant_id)
        return api_key, True

    # Check platform keys (from environment / AWS Secrets Manager)
    platform_key = _PLATFORM_KEYS.get(provider_name)
    if platform_key:
        logger.info(
            "Using platform key for %s (tenant %s)", provider_name, tenant_id
        )
        return platform_key, False  # is_byok = False → credits will be charged

    raise ProviderError(
        provider_name,
        f"No API key found for provider '{provider_name}'. "
        f"Add one with: nrev-lite keys add {provider_name}",
    )


async def execute_single(
    db: AsyncSession,
    operation: str,
    provider_name: str | None,
    params: dict[str, Any],
    tenant_id: str,
) -> dict[str, Any]:
    """Execute a single enrichment or search operation against a provider.

    Full pipeline:
    1. Resolve which provider to use
    2. Check the response cache (return immediately on hit)
    3. Check the rate limiter (reject if over limit)
    4. Look up the API key (BYOK or platform)
    5. Call the provider with retry + exponential backoff
    6. Normalize the response to nrev-lite schema
    7. Store in cache for future requests
    8. Return the result

    Credit hold/debit is managed by the caller (router).
    """
    # ── Step 1: Resolve provider ──────────────────────────────────────────
    if not provider_name:
        provider_name = DEFAULT_PROVIDERS.get(operation)
    if not provider_name:
        raise ProviderError(
            "unknown",
            f"No default provider for operation '{operation}'. "
            f"Available providers: {', '.join(list_providers())}",
        )

    provider_cls = get_provider(provider_name)
    if provider_cls is None:
        raise ProviderError(
            provider_name,
            f"Provider '{provider_name}' is not registered. "
            f"Available: {', '.join(list_providers())}",
        )

    if operation not in provider_cls.supported_operations:
        raise ProviderError(
            provider_name,
            f"Provider '{provider_name}' does not support '{operation}'. "
            f"Supported: {', '.join(provider_cls.supported_operations)}",
        )

    # ── Step 2: Check cache ───────────────────────────────────────────────
    cache = _get_cache()
    if cache is not None:
        try:
            cached = await cache.get(tenant_id, operation, params)
            if cached is not None:
                logger.info(
                    "Cache HIT for %s/%s tenant=%s",
                    provider_name, operation, tenant_id,
                )
                return {
                    "provider": provider_name,
                    "operation": operation,
                    "is_byok": cached.get("is_byok", True),
                    "cached": True,
                    "data": cached.get("data", cached),
                }
        except Exception:
            logger.warning("Cache read failed, proceeding without cache", exc_info=True)

    # ── Step 3: Rate limit ────────────────────────────────────────────────
    limiter = _get_rate_limiter()
    if limiter is not None:
        try:
            allowed = await limiter.acquire(provider_name, tenant_id)
            if not allowed:
                raise ProviderError(
                    provider_name,
                    f"Rate limit exceeded for provider '{provider_name}'. "
                    f"Please wait a moment and try again.",
                    status_code=429,
                )
        except ProviderError:
            raise
        except Exception:
            logger.warning("Rate limiter check failed, proceeding", exc_info=True)

    # ── Step 4: Resolve API key ───────────────────────────────────────────
    api_key, is_byok = await resolve_api_key(db, tenant_id, provider_name)

    # ── Step 5: Execute with retry ────────────────────────────────────────
    provider = provider_cls()
    logger.info(
        "Executing %s via %s for tenant %s (byok=%s)",
        operation, provider_name, tenant_id, is_byok,
    )

    raw_result = await retry_with_backoff(
        provider.execute,
        operation,
        params,
        api_key,
        max_retries=3,
        base_delay=1.0,
        max_delay=30.0,
        jitter=True,
        retryable_exceptions=(ProviderError,),
    )

    # ── Step 6: Normalize ─────────────────────────────────────────────────
    if operation in PERSON_OPERATIONS:
        normalized = normalize_person(raw_result, provider_name)
    elif operation in COMPANY_OPERATIONS:
        normalized = normalize_company(raw_result, provider_name)
    elif operation in PREDICTLEADS_OPERATIONS:
        normalized = normalize_predictleads(raw_result, operation)
    else:
        normalized = raw_result

    # ── Step 7: Store in cache ────────────────────────────────────────────
    cache_payload = {"data": normalized, "is_byok": is_byok}
    if cache is not None:
        try:
            ttl = CACHE_TTLS.get(operation, 3600)
            await cache.set(tenant_id, operation, params, cache_payload, ttl=ttl)
            logger.debug("Cached %s/%s for %ds", provider_name, operation, ttl)
        except Exception:
            logger.warning("Cache write failed", exc_info=True)

    # ── Step 8: Return ────────────────────────────────────────────────────
    return {
        "provider": provider_name,
        "operation": operation,
        "is_byok": is_byok,
        "cached": False,
        "actual_cost": calculate_cost(operation, params),
        "data": normalized,
    }
