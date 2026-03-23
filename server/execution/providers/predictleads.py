"""PredictLeads provider — company signals, job openings, technology, news.

PredictLeads provides company intelligence data:
- Company profiles (firmographics, location, subsidiaries)
- Job openings (220M+ historical, 8.5M active)
- Technology detections (46K technologies tracked, 980M records)
- News events (9M+ structured events from 20M+ sources)
- Financing events (funding rounds, investments)
- Similar companies (scored 0.0-1.0, ranked 1-50)
- Company connections (business relationships)
- Website evolution tracking

API quirks:
- Dual-key auth: requires BOTH api_token AND api_key on every request
- Headers: X-Api-Token + X-Api-Key (or query params)
- Base URL: https://predictleads.com/api/v3/
- Response format: JSON:API spec (data[].attributes, included[], meta)
- Company lookups are by DOMAIN (not name) — domain is the primary key
- meta.count only included when page param is passed
- Rate limits are plan-dependent (check subscription endpoint)
- Data refreshed every 36 hours for jobs, varies for others
- IDs are UUIDs
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from server.core.exceptions import ProviderError
from server.execution.providers import register_provider
from server.execution.providers.base import BaseProvider

logger = logging.getLogger(__name__)

# PredictLeads uses dual-key auth — we pack both into a single string
# separated by ":::" since the vault stores one key per provider.
# Format: "{api_token}:::{api_key}"
# Alternative: Use the api_token as the main key and api_key from settings.
KEY_SEPARATOR = ":::"


def _clean_domain(raw: str) -> str:
    """Normalize a domain input to bare domain (e.g. 'example.com')."""
    if not raw:
        return raw
    raw = raw.strip().lower()
    if "://" in raw:
        parsed = urlparse(raw)
        raw = parsed.netloc or parsed.path
    raw = raw.split("/")[0]
    if raw.startswith("www."):
        raw = raw[4:]
    return raw.rstrip(".")


def _parse_dual_key(api_key: str) -> tuple[str, str]:
    """Parse the dual-key string into (api_token, api_key).

    PredictLeads requires both. We store them as 'token:::key' in the vault.
    If no separator found, treat the whole string as api_token and
    try loading api_key from settings.
    """
    if KEY_SEPARATOR in api_key:
        parts = api_key.split(KEY_SEPARATOR, 1)
        return parts[0].strip(), parts[1].strip()

    # Fallback: single key provided, try to get the other from settings
    from server.core.config import settings
    api_key_val = getattr(settings, "PREDICTLEADS_API_KEY", None)
    if api_key_val:
        return api_key.strip(), api_key_val.strip()

    raise ProviderError(
        "predictleads",
        "PredictLeads requires both API token and API key. "
        "Store as: nrev-lite keys add predictleads --key 'TOKEN:::KEY'",
    )


class PredictLeadsProvider(BaseProvider):
    """PredictLeads — company intelligence and signals provider."""

    name = "predictleads"
    supported_operations = [
        "enrich_company",        # Company profile by domain
        "company_jobs",          # Job openings for a company
        "company_technologies",  # Technologies detected at a company
        "company_news",          # News events about a company
        "company_financing",     # Financing/funding events
        "similar_companies",     # Find similar companies
    ]

    BASE_URL = "https://predictleads.com/api/v3"

    # Operation → endpoint mapping
    _OPERATION_MAP: dict[str, dict[str, Any]] = {
        "enrich_company": {
            "method": "GET",
            "path": "/companies/{domain}",
        },
        "company_jobs": {
            "method": "GET",
            "path": "/companies/{domain}/job_openings",
        },
        "company_technologies": {
            "method": "GET",
            "path": "/companies/{domain}/technology_detections",
        },
        "company_news": {
            "method": "GET",
            "path": "/companies/{domain}/news_events",
        },
        "company_financing": {
            "method": "GET",
            "path": "/companies/{domain}/financing_events",
        },
        "similar_companies": {
            "method": "GET",
            "path": "/companies/{domain}/similar_companies",
        },
    }

    async def execute(
        self,
        operation: str,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        """Execute a PredictLeads operation."""
        if operation not in self._OPERATION_MAP:
            raise ProviderError(
                self.name,
                f"Unsupported operation: {operation}. "
                f"Supported: {', '.join(self.supported_operations)}",
            )

        # Parse dual keys
        api_token, api_key_val = _parse_dual_key(api_key)

        # Get domain (required for all operations)
        domain = params.get("domain") or params.get("company_domain") or ""
        if not domain:
            raise ProviderError(
                self.name,
                "PredictLeads requires a 'domain' parameter (e.g. 'example.com')",
                status_code=422,
            )
        domain = _clean_domain(domain)

        # Build endpoint
        op_config = self._OPERATION_MAP[operation]
        path = op_config["path"].format(domain=domain)
        url = f"{self.BASE_URL}{path}"

        # Build query params
        query_params: dict[str, Any] = {}

        # Pagination
        page = params.get("page")
        per_page = params.get("per_page") or params.get("limit")
        if page is not None:
            query_params["page"] = int(page)
        if per_page is not None:
            query_params["per_page"] = int(per_page)

        # Time filtering for incremental fetches
        if params.get("found_at_from"):
            query_params["found_at_from"] = params["found_at_from"]

        # Job-specific filters
        if operation == "company_jobs":
            for key in ("title", "categories", "location_country", "active_only"):
                if params.get(key):
                    query_params[key] = params[key]

        # News-specific filters
        if operation == "company_news":
            for key in ("category", "found_at_from", "found_at_to"):
                if params.get(key):
                    query_params[key] = params[key]

        # Auth headers (dual-key)
        headers = {
            "X-Api-Token": api_token,
            "X-Api-Key": api_key_val,
            "Accept": "application/json",
        }

        logger.info(
            "PredictLeads %s: %s %s (domain=%s)",
            operation, op_config["method"], path, domain,
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=op_config["method"],
                url=url,
                headers=headers,
                params=query_params,
            )

        # Handle errors
        if response.status_code == 404:
            return {"match_found": False, "domain": domain}

        if response.status_code == 401:
            raise ProviderError(
                self.name,
                "PredictLeads authentication failed. Check both API token and API key.",
                status_code=401,
            )

        if response.status_code == 403:
            raise ProviderError(
                self.name,
                "PredictLeads access denied. Your plan may not include this dataset.",
                status_code=403,
            )

        if response.status_code == 429:
            raise ProviderError(
                self.name,
                "PredictLeads rate limit exceeded. Wait and retry.",
                status_code=429,
            )

        if response.status_code >= 500:
            raise ProviderError(
                self.name,
                f"PredictLeads server error ({response.status_code})",
                status_code=response.status_code,
            )

        if response.status_code not in (200, 201):
            raise ProviderError(
                self.name,
                f"PredictLeads returned {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )

        raw = response.json()

        # Transform JSON:API format to flat format for normalizer
        return self._flatten_jsonapi(raw, operation, domain)

    def _flatten_jsonapi(
        self, raw: dict[str, Any], operation: str, domain: str
    ) -> dict[str, Any]:
        """Convert JSON:API response to a flat dict for the normalizer.

        PredictLeads returns JSON:API format:
        {
            "data": [{"id": "...", "type": "...", "attributes": {...}}],
            "included": [...],
            "meta": {"count": N}
        }
        """
        data = raw.get("data")
        meta = raw.get("meta", {})

        if data is None:
            return {"match_found": False, "domain": domain}

        # Single object response (company profile)
        if isinstance(data, dict):
            result = data.get("attributes", {})
            result["id"] = data.get("id")
            result["type"] = data.get("type")
            result["_domain"] = domain

            # Include related objects
            included = raw.get("included", [])
            if included:
                result["_related"] = [
                    {**item.get("attributes", {}), "id": item.get("id"), "type": item.get("type")}
                    for item in included
                ]
            return result

        # Array response
        if isinstance(data, list):
            items = []
            for item in data:
                flat = item.get("attributes", {})
                flat["id"] = item.get("id")
                flat["type"] = item.get("type")
                items.append(flat)

            # For enrich_company, extract the single company as a flat record
            if operation == "enrich_company" and len(items) == 1:
                result = items[0]
                result["_domain"] = domain
                # Include related objects (parent/subsidiary companies)
                included = raw.get("included", [])
                if included:
                    result["_related"] = [
                        {**i.get("attributes", {}), "id": i.get("id"), "type": i.get("type")}
                        for i in included
                    ]
                return result

            result: dict[str, Any] = {
                "domain": domain,
                "items": items,
                "count": meta.get("count", len(items)),
            }

            # Include pagination info
            if "page" in meta:
                result["page"] = meta["page"]
            if "per_page" in meta:
                result["per_page"] = meta["per_page"]

            return result

        return {"raw": raw, "domain": domain}

    async def health_check(self, api_key: str) -> bool:
        """Check if PredictLeads credentials are valid."""
        try:
            api_token, api_key_val = _parse_dual_key(api_key)
            headers = {
                "X-Api-Token": api_token,
                "X-Api-Key": api_key_val,
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/api_subscription",
                    headers=headers,
                )
            return resp.status_code == 200
        except Exception:
            return False


# Register the provider
register_provider("predictleads", PredictLeadsProvider)
