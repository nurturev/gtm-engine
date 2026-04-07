"""Pre-execution cost confirmation for CLI commands."""

from __future__ import annotations

from typing import Any

import click

from nrev_lite.utils.display import print_warning


def confirm_cost(
    client: Any,
    operation: str,
    params: dict[str, Any],
    skip_confirm: bool = False,
) -> bool:
    """Show estimated cost and prompt the user before execution.

    Returns True if the user confirms (or skip_confirm is True), False to abort.
    If the estimate call fails, silently proceeds (returns True) so as not to
    block execution due to a non-critical failure.
    """
    try:
        estimate = client.estimate_cost(operation, params)
        credits_info = client.get_credits()
    except Exception:
        # Don't block execution if estimate/balance check fails
        return True

    estimated = estimate.get("estimated_credits", 0)
    breakdown = estimate.get("breakdown", "")
    is_free = estimate.get("is_free_with_byok", False)
    balance = credits_info.get("balance", 0)

    # Display cost info
    cost_str = f"Estimated cost: ~{estimated:.0f} credits"
    if is_free:
        cost_str += " (free with BYOK key)"
    cost_str += f" | Balance: {balance:,.0f} credits"
    click.echo(cost_str)

    if breakdown:
        click.echo(f"  {breakdown}")

    if balance < estimated and not is_free:
        print_warning(
            f"Insufficient credits: need ~{estimated:.0f} but have {balance:,.0f}. "
            "Top up or add your own API key (BYOK) to run for free."
        )

    if skip_confirm:
        return True

    return click.confirm("Proceed?", default=True)
