"""The daily digest: every job agent, one message.

    systemd timer ──▶ python -m scout.digest ──▶ agent per source ──▶ one DM

Which agents run is derived, not listed: any spec with ``tailor_with_resume`` is
a job agent, so adding one to ``AGENTS`` puts it in tomorrow's digest without
touching this file.

Each agent runs on its own thread (``digest:<key>``), kept apart from the
threads the Slack bot uses. Two things follow from that. The digest never
consumes the chat history's ``MAX_TURNS`` window — you can ask the bot something
right after a digest and it has no idea one happened. And because the thread
persists (given ``CHECKPOINT_DB``), each agent can see what it reported on
previous days and skip repeats, which is the only dedupe available here: agents
return prose, not the structured ``JobPosting`` objects their tools built.
"""

from __future__ import annotations

import logging
from datetime import date

from .agents import AGENTS, build_agent
from .core import settings
from .core.agent import AgentSpec
from .core.logging_config import configure_logging
from .slack.notify import post_dm

log = logging.getLogger("scout")

#: One thread per agent, never a real Slack user id, so it cannot collide with
#: one. Kept stable across runs so the cached profile and the record of what was
#: already reported both survive.
DIGEST_THREAD = "digest:{key}"

#: What each agent is asked. Deliberately does the ranking inside the agent that
#: did the searching: a separate merge-and-rank pass would be another model call
#: per day for a list the agent could already order itself.
DIGEST_REQUEST = (
    "Daily job digest. Search every job source you have for roles matching my "
    "profile, then reply with only the strongest matches — at most {max_roles}, "
    "best first, each with one short line on why it fits. Prefer roles posted in "
    "the last few days. Leave out anything you already reported earlier in this "
    "conversation. If nothing worth sending came up, say so in one line."
)


def job_agents() -> list[AgentSpec]:
    """The specs the digest runs: every agent that searches jobs.

    ``tailor_with_resume`` is the marker — it is what makes an agent one that
    matches roles against the candidate, which is what a digest is for. The
    Resume Parser is not one, and is skipped.
    """
    return [spec for spec in AGENTS.values() if spec.tailor_with_resume]


def run_digest() -> str:
    """Run every job agent and return the merged report.

    One agent failing does not lose the others: its section says so and the rest
    are still delivered.
    """
    sections = [f"*Job digest — {date.today():%a %d %b %Y}*"]

    for spec in job_agents():
        log.info("Digest: running %s", spec.key)
        try:
            reply = build_agent(spec).respond(
                DIGEST_THREAD.format(key=spec.key),
                DIGEST_REQUEST.format(max_roles=settings.DIGEST_MAX_ROLES),
            )
        except Exception as e:
            log.exception("Digest: %s failed", spec.key)
            reply = f":warning: failed — `{e.__class__.__name__}: {e}`"
        sections.append(f"*{spec.name}*\n{reply}")

    return "\n\n".join(sections)


def main() -> None:
    configure_logging()
    settings.require_digest_config()
    post_dm(settings.DIGEST_SLACK_USER, run_digest())


if __name__ == "__main__":
    main()
