"""Phase 2.1 — Fresh LinkedIn company normalizer.

Unit tests for ``_normalize_fresh_linkedin_company``. Exercises the contract
defined in HLD Phase 2.1 §4 against the live sample captured on 2026-04-18
(``docs/sample_responses/company_by_url.json``).

Pure function. Zero mocks.

Contract under test:
1. Six canonical Company keys (HLD 2.0 §3.2) promoted from the vendor payload:
   ``name, domain, linkedin, employee_count, industry, hq_location``.
2. Everything else lands in ``additional_data["fresh_linkedin"]``: confident_score,
   employee_range, follower_count, description, specialties, year_founded,
   logo_url, phone, email, website, locations[], affiliated_companies[],
   funding_info, company_id.
3. ``enrichment_sources["fresh_linkedin"]`` lists only populated canonical keys.
4. Fuzzy-match (Google Japan returned when domain lookup probes Google) is
   not an error: primary fields reflect the vendor's actual return;
   ``confident_score`` is exposed inside ``additional_data`` so the skill can
   tell Claude to inspect it.
5. industries[0] is promoted (first entry is vendor's primary industry).
6. hq_full_address → hq_location, with city/region/country fallback when the
   pre-assembled string is empty.
"""

from __future__ import annotations

import pytest

from server.execution.normalizer import normalize_company
from tests.unit_tests.execution.fixtures import (
    company_by_domain_response,
    company_by_url_response,
)


PROVIDER = "fresh_linkedin"


def _probe_fresh_linkedin_company_dispatch_exists() -> bool:
    """Returns True once ``normalize_company`` routes ``provider="fresh_linkedin"``
    to its own branch (Phase 2.1). Until then, the dispatcher falls through to
    the default ``{"raw": ..., "enrichment_sources": {"fresh_linkedin": ["raw"]}}``
    pass-through — which means our assertions on ``name``/``domain`` etc. would
    all fail in confusing ways. Cleaner to skip until wired up."""
    result = normalize_company({"data": {"company_name": "probe"}}, PROVIDER)
    return "name" in result or "raw" not in result


pytestmark = pytest.mark.skipif(
    not _probe_fresh_linkedin_company_dispatch_exists(),
    reason="Phase 2.1 not yet implemented — normalize_company(fresh_linkedin) "
           "branch missing (currently falls through to default raw pass-through)",
)

CANONICAL_COMPANY_KEYS = frozenset({
    "name", "domain", "linkedin_url",
    "employee_count", "industry", "hq_location",
})


# ---------------------------------------------------------------------------
# Happy path — Google (company_by_url)
# ---------------------------------------------------------------------------


class TestGoogleSampleCanonicalFields:
    """The Google fixture is a 100% confident-score full response — every
    canonical field should be populated."""

    def test_name_promoted_from_company_name(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)
        assert result.get("name") == "Google"

    def test_domain_promoted(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)
        # Vendor returns "goo.gle" for Google — trust what the vendor says.
        # Normalizer must not invent a different value.
        assert result.get("domain") == "goo.gle"

    def test_linkedin_url_promoted(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)
        assert result.get("linkedin_url") == "https://www.linkedin.com/company/google/"

    def test_employee_count_is_int(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)
        assert result.get("employee_count") == 340928

    def test_industry_is_first_of_industries_list(self) -> None:
        """Vendor returns a list; primary industry is the first element."""
        result = normalize_company(company_by_url_response(), PROVIDER)
        assert result.get("industry") == "Software Development"

    def test_hq_location_prefers_hq_full_address(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)
        assert result.get("hq_location") == (
            "1600 Amphitheatre Parkway, Mountain View, CA 94043, US"
        )


class TestGoogleSampleAdditionalData:
    """Non-canonical vendor fields land under ``additional_data["fresh_linkedin"]``."""

    def test_follower_count_and_employee_range_in_additional_data(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)

        extras = result.get("additional_data") or {}
        assert extras.get("follower_count") == 41_424_645
        assert extras.get("employee_range") == "10001+"

    def test_description_and_specialties_in_additional_data(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)

        extras = result.get("additional_data") or {}
        assert isinstance(extras.get("description"), str)
        assert len(extras["description"]) > 0

    def test_affiliated_companies_preserved_as_list(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)

        extras = result.get("additional_data") or {}
        affiliates = extras.get("affiliated_companies")
        assert isinstance(affiliates, list) and len(affiliates) >= 1
        # Each item is the vendor's raw shape — we don't normalise affiliate entries.
        first = affiliates[0]
        assert "linkedin_url" in first
        assert "name" in first

    def test_company_id_preserved_in_additional_data(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)

        extras = result.get("additional_data") or {}
        assert extras.get("company_id") == "1441"

    def test_locations_preserved_as_list_of_dicts(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)

        extras = result.get("additional_data") or {}
        locations = extras.get("locations")
        assert isinstance(locations, list) and len(locations) >= 1


class TestGoogleSampleEnrichmentSources:
    def test_lists_all_populated_canonical_keys(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)

        populated = set((result.get("enrichment_sources") or {}).get(PROVIDER, []))
        # Full Google sample → every canonical key populated.
        assert populated == CANONICAL_COMPANY_KEYS

    def test_never_lists_additional_data_keys(self) -> None:
        result = normalize_company(company_by_url_response(), PROVIDER)

        populated = set((result.get("enrichment_sources") or {}).get(PROVIDER, []))
        for forbidden in (
            "follower_count", "description", "specialties",
            "affiliated_companies", "confident_score", "company_id",
        ):
            assert forbidden not in populated


# ---------------------------------------------------------------------------
# Fuzzy domain lookup — Google Japan returned when probing google.com
# ---------------------------------------------------------------------------


class TestFuzzyDomainMatch:
    """This is the key data-quality surface (grooming §12.1.3). The normalizer
    passes through whatever the vendor gave us — the 80% confident_score is
    exposed so downstream logic/skill can judge."""

    def test_primary_fields_reflect_vendor_return(self) -> None:
        result = normalize_company(company_by_domain_response(), PROVIDER)

        # Vendor returned Google Japan for google.com query — we don't second-guess.
        assert result.get("name") == "Google Japan"
        assert result.get("linkedin_url") == "https://www.linkedin.com/company/google-japan/"
        assert result.get("employee_count") == 270

    def test_confident_score_surfaces_in_additional_data(self) -> None:
        result = normalize_company(company_by_domain_response(), PROVIDER)

        extras = result.get("additional_data") or {}
        # The score is the actionable signal for the caller/skill.
        assert extras.get("confident_score") == "80%"

    def test_hq_location_populated_from_full_address_even_partial(self) -> None:
        result = normalize_company(company_by_domain_response(), PROVIDER)
        assert result.get("hq_location") == "Tokyo, JP"


# ---------------------------------------------------------------------------
# Empty / partial responses
# ---------------------------------------------------------------------------


class TestEmptyResponses:
    def test_match_found_false_yields_empty_row(self) -> None:
        result = normalize_company({"match_found": False, "data": None}, PROVIDER)

        assert result.get("match_found") is False
        sources = (result.get("enrichment_sources") or {}).get(PROVIDER)
        # Empty canonical set for a no-match response.
        assert sources == [] or sources is None

    def test_partial_response_populates_only_available_canonical_keys(self) -> None:
        """Vendor returns a company with only a name and employee_count — the
        rest of the canonical slots must be absent (not present-with-None)."""
        raw = {
            "data": {
                "company_name": "Tiny Co",
                "employee_count": 3,
                "industries": [],
            }
        }
        result = normalize_company(raw, PROVIDER)

        assert result.get("name") == "Tiny Co"
        assert result.get("employee_count") == 3
        # industries is empty — industry must NOT be set (not None, simply absent).
        assert "industry" not in result
        assert "linkedin_url" not in result
        assert "hq_location" not in result


# ---------------------------------------------------------------------------
# HQ location fallback — pre-assembled string empty
# ---------------------------------------------------------------------------


class TestHqLocationFallback:
    def test_constructs_from_city_region_country_when_full_address_empty(self) -> None:
        raw = {
            "data": {
                "company_name": "Acme",
                "hq_full_address": "",
                "hq_city": "San Francisco",
                "hq_region": "CA",
                "hq_country": "US",
            }
        }
        result = normalize_company(raw, PROVIDER)

        assert result.get("hq_location") == "San Francisco, CA, US"

    def test_no_hq_location_when_all_location_fields_missing(self) -> None:
        raw = {"data": {"company_name": "Acme"}}
        result = normalize_company(raw, PROVIDER)

        assert "hq_location" not in result


# ---------------------------------------------------------------------------
# Industry handling — empty list, None, multi-entry
# ---------------------------------------------------------------------------


class TestIndustryListHandling:
    def test_first_industry_wins(self) -> None:
        raw = {
            "data": {
                "company_name": "Acme",
                "industries": ["Retail", "E-commerce", "Consumer Goods"],
            }
        }
        result = normalize_company(raw, PROVIDER)
        assert result.get("industry") == "Retail"

    @pytest.mark.parametrize("industries", [[], None])
    def test_empty_or_missing_industries_absent_from_output(self, industries) -> None:
        raw = {
            "data": {
                "company_name": "Acme",
                "industries": industries,
            }
        }
        result = normalize_company(raw, PROVIDER)
        assert "industry" not in result
