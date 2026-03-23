"""Search commands: people, companies."""

from __future__ import annotations

import re
import sys
from typing import Any
from urllib.parse import urlparse

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import print_error, print_table, print_warning, spinner


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated

    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


def _clean_domain(raw: str) -> str:
    """Clean a domain: https://www.example.com/path -> example.com"""
    d = raw.strip().lower()
    if re.match(r"^https?://", d):
        parsed = urlparse(d)
        d = parsed.hostname or d
    else:
        d = d.split("/")[0]
    if d.startswith("www."):
        d = d[4:]
    return d.rstrip(".")


@click.group("search")
def search() -> None:
    """Search for people and companies via Apollo, RocketReach, and more."""


@search.command()
@click.option("--title", default=None, help="Job title(s) to search for. Comma-separated for multiple (e.g. 'VP Sales,Director Marketing').")
@click.option("--company", default=None, help="Current company name filter.")
@click.option("--domain", default=None, help="Company domain (auto-cleaned). Comma-separated for multiple.")
@click.option("--location", default=None, help="Location filter (e.g. 'California, US').")
@click.option("--seniority", default=None, help="Seniority filter (e.g. 'vp,director,c_suite').")
@click.option("--school", default=None, help="University/school attended (e.g. 'IIT Kharagpur'). Comma-separated.")
@click.option("--past-company", default=None, help="Previous employer (e.g. 'Mindtickle'). Comma-separated.")
@click.option("--keyword", default=None, help="Free-text keyword search across profiles.")
@click.option(
    "--provider",
    default=None,
    type=click.Choice(["apollo", "rocketreach"]),
    help="Force a specific provider. Auto-selects rocketreach for --school/--past-company.",
)
@click.option("--limit", default=25, type=int, help="Results per page (max 100).")
@click.option("--page", default=1, type=int, help="Page number.")
@click.option("--json-output", "json_out", is_flag=True, help="Output raw JSON instead of table.")
def people(
    title: str | None,
    company: str | None,
    domain: str | None,
    location: str | None,
    seniority: str | None,
    school: str | None,
    past_company: str | None,
    keyword: str | None,
    provider: str | None,
    limit: int,
    page: int,
    json_out: bool,
) -> None:
    """Search for people by title, company, location, school, and more.

    \b
    Examples:
        nrev-lite searchpeople --title "VP Sales" --domain google.com
        nrev-lite searchpeople --title CTO --location "San Francisco"
        nrev-lite searchpeople --school "IIT Kharagpur" --title "Director"
        nrev-lite searchpeople --past-company Mindtickle --title "VP Sales"
        nrev-lite searchpeople --title "Head of Growth" --provider rocketreach
        nrev-lite searchpeople --keyword "fintech" --title "CRO"

    \b
    Providers:
        apollo       - Default. Best for title/company/domain searches.
        rocketreach  - Best for school/alumni and past-company searches.
                       Automatically selected when --school or --past-company is used.

    \b
    Note: People Search does NOT return verified email addresses.
    Use 'nrev-lite enrich person' to get contact details for specific people.
    """
    _require_auth()
    client = NrvClient()

    # Auto-select provider based on filters
    if provider is None:
        if school or past_company:
            provider = "rocketreach"
            click.echo(f"  Auto-selected provider: {provider} (best for school/alumni searches)")

    is_rr = provider == "rocketreach"
    params: dict[str, Any] = {"per_page": min(limit, 100), "page": min(page, 500)}

    if title:
        titles_list = [t.strip() for t in title.split(",")]
        if is_rr:
            params["current_title"] = titles_list
        else:
            params["person_titles"] = titles_list
    if company:
        if is_rr:
            params["current_employer"] = [company.strip()]
        else:
            params["q_organization_name"] = company.strip()
    if domain:
        cleaned = [_clean_domain(d) for d in domain.split(",") if d.strip()]
        if cleaned:
            if is_rr:
                params["company_domain"] = cleaned
            else:
                params["q_organization_domains"] = "\n".join(cleaned)
    if location:
        locs = [loc.strip() for loc in location.split(",")]
        if is_rr:
            params["geo"] = locs
        else:
            params["person_locations"] = locs
    if seniority:
        seniorities = [s.strip() for s in seniority.split(",")]
        if is_rr:
            params["management_levels"] = seniorities
        else:
            params["person_seniorities"] = seniorities
    if school:
        params["school"] = [s.strip() for s in school.split(",")]
    if past_company:
        params["previous_employer"] = [c.strip() for c in past_company.split(",")]
    if keyword:
        if is_rr:
            params["keyword"] = keyword.strip()
        else:
            params["q_keywords"] = keyword.strip()

    # Validate: need at least one filter
    filter_keys = {
        "person_titles", "q_organization_name", "q_organization_domains",
        "person_locations", "current_title", "current_employer", "company_domain",
        "geo", "school", "previous_employer", "keyword", "q_keywords",
        "person_seniorities", "management_levels",
    }
    if not any(k in params for k in filter_keys):
        print_error(
            "At least one search filter is required.\n"
            "Use --title, --company, --domain, --location, --school, or --past-company."
        )
        sys.exit(1)

    try:
        with spinner(f"Searching people via {provider or 'apollo'}..."):
            result = client.execute(
                "search_people",
                params,
                providers=[provider] if provider else None,
            )
    except NrvApiError as exc:
        print_error(f"Search failed: {exc.message}")
        sys.exit(1)

    if json_out:
        from nrev_lite.utils.display import print_json

        print_json(result)
    else:
        _display_people_results(result)


@search.command()
@click.option("--name", default=None, help="Company name to search.")
@click.option("--industry", default=None, help="Industry filter.")
@click.option("--size", default=None, help="Employee count range (e.g. '50-200').")
@click.option("--location", default=None, help="Location filter.")
@click.option("--domain", default=None, help="Company domain filter.")
@click.option("--provider", default=None, type=click.Choice(["apollo", "rocketreach"]), help="Force a provider.")
@click.option("--limit", default=25, type=int, help="Results per page (max 100).")
@click.option("--page", default=1, type=int, help="Page number.")
def companies(
    name: str | None,
    industry: str | None,
    size: str | None,
    location: str | None,
    domain: str | None,
    provider: str | None,
    limit: int,
    page: int,
) -> None:
    """Search for companies by name, industry, size, and more.

    \b
    Examples:
        nrev-lite searchcompanies --industry "SaaS" --size "50-200"
        nrev-lite searchcompanies --name "Stripe" --location "US"
        nrev-lite searchcompanies --domain stripe.com
    """
    _require_auth()
    client = NrvClient()

    params: dict[str, Any] = {"per_page": min(limit, 100), "page": min(page, 500)}
    if name:
        params["q_organization_name"] = name.strip()
    if industry:
        params["organization_industry_tag_ids"] = [i.strip() for i in industry.split(",")]
    if size:
        params["organization_num_employees_ranges"] = [size.strip()]
    if location:
        params["organization_locations"] = [loc.strip() for loc in location.split(",")]
    if domain:
        cleaned = [_clean_domain(d) for d in domain.split(",") if d.strip()]
        if cleaned:
            params["q_organization_domains"] = "\n".join(cleaned)

    if not any(k for k in params if k not in ("per_page", "page")):
        print_error("At least one search filter is required.")
        sys.exit(1)

    try:
        with spinner(f"Searching companies via {provider or 'apollo'}..."):
            result = client.execute(
                "search_companies",
                params,
                providers=[provider] if provider else None,
            )
    except NrvApiError as exc:
        print_error(f"Search failed: {exc.message}")
        sys.exit(1)

    _display_company_results(result)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _display_people_results(result: dict[str, Any]) -> None:
    """Render people search results as a Rich table."""
    data = result.get("result") or result.get("data") or result

    people_list = []
    total = None
    credits_charged = result.get("credits_charged")

    if isinstance(data, dict):
        # Apollo returns "people", RocketReach returns "profiles"
        people_list = data.get("people", data.get("profiles", []))
        total = data.get("total", data.get("pagination", {}).get("total"))
    elif isinstance(data, list):
        people_list = data

    if not people_list:
        print_warning("No people found matching your search.")
        return

    # Normalize field names (RocketReach uses different names)
    cols = ["name", "title", "company", "location", "seniority"]
    columns = [c.replace("_", " ").title() for c in cols]
    rows = []
    for p in people_list:
        name = p.get("name", "")
        if not name:
            fn = p.get("first_name", "")
            ln = p.get("last_name", "")
            name = f"{fn} {ln}".strip()
        title = p.get("title", p.get("current_title", ""))
        company = p.get("company", p.get("current_employer", ""))
        location = p.get("location", p.get("city", ""))
        seniority = p.get("seniority", p.get("management_level", ""))
        rows.append([name, title, company, location, seniority])

    print_table(columns, rows, title="People Search Results")

    footer = []
    if total is not None:
        footer.append(f"Showing {len(people_list)} of {total:,} results")
    if credits_charged is not None:
        footer.append(f"Credits: {credits_charged}")
    if footer:
        click.echo(f"\n{' | '.join(footer)}")

    click.echo("\nTip: Use 'nrev-lite enrich person --email <email>' to get contact details.")


def _display_company_results(result: dict[str, Any]) -> None:
    """Render company search results as a Rich table."""
    data = result.get("result") or result.get("data") or result

    companies_list = []
    total = None
    if isinstance(data, dict):
        companies_list = data.get("companies", [])
        total = data.get("total")
    elif isinstance(data, list):
        companies_list = data

    if not companies_list:
        print_warning("No companies found matching your search.")
        return

    cols = ["name", "domain", "industry", "employee_count", "location"]
    columns = [c.replace("_", " ").title() for c in cols]
    rows = [[str(c.get(col, "") or "") for col in cols] for c in companies_list]
    print_table(columns, rows, title="Company Search Results")

    if total is not None:
        click.echo(f"\nShowing {len(companies_list)} of {total} results")
