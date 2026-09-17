"""The command line: one entry point for every way Scout is run.

    scout run [--agent KEY]    start the Slack bot for one agent
    scout digest               run every job agent and DM one merged report
    scout stats [--days N]     read the turn metrics back
    scout agents               list the registered agents and their tools
    scout doctor               check the configuration without connecting

``--agent`` becomes the ``AGENT`` environment variable *before*
``scout.core.settings`` is imported, because that import is what layers
``.env.<agent>`` over ``.env`` — a choice which cannot be revisited once the
module is in memory. That is the one reason the commands below import settings
inside the function rather than at module scope, and why nothing at module
scope here may import it either.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

#: Environment variable the platform reads to pick an agent. See settings.
AGENT_ENV = "AGENT"


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the chosen command, returning an exit status."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    select_agent(getattr(args, "agent", None))
    status: int = args.run(args)
    return status


def build_parser() -> argparse.ArgumentParser:
    """The argument parser, one subcommand per entry point."""
    parser = argparse.ArgumentParser(
        prog="scout",
        description="A multi-agent job-search assistant that lives in Slack DMs.",
    )
    # No subcommand runs the bot, so `scout` alone behaves like `scout run`.
    parser.set_defaults(run=_run)
    subcommands = parser.add_subparsers(title="commands", metavar="<command>")

    run = subcommands.add_parser("run", help="start the Slack bot for one agent")
    run.add_argument(
        "--agent",
        metavar="KEY",
        help="which agent to run (default: $AGENT, else bigtech)",
    )
    run.set_defaults(run=_run)

    digest = subcommands.add_parser(
        "digest", help="run every job agent and DM one merged report"
    )
    digest.set_defaults(run=_digest)

    stats = subcommands.add_parser("stats", help="read the turn metrics back")
    stats.add_argument("--days", type=int, default=7, help="how far back to look")
    stats.add_argument(
        "source", nargs="?", help="'-' to read log lines from stdin instead"
    )
    stats.set_defaults(run=_stats)

    agents = subcommands.add_parser(
        "agents", help="list the registered agents and their tools"
    )
    agents.set_defaults(run=_agents)

    doctor = subcommands.add_parser(
        "doctor", help="check the configuration without connecting"
    )
    doctor.set_defaults(run=_doctor)

    return parser


def select_agent(key: str | None) -> None:
    """Pin the active agent for this process, if one was asked for.

    Setting the environment variable rather than passing the key on is what
    keeps ``.env.<agent>`` in play: ``settings`` reads ``AGENT`` on import to
    decide which per-agent file layers over ``.env``, so the choice has to be
    made before anything imports it.
    """
    if key:
        os.environ[AGENT_ENV] = key


def _run(_args: argparse.Namespace) -> int:
    """Start the Slack bot: the long-running command."""
    from .agents import build_agent, get_spec
    from .core import settings
    from .core.logging_config import configure_logging
    from .slack import SlackBot

    log = configure_logging()
    # Check config before building anything: constructing the Slack app verifies
    # the bot token against Slack, so a missing one should be reported here.
    settings.require_slack_credentials()

    spec = get_spec(settings.ACTIVE_AGENT)
    agent = build_agent(spec)
    log.info("Agent %r ready (default backend=%s)", spec.key, spec.default_backend)
    SlackBot(agent).start()
    return 0


def _digest(_args: argparse.Namespace) -> int:
    """Run the daily digest once, now."""
    from .digest import main as run_digest

    run_digest()
    return 0


def _stats(args: argparse.Namespace) -> int:
    """Print the turn-metrics table."""
    from .stats import report

    print(report(args.days, args.source))
    return 0


def _agents(_args: argparse.Namespace) -> int:
    """List what is registered, so ``AGENT=`` values are discoverable."""
    from .agents import AGENTS
    from .core.agent import agent_tools

    for key, spec in sorted(AGENTS.items()):
        marks = " (resume-tailored)" if spec.tailor_with_resume else ""
        print(f"{key:<10} {spec.name}{marks}")
        print(f"{'':<10} backend: {spec.default_backend}")
        names = [tool.name for tool in agent_tools(spec)]
        print(f"{'':<10} tools:   {', '.join(names) or 'none'}")
    return 0


def _doctor(_args: argparse.Namespace) -> int:
    """Report what is configured and what is missing, touching no network.

    The first thing to run in a fresh checkout, and the first thing to run when
    a deployed bot is quiet: every check here is a local read, so it answers
    "is this configured?" without asking "is Slack up?".
    """
    from . import checks

    report = checks.run()
    print(report.render())
    return 0 if report.ok else 1
