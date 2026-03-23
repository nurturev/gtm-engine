"""Apollo.io provider — full API implementation.

Supported operations:
    - enrich_person: Enrich a person by email, name+domain, or LinkedIn
    - enrich_company: Enrich a company by domain
    - search_people: Search for people by title, company, location, etc.
    - search_companies: Search for companies by industry, size, etc.
    - bulk_enrich_people: Enrich up to 10 people in one call
    - bulk_enrich_companies: Enrich up to 10 companies in one call

Apollo API quirks handled here:
    - Domain format MUST be "example.com" — never "https://www.example.com"
    - Array parameters must be actual arrays, not strings
    - People Search returns obfuscated data (no emails) — needs separate enrichment
    - 200 with empty data is normal (no match found)
    - reveal_personal_emails and reveal_phone_number default to false
    - Rate limit headers: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
    - Organization enrichment is GET (not POST like the others)
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from server.core.exceptions import ProviderError
from server.execution.providers.base import BaseProvider
from server.execution.providers import register_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain / input sanitisation
# ---------------------------------------------------------------------------

_PROTOCOL_RE = re.compile(r"^https?://", re.IGNORECASE)


def clean_domain(raw: str) -> str:
    """Normalize a domain to the format Apollo expects: 'example.com'.

    Handles all common user inputs:
        https://www.example.com/path  ->  example.com
        http://example.com/           ->  example.com
        www.example.com               ->  example.com
        example.com/                  ->  example.com
        EXAMPLE.COM                   ->  example.com
    """
    if not raw or not raw.strip():
        return raw

    d = raw.strip().lower()

    # If it looks like a URL, parse it properly
    if _PROTOCOL_RE.match(d):
        parsed = urlparse(d)
        d = parsed.hostname or d
    else:
        # Strip any trailing path
        d = d.split("/")[0]

    # Remove www. prefix
    if d.startswith("www."):
        d = d[4:]

    # Remove trailing dots
    d = d.rstrip(".")

    return d


def clean_domains(raw: Any) -> list[str]:
    """Normalize a domain input to a list of clean domains.

    Handles:
        "example.com"                     -> ["example.com"]
        ["example.com", "test.com"]       -> ["example.com", "test.com"]
        "example.com\\ntest.com"           -> ["example.com", "test.com"]
        "example.com, test.com"           -> ["example.com", "test.com"]
    """
    if isinstance(raw, list):
        return [clean_domain(d) for d in raw if d]

    if isinstance(raw, str):
        # Handle newline-separated (Apollo's format) or comma-separated
        parts = re.split(r"[,\n]+", raw)
        return [clean_domain(d) for d in parts if d.strip()]

    return []


def ensure_list(val: Any) -> list:
    """Ensure a value is a list — Apollo rejects strings where it expects arrays."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [v.strip() for v in val.split(",") if v.strip()]
    return [val]


# ---------------------------------------------------------------------------
# Parameter preparation per operation
# ---------------------------------------------------------------------------


def _prepare_enrich_person(params: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for POST /api/v1/people/match.

    Accepted input params:
        email, first_name, last_name, name, domain, organization_name,
        linkedin_url, id, reveal_personal_emails, reveal_phone_number
    """
    p: dict[str, Any] = {}

    # Identity fields
    if params.get("email"):
        p["email"] = params["email"].strip().lower()
    if params.get("first_name"):
        p["first_name"] = params["first_name"].strip()
    if params.get("last_name"):
        p["last_name"] = params["last_name"].strip()
    if params.get("name"):
        # Split "First Last" into first_name + last_name if not already set
        parts = params["name"].strip().split(None, 1)
        if not p.get("first_name") and len(parts) >= 1:
            p["first_name"] = parts[0]
        if not p.get("last_name") and len(parts) >= 2:
            p["last_name"] = parts[1]
    if params.get("linkedin_url") or params.get("linkedin"):
        p["linkedin_url"] = (params.get("linkedin_url") or params.get("linkedin")).strip()
    if params.get("id"):
        p["id"] = params["id"]

    # Company context (critical: domain must be clean)
    if params.get("domain"):
        p["domain"] = clean_domain(params["domain"])
    if params.get("organization_name") or params.get("company"):
        p["organization_name"] = (
            params.get("organization_name") or params.get("company")
        ).strip()

    # Enrichment options
    if params.get("reveal_personal_emails"):
        p["reveal_personal_emails"] = True
    if params.get("reveal_phone_number") or params.get("reveal_phone"):
        p["reveal_phone_number"] = True

    return p


def _prepare_enrich_company(params: dict[str, Any]) -> dict[str, Any]:
    """Build query params for GET /api/v1/organizations/enrich.

    Note: This is a GET endpoint — params go as query string, not JSON body.
    """
    if not params.get("domain"):
        raise ProviderError("apollo", "domain is required for company enrichment")
    return {"domain": clean_domain(params["domain"])}


def _prepare_search_people(params: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for POST /api/v1/mixed_people/search.

    Key param mappings from nrev-lite -> Apollo:
        title/titles   -> person_titles[]
        location       -> person_locations[]
        domain/domains -> q_organization_domains
        company        -> q_organization_name
        seniority      -> person_seniorities[]
        limit          -> per_page (max 100)
        page           -> page (max 500)
    """
    p: dict[str, Any] = {}

    # Titles
    titles = params.get("person_titles") or params.get("titles") or params.get("title")
    if titles:
        p["person_titles"] = ensure_list(titles)

    # Locations
    locs = params.get("person_locations") or params.get("locations") or params.get("location")
    if locs:
        p["person_locations"] = ensure_list(locs)

    # Seniority
    seniority = params.get("person_seniorities") or params.get("seniority")
    if seniority:
        p["person_seniorities"] = ensure_list(seniority)

    # Company domains (critical: must be clean)
    domains = (
        params.get("q_organization_domains")
        or params.get("domains")
        or params.get("domain")
    )
    if domains:
        p["q_organization_domains"] = "\n".join(clean_domains(domains))

    # Company name
    org_name = params.get("q_organization_name") or params.get("company")
    if org_name:
        p["q_organization_name"] = org_name.strip()

    # Education / school
    schools = params.get("person_education_school_names") or params.get("schools") or params.get("school")
    if schools:
        p["person_education_school_names"] = ensure_list(schools)

    # Keywords (free-text search across profile)
    if params.get("q_keywords"):
        p["q_keywords"] = params["q_keywords"].strip()

    # Past organizations (e.g., alumni searches)
    past_orgs = params.get("organization_past_domains") or params.get("past_domains")
    if past_orgs:
        p["organization_past_domains"] = ensure_list(past_orgs)

    # Current employer domains (NOT)
    not_domains = params.get("q_organization_domains_not") or params.get("exclude_domains")
    if not_domains:
        p["q_organization_domains_not"] = "\n".join(clean_domains(not_domains))

    # Departments
    departments = params.get("person_department_or_subdepartments") or params.get("departments")
    if departments:
        p["person_department_or_subdepartments"] = ensure_list(departments)

    # Pagination
    limit = params.get("per_page") or params.get("limit")
    if limit:
        p["per_page"] = min(int(limit), 100)
    else:
        p["per_page"] = 25

    page = params.get("page")
    if page:
        p["page"] = min(int(page), 500)

    return p


def _prepare_search_companies(params: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for POST /api/v1/mixed_companies/search."""
    p: dict[str, Any] = {}

    # Industry
    industry = params.get("organization_industry_tag_ids") or params.get("industry")
    if industry:
        p["organization_industry_tag_ids"] = ensure_list(industry)

    # Size
    size = params.get("organization_num_employees_ranges") or params.get("size")
    if size:
        p["organization_num_employees_ranges"] = ensure_list(size)

    # Location
    locs = params.get("organization_locations") or params.get("location")
    if locs:
        p["organization_locations"] = ensure_list(locs)

    # Domains
    domains = params.get("q_organization_domains") or params.get("domain")
    if domains:
        p["q_organization_domains"] = "\n".join(clean_domains(domains))

    # Name search
    name = params.get("q_organization_name") or params.get("name") or params.get("company")
    if name:
        p["q_organization_name"] = name.strip()

    # Pagination
    limit = params.get("per_page") or params.get("limit")
    if limit:
        p["per_page"] = min(int(limit), 100)
    else:
        p["per_page"] = 25

    page = params.get("page")
    if page:
        p["page"] = min(int(page), 500)

    return p


def _prepare_bulk_enrich_people(params: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for POST /api/v1/people/bulk_match.

    Expects params["details"] to be a list of up to 10 person dicts.
    """
    details = params.get("details", [])
    if not details:
        raise ProviderError("apollo", "details[] is required for bulk enrichment")
    if len(details) > 10:
        raise ProviderError("apollo", "bulk_enrich_people supports max 10 people per call")

    cleaned = []
    for d in details:
        p: dict[str, Any] = {}
        if d.get("email"):
            p["email"] = d["email"].strip().lower()
        if d.get("first_name"):
            p["first_name"] = d["first_name"]
        if d.get("last_name"):
            p["last_name"] = d["last_name"]
        if d.get("domain"):
            p["domain"] = clean_domain(d["domain"])
        if d.get("linkedin_url"):
            p["linkedin_url"] = d["linkedin_url"]
        cleaned.append(p)

    result: dict[str, Any] = {"details": cleaned}
    if params.get("reveal_personal_emails"):
        result["reveal_personal_emails"] = True
    if params.get("reveal_phone_number"):
        result["reveal_phone_number"] = True
    return result


def _prepare_bulk_enrich_companies(params: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for POST /api/v1/organizations/bulk_enrich.

    Expects params["domains"] to be a list of up to 10 domain strings.
    """
    domains = params.get("domains", [])
    if not domains:
        raise ProviderError("apollo", "domains[] is required for bulk company enrichment")
    if len(domains) > 10:
        raise ProviderError("apollo", "bulk_enrich_companies supports max 10 per call")
    return {"domains": [clean_domain(d) for d in domains]}


# ---------------------------------------------------------------------------
# Apollo provider class
# ---------------------------------------------------------------------------


class ApolloProvider(BaseProvider):
    """Apollo.io enrichment and search provider."""

    name = "apollo"
    supported_operations = [
        "enrich_person",
        "enrich_company",
        "search_people",
        "search_companies",
        "bulk_enrich_people",
        "bulk_enrich_companies",
    ]

    APOLLO_API_BASE = "https://api.apollo.io"

    # Map operations to their API details
    _OPERATION_MAP = {
        "enrich_person": {
            "method": "POST",
            "path": "/api/v1/people/match",
            "prepare": staticmethod(_prepare_enrich_person),
        },
        "enrich_company": {
            "method": "GET",
            "path": "/api/v1/organizations/enrich",
            "prepare": staticmethod(_prepare_enrich_company),
        },
        "search_people": {
            "method": "POST",
            "path": "/api/v1/mixed_people/search",
            "prepare": staticmethod(_prepare_search_people),
        },
        "search_companies": {
            "method": "POST",
            "path": "/api/v1/mixed_companies/search",
            "prepare": staticmethod(_prepare_search_companies),
        },
        "bulk_enrich_people": {
            "method": "POST",
            "path": "/api/v1/people/bulk_match",
            "prepare": staticmethod(_prepare_bulk_enrich_people),
        },
        "bulk_enrich_companies": {
            "method": "POST",
            "path": "/api/v1/organizations/bulk_enrich",
            "prepare": staticmethod(_prepare_bulk_enrich_companies),
        },
    }

    async def execute(
        self,
        operation: str,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        """Execute an Apollo API operation with full sanitisation.

        1. Prepare params (clean domains, coerce arrays, validate)
        2. Make the API call
        3. Parse rate limit headers (logged, not returned to user)
        4. Return the raw response for normalisation upstream
        """
        op_config = self._OPERATION_MAP.get(operation)
        if not op_config:
            raise ProviderError(self.name, f"Unsupported operation: {operation}")

        # Step 1: Sanitise and prepare params
        prepare_fn = op_config["prepare"]
        try:
            clean_params = prepare_fn(params)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                self.name, f"Invalid parameters for {operation}: {exc}"
            ) from exc

        # Step 2: Make the API call
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key,
        }

        method = op_config["method"]
        url = f"{self.APOLLO_API_BASE}{op_config['path']}"

        try:
            async with httpx.AsyncClient() as client:
                if method == "GET":
                    response = await client.get(
                        url, headers=headers, params=clean_params, timeout=30.0,
                    )
                else:
                    response = await client.post(
                        url, headers=headers, json=clean_params, timeout=30.0,
                    )
        except httpx.TimeoutException:
            raise ProviderError(
                self.name,
                f"Request timed out for {operation}. Apollo may be slow — retry shortly.",
                status_code=504,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"HTTP error: {exc}")

        # Step 3: Parse rate limit headers
        self._log_rate_limits(response, operation)

        # Step 4: Handle response
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "60")
            raise ProviderError(
                self.name,
                f"Apollo rate limit hit. Retry after {retry_after}s.",
                status_code=429,
            )
        if response.status_code == 422:
            # Common: array format issues, bad params
            detail = response.text[:500]
            raise ProviderError(
                self.name,
                f"Apollo rejected the request (422): {detail}. "
                "This usually means a parameter format issue.",
                status_code=422,
            )
        if response.status_code == 401:
            raise ProviderError(
                self.name,
                "Apollo API key is invalid or expired. "
                "Update it with: nrev-lite keys add apollo",
                status_code=401,
            )
        if response.status_code == 403:
            raise ProviderError(
                self.name,
                "Apollo API key lacks permission for this operation. "
                "Some endpoints require a master API key.",
                status_code=403,
            )
        if response.status_code >= 500:
            raise ProviderError(
                self.name,
                f"Apollo server error ({response.status_code}). Will retry.",
                status_code=response.status_code,
            )
        if response.status_code != 200:
            raise ProviderError(
                self.name,
                f"Apollo returned {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )

        return response.json()

    def _log_rate_limits(self, response: httpx.Response, operation: str) -> None:
        """Log Apollo rate limit headers for monitoring."""
        limit = response.headers.get("X-RateLimit-Limit")
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")

        if remaining is not None:
            try:
                rem = int(remaining)
                lim = int(limit) if limit else "?"
                if rem < 10:
                    logger.warning(
                        "Apollo rate limit LOW: %s/%s remaining for %s (reset: %s)",
                        rem, lim, operation, reset,
                    )
                else:
                    logger.debug(
                        "Apollo rate limit: %s/%s remaining for %s",
                        rem, lim, operation,
                    )
            except (ValueError, TypeError):
                pass

    async def health_check(self, api_key: str) -> bool:
        """Check if Apollo API is reachable with the given key."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.APOLLO_API_BASE}/api/v1/people/match",
                    headers={
                        "X-Api-Key": api_key,
                        "Content-Type": "application/json",
                    },
                    json={"email": "test@example.com"},
                    timeout=10.0,
                )
                # 200 = valid key, 401 = invalid key, anything else = reachable
                return response.status_code in (200, 404)
        except Exception:
            return False


# Register on import
register_provider("apollo", ApolloProvider)
