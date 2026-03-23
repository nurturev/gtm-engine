"""Schedule management commands: list, enable, disable."""

from __future__ import annotations
import sys

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import print_error, print_success, print_table, spinner


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated
    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


@click.group("schedules")
def schedules() -> None:
    """Manage scheduled workflows."""


@schedules.command("list")
def list_schedules() -> None:
    """List all scheduled workflows."""
    _require_auth()
    client = NrvClient()
    try:
        with spinner("Fetching schedules..."):
            result = client.get("/schedules")
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)

    items = result.get("schedules", [])
    if not items:
        click.echo("No scheduled workflows found.")
        return

    columns = ["Name", "Schedule", "Enabled", "Next Run", "Last Run", "Runs"]
    rows = [
        [
            s.get("name", ""),
            s.get("schedule", s.get("cron_expression", "—")),
            "Yes" if s.get("enabled") else "No",
            str(s.get("next_run_at", "—"))[:19] if s.get("next_run_at") else "—",
            str(s.get("last_run_at", "—"))[:19] if s.get("last_run_at") else "—",
            str(s.get("run_count", 0)),
        ]
        for s in items
    ]
    print_table(columns, rows, title="Scheduled Workflows")


@schedules.command()
@click.argument("schedule_id")
def enable(schedule_id: str) -> None:
    """Enable a scheduled workflow."""
    _require_auth()
    client = NrvClient()
    try:
        with spinner("Enabling schedule..."):
            client.patch(f"/schedules/{schedule_id}", json={"enabled": True})
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)
    print_success(f"Schedule {schedule_id} enabled.")


@schedules.command()
@click.argument("schedule_id")
def disable(schedule_id: str) -> None:
    """Disable a scheduled workflow."""
    _require_auth()
    client = NrvClient()
    try:
        with spinner("Disabling schedule..."):
            client.patch(f"/schedules/{schedule_id}", json={"enabled": False})
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)
    print_success(f"Schedule {schedule_id} disabled.")
