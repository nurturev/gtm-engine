"""Key management commands: add, list, remove."""

from __future__ import annotations

import sys

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import print_error, print_success, print_table, print_warning, spinner


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated

    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


@click.group("keys")
def keys() -> None:
    """Manage provider API keys (BYOK)."""


@keys.command("add")
@click.argument("provider")
def add_key(provider: str) -> None:
    """Add an API key for a provider.

    The key is stored securely on the nrev-lite server and never logged locally.
    """
    _require_auth()

    api_key = click.prompt(f"Enter API key for {provider}", hide_input=True)
    if not api_key.strip():
        print_error("API key cannot be empty.")
        sys.exit(1)

    client = NrvClient()
    try:
        with spinner(f"Saving key for {provider}..."):
            client.add_key(provider, api_key.strip())
    except NrvApiError as exc:
        print_error(f"Failed to add key: {exc.message}")
        sys.exit(1)

    print_success(f"Key for '{provider}' saved.")


@keys.command("list")
def list_keys() -> None:
    """Show configured API keys (hints only, not the full key)."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner("Fetching keys..."):
            result = client.list_keys()
    except NrvApiError as exc:
        print_error(f"Failed to list keys: {exc.message}")
        sys.exit(1)

    key_list = result.get("keys", [])
    if not key_list:
        print_warning("No API keys configured. Run: nrev-lite keys add <provider>")
        return

    columns = ["Provider", "Hint", "Status"]
    rows = [
        [k.get("provider", ""), k.get("key_hint", k.get("hint", "****")), k.get("status", "")]
        for k in key_list
    ]
    print_table(columns, rows, title="API Keys")


@keys.command("remove")
@click.argument("provider")
def remove_key(provider: str) -> None:
    """Remove a stored API key for a provider."""
    _require_auth()
    client = NrvClient()

    try:
        with spinner(f"Removing key for {provider}..."):
            client.remove_key(provider)
    except NrvApiError as exc:
        print_error(f"Failed to remove key: {exc.message}")
        sys.exit(1)

    print_success(f"Key for '{provider}' removed.")
