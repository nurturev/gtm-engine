"""Response normalisation to nrev-lite schema.

Maps provider-specific response formats to a consistent nrev-lite schema.
Handles all Apollo response types including search results (which return
lists of people/companies) and bulk enrichment results.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from server.core.exceptions import ProviderError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Person normalisation
# ---------------------------------------------------------------------------


def normalize_person(raw: dict[str, Any], provider: str) -> dict[str, Any]:
    """Normalize a person enrichment/search result to the nrev-lite schema.

    Handles both single enrichment responses and search results
    (which contain a list of people).
    """
    if provider == "apollo":
        # Search results have a "people" array
        if "people" in raw and isinstance(raw["people"], list):
            return {
                "people": [_normalize_apollo_person(p) for p in raw["people"]],
                "total": raw.get("pagination", {}).get("total_entries"),
                "page": raw.get("pagination", {}).get("page"),
                "per_page": raw.get("pagination", {}).get("per_page"),
            }

        # Bulk enrichment has a "matches" array
        if "matches" in raw and isinstance(raw["matches"], list):
            return {
                "people": [_normalize_apollo_person(m) for m in raw["matches"]],
                "total": len(raw["matches"]),
            }

        # Single enrichment has a "person" object
        person = raw.get("person", raw)
        if person is None:
            # Apollo returned 200 but no match found
            return {"match_found": False, "people": [], "enrichment_sources": {"apollo": []}}
        return _normalize_apollo_person(person)

    if provider == "rocketreach":
        # No match (provider emits this sentinel for upstream 404 / empty payload,
        # sometimes alongside an empty "profiles" list — check before the search branch)
        if raw.get("match_found") is False:
            return {"match_found": False, "people": [], "enrichment_sources": {"rocketreach": []}}

        # Search results have a "profiles" array. Search rows carry contact
        # hints under teaser.* rather than full emails/phones at the top
        # level — map them with the dedicated search-row helper so hints
        # land in additional_data instead of being dropped.
        if "profiles" in raw and isinstance(raw["profiles"], list):
            pagination = raw.get("pagination") or {}
            return {
                "people": [_normalize_rr_person_search_row(p) for p in raw["profiles"]],
                "total": pagination.get("total", len(raw["profiles"])),
                "page": pagination.get("start", 1),
                "per_page": pagination.get("page_size", 25),
            }

        # Single lookup returns a flat profile
        if raw.get("id") or raw.get("name"):
            return _normalize_rr_person(raw)

        return {"raw": raw, "enrichment_sources": {provider: ["raw"]}}

    if provider == "fresh_linkedin":
        # Provider emits this sentinel for upstream 404 / empty payload.
        if raw.get("match_found") is False:
            return {
                "match_found": False,
                "people": [],
                "enrichment_sources": {"fresh_linkedin": []},
            }
        # Fresh LinkedIn wraps the profile under "data"; unwrap if present.
        profile = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if not isinstance(profile, dict):
            # Unambiguous bug in the upstream payload shape — not best-effort drift.
            raise ProviderError(
                "fresh_linkedin",
                "Response could not be parsed (non-dict top level)",
                status_code=502,
            )
        return _normalize_fresh_linkedin_person(profile)

    # Default pass-through for unknown providers
    return {
        "raw": raw,
        "enrichment_sources": {provider: ["raw"]},
    }


# ---------------------------------------------------------------------------
# Shared person helpers (round-5 canonical + additional_data shape)
# ---------------------------------------------------------------------------

# Canonical Person fields per `unique_entity_fields.csv`. Top-level keys of the
# normalized Person row are strictly this set (plus `enrichment_sources` and
# `additional_data`). Every other field a provider returns lives inside
# `additional_data`.
from server.execution.entity_schemas import (
    COMPANY_PRIMARY_FIELDS,
    PERSON_PRIMARY_FIELDS,
)

CANONICAL_PERSON_KEYS: frozenset[str] = frozenset(PERSON_PRIMARY_FIELDS)
CANONICAL_COMPANY_KEYS: frozenset[str] = frozenset(COMPANY_PRIMARY_FIELDS)


def _nonempty(value: Any) -> bool:
    """A value is 'present' if it's not None, empty string, empty list, or empty dict."""
    return value is not None and value != "" and value != [] and value != {}


def _build_person_row(
    canonical: dict[str, Any],
    extras: dict[str, Any],
    sources: dict[str, list[str]],
) -> dict[str, Any]:
    """Assemble a normalized Person row in the canonical + additional_data shape.

    - `canonical` must be keyed by `CANONICAL_PERSON_KEYS`. Empty values are dropped.
      Any non-canonical key accidentally passed here is routed to `additional_data`
      as a safety net; provider mappers should not rely on this.
    - `extras` lands inside `additional_data`. Empty values are dropped.
    - `sources` populates `enrichment_sources` verbatim.

    The returned dict always has `enrichment_sources` and `additional_data` keys,
    even when the latter is empty — consumers read either without guarding.
    """
    row: dict[str, Any] = {}
    merged_extras: dict[str, Any] = {}

    for key, val in canonical.items():
        if not _nonempty(val):
            continue
        if key in CANONICAL_PERSON_KEYS:
            row[key] = val
        else:
            merged_extras[key] = val

    for key, val in extras.items():
        if _nonempty(val):
            merged_extras[key] = val

    row["enrichment_sources"] = sources
    row["additional_data"] = merged_extras
    return row


def _safe_extract(field_name: str, fn: Callable[[], Any], log_prefix: str = "normalizer") -> Any:
    """Best-effort helper: run *fn*, log and skip on any exception.

    Used by the best-effort normalizer path (grooming D4 / T04). Returns None on
    failure so the caller can choose to omit the field from the output row.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "[%s] field '%s' skipped: %s: %s",
            log_prefix, field_name, type(exc).__name__, exc,
        )
        return None


def _coerce_int(value: Any) -> int | None:
    """Coerce count-like vendor shapes (int, float, '1,234', '500+', '', None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip().rstrip("+").replace(",", "")
        if stripped.isdigit():
            return int(stripped)
    return None


def _string_or_none(value: Any) -> str | None:
    """Coerce a field that should be a string; empty strings → missing."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    s = str(value)
    return s or None


def _map_list(
    raw_list: Any,
    mapper: Callable[[dict[str, Any]], dict[str, Any] | None],
    log_prefix: str = "normalizer",
) -> list[dict[str, Any]]:
    """Apply *mapper* to each entry in *raw_list*, dropping failed / empty entries."""
    if not isinstance(raw_list, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw_list:
        try:
            mapped = mapper(entry)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[%s] list-item skipped in %s: %s: %s",
                log_prefix, mapper.__name__, type(exc).__name__, exc,
            )
            continue
        if mapped:
            out.append(mapped)
    return out


def _flatten_string_list(raw_list: Any) -> list[str]:
    """Normalize `skills` / `languages`-style fields to `list[str]`."""
    if not isinstance(raw_list, list):
        return []
    out: list[str] = []
    for item in raw_list:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
        elif isinstance(item, dict):
            s = _string_or_none(item.get("name") or item.get("title"))
            if s:
                out.append(s)
    return out


# Generic nested-item mappers. `Education`, `Certification`, `Post` always live
# inside `additional_data`; their shapes match the TypedDicts in
# `server/execution/person_types.py`.


def _map_education_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    out: dict[str, Any] = {}
    for our_key, *candidates in (
        ("school", "school", "school_name", "institution"),
        ("degree", "degree", "degree_name"),
        ("field_of_study", "field_of_study", "field", "study_field"),
    ):
        for key in candidates:
            val = _string_or_none(item.get(key))
            if val:
                out[our_key] = val
                break
    start_year = _coerce_int(item.get("start_year") or item.get("starts_at"))
    if start_year is not None:
        out["start_year"] = start_year
    end_year = _coerce_int(item.get("end_year") or item.get("ends_at"))
    if end_year is not None:
        out["end_year"] = end_year
    return out or None


def _map_certification_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    out: dict[str, Any] = {}
    for our_key, *candidates in (
        ("name", "name", "certification_name", "title"),
        ("issuer", "issuer", "authority", "organization"),
    ):
        for key in candidates:
            val = _string_or_none(item.get(key))
            if val:
                out[our_key] = val
                break
    year = _coerce_int(item.get("year") or item.get("issue_year") or item.get("issued_at"))
    if year is not None:
        out["year"] = year
    return out or None


def _map_post_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    out: dict[str, Any] = {}
    url = _string_or_none(item.get("url") or item.get("post_url") or item.get("link"))
    if url:
        out["url"] = url
    text = _string_or_none(item.get("text") or item.get("text_snippet") or item.get("content"))
    if text:
        out["text_snippet"] = text
    ts = _string_or_none(item.get("posted_at") or item.get("date") or item.get("published_at"))
    if ts:
        out["posted_at"] = ts
    return out or None


# ---------------------------------------------------------------------------
# RocketReach person normalisation (round-5: canonical + additional_data)
# ---------------------------------------------------------------------------


def _normalize_rr_person(person: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single RocketReach person object.

    Retrofitted round 5: canonical Person keys at top level; all other fields
    (`id`, `photo_url`, `city`, `state`, `country`, `skills`, `lookup_status`)
    relocated under `additional_data`.
    """
    # Best email (prefer A/A- grades, fallback to first, fallback to recommended).
    emails = person.get("emails") or []
    primary_email: str | None = None
    if isinstance(emails, list):
        for e in emails:
            if isinstance(e, dict):
                grade = (e.get("grade") or "").upper()
                if grade in ("A", "A-"):
                    primary_email = e.get("email")
                    break
        if not primary_email:
            for e in emails:
                if isinstance(e, dict) and e.get("email"):
                    primary_email = e["email"]
                    break
                if isinstance(e, str) and e:
                    primary_email = e
                    break
    if not primary_email and person.get("recommended_email"):
        primary_email = person["recommended_email"]

    # Best phone (prefer recommended, fallback to first).
    phones = person.get("phones") or []
    primary_phone: str | None = None
    if isinstance(phones, list):
        for ph in phones:
            if isinstance(ph, dict) and ph.get("recommended"):
                primary_phone = ph.get("number")
                break
        if not primary_phone:
            for ph in phones:
                if isinstance(ph, dict) and ph.get("number"):
                    primary_phone = ph["number"]
                    break
                if isinstance(ph, str) and ph:
                    primary_phone = ph
                    break

    # Name split.
    name = person.get("name") or ""
    parts = name.split(None, 1) if name else []
    first_name = parts[0] if len(parts) >= 1 else person.get("first_name")
    last_name = parts[1] if len(parts) >= 2 else person.get("last_name")

    # Location string (composite from parts, fallback to flat `location`).
    loc_parts = [
        person.get("city"),
        person.get("region"),
        person.get("country_code"),
    ]
    location = ", ".join(p for p in loc_parts if p) or person.get("location") or None

    canonical = {
        "name": name or None,
        "first_name": first_name,
        "last_name": last_name,
        "title": person.get("current_title"),
        "headline": None,
        "experiences": None,
        "linkedin_url": person.get("linkedin_url"),
        "email": primary_email,
        "phone": primary_phone,
        "location": location,
        "company_name": person.get("current_employer"),
        "company_domain": person.get("current_employer_domain"),
    }
    populated = [k for k in CANONICAL_PERSON_KEYS if _nonempty(canonical.get(k))]

    extras: dict[str, Any] = {
        "id": person.get("id"),
        "photo_url": person.get("profile_pic"),
        "city": person.get("city"),
        "state": person.get("region"),
        "country": person.get("country_code"),
        "skills": person.get("skills"),
    }

    # Async lookup flag / explicit status.
    if person.get("_async_in_progress"):
        extras["lookup_status"] = "in_progress"
    status = person.get("status")
    if status and status != "complete":
        extras["lookup_status"] = status

    row = _build_person_row(canonical, extras, {"rocketreach": populated})

    # Elevate in-progress signals to the top level so service.py can gate
    # billing + cache-write on them without drilling into additional_data.
    if person.get("lookup_status") == "in_progress":
        row["lookup_status"] = "in_progress"
        retry_hint = person.get("retry_hint")
        if isinstance(retry_hint, dict):
            row["retry_hint"] = retry_hint

    return row


def _normalize_rr_person_search_row(profile: dict[str, Any]) -> dict[str, Any]:
    """Normalize a RocketReach person-search row.

    Search rows never carry full emails/phones — only domain hints under
    `teaser.*` and a masked first phone. Canonical `email` / `phone` stay
    `None`; the hints land in `additional_data` so downstream agents can
    decide whether to chain a lookup.
    """
    name = profile.get("name") or ""
    parts = name.split(None, 1) if name else []
    first_name = parts[0] if len(parts) >= 1 else profile.get("first_name")
    last_name = parts[1] if len(parts) >= 2 else profile.get("last_name")

    loc_parts = [
        profile.get("city"),
        profile.get("region"),
        profile.get("country_code"),
    ]
    location = ", ".join(p for p in loc_parts if p) or profile.get("location") or None

    canonical = {
        "name": name or None,
        "first_name": first_name,
        "last_name": last_name,
        "title": profile.get("current_title"),
        "headline": None,
        "experiences": None,
        "linkedin_url": profile.get("linkedin_url"),
        "email": None,
        "phone": None,
        "location": location,
        "company_name": profile.get("current_employer"),
        "company_domain": profile.get("current_employer_domain"),
    }
    populated = [k for k in CANONICAL_PERSON_KEYS if _nonempty(canonical.get(k))]

    teaser = profile.get("teaser") or {}
    email_domain_hints: list[str] = []
    if isinstance(teaser, dict):
        for key in ("professional_emails", "emails"):
            raw_hints = teaser.get(key)
            if isinstance(raw_hints, list):
                for hint in raw_hints:
                    if isinstance(hint, str) and hint and hint not in email_domain_hints:
                        email_domain_hints.append(hint)
                if email_domain_hints:
                    break

    phone_hint: str | None = None
    if isinstance(teaser, dict):
        raw_phones = teaser.get("phones")
        if isinstance(raw_phones, list):
            for entry in raw_phones:
                if isinstance(entry, dict):
                    candidate = entry.get("number")
                    if isinstance(candidate, str) and candidate:
                        phone_hint = candidate
                        break
                elif isinstance(entry, str) and entry:
                    phone_hint = entry
                    break

    is_premium_phone_available = None
    if isinstance(teaser, dict):
        raw_flag = teaser.get("is_premium_phone_available")
        if isinstance(raw_flag, bool):
            is_premium_phone_available = raw_flag

    extras: dict[str, Any] = {
        "id": profile.get("id"),
        "photo_url": profile.get("profile_pic"),
        "city": profile.get("city"),
        "state": profile.get("region"),
        "country": profile.get("country_code"),
        "connections": profile.get("connections"),
        "email_domain_hints": email_domain_hints or None,
        "phone_hint": phone_hint,
        "is_premium_phone_available": is_premium_phone_available,
    }

    return _build_person_row(canonical, extras, {"rocketreach": populated})


# ---------------------------------------------------------------------------
# Fresh LinkedIn person normalisation (round-5: canonical + additional_data)
# ---------------------------------------------------------------------------


def _fl_build_date(month: Any, year: Any) -> str | None:
    """Build an ISO-style date string from Fresh LinkedIn's month+year integer fields.

    - Both month and year present (and int-coercible) → "YYYY-MM".
    - Only year → "YYYY".
    - Neither → None.
    Empty strings in the vendor payload are treated as missing (via `_coerce_int`).
    """
    y = _coerce_int(year)
    m = _coerce_int(month)
    if y is None:
        return None
    if m is None:
        return f"{y:04d}"
    return f"{y:04d}-{m:02d}"


def _map_fl_experience_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Shape a Fresh LinkedIn experience entry to the canonical `Experience` TypedDict.

    Vendor uses separate integer `start_month` / `start_year` (and `end_*`) fields
    plus a string `is_current` marker — we reconstruct ISO-style `start_date` /
    `end_date` from those. Honors `is_current` to avoid emitting an end_date for
    the current role even if the vendor ships stale values.
    """
    if not isinstance(item, dict):
        return None
    out: dict[str, Any] = {}

    company = _string_or_none(item.get("company"))
    if company:
        out["company"] = company
    title = _string_or_none(item.get("title"))
    if title:
        out["title"] = title

    start_date = _fl_build_date(item.get("start_month"), item.get("start_year"))
    if start_date:
        out["start_date"] = start_date

    is_current = str(item.get("is_current", "")).strip().lower() == "true"
    if not is_current:
        end_date = _fl_build_date(item.get("end_month"), item.get("end_year"))
        if end_date:
            out["end_date"] = end_date

    description = _string_or_none(item.get("description"))
    if description:
        out["description"] = description
    location = _string_or_none(item.get("location"))
    if location:
        out["location"] = location
    return out or None


def _normalize_fresh_linkedin_person(profile: dict[str, Any]) -> dict[str, Any]:
    """Map a Fresh LinkedIn profile payload onto the canonical + additional_data shape.

    Canonical top-level keys (per `unique_entity_fields.csv`) populated where the
    vendor ships them; every other field lands inside `additional_data`. Best-effort
    per grooming D4 — per-field extraction wrapped in try/except, INFO-logged on
    failure, dropped from the output row.
    """

    log_prefix = "fresh_linkedin"

    # ── Canonical fields ───────────────────────────────────────────────
    canonical: dict[str, Any] = {}

    canonical["name"] = _safe_extract(
        "name",
        lambda: _string_or_none(profile.get("full_name") or profile.get("name")),
        log_prefix,
    )
    canonical["first_name"] = _safe_extract(
        "first_name", lambda: _string_or_none(profile.get("first_name")), log_prefix,
    )
    canonical["last_name"] = _safe_extract(
        "last_name", lambda: _string_or_none(profile.get("last_name")), log_prefix,
    )
    canonical["title"] = _safe_extract(
        "title",
        lambda: _string_or_none(profile.get("job_title") or profile.get("title")),
        log_prefix,
    )
    canonical["headline"] = _safe_extract(
        "headline",
        lambda: _string_or_none(profile.get("headline") or profile.get("sub_title")),
        log_prefix,
    )
    canonical["experiences"] = _safe_extract(
        "experiences",
        lambda: _map_list(
            profile.get("experiences") or profile.get("experience"),
            _map_fl_experience_item,
            log_prefix,
        ),
        log_prefix,
    )
    canonical["linkedin_url"] = _safe_extract(
        "linkedin_url",
        lambda: _string_or_none(profile.get("linkedin_url") or profile.get("profile_url")),
        log_prefix,
    )
    canonical["email"] = _safe_extract(
        "email", lambda: _string_or_none(profile.get("email")), log_prefix,
    )
    canonical["phone"] = _safe_extract(
        "phone", lambda: _string_or_none(profile.get("phone")), log_prefix,
    )
    canonical["location"] = _safe_extract(
        "location",
        lambda: _string_or_none(
            profile.get("location_string")
            or profile.get("full_location")
            or (profile.get("location") if isinstance(profile.get("location"), str) else None)
        ),
        log_prefix,
    )
    canonical["company_name"] = _safe_extract(
        "company_name", lambda: _string_or_none(profile.get("company")), log_prefix,
    )
    canonical["company_domain"] = _safe_extract(
        "company_domain", lambda: _string_or_none(profile.get("company_domain")), log_prefix,
    )

    populated = [k for k in CANONICAL_PERSON_KEYS if _nonempty(canonical.get(k))]

    # ── Extras → additional_data ───────────────────────────────────────
    extras: dict[str, Any] = {}

    extras["about"] = _safe_extract(
        "about",
        lambda: _string_or_none(profile.get("about") or profile.get("summary")),
        log_prefix,
    )
    extras["photo_url"] = _safe_extract(
        "photo_url",
        lambda: _string_or_none(
            profile.get("profile_picture_url")
            or profile.get("profile_image_url")
            or profile.get("photo_url")
        ),
        log_prefix,
    )

    # Location breakdown (flat vendor keys; `location` itself is canonical above).
    extras["city"] = _safe_extract(
        "city", lambda: _string_or_none(profile.get("city")), log_prefix,
    )
    extras["state"] = _safe_extract(
        "state", lambda: _string_or_none(profile.get("state")), log_prefix,
    )
    extras["country"] = _safe_extract(
        "country", lambda: _string_or_none(profile.get("country")), log_prefix,
    )

    # Counts — vendor uses `connection_count` (singular) for connections.
    extras["connections_count"] = _safe_extract(
        "connections_count",
        lambda: _coerce_int(
            profile.get("connection_count")
            or profile.get("connections_count")
            or profile.get("connections")
        ),
        log_prefix,
    )
    extras["follower_count"] = _safe_extract(
        "follower_count",
        lambda: _coerce_int(profile.get("follower_count") or profile.get("followers_count")),
        log_prefix,
    )

    # Nested lists.
    extras["education"] = _safe_extract(
        "education",
        lambda: _map_list(
            profile.get("educations") or profile.get("education"),
            _map_education_item,
            log_prefix,
        ),
        log_prefix,
    )
    extras["certifications"] = _safe_extract(
        "certifications",
        lambda: _map_list(profile.get("certifications"), _map_certification_item, log_prefix),
        log_prefix,
    )
    extras["recent_posts"] = _safe_extract(
        "recent_posts",
        lambda: _map_list(
            profile.get("recent_posts") or profile.get("posts"),
            _map_post_item,
            log_prefix,
        ),
        log_prefix,
    )

    # Scalar lists.
    extras["skills"] = _safe_extract(
        "skills", lambda: _flatten_string_list(profile.get("skills")), log_prefix,
    )
    extras["languages"] = _safe_extract(
        "languages", lambda: _flatten_string_list(profile.get("languages")), log_prefix,
    )

    # Company metadata (flat vendor keys — canonical `company` + `company_domain`
    # already populated above; these are the richer-than-canonical bits).
    extras["company_industry"] = _safe_extract(
        "company_industry", lambda: _string_or_none(profile.get("company_industry")), log_prefix,
    )
    extras["company_size"] = _safe_extract(
        "company_size", lambda: _coerce_int(profile.get("company_employee_count")), log_prefix,
    )
    extras["company_employee_range"] = _safe_extract(
        "company_employee_range",
        lambda: _string_or_none(profile.get("company_employee_range")),
        log_prefix,
    )
    extras["company_description"] = _safe_extract(
        "company_description",
        lambda: _string_or_none(profile.get("company_description")),
        log_prefix,
    )
    extras["company_website"] = _safe_extract(
        "company_website", lambda: _string_or_none(profile.get("company_website")), log_prefix,
    )
    extras["company_year_founded"] = _safe_extract(
        "company_year_founded", lambda: _coerce_int(profile.get("company_year_founded")), log_prefix,
    )
    extras["company_logo_url"] = _safe_extract(
        "company_logo_url", lambda: _string_or_none(profile.get("company_logo_url")), log_prefix,
    )
    extras["company_linkedin"] = _safe_extract(
        "company_linkedin",
        lambda: _string_or_none(profile.get("company_linkedin_url")),
        log_prefix,
    )

    # Company HQ location.
    extras["company_hq_city"] = _safe_extract(
        "company_hq_city", lambda: _string_or_none(profile.get("hq_city")), log_prefix,
    )
    extras["company_hq_state"] = _safe_extract(
        "company_hq_state", lambda: _string_or_none(profile.get("hq_region")), log_prefix,
    )
    extras["company_hq_country"] = _safe_extract(
        "company_hq_country", lambda: _string_or_none(profile.get("hq_country")), log_prefix,
    )

    # LinkedIn identifiers — grouped into a single sub-dict for dedup use cases.
    identifiers = _safe_extract(
        "linkedin_identifiers",
        lambda: {
            k: v for k, v in (
                ("profile_id", _string_or_none(profile.get("profile_id"))),
                ("public_id", _string_or_none(profile.get("public_id"))),
                ("urn", _string_or_none(profile.get("urn"))),
            ) if v
        },
        log_prefix,
    )
    if identifiers:
        extras["linkedin_identifiers"] = identifiers

    return _build_person_row(canonical, extras, {"fresh_linkedin": populated})


def _map_apollo_experience_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Shape one Apollo `employment_history` entry to the canonical `Experience` TypedDict.

    Apollo ships ISO date strings (`start_date`, `end_date`) and a `current: bool`
    flag. Honors `current` — omits `end_date` for the current role even if the
    vendor shipped a stale value.
    """
    if not isinstance(item, dict):
        return None
    out: dict[str, Any] = {}
    company = _string_or_none(item.get("organization_name") or item.get("company"))
    if company:
        out["company"] = company
    title = _string_or_none(item.get("title"))
    if title:
        out["title"] = title
    start = _string_or_none(item.get("start_date"))
    if start:
        out["start_date"] = start
    if not item.get("current"):
        end = _string_or_none(item.get("end_date"))
        if end:
            out["end_date"] = end
    description = _string_or_none(item.get("description"))
    if description:
        out["description"] = description
    return out or None


def _normalize_apollo_person(person: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single Apollo person object.

    Retrofitted round 5: canonical Person keys at top level; non-canonical fields
    (`id`, `photo_url`, `seniority`, `departments`, `city`, `state`, `country`,
    `company_industry`, `company_size`) relocated under `additional_data`.
    Apollo's `employment_history` is mapped into canonical `experiences` when present.
    """
    org = person.get("organization") or {}
    if not isinstance(org, dict):
        org = {}

    # Phone: Apollo ships an array of `{sanitized_number, raw_number, ...}` dicts;
    # fall back to the flat `phone_number` if the array is absent.
    phones = person.get("phone_numbers") or []
    primary_phone: str | None = None
    if isinstance(phones, list) and phones:
        for phone in phones:
            if isinstance(phone, dict):
                primary_phone = phone.get("sanitized_number") or phone.get("raw_number")
                if primary_phone:
                    break
    if not primary_phone:
        primary_phone = person.get("phone_number")

    experiences = _map_list(
        person.get("employment_history"), _map_apollo_experience_item, "apollo",
    )

    canonical = {
        "name": person.get("name"),
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name"),
        "title": person.get("title"),
        "headline": person.get("headline"),
        "experiences": experiences,
        "linkedin_url": person.get("linkedin_url"),
        "email": person.get("email"),
        "phone": primary_phone,
        "location": _build_location(person),
        "company_name": org.get("name"),
        "company_domain": org.get("primary_domain"),
    }
    populated = [k for k in CANONICAL_PERSON_KEYS if _nonempty(canonical.get(k))]

    extras: dict[str, Any] = {
        "id": person.get("id"),
        "photo_url": person.get("photo_url"),
        "seniority": person.get("seniority"),
        "departments": person.get("departments"),
        "city": person.get("city"),
        "state": person.get("state"),
        "country": person.get("country"),
        "company_industry": org.get("industry"),
        "company_size": org.get("estimated_num_employees"),
    }

    return _build_person_row(canonical, extras, {"apollo": populated})


# ---------------------------------------------------------------------------
# Company normalisation
# ---------------------------------------------------------------------------


def normalize_company(raw: dict[str, Any], provider: str) -> dict[str, Any]:
    """Normalize a company enrichment/search result to the nrev-lite schema."""
    if provider == "apollo":
        if "organizations" in raw and isinstance(raw["organizations"], list):
            return {
                "companies": [_normalize_apollo_company(o) for o in raw["organizations"]],
                "total": raw.get("pagination", {}).get("total_entries"),
                "page": raw.get("pagination", {}).get("page"),
                "per_page": raw.get("pagination", {}).get("per_page"),
            }

        org = raw.get("organization", raw)
        if org is None:
            return {"match_found": False, "companies": [], "enrichment_sources": {"apollo": []}}
        return _normalize_apollo_company(org)

    if provider == "rocketreach":
        if raw.get("match_found") is False:
            return {"match_found": False, "companies": [], "enrichment_sources": {"rocketreach": []}}

        if "companies" in raw and isinstance(raw["companies"], list):
            pagination = raw.get("pagination") or {}
            # Legacy v2 search returns {total, thisPage, nextPage, pageSize}.
            # Universal returns {start, next, total}. Read either shape.
            return {
                "companies": [_normalize_rr_company(c) for c in raw["companies"]],
                "total": pagination.get("total", len(raw["companies"])),
                "page": pagination.get("start") or pagination.get("thisPage") or 1,
                "per_page": pagination.get("page_size") or pagination.get("pageSize") or 25,
            }
        if raw.get("id") or raw.get("name"):
            return _normalize_rr_company(raw)
        return {"raw": raw, "enrichment_sources": {provider: ["raw"]}}

    if provider == "fresh_linkedin":
        if raw.get("match_found") is False:
            return {
                "match_found": False,
                "companies": [],
                "enrichment_sources": {"fresh_linkedin": []},
            }
        profile = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if not isinstance(profile, dict):
            raise ProviderError(
                "fresh_linkedin",
                "Response could not be parsed (non-dict top level)",
                status_code=502,
            )
        return _normalize_fresh_linkedin_company(profile, top_level=raw)

    if provider == "predictleads":
        return _normalize_predictleads_company(raw)

    return {
        "raw": raw,
        "enrichment_sources": {provider: ["raw"]},
    }


def _build_company_row(
    canonical: dict[str, Any],
    extras: dict[str, Any],
    sources: dict[str, list[str]],
) -> dict[str, Any]:
    """Assemble a normalized Company row — canonical + additional_data shape."""
    row: dict[str, Any] = {}
    merged_extras: dict[str, Any] = {}

    for key, val in canonical.items():
        if not _nonempty(val):
            continue
        if key in CANONICAL_COMPANY_KEYS:
            row[key] = val
        else:
            merged_extras[key] = val

    for key, val in extras.items():
        if _nonempty(val):
            merged_extras[key] = val

    row["enrichment_sources"] = sources
    row["additional_data"] = merged_extras
    return row


def _normalize_rr_company(company: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single RocketReach company object — canonical + additional_data."""
    loc_parts = [
        company.get("city"),
        company.get("region"),
        company.get("country_code"),
    ]
    hq_location = ", ".join(p for p in loc_parts if p) or None

    # Universal renames several v2 keys: email_domain→domain,
    # industry_str→industry, num_employees→employees, ticker_symbol→ticker,
    # website_url→website. Read both so the normalizer works against either
    # vendor shape.
    canonical = {
        "name": company.get("name"),
        "domain": company.get("domain") or company.get("email_domain"),
        "linkedin_url": company.get("linkedin_url"),
        "employee_count": company.get("employees") or company.get("num_employees"),
        "industry": company.get("industry") or company.get("industry_str"),
        "hq_location": hq_location,
    }
    populated = [k for k in CANONICAL_COMPANY_KEYS if _nonempty(canonical.get(k))]

    extras: dict[str, Any] = {
        "id": company.get("id"),
        "website": company.get("website") or company.get("website_url"),
        "description": company.get("description"),
        "city": company.get("city"),
        "state": company.get("region"),
        "country": company.get("country_code"),
        "phone": company.get("phone"),
        "logo_url": company.get("logo_url"),
        "ticker": company.get("ticker") or company.get("ticker_symbol"),
        "revenue": company.get("revenue"),
    }

    return _build_company_row(canonical, extras, {"rocketreach": populated})


def _normalize_apollo_company(org: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single Apollo organization — canonical + additional_data."""
    canonical = {
        "name": org.get("name"),
        "domain": org.get("primary_domain") or org.get("website_url"),
        "linkedin_url": org.get("linkedin_url"),
        "employee_count": org.get("estimated_num_employees"),
        "industry": org.get("industry"),
        "hq_location": org.get("raw_address") or _compose_hq_location(
            org.get("city"), org.get("state"), org.get("country"),
        ),
    }
    populated = [k for k in CANONICAL_COMPANY_KEYS if _nonempty(canonical.get(k))]

    extras: dict[str, Any] = {
        "id": org.get("id"),
        "website": org.get("website_url"),
        "annual_revenue": org.get("annual_revenue"),
        "founded_year": org.get("founded_year"),
        "description": org.get("short_description"),
        "city": org.get("city"),
        "state": org.get("state"),
        "country": org.get("country"),
        "phone": org.get("phone"),
        "logo_url": org.get("logo_url"),
        "keywords": org.get("keywords"),
        "technologies": org.get("technologies"),
        "funding_total": org.get("total_funding"),
        "latest_funding_round": org.get("latest_funding_round_type"),
    }

    return _build_company_row(canonical, extras, {"apollo": populated})


def _compose_hq_location(city: Any, state: Any, country: Any) -> str | None:
    parts = [p for p in (city, state, country) if p]
    return ", ".join(parts) if parts else None


def _normalize_fresh_linkedin_company(
    data: dict[str, Any],
    top_level: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a Fresh LinkedIn company payload — canonical + additional_data.

    `data` is the inner object under `response.data`. `top_level` carries fields
    the vendor places outside `data` (e.g. `confident_score`); those land in
    `additional_data` unchanged.
    """
    log_prefix = "fresh_linkedin"

    hq_full = _string_or_none(data.get("hq_full_address"))
    if not hq_full:
        hq_full = _compose_hq_location(
            data.get("hq_city"), data.get("hq_region"), data.get("hq_country"),
        )
    industries = data.get("industries")
    industry = None
    if isinstance(industries, list) and industries:
        industry = _string_or_none(industries[0])

    canonical = {
        "name": _safe_extract(
            "name", lambda: _string_or_none(data.get("company_name")), log_prefix,
        ),
        "domain": _safe_extract(
            "domain", lambda: _string_or_none(data.get("domain")), log_prefix,
        ),
        "linkedin_url": _safe_extract(
            "linkedin_url", lambda: _string_or_none(data.get("linkedin_url")), log_prefix,
        ),
        "employee_count": _safe_extract(
            "employee_count", lambda: _coerce_int(data.get("employee_count")), log_prefix,
        ),
        "industry": industry,
        "hq_location": hq_full,
    }
    populated = [k for k in CANONICAL_COMPANY_KEYS if _nonempty(canonical.get(k))]

    extras: dict[str, Any] = {
        "company_id": data.get("company_id"),
        "description": data.get("description"),
        "employee_range": data.get("employee_range"),
        "follower_count": data.get("follower_count"),
        "specialties": data.get("specialties"),
        "tagline": data.get("tagline"),
        "year_founded": data.get("year_founded"),
        "logo_url": data.get("logo_url"),
        "phone": data.get("phone"),
        "email": data.get("email"),
        "website": data.get("website"),
        "locations": data.get("locations"),
        "affiliated_companies": data.get("affiliated_companies"),
        "funding_info": data.get("funding_info"),
        "hq_city": data.get("hq_city"),
        "hq_region": data.get("hq_region"),
        "hq_country": data.get("hq_country"),
        "hq_postalcode": data.get("hq_postalcode"),
        "hq_address_line1": data.get("hq_address_line1"),
        "hq_address_line2": data.get("hq_address_line2"),
        "industries": industries if isinstance(industries, list) else None,
    }
    if isinstance(top_level, dict):
        confident_score = top_level.get("confident_score")
        if confident_score:
            extras["confident_score"] = confident_score

    return _build_company_row(canonical, extras, {"fresh_linkedin": populated})


# ---------------------------------------------------------------------------
# Post normalisation (Fresh LinkedIn — Phase 2.2 / 2.4)
# ---------------------------------------------------------------------------


def normalize_post(raw: dict[str, Any], provider: str, operation: str) -> dict[str, Any]:
    """Dispatch to the per-provider / per-operation post normalizer."""
    if provider == "fresh_linkedin":
        if operation in ("fetch_profile_posts", "fetch_company_posts"):
            return _normalize_fresh_linkedin_post_list(raw, operation)
        if operation == "fetch_post_details":
            return _normalize_fresh_linkedin_post_details(raw)
        if operation == "fetch_post_reactions":
            return _normalize_fresh_linkedin_reactions(raw)
        if operation == "fetch_post_comments":
            return _normalize_fresh_linkedin_comments(raw)
        if operation == "search_posts":
            return _normalize_fresh_linkedin_search_posts(raw)
    return {"raw": raw, "enrichment_sources": {provider: ["raw"]}}


def _normalize_person_snippet(raw: dict[str, Any], include_image: bool) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    name = _string_or_none(raw.get("name"))
    if name:
        out["name"] = name
    headline = _string_or_none(raw.get("headline"))
    if headline:
        out["headline"] = headline
    linkedin_url = _string_or_none(raw.get("linkedin_url"))
    if linkedin_url:
        out["linkedin_url"] = linkedin_url
    urn = _string_or_none(raw.get("urn"))
    if urn:
        out["urn"] = urn
    if include_image:
        image_url = _string_or_none(raw.get("image_url"))
        if image_url:
            out["image_url"] = image_url
    return out or None


def _normalize_fresh_linkedin_reaction_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    reactor = _normalize_person_snippet(raw.get("reactor") or {}, include_image=False)
    reaction: dict[str, Any] = {}
    type_val = _string_or_none(raw.get("type"))
    if type_val:
        reaction["type"] = type_val
    if reactor:
        reaction["reactor"] = reactor

    extras: dict[str, Any] = {}
    for key, val in raw.items():
        if key in ("type", "reactor"):
            continue
        if val in (None, "", [], {}):
            continue
        extras[key] = val
    if extras:
        reaction["additional_data"] = extras

    if "reactor" not in reaction and "type" not in reaction:
        return None
    return reaction


def _normalize_fresh_linkedin_comment_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    commenter = _normalize_person_snippet(raw.get("commenter") or {}, include_image=True)
    comment: dict[str, Any] = {}

    text = _string_or_none(raw.get("text"))
    if text:
        comment["text"] = text
    created_at = _string_or_none(raw.get("created_datetime"))
    if created_at:
        comment["created_at"] = created_at
    if commenter:
        comment["commenter"] = commenter
    if "pinned" in raw and raw["pinned"] is not None:
        comment["pinned"] = bool(raw["pinned"])
    replies = raw.get("replies")
    if isinstance(replies, list):
        comment["reply_count"] = len(replies)
    else:
        comment["reply_count"] = 0

    extras: dict[str, Any] = {}
    promoted = {"text", "created_datetime", "commenter", "pinned"}
    for key, val in raw.items():
        if key in promoted:
            continue
        if val in (None, "", [], {}):
            continue
        extras[key] = val
    if extras:
        comment["additional_data"] = extras

    if "text" not in comment and "commenter" not in comment:
        return None
    return comment


def _normalize_fresh_linkedin_reactions(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("match_found") is False:
        return {
            "reactions": [],
            "total": 0,
            "cursor": None,
            "enrichment_sources": {"fresh_linkedin": []},
        }
    data = raw.get("data") if isinstance(raw.get("data"), list) else []
    reactions: list[dict[str, Any]] = []
    for item in data:
        try:
            r = _normalize_fresh_linkedin_reaction_item(item)
        except Exception as exc:  # noqa: BLE001
            logger.info("[fresh_linkedin] reaction skipped: %s: %s", type(exc).__name__, exc)
            continue
        if r:
            reactions.append(r)

    total = raw.get("total")
    if not isinstance(total, int):
        total = len(reactions)
    cursor = (
        raw.get("cursor")
        or raw.get("next_cursor")
        or raw.get("pagination_token")
        or None
    )
    return {
        "reactions": reactions,
        "total": total,
        "cursor": cursor,
        "enrichment_sources": {"fresh_linkedin": ["reactions"] if reactions else []},
    }


def _normalize_fresh_linkedin_comments(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("match_found") is False:
        return {
            "comments": [],
            "total": 0,
            "cursor": None,
            "enrichment_sources": {"fresh_linkedin": []},
        }
    data = raw.get("data") if isinstance(raw.get("data"), list) else []
    comments: list[dict[str, Any]] = []
    for item in data:
        try:
            c = _normalize_fresh_linkedin_comment_item(item)
        except Exception as exc:  # noqa: BLE001
            logger.info("[fresh_linkedin] comment skipped: %s: %s", type(exc).__name__, exc)
            continue
        if c:
            comments.append(c)

    total = raw.get("total")
    if not isinstance(total, int):
        total = len(comments)
    cursor = raw.get("pagination_token") or raw.get("cursor") or None
    return {
        "comments": comments,
        "total": total,
        "cursor": cursor,
        "enrichment_sources": {"fresh_linkedin": ["comments"] if comments else []},
    }


# ---------------------------------------------------------------------------
# search_posts (Phase 2.4) — coalesces vendor shape delta into canonical Post
# ---------------------------------------------------------------------------


_SEARCH_POST_PROMOTED_KEYS: frozenset[str] = frozenset({
    "urn", "post_url", "url", "posted", "text",
    "poster_name", "poster_linkedin_url", "poster_title", "poster_type",
    "num_likes", "num_comments", "num_reactions", "num_shares",
    "images", "reshared", "original_post",
})


def _coalesce_search_post_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    urn = _string_or_none(raw.get("urn"))
    if not urn:
        logger.info("[fresh_linkedin] search_posts item skipped: missing urn")
        return None

    post: dict[str, Any] = {"urn": urn}

    post_url = _string_or_none(raw.get("post_url") or raw.get("url"))
    if post_url:
        post["post_url"] = post_url

    posted_at = _string_or_none(raw.get("posted"))
    if posted_at:
        post["posted_at"] = posted_at

    text = _string_or_none(raw.get("text"))
    if text:
        post["text"] = text

    poster: dict[str, Any] = {}
    name = _string_or_none(raw.get("poster_name"))
    if name:
        poster["name"] = name
    linkedin_url = _string_or_none(raw.get("poster_linkedin_url"))
    if linkedin_url:
        poster["linkedin_url"] = linkedin_url
    headline = _string_or_none(raw.get("poster_title"))
    if headline:
        poster["headline"] = headline
    type_val = _string_or_none(raw.get("poster_type"))
    if type_val:
        poster["type"] = type_val
    if poster:
        post["poster"] = poster

    for key_src, key_dst in (
        ("num_likes", "num_likes"),
        ("num_comments", "num_comments"),
        ("num_reactions", "num_reactions"),
        ("num_shares", "num_reposts"),
    ):
        val = _coerce_int(raw.get(key_src))
        if val is not None:
            post[key_dst] = val

    images = raw.get("images")
    if isinstance(images, list):
        post["images"] = images

    original_post = raw.get("original_post")
    reshared_flat = raw.get("reshared")
    if isinstance(original_post, dict):
        post["reshared"] = True
    elif reshared_flat is not None:
        post["reshared"] = bool(reshared_flat)

    extras: dict[str, Any] = {}
    if isinstance(original_post, dict):
        reshared_from: dict[str, Any] = {}
        op_urn = _string_or_none(original_post.get("urn"))
        if op_urn:
            reshared_from["urn"] = op_urn
        op_url = _string_or_none(original_post.get("post_url") or original_post.get("url"))
        if op_url:
            reshared_from["post_url"] = op_url
        if reshared_from:
            extras["reshared_from"] = reshared_from
        residual = {
            k: v for k, v in original_post.items()
            if k not in ("urn", "post_url", "url")
            and v not in (None, "", [], {})
        }
        if residual:
            extras["original_post_extras"] = residual

    for key, val in raw.items():
        if key in _SEARCH_POST_PROMOTED_KEYS:
            continue
        if val in (None, "", [], {}):
            continue
        extras[key] = val

    if extras:
        post["additional_data"] = extras

    return post


def _normalize_fresh_linkedin_search_posts(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("match_found") is False:
        return {
            "posts": [],
            "page": raw.get("__page__") or 1,
            "enrichment_sources": {"fresh_linkedin": []},
        }
    data = raw.get("data") if isinstance(raw.get("data"), list) else []
    posts: list[dict[str, Any]] = []
    for item in data:
        try:
            p = _coalesce_search_post_item(item)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[fresh_linkedin] search_posts item skipped: %s: %s",
                type(exc).__name__, exc,
            )
            continue
        if p:
            posts.append(p)

    page = raw.get("page") if isinstance(raw.get("page"), int) else raw.get("__page__", 1)
    return {
        "posts": posts,
        "page": page,
        "enrichment_sources": {"fresh_linkedin": ["search_posts"] if posts else []},
    }


_POST_PROMOTED_KEYS: frozenset[str] = frozenset({
    "urn", "post_url", "url", "posted", "text", "poster",
    "num_likes", "num_comments", "num_reactions", "num_reposts",
    "images", "reshared",
})


def _build_fl_poster(raw_post: dict[str, Any], operation: str) -> dict[str, Any] | None:
    poster = raw_post.get("poster")
    if not isinstance(poster, dict):
        return None

    first = _string_or_none(poster.get("first"))
    last = _string_or_none(poster.get("last"))
    name = _string_or_none(poster.get("name"))
    if not name and (first or last):
        name = " ".join(p for p in (first, last) if p)

    out: dict[str, Any] = {}
    if name:
        out["name"] = name
    linkedin_url = _string_or_none(poster.get("linkedin_url"))
    if linkedin_url:
        out["linkedin_url"] = linkedin_url
    urn = _string_or_none(poster.get("urn"))
    if urn:
        out["urn"] = urn
    headline = _string_or_none(poster.get("headline"))
    if headline:
        out["headline"] = headline
    type_val = _string_or_none(poster.get("type"))
    if type_val:
        out["type"] = type_val
    return out or None


def _normalize_fresh_linkedin_post_item(
    raw_post: dict[str, Any],
    operation: str,
) -> dict[str, Any] | None:
    if not isinstance(raw_post, dict):
        return None

    urn = _string_or_none(raw_post.get("urn"))
    if not urn:
        logger.info("[fresh_linkedin] post item skipped: missing urn")
        return None

    post: dict[str, Any] = {"urn": urn}

    post_url = _string_or_none(raw_post.get("post_url") or raw_post.get("url"))
    if post_url:
        post["post_url"] = post_url

    for key in ("text",):
        val = _string_or_none(raw_post.get(key))
        if val:
            post[key] = val

    posted_at = _string_or_none(raw_post.get("posted"))
    if posted_at:
        post["posted_at"] = posted_at

    poster = _build_fl_poster(raw_post, operation)
    if poster:
        post["poster"] = poster

    if "reshared" in raw_post and raw_post["reshared"] is not None:
        post["reshared"] = bool(raw_post["reshared"])

    for key in ("num_likes", "num_comments", "num_reactions", "num_reposts"):
        val = _coerce_int(raw_post.get(key))
        if val is not None:
            post[key] = val

    images = raw_post.get("images")
    if isinstance(images, list):
        post["images"] = images

    extras: dict[str, Any] = {}
    for key, val in raw_post.items():
        if key in _POST_PROMOTED_KEYS:
            continue
        if val is None or val == "" or val == [] or val == {}:
            continue
        extras[key] = val
    poster_dict = raw_post.get("poster")
    if isinstance(poster_dict, dict):
        poster_extras = {
            k: v for k, v in poster_dict.items()
            if k not in ("first", "last", "name", "linkedin_url", "urn", "headline", "type")
            and v not in (None, "", [], {})
        }
        if poster_extras:
            extras["poster_extras"] = poster_extras

    if extras:
        post["additional_data"] = extras

    return post


def _normalize_fresh_linkedin_post_list(
    raw: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    if raw.get("match_found") is False:
        return {
            "posts": [],
            "total": 0,
            "cursor": None,
            "enrichment_sources": {"fresh_linkedin": []},
        }

    data = raw.get("data") if isinstance(raw.get("data"), list) else []
    posts: list[dict[str, Any]] = []
    for item in data:
        try:
            p = _normalize_fresh_linkedin_post_item(item, operation)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[fresh_linkedin] post item skipped in %s: %s: %s",
                operation, type(exc).__name__, exc,
            )
            continue
        if p:
            posts.append(p)

    total = raw.get("total")
    if not isinstance(total, int):
        total = len(posts)

    cursor = raw.get("cursor") or raw.get("next_cursor") or raw.get("pagination_token") or None

    return {
        "posts": posts,
        "total": total,
        "cursor": cursor,
        "enrichment_sources": {"fresh_linkedin": ["posts"] if posts else []},
    }


def _normalize_fresh_linkedin_post_details(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("match_found") is False:
        return {
            "match_found": False,
            "post": None,
            "enrichment_sources": {"fresh_linkedin": []},
        }
    data = raw.get("data")
    if not isinstance(data, dict):
        raise ProviderError(
            "fresh_linkedin",
            "Response could not be parsed (non-dict data for post_details)",
            status_code=502,
        )
    post = _normalize_fresh_linkedin_post_item(data, "fetch_post_details")
    if post is None:
        return {
            "match_found": False,
            "post": None,
            "enrichment_sources": {"fresh_linkedin": []},
        }
    return {
        "post": post,
        "enrichment_sources": {"fresh_linkedin": ["post_details"]},
    }


# ---------------------------------------------------------------------------
# PredictLeads normalisation
# ---------------------------------------------------------------------------


def normalize_predictleads(raw: dict[str, Any], operation: str) -> dict[str, Any]:
    """Normalize any PredictLeads response to nrev-lite schema.

    PredictLeads data is already flattened from JSON:API in the provider.
    This function maps it to the standard nrev-lite field names.
    """
    if operation == "enrich_company":
        return _normalize_predictleads_company(raw)
    if operation == "company_jobs":
        return _normalize_predictleads_jobs(raw)
    if operation == "company_technologies":
        return _normalize_predictleads_tech(raw)
    if operation == "company_news":
        return _normalize_predictleads_news(raw)
    if operation == "company_financing":
        return _normalize_predictleads_financing(raw)
    if operation == "similar_companies":
        return _normalize_predictleads_similar(raw)
    return raw


def _normalize_predictleads_company(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a PredictLeads company profile."""
    if raw.get("match_found") is False:
        return {"match_found": False, "companies": []}

    # location_data can be a list of dicts or a single dict
    raw_loc = raw.get("location_data") or {}
    if isinstance(raw_loc, list):
        loc_data = raw_loc[0] if raw_loc else {}
    else:
        loc_data = raw_loc
    location_parts = [
        loc_data.get("city"),
        loc_data.get("state"),
        loc_data.get("country"),
    ]
    location = ", ".join(p for p in location_parts if p) or raw.get("location") or None

    result: dict[str, Any] = {
        "id": raw.get("id"),
        "name": raw.get("company_name") or raw.get("friendly_company_name"),
        "domain": raw.get("domain") or raw.get("_domain"),
        "description": raw.get("description") or raw.get("description_short"),
        "meta_title": raw.get("meta_title"),
        "location": location,
        "city": loc_data.get("city"),
        "state": loc_data.get("state"),
        "country": loc_data.get("country"),
        "continent": loc_data.get("continent"),
        "language": raw.get("language"),
        "ticker": raw.get("ticker"),
        "enrichment_sources": {"predictleads": ["company"]},
    }
    if raw.get("parent_company"):
        result["parent_company"] = raw["parent_company"]
    if raw.get("subsidiary_companies"):
        result["subsidiary_companies"] = raw["subsidiary_companies"]

    return {k: v for k, v in result.items() if v is not None}


def _normalize_predictleads_jobs(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize PredictLeads job openings."""
    items = raw.get("items", [])
    jobs = []
    for item in items:
        job: dict[str, Any] = {
            "id": item.get("id"),
            "title": item.get("title"),
            "url": item.get("url"),
            "location": item.get("location"),
            "category": item.get("category"),
            "seniority": item.get("seniority"),
            "first_seen": item.get("first_seen_at"),
            "last_seen": item.get("last_seen_at"),
            "salary_low": item.get("salary_low_usd"),
            "salary_high": item.get("salary_high_usd"),
            "contract_type": item.get("contract_type"),
        }
        jobs.append({k: v for k, v in job.items() if v is not None})
    return {
        "domain": raw.get("domain"),
        "jobs": jobs,
        "total": raw.get("count", len(jobs)),
        "enrichment_sources": {"predictleads": ["job_openings"]},
    }


def _normalize_predictleads_tech(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize PredictLeads technology detections."""
    items = raw.get("items", [])
    techs = []
    for item in items:
        tech: dict[str, Any] = {
            "id": item.get("id"),
            "name": item.get("name"),
            "category": item.get("category"),
            "description": item.get("description"),
            "detected_on": item.get("detected_on"),
        }
        techs.append({k: v for k, v in tech.items() if v is not None})
    return {
        "domain": raw.get("domain"),
        "technologies": techs,
        "total": raw.get("count", len(techs)),
        "enrichment_sources": {"predictleads": ["technology_detections"]},
    }


def _normalize_predictleads_news(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize PredictLeads news events."""
    items = raw.get("items", [])
    events = []
    for item in items:
        event: dict[str, Any] = {
            "id": item.get("id"),
            "summary": item.get("summary"),
            "category": item.get("category"),
            "confidence": item.get("confidence"),
            "found_at": item.get("found_at"),
            "article_title": item.get("article_title"),
            "article_url": item.get("article_url"),
            "article_author": item.get("author"),
            "location": item.get("location"),
        }
        events.append({k: v for k, v in event.items() if v is not None})
    return {
        "domain": raw.get("domain"),
        "news_events": events,
        "total": raw.get("count", len(events)),
        "enrichment_sources": {"predictleads": ["news_events"]},
    }


def _normalize_predictleads_financing(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize PredictLeads financing events."""
    items = raw.get("items", [])
    rounds = []
    for item in items:
        fround: dict[str, Any] = {
            "id": item.get("id"),
            "amount": item.get("amount"),
            "currency": item.get("currency"),
            "round_type": item.get("round_type"),
            "announced_at": item.get("announced_at") or item.get("found_at"),
            "investors": item.get("investors"),
        }
        rounds.append({k: v for k, v in fround.items() if v is not None})
    return {
        "domain": raw.get("domain"),
        "financing_events": rounds,
        "total": raw.get("count", len(rounds)),
        "enrichment_sources": {"predictleads": ["financing_events"]},
    }


def _normalize_predictleads_similar(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize PredictLeads similar companies."""
    items = raw.get("items", [])
    similar = []
    for item in items:
        comp: dict[str, Any] = {
            "id": item.get("id"),
            "domain": item.get("domain"),
            "name": item.get("company_name"),
            "score": item.get("score"),
            "rank": item.get("position"),
            "reason": item.get("reason"),
        }
        similar.append({k: v for k, v in comp.items() if v is not None})
    return {
        "domain": raw.get("domain"),
        "similar_companies": similar,
        "total": raw.get("count", len(similar)),
        "enrichment_sources": {"predictleads": ["similar_companies"]},
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_location(person: dict[str, Any]) -> str | None:
    """Build a location string from person data."""
    parts = [
        person.get("city"),
        person.get("state"),
        person.get("country"),
    ]
    filtered = [p for p in parts if p]
    return ", ".join(filtered) if filtered else None
