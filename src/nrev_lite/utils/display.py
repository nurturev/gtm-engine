"""Rich display helpers for nrev-lite CLI output."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Generator, Sequence

from rich.console import Console
from rich.json import JSON
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()
error_console = Console(stderr=True)

MAX_CELL_WIDTH = 60


def _truncate(value: Any, max_width: int = MAX_CELL_WIDTH) -> str:
    """Truncate a string representation if it exceeds max_width."""
    s = str(value) if value is not None else ""
    if len(s) > max_width:
        return s[: max_width - 3] + "..."
    return s


def print_table(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    title: str | None = None,
) -> None:
    """Pretty-print a table using Rich."""
    table = Table(title=title, box=box.ROUNDED, show_lines=False)
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*[_truncate(cell) for cell in row])
    console.print(table)


def print_json(data: Any) -> None:
    """Pretty-print JSON data."""
    raw = json.dumps(data, indent=2, default=str)
    console.print(JSON(raw))


def print_success(message: str) -> None:
    """Print a success message with a green checkmark."""
    console.print(Text(f"[green]\u2714[/green] {message}", style="bold"))


def print_error(message: str) -> None:
    """Print an error message with a red X."""
    error_console.print(Text(f"[red]\u2718[/red] {message}", style="bold red"))


def print_warning(message: str) -> None:
    """Print a warning message in yellow."""
    console.print(Text(f"[yellow]\u26a0[/yellow] {message}", style="yellow"))


def print_credits(balance: float, used: float | None = None) -> None:
    """Display credit balance with color coding."""
    if balance > 100:
        style = "green"
    elif balance > 20:
        style = "yellow"
    else:
        style = "red"
    console.print(f"Credit balance: [{style}]{balance:,.2f}[/{style}]")
    if used is not None:
        console.print(f"Credits used:   [dim]{used:,.2f}[/dim]")


@contextmanager
def spinner(message: str = "Working...") -> Generator[None, None, None]:
    """Context manager that shows a loading spinner."""
    with console.status(message, spinner="dots"):
        yield
