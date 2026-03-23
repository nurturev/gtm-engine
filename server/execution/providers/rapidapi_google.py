"""RapidAPI Real-Time Web Search provider by OpenWeb Ninja (letscrape).

Provides fast Google search results via RapidAPI.

Supported operations:
    - search_web:   Google web search (organic results, knowledge graph, PAA)

API docs: https://rapidapi.com/letscrape-6bRBa3QguO5/api/real-time-web-search
Host: real-time-web-search.p.rapidapi.com

Authentication:
    - X-RapidAPI-Key: stored as X_RAPIDAPI_KEY
    - X-RapidAPI-Host: real-time-web-search.p.rapidapi.com

Rate limits (by tier):
    - Free:   1 req/sec,  100 req/month
    - Pro:    10 req/sec,  10K req/month   ($25/mo, overage $0.003/req)
    - Ultra:  20 req/sec,  50K req/month   ($75/mo, overage $0.002/req)
    - Mega:   30 req/sec,  200K req/month  ($150/mo, overage $0.001/req)

Response headers for adaptive throttling:
    - x-ratelimit-remaining: requests left in current window
    - x-ratelimit-reset: timestamp when limit resets

Quirks:
    - Single endpoint: GET /search — no pagination (set num up to 300)
    - No separate news/images/maps endpoints (those are separate RapidAPI products)
    - Google operators work in q: site:, filetype:, inurl:, intitle:, -keyword
    - Failed requests still consume quota
    - Response wraps results in {"status": "OK", "data": [...]}
    - num max is ambiguous: marketing says 500, MCP wrapper caps at 300
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from server.core.exceptions import ProviderError
from server.execution.providers.base import BaseProvider
from server.execution.providers import register_provider

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "real-time-web-search.p.rapidapi.com"
RAPIDAPI_BASE = f"https://{RAPIDAPI_HOST}"

_REQUEST_TIMEOUT = 30.0
_BULK_CONCURRENCY = 10      # max concurrent requests for bulk search
_BULK_DELAY = 0.1           # 100ms delay between bulk requests


class RapidAPIGoogleProvider(BaseProvider):
    """Google Search via RapidAPI Real-Time Web Search.

    High-performance Google SERP API for GTM research:
    company intel, hiring signals, funding news, competitive analysis.
    """

    name = "rapidapi_google"
    supported_operations = [
        "search_web",
    ]

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": RAPIDAPI_HOST,
        }

    async def execute(
        self,
        operation: str,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        """Execute a RapidAPI Google search operation."""
        if operation == "search_web":
            return await self._search_web(params, api_key)
        raise ProviderError(self.name, f"Unknown operation: {operation}")

    async def health_check(self, api_key: str) -> bool:
        """Check if the API is reachable and key is valid."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{RAPIDAPI_BASE}/search",
                    headers=self._headers(api_key),
                    params={"q": "test", "num": "1"},
                )
                return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # search_web — GET /search
    # ------------------------------------------------------------------

    async def _search_web(
        self, params: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        """Google web search via RapidAPI.

        Params:
            q / query (str):        Required. Search query.
                                    Supports Google operators: site:, filetype:,
                                    inurl:, intitle:, -keyword
            num (int):              Results to return (1-300). Default: 10.
            gl (str):               Country code (us, in, gb, de). Default: auto.
            hl (str):               Language code (en, hi, fr). Default: auto.
            lr (str):               Language restrict (e.g. lang_en).
            tbs (str):              Time filter: qdr:h (hour), qdr:d (day),
                                    qdr:w (week), qdr:m (month), qdr:y (year).
            safe (str):             Safe search: "active" or "off". Default: "off".

            # Convenience shortcuts:
            site (str):             Restrict to domain (adds site: operator).
            time (str):             Friendly time filter: "hour", "day", "week",
                                    "month", "year" → mapped to tbs.

            # Bulk search:
            queries (list[str]):    Multiple queries to run concurrently.
                                    Returns results grouped by query.
        """
        # Check for bulk search
        queries = params.get("queries")
        if queries and isinstance(queries, list) and len(queries) > 1:
            return await self._bulk_search(queries, params, api_key)

        # Single search
        query = params.get("q") or params.get("query")
        if not query:
            raise ProviderError(self.name, "Missing required parameter: q (search query)")

        # Convenience: site restriction
        if params.get("site"):
            site = params["site"].replace("https://", "").replace("http://", "").rstrip("/")
            query = f"site:{site} {query}"

        # Build query params
        qparams: dict[str, str] = {"q": query}

        # Number of results (cap at 300)
        num = min(int(params.get("num", 10)), 300)
        qparams["num"] = str(num)

        if params.get("gl"):
            qparams["gl"] = params["gl"]
        if params.get("hl"):
            qparams["hl"] = params["hl"]
        if params.get("lr"):
            qparams["lr"] = params["lr"]

        # Time filter — accept both raw tbs and friendly names
        tbs = params.get("tbs") or params.get("time")
        if tbs:
            time_map = {
                "hour": "qdr:h", "day": "qdr:d", "week": "qdr:w",
                "month": "qdr:m", "year": "qdr:y",
            }
            tbs = time_map.get(tbs, tbs)
            qparams["tbs"] = tbs

        if params.get("safe"):
            qparams["safe"] = params["safe"]

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(
                f"{RAPIDAPI_BASE}/search",
                headers=self._headers(api_key),
                params=qparams,
            )

        self._check_response(resp, query)

        data = resp.json()
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining:
            logger.debug("RapidAPI rate limit remaining: %s", remaining)

        return self._normalize_search(data, query)

    # ------------------------------------------------------------------
    # Bulk search — concurrent queries with rate limiting
    # ------------------------------------------------------------------

    async def _bulk_search(
        self,
        queries: list[str],
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        """Run multiple search queries concurrently with rate limiting.

        Uses a semaphore to cap concurrency and a small delay between
        requests to stay within rate limits.
        """
        logger.info("Bulk search: %d queries", len(queries))
        semaphore = asyncio.Semaphore(_BULK_CONCURRENCY)

        async def _run_query(query: str, idx: int) -> dict[str, Any]:
            async with semaphore:
                # Small delay to avoid rate limit bursts
                if idx > 0:
                    await asyncio.sleep(_BULK_DELAY * idx)
                single_params = {**params, "q": query}
                single_params.pop("queries", None)
                try:
                    return await self._search_web(single_params, api_key)
                except ProviderError as e:
                    return {
                        "query": query,
                        "error": str(e),
                        "results": [],
                        "total": 0,
                    }

        results = await asyncio.gather(
            *[_run_query(q, i) for i, q in enumerate(queries)],
        )

        # Flatten all results into a single unified response
        # so the client sees the same format as a single search
        all_results: list[dict[str, Any]] = []
        for search in results:
            for r in search.get("results", []):
                r["_query"] = search.get("query", "")
                all_results.append(r)

        return {
            "query": f"[bulk: {len(queries)} queries]",
            "results": all_results,
            "total": len(all_results),
            "bulk": True,
            "total_queries": len(queries),
            "searches": list(results),  # keep per-query breakdown too
        }

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _check_response(self, resp: httpx.Response, query: str) -> None:
        """Check HTTP response and raise ProviderError on failure."""
        if resp.status_code == 429:
            reset = resp.headers.get("x-ratelimit-reset", "unknown")
            raise ProviderError(
                self.name,
                f"RapidAPI rate limit exceeded (resets at {reset}). "
                "Upgrade your RapidAPI plan or wait.",
                status_code=429,
            )
        if resp.status_code == 401 or resp.status_code == 403:
            raise ProviderError(
                self.name,
                "Invalid or unauthorized RapidAPI key. "
                "Check your X_RAPIDAPI_KEY.",
                status_code=resp.status_code,
            )
        if resp.status_code != 200:
            raise ProviderError(
                self.name,
                f"RapidAPI search failed for '{query[:100]}': "
                f"{resp.status_code} {resp.text[:500]}",
                status_code=resp.status_code,
            )

        # Check response-level status
        try:
            data = resp.json()
            if data.get("status") == "ERROR":
                error = data.get("error", {})
                raise ProviderError(
                    self.name,
                    f"RapidAPI search error: {error.get('message', 'unknown')}",
                    status_code=error.get("code", 500),
                )
        except ProviderError:
            raise
        except Exception:
            pass  # If JSON parsing fails, let the normalizer handle it

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize_search(
        self, raw: dict[str, Any], query: str
    ) -> dict[str, Any]:
        """Normalize RapidAPI search response to nrev-lite schema.

        Handles both API response formats:
        - Old: {"data": [{"title": "...", "url": "...", ...}]}
        - New: {"data": {"organic_results": [{"title": "...", "url": "...", ...}], "total_organic_results": N}}
        """
        data = raw.get("data", [])

        # Handle new format: data is a dict with organic_results
        if isinstance(data, dict):
            results_raw = data.get("organic_results", [])
        elif isinstance(data, list):
            results_raw = data
        else:
            results_raw = []

        results = []
        for i, item in enumerate(results_raw):
            if not isinstance(item, dict):
                continue
            result: dict[str, Any] = {
                "position": i + 1,
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("snippet"),
                "source": item.get("source"),
                "date": item.get("date"),
            }
            results.append({k: v for k, v in result.items() if v is not None})

        normalized: dict[str, Any] = {
            "query": query,
            "results": results,
            "total": len(results),
            "request_id": raw.get("request_id"),
        }

        # Include any additional SERP features if present
        # (knowledge graph, people also ask, related searches, etc.)
        # Check both top-level and nested under data (new API format)
        feature_sources = [raw]
        if isinstance(data, dict):
            feature_sources.append(data)

        for source in feature_sources:
            for key in ("knowledgeGraph", "knowledge_graph", "peopleAlsoAsk",
                         "people_also_ask", "relatedSearches", "related_searches"):
                if source.get(key):
                    clean_key = key.replace("Also", "_also_").lower()
                    if "knowledge" in clean_key:
                        clean_key = "knowledge_graph"
                    elif "people" in clean_key:
                        clean_key = "people_also_ask"
                    elif "related" in clean_key:
                        clean_key = "related_searches"
                    if clean_key not in normalized:
                        normalized[clean_key] = source[key]

        return normalized


register_provider("rapidapi_google", RapidAPIGoogleProvider)
