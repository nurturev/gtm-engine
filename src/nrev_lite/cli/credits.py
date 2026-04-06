"""Credit management commands: balance, history, topup."""

from __future__ import annotations

import sys
import webbrowser

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.constants import PLATFORM_PAYMENTS_URL, PLATFORM_USAGE_URL
from nrev_lite.utils.display import (
    print_credits,
    print_error,
    print_success,
    spinner,
)


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated

    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


@click.group("credits")
def credits() -> None:
    """Manage credits and billing."""


@credits.command()
def balance() -> None:
    """Show current credit balance."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner("Fetching balance..."):
            result = client.get_credits()
    except NrvApiError as exc:
        print_error(f"Failed to fetch balance: {exc.message}")
        sys.exit(1)

    print_credits(
        balance=result.get("balance", 0),
        used=result.get("spend_this_month"),
    )


@credits.command()
def history() -> None:
    """View credit transaction history on the platform."""
    _require_auth()
    click.echo(f"View your credit history at: {PLATFORM_USAGE_URL}")


@credits.command()
def topup() -> None:
    """Open browser to purchase credits on the platform."""
    _require_auth()
    url = PLATFORM_PAYMENTS_URL
    click.echo(f"Opening: {url}")
    try:
        webbrowser.open(url)
        print_success("Payment page opened in browser.")
    except Exception:
        click.echo(f"Open this URL in your browser: {url}")
