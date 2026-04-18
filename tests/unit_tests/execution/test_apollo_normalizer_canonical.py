"""Retrofit tests for ``_normalize_apollo_person`` after the canonical-shape
refactor (LLD §11.1: "retrofitted" entry).

The refactor narrows top-level Person output to 12 canonical fields plus
``enrichment_sources`` and ``additional_data``. Apollo's normalizer must be
updated so every previously-top-level non-canonical key (``photo_url``,
``seniority``, ``departments``, ``city``, ``state``, ``country``,
``company_industry``, ``company_size``, ``id``) now lives under
``additional_data``. Canonical keys stay where they were.

No data is lost in the move — the point of this test file.
"""

from __future__ import annotations

from server.execution.normalizer import normalize_person


PROVIDER = "apollo"

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


def build_raw_apollo_person(**overrides) -> dict:
    """Minimally realistic Apollo single-enrichment payload."""
    base = {
        "person": {
            "id": "apollo-12345",
            "name": "John Smith",
            "first_name": "John",
            "last_name": "Smith",
            "email": "john@acme.com",
            "title": "VP Sales",
            "headline": "Scaling revenue teams",
            "linkedin_url": "https://www.linkedin.com/in/johnsmith",
            "photo_url": "https://cdn.apollo.io/photos/john.jpg",
            "phone_numbers": [{"sanitized_number": "+1-415-555-0100"}],
            "city": "San Francisco",
            "state": "CA",
            "country": "United States",
            "seniority": "vp",
            "departments": ["sales"],
            "organization": {
                "name": "Acme Inc",
                "primary_domain": "acme.com",
                "industry": "Software",
                "estimated_num_employees": 500,
            },
        }
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Canonical shape at top level
# ---------------------------------------------------------------------------


class TestApolloCanonicalFieldsAtTopLevel:
    def test_identity_role_and_contact(self) -> None:
        result = normalize_person(build_raw_apollo_person(), PROVIDER)

        assert result.get("name") == "John Smith"
        assert result.get("first_name") == "John"
        assert result.get("last_name") == "Smith"
        assert result.get("title") == "VP Sales"
        assert result.get("headline") == "Scaling revenue teams"
        assert result.get("email") == "john@acme.com"
        assert result.get("phone") == "+1-415-555-0100"
        assert result.get("linkedin_url") == "https://www.linkedin.com/in/johnsmith"

    def test_company_canonical_fields(self) -> None:
        result = normalize_person(build_raw_apollo_person(), PROVIDER)

        assert result.get("company_name") == "Acme Inc"
        assert result.get("company_domain") == "acme.com"

    def test_location_is_free_text_string(self) -> None:
        result = normalize_person(build_raw_apollo_person(), PROVIDER)

        location = result.get("location")
        assert isinstance(location, str) and location, (
            "Apollo must emit a free-text location string at the canonical key"
        )


# ---------------------------------------------------------------------------
# additional_data envelope for non-canonical fields
# ---------------------------------------------------------------------------


class TestApolloExtrasLiveUnderAdditionalData:
    """Everything Apollo previously emitted at top level that isn't canonical
    must now appear under ``additional_data`` — no data loss."""

    def test_id_moves_to_additional_data(self) -> None:
        result = normalize_person(build_raw_apollo_person(), PROVIDER)

        assert "id" not in result
        assert result.get("additional_data", {}).get("id") == "apollo-12345"

    def test_photo_url_moves_to_additional_data(self) -> None:
        result = normalize_person(build_raw_apollo_person(), PROVIDER)

        assert "photo_url" not in result
        assert result.get("additional_data", {}).get("photo_url") == (
            "https://cdn.apollo.io/photos/john.jpg"
        )

    def test_seniority_and_departments_move_to_additional_data(self) -> None:
        result = normalize_person(build_raw_apollo_person(), PROVIDER)

        for key in ("seniority", "departments"):
            assert key not in result, f"{key} must not leak to top level"

        extras = result.get("additional_data", {})
        assert extras.get("seniority") == "vp"
        assert extras.get("departments") == ["sales"]

    def test_structured_location_fields_move_to_additional_data(self) -> None:
        result = normalize_person(build_raw_apollo_person(), PROVIDER)

        for key in ("city", "state", "country"):
            assert key not in result

        extras = result.get("additional_data", {})
        assert extras.get("city") == "San Francisco"
        assert extras.get("state") == "CA"
        assert extras.get("country") == "United States"

    def test_company_metadata_moves_to_additional_data(self) -> None:
        """Only ``company`` and ``company_domain`` are canonical — industry and
        size move under ``additional_data``."""
        result = normalize_person(build_raw_apollo_person(), PROVIDER)

        for key in ("company_industry", "company_size"):
            assert key not in result

        extras = result.get("additional_data", {})
        assert extras.get("company_industry") == "Software"
        assert extras.get("company_size") == 500


# ---------------------------------------------------------------------------
# Top-level bounded invariant + enrichment_sources contract
# ---------------------------------------------------------------------------


class TestApolloTopLevelIsBounded:
    def test_only_canonical_plus_metadata_at_top_level(self) -> None:
        result = normalize_person(build_raw_apollo_person(), PROVIDER)

        stray = set(result.keys()) - ALLOWED_TOP_LEVEL
        assert stray == set(), (
            f"non-canonical keys leaked to top level from Apollo: {sorted(stray)}"
        )


class TestApolloEnrichmentSources:
    def test_lists_only_canonical_keys(self) -> None:
        result = normalize_person(build_raw_apollo_person(), PROVIDER)

        populated = (result.get("enrichment_sources") or {}).get(PROVIDER, [])

        assert populated, "Apollo must emit at least one populated canonical key"
        for key in populated:
            assert key in CANONICAL_KEYS, (
                f"enrichment_sources['apollo'] contains non-canonical key '{key}'"
            )
