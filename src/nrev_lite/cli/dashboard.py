"""Dashboard commands: deploy, list, remove."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import print_error, print_success, print_table, spinner


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated

    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


@click.group("dashboard")
def dashboard() -> None:
    """Manage deployed dashboards."""


@dashboard.command("deploy")
@click.argument("path", type=click.Path(exists=True))
@click.option("--name", default=None, help="Dashboard name (defaults to directory name).")
def deploy(path: str, name: str | None) -> None:
    """Deploy a local dashboard to nrev-lite cloud.

    PATH is the directory or bundle file to deploy.
    """
    _require_auth()

    p = Path(path)
    dashboard_name = name or p.stem

    if p.is_dir():
        # TODO: bundle the directory into a zip/tar
        print_error("Directory deploy not yet supported. Please provide a bundle file.")
        sys.exit(1)

    client = NrvClient()
    try:
        with spinner(f"Deploying '{dashboard_name}'..."):
            result = client.deploy_dashboard(dashboard_name, str(p))
    except NrvApiError as exc:
        print_error(f"Deploy failed: {exc.message}")
        sys.exit(1)

    url = result.get("url", "")
    print_success(f"Dashboard '{dashboard_name}' deployed.")
    if url:
        click.echo(f"URL: {url}")


@dashboard.command("list")
def list_dashboards() -> None:
    """List deployed dashboards."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner("Fetching dashboards..."):
            result = client.list_dashboards()
    except NrvApiError as exc:
        print_error(f"Failed to list dashboards: {exc.message}")
        sys.exit(1)

    dashboards = result.get("dashboards", [])
    if not dashboards:
        click.echo("No dashboards deployed.")
        return

    columns = ["Name", "URL", "Updated"]
    rows = [
        [d.get("name", ""), d.get("url", ""), d.get("updated_at", "")]
        for d in dashboards
    ]
    print_table(columns, rows, title="Dashboards")


@dashboard.command("remove")
@click.argument("name")
def remove(name: str) -> None:
    """Remove a deployed dashboard."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner(f"Removing '{name}'..."):
            client.remove_dashboard(name)
    except NrvApiError as exc:
        print_error(f"Failed to remove dashboard: {exc.message}")
        sys.exit(1)

    print_success(f"Dashboard '{name}' removed.")
