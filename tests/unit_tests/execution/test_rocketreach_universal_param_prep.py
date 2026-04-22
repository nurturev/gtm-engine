"""Unit tests for RocketReach param-prep helpers under the Universal API migration.

Scope: the four ``_prepare_*`` pure functions in
``server.execution.providers.rocketreach``. Pure-function tests — zero mocks,
per backend-unit-testing-blueprint §6 tier 1.

Covers (LLD §11.1, "Reveal-flag plumbing" + "Param prep (general)"):
    - Pinned Universal reveal_* + return_cached_emails flags on enrich_person.
    - Client cannot override the pinned reveal flags.
    - Missing-identifier validation on enrich_person still fires after the
      Universal migration.
    - Alias mapping (company → current_employer, etc.) on enrich_person.
    - Domain cleaner on enrich_person when only domain is supplied.
    - LinkedIn URL normalisation (adds https://, strips trailing slash).
    - search_people happy path + alias mapping + domain cleaning +
      pagination/ordering.
    - enrich_company accepts domain or name; otherwise raises.
    - search_companies payload shape + page_size cap at 100 + start cap at
      10 000.

Non-goals here: HTTP behaviour, async polling, error mapping — those live in
the sibling test modules.
"""

from __future__ import annotations

from typing import Any

import pytest

from server.core.exceptions import ProviderError
from server.execution.providers.rocketreach import (
    _prepare_enrich_company,
    _prepare_enrich_person,
    _prepare_search_companies,
    _prepare_search_people,
)


# ---------------------------------------------------------------------------
# enrich_person — Universal reveal-flag plumbing (LLD §3.2 T2)
# ---------------------------------------------------------------------------


class TestEnrichPersonRevealFlagDefaults:
    """Per requirements §5.1, when the caller does not set reveal flags, the
    provider applies a predictable default: professional email on, personal
    email and phone off, cached emails on. Universal rejects any call with
    zero reveal_* flags, so these defaults also guarantee validity."""

    def test_defaults_reveal_professional_email_true(self) -> None:
        params = _prepare_enrich_person({"linkedin_url": "https://linkedin.com/in/jane"})

        assert params["reveal_professional_email"] == "true"

    def test_defaults_reveal_personal_email_false(self) -> None:
        params = _prepare_enrich_person({"linkedin_url": "https://linkedin.com/in/jane"})

        assert params["reveal_personal_email"] == "false"

    def test_defaults_reveal_phone_false(self) -> None:
        params = _prepare_enrich_person({"linkedin_url": "https://linkedin.com/in/jane"})

        assert params["reveal_phone"] == "false"

    def test_pins_return_cached_emails_true(self) -> None:
        """Pins today's default against the vendor flip scheduled for
        2026-05-01 (requirements §5.1). Without this the migration would
        silently bump cost + change behaviour the day the vendor default
        changes. Also required to keep the call synchronous — see the
        override-ignored test below."""
        params = _prepare_enrich_person({"linkedin_url": "https://linkedin.com/in/jane"})

        assert params["return_cached_emails"] == "true"

    def test_flag_values_are_strings_for_query_string_travel(self) -> None:
        """They travel as GET query params, so they must be strings (not
        Python booleans) — httpx would render ``True`` as the string
        ``'True'`` and confuse the vendor."""
        params = _prepare_enrich_person({"linkedin_url": "https://linkedin.com/in/jane"})

        for key in (
            "reveal_professional_email",
            "reveal_personal_email",
            "reveal_phone",
            "return_cached_emails",
        ):
            assert isinstance(params[key], str), (
                f"{key} must be a string for GET query-string travel"
            )


class TestEnrichPersonClientCanOverrideRevealFlags:
    """Requirements §5.1 (updated): for reveal_professional_email,
    reveal_personal_email, and reveal_phone, caller-supplied values are
    honored. return_cached_emails remains hard-pinned (see the separate
    override-ignored test) because the alternative forces an async SMTP
    flow we do not support."""

    @pytest.mark.parametrize(
        "flag_key, client_value, expected",
        [
            ("reveal_professional_email", False, "false"),
            ("reveal_professional_email", "false", "false"),
            ("reveal_personal_email", True, "true"),
            ("reveal_personal_email", "true", "true"),
            ("reveal_phone", True, "true"),
            ("reveal_phone", "true", "true"),
        ],
    )
    def test_caller_values_pass_through_as_strings(
        self, flag_key: str, client_value: Any, expected: str
    ) -> None:
        params = _prepare_enrich_person({
            "linkedin_url": "https://linkedin.com/in/jane",
            flag_key: client_value,
        })

        assert params[flag_key] == expected

    def test_enrich_phone_number_alias_flips_reveal_phone(self) -> None:
        params = _prepare_enrich_person({
            "linkedin_url": "https://linkedin.com/in/jane",
            "enrich_phone_number": True,
        })

        assert params["reveal_phone"] == "true"


class TestEnrichPersonReturnCachedEmailsIsHardPinned:
    """Requirements §5.1 (updated): return_cached_emails is forced to
    ``"true"`` regardless of caller input. Setting it to false forces the
    vendor into live SMTP verification, which returns status="searching"
    and requires polling /universal/person/check_status. Our synchronous
    call contract does not support per-call polling."""

    def test_caller_false_is_overridden_to_true(self) -> None:
        params = _prepare_enrich_person({
            "linkedin_url": "https://linkedin.com/in/jane",
            "return_cached_emails": False,
        })

        assert params["return_cached_emails"] == "true"

    def test_caller_false_string_is_overridden_to_true(self) -> None:
        params = _prepare_enrich_person({
            "linkedin_url": "https://linkedin.com/in/jane",
            "return_cached_emails": "false",
        })

        assert params["return_cached_emails"] == "true"

    def test_override_attempt_logs_warning(self, caplog) -> None:
        import logging
        with caplog.at_level(logging.WARNING, logger="server.execution.providers.rocketreach"):
            _prepare_enrich_person({
                "linkedin_url": "https://linkedin.com/in/jane",
                "return_cached_emails": False,
            })

        assert any(
            "return_cached_emails=false" in rec.message for rec in caplog.records
        ), "expected a warning when caller tries to override return_cached_emails"


# ---------------------------------------------------------------------------
# enrich_person — identifier validation + mapping
# ---------------------------------------------------------------------------


class TestEnrichPersonIdentifierValidation:
    """At least one of (name+company, email, linkedin_url, id) is required
    — unchanged from v2, preserved under Universal."""

    def test_missing_all_identifiers_raises_provider_error(self) -> None:
        with pytest.raises(ProviderError) as exc_info:
            _prepare_enrich_person({})

        assert exc_info.value.provider == "rocketreach"
        # Message exists and mentions the accepted identifiers; we avoid
        # pinning the exact wording so future copy edits don't churn tests.
        assert "linkedin_url" in str(exc_info.value).lower()

    def test_title_alone_is_not_a_valid_identifier(self) -> None:
        """Title without name/company/email/linkedin_url/id is insufficient —
        regression guard against ``title`` being accidentally treated as an
        identifier key."""
        with pytest.raises(ProviderError):
            _prepare_enrich_person({"title": "CTO"})

    def test_domain_alone_is_not_a_valid_identifier(self) -> None:
        """Domain is mapped to ``current_employer`` but does not satisfy the
        identifier requirement on its own — needs a name alongside."""
        with pytest.raises(ProviderError):
            _prepare_enrich_person({"domain": "acme.com"})

    def test_linkedin_url_alone_is_sufficient(self) -> None:
        params = _prepare_enrich_person({
            "linkedin_url": "https://linkedin.com/in/jane",
        })

        assert params["linkedin_url"].startswith("https://")

    def test_email_alone_is_sufficient(self) -> None:
        params = _prepare_enrich_person({"email": "Jane@Acme.com"})

        assert params["email"] == "jane@acme.com"

    def test_name_plus_company_is_sufficient(self) -> None:
        params = _prepare_enrich_person({
            "name": "Jane Doe",
            "company": "Acme",
        })

        assert params["name"] == "Jane Doe"
        assert params["current_employer"] == "Acme"

    def test_rocketreach_id_alone_is_sufficient(self) -> None:
        params = _prepare_enrich_person({"id": "98765"})

        assert params["id"] == 98765


class TestEnrichPersonAliasesAndCleaners:
    """Caller ergonomics — we accept several alias keys and clean inputs."""

    def test_company_alias_maps_to_current_employer(self) -> None:
        params = _prepare_enrich_person({"name": "Jane Doe", "company": " Acme "})

        assert params["current_employer"] == "Acme"

    def test_linkedin_alias_maps_to_linkedin_url(self) -> None:
        params = _prepare_enrich_person({"linkedin": "www.linkedin.com/in/jane"})

        # Helper prepends https:// and strips trailing slash. We only pin the
        # contract (http URL, no trailing slash) — not the exact host-case
        # handling, which belongs to the URL helper's own tests.
        assert params["linkedin_url"].startswith("http")
        assert not params["linkedin_url"].endswith("/")

    def test_first_plus_last_name_compose_full_name(self) -> None:
        params = _prepare_enrich_person({
            "first_name": "Jane",
            "last_name": "Doe",
            "company": "Acme",
        })

        assert params["name"] == "Jane Doe"

    def test_domain_fills_in_current_employer_when_absent(self) -> None:
        """Domain maps to ``current_employer`` only when one isn't already
        specified — keeps explicit ``company`` primary."""
        params = _prepare_enrich_person({
            "name": "Jane Doe",
            "domain": "https://www.Acme.com/",
        })

        assert params["current_employer"] == "acme.com"

    def test_explicit_company_wins_over_domain(self) -> None:
        params = _prepare_enrich_person({
            "name": "Jane Doe",
            "company": "Acme Corp",
            "domain": "other.com",
        })

        assert params["current_employer"] == "Acme Corp"


# ---------------------------------------------------------------------------
# search_people — alias + pagination + ordering
# ---------------------------------------------------------------------------


class TestSearchPeoplePayloadShape:
    def test_builds_query_dict_with_titles_and_employer(self) -> None:
        payload = _prepare_search_people({
            "titles": ["VP Sales", "Head of Sales"],
            "company": "Acme",
        })

        assert payload["query"]["current_title"] == ["VP Sales", "Head of Sales"]
        assert payload["query"]["current_employer"] == ["Acme"]

    def test_single_string_title_is_wrapped_into_a_list(self) -> None:
        """The query-side fields always ship as lists — avoids a vendor-side
        400 when the string path accidentally leaks through."""
        payload = _prepare_search_people({"title": "VP Sales"})

        assert payload["query"]["current_title"] == ["VP Sales"]

    def test_comma_separated_string_is_split_into_items(self) -> None:
        payload = _prepare_search_people({"titles": "VP Sales, Head of Sales"})

        assert payload["query"]["current_title"] == ["VP Sales", "Head of Sales"]

    def test_domain_is_cleaned_across_the_list(self) -> None:
        payload = _prepare_search_people({
            "domain": ["https://www.Acme.com/", "https://Beta.io/"],
        })

        assert payload["query"]["company_domain"] == ["acme.com", "beta.io"]

    def test_defaults_page_size_to_25(self) -> None:
        payload = _prepare_search_people({"title": "VP Sales"})

        assert payload["page_size"] == 25

    @pytest.mark.parametrize(
        "given, expected",
        [(10, 10), (100, 100), (250, 100)],
    )
    def test_page_size_is_capped_at_100(self, given: int, expected: int) -> None:
        """RocketReach rejects page_size > 100. Cap client-side to avoid
        wasted round-trips."""
        payload = _prepare_search_people({"title": "VP", "limit": given})

        assert payload["page_size"] == expected

    @pytest.mark.parametrize(
        "given, expected",
        [(1, 1), (5000, 5000), (20000, 10000)],
    )
    def test_start_is_capped_at_vendor_offset_ceiling(
        self, given: int, expected: int
    ) -> None:
        """Vendor offset ceiling is 10 000 (requirements §8). Higher values
        would waste a round-trip on a guaranteed 400."""
        payload = _prepare_search_people({"title": "VP", "start": given})

        assert payload["start"] == expected

    def test_page_number_is_converted_to_1_indexed_start_offset(self) -> None:
        payload = _prepare_search_people({"title": "VP", "page": 3, "limit": 10})

        assert payload["start"] == 21  # (3 - 1) * 10 + 1

    @pytest.mark.parametrize(
        "order_by, expected_in_payload",
        [
            ("relevance", True),
            ("popularity", True),
            ("score", True),
            ("unsupported", False),
        ],
    )
    def test_order_by_is_whitelisted(
        self, order_by: str, expected_in_payload: bool
    ) -> None:
        payload = _prepare_search_people({"title": "VP", "order_by": order_by})

        assert ("order_by" in payload) is expected_in_payload


# ---------------------------------------------------------------------------
# enrich_company — identifier validation + domain cleaning
# ---------------------------------------------------------------------------


class TestEnrichCompanyValidation:
    def test_domain_is_cleaned(self) -> None:
        params = _prepare_enrich_company({"domain": "https://www.Acme.com/contact"})

        assert params["domain"] == "acme.com"

    def test_name_fallback_when_no_domain(self) -> None:
        params = _prepare_enrich_company({"name": "Acme Corp"})

        assert params["name"] == "Acme Corp"

    def test_domain_wins_over_name(self) -> None:
        """Requirements §5: domain is the more accurate identifier — prefer
        it over name when both are supplied."""
        params = _prepare_enrich_company({
            "domain": "acme.com",
            "name": "Something Else",
        })

        assert params["domain"] == "acme.com"
        assert "name" not in params

    def test_missing_identifiers_raises(self) -> None:
        with pytest.raises(ProviderError) as exc_info:
            _prepare_enrich_company({})

        assert exc_info.value.provider == "rocketreach"
        assert "domain" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# search_companies — payload shape + pagination
# ---------------------------------------------------------------------------


class TestSearchCompaniesPayloadShape:
    def test_builds_query_with_company_name_industry_geo(self) -> None:
        payload = _prepare_search_companies({
            "name": "Acme",
            "industry": "Software",
            "location": "San Francisco",
        })

        assert payload["query"]["company_name"] == ["Acme"]
        assert payload["query"]["industry"] == ["Software"]
        assert payload["query"]["geo"] == ["San Francisco"]

    def test_domain_list_is_cleaned(self) -> None:
        payload = _prepare_search_companies({
            "domain": ["https://www.Acme.com/", "beta.io"],
        })

        assert payload["query"]["domain"] == ["acme.com", "beta.io"]

    def test_defaults_page_size_to_25(self) -> None:
        payload = _prepare_search_companies({"industry": "Software"})

        assert payload["page_size"] == 25

    def test_page_size_capped_at_100(self) -> None:
        payload = _prepare_search_companies({"industry": "Software", "limit": 500})

        assert payload["page_size"] == 100

    def test_start_capped_at_10000(self) -> None:
        payload = _prepare_search_companies({"industry": "Software", "start": 50000})

        assert payload["start"] == 10000
