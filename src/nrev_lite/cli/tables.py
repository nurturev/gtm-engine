"""Table management commands: list, describe, add-column."""

from __future__ import annotations

import sys

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import print_error, print_json, print_success, print_table, spinner


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated

    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


@click.group("table")
def table() -> None:
    """Manage tables."""


@table.command("list")
def list_tables() -> None:
    """List all tables with row counts."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner("Fetching tables..."):
            result = client.list_tables()
    except NrvApiError as exc:
        print_error(f"Failed to list tables: {exc.message}")
        sys.exit(1)

    tables = result.get("tables", [])
    if not tables:
        click.echo("No tables found.")
        return

    columns = ["Name", "Rows", "Columns"]
    rows = [
        [t.get("name", ""), t.get("row_count", "?"), t.get("column_count", "?")]
        for t in tables
    ]
    print_table(columns, rows, title="Tables")


@table.command("describe")
@click.argument("name")
def describe_table(name: str) -> None:
    """Show the schema of a table."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner(f"Describing {name}..."):
            result = client.get_table(name)
    except NrvApiError as exc:
        print_error(f"Failed to describe table: {exc.message}")
        sys.exit(1)

    schema = result.get("schema") or result.get("columns", [])
    if isinstance(schema, list):
        columns = ["Column", "Type", "Nullable", "Default"]
        rows = [
            [
                col.get("name", ""),
                col.get("type", ""),
                "yes" if col.get("nullable", True) else "no",
                col.get("default", ""),
            ]
            for col in schema
        ]
        print_table(columns, rows, title=f"Table: {name}")
    else:
        print_json(result)


@table.command("add-column")
@click.argument("table_name", metavar="TABLE")
@click.argument("column")
@click.argument("col_type", metavar="TYPE")
@click.option("--default", "default_val", default=None, help="Default value for new column.")
def add_column(table_name: str, column: str, col_type: str, default_val: str | None) -> None:
    """Add a column to a table."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner(f"Adding column {column}..."):
            client.add_column(table_name, column, col_type, default=default_val)
    except NrvApiError as exc:
        print_error(f"Failed to add column: {exc.message}")
        sys.exit(1)

    print_success(f"Column '{column}' ({col_type}) added to '{table_name}'.")
