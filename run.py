"""Entry point: runs the agent named by ``AGENT`` (default ``bigtech``).

Job-search agents set ``tailor_with_resume`` in their spec, so they run behind
the Resume Parser hand-off — the profile is distilled once per user and prepended
to every job-search turn. Agents without the flag run standalone (e.g.
``AGENT=resume python run.py`` to exercise the parser).

    python run.py
"""

from __future__ import annotations

from scout.agents import build_agent, get_spec
from scout.core import settings
from scout.core.logging_config import configure_logging
from scout.slack import SlackBot


def main() -> None:
    log = configure_logging()
    # Check config before building anything: constructing the Slack app verifies
    # the bot token against Slack, so a missing one should be reported here.
    settings.require_slack_credentials()

    spec = get_spec(settings.ACTIVE_AGENT)
    agent = build_agent(spec)
    log.info("Agent %r ready (default backend=%s)", spec.key, spec.default_backend)
    SlackBot(agent).start()


if __name__ == "__main__":
    main()
