"""Credit management commands: balance, history, topup."""

from __future__ import annotations

import sys
import webbrowser

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import (
    print_credits,
    print_error,
    print_success,
    print_table,
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
@click.option("--limit", default=20, type=int, help="Number of transactions to show.")
def history(limit: int) -> None:
    """Show recent credit transactions."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner("Fetching history..."):
            result = client.get_credit_history(limit=limit)
    except NrvApiError as exc:
        print_error(f"Failed to fetch history: {exc.message}")
        sys.exit(1)

    entries = result.get("entries", [])
    if not entries:
        click.echo("No transactions found.")
        return

    columns = ["Date", "Type", "Amount", "Balance After", "Description"]
    rows = [
        [
            e.get("created_at", "")[:19],
            e.get("entry_type", ""),
            f"{e.get('amount', 0):,.2f}",
            f"{e.get('balance_after', 0):,.2f}",
            e.get("description", ""),
        ]
        for e in entries
    ]
    print_table(columns, rows, title="Credit History")


@credits.command()
def topup() -> None:
    """Open browser to purchase credits via Stripe."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner("Creating checkout session..."):
            result = client.get_topup_url()
    except NrvApiError as exc:
        print_error(f"Failed to create checkout: {exc.message}")
        sys.exit(1)

    url = result.get("url") or result.get("checkout_url")
    if not url:
        print_error("No checkout URL received from server.")
        sys.exit(1)

    click.echo(f"Opening checkout: {url}")
    webbrowser.open(url)
    print_success("Checkout page opened in browser.")
