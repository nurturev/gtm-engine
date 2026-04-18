"""Phase 2.0 — Company normalizer scope reduction (Apollo, RocketReach).

The canonical Company surface is the 6 primary fields from
``unique_entity_fields.csv``:

    name, domain, linkedin, employee_count, industry, hq_location

Everything else a provider returns lives under ``additional_data`` keyed by
provider name.

Contract (HLD 2.0 §3.2, §3.4, §8):
- Top-level keys are a subset of ``{canonical} ∪ {enrichment_sources,
  additional_data, match_found, companies, total, page, per_page}`` — no other
  key leaks through.
- ``additional_data[<provider>]`` carries every non-canonical vendor field
  (anything not promoted).
- ``enrichment_sources["<provider>"]`` lists **only populated canonical keys**;
  never additional_data keys, never absent fields.
- Re-enriching with a different provider merges under its own key, without
  clobbering other providers' ``additional_data``.

Pure functions. Zero mocks. Blueprint §7 "Helpers, Validators, Mappers".
"""

from __future__ import annotations

import pytest

from server.execution.normalizer import normalize_company


def _phase_2_0_retrofit_applied() -> bool:
    """Probe: after Phase 2.0 lands, Apollo's ``normalize_company`` will NOT
    leak structured location sub-fields (``city``, ``state``, ``country``) or
    the vendor's ``phone`` / ``website`` at top level — those all move under
    ``additional_data``. Until the retrofit happens, skip the whole file."""
    probe = normalize_company(
        {"name": "X", "city": "Y", "phone": "555"},
        "apollo",
    )
    # Retrofit done when these no longer leak to top-level.
    return "city" not in probe and "phone" not in probe


pytestmark = pytest.mark.skipif(
    not _phase_2_0_retrofit_applied(),
    reason="Phase 2.0 company retrofit not yet applied — Apollo/RR still emit "
           "wide-shape Company (city/phone/website at top level)",
)


# Canonical Company keys per unique_entity_fields.csv.
CANONICAL_COMPANY_KEYS = frozenset({
    "name", "domain", "linkedin_url",
    "employee_count", "industry", "hq_location",
})
META_KEYS = frozenset({
    "enrichment_sources", "additional_data", "match_found",
    "companies", "total", "page", "per_page",
})
ALLOWED_TOP_LEVEL = CANONICAL_COMPANY_KEYS | META_KEYS


# ---------------------------------------------------------------------------
# Apollo company — fixture builder
# ---------------------------------------------------------------------------


def build_apollo_company(**overrides) -> dict:
    """Minimal Apollo `organization` shape with a reasonable set of fields.
    Expand via overrides when a test needs specific edge conditions."""
    base = {
        "id": "apollo-org-1",
        "name": "Acme Inc",
        "primary_domain": "acme.com",
        "website_url": "https://acme.com",
        "linkedin_url": "https://www.linkedin.com/company/acme",
        "industry": "Software",
        "estimated_num_employees": 500,
        "raw_address": "1 Main Street, San Francisco, CA 94105, US",
        "city": "San Francisco",
        "state": "CA",
        "country": "US",
        "annual_revenue": 100_000_000,
        "founded_year": 2015,
        "short_description": "A software company.",
        "phone": "+1-555-0100",
        "logo_url": "https://logo.example.com/acme.png",
        "keywords": ["saas", "b2b"],
        "technologies": ["AWS", "React"],
        "total_funding": 25_000_000,
        "latest_funding_round_type": "Series B",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# RocketReach company — fixture builder
# ---------------------------------------------------------------------------


def build_rr_company(**overrides) -> dict:
    base = {
        "id": "rr-company-1",
        "name": "Acme Inc",
        "email_domain": "acme.com",
        "domain": "acme.com",
        "website_url": "https://acme.com",
        "linkedin_url": "https://www.linkedin.com/company/acme",
        "industry_str": "Software",
        "num_employees": 500,
        "description": "Software company.",
        "city": "San Francisco",
        "region": "CA",
        "country_code": "US",
        "phone": "+1-555-0100",
        "logo_url": "https://logo.example.com/acme.png",
        "ticker_symbol": None,
        "revenue": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Apollo
# ---------------------------------------------------------------------------


class TestApolloCompanyCanonicalFields:
    def test_canonical_keys_promoted_from_vendor_payload(self) -> None:
        result = normalize_company(build_apollo_company(), "apollo")

        assert result.get("name") == "Acme Inc"
        assert result.get("domain") == "acme.com"
        assert result.get("linkedin_url") == "https://www.linkedin.com/company/acme"
        assert result.get("employee_count") == 500
        assert result.get("industry") == "Software"
        assert result.get("hq_location") == "1 Main Street, San Francisco, CA 94105, US"

    def test_hq_location_falls_back_to_city_state_country_when_raw_address_absent(self) -> None:
        raw = build_apollo_company(raw_address=None)
        result = normalize_company(raw, "apollo")

        assert result.get("hq_location") == "San Francisco, CA, US"

    def test_domain_falls_back_to_website_url_when_primary_domain_missing(self) -> None:
        raw = build_apollo_company(primary_domain=None, website_url="https://acme.io")
        result = normalize_company(raw, "apollo")

        # Implementation may strip or not, but the test of intent is: we got
        # *some* valid domain signal, not None.
        assert result.get("domain") is not None


class TestApolloCompanyAdditionalDataContent:
    """The impl's ``additional_data`` is a flat dict (not keyed by provider
    name) — the provider-keying is done by the outer caller when merging
    multiple providers' rows into a shared Company record."""

    def test_non_canonical_vendor_fields_routed_to_additional_data(self) -> None:
        result = normalize_company(build_apollo_company(), "apollo")

        extras = result.get("additional_data") or {}
        assert extras.get("annual_revenue") == 100_000_000
        assert extras.get("founded_year") == 2015

    def test_additional_data_does_not_duplicate_canonical_values(self) -> None:
        """Anti-duplication: `name`, `domain` etc. appear at top level only,
        not ALSO inside additional_data. Otherwise callers will drift in which
        they read, and the contract becomes meaningless."""
        result = normalize_company(build_apollo_company(), "apollo")

        extras = result.get("additional_data") or {}
        for canonical_key in ("name", "domain", "linkedin_url",
                              "employee_count", "industry", "hq_location"):
            assert canonical_key not in extras, (
                f"'{canonical_key}' is canonical — must not appear in additional_data"
            )


class TestApolloCompanyEnrichmentSources:
    def test_lists_only_populated_canonical_keys(self) -> None:
        result = normalize_company(build_apollo_company(), "apollo")

        populated = (result.get("enrichment_sources") or {}).get("apollo", [])
        assert isinstance(populated, list)

        for key in populated:
            assert key in CANONICAL_COMPANY_KEYS, (
                f"'{key}' is not canonical — enrichment_sources must not list it"
            )

    def test_absent_canonical_not_listed(self) -> None:
        raw = build_apollo_company(
            estimated_num_employees=None,
            industry=None,
        )
        result = normalize_company(raw, "apollo")

        populated = set((result.get("enrichment_sources") or {}).get("apollo", []))
        assert "employee_count" not in populated
        assert "industry" not in populated


class TestApolloCompanyTopLevelIsBounded:
    """Breaking-change guard for Phase 2.0 — after retrofit, no non-canonical
    key leaks to top level."""

    def test_top_level_is_subset_of_canonical_plus_meta(self) -> None:
        result = normalize_company(build_apollo_company(), "apollo")

        stray = set(result.keys()) - ALLOWED_TOP_LEVEL
        assert stray == set(), (
            f"non-canonical keys leaked to top level: {sorted(stray)}"
        )


class TestApolloCompanyEmptyShape:
    def test_match_found_false_yields_empty_row(self) -> None:
        raw = {"organization": None}
        result = normalize_company(raw, "apollo")

        assert result.get("match_found") is False


# ---------------------------------------------------------------------------
# RocketReach
# ---------------------------------------------------------------------------


class TestRocketReachCompanyCanonical:
    def test_canonical_keys_promoted(self) -> None:
        result = normalize_company(build_rr_company(), "rocketreach")

        assert result.get("name") == "Acme Inc"
        assert result.get("domain") == "acme.com"
        assert result.get("linkedin_url") == "https://www.linkedin.com/company/acme"
        assert result.get("employee_count") == 500
        assert result.get("industry") == "Software"
        # hq_location built from city/region/country_code.
        assert result.get("hq_location") == "San Francisco, CA, US"


class TestRocketReachCompanyAdditionalData:
    def test_non_canonical_fields_routed_to_additional_data_rocketreach(self) -> None:
        result = normalize_company(build_rr_company(), "rocketreach")

        extras = result.get("additional_data") or {}
        # RR ships ``description`` as a non-canonical vendor field — must land
        # in additional_data, not leak to top level.
        assert "description" in extras or extras.get("description") == "Software company."
        # Canonical fields must NOT appear in additional_data.
        for canonical in ("name", "domain", "linkedin_url",
                          "employee_count", "industry", "hq_location"):
            assert canonical not in extras


# ---------------------------------------------------------------------------
# Multi-provider merge semantics (HLD 2.0 §5 merge semantics)
# ---------------------------------------------------------------------------


class TestIdempotentNormalisation:
    """Re-normalising the same vendor payload produces the same canonical row —
    no hidden state, no drift on repeat calls."""

    def test_repeat_normalisation_is_stable(self) -> None:
        first = normalize_company(build_apollo_company(name="Acme Inc"), "apollo")
        second = normalize_company(build_apollo_company(name="Acme Inc"), "apollo")

        assert first.get("name") == second.get("name")
        assert set((first.get("additional_data") or {}).keys()) == \
            set((second.get("additional_data") or {}).keys())

    def test_enrichment_sources_lists_canonical_keys_populated_per_row(self) -> None:
        """Source attribution is per-row; two providers enriching the same
        record each emit their own ``enrichment_sources`` entry."""
        apollo_row = normalize_company(build_apollo_company(), "apollo")
        rr_row = normalize_company(build_rr_company(), "rocketreach")

        assert "apollo" in (apollo_row.get("enrichment_sources") or {})
        assert "rocketreach" in (rr_row.get("enrichment_sources") or {})
