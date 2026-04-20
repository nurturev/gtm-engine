"""Retrofit tests for ``_normalize_rr_person`` after the canonical-shape
refactor (LLD §11.1: "retrofitted" entry).

RocketReach's normalizer must narrow to the same 12-field canonical top level
as Apollo and Fresh LinkedIn. Previously-top-level extras (``id``,
``photo_url``, ``skills``, ``city``, ``state``, ``country``, ``lookup_status``)
now live under ``additional_data``.
"""

from __future__ import annotations

from server.execution.normalizer import normalize_person


PROVIDER = "rocketreach"

CANONICAL_KEYS = frozenset({
    "name", "first_name", "last_name",
    "title", "headline",
    "experiences",
    "linkedin_url", "email", "phone",
    "location",
    "company_name", "company_domain",
})
META_KEYS = frozenset({"enrichment_sources", "additional_data", "match_found"})
ALLOWED_TOP_LEVEL = CANONICAL_KEYS | META_KEYS


def build_raw_rr_person(**overrides) -> dict:
    """Minimally realistic RocketReach single-lookup payload. RR dispatches
    on the presence of ``id``/``name`` at the raw top level (see current
    normalizer.py) — no ``person:`` wrapper."""
    base = {
        "id": "rr-98765",
        "name": "Priya Sharma",
        "first_name": "Priya",
        "last_name": "Sharma",
        "current_title": "Director of Marketing",
        "current_employer": "Beta LLC",
        "current_employer_domain": "beta.com",
        "linkedin_url": "https://www.linkedin.com/in/priyasharma",
        "profile_pic": "https://cdn.rocketreach.co/photos/priya.jpg",
        "emails": [{"email": "priya@beta.com", "grade": "A"}],
        "phones": [{"number": "+1-212-555-0101", "recommended": True}],
        "city": "New York",
        "region": "NY",
        "country_code": "US",
        "skills": ["SEO", "Content Strategy"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Canonical shape at top level
# ---------------------------------------------------------------------------


class TestRocketReachCanonicalFieldsAtTopLevel:
    def test_identity_and_role(self) -> None:
        result = normalize_person(build_raw_rr_person(), PROVIDER)

        assert result.get("name") == "Priya Sharma"
        assert result.get("first_name") == "Priya"
        assert result.get("last_name") == "Sharma"
        assert result.get("title") == "Director of Marketing"

    def test_contact_fields(self) -> None:
        result = normalize_person(build_raw_rr_person(), PROVIDER)

        assert result.get("email") == "priya@beta.com"
        assert result.get("phone") == "+1-212-555-0101"
        assert result.get("linkedin_url") == "https://www.linkedin.com/in/priyasharma"

    def test_company_canonical_fields(self) -> None:
        result = normalize_person(build_raw_rr_person(), PROVIDER)

        assert result.get("company_name") == "Beta LLC"
        assert result.get("company_domain") == "beta.com"

    def test_location_is_free_text_string(self) -> None:
        result = normalize_person(build_raw_rr_person(), PROVIDER)

        location = result.get("location")
        assert isinstance(location, str) and location, (
            "RocketReach must emit a free-text location at the canonical key "
            "(built from city/region/country_code)"
        )


# ---------------------------------------------------------------------------
# additional_data envelope for non-canonical fields
# ---------------------------------------------------------------------------


class TestRocketReachExtrasLiveUnderAdditionalData:
    def test_id_moves_to_additional_data(self) -> None:
        result = normalize_person(build_raw_rr_person(), PROVIDER)

        assert "id" not in result
        assert result.get("additional_data", {}).get("id") == "rr-98765"

    def test_photo_url_moves_to_additional_data(self) -> None:
        result = normalize_person(build_raw_rr_person(), PROVIDER)

        assert "photo_url" not in result
        assert result.get("additional_data", {}).get("photo_url") == (
            "https://cdn.rocketreach.co/photos/priya.jpg"
        )

    def test_skills_moves_to_additional_data(self) -> None:
        result = normalize_person(build_raw_rr_person(), PROVIDER)

        assert "skills" not in result
        assert result.get("additional_data", {}).get("skills") == ["SEO", "Content Strategy"]

    def test_structured_location_fields_move_to_additional_data(self) -> None:
        """RR's ``region`` and ``country_code`` normalise to ``state`` /
        ``country`` inside ``additional_data`` — the same provider-agnostic
        naming Apollo and Fresh LinkedIn use."""
        result = normalize_person(build_raw_rr_person(), PROVIDER)

        for key in ("city", "state", "country"):
            assert key not in result

        extras = result.get("additional_data", {})
        assert extras.get("city") == "New York"
        assert extras.get("state") == "NY"
        assert extras.get("country") == "US"


# ---------------------------------------------------------------------------
# Top-level bounded invariant + enrichment_sources contract
# ---------------------------------------------------------------------------


class TestRocketReachTopLevelIsBounded:
    def test_only_canonical_plus_metadata_at_top_level(self) -> None:
        result = normalize_person(build_raw_rr_person(), PROVIDER)

        stray = set(result.keys()) - ALLOWED_TOP_LEVEL
        assert stray == set(), (
            f"non-canonical keys leaked to top level from RocketReach: {sorted(stray)}"
        )


class TestRocketReachEnrichmentSources:
    def test_lists_only_canonical_keys(self) -> None:
        result = normalize_person(build_raw_rr_person(), PROVIDER)

        populated = (result.get("enrichment_sources") or {}).get(PROVIDER, [])

        assert populated, "RocketReach must emit at least one populated canonical key"
        for key in populated:
            assert key in CANONICAL_KEYS, (
                f"enrichment_sources['rocketreach'] contains non-canonical key '{key}'"
            )


# ---------------------------------------------------------------------------
# Lookup status (was top-level in v1, now in additional_data)
# ---------------------------------------------------------------------------


class TestRocketReachLookupStatus:
    """The async-lookup `lookup_status` flag was top-level in v1. Per the
    refactor it moves under ``additional_data`` alongside other extras."""

    def test_lookup_status_moves_to_additional_data(self) -> None:
        # Use only the _async_in_progress flag — the impl translates it to
        # the "in_progress" alias. Passing a raw `status` key would overwrite
        # that alias with the vendor's raw value; a separate assertion covers
        # that path.
        raw = build_raw_rr_person(_async_in_progress=True)

        result = normalize_person(raw, PROVIDER)

        assert "lookup_status" not in result
        assert result.get("additional_data", {}).get("lookup_status") == "in_progress"

    def test_raw_status_field_moves_to_additional_data(self) -> None:
        """When the vendor ships a raw `status` other than `complete`, it
        lands in `additional_data["lookup_status"]` verbatim."""
        raw = build_raw_rr_person(status="progress")

        result = normalize_person(raw, PROVIDER)

        assert "lookup_status" not in result
        assert result.get("additional_data", {}).get("lookup_status") == "progress"
