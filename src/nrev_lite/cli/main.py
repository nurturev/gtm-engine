"""Main Click CLI group — registers all subcommands."""

from __future__ import annotations

import click

from nrev_lite import __version__


@click.group()
@click.version_option(version=__version__, prog_name="nrev-lite")
def cli() -> None:
    """nrev-lite — agent-native GTM execution platform."""


# ---- Register subcommands ------------------------------------------------

from nrev_lite.cli.init import init  # noqa: E402  — one-command setup
from nrev_lite.cli.auth import auth  # noqa: E402
from nrev_lite.cli.enrich import enrich  # noqa: E402
from nrev_lite.cli.search import search  # noqa: E402
from nrev_lite.cli.query import query  # noqa: E402
from nrev_lite.cli.tables import table  # noqa: E402
from nrev_lite.cli.keys import keys  # noqa: E402
from nrev_lite.cli.credits import credits  # noqa: E402
from nrev_lite.cli.config_cmd import config  # noqa: E402
from nrev_lite.cli.dashboard import dashboard  # noqa: E402
from nrev_lite.cli.setup import setup_claude  # noqa: E402
from nrev_lite.cli.status import status  # noqa: E402
from nrev_lite.cli.web import web  # noqa: E402
from nrev_lite.mcp.run import mcp  # noqa: E402
from nrev_lite.cli.datasets import datasets  # noqa: E402
from nrev_lite.cli.schedules import schedules  # noqa: E402
from nrev_lite.cli.scripts import scripts  # noqa: E402
from nrev_lite.cli.feedback import feedback  # noqa: E402

cli.add_command(init)  # nrev-lite init — primary onboarding entry point
cli.add_command(auth)
cli.add_command(status)
cli.add_command(enrich)
cli.add_command(search)
cli.add_command(query)
cli.add_command(table)
cli.add_command(keys)
cli.add_command(credits)
cli.add_command(config)
cli.add_command(dashboard)
cli.add_command(setup_claude)
cli.add_command(web)
cli.add_command(mcp)
cli.add_command(datasets)
cli.add_command(schedules)
cli.add_command(scripts)
cli.add_command(feedback)
