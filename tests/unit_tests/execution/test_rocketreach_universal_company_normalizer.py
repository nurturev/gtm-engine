"""Unit tests for the RocketReach company normalizer under the Universal
rename tolerance (LLD §3.6 T6 + requirements §6).

Under Universal, RocketReach ships several renamed keys on company rows
and a different pagination envelope on company search:

    domain          ← was email_domain
    industry        ← was industry_str
    employees       ← was num_employees      (canonical: employee_count)
    ticker          ← was ticker_symbol
    website         ← was website_url

Search pagination envelope:
    Universal:   {start, next, total}
    Legacy v2:   {total, thisPage, nextPage, pageSize}

The normalizer must accept either shape so a staged / rolled-back
deployment keeps working. These tests pin that tolerance.
"""

from __future__ import annotations

from server.execution.normalizer import normalize_company


PROVIDER = "rocketreach"


# ---------------------------------------------------------------------------
# Lookup rows — Universal vs legacy v2 key names
# ---------------------------------------------------------------------------


class TestCompanyLookupReadsUniversalKeys:
    def test_reads_domain_directly(self) -> None:
        raw = {"id": 1, "name": "Acme", "domain": "acme.com"}

        out = normalize_company(raw, PROVIDER)

        assert out.get("domain") == "acme.com"

    def test_reads_industry_directly(self) -> None:
        raw = {"id": 1, "name": "Acme", "industry": "Software"}

        out = normalize_company(raw, PROVIDER)

        assert out.get("industry") == "Software"

    def test_reads_employees_as_employee_count(self) -> None:
        raw = {"id": 1, "name": "Acme", "employees": 500}

        out = normalize_company(raw, PROVIDER)

        assert out.get("employee_count") == 500

    def test_reads_ticker_in_additional_data(self) -> None:
        raw = {"id": 1, "name": "Acme", "ticker": "ACME"}

        out = normalize_company(raw, PROVIDER)

        assert out.get("additional_data", {}).get("ticker") == "ACME"

    def test_reads_website_in_additional_data(self) -> None:
        raw = {"id": 1, "name": "Acme", "website": "https://acme.com"}

        out = normalize_company(raw, PROVIDER)

        assert out.get("additional_data", {}).get("website") == "https://acme.com"


class TestCompanyLookupAcceptsLegacyV2Keys:
    """A staged rollout may still see legacy v2 shapes — or a rollback can
    surface them again. The normalizer must degrade gracefully."""

    def test_reads_email_domain_when_domain_is_absent(self) -> None:
        raw = {"id": 1, "name": "Acme", "email_domain": "acme.com"}

        out = normalize_company(raw, PROVIDER)

        assert out.get("domain") == "acme.com"

    def test_reads_industry_str_when_industry_is_absent(self) -> None:
        raw = {"id": 1, "name": "Acme", "industry_str": "Software"}

        out = normalize_company(raw, PROVIDER)

        assert out.get("industry") == "Software"

    def test_reads_num_employees_when_employees_is_absent(self) -> None:
        raw = {"id": 1, "name": "Acme", "num_employees": 500}

        out = normalize_company(raw, PROVIDER)

        assert out.get("employee_count") == 500

    def test_reads_ticker_symbol_when_ticker_is_absent(self) -> None:
        raw = {"id": 1, "name": "Acme", "ticker_symbol": "ACME"}

        out = normalize_company(raw, PROVIDER)

        assert out.get("additional_data", {}).get("ticker") == "ACME"

    def test_reads_website_url_when_website_is_absent(self) -> None:
        raw = {"id": 1, "name": "Acme", "website_url": "https://acme.com"}

        out = normalize_company(raw, PROVIDER)

        assert out.get("additional_data", {}).get("website") == "https://acme.com"


class TestUniversalWinsOverLegacyWhenBothPresent:
    """Transition window: if the vendor ever ships both keys, prefer the
    Universal name — that's the forward-compatible choice."""

    def test_domain_preferred_over_email_domain(self) -> None:
        raw = {
            "id": 1, "name": "Acme",
            "domain": "acme.com",
            "email_domain": "old-acme.com",
        }

        out = normalize_company(raw, PROVIDER)

        assert out.get("domain") == "acme.com"

    def test_industry_preferred_over_industry_str(self) -> None:
        raw = {
            "id": 1, "name": "Acme",
            "industry": "Software",
            "industry_str": "Legacy Software",
        }

        out = normalize_company(raw, PROVIDER)

        assert out.get("industry") == "Software"


# ---------------------------------------------------------------------------
# Search pagination envelope tolerance
# ---------------------------------------------------------------------------


class TestCompanySearchPaginationTolerance:
    """Requirements §6 / LLD §4.2 — the normalizer reads either pagination
    shape so the client never cares which API version actually answered."""

    def _company_row(self) -> dict:
        return {"id": 1, "name": "Acme", "domain": "acme.com"}

    def test_universal_pagination_shape(self) -> None:
        raw = {
            "companies": [self._company_row()],
            "pagination": {"start": 1, "next": 26, "total": 42},
        }

        out = normalize_company(raw, PROVIDER)

        assert out.get("total") == 42
        assert out.get("page") == 1

    def test_legacy_v2_pagination_shape(self) -> None:
        """Legacy v2 ships ``{total, thisPage, nextPage, pageSize}``. When
        this is the only envelope present, the normalizer reads it
        verbatim."""
        raw = {
            "companies": [self._company_row()],
            "pagination": {
                "total": 42, "thisPage": 2, "nextPage": 3, "pageSize": 25,
            },
        }

        out = normalize_company(raw, PROVIDER)

        assert out.get("total") == 42
        assert out.get("page") == 2
        assert out.get("per_page") == 25

    def test_missing_pagination_falls_back_to_row_count(self) -> None:
        """If the vendor ships nothing (edge case), total defaults to the
        row count — consumers don't get ``None`` and blow up during
        pagination math."""
        raw = {"companies": [self._company_row(), self._company_row()]}

        out = normalize_company(raw, PROVIDER)

        assert out.get("total") == 2
