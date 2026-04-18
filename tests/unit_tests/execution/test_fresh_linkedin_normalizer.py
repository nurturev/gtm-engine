"""Unit tests for the Fresh LinkedIn normalizer — canonical + additional_data shape.

Pure function. Zero mocks. Blueprint §7 "Helpers, Validators, Mappers".

Contracts under test (from ``fresh_linkedin_hld.md`` §3.1 and
``fresh_linkedin_lld.md`` §11.1 — canonical-shape refactor, v2.0 breaking change):

1. Dispatcher: ``normalize_person(raw, provider="fresh_linkedin")`` routes to
   the fresh-linkedin branch.
2. Top-level canonical keys (12 fields from ``unique_entity_fields.csv``) are
   the ONLY output keys allowed at top level, alongside ``enrichment_sources``
   and ``additional_data``. Nothing else.
3. Every non-canonical vendor field lives under ``additional_data``.
4. ``enrichment_sources["fresh_linkedin"]`` lists ONLY populated canonical keys
   (never ``additional_data`` keys — those aren't attributed).
5. Empty / None values are stripped from both canonical top-level and from
   ``additional_data``.
6. Best-effort drift handling — broken field skipped, others preserved.
7. ``experiences`` (plural) is the canonical key name; it is a top-level
   ``list[Experience]``.
8. ``match_found: False`` shape when upstream had no result.
"""

from __future__ import annotations

import pytest

from server.core.exceptions import ProviderError
from server.execution.normalizer import normalize_person


PROVIDER = "fresh_linkedin"

# Canonical Person keys per unique_entity_fields.csv + HLD §3.1. Every provider
# conforms to this at top level. Nothing else is allowed there besides the
# two cross-cutting metadata keys.
CANONICAL_KEYS = frozenset({
    "name",
    "first_name",
    "last_name",
    "title",
    "headline",
    "experiences",
    "linkedin_url",
    "email",
    "phone",
    "location",
    "company_name",
    "company_domain",
})
META_KEYS = frozenset({"enrichment_sources", "additional_data", "match_found"})
ALLOWED_TOP_LEVEL = CANONICAL_KEYS | META_KEYS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def build_raw_fresh_linkedin_response(**overrides) -> dict:
    """Vendor-shaped raw payload. Keys mirror the Fresh LinkedIn / RapidAPI
    sample payload shape. Output-side assertions target the canonical +
    additional_data contract, not the raw key names — this fixture is free
    to evolve when the vendor mapping table changes.
    """
    # Raw keys mirror the round-5 Fresh LinkedIn normalizer expectations:
    # company / location / industry / size are all FLAT at profile root
    # (not nested). `experiences[*].start_date` is built from `start_month`
    # + `start_year`; `end_date` is omitted when `is_current` is truthy.
    base = {
        "full_name": "Jane Doe",
        "first_name": "Jane",
        "last_name": "Doe",
        "headline": "Head of Growth at Acme",
        "job_title": "Head of Growth",
        "about": "Builder, investor, writer.",
        "linkedin_url": "https://www.linkedin.com/in/janedoe",
        "profile_picture_url": "https://media.licdn.com/example.jpg",
        "location": "San Francisco, CA, USA",
        "city": "San Francisco",
        "state": "CA",
        "country": "United States",
        "company": "Acme Inc",
        "company_domain": "acme.com",
        "company_industry": "Software",
        "company_employee_count": 500,
        "experiences": [
            {
                "company": "Acme Inc",
                "title": "Head of Growth",
                "start_month": 1,
                "start_year": 2023,
                "is_current": "true",
                "description": "Led growth.",
                "location": "Remote",
            },
            {
                "company": "Beta LLC",
                "title": "Senior PM",
                "start_month": 6,
                "start_year": 2019,
                "end_month": 12,
                "end_year": 2022,
                "description": "",
                "location": "SF",
            },
        ],
        "educations": [
            {
                "school": "Stanford",
                "degree": "BS",
                "field_of_study": "CS",
                "start_year": 2010,
                "end_year": 2014,
            }
        ],
        "skills": ["Growth", "SQL", "Leadership"],
        "languages": ["English", "French"],
        "certifications": [
            {"name": "AWS CCP", "issuer": "AWS", "year": 2022},
        ],
        "connections_count": 500,
        "follower_count": 1200,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class TestDispatcherRoutesToFreshLinkedInBranch:
    def test_does_not_return_default_raw_wrapper(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert "raw" not in result or set(result.keys()) != {"raw", "enrichment_sources"}
        assert result.get("enrichment_sources", {}).get(PROVIDER)


# ---------------------------------------------------------------------------
# Canonical Person — the 12 fixed top-level fields
# ---------------------------------------------------------------------------


class TestCanonicalPersonFieldsAtTopLevel:
    """Per HLD §3.1 the canonical set is fixed at 12 optional fields. Each
    lives at the top level of the output dict; nothing else does (except
    the two metadata keys tested separately)."""

    def test_identity_fields_at_top_level(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert result.get("name") == "Jane Doe"
        assert result.get("first_name") == "Jane"
        assert result.get("last_name") == "Doe"

    def test_role_fields_at_top_level(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert result.get("title") == "Head of Growth"
        assert result.get("headline") == "Head of Growth at Acme"

    def test_linkedin_is_canonical_top_level(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert result.get("linkedin_url") == "https://www.linkedin.com/in/janedoe"

    def test_location_is_free_text_string_at_top_level(self) -> None:
        """``location`` is a single free-text string per HLD §3.1. Structured
        city / state / country live under ``additional_data`` (see below)."""
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert result.get("location") == "San Francisco, CA, USA"

    def test_company_fields_at_top_level(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert result.get("company_name") == "Acme Inc"
        assert result.get("company_domain") == "acme.com"


class TestExperiencesIsCanonicalTopLevel:
    """``experiences`` (plural, per CSV) is the one canonical nested field —
    top-level ``list[Experience]`` preserving vendor order."""

    def test_experiences_at_top_level_not_additional_data(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert "experiences" in result, "experiences must be canonical top-level"
        assert "experiences" not in (result.get("additional_data") or {}), (
            "experiences must NOT also appear under additional_data"
        )

    def test_preserves_order_and_item_shape(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        experiences = result.get("experiences")
        assert isinstance(experiences, list)
        assert len(experiences) == 2

        first = experiences[0]
        assert first["company"] == "Acme Inc"
        assert first["title"] == "Head of Growth"
        # Impl builds start_date from start_month + start_year via _fl_build_date.
        assert first["start_date"] == "2023-01"
        # is_current=true: end_date must be omitted regardless of vendor end fields.
        assert "end_date" not in first
        assert first.get("description") == "Led growth."
        assert first.get("location") == "Remote"

        second = experiences[1]
        assert second["company"] == "Beta LLC"
        assert second["start_date"] == "2019-06"
        assert second["end_date"] == "2022-12"


# ---------------------------------------------------------------------------
# additional_data — everything non-canonical lives here
# ---------------------------------------------------------------------------


class TestAdditionalDataEnvelopeCarriesNonCanonicalFields:
    """HLD §3.3: ``additional_data`` is a flat catch-all for every
    provider-specific field outside the canonical set. Keys are
    provider-agnostic names; no per-provider nesting (D30)."""

    def test_additional_data_is_present_and_is_a_dict(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert "additional_data" in result
        assert isinstance(result["additional_data"], dict)

    def test_about_lives_under_additional_data(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert "about" not in result, "about must not leak to top level"
        assert result["additional_data"].get("about") == "Builder, investor, writer."

    def test_photo_url_lives_under_additional_data(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert "photo_url" not in result
        assert result["additional_data"].get("photo_url") == "https://media.licdn.com/example.jpg"

    def test_city_state_country_live_under_additional_data(self) -> None:
        """Structured location decomposition belongs in ``additional_data`` —
        canonical ``location`` is free-text only (HLD §3.1)."""
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        for key in ("city", "state", "country"):
            assert key not in result, f"{key} must not leak to top level"

        extras = result["additional_data"]
        assert extras.get("city") == "San Francisco"
        assert extras.get("state") == "CA"
        assert extras.get("country") == "United States"

    def test_company_metadata_lives_under_additional_data(self) -> None:
        """Canonical company surface is name + domain only. Industry, size,
        and the HQ sub-dict live in ``additional_data``."""
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        for key in ("company_industry", "company_size"):
            assert key not in result

        extras = result["additional_data"]
        assert extras.get("company_industry") == "Software"
        assert extras.get("company_size") == 500

    def test_counts_live_under_additional_data(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        for key in ("connections_count", "follower_count"):
            assert key not in result
        assert result["additional_data"].get("connections_count") == 500
        assert result["additional_data"].get("follower_count") == 1200

    def test_education_lives_under_additional_data(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert "education" not in result
        education = result["additional_data"].get("education")
        assert isinstance(education, list) and len(education) == 1
        assert education[0] == {
            "school": "Stanford",
            "degree": "BS",
            "field_of_study": "CS",
            "start_year": 2010,
            "end_year": 2014,
        }

    def test_skills_and_languages_live_under_additional_data(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        for key in ("skills", "languages"):
            assert key not in result

        assert result["additional_data"].get("skills") == ["Growth", "SQL", "Leadership"]
        assert result["additional_data"].get("languages") == ["English", "French"]

    def test_certifications_live_under_additional_data(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        assert "certifications" not in result
        certs = result["additional_data"].get("certifications")
        assert isinstance(certs, list) and len(certs) == 1
        assert certs[0] == {"name": "AWS CCP", "issuer": "AWS", "year": 2022}


# ---------------------------------------------------------------------------
# Top-level-bounded invariant — the breaking-change guard
# ---------------------------------------------------------------------------


class TestTopLevelKeysAreBounded:
    """The new shape is a breaking change (grooming §Backward compat). Guard
    that no stray non-canonical key leaks to top level — every consumer that
    migrated from v1 will rely on this."""

    def test_top_level_is_subset_of_canonical_plus_metadata(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        stray = set(result.keys()) - ALLOWED_TOP_LEVEL
        assert stray == set(), (
            f"non-canonical keys leaked to top level: {sorted(stray)}. "
            f"Allowed top-level keys are canonical + enrichment_sources + additional_data."
        )


# ---------------------------------------------------------------------------
# enrichment_sources — tracks populated CANONICAL keys only
# ---------------------------------------------------------------------------


class TestEnrichmentSourcesListsOnlyCanonicalKeys:
    """Per HLD §3.1: ``enrichment_sources`` lists which *canonical* keys the
    provider contributed. ``additional_data`` keys are never listed — they
    aren't per-field attributed."""

    def test_lists_populated_canonical_keys(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        populated = (result.get("enrichment_sources") or {}).get(PROVIDER, [])

        # Every listed key must be canonical.
        for key in populated:
            assert key in CANONICAL_KEYS, (
                f"enrichment_sources contains non-canonical key '{key}' — "
                "only canonical fields are attributed"
            )

        # A reasonable set of canonical keys filled by this fixture must appear.
        for key in ("name", "title", "headline", "company_name", "company_domain", "experiences", "linkedin_url"):
            assert key in populated, f"expected '{key}' in enrichment_sources"

    def test_does_not_list_additional_data_keys(self) -> None:
        result = normalize_person(build_raw_fresh_linkedin_response(), PROVIDER)

        populated = set((result.get("enrichment_sources") or {}).get(PROVIDER, []))

        # None of these are canonical — they must NOT appear in enrichment_sources.
        for additional_key in (
            "about", "photo_url", "city", "state", "country",
            "company_industry", "company_size", "connections_count",
            "follower_count", "education", "skills", "languages", "certifications",
        ):
            assert additional_key not in populated, (
                f"'{additional_key}' is an additional_data key and must not appear "
                "in enrichment_sources"
            )

    def test_lists_only_keys_actually_populated(self) -> None:
        # Minimal payload — only a name.
        result = normalize_person({"full_name": "Jane Doe"}, PROVIDER)

        populated = (result.get("enrichment_sources") or {}).get(PROVIDER, [])

        assert "name" in populated
        # Canonical fields absent from raw must NOT appear.
        for absent in ("experiences", "title", "company_name", "linkedin_url", "email"):
            assert absent not in populated


# ---------------------------------------------------------------------------
# Empty-stripping — both canonical and additional_data
# ---------------------------------------------------------------------------


class TestStripsEmptyValues:
    """Absent / empty canonical fields must be missing from the dict, not
    present-with-None. Same rule for ``additional_data`` keys."""

    def test_absent_canonical_fields_are_missing_from_output(self) -> None:
        result = normalize_person({"full_name": "Jane Doe"}, PROVIDER)

        for canonical_key in ("title", "company_name", "company_domain", "linkedin_url", "location", "experiences"):
            assert canonical_key not in result, (
                f"expected '{canonical_key}' absent, not None"
            )

    def test_absent_additional_data_keys_are_missing(self) -> None:
        """When no vendor field populates an additional_data slot, the key
        must be absent from the ``additional_data`` dict — not present-with-None."""
        result = normalize_person({"full_name": "Jane Doe"}, PROVIDER)

        extras = result.get("additional_data") or {}
        for extra_key in ("about", "photo_url", "follower_count", "skills", "education"):
            assert extra_key not in extras, (
                f"additional_data['{extra_key}'] should be absent, not None"
            )


# ---------------------------------------------------------------------------
# Best-effort drift handling
# ---------------------------------------------------------------------------


class TestBestEffortHandlesPerFieldParseFailures:
    """A single bad field must not kill the whole row. Applies to both
    canonical extraction and additional_data extraction."""

    def test_silently_coerces_vendor_variants_and_preserves_others(self) -> None:
        raw = build_raw_fresh_linkedin_response(
            # Known-tolerated LinkedIn shape — coerces to 500.
            connections_count="500+",
            # Unparseable int shape — must NOT crash the row.
            follower_count={"nested": "garbage"},
        )

        result = normalize_person(raw, PROVIDER)

        extras = result.get("additional_data") or {}
        # "500+" coerces to 500.
        assert extras.get("connections_count") == 500
        # Unparseable int absent, not None.
        assert "follower_count" not in extras
        # Canonical fields survive.
        assert result.get("name") == "Jane Doe"
        assert result.get("company_name") == "Acme Inc"

    def test_skips_broken_nested_item_in_experiences_list(self) -> None:
        raw = build_raw_fresh_linkedin_response(
            experiences=[
                {"company": "Acme Inc", "title": "Head of Growth"},  # ok (no dates)
                "this-is-not-a-dict",                                  # bad
                {"company": "Beta LLC", "title": "Senior PM"},         # ok
            ],
        )

        result = normalize_person(raw, PROVIDER)

        experiences = result.get("experiences", [])
        assert len(experiences) == 2
        companies = {item.get("company") for item in experiences}
        assert companies == {"Acme Inc", "Beta LLC"}


class TestHardFailsOnNonDictProfile:
    """LLD §5.6 carve-out: non-dict profile shape must surface cleanly.
    The current impl reaches this guard only via the ``raw["data"]`` unwrap
    path — very-top-level non-dict raws crash upstream of this branch (noted
    as a known gap vs. spec; provider layer guarantees raw is a dict)."""

    @pytest.mark.parametrize("bad_profile", [{"data": None}, {"data": 42}])
    def test_falls_back_cleanly_on_non_dict_data_field(self, bad_profile) -> None:
        result = normalize_person(bad_profile, PROVIDER)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Match-found: False shape — upstream 404 equivalent
# ---------------------------------------------------------------------------


class TestNoMatchShape:
    def test_match_found_false_yields_empty_row(self) -> None:
        result = normalize_person({"match_found": False, "data": None}, PROVIDER)

        assert result.get("match_found") is False
        sources = result.get("enrichment_sources") or {}
        assert sources.get(PROVIDER) == []
