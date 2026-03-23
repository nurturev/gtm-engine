"""Enrich commands: person, company, batch."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import (
    print_error,
    print_json,
    print_success,
    print_table,
    print_warning,
    spinner,
)


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated

    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


def _clean_domain(raw: str) -> str:
    """Clean a domain to Apollo's required format: example.com

    Handles: https://www.example.com/path -> example.com
    """
    d = raw.strip().lower()
    if re.match(r"^https?://", d):
        parsed = urlparse(d)
        d = parsed.hostname or d
    else:
        d = d.split("/")[0]
    if d.startswith("www."):
        d = d[4:]
    return d.rstrip(".")


@click.group("enrich")
def enrich() -> None:
    """Enrich people and companies with data from Apollo and other providers."""


@enrich.command()
@click.option("--email", default=None, help="Email address to enrich.")
@click.option("--name", default=None, help="Full name (e.g. 'John Doe').")
@click.option("--first-name", default=None, help="First name.")
@click.option("--last-name", default=None, help="Last name.")
@click.option("--domain", default=None, help="Company domain (e.g. google.com).")
@click.option("--company", default=None, help="Company name.")
@click.option("--linkedin", default=None, help="LinkedIn profile URL.")
@click.option("--reveal-emails", is_flag=True, help="Include personal emails (Apollo credits).")
@click.option("--reveal-phone", is_flag=True, help="Include phone numbers (Apollo credits).")
@click.option("--provider", default=None, help="Force a specific provider (default: apollo).")
@click.option("--dry-run", is_flag=True, help="Show what would run without executing.")
def person(
    email: str | None,
    name: str | None,
    first_name: str | None,
    last_name: str | None,
    domain: str | None,
    company: str | None,
    linkedin: str | None,
    reveal_emails: bool,
    reveal_phone: bool,
    provider: str | None,
    dry_run: bool,
) -> None:
    """Enrich a person by email, name+domain, or LinkedIn URL.

    \b
    Examples:
        nrev-lite enrich person --email john@acme.com
        nrev-lite enrich person --name "John Doe" --domain acme.com
        nrev-lite enrich person --linkedin https://linkedin.com/in/johndoe
        nrev-lite enrich person --email john@acme.com --reveal-phone
    """
    _require_auth()

    # Build params — at least one identifier required
    params: dict[str, Any] = {}
    if email:
        params["email"] = email.strip().lower()
    if name:
        params["name"] = name.strip()
    if first_name:
        params["first_name"] = first_name.strip()
    if last_name:
        params["last_name"] = last_name.strip()
    if domain:
        params["domain"] = _clean_domain(domain)
    if company:
        params["organization_name"] = company.strip()
    if linkedin:
        params["linkedin_url"] = linkedin.strip()
    if reveal_emails:
        params["reveal_personal_emails"] = True
    if reveal_phone:
        params["reveal_phone_number"] = True

    if not params:
        print_error(
            "At least one identifier is required.\n"
            "Use --email, --name + --domain, or --linkedin."
        )
        sys.exit(1)

    if dry_run:
        print_warning("Dry run — showing what would be sent:")
        for k, v in params.items():
            click.echo(f"  {k}: {v}")
        return

    client = NrvClient()
    try:
        with spinner("Enriching person..."):
            result = client.execute("enrich_person", params, providers=[provider] if provider else None)
    except NrvApiError as exc:
        print_error(f"Enrichment failed: {exc.message}")
        sys.exit(1)

    _display_person_result(result)


@enrich.command()
@click.option("--domain", required=True, help="Company domain (e.g. google.com, https://www.google.com).")
@click.option("--provider", default=None, help="Force a specific provider.")
@click.option("--dry-run", is_flag=True, help="Show what would run without executing.")
def company(domain: str, provider: str | None, dry_run: bool) -> None:
    """Enrich a company by domain.

    \b
    The domain is automatically cleaned:
        https://www.google.com  ->  google.com
        www.acme.io/about       ->  acme.io

    \b
    Examples:
        nrev-lite enrich company --domain google.com
        nrev-lite enrich company --domain https://www.acme.io
    """
    _require_auth()

    clean = _clean_domain(domain)
    if clean != domain.strip().lower():
        click.echo(f"  Domain cleaned: {domain} -> {clean}")

    params: dict[str, Any] = {"domain": clean}

    if dry_run:
        print_warning(f"Dry run — would enrich company: {clean}")
        return

    client = NrvClient()
    try:
        with spinner(f"Enriching {clean}..."):
            result = client.execute("enrich_company", params, providers=[provider] if provider else None)
    except NrvApiError as exc:
        print_error(f"Enrichment failed: {exc.message}")
        sys.exit(1)

    _display_company_result(result)


@enrich.command()
@click.option(
    "--file",
    "file_path",
    required=True,
    type=click.Path(exists=True),
    help="CSV file with records to enrich.",
)
@click.option(
    "--strategy",
    type=click.Choice(["parallel", "waterfall"]),
    default="waterfall",
    help="Execution strategy.",
)
@click.option("--dry-run", is_flag=True, help="Show what would run without executing.")
def batch(file_path: str, strategy: str, dry_run: bool) -> None:
    """Enrich a batch of records from a CSV file.

    \b
    CSV should have columns like: email, first_name, last_name, domain
    Domains are automatically cleaned (https://www.x.com -> x.com).
    """
    _require_auth()

    path = Path(file_path)
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        items = list(reader)

    if not items:
        print_warning("CSV file is empty.")
        return

    # Clean domains in all records
    for item in items:
        if item.get("domain"):
            item["domain"] = _clean_domain(item["domain"])
        if item.get("company_domain"):
            item["company_domain"] = _clean_domain(item["company_domain"])

    click.echo(f"Loaded {len(items)} records from {path.name}")

    if len(items) > 10:
        # Cost estimate
        cost = len(items) * 1.0  # 1 credit per record (estimate)
        click.echo(f"Estimated cost: ~{cost:.0f} credits (BYOK = free)")
        if not dry_run and not click.confirm("Proceed?"):
            return

    if dry_run:
        print_warning("Dry run — no enrichment will be executed.")
        columns = list(items[0].keys())
        rows = [[row.get(c, "") for c in columns] for row in items[:5]]
        print_table(columns, rows, title="Preview (first 5)")
        return

    client = NrvClient()
    operation = "enrich_person" if "email" in items[0] else "enrich_company"

    try:
        with spinner(f"Enriching {len(items)} records..."):
            result = client.execute_batch(operation, items, strategy=strategy)
    except NrvApiError as exc:
        print_error(f"Batch enrichment failed: {exc.message}")
        sys.exit(1)

    summary = result.get("summary", {})
    click.echo(
        f"\nCompleted: {summary.get('succeeded', '?')} succeeded, "
        f"{summary.get('failed', '?')} failed"
    )
    print_json(result.get("results", result))


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _display_person_result(result: dict[str, Any]) -> None:
    """Display a person enrichment result as a Rich table."""
    data = result.get("result") or result.get("data") or result

    # Check for no-match
    if isinstance(data, dict) and data.get("match_found") is False:
        print_warning("No match found for this person.")
        return

    # If it's a single person dict, show as key-value pairs
    if isinstance(data, dict) and "email" in data:
        _show_person_card(data)
        return

    # If it has a "people" list, show the first one
    if isinstance(data, dict) and "people" in data:
        people = data["people"]
        if not people:
            print_warning("No match found.")
            return
        if len(people) == 1:
            _show_person_card(people[0])
        else:
            _show_person_table(people)
        return

    # Fallback
    _show_kv_table(data, "Enrichment Result")


def _display_company_result(result: dict[str, Any]) -> None:
    """Display a company enrichment result."""
    data = result.get("result") or result.get("data") or result

    if isinstance(data, dict) and data.get("match_found") is False:
        print_warning("No company found for this domain.")
        return

    if isinstance(data, dict) and "companies" in data:
        companies = data["companies"]
        if not companies:
            print_warning("No company found.")
            return
        data = companies[0]

    _show_kv_table(data, "Company Profile")


def _show_person_card(person: dict[str, Any]) -> None:
    """Display a single person as a formatted card."""
    # Priority fields to show first
    priority = [
        "name", "email", "title", "company", "company_domain",
        "phone", "linkedin", "location", "seniority",
    ]

    columns = ["Field", "Value"]
    rows = []

    # Show priority fields first
    for key in priority:
        val = person.get(key)
        if val is not None:
            rows.append([key, str(val)])

    # Then show remaining fields
    skip = set(priority) | {"enrichment_sources", "id", "departments"}
    for key, val in person.items():
        if key not in skip and val is not None:
            rows.append([key, str(val)])

    if rows:
        print_table(columns, rows, title="Person Profile")
    else:
        print_warning("Person found but no data fields returned.")


def _show_person_table(people: list[dict[str, Any]]) -> None:
    """Display multiple people as a table."""
    cols = ["name", "email", "title", "company", "location"]
    columns = [c.replace("_", " ").title() for c in cols]
    rows = [[str(p.get(c, "")) for c in cols] for p in people]
    print_table(columns, rows, title=f"People ({len(people)} results)")


def _show_kv_table(data: Any, title: str) -> None:
    """Display a dict as key-value pairs."""
    if isinstance(data, dict):
        skip = {"enrichment_sources"}
        rows = [
            [k, str(v)[:120]]
            for k, v in data.items()
            if k not in skip and v is not None
        ]
        if rows:
            print_table(["Field", "Value"], rows, title=title)
            return
    print_json(data)
