"""Script management commands: list, show, delete."""

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


@click.group("scripts")
def scripts() -> None:
    """Manage saved workflow scripts."""


@scripts.command("list")
def list_scripts() -> None:
    """List all saved scripts."""
    _require_auth()
    client = NrvClient()
    try:
        with spinner("Fetching scripts..."):
            result = client.get("/scripts")
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)

    items = result.get("scripts", [])
    if not items:
        click.echo("No saved scripts. Use Claude Code to save a workflow as a script.")
        return

    columns = ["Name", "Description", "Params", "Steps", "Runs", "Last Run"]
    rows = [
        [
            s.get("name", ""),
            (s.get("description") or "—")[:50],
            str(s.get("parameter_count", 0)),
            str(s.get("step_count", 0)),
            str(s.get("run_count", 0)),
            str(s.get("last_run_at", "—"))[:19] if s.get("last_run_at") else "—",
        ]
        for s in items
    ]
    print_table(columns, rows, title="Saved Scripts")


@scripts.command("show")
@click.argument("name")
def show_script(name: str) -> None:
    """Show full script definition (steps, parameters)."""
    _require_auth()
    client = NrvClient()
    try:
        with spinner(f"Loading script '{name}'..."):
            result = client.get(f"/scripts/{name}")
    except NrvApiError as exc:
        if exc.status_code == 404:
            print_error(f"Script '{name}' not found.")
        else:
            print_error(f"Failed: {exc.message}")
        sys.exit(1)

    click.echo()
    click.secho(f"  {result.get('name', name)}", bold=True)
    if result.get("description"):
        click.echo(f"  {result['description']}")
    click.echo()

    # Parameters
    params = result.get("parameters", [])
    if params:
        click.secho("  Parameters:", underline=True)
        for p in params:
            default = f" (default: {p['default']})" if "default" in p else ""
            click.echo(f"    {{{{ {p['name']} }}}}  [{p.get('type', 'string')}] — {p.get('description', '')}{default}")
        click.echo()

    # Steps
    steps = result.get("steps", [])
    if steps:
        click.secho("  Steps:", underline=True)
        for s in steps:
            order = s.get("order", "?")
            tool = s.get("tool_name", "?")
            desc = s.get("description", "")
            for_each = s.get("for_each")
            prefix = f"    {order}. {tool}"
            if for_each:
                prefix += f"  (for each in {for_each})"
            click.echo(prefix)
            if desc:
                click.echo(f"       {desc}")
        click.echo()

    # Meta
    click.echo(f"  Slug: {result.get('slug', '—')}")
    click.echo(f"  Runs: {result.get('run_count', 0)}")
    if result.get("source_workflow_id"):
        click.echo(f"  Source workflow: {result['source_workflow_id']}")
    click.echo()


@scripts.command("delete")
@click.argument("name")
@click.confirmation_option(prompt="Are you sure you want to delete this script?")
def delete_script(name: str) -> None:
    """Delete a saved script."""
    _require_auth()
    client = NrvClient()
    try:
        with spinner(f"Deleting script '{name}'..."):
            client.delete(f"/scripts/{name}")
    except NrvApiError as exc:
        if exc.status_code == 404:
            print_error(f"Script '{name}' not found.")
        else:
            print_error(f"Failed: {exc.message}")
        sys.exit(1)
    print_success(f"Script '{name}' deleted.")
