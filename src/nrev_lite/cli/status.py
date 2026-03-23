"""Status command: one-glance view of auth, keys, credits, and providers."""

from __future__ import annotations

import sys
import time

import click

from nrev_lite.client.auth import is_authenticated, load_credentials
from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.config import get_api_base_url
from nrev_lite.utils.display import console, print_error, print_warning, spinner

from rich.panel import Panel
from rich.table import Table
from rich import box


@click.command("status")
def status() -> None:
    """Show a full overview: auth, providers, credits, and server health."""
    base_url = get_api_base_url()

    # ---- Auth ----
    creds = load_credentials()
    if creds is None:
        console.print(
            Panel(
                "[red bold]Not logged in[/red bold]\n\n"
                "Run [cyan]nrev-lite auth login[/cyan] to get started.\n"
                f"Server: {base_url}",
                title="nrev-lite status",
                border_style="red",
            )
        )
        return

    user_info = creds.get("user_info", {})
    email = user_info.get("email", "unknown")
    tenant = user_info.get("tenant", "unknown")
    expires_at = creds.get("expires_at", 0)
    token_status = "valid" if time.time() < expires_at else "expired (will auto-refresh)"

    # ---- Server health ----
    server_ok = False
    try:
        import httpx

        r = httpx.get(f"{base_url}/health", timeout=5)
        server_ok = r.status_code == 200
        server_version = r.json().get("version", "?") if server_ok else "?"
    except Exception:
        server_version = "unreachable"

    # ---- Build auth section ----
    lines = [
        f"[bold]Email:[/bold]   {email}",
        f"[bold]Tenant:[/bold]  {tenant}",
        f"[bold]Token:[/bold]   {token_status}",
        f"[bold]Server:[/bold]  {base_url} ({'[green]online[/green] v' + server_version if server_ok else '[red]offline[/red]'})",
    ]

    if not server_ok:
        console.print(
            Panel(
                "\n".join(lines) + "\n\n[red]Server is not reachable.[/red]",
                title="nrev-lite status",
                border_style="yellow",
            )
        )
        return

    if not is_authenticated():
        console.print(Panel("\n".join(lines), title="nrev-lite status", border_style="yellow"))
        return

    # ---- Fetch keys + credits from server ----
    client = NrvClient(base_url=base_url)
    keys_data: list[dict] = []
    balance = 0.0
    spend = 0.0

    try:
        with spinner("Fetching account details..."):
            try:
                keys_result = client.list_keys()
                keys_data = keys_result.get("keys", [])
            except NrvApiError:
                pass

            try:
                credits_result = client.get_credits()
                balance = credits_result.get("balance", 0)
                spend = credits_result.get("spend_this_month", 0)
            except NrvApiError:
                pass
    except Exception:
        pass

    # ---- Credits line ----
    if balance > 100:
        bal_style = "green"
    elif balance > 20:
        bal_style = "yellow"
    else:
        bal_style = "red"
    lines.append(f"[bold]Credits:[/bold] [{bal_style}]{balance:,.0f}[/{bal_style}] (spent this month: {spend:,.2f})")

    # ---- Keys table ----
    if keys_data:
        lines.append("")
        lines.append("[bold]API Keys (BYOK):[/bold]")
        for k in keys_data:
            provider = k.get("provider", "?")
            hint = k.get("key_hint", k.get("hint", "****"))
            lines.append(f"  [cyan]{provider:15}[/cyan] ...{hint}")
    else:
        lines.append("")
        lines.append("[dim]No BYOK keys configured. Run: nrev-lite keys add <provider>[/dim]")

    # ---- Available providers (from vendor catalog) ----
    lines.append("")
    providers_by_category = {
        "Enrichment": [
            ("apollo", "Enrich + Search people/companies"),
            ("rocketreach", "Enrich + Search with alumni filters"),
            ("bettercontact", "Waterfall enrichment (email + phone)"),
            ("hunter", "Email finding + verification"),
            ("clearbit", "Company enrichment + reveal"),
            ("lusha", "B2B contact enrichment"),
        ],
        "Verification": [
            ("zerobounce", "Email verification + catch-all detection"),
        ],
        "Search & Scraping": [
            ("rapidapi", "Google SERP search"),
            ("parallel", "Web scraping (anti-bot capable)"),
        ],
        "Signals": [
            ("predictleads", "Company jobs, tech, funding signals"),
        ],
        "Outreach": [
            ("instantly", "Email campaigns + warmup"),
            ("lemlist", "Multi-channel outreach"),
        ],
        "LLM / AI Research": [
            ("openai", "GPT models for research & analysis"),
            ("anthropic", "Claude models for research & analysis"),
            ("perplexity", "AI-powered web research"),
        ],
    }
    # Which providers have actual backend integration
    integrated = {"apollo", "rocketreach", "predictleads", "parallel", "rapidapi"}

    lines.append("[bold]Available Providers:[/bold]")
    for category, provs in providers_by_category.items():
        lines.append(f"  [bold dim]{category}[/bold dim]")
        for prov, desc in provs:
            has_key = any(k.get("provider") == prov for k in keys_data)
            if has_key:
                badge = "[green]BYOK[/green]"
            elif prov in integrated:
                badge = "[dim]platform[/dim]"
            else:
                badge = "[yellow]BYOK only[/yellow]"
            lines.append(f"    [cyan]{prov:16}[/cyan] {badge}  {desc}")

    # ---- Quick start hints ----
    lines.append("")
    lines.append("[bold]Quick Start:[/bold]")
    lines.append("  [cyan]nrev-lite search people --title 'VP Sales' --domain stripe.com[/cyan]")
    lines.append("  [cyan]nrev-lite enrich person --email john@acme.com[/cyan]")
    lines.append("  [cyan]nrev-lite enrich company --domain stripe.com[/cyan]")
    lines.append("  [cyan]nrev-lite keys add apollo[/cyan]  (use your own key for free calls)")

    console.print(Panel("\n".join(lines), title="[bold]nrev-lite status[/bold]", border_style="green"))
