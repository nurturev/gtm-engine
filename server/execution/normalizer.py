"""Response normalisation to nrev-lite schema.

Maps provider-specific response formats to a consistent nrev-lite schema.
Handles all Apollo response types including search results (which return
lists of people/companies) and bulk enrichment results.
"""

from __future__ import annotations

from typing import Any


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
            return {"match_found": False, "people": []}
        return _normalize_apollo_person(person)

    if provider == "rocketreach":
        # Search results have a "profiles" array
        if "profiles" in raw and isinstance(raw["profiles"], list):
            return {
                "people": [_normalize_rr_person(p) for p in raw["profiles"]],
                "total": raw.get("pagination", {}).get("total", len(raw["profiles"])),
                "page": raw.get("pagination", {}).get("start", 1),
                "per_page": raw.get("pagination", {}).get("page_size", 25),
            }

        # No match
        if raw.get("match_found") is False:
            return {"match_found": False, "people": []}

        # Single lookup returns a flat profile
        if raw.get("id") or raw.get("name"):
            return _normalize_rr_person(raw)

        return {"raw": raw, "enrichment_sources": {provider: ["raw"]}}

    # Default pass-through for unknown providers
    return {
        "raw": raw,
        "enrichment_sources": {provider: ["raw"]},
    }


def _normalize_rr_person(person: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single RocketReach person object to the nrev-lite schema."""
    # Extract best email
    emails = person.get("emails") or []
    primary_email = None
    if emails and isinstance(emails, list):
        # Prefer A/A- grade emails, then any
        for e in emails:
            if isinstance(e, dict):
                grade = (e.get("grade") or "").upper()
                if grade in ("A", "A-"):
                    primary_email = e.get("email")
                    break
        if not primary_email and emails:
            first = emails[0]
            if isinstance(first, dict):
                primary_email = first.get("email")
            elif isinstance(first, str):
                primary_email = first
    elif person.get("recommended_email"):
        primary_email = person["recommended_email"]

    # Extract best phone
    phones = person.get("phones") or []
    primary_phone = None
    if phones and isinstance(phones, list):
        for ph in phones:
            if isinstance(ph, dict):
                if ph.get("recommended"):
                    primary_phone = ph.get("number")
                    break
        if not primary_phone and phones:
            first = phones[0]
            if isinstance(first, dict):
                primary_phone = first.get("number")
            elif isinstance(first, str):
                primary_phone = first

    # Parse name
    name = person.get("name") or ""
    parts = name.split(None, 1) if name else []
    first_name = parts[0] if len(parts) >= 1 else person.get("first_name")
    last_name = parts[1] if len(parts) >= 2 else person.get("last_name")

    # Build location from available fields
    loc_parts = [
        person.get("city"),
        person.get("region"),
        person.get("country_code"),
    ]
    location = ", ".join(p for p in loc_parts if p) or person.get("location")

    result: dict[str, Any] = {
        "id": person.get("id"),
        "email": primary_email,
        "name": name or None,
        "first_name": first_name,
        "last_name": last_name,
        "title": person.get("current_title"),
        "phone": primary_phone,
        "linkedin": person.get("linkedin_url"),
        "photo_url": person.get("profile_pic"),
        "company": person.get("current_employer"),
        "company_domain": person.get("current_employer_domain"),
        "location": location or None,
        "city": person.get("city"),
        "state": person.get("region"),
        "country": person.get("country_code"),
        "seniority": None,  # RR uses management_levels, mapped below
        "skills": person.get("skills"),
        "enrichment_sources": {"rocketreach": ["person"]},
    }

    # Async lookup flag
    if person.get("_async_in_progress"):
        result["lookup_status"] = "in_progress"

    # Status
    status = person.get("status")
    if status and status != "complete":
        result["lookup_status"] = status

    return {k: v for k, v in result.items() if v is not None}


def _normalize_apollo_person(person: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single Apollo person object to the nrev-lite schema."""
    org = person.get("organization") or {}

    # Extract phone numbers from the array format Apollo uses
    phones = person.get("phone_numbers") or []
    primary_phone = None
    if phones and isinstance(phones, list):
        for phone in phones:
            if isinstance(phone, dict):
                primary_phone = phone.get("sanitized_number") or phone.get("raw_number")
                break
    elif person.get("phone_number"):
        primary_phone = person["phone_number"]

    result: dict[str, Any] = {
        "id": person.get("id"),
        "email": person.get("email"),
        "name": person.get("name"),
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name"),
        "title": person.get("title"),
        "headline": person.get("headline"),
        "phone": primary_phone,
        "linkedin": person.get("linkedin_url"),
        "photo_url": person.get("photo_url"),
        # Company info from nested organization
        "company": org.get("name") if isinstance(org, dict) else None,
        "company_domain": org.get("primary_domain") if isinstance(org, dict) else None,
        "company_industry": org.get("industry") if isinstance(org, dict) else None,
        "company_size": org.get("estimated_num_employees") if isinstance(org, dict) else None,
        # Location
        "location": _build_location(person),
        "city": person.get("city"),
        "state": person.get("state"),
        "country": person.get("country"),
        # Metadata
        "seniority": person.get("seniority"),
        "departments": person.get("departments"),
        "enrichment_sources": {"apollo": ["person"]},
    }

    # Remove None values for cleaner output
    return {k: v for k, v in result.items() if v is not None}


# ---------------------------------------------------------------------------
# Company normalisation
# ---------------------------------------------------------------------------


def normalize_company(raw: dict[str, Any], provider: str) -> dict[str, Any]:
    """Normalize a company enrichment/search result to the nrev-lite schema."""
    if provider == "apollo":
        # Search results have an "organizations" array
        if "organizations" in raw and isinstance(raw["organizations"], list):
            return {
                "companies": [_normalize_apollo_company(o) for o in raw["organizations"]],
                "total": raw.get("pagination", {}).get("total_entries"),
                "page": raw.get("pagination", {}).get("page"),
                "per_page": raw.get("pagination", {}).get("per_page"),
            }

        # Single enrichment has an "organization" object
        org = raw.get("organization", raw)
        if org is None:
            return {"match_found": False, "companies": []}
        return _normalize_apollo_company(org)

    if provider == "rocketreach":
        # Search results
        if "companies" in raw and isinstance(raw["companies"], list):
            return {
                "companies": [_normalize_rr_company(c) for c in raw["companies"]],
                "total": raw.get("pagination", {}).get("total", len(raw["companies"])),
            }
        # No match
        if raw.get("match_found") is False:
            return {"match_found": False, "companies": []}
        # Single lookup
        if raw.get("id") or raw.get("name"):
            return _normalize_rr_company(raw)
        return {"raw": raw, "enrichment_sources": {provider: ["raw"]}}

    if provider == "predictleads":
        return _normalize_predictleads_company(raw)

    return {
        "raw": raw,
        "enrichment_sources": {provider: ["raw"]},
    }


def _normalize_rr_company(company: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single RocketReach company object to the nrev-lite schema."""
    loc_parts = [
        company.get("city"),
        company.get("region"),
        company.get("country_code"),
    ]
    location = ", ".join(p for p in loc_parts if p)

    result: dict[str, Any] = {
        "id": company.get("id"),
        "name": company.get("name"),
        "domain": company.get("email_domain") or company.get("domain"),
        "website": company.get("website_url"),
        "linkedin": company.get("linkedin_url"),
        "industry": company.get("industry_str") or company.get("industry"),
        "employee_count": company.get("num_employees"),
        "description": company.get("description"),
        "location": location or None,
        "city": company.get("city"),
        "state": company.get("region"),
        "country": company.get("country_code"),
        "phone": company.get("phone"),
        "logo_url": company.get("logo_url"),
        "ticker": company.get("ticker_symbol"),
        "revenue": company.get("revenue"),
        "enrichment_sources": {"rocketreach": ["company"]},
    }

    return {k: v for k, v in result.items() if v is not None}


def _normalize_apollo_company(org: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single Apollo organization object to the nrev-lite schema."""
    result: dict[str, Any] = {
        "id": org.get("id"),
        "name": org.get("name"),
        "domain": org.get("primary_domain") or org.get("website_url"),
        "website": org.get("website_url"),
        "linkedin": org.get("linkedin_url"),
        "industry": org.get("industry"),
        "employee_count": org.get("estimated_num_employees"),
        "annual_revenue": org.get("annual_revenue"),
        "founded_year": org.get("founded_year"),
        "description": org.get("short_description"),
        "location": org.get("raw_address"),
        "city": org.get("city"),
        "state": org.get("state"),
        "country": org.get("country"),
        "phone": org.get("phone"),
        "logo_url": org.get("logo_url"),
        "keywords": org.get("keywords"),
        "technologies": org.get("technologies"),
        "funding_total": org.get("total_funding"),
        "latest_funding_round": org.get("latest_funding_round_type"),
        "enrichment_sources": {"apollo": ["organization"]},
    }

    return {k: v for k, v in result.items() if v is not None}


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
