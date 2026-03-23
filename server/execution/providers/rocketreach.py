"""RocketReach provider — full API implementation.

Supported operations:
    - enrich_person: Look up a person by name+employer, email, LinkedIn, or ID
    - search_people: Search for people by title, company, location, skills, etc.
    - enrich_company: Look up a company by domain or name
    - search_companies: Search for companies by industry, size, location, etc.

RocketReach API quirks handled here:
    - Auth header is "Api-Key <key>" (NOT Bearer, NOT X-Api-Key)
    - All endpoints are v2: base URL is https://api.rocketreach.co/api/v2
    - v1 endpoints always return errors — NEVER use v1
    - Person lookup is GET (not POST)
    - Company lookup is GET (not POST)
    - Person search is POST, returns 201 (not 200) on success
    - Company search is POST, returns 201 (not 200) on success
    - Pagination uses "start" (1-indexed) and "page_size" (max 100)
    - Search has max 10,000 results per query — narrow filters if exceeded
    - Bulk lookup requires min 10, max 100 profiles, needs webhook
    - Re-lookups are free (same profile won't cost credits again)
    - No credits charged for lookups with no results
    - No credits charged for B or F grade emails
    - Lookup can return status="progress" (async) — need to poll checkStatus
    - Global rate limit: 10 requests/second across ALL endpoints
    - Retry-After header provided on 429
    - Domain format: "example.com" (same as Apollo — clean it)
    - LinkedIn URLs are the most accurate lookup method (~99% success)
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


def _clean_domain(raw: str) -> str:
    """Normalize a domain to bare format: 'example.com'."""
    if not raw or not raw.strip():
        return raw
    d = raw.strip().lower()
    if _PROTOCOL_RE.match(d):
        parsed = urlparse(d)
        d = parsed.hostname or d
    else:
        d = d.split("/")[0]
    if d.startswith("www."):
        d = d[4:]
    d = d.rstrip(".")
    return d


def _ensure_list(val: Any) -> list:
    """Ensure a value is a list."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [v.strip() for v in val.split(",") if v.strip()]
    return [val]


def _clean_linkedin_url(url: str) -> str:
    """Normalize LinkedIn URL to the format RocketReach expects."""
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    url = url.rstrip("/")
    return url


# ---------------------------------------------------------------------------
# Parameter preparation per operation
# ---------------------------------------------------------------------------


def _prepare_enrich_person(params: dict[str, Any]) -> dict[str, Any]:
    """Build query params for GET /api/v2/person/lookup.

    RocketReach person lookup accepts:
        - name + current_employer (most common)
        - email
        - linkedin_url (most accurate, ~99% success)
        - id (RocketReach profile ID)

    At least one identifier is required.
    """
    p: dict[str, Any] = {}

    # LinkedIn URL — most accurate method
    if params.get("linkedin_url") or params.get("linkedin"):
        p["linkedin_url"] = _clean_linkedin_url(
            params.get("linkedin_url") or params.get("linkedin")
        )

    # Email
    if params.get("email"):
        p["email"] = params["email"].strip().lower()

    # Name + employer combo
    if params.get("name"):
        p["name"] = params["name"].strip()
    if params.get("first_name") and params.get("last_name"):
        if not p.get("name"):
            p["name"] = f"{params['first_name'].strip()} {params['last_name'].strip()}"

    if params.get("current_employer") or params.get("company"):
        p["current_employer"] = (
            params.get("current_employer") or params.get("company")
        ).strip()

    # Domain context — map to current_employer if not set
    if params.get("domain") and not p.get("current_employer"):
        p["current_employer"] = _clean_domain(params["domain"])

    # Title
    if params.get("title"):
        p["title"] = params["title"].strip()

    # RocketReach ID
    if params.get("id"):
        p["id"] = int(params["id"])

    # Lookup type
    if params.get("lookup_type"):
        p["lookup_type"] = params["lookup_type"]

    # Validation: need at least one identifier
    if not any(k in p for k in ("name", "email", "linkedin_url", "id")):
        raise ProviderError(
            "rocketreach",
            "Person lookup requires at least one of: name+company, email, "
            "linkedin_url, or id. LinkedIn URL is the most accurate method.",
        )

    # If name is provided, employer should be too for best results
    if p.get("name") and not p.get("current_employer") and not p.get("linkedin_url"):
        logger.warning(
            "RocketReach person lookup with name but no employer — "
            "results may be inaccurate. Provide company or linkedin_url."
        )

    return p


def _prepare_search_people(params: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for POST /api/v2/person/search.

    Key param mappings from nrev-lite -> RocketReach:
        title/titles         -> query.current_title
        company/employer     -> query.current_employer
        domain               -> query.company_domain
        location             -> query.geo
        seniority            -> query.management_levels
        skills               -> query.skills
        industry             -> query.company_industry
        limit/per_page       -> page_size (max 100)
        page/start           -> start (1-indexed)
        order_by             -> order_by (relevance|popularity|score)
    """
    query: dict[str, Any] = {}

    # Titles
    titles = params.get("current_title") or params.get("titles") or params.get("title")
    if titles:
        query["current_title"] = _ensure_list(titles)

    # Employer
    employer = (
        params.get("current_employer")
        or params.get("company")
        or params.get("employer")
    )
    if employer:
        query["current_employer"] = _ensure_list(employer)

    # Domain
    domains = (
        params.get("company_domain")
        or params.get("domains")
        or params.get("domain")
    )
    if domains:
        cleaned = [_clean_domain(d) for d in _ensure_list(domains)]
        query["company_domain"] = cleaned

    # Location
    geo = params.get("geo") or params.get("location") or params.get("locations")
    if geo:
        query["geo"] = _ensure_list(geo)

    # Management level / seniority
    mgmt = (
        params.get("management_levels")
        or params.get("seniority")
        or params.get("management_level")
    )
    if mgmt:
        query["management_levels"] = _ensure_list(mgmt)

    # Industry
    industry = params.get("company_industry") or params.get("industry")
    if industry:
        query["company_industry"] = _ensure_list(industry)

    # Skills
    skills = params.get("skills")
    if skills:
        query["skills"] = _ensure_list(skills)

    # Company size
    size = params.get("company_size") or params.get("employees")
    if size:
        query["company_size"] = _ensure_list(size)

    # Department
    dept = params.get("department")
    if dept:
        query["department"] = _ensure_list(dept)

    # Education / school
    school = params.get("school") or params.get("schools") or params.get("education")
    if school:
        query["school"] = _ensure_list(school)

    # Previous employer (alumni searches)
    prev_employer = params.get("previous_employer") or params.get("past_employer") or params.get("past_company")
    if prev_employer:
        query["previous_employer"] = _ensure_list(prev_employer)

    # Keyword search
    keyword = params.get("keyword") or params.get("keywords") or params.get("q")
    if keyword:
        query["keyword"] = keyword if isinstance(keyword, list) else keyword

    # Exclude filters (prefix with exclude_)
    for key in list(params.keys()):
        if key.startswith("exclude_"):
            query[key] = _ensure_list(params[key])

    # Contact method filter
    contact_method = params.get("contact_method")
    if contact_method:
        query["contact_method"] = _ensure_list(contact_method)

    # Build the full payload
    payload: dict[str, Any] = {"query": query}

    # Pagination — RocketReach uses start (1-indexed) and page_size
    page_size = params.get("page_size") or params.get("per_page") or params.get("limit")
    if page_size:
        payload["page_size"] = min(int(page_size), 100)
    else:
        payload["page_size"] = 25

    # Convert page number to start index
    page = params.get("page")
    start = params.get("start")
    if start:
        payload["start"] = min(int(start), 10000)
    elif page:
        pg = int(page)
        ps = payload.get("page_size", 25)
        payload["start"] = ((pg - 1) * ps) + 1

    # Ordering
    order_by = params.get("order_by")
    if order_by and order_by in ("relevance", "popularity", "score"):
        payload["order_by"] = order_by

    return payload


def _prepare_enrich_company(params: dict[str, Any]) -> dict[str, Any]:
    """Build query params for GET /api/v2/company/lookup.

    Can look up by domain or company name.
    """
    p: dict[str, Any] = {}

    if params.get("domain"):
        p["domain"] = _clean_domain(params["domain"])
    elif params.get("name") or params.get("company"):
        p["name"] = (params.get("name") or params.get("company")).strip()
    else:
        raise ProviderError(
            "rocketreach",
            "Company lookup requires 'domain' or 'name'. Domain is more accurate.",
        )

    return p


def _prepare_search_companies(params: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for POST /api/v2/company/search.

    Key param mappings from nrev-lite -> RocketReach:
        name/company       -> query.company_name
        domain             -> query.domain
        industry           -> query.industry
        location/geo       -> query.geo
        size/employees     -> query.employees
        revenue            -> query.revenue
    """
    query: dict[str, Any] = {}

    name = params.get("company_name") or params.get("name") or params.get("company")
    if name:
        query["company_name"] = _ensure_list(name)

    domain = params.get("domain")
    if domain:
        query["domain"] = [_clean_domain(d) for d in _ensure_list(domain)]

    industry = params.get("industry")
    if industry:
        query["industry"] = _ensure_list(industry)

    geo = params.get("geo") or params.get("location")
    if geo:
        query["geo"] = _ensure_list(geo)

    employees = params.get("employees") or params.get("size")
    if employees:
        query["employees"] = _ensure_list(employees)

    revenue = params.get("revenue")
    if revenue:
        query["revenue"] = _ensure_list(revenue)

    payload: dict[str, Any] = {"query": query}

    page_size = params.get("page_size") or params.get("per_page") or params.get("limit")
    if page_size:
        payload["page_size"] = min(int(page_size), 100)
    else:
        payload["page_size"] = 25

    page = params.get("page")
    start = params.get("start")
    if start:
        payload["start"] = min(int(start), 10000)
    elif page:
        pg = int(page)
        ps = payload.get("page_size", 25)
        payload["start"] = ((pg - 1) * ps) + 1

    order_by = params.get("order_by")
    if order_by and order_by in ("relevance", "popularity", "score"):
        payload["order_by"] = order_by

    return payload


# ---------------------------------------------------------------------------
# RocketReach provider class
# ---------------------------------------------------------------------------


class RocketReachProvider(BaseProvider):
    """RocketReach enrichment and search provider."""

    name = "rocketreach"
    supported_operations = [
        "enrich_person",
        "search_people",
        "enrich_company",
        "search_companies",
    ]

    BASE_URL = "https://api.rocketreach.co/api/v2"

    # Map operations to their API details
    _OPERATION_MAP = {
        "enrich_person": {
            "method": "GET",
            "path": "/person/lookup",
            "prepare": staticmethod(_prepare_enrich_person),
            "success_codes": {200},
        },
        "search_people": {
            "method": "POST",
            "path": "/person/search",
            "prepare": staticmethod(_prepare_search_people),
            "success_codes": {200, 201},  # RR returns 201 for search
        },
        "enrich_company": {
            "method": "GET",
            "path": "/company/lookup",
            "prepare": staticmethod(_prepare_enrich_company),
            "success_codes": {200},
        },
        "search_companies": {
            "method": "POST",
            "path": "/company/search",
            "prepare": staticmethod(_prepare_search_companies),
            "success_codes": {200, 201},
        },
    }

    async def execute(
        self,
        operation: str,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        """Execute a RocketReach API operation with full sanitisation.

        1. Prepare params (clean domains, validate identifiers)
        2. Make the API call with proper auth header
        3. Handle async lookups (status=progress)
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
        # RocketReach uses "Api-Key <key>" header (NOT Bearer, NOT X-Api-Key)
        headers = {
            "Api-Key": api_key,
            "Content-Type": "application/json",
        }

        method = op_config["method"]
        url = f"{self.BASE_URL}{op_config['path']}"

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
                f"Request timed out for {operation}. RocketReach may be slow — retry shortly.",
                status_code=504,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"HTTP error: {exc}")

        # Step 3: Handle response status codes
        self._log_rate_info(response, operation)

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "60")
            raise ProviderError(
                self.name,
                f"RocketReach rate limit hit. Retry after {retry_after}s. "
                f"Global limit is 10 req/s.",
                status_code=429,
            )
        if response.status_code == 401:
            raise ProviderError(
                self.name,
                "RocketReach API key is invalid or expired. "
                "Update it with: nrev-lite keys add rocketreach",
                status_code=401,
            )
        if response.status_code == 403:
            raise ProviderError(
                self.name,
                "RocketReach API key lacks permission for this operation. "
                "Check your plan level.",
                status_code=403,
            )
        if response.status_code == 400:
            detail = response.text[:500]
            raise ProviderError(
                self.name,
                f"RocketReach rejected the request (400): {detail}. "
                "Check parameter format.",
                status_code=400,
            )
        if response.status_code == 404:
            # 404 can mean "no profile found" — not an error, just no match
            return {"match_found": False, "profiles": []}
        if response.status_code >= 500:
            raise ProviderError(
                self.name,
                f"RocketReach server error ({response.status_code}). Will retry.",
                status_code=response.status_code,
            )

        success_codes = op_config["success_codes"]
        if response.status_code not in success_codes:
            raise ProviderError(
                self.name,
                f"RocketReach returned {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )

        data = response.json()

        # Step 4: Handle async lookup status
        # Person lookups may return status="progress" (still searching)
        if isinstance(data, dict) and data.get("status") == "progress":
            data["_async_in_progress"] = True
            logger.info(
                "RocketReach lookup in progress for %s — cached data may be partial",
                operation,
            )

        return data

    def _log_rate_info(self, response: httpx.Response, operation: str) -> None:
        """Log RocketReach rate limit status from response headers."""
        request_id = response.headers.get("RR-Request-ID")
        if request_id:
            logger.debug(
                "RocketReach request %s for %s: status=%d",
                request_id, operation, response.status_code,
            )

    async def health_check(self, api_key: str) -> bool:
        """Check if RocketReach API is reachable with the given key."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.BASE_URL}/account",
                    headers={"Api-Key": api_key},
                    timeout=10.0,
                )
                return response.status_code == 200
        except Exception:
            return False


# Register on import
register_provider("rocketreach", RocketReachProvider)
