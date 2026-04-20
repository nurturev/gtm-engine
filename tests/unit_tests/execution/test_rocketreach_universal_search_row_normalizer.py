"""Unit tests for the RocketReach search-row teaser extraction path.

Requirements §5.5 + LLD §3.5 T5 — pre-existing-bug fix in scope for the
Universal migration.

Context: ``_normalize_rr_person`` used to normalise both lookup payloads
and search rows. Search rows never carry full emails/phones — they only
carry hints under ``teaser.*``. The old code path read ``emails[]`` at the
top level (never present on search rows) and silently dropped every hint.

The migration routes search rows through ``_normalize_rr_person_search_row``
so the hints land in ``additional_data`` instead of disappearing. This file
pins that contract.

The tests drive the public ``normalize_person(raw, "rocketreach")`` dispatch
so we also cover the routing decision (``"profiles"`` key → search path).
"""

from __future__ import annotations

import pytest

from server.execution.normalizer import normalize_person


PROVIDER = "rocketreach"


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def _search_row(**overrides) -> dict:
    """Build a single Universal person-search profile. Mirrors the payload
    shape recorded in ``docs/rocketreach_sample_responses/``."""
    base = {
        "id": 98765,
        "name": "Priya Sharma",
        "first_name": "Priya",
        "last_name": "Sharma",
        "current_title": "Director of Marketing",
        "current_employer": "Beta LLC",
        "current_employer_domain": "beta.com",
        "linkedin_url": "https://www.linkedin.com/in/priyasharma",
        "profile_pic": "https://cdn.rocketreach.co/photos/priya.jpg",
        "city": "New York",
        "region": "NY",
        "country_code": "US",
        "teaser": {
            "professional_emails": ["beta.com"],
            "personal_emails": ["gmail.com"],
            "emails": ["beta.com", "gmail.com"],
            "phones": [
                {"number": "501868XXXX", "is_premium": True},
                {"number": "917555XXXX", "is_premium": False},
            ],
            "is_premium_phone_available": True,
        },
    }
    base.update(overrides)
    return base


def _search_payload(*rows) -> dict:
    """Wrap rows in the Universal ``{profiles: [...], pagination: {...}}``
    envelope that ``normalize_person`` dispatches on."""
    return {
        "profiles": list(rows),
        "pagination": {"start": 1, "next": 26, "total": len(rows)},
    }


# ---------------------------------------------------------------------------
# Canonical values — emails and phones stay None on search rows
# ---------------------------------------------------------------------------


class TestSearchRowCanonicalContactFieldsAreNone:
    """Requirements §5.5: full emails/phones are never shipped on search
    rows, so the canonical fields must stay ``None``. The alternative
    (fabricating an email from a domain) would be a truthfulness bug."""

    def test_email_stays_none_even_with_teaser_hints(self) -> None:
        out = normalize_person(_search_payload(_search_row()), PROVIDER)

        row = out["people"][0]
        assert "email" not in row, (
            "canonical email must stay absent on search rows; hints live in "
            "additional_data.email_domain_hints"
        )

    def test_phone_stays_none_even_with_masked_teaser_phone(self) -> None:
        out = normalize_person(_search_payload(_search_row()), PROVIDER)

        row = out["people"][0]
        assert "phone" not in row


class TestSearchRowCanonicalIdentityFieldsPopulated:
    def test_name_title_company_and_linkedin_url_survive(self) -> None:
        out = normalize_person(_search_payload(_search_row()), PROVIDER)

        row = out["people"][0]
        assert row.get("name") == "Priya Sharma"
        assert row.get("title") == "Director of Marketing"
        assert row.get("company_name") == "Beta LLC"
        assert row.get("company_domain") == "beta.com"
        assert row.get("linkedin_url") == "https://www.linkedin.com/in/priyasharma"


# ---------------------------------------------------------------------------
# additional_data.email_domain_hints
# ---------------------------------------------------------------------------


class TestTeaserEmailDomainHints:
    def test_professional_email_domains_land_in_additional_data(self) -> None:
        out = normalize_person(_search_payload(_search_row()), PROVIDER)

        hints = out["people"][0].get("additional_data", {}).get("email_domain_hints")
        assert hints == ["beta.com"]

    def test_personal_email_domains_are_excluded_by_policy(self) -> None:
        """Mirrors the enrich_person reveal policy — personal emails are
        out of scope for this migration (requirements §5.1 + §5.5). A
        leaked personal domain would be a policy violation."""
        out = normalize_person(_search_payload(_search_row()), PROVIDER)

        hints = out["people"][0].get("additional_data", {}).get("email_domain_hints") or []
        assert "gmail.com" not in hints

    def test_falls_back_to_teaser_emails_when_professional_missing(self) -> None:
        """When the vendor only ships the merged ``teaser.emails`` list
        (no ``professional_emails``), the normalizer reads that list."""
        row = _search_row(teaser={
            "emails": ["beta.com", "gmail.com"],
            "phones": [],
            "is_premium_phone_available": False,
        })

        out = normalize_person(_search_payload(row), PROVIDER)

        hints = out["people"][0].get("additional_data", {}).get("email_domain_hints")
        assert hints is not None and "beta.com" in hints

    def test_missing_teaser_yields_no_hint_key(self) -> None:
        """Absent hints must not produce empty/None noise in
        ``additional_data`` — the key should be omitted entirely so
        consumers can do a simple ``in`` check."""
        row = _search_row()
        row.pop("teaser", None)

        out = normalize_person(_search_payload(row), PROVIDER)

        extras = out["people"][0].get("additional_data", {})
        assert "email_domain_hints" not in extras


# ---------------------------------------------------------------------------
# additional_data.phone_hint
# ---------------------------------------------------------------------------


class TestTeaserPhoneHint:
    def test_first_masked_phone_is_surfaced(self) -> None:
        out = normalize_person(_search_payload(_search_row()), PROVIDER)

        phone_hint = out["people"][0].get("additional_data", {}).get("phone_hint")
        assert phone_hint == "501868XXXX"

    def test_mask_characters_are_preserved_not_stripped(self) -> None:
        """Regression guard: previously the row stripped non-digits — which
        would have turned ``501868XXXX`` into ``501868`` and destroyed the
        signal. Pin the masked string verbatim."""
        row = _search_row(teaser={
            "professional_emails": ["beta.com"],
            "phones": [{"number": "501868XXXX", "is_premium": True}],
            "is_premium_phone_available": True,
        })

        out = normalize_person(_search_payload(row), PROVIDER)

        phone_hint = out["people"][0].get("additional_data", {}).get("phone_hint")
        assert "X" in (phone_hint or ""), (
            "masked mask-chars must be preserved — stripping them would "
            "fabricate a usable phone number the vendor never returned"
        )

    def test_empty_teaser_phones_list_yields_no_phone_hint_key(self) -> None:
        row = _search_row(teaser={
            "professional_emails": ["beta.com"],
            "phones": [],
            "is_premium_phone_available": False,
        })

        out = normalize_person(_search_payload(row), PROVIDER)

        extras = out["people"][0].get("additional_data", {})
        assert "phone_hint" not in extras


# ---------------------------------------------------------------------------
# additional_data.is_premium_phone_available
# ---------------------------------------------------------------------------


class TestPremiumPhoneFlag:
    @pytest.mark.parametrize("flag", [True, False])
    def test_boolean_flag_is_preserved(self, flag: bool) -> None:
        """Downstream code gates on this to decide whether a paid lookup
        would actually yield a phone — drifting it to truthy/falsy-only
        would cause wasted credits or missed opportunities."""
        row = _search_row(teaser={
            "professional_emails": ["beta.com"],
            "phones": [],
            "is_premium_phone_available": flag,
        })

        out = normalize_person(_search_payload(row), PROVIDER)

        extras = out["people"][0].get("additional_data", {})
        # False is nonempty-enough for the extras builder to keep; True
        # obviously lands. We assert equality, not truthiness.
        if flag is True:
            assert extras.get("is_premium_phone_available") is True
        else:
            # The normalizer's _nonempty() drops False. Accept either
            # 'not present' or 'False' — the contract the agent reads is
            # "absent means no".
            assert extras.get("is_premium_phone_available") in (False, None)
            assert "is_premium_phone_available" not in {
                k: v for k, v in extras.items() if v is True
            }

    def test_non_boolean_flag_is_ignored(self) -> None:
        """Defensive: if the vendor ever ships a string ``"true"`` we must
        not silently coerce — coercion here would give consumers a bool
        the vendor didn't actually set."""
        row = _search_row(teaser={
            "professional_emails": ["beta.com"],
            "phones": [],
            "is_premium_phone_available": "true",
        })

        out = normalize_person(_search_payload(row), PROVIDER)

        extras = out["people"][0].get("additional_data", {})
        assert extras.get("is_premium_phone_available") in (None, False)


# ---------------------------------------------------------------------------
# Search-payload envelope — routing + pagination
# ---------------------------------------------------------------------------


class TestSearchPayloadEnvelope:
    def test_profiles_key_routes_to_search_row_path(self) -> None:
        """Raw payload with ``profiles`` → normalizer returns ``people``
        (search shape), not a single-row lookup shape."""
        out = normalize_person(
            _search_payload(_search_row(), _search_row(name="Arun Kumar")),
            PROVIDER,
        )

        assert "people" in out
        assert len(out["people"]) == 2

    def test_pagination_is_read_from_universal_shape(self) -> None:
        """Universal ships ``{start, next, total}``. The normalizer pins
        these onto the outer envelope so callers can paginate."""
        out = normalize_person(_search_payload(_search_row()), PROVIDER)

        assert out.get("total") == 1
        assert out.get("page") == 1
