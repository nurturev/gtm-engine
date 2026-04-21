"""Fresh LinkedIn Profile Data provider (via RapidAPI).

Vendor: `freshdata-freshdata-default` on RapidAPI.
Product: "Fresh LinkedIn Profile Data".
V1 scope: the "Get person profile by LinkedIn URL" endpoint only — backs
the shared `enrich_person` operation when the caller passes a LinkedIn URL
and opts in with `provider="fresh_linkedin"`.

Design notes (kept terse; the long-form reasoning lives in the HLD/LLD):
- `cacheable = False` — freshness is the value prop; we never serve stale.
- `retry_config` — on 429 the outer retry harness waits ~60s and retries
  exactly once. Matches grooming D22.
- URL normalisation is intentionally strict: only accept profile URLs
  (`/in/<slug>`). Company, job and post URLs are rejected with a clear 400.
- Response returned raw; `_normalize_fresh_linkedin_person` in `normalizer.py`
  maps it to the shared union `Person` shape.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from server.core.exceptions import ProviderError
from server.execution.providers import register_provider
from server.execution.providers.base import BaseProvider
from server.execution.retry import RetryConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Upstream coordinates
# ---------------------------------------------------------------------------

RAPIDAPI_HOST = "fresh-linkedin-profile-data.p.rapidapi.com"
RAPIDAPI_BASE = f"https://{RAPIDAPI_HOST}"

# RapidAPI endpoint paths — one-line fixes if the vendor renames.
ENDPOINT_PROFILE_BY_URL      = "/get-linkedin-profile"
ENDPOINT_ENRICH_LEAD         = "/enrich-lead"
ENDPOINT_COMPANY_BY_URL      = "/get-company-by-linkedinurl"
ENDPOINT_COMPANY_BY_DOMAIN   = "/get-company-by-domain"
ENDPOINT_PROFILE_POSTS       = "/get-profile-posts"
ENDPOINT_COMPANY_POSTS       = "/get-company-posts"
ENDPOINT_POST_DETAILS        = "/get-post-details"
ENDPOINT_POST_REACTIONS      = "/get-post-reactions"
ENDPOINT_POST_COMMENTS       = "/get-post-comments"
ENDPOINT_SEARCH_POSTS        = "/search-posts"

_SEARCH_DEFAULT_PAYLOAD: dict[str, Any] = {
    "search_keywords":    "",
    "sort_by":            "Latest",
    "date_posted":        "",
    "content_type":       "",
    "from_member":        [],
    "from_company":       [],
    "mentioning_member":  [],
    "mentioning_company": [],
    "author_company":     [],
    "author_industry":    [],
    "author_keyword":     "",
    "page":               1,
}
_SEARCH_FILTER_LIST_KEYS = (
    "from_member", "from_company",
    "mentioning_member", "mentioning_company",
    "author_company", "author_industry",
)

_POST_URN_RE = re.compile(r"^\d{15,20}$")
_NUMERIC_STRING_RE = re.compile(r"^\d+$")

_REQUEST_TIMEOUT = 30.0


def _coerce_numeric_string(value: Any, field_name: str) -> str:
    """Accept int or numeric string; return str. Reject bool, negative, non-numeric."""
    if isinstance(value, bool):
        raise ProviderError(
            "fresh_linkedin",
            f"'{field_name}' must be a non-negative integer string; got bool",
            status_code=400,
        )
    if isinstance(value, int):
        if value < 0:
            raise ProviderError(
                "fresh_linkedin",
                f"'{field_name}' must be a non-negative integer string; got {value}",
                status_code=400,
            )
        return str(value)
    if isinstance(value, str):
        if not _NUMERIC_STRING_RE.match(value):
            raise ProviderError(
                "fresh_linkedin",
                f"'{field_name}' must be a non-negative integer string; got '{value}'",
                status_code=400,
            )
        return value
    raise ProviderError(
        "fresh_linkedin",
        f"'{field_name}' must be a non-negative integer string; got type {type(value).__name__}",
        status_code=400,
    )


def _require_paired(a: Any, b: Any, names: tuple[str, str]) -> None:
    """Raise 400 if exactly one of a / b is truthy. Both truthy OR both falsy = OK."""
    if bool(a) ^ bool(b):
        raise ProviderError(
            "fresh_linkedin",
            f"'{names[0]}' and '{names[1]}' must be provided together",
            status_code=400,
        )


# ---------------------------------------------------------------------------
# LinkedIn URL + domain normalisation helpers
# ---------------------------------------------------------------------------

_PROTOCOL_RE = re.compile(r"^https?://", re.IGNORECASE)
_LINKEDIN_HOST_RE = re.compile(r"^(?:[a-z0-9-]+\.)?linkedin\.com$", re.IGNORECASE)
_PROFILE_PATH_RE = re.compile(r"^/in/([A-Za-z0-9\-_%]+)/?$")
_COMPANY_PATH_RE = re.compile(r"^/company/([A-Za-z0-9\-_%]+)/?$")
_REJECTED_PROFILE_PATH_PREFIXES = ("/company/", "/jobs/", "/posts/", "/pulse/", "/school/")
_REJECTED_COMPANY_PATH_PREFIXES = ("/in/", "/jobs/", "/posts/", "/pulse/", "/school/")
_DOMAIN_VALIDATOR_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$")


def _normalize_linkedin_profile_url(raw: str) -> str:
    """Canonicalise a LinkedIn profile URL.

    Accepts: any reasonable variant of `linkedin.com/in/<slug>` — with/without
    `https://`, with/without `www.`, with/without trailing slash, with
    tracking query params.

    Returns: `https://www.linkedin.com/in/<slug>` (lowercase host, slug preserved).

    Raises: `ProviderError(400)` on any malformed or non-profile URL, including
    `/company/`, `/jobs/view/`, `/posts/`, and non-linkedin hosts.
    """
    if not raw or not raw.strip():
        raise ProviderError(
            "fresh_linkedin",
            "linkedin_url is empty",
            status_code=400,
        )

    url = raw.strip()
    if not _PROTOCOL_RE.match(url):
        url = f"https://{url}"

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not _LINKEDIN_HOST_RE.match(host):
        raise ProviderError(
            "fresh_linkedin",
            f"Only linkedin.com URLs are supported; got host '{host}'",
            status_code=400,
        )

    path = parsed.path or "/"
    for bad in _REJECTED_PROFILE_PATH_PREFIXES:
        if path.startswith(bad):
            raise ProviderError(
                "fresh_linkedin",
                (
                    "Only LinkedIn /in/<slug> profile URLs are supported; "
                    f"got path '{path}'"
                ),
                status_code=400,
            )

    match = _PROFILE_PATH_RE.match(path)
    if match is None:
        raise ProviderError(
            "fresh_linkedin",
            f"URL does not match a LinkedIn profile (/in/<slug>); got path '{path}'",
            status_code=400,
        )

    slug = match.group(1)
    return urlunparse(("https", "www.linkedin.com", f"/in/{slug}", "", "", ""))


def _normalize_linkedin_company_url(raw: str) -> str:
    """Canonicalise a LinkedIn company URL.

    Accepts any reasonable variant of `linkedin.com/company/<slug>`. Returns
    `https://www.linkedin.com/company/<slug>`. Rejects profile / job / post URLs.
    """
    if not raw or not raw.strip():
        raise ProviderError("fresh_linkedin", "linkedin_url is empty", status_code=400)

    url = raw.strip()
    if not _PROTOCOL_RE.match(url):
        url = f"https://{url}"

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not _LINKEDIN_HOST_RE.match(host):
        raise ProviderError(
            "fresh_linkedin",
            f"Only linkedin.com company URLs are supported; got host '{host}'",
            status_code=400,
        )

    path = parsed.path or "/"
    if path.startswith("/in/"):
        raise ProviderError(
            "fresh_linkedin",
            f"Company URL required, got profile URL '{path}'. Use enrich_person for /in/ URLs.",
            status_code=400,
        )
    for bad in _REJECTED_COMPANY_PATH_PREFIXES:
        if bad == "/in/":
            continue
        if path.startswith(bad):
            raise ProviderError(
                "fresh_linkedin",
                (
                    "Only LinkedIn /company/<slug> URLs are supported; "
                    f"got path '{path}'"
                ),
                status_code=400,
            )

    match = _COMPANY_PATH_RE.match(path)
    if match is None:
        raise ProviderError(
            "fresh_linkedin",
            f"URL does not match a LinkedIn company (/company/<slug>); got path '{path}'",
            status_code=400,
        )

    slug = match.group(1)
    return urlunparse(("https", "www.linkedin.com", f"/company/{slug}", "", "", ""))


def _normalize_domain(raw: str) -> str:
    """Auto-strip a domain input to the bare hostname.

    Accepts protocol, path, trailing slash, www. prefix. Returns lowercase
    bare domain. Raises `ProviderError(400)` on empty / invalid input.
    """
    if not isinstance(raw, str):
        raise ProviderError("fresh_linkedin", f"Invalid domain: {raw!r}", status_code=400)
    stripped = raw.strip()
    if not stripped:
        raise ProviderError("fresh_linkedin", "Domain is empty", status_code=400)
    stripped = _PROTOCOL_RE.sub("", stripped)
    stripped = stripped.split("/", 1)[0]
    stripped = stripped.lower()
    if stripped.startswith("www."):
        stripped = stripped[4:]
    if not stripped or not _DOMAIN_VALIDATOR_RE.match(stripped):
        raise ProviderError("fresh_linkedin", f"Invalid domain: '{raw}'", status_code=400)
    return stripped


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class FreshLinkedInProvider(BaseProvider):
    """Fresh LinkedIn Profile Data via RapidAPI.

    Delivers the `enrich_person` operation for callers who already have a
    LinkedIn URL and want direct-from-LinkedIn data (fresher than Apollo or
    RocketReach snapshots for that input type).
    """

    name = "fresh_linkedin"
    supported_operations = [
        "enrich_person",
        "enrich_company",
        "fetch_profile_posts",
        "fetch_company_posts",
        "fetch_post_details",
        "fetch_post_reactions",
        "fetch_post_comments",
        "search_posts",
    ]

    # Freshness is the product; never serve a cached response for this provider.
    cacheable = False

    # Upstream's per-key ceiling is 300 req/min. On 429, wait ~60s and retry
    # exactly once; if still throttled, surface the error so callers can
    # back off. (Grooming D22.)
    retry_config = RetryConfig(
        max_retries=1,
        base_delay=60.0,
        max_delay=60.0,
        jitter=False,
    )

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
        if operation == "enrich_person":
            return await self._enrich_person(params, api_key)
        if operation == "enrich_company":
            return await self._enrich_company(params, api_key)
        if operation == "fetch_profile_posts":
            return await self._fetch_profile_posts(params, api_key)
        if operation == "fetch_company_posts":
            return await self._fetch_company_posts(params, api_key)
        if operation == "fetch_post_details":
            return await self._fetch_post_details(params, api_key)
        if operation == "fetch_post_reactions":
            return await self._fetch_post_reactions(params, api_key)
        if operation == "fetch_post_comments":
            return await self._fetch_post_comments(params, api_key)
        if operation == "search_posts":
            return await self._search_posts(params, api_key)
        raise ProviderError(
            self.name,
            (
                f"operation '{operation}' is not supported by fresh_linkedin; "
                f"supported: {', '.join(self.supported_operations)}"
            ),
            status_code=400,
        )

    async def health_check(self, api_key: str) -> bool:
        """Light GET against the profile endpoint with a throwaway URL.

        Returns True on any 2xx. 4xx and 5xx are treated as unhealthy — including
        400 on the probe URL, since an unhealthy key often manifests as 401/403
        which we want to surface as "not healthy".
        """
        probe_url = "https://www.linkedin.com/in/linkedin"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{RAPIDAPI_BASE}{ENDPOINT_PROFILE_BY_URL}",
                    headers=self._headers(api_key),
                    params={"linkedin_url": probe_url},
                )
                return 200 <= resp.status_code < 300
        except Exception:
            return False

    async def _get(
        self,
        path: str,
        qs: dict[str, Any],
        api_key: str,
    ) -> "httpx.Response":
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                return await client.get(
                    f"{RAPIDAPI_BASE}{path}",
                    headers=self._headers(api_key),
                    params=qs,
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                self.name,
                f"Upstream request timed out after {_REQUEST_TIMEOUT:.0f}s",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name,
                f"Upstream connection error: {exc}",
                status_code=502,
            ) from exc

    async def _post(
        self,
        path: str,
        json_body: dict[str, Any],
        api_key: str,
    ) -> "httpx.Response":
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                return await client.post(
                    f"{RAPIDAPI_BASE}{path}",
                    headers=self._headers(api_key),
                    json=json_body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                self.name,
                f"Upstream request timed out after {_REQUEST_TIMEOUT:.0f}s",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name,
                f"Upstream connection error: {exc}",
                status_code=502,
            ) from exc

    def _classify_response(self, resp: "httpx.Response") -> dict[str, Any]:
        status = resp.status_code

        if 200 <= status < 300:
            try:
                payload = resp.json()
            except ValueError as exc:
                raise ProviderError(
                    self.name,
                    "Response could not be parsed (non-JSON body)",
                    status_code=502,
                ) from exc
            if payload is None:
                return {"match_found": False, "data": None}
            return payload

        if status == 401:
            raise ProviderError(
                self.name,
                "Authentication failed — key may be invalid or expired",
                status_code=401,
            )
        if status == 403:
            raise ProviderError(
                self.name,
                "Authorization failed — plan or quota issue on the upstream account",
                status_code=403,
            )
        if status == 404:
            raise ProviderError(
                self.name,
                "Profile not found on upstream (404)",
                status_code=404,
            )
        if status == 429:
            raise ProviderError(
                self.name,
                "Upstream rate limit exceeded; retry after ~60s",
                status_code=429,
            )
        if 500 <= status < 600:
            body_snippet = resp.text[:200] if resp.text else ""
            raise ProviderError(
                self.name,
                f"Upstream error ({status}): {body_snippet}",
                status_code=status,
            )

        body_snippet = resp.text[:200] if resp.text else ""
        raise ProviderError(
            self.name,
            f"Unexpected upstream status {status}: {body_snippet}",
            status_code=status,
        )

    # ------------------------------------------------------------------
    # enrich_person
    # ------------------------------------------------------------------

    async def _enrich_person(
        self,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        raw_url = params.get("linkedin_url") or params.get("linkedin")
        if not raw_url:
            raise ProviderError(
                self.name,
                (
                    "fresh_linkedin requires linkedin_url; "
                    f"got keys: {sorted(params.keys())}"
                ),
                status_code=400,
            )

        canonical_url = _normalize_linkedin_profile_url(str(raw_url))
        logger.info("fresh_linkedin enrich_person for %s", canonical_url)
        resp = await self._get(
            ENDPOINT_ENRICH_LEAD,
            {
                "linkedin_url": canonical_url,
                "include_skills": "true",
                "include_certifications": "true",
            },
            api_key,
        )
        return self._classify_response(resp)

    # ------------------------------------------------------------------
    # enrich_company
    # ------------------------------------------------------------------

    async def _enrich_company(
        self,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        linkedin_url = params.get("linkedin_url") or params.get("linkedin")
        domain = params.get("domain")

        if linkedin_url:
            canonical_url = _normalize_linkedin_company_url(str(linkedin_url))
            logger.info("fresh_linkedin enrich_company by URL for %s", canonical_url)
            resp = await self._get(
                ENDPOINT_COMPANY_BY_URL,
                {"linkedin_url": canonical_url},
                api_key,
            )
            return self._classify_response(resp)

        if domain:
            clean_domain = _normalize_domain(str(domain))
            logger.info("fresh_linkedin enrich_company by domain for %s", clean_domain)
            resp = await self._get(
                ENDPOINT_COMPANY_BY_DOMAIN,
                {"domain": clean_domain},
                api_key,
            )
            return self._classify_response(resp)

        raise ProviderError(
            self.name,
            "fresh_linkedin enrich_company requires one of: linkedin_url, domain",
            status_code=400,
        )

    # ------------------------------------------------------------------
    # Post family — shared URN validator
    # ------------------------------------------------------------------

    def _validate_post_urn(self, raw: Any) -> str:
        if not raw or not isinstance(raw, str):
            raise ProviderError(
                self.name,
                "fetch_post_* requires urn (bare activity id)",
                status_code=400,
            )
        stripped = raw.strip()
        if not _POST_URN_RE.match(stripped):
            raise ProviderError(
                self.name,
                (
                    "urn must be the bare activity id from a posts response "
                    f"(e.g. '7450415215956987904'); got '{raw[:80]}'"
                ),
                status_code=400,
            )
        return stripped

    # ------------------------------------------------------------------
    # fetch_profile_posts
    # ------------------------------------------------------------------

    async def _fetch_profile_posts(
        self,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        raw_url = params.get("linkedin_url") or params.get("linkedin")
        if not raw_url:
            raise ProviderError(
                self.name,
                "fetch_profile_posts requires linkedin_url (profile /in/<slug>)",
                status_code=400,
            )
        canonical_url = _normalize_linkedin_profile_url(str(raw_url))

        start = params.get("start")
        token = params.get("pagination_token")
        start_str = _coerce_numeric_string(start, "start") if start is not None and start != "" else None
        _require_paired(start_str, token, ("start", "pagination_token"))

        qs: dict[str, Any] = {"linkedin_url": canonical_url}
        type_val = params.get("type")
        if type_val is not None and type_val != "":
            qs["type"] = str(type_val)
        if start_str:
            qs["start"] = start_str
        if token:
            qs["pagination_token"] = str(token)

        logger.info(
            "fresh_linkedin fetch_profile_posts for %s start=%s token=%s",
            canonical_url, bool(start_str), bool(token),
        )
        resp = await self._get(ENDPOINT_PROFILE_POSTS, qs, api_key)
        return self._classify_response(resp)

    # ------------------------------------------------------------------
    # fetch_company_posts
    # ------------------------------------------------------------------

    async def _fetch_company_posts(
        self,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        raw_url = params.get("linkedin_url") or params.get("linkedin")
        if not raw_url:
            raise ProviderError(
                self.name,
                "fetch_company_posts requires linkedin_url (company /company/<slug>)",
                status_code=400,
            )
        canonical_url = _normalize_linkedin_company_url(str(raw_url))

        start = params.get("start")
        token = params.get("pagination_token")
        start_str = _coerce_numeric_string(start, "start") if start is not None and start != "" else None
        _require_paired(start_str, token, ("start", "pagination_token"))

        qs: dict[str, Any] = {"linkedin_url": canonical_url}
        sort_by = params.get("sort_by")
        if sort_by is not None and sort_by != "":
            qs["sort_by"] = str(sort_by)
        if start_str:
            qs["start"] = start_str
        if token:
            qs["pagination_token"] = str(token)

        logger.info(
            "fresh_linkedin fetch_company_posts for %s start=%s token=%s",
            canonical_url, bool(start_str), bool(token),
        )
        resp = await self._get(ENDPOINT_COMPANY_POSTS, qs, api_key)
        return self._classify_response(resp)

    # ------------------------------------------------------------------
    # fetch_post_details
    # ------------------------------------------------------------------

    async def _fetch_post_details(
        self,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        urn = self._validate_post_urn(params.get("urn"))
        logger.info("fresh_linkedin fetch_post_details for urn=%s", urn)
        resp = await self._get(ENDPOINT_POST_DETAILS, {"urn": urn}, api_key)
        return self._classify_response(resp)

    # ------------------------------------------------------------------
    # fetch_post_reactions
    # ------------------------------------------------------------------

    async def _fetch_post_reactions(
        self,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        urn = self._validate_post_urn(params.get("urn"))
        page = params.get("page")
        page_str = _coerce_numeric_string(page, "page") if page is not None and page != "" else None

        qs: dict[str, Any] = {"urn": urn}
        type_val = params.get("type")
        if type_val is not None and type_val != "":
            qs["type"] = str(type_val)
        if page_str:
            qs["page"] = page_str
        # pagination_token silently ignored — vendor endpoint accepts only `page`.

        logger.info(
            "fresh_linkedin fetch_post_reactions for urn=%s page=%s",
            urn, bool(page_str),
        )
        resp = await self._get(ENDPOINT_POST_REACTIONS, qs, api_key)
        return self._classify_response(resp)

    # ------------------------------------------------------------------
    # fetch_post_comments
    # ------------------------------------------------------------------

    async def _fetch_post_comments(
        self,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        urn = self._validate_post_urn(params.get("urn"))

        page = params.get("page")
        token = params.get("pagination_token")
        page_str = _coerce_numeric_string(page, "page") if page is not None and page != "" else None
        _require_paired(page_str, token, ("page", "pagination_token"))

        qs: dict[str, Any] = {"urn": urn}
        sort_by = params.get("sort_by")
        if sort_by is not None and sort_by != "":
            qs["sort_by"] = str(sort_by)
        if page_str:
            qs["page"] = page_str
        if token:
            qs["pagination_token"] = str(token)

        logger.info(
            "fresh_linkedin fetch_post_comments for urn=%s page=%s token=%s",
            urn, bool(page_str), bool(token),
        )
        resp = await self._get(ENDPOINT_POST_COMMENTS, qs, api_key)
        return self._classify_response(resp)

    # ------------------------------------------------------------------
    # search_posts (POST-bodied upstream)
    # ------------------------------------------------------------------

    def _build_search_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        filtered = {
            k: v for k, v in params.items() if k in _SEARCH_DEFAULT_PAYLOAD
        }
        payload = {**_SEARCH_DEFAULT_PAYLOAD, **filtered}

        for key in _SEARCH_FILTER_LIST_KEYS:
            val = payload.get(key)
            if isinstance(val, str) and val:
                payload[key] = [val]
            elif val is None or val is False:
                payload[key] = []
            elif isinstance(val, list):
                payload[key] = val
            else:
                raise ProviderError(
                    self.name,
                    f"search_posts: '{key}' must be a list of URN strings",
                    status_code=400,
                )

        for key in ("search_keywords", "author_keyword", "sort_by", "date_posted", "content_type"):
            val = payload.get(key)
            if val is None:
                payload[key] = ""
            elif not isinstance(val, str):
                raise ProviderError(
                    self.name,
                    f"search_posts: '{key}' must be a string",
                    status_code=400,
                )

        page = payload.get("page")
        if isinstance(page, str) and page.isdigit():
            page = int(page)
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ProviderError(
                self.name,
                f"search_posts: page must be a positive integer; got {params.get('page')!r}",
                status_code=400,
            )
        payload["page"] = page

        has_any_filter = (
            bool(payload["search_keywords"].strip())
            or bool(payload["author_keyword"].strip())
            or any(payload[k] for k in _SEARCH_FILTER_LIST_KEYS)
        )
        if not has_any_filter:
            raise ProviderError(
                self.name,
                "search_posts requires at least one filter (keyword, author URN, mention, etc.)",
                status_code=400,
            )

        return payload

    async def _search_posts(
        self,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        payload = self._build_search_payload(params)
        logger.info(
            "fresh_linkedin search_posts filters=%s page=%d",
            {
                k: (len(payload[k]) if isinstance(payload[k], list) else bool(payload[k]))
                for k in (*_SEARCH_FILTER_LIST_KEYS, "search_keywords", "author_keyword")
            },
            payload["page"],
        )
        resp = await self._post(ENDPOINT_SEARCH_POSTS, payload, api_key)
        classified = self._classify_response(resp)
        if isinstance(classified, dict) and "__page__" not in classified:
            classified["__page__"] = payload["page"]
        return classified


register_provider("fresh_linkedin", FreshLinkedInProvider)
