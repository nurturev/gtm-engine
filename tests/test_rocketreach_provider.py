"""Unit tests for RocketReach provider — parameter preparation and sanitisation.

Pure-function tests — no DB, no HTTP, no API keys needed.
Tests all 4 Universal API operation preparers plus helper functions.
"""

from __future__ import annotations

import pytest

from server.execution.providers.rocketreach import (
    RocketReachProvider,
    _clean_domain,
    _clean_linkedin_url,
    _ensure_list,
    _prepare_enrich_company,
    _prepare_enrich_person,
    _prepare_revenue_range,
    _prepare_search_companies,
    _prepare_search_people,
)
from server.core.exceptions import ProviderError


# ---------------------------------------------------------------------------
# Helper: _clean_domain
# ---------------------------------------------------------------------------


class TestCleanDomain:
    def test_bare_domain(self):
        assert _clean_domain("example.com") == "example.com"

    def test_with_https(self):
        assert _clean_domain("https://example.com") == "example.com"

    def test_with_http(self):
        assert _clean_domain("http://example.com") == "example.com"

    def test_with_www(self):
        assert _clean_domain("www.example.com") == "example.com"

    def test_with_https_and_www(self):
        assert _clean_domain("https://www.example.com") == "example.com"

    def test_with_path(self):
        assert _clean_domain("example.com/about") == "example.com"

    def test_with_https_and_path(self):
        assert _clean_domain("https://example.com/about/team") == "example.com"

    def test_trailing_dot(self):
        assert _clean_domain("example.com.") == "example.com"

    def test_uppercase(self):
        assert _clean_domain("EXAMPLE.COM") == "example.com"

    def test_whitespace(self):
        assert _clean_domain("  example.com  ") == "example.com"

    def test_empty_string(self):
        assert _clean_domain("") == ""

    def test_none_passthrough(self):
        assert _clean_domain(None) is None


# ---------------------------------------------------------------------------
# Helper: _ensure_list
# ---------------------------------------------------------------------------


class TestEnsureList:
    def test_none_returns_empty(self):
        assert _ensure_list(None) == []

    def test_list_passthrough(self):
        assert _ensure_list(["a", "b"]) == ["a", "b"]

    def test_string_single(self):
        assert _ensure_list("hello") == ["hello"]

    def test_csv_string(self):
        assert _ensure_list("a, b, c") == ["a", "b", "c"]

    def test_scalar(self):
        assert _ensure_list(42) == [42]


# ---------------------------------------------------------------------------
# Helper: _clean_linkedin_url
# ---------------------------------------------------------------------------


class TestCleanLinkedInUrl:
    def test_full_url(self):
        assert _clean_linkedin_url("https://linkedin.com/in/jdoe") == "https://linkedin.com/in/jdoe"

    def test_trailing_slash(self):
        assert _clean_linkedin_url("https://linkedin.com/in/jdoe/") == "https://linkedin.com/in/jdoe"

    def test_no_protocol(self):
        assert _clean_linkedin_url("linkedin.com/in/jdoe") == "https://linkedin.com/in/jdoe"

    def test_whitespace(self):
        assert _clean_linkedin_url("  https://linkedin.com/in/jdoe  ") == "https://linkedin.com/in/jdoe"


# ---------------------------------------------------------------------------
# Helper: _prepare_revenue_range
# ---------------------------------------------------------------------------


class TestPrepareRevenueRange:
    def test_both_provided(self):
        params = {"revenue_min": 1000000, "revenue_max": 50000000}
        assert _prepare_revenue_range(params, "revenue_min", "revenue_max") == ["1000000-50000000"]

    def test_only_min(self):
        result = _prepare_revenue_range({"revenue_min": 5000000, "revenue_max": ""}, "revenue_min", "revenue_max")
        assert result == ["5000000-1000000000000"]

    def test_only_max(self):
        result = _prepare_revenue_range({"revenue_min": "", "revenue_max": 10000000}, "revenue_min", "revenue_max")
        assert result == ["0-10000000"]

    def test_both_empty(self):
        assert _prepare_revenue_range({"revenue_min": "", "revenue_max": ""}, "revenue_min", "revenue_max") is None

    def test_both_missing(self):
        assert _prepare_revenue_range({}, "revenue_min", "revenue_max") is None

    def test_min_none_max_set(self):
        result = _prepare_revenue_range({"revenue_max": 999}, "revenue_min", "revenue_max")
        assert result == ["0-999"]

    def test_company_revenue_keys(self):
        params = {"company_revenue_min": 100, "company_revenue_max": 500}
        assert _prepare_revenue_range(params, "company_revenue_min", "company_revenue_max") == ["100-500"]


# ---------------------------------------------------------------------------
# _prepare_enrich_person (GET /universal/person/lookup)
# ---------------------------------------------------------------------------


class TestPrepareEnrichPerson:
    def test_linkedin_url(self):
        result = _prepare_enrich_person({"linkedin_url": "https://linkedin.com/in/jdoe"})
        assert result["linkedin_url"] == "https://linkedin.com/in/jdoe"

    def test_linkedin_alias(self):
        result = _prepare_enrich_person({"linkedin": "linkedin.com/in/jdoe"})
        assert result["linkedin_url"] == "https://linkedin.com/in/jdoe"

    def test_email(self):
        result = _prepare_enrich_person({"email": "John@Example.com"})
        assert result["email"] == "john@example.com"

    def test_name_and_employer(self):
        result = _prepare_enrich_person({"name": "John Doe", "current_employer": "Acme"})
        assert result["name"] == "John Doe"
        assert result["current_employer"] == "Acme"

    def test_first_last_name(self):
        result = _prepare_enrich_person({"first_name": "John", "last_name": "Doe", "company": "Acme"})
        assert result["name"] == "John Doe"
        assert result["current_employer"] == "Acme"

    def test_company_alias(self):
        result = _prepare_enrich_person({"name": "Jane", "company": "Acme Corp"})
        assert result["current_employer"] == "Acme Corp"

    def test_domain_as_employer_fallback(self):
        result = _prepare_enrich_person({"name": "Jane", "domain": "https://www.acme.com"})
        assert result["current_employer"] == "acme.com"

    def test_domain_not_used_if_employer_set(self):
        result = _prepare_enrich_person({"name": "Jane", "company": "Acme", "domain": "other.com"})
        assert result["current_employer"] == "Acme"
        assert "domain" not in result

    def test_id_lookup(self):
        result = _prepare_enrich_person({"id": "12345"})
        assert result["id"] == 12345

    def test_npi_number(self):
        result = _prepare_enrich_person({"npi_number": "1234567890"})
        assert result["npi_number"] == 1234567890

    def test_reveal_phone(self):
        result = _prepare_enrich_person({"email": "a@b.com", "reveal_phone": True})
        assert result["reveal_phone"] == "true"

    def test_enrich_phone_number_alias(self):
        result = _prepare_enrich_person({"email": "a@b.com", "enrich_phone_number": True})
        assert result["reveal_phone"] == "true"

    def test_reveal_professional_email(self):
        result = _prepare_enrich_person({"email": "a@b.com", "reveal_professional_email": True})
        assert result["reveal_professional_email"] == "true"

    def test_reveal_personal_email(self):
        result = _prepare_enrich_person({"email": "a@b.com", "reveal_personal_email": False})
        assert result["reveal_personal_email"] == "false"

    def test_webhook_id(self):
        result = _prepare_enrich_person({"email": "a@b.com", "webhook_id": "99"})
        assert result["webhook_id"] == 99

    def test_no_identifier_raises(self):
        with pytest.raises(ProviderError, match="Person lookup requires"):
            _prepare_enrich_person({"title": "CEO"})

    def test_name_without_employer_warns_but_succeeds(self):
        # Should succeed but log a warning — we just check it doesn't raise
        result = _prepare_enrich_person({"name": "John Doe"})
        assert result["name"] == "John Doe"


# ---------------------------------------------------------------------------
# _prepare_search_people (POST /universal/person/search)
# ---------------------------------------------------------------------------


class TestPrepareSearchPeople:
    def test_basic_title_search(self):
        result = _prepare_search_people({"title": "VP Engineering"})
        assert result["query"]["current_title"] == ["VP Engineering"]
        assert result["page_size"] == 25  # default

    def test_include_past_titles_toggle(self):
        result = _prepare_search_people({"title": "CTO", "include_past_titles": True})
        assert "current_title" not in result["query"]
        assert result["query"]["current_or_previous_title"] == ["CTO"]

    def test_include_past_titles_false(self):
        result = _prepare_search_people({"title": "CTO", "include_past_titles": False})
        assert result["query"]["current_title"] == ["CTO"]
        assert "current_or_previous_title" not in result["query"]

    def test_current_or_previous_title_direct(self):
        result = _prepare_search_people({"current_or_previous_title": "Founder"})
        assert result["query"]["current_or_previous_title"] == ["Founder"]

    def test_employer(self):
        result = _prepare_search_people({"company": "Google", "title": "SWE"})
        assert result["query"]["current_employer"] == ["Google"]

    def test_domain_cleaning(self):
        result = _prepare_search_people({"domain": "https://www.google.com/about", "title": "PM"})
        assert result["query"]["company_domain"] == ["google.com"]

    def test_multiple_domains(self):
        result = _prepare_search_people({"domain": "google.com, meta.com", "title": "PM"})
        assert result["query"]["company_domain"] == ["google.com", "meta.com"]

    def test_department(self):
        result = _prepare_search_people({"department": "Engineering", "title": "Manager"})
        assert result["query"]["department"] == ["Engineering"]

    def test_management_levels(self):
        result = _prepare_search_people({"seniority": "VP", "title": "Product"})
        assert result["query"]["management_levels"] == ["VP"]

    def test_previous_employer(self):
        result = _prepare_search_people({"previous_employer": "Microsoft", "title": "Engineer"})
        assert result["query"]["previous_employer"] == ["Microsoft"]

    def test_past_company_alias(self):
        result = _prepare_search_people({"past_company": "Amazon", "title": "PM"})
        assert result["query"]["previous_employer"] == ["Amazon"]

    def test_job_change_range_days(self):
        result = _prepare_search_people({"job_change_range_days": "90", "title": "SDR"})
        assert result["query"]["job_change_range_days"] == ["90"]

    def test_company_industry(self):
        result = _prepare_search_people({"industry": "SaaS", "title": "Sales"})
        assert result["query"]["company_industry"] == ["SaaS"]

    def test_company_industry_keywords(self):
        result = _prepare_search_people({"company_industry_keywords": "fintech, payments", "title": "PM"})
        assert result["query"]["company_industry_keywords"] == ["fintech", "payments"]

    def test_company_size(self):
        result = _prepare_search_people({"company_size": "51-200", "title": "Eng"})
        assert result["query"]["company_size"] == ["51-200"]

    def test_company_revenue_range(self):
        result = _prepare_search_people({
            "company_revenue_min": 1000000,
            "company_revenue_max": 50000000,
            "title": "Sales",
        })
        assert result["query"]["company_revenue"] == ["1000000-50000000"]

    def test_company_revenue_min_only(self):
        result = _prepare_search_people({
            "company_revenue_min": 5000000,
            "company_revenue_max": "",
            "title": "Sales",
        })
        assert result["query"]["company_revenue"] == ["5000000-1000000000000"]

    def test_company_revenue_max_only(self):
        result = _prepare_search_people({
            "company_revenue_min": "",
            "company_revenue_max": 10000000,
            "title": "Sales",
        })
        assert result["query"]["company_revenue"] == ["0-10000000"]

    def test_company_funding_min_max(self):
        result = _prepare_search_people({
            "company_funding_min": 1000000,
            "company_funding_max": 50000000,
            "title": "Eng",
        })
        assert result["query"]["company_funding_min"] == 1000000
        assert result["query"]["company_funding_max"] == 50000000

    def test_company_competitors(self):
        result = _prepare_search_people({"company_competitors": "Google, Meta", "title": "PM"})
        assert result["query"]["company_competitors"] == ["Google", "Meta"]

    def test_school(self):
        result = _prepare_search_people({"school": "IIT Kharagpur", "title": "Eng"})
        assert result["query"]["school"] == ["IIT Kharagpur"]

    def test_school_alias_education(self):
        result = _prepare_search_people({"education": "MIT", "title": "Eng"})
        assert result["query"]["school"] == ["MIT"]

    def test_degree_and_major(self):
        result = _prepare_search_people({"degree": "MBA", "major": "Finance", "title": "VP"})
        assert result["query"]["degree"] == ["MBA"]
        assert result["query"]["major"] == ["Finance"]

    def test_skills(self):
        result = _prepare_search_people({"skills": "Python, Machine Learning", "title": "Data"})
        assert result["query"]["skills"] == ["Python", "Machine Learning"]

    def test_years_experience(self):
        result = _prepare_search_people({"years_experience": "5-10", "title": "Eng"})
        assert result["query"]["years_experience"] == ["5-10"]

    def test_location_geo(self):
        result = _prepare_search_people({"location": "San Francisco", "title": "PM"})
        assert result["query"]["geo"] == ["San Francisco"]

    def test_city_state_country(self):
        result = _prepare_search_people({
            "city": "Austin",
            "state": "Texas",
            "country_code": "US",
            "title": "Eng",
        })
        assert result["query"]["city"] == ["Austin"]
        assert result["query"]["state"] == ["Texas"]
        assert result["query"]["country_code"] == ["US"]

    def test_growth_signal(self):
        # Growth values contain commas (min-max::Dept,TimeRange) so must be passed as list
        result = _prepare_search_people({"growth": ["10-50::Engineering,6m"], "title": "Eng"})
        assert result["query"]["growth"] == ["10-50::Engineering,6m"]

    def test_growth_signal_csv_splits(self):
        # If passed as string, _ensure_list splits on comma — caller must use list
        result = _prepare_search_people({"growth": "10-50::Engineering,6m", "title": "Eng"})
        assert result["query"]["growth"] == ["10-50::Engineering", "6m"]

    def test_job_change_signal(self):
        result = _prepare_search_people({"job_change_signal": "true", "title": "Sales"})
        assert result["query"]["job_change_signal"] == ["true"]

    def test_company_publicly_traded(self):
        result = _prepare_search_people({"company_publicly_traded": True, "title": "CFO"})
        assert result["query"]["company_publicly_traded"] is True

    def test_healthcare_filters(self):
        result = _prepare_search_people({
            "health_credentials": "MD",
            "health_specialization": "Cardiology",
            "title": "Doctor",
        })
        assert result["query"]["health_credentials"] == ["MD"]
        assert result["query"]["health_specialization"] == ["Cardiology"]

    def test_email_grade(self):
        result = _prepare_search_people({"email_grade": "A", "title": "Sales"})
        assert result["query"]["email_grade"] == ["A"]

    def test_exclude_filters(self):
        result = _prepare_search_people({
            "exclude_current_employer": "Google",
            "title": "Eng",
        })
        assert result["query"]["exclude_current_employer"] == ["Google"]

    def test_pagination_defaults(self):
        result = _prepare_search_people({"title": "CTO"})
        assert result["page_size"] == 25
        assert "start" not in result

    def test_custom_page_size(self):
        result = _prepare_search_people({"title": "CTO", "per_page": "50"})
        assert result["page_size"] == 50

    def test_page_size_capped_at_100(self):
        result = _prepare_search_people({"title": "CTO", "limit": "500"})
        assert result["page_size"] == 100

    def test_page_to_start_conversion(self):
        result = _prepare_search_people({"title": "CTO", "page": "3", "per_page": "25"})
        assert result["start"] == 51  # (3-1)*25 + 1

    def test_start_capped_at_10000(self):
        result = _prepare_search_people({"title": "CTO", "start": "99999"})
        assert result["start"] == 10000

    def test_order_by(self):
        result = _prepare_search_people({"title": "CTO", "order_by": "popularity"})
        assert result["order_by"] == "popularity"

    def test_invalid_order_by_ignored(self):
        result = _prepare_search_people({"title": "CTO", "order_by": "invalid"})
        assert "order_by" not in result


# ---------------------------------------------------------------------------
# _prepare_enrich_company (GET /universal/company/lookup)
# ---------------------------------------------------------------------------


class TestPrepareEnrichCompany:
    def test_domain(self):
        result = _prepare_enrich_company({"domain": "https://www.acme.com"})
        assert result["domain"] == "acme.com"

    def test_name(self):
        result = _prepare_enrich_company({"name": "Acme Corp"})
        assert result["name"] == "Acme Corp"

    def test_company_alias(self):
        result = _prepare_enrich_company({"company": "Acme Corp"})
        assert result["name"] == "Acme Corp"

    def test_id(self):
        result = _prepare_enrich_company({"id": "12345"})
        assert result["id"] == 12345

    def test_linkedin_url(self):
        result = _prepare_enrich_company({"linkedin_url": "linkedin.com/company/acme"})
        assert result["linkedin_url"] == "https://linkedin.com/company/acme"

    def test_ticker(self):
        result = _prepare_enrich_company({"ticker": "aapl"})
        assert result["ticker"] == "AAPL"

    def test_no_identifier_raises(self):
        with pytest.raises(ProviderError, match="Company lookup requires"):
            _prepare_enrich_company({})

    def test_domain_takes_priority(self):
        """When domain is provided, it should be used even if name is also given."""
        result = _prepare_enrich_company({"domain": "acme.com", "name": "Acme"})
        assert "domain" in result
        assert "name" not in result


# ---------------------------------------------------------------------------
# _prepare_search_companies (POST /universal/company/search)
# ---------------------------------------------------------------------------


class TestPrepareSearchCompanies:
    def test_basic_industry(self):
        result = _prepare_search_companies({"industry": "Technology"})
        assert result["query"]["industry"] == ["Technology"]

    def test_name(self):
        result = _prepare_search_companies({"company_name": "Acme"})
        assert result["query"]["name"] == ["Acme"]

    def test_domain_cleaning(self):
        result = _prepare_search_companies({"domain": "https://acme.com"})
        assert result["query"]["domain"] == ["acme.com"]

    def test_industry_keywords(self):
        result = _prepare_search_companies({"industry_keywords": "fintech, payments"})
        assert result["query"]["industry_keywords"] == ["fintech", "payments"]

    def test_location(self):
        result = _prepare_search_companies({"location": "San Francisco"})
        assert result["query"]["geo"] == ["San Francisco"]

    def test_employees(self):
        result = _prepare_search_companies({"size": "51-200"})
        assert result["query"]["employees"] == ["51-200"]

    def test_revenue_range(self):
        result = _prepare_search_companies({"revenue_min": 1000000, "revenue_max": 50000000})
        assert result["query"]["revenue"] == ["1000000-50000000"]

    def test_revenue_min_only(self):
        result = _prepare_search_companies({"revenue_min": 5000000, "revenue_max": ""})
        assert result["query"]["revenue"] == ["5000000-1000000000000"]

    def test_total_funding(self):
        result = _prepare_search_companies({"total_funding": "10M-50M"})
        assert result["query"]["total_funding"] == ["10M-50M"]

    def test_competitors(self):
        result = _prepare_search_companies({"competitors": "Google"})
        assert result["query"]["competitors"] == ["Google"]

    def test_publicly_traded(self):
        result = _prepare_search_companies({"publicly_traded": True})
        assert result["query"]["publicly_traded"] == [True]

    def test_techstack(self):
        result = _prepare_search_companies({"techstack": "Salesforce, HubSpot"})
        assert result["query"]["techstack"] == ["Salesforce", "HubSpot"]

    def test_growth(self):
        # Growth values contain commas so must be passed as list
        result = _prepare_search_companies({"growth": ["10-50::Engineering,6m"]})
        assert result["query"]["growth"] == ["10-50::Engineering,6m"]

    def test_news_signal(self):
        result = _prepare_search_companies({"news_signal": "funding"})
        assert result["query"]["news_signal"] == ["funding"]

    def test_intent(self):
        result = _prepare_search_companies({"intent": "CRM"})
        assert result["query"]["intent"] == ["CRM"]

    def test_website_category(self):
        result = _prepare_search_companies({"website_category": "ecommerce"})
        assert result["query"]["website_category"] == ["ecommerce"]

    def test_keyword(self):
        result = _prepare_search_companies({"keyword": "AI automation"})
        assert result["query"]["keyword"] == ["AI automation"]

    def test_description(self):
        result = _prepare_search_companies({"description": "enterprise software"})
        assert result["query"]["description"] == ["enterprise software"]

    def test_pagination(self):
        result = _prepare_search_companies({"industry": "Tech", "page": "2", "per_page": "50"})
        assert result["page_size"] == 50
        assert result["start"] == 51


# ---------------------------------------------------------------------------
# RocketReachProvider class — operation map and config
# ---------------------------------------------------------------------------


class TestRocketReachProviderConfig:
    def test_supported_operations(self):
        provider = RocketReachProvider()
        assert set(provider.supported_operations) == {
            "enrich_person",
            "search_people",
            "enrich_company",
            "search_companies",
        }

    def test_base_url(self):
        provider = RocketReachProvider()
        assert provider.BASE_URL == "https://api.rocketreach.co/api/v2"

    def test_operation_map_endpoints(self):
        provider = RocketReachProvider()
        assert provider._OPERATION_MAP["enrich_person"]["path"] == "/universal/person/lookup"
        assert provider._OPERATION_MAP["enrich_person"]["method"] == "GET"

        assert provider._OPERATION_MAP["search_people"]["path"] == "/universal/person/search"
        assert provider._OPERATION_MAP["search_people"]["method"] == "POST"

        assert provider._OPERATION_MAP["enrich_company"]["path"] == "/universal/company/lookup"
        assert provider._OPERATION_MAP["enrich_company"]["method"] == "GET"

        assert provider._OPERATION_MAP["search_companies"]["path"] == "/universal/company/search"
        assert provider._OPERATION_MAP["search_companies"]["method"] == "POST"

    def test_search_success_codes_include_201(self):
        """RocketReach search endpoints return 201, not 200."""
        provider = RocketReachProvider()
        assert 201 in provider._OPERATION_MAP["search_people"]["success_codes"]
        assert 201 in provider._OPERATION_MAP["search_companies"]["success_codes"]

    def test_lookup_success_codes_200_only(self):
        provider = RocketReachProvider()
        assert provider._OPERATION_MAP["enrich_person"]["success_codes"] == {200}
        assert provider._OPERATION_MAP["enrich_company"]["success_codes"] == {200}

    def test_provider_name(self):
        provider = RocketReachProvider()
        assert provider.name == "rocketreach"
