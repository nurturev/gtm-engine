"""RocketReach provider — Universal API implementation.

Supported operations:
    - enrich_person: Look up a person by name+employer, email, LinkedIn, or ID
    - search_people: Search for people by title, company, location, skills, etc.
    - enrich_company: Look up a company by domain, name, LinkedIn, or ticker
    - search_companies: Search for companies by industry, size, location, etc.

All endpoints use the Universal API:
    - GET  /universal/person/lookup
    - POST /universal/person/search
    - GET  /universal/company/lookup
    - POST /universal/company/search

Pricing:
    - All calls cost 3 credits ($0.03)
    - Phone reveal (reveal_phone=true) costs 18 credits

RocketReach API quirks handled here:
    - Auth header is "Api-Key <key>" (NOT Bearer, NOT X-Api-Key)
    - Person lookup is GET (not POST)
    - Company lookup is GET (not POST)
    - Person search is POST, returns 201 (not 200) on success
    - Company search is POST, returns 201 (not 200) on success
    - Pagination uses "start" (1-indexed) and "page_size" (max 100)
    - Search has max 10,000 results per query — narrow filters if exceeded
    - Re-lookups are free (same profile won't cost credits again)
    - Lookup can return status="progress" (async) — need to poll checkStatus
    - Global rate limit: 10 requests/second across ALL endpoints
    - Retry-After header provided on 429
    - Domain format: "example.com" (same as Apollo — clean it)
    - LinkedIn URLs are the most accurate lookup method (~99% success)
    - Revenue empty-field handling: min empty→0, max empty→1000000000000, both empty→don't send
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
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


def _prepare_revenue_range(params: dict[str, Any], min_key: str, max_key: str) -> list | None:
    """Build revenue/funding range with empty-field handling.

    Rules:
        - Both empty: return None (don't send parameter)
        - Only min empty: use 0
        - Only max empty: use 1000000000000
    """
    raw_min = params.get(min_key)
    raw_max = params.get(max_key)

    has_min = raw_min is not None and str(raw_min).strip() != ""
    has_max = raw_max is not None and str(raw_max).strip() != ""

    if not has_min and not has_max:
        return None

    val_min = int(raw_min) if has_min else 0
    val_max = int(raw_max) if has_max else 1000000000000

    return [f"{val_min}-{val_max}"]


# ---------------------------------------------------------------------------
# Parameter preparation per operation
# ---------------------------------------------------------------------------


def _prepare_enrich_person(params: dict[str, Any]) -> dict[str, Any]:
    """Build query params for GET /universal/person/lookup.

    Identifiers (at least one required, ranked by accuracy):
        - linkedin_url (~99% match)
        - email (~87% match)
        - name + current_employer
        - id (RocketReach profile ID)
        - npi_number (US healthcare)

    Reveal flags control returned data and cost:
        - reveal_phone: bumps cost from 3 to 18 credits
        - reveal_professional_email, reveal_personal_email
        - reveal_detailed_person_enrichment
        - reveal_healthcare_enrichment
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

    # NPI number (US healthcare)
    if params.get("npi_number"):
        p["npi_number"] = int(params["npi_number"])

    # Reveal flags — control what data is returned and cost
    if params.get("reveal_phone") or params.get("enrich_phone_number"):
        p["reveal_phone"] = True
    if params.get("reveal_professional_email") is not None:
        p["reveal_professional_email"] = bool(params["reveal_professional_email"])
    if params.get("reveal_personal_email") is not None:
        p["reveal_personal_email"] = bool(params["reveal_personal_email"])
    if params.get("reveal_detailed_person_enrichment") is not None:
        p["reveal_detailed_person_enrichment"] = bool(params["reveal_detailed_person_enrichment"])
    if params.get("reveal_healthcare_enrichment") is not None:
        p["reveal_healthcare_enrichment"] = bool(params["reveal_healthcare_enrichment"])

    # Cached emails — default true, changing to false after May 2026
    if params.get("return_cached_emails") is not None:
        p["return_cached_emails"] = bool(params["return_cached_emails"])

    # Webhook for async delivery
    if params.get("webhook_id"):
        p["webhook_id"] = int(params["webhook_id"])

    # Validation: need at least one identifier
    if not any(k in p for k in ("name", "email", "linkedin_url", "id", "npi_number")):
        raise ProviderError(
            "rocketreach",
            "Person lookup requires at least one of: name+company, email, "
            "linkedin_url, id, or npi_number. LinkedIn URL is the most accurate method.",
        )

    # If name is provided, employer should be too for best results
    if p.get("name") and not p.get("current_employer") and not p.get("linkedin_url"):
        logger.warning(
            "RocketReach person lookup with name but no employer — "
            "results may be inaccurate. Provide company or linkedin_url."
        )

    return p


def _prepare_search_people(params: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for POST /universal/person/search.

    Key param mappings from nrev-lite -> RocketReach:
        title/titles                -> query.current_title (or current_or_previous_title)
        include_past_titles         -> switches current_title to current_or_previous_title
        company/employer            -> query.current_employer
        domain                      -> query.company_domain
        location                    -> query.geo
        seniority                   -> query.management_levels
        department                  -> query.department
        skills                      -> query.skills
        industry                    -> query.company_industry
        company_industry_keywords   -> query.company_industry_keywords
        company_size                -> query.company_size
        company_revenue             -> query.company_revenue (min-max range)
        company_funding_min/max     -> query.company_funding_min/max
        company_competitors         -> query.company_competitors
        previous_employer           -> query.previous_employer
        school                      -> query.school
        degree                      -> query.degree
        major                       -> query.major
        years_experience            -> query.years_experience
        job_change_range_days       -> query.job_change_range_days
        growth                      -> query.growth
        company_tag                 -> query.company_tag
        company_publicly_traded     -> query.company_publicly_traded
        contact_method              -> query.contact_method
        job_change_signal           -> query.job_change_signal
        news_signal                 -> query.news_signal
        company_news_signal         -> query.company_news_signal
        company_job_posting_signal  -> query.company_job_posting_signal
        company_intent              -> query.company_intent
        limit/per_page              -> page_size (max 100)
        page/start                  -> start (1-indexed)
        order_by                    -> order_by (relevance|popularity|score)
    """
    query: dict[str, Any] = {}

    # --- Employment (Current) ---

    # Titles — with include_past_titles toggle
    titles = params.get("current_title") or params.get("titles") or params.get("title")
    include_past = params.get("include_past_titles", False)
    if titles:
        title_list = _ensure_list(titles)
        if include_past:
            query["current_or_previous_title"] = title_list
        else:
            query["current_title"] = title_list

    # Current or previous title (direct passthrough)
    if params.get("current_or_previous_title") and "current_or_previous_title" not in query:
        query["current_or_previous_title"] = _ensure_list(params["current_or_previous_title"])

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

    # Department
    dept = params.get("department")
    if dept:
        query["department"] = _ensure_list(dept)

    # Management level / seniority
    mgmt = (
        params.get("management_levels")
        or params.get("seniority")
        or params.get("management_level")
    )
    if mgmt:
        query["management_levels"] = _ensure_list(mgmt)

    # --- Employment (Previous) ---

    # Previous employer (alumni searches)
    prev_employer = params.get("previous_employer") or params.get("past_employer") or params.get("past_company")
    if prev_employer:
        query["previous_employer"] = _ensure_list(prev_employer)

    # Previous title
    prev_title = params.get("previous_title")
    if prev_title:
        query["previous_title"] = _ensure_list(prev_title)

    # Previous company ID (more reliable than free-text)
    prev_company_id = params.get("previous_company_id")
    if prev_company_id:
        query["previous_company_id"] = _ensure_list(prev_company_id)

    # Recently moved in
    job_change_days = params.get("job_change_range_days")
    if job_change_days:
        query["job_change_range_days"] = _ensure_list(job_change_days)

    # --- Company Attributes ---

    # Industry
    industry = params.get("company_industry") or params.get("industry")
    if industry:
        query["company_industry"] = _ensure_list(industry)

    # Industry keywords
    industry_kw = params.get("company_industry_keywords")
    if industry_kw:
        query["company_industry_keywords"] = _ensure_list(industry_kw)

    # Company size
    size = params.get("company_size") or params.get("employees")
    if size:
        query["company_size"] = _ensure_list(size)

    # Company revenue (min-max range handling)
    revenue_range = _prepare_revenue_range(params, "company_revenue_min", "company_revenue_max")
    if revenue_range:
        query["company_revenue"] = revenue_range
    elif params.get("company_revenue"):
        query["company_revenue"] = _ensure_list(params["company_revenue"])

    # Company funding range
    if params.get("company_funding_min") is not None:
        query["company_funding_min"] = int(params["company_funding_min"])
    if params.get("company_funding_max") is not None:
        query["company_funding_max"] = int(params["company_funding_max"])

    # Total funding
    if params.get("total_funding"):
        query["total_funding"] = _ensure_list(params["total_funding"])

    # Company competitors
    competitors = params.get("company_competitors") or params.get("competitors")
    if competitors:
        query["company_competitors"] = _ensure_list(competitors)

    # Company tag
    if params.get("company_tag"):
        query["company_tag"] = _ensure_list(params["company_tag"])

    # Company publicly traded
    if params.get("company_publicly_traded") is not None:
        query["company_publicly_traded"] = params["company_publicly_traded"]

    # --- Location ---

    geo = params.get("geo") or params.get("location") or params.get("locations")
    if geo:
        query["geo"] = _ensure_list(geo)

    if params.get("city"):
        query["city"] = _ensure_list(params["city"])
    if params.get("state"):
        query["state"] = _ensure_list(params["state"])
    if params.get("country_code"):
        query["country_code"] = _ensure_list(params["country_code"])
    if params.get("postal_code"):
        query["postal_code"] = _ensure_list(params["postal_code"])
    if params.get("region"):
        query["region"] = _ensure_list(params["region"])
    if params.get("company_country_code"):
        query["company_country_code"] = _ensure_list(params["company_country_code"])

    # --- Education ---

    school = params.get("school") or params.get("schools") or params.get("education")
    if school:
        query["school"] = _ensure_list(school)

    if params.get("degree"):
        query["degree"] = _ensure_list(params["degree"])

    if params.get("major"):
        query["major"] = _ensure_list(params["major"])

    # --- Skills & Experience ---

    skills = params.get("skills")
    if skills:
        query["skills"] = _ensure_list(skills)

    all_skills = params.get("all_skills")
    if all_skills:
        query["all_skills"] = _ensure_list(all_skills)

    years_exp = params.get("years_experience")
    if years_exp:
        query["years_experience"] = _ensure_list(years_exp)

    if params.get("connections"):
        query["connections"] = _ensure_list(params["connections"])

    # --- Signals ---

    # Department growth — structured format: min-max::Department,TimeRange
    growth = params.get("growth")
    if growth:
        query["growth"] = _ensure_list(growth)

    # Job change signal
    if params.get("job_change_signal"):
        query["job_change_signal"] = _ensure_list(params["job_change_signal"])

    # News signals
    if params.get("news_signal"):
        query["news_signal"] = _ensure_list(params["news_signal"])
    if params.get("company_news_signal"):
        query["company_news_signal"] = _ensure_list(params["company_news_signal"])
    if params.get("company_job_posting_signal"):
        query["company_job_posting_signal"] = _ensure_list(params["company_job_posting_signal"])

    # Intent
    if params.get("company_intent"):
        query["company_intent"] = _ensure_list(params["company_intent"])

    # Contact method filter
    contact_method = params.get("contact_method")
    if contact_method:
        query["contact_method"] = _ensure_list(contact_method)

    # --- Healthcare ---

    if params.get("health_credentials"):
        query["health_credentials"] = _ensure_list(params["health_credentials"])
    if params.get("health_license"):
        query["health_license"] = _ensure_list(params["health_license"])
    if params.get("health_npi"):
        query["health_npi"] = _ensure_list(params["health_npi"])
    if params.get("health_specialization"):
        query["health_specialization"] = _ensure_list(params["health_specialization"])

    # --- Other ---

    # Keyword search
    keyword = params.get("keyword") or params.get("keywords") or params.get("q")
    if keyword:
        query["keyword"] = keyword if isinstance(keyword, list) else keyword

    # Email grade filter
    if params.get("email_grade"):
        query["email_grade"] = _ensure_list(params["email_grade"])

    # Exclude filters (prefix with exclude_)
    for key in list(params.keys()):
        if key.startswith("exclude_"):
            query[key] = _ensure_list(params[key])

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
    """Build query params for GET /universal/company/lookup.

    Can look up by domain (preferred), name, id, linkedin_url, or ticker.
    """
    p: dict[str, Any] = {}

    if params.get("domain"):
        p["domain"] = _clean_domain(params["domain"])
    elif params.get("name") or params.get("company"):
        p["name"] = (params.get("name") or params.get("company")).strip()
    elif params.get("id"):
        p["id"] = int(params["id"])
    elif params.get("linkedin_url"):
        p["linkedin_url"] = _clean_linkedin_url(params["linkedin_url"])
    elif params.get("ticker"):
        p["ticker"] = params["ticker"].strip().upper()
    else:
        raise ProviderError(
            "rocketreach",
            "Company lookup requires one of: domain, name, id, linkedin_url, or ticker. "
            "Domain is the most accurate.",
        )

    return p


def _prepare_search_companies(params: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for POST /universal/company/search.

    Key param mappings from nrev-lite -> RocketReach:
        name/company            -> query.name
        domain                  -> query.domain
        industry                -> query.industry
        industry_keywords       -> query.industry_keywords
        industry_tags           -> query.industry_tags
        location/geo            -> query.geo / query.location
        size/employees          -> query.employees
        revenue                 -> query.revenue (min-max range)
        total_funding           -> query.total_funding
        competitors             -> query.competitors
        publicly_traded         -> query.publicly_traded
        techstack               -> query.techstack
        growth                  -> query.growth
        news_signal             -> query.news_signal
        job_posting_signal      -> query.job_posting_signal
        intent                  -> query.intent
        website_category        -> query.website_category
    """
    query: dict[str, Any] = {}

    name = params.get("company_name") or params.get("name") or params.get("company")
    if name:
        query["name"] = _ensure_list(name)

    domain = params.get("domain")
    if domain:
        query["domain"] = [_clean_domain(d) for d in _ensure_list(domain)]

    # Industry
    industry = params.get("industry")
    if industry:
        query["industry"] = _ensure_list(industry)

    industry_kw = params.get("industry_keywords") or params.get("company_keywords")
    if industry_kw:
        query["industry_keywords"] = _ensure_list(industry_kw)

    if params.get("industry_tags"):
        query["industry_tags"] = _ensure_list(params["industry_tags"])

    # Location
    geo = params.get("geo") or params.get("location") or params.get("locations")
    if geo:
        query["geo"] = _ensure_list(geo)

    # Size
    employees = params.get("employees") or params.get("size")
    if employees:
        query["employees"] = _ensure_list(employees)

    # Revenue (min-max range handling)
    revenue_range = _prepare_revenue_range(params, "revenue_min", "revenue_max")
    if revenue_range:
        query["revenue"] = revenue_range
    elif params.get("revenue"):
        query["revenue"] = _ensure_list(params["revenue"])

    # Total funding
    if params.get("total_funding"):
        query["total_funding"] = _ensure_list(params["total_funding"])

    # Competitors
    competitors = params.get("competitors")
    if competitors:
        query["competitors"] = _ensure_list(competitors)

    # Publicly traded
    if params.get("publicly_traded") is not None:
        query["publicly_traded"] = _ensure_list(params["publicly_traded"])

    # Tech stack
    if params.get("techstack"):
        query["techstack"] = _ensure_list(params["techstack"])

    # Growth — structured format: min-max::Department,TimeRange
    if params.get("growth"):
        query["growth"] = _ensure_list(params["growth"])

    # Signals
    if params.get("news_signal"):
        query["news_signal"] = _ensure_list(params["news_signal"])
    if params.get("job_posting_signal"):
        query["job_posting_signal"] = _ensure_list(params["job_posting_signal"])

    # Intent
    if params.get("intent"):
        query["intent"] = _ensure_list(params["intent"])

    # Website category
    if params.get("website_category"):
        query["website_category"] = _ensure_list(params["website_category"])

    # Keyword
    keyword = params.get("keyword") or params.get("keywords")
    if keyword:
        query["keyword"] = _ensure_list(keyword)

    # Description
    if params.get("description"):
        query["description"] = _ensure_list(params["description"])

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
    """RocketReach enrichment and search provider (Universal API)."""

    name = "rocketreach"
    supported_operations = [
        "enrich_person",
        "search_people",
        "enrich_company",
        "search_companies",
    ]

    BASE_URL = "https://api.rocketreach.co/api/v2"
    _POLL_PATH = "/universal/person/check_status"
    _POLL_INTERVAL_SECONDS = 3.0
    _POLL_CAP_SECONDS = 30.0
    _ASYNC_STATUSES = frozenset({"searching", "progress", "waiting"})

    # Map operations to their Universal API details
    _OPERATION_MAP = {
        "enrich_person": {
            "method": "GET",
            "path": "/universal/person/lookup",
            "prepare": staticmethod(_prepare_enrich_person),
            "success_codes": {200},
        },
        "search_people": {
            "method": "POST",
            "path": "/universal/person/search",
            "prepare": staticmethod(_prepare_search_people),
            "success_codes": {200, 201},  # RR returns 201 for search
        },
        "enrich_company": {
            "method": "GET",
            "path": "/universal/company/lookup",
            "prepare": staticmethod(_prepare_enrich_company),
            "success_codes": {200},
        },
        "search_companies": {
            "method": "POST",
            "path": "/universal/company/search",
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
            # Universal endpoints return 403 when the key lacks a Universal
            # credit allocation. Map to 402 to match insufficient-credits
            # convention (see execution/router.py :109, :358).
            body_detail = ""
            try:
                body_detail = str(response.json().get("detail") or "")
            except ValueError:
                body_detail = response.text[:200]
            if "Universal Credits" in body_detail:
                raise ProviderError(
                    self.name,
                    "RocketReach key lacks Universal credit allocation. "
                    "Upgrade the RocketReach plan at rocketreach.co, or "
                    "contact nRev support to provision Universal credits.",
                    status_code=402,
                )
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

        # Step 4: Async lookup handling for enrich_person.
        # Universal person lookup can return status in {searching, progress,
        # waiting} with a vendor id; poll /check_status until the record
        # reaches complete/failed or we hit the 30-second wall-time cap.
        if (
            operation == "enrich_person"
            and isinstance(data, dict)
            and data.get("status") in self._ASYNC_STATUSES
            and isinstance(data.get("id"), int)
        ):
            logger.info(
                "RocketReach enrich_person async; id=%s status=%s — polling",
                data["id"], data.get("status"),
            )
            data = await self._poll_person_lookup_until_complete(
                vendor_id=data["id"], api_key=api_key,
            )

        return data

    async def _poll_person_lookup_until_complete(
        self,
        vendor_id: int,
        api_key: str,
    ) -> dict[str, Any]:
        """Poll /universal/person/check_status until complete/failed or 30s cap.

        Contract:
            - On status=complete: returns the matched profile payload verbatim.
            - On status=failed: returns the match_found=false sentinel.
            - On cap-hit: returns the last-seen profile with lookup_status and
              retry_hint stamped on it so the normalizer can thread them
              through to the caller.
        """
        headers = {
            "Api-Key": api_key,
            "Content-Type": "application/json",
        }
        url = f"{self.BASE_URL}{self._POLL_PATH}"
        params = {"ids": str(vendor_id)}

        start = time.monotonic()
        last_profile: dict[str, Any] = {}

        async with httpx.AsyncClient() as client:
            while True:
                remaining = self._POLL_CAP_SECONDS - (time.monotonic() - start)
                if remaining <= 0:
                    break

                await asyncio.sleep(min(self._POLL_INTERVAL_SECONDS, remaining))

                try:
                    response = await client.get(
                        url, headers=headers, params=params, timeout=15.0,
                    )
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    logger.info("RocketReach poll tick transient failure: %s", exc)
                    continue

                self._log_rate_info(response, "check_status")

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "3"))
                    sleep_for = min(retry_after, max(remaining - 0.1, 0.0))
                    if sleep_for <= 0:
                        break
                    await asyncio.sleep(sleep_for)
                    continue
                if response.status_code == 401:
                    raise ProviderError(
                        self.name,
                        "RocketReach API key became invalid during async poll.",
                        status_code=401,
                    )
                if response.status_code in (402, 403):
                    raise ProviderError(
                        self.name,
                        "RocketReach rejected async poll — permission or "
                        "credit state changed mid-request.",
                        status_code=response.status_code,
                    )
                if response.status_code != 200:
                    continue

                try:
                    payload = response.json()
                except ValueError:
                    continue

                entries = (
                    payload if isinstance(payload, list)
                    else payload.get("profiles") if isinstance(payload, dict)
                    else []
                )
                matched = next(
                    (
                        entry for entry in entries
                        if isinstance(entry, dict) and entry.get("id") == vendor_id
                    ),
                    None,
                )
                if matched is None:
                    continue
                last_profile = matched
                status = matched.get("status")
                if status == "complete":
                    return matched
                if status == "failed":
                    logger.info(
                        "RocketReach async lookup returned status=failed id=%s",
                        vendor_id,
                    )
                    return {"match_found": False, "profiles": []}

        result = dict(last_profile)
        result["lookup_status"] = "in_progress"
        result["retry_hint"] = {
            "vendor_id": vendor_id,
            "retry_after_seconds": int(self._POLL_CAP_SECONDS),
        }
        logger.warning(
            "RocketReach enrich_person cap-hit at %ss; returning in_progress id=%s",
            self._POLL_CAP_SECONDS, vendor_id,
        )
        return result

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
