"""Dataset management commands: list, describe, query, export."""

from __future__ import annotations
import csv
import sys
from io import StringIO

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import print_error, print_json, print_success, print_table, spinner


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated
    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


@click.group("datasets")
def datasets() -> None:
    """Manage persistent datasets."""


@datasets.command("list")
def list_datasets() -> None:
    """List all datasets with row counts."""
    _require_auth()
    client = NrvClient()
    try:
        with spinner("Fetching datasets..."):
            result = client.get("/datasets")
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)

    items = result.get("datasets", [])
    if not items:
        click.echo("No datasets found. Create one with nrev_create_dataset in Claude Code.")
        return

    columns = ["Name", "Slug", "Rows", "Dedup Key", "Updated"]
    rows = [
        [
            d.get("name", ""),
            d.get("slug", ""),
            str(d.get("row_count", 0)),
            d.get("dedup_key", "—"),
            str(d.get("updated_at", ""))[:19],
        ]
        for d in items
    ]
    print_table(columns, rows, title="Datasets")


@datasets.command()
@click.argument("slug")
def describe(slug: str) -> None:
    """Show dataset schema and sample rows."""
    _require_auth()
    client = NrvClient()
    try:
        with spinner(f"Loading {slug}..."):
            meta = client.get(f"/datasets/{slug}")
            rows_resp = client.get(f"/datasets/{slug}/rows", params={"limit": 5})
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)

    ds = meta.get("dataset", meta)
    click.echo(f"\n  Dataset: {ds.get('name', slug)}")
    click.echo(f"  Slug:    {ds.get('slug', slug)}")
    click.echo(f"  Rows:    {ds.get('row_count', 0)}")
    click.echo(f"  Dedup:   {ds.get('dedup_key', '—')}")

    cols = ds.get("columns", [])
    if cols:
        click.echo(f"\n  Columns:")
        for c in cols:
            click.echo(f"    - {c.get('name', '?')} ({c.get('type', 'text')})")

    rows = rows_resp.get("rows", [])
    if rows:
        click.echo(f"\n  Sample rows ({len(rows)}):")
        print_json(rows)


@datasets.command()
@click.argument("slug")
@click.option("--limit", default=50, type=int, help="Max rows to return.")
@click.option("--filter", "filters", multiple=True, help="Filter as key=value.")
@click.option("--json-output", is_flag=True, help="Output as JSON.")
def query(slug: str, limit: int, filters: tuple, json_output: bool) -> None:
    """Query rows from a dataset."""
    _require_auth()
    client = NrvClient()

    params: dict = {"limit": limit}
    for f in filters:
        if "=" in f:
            k, v = f.split("=", 1)
            params[f"filter_{k}"] = v

    try:
        with spinner(f"Querying {slug}..."):
            result = client.get(f"/datasets/{slug}/rows", params=params)
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)

    rows = result.get("rows", [])
    if not rows:
        click.echo("No rows found.")
        return

    if json_output:
        print_json(rows)
        return

    # Auto-detect columns from first row
    col_names = list(rows[0].keys()) if rows else []
    table_rows = [[str(r.get(c, "")) for c in col_names] for r in rows]
    print_table(col_names, table_rows, title=f"{slug} ({len(rows)} rows)")


@datasets.command()
@click.argument("slug")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv")
@click.option("--output", "-o", default=None, help="Output file path (default: stdout).")
def export(slug: str, fmt: str, output: str | None) -> None:
    """Export dataset rows to CSV or JSON."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner(f"Exporting {slug}..."):
            result = client.get(f"/datasets/{slug}/rows", params={"limit": 10000})
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)

    rows = result.get("rows", [])
    if not rows:
        click.echo("No rows to export.")
        return

    if fmt == "json":
        import json
        content = json.dumps(rows, indent=2, default=str)
    else:
        buf = StringIO()
        col_names = list(rows[0].keys())
        writer = csv.DictWriter(buf, fieldnames=col_names)
        writer.writeheader()
        writer.writerows(rows)
        content = buf.getvalue()

    if output:
        with open(output, "w") as f:
            f.write(content)
        print_success(f"Exported {len(rows)} rows to {output}")
    else:
        click.echo(content)
