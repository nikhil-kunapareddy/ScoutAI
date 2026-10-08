"""The daily digest: one agent, one DM, in that agent's own Slack window.

    systemd timer ──▶ python -m scout.digest ──▶ one agent ──▶ one DM
    (scout-digest@<key>)   (AGENT=<key>)                     (that bot's app)

One process per agent, the same rule ``scout run`` follows: the Slack token
comes from ``.env.<agent>``, so the BigTech app posts the BigTech digest and the
Referral Window posts its own. A process can only hold one bot's token, which is
why reports are never merged into one message.

Which agents *have* a digest is derived, not listed: any spec with ``in_digest``
is one, so a new job agent gets a morning report by existing. What it takes to
deliver that report is a timer instance — see ``deploy/scout-digest@.service``.

Each agent runs on its own thread (``digest:<key>``), kept apart from the
threads the Slack bot uses, so the digest never consumes the chat history's
``MAX_TURNS`` window — you can ask the bot something right after a digest and it
has no idea one happened.

What a digest already sent is not in that thread; it is in ``shared_jobs``. Every
job tool hides what this agent sent within ``DIGEST_DEDUPE_DAYS`` before the
model sees it (``tools/jobs/unsent.py``), and once the DM is posted, the postings
whose links it carries are recorded. So the thread holds nothing tomorrow needs,
and it is reset at the start of every run: no history to outgrow the window, and
a changed résumé is parsed again the next morning rather than never.
"""

from __future__ import annotations

from datetime import date

from .agents import AGENTS, build_agent
from .core import settings, shared_jobs, tracing
from .core.agent import AgentSpec
from .core.logging_config import configure_logging, logger
from .core.referrals import DIGEST_THREAD_PREFIX
from .slack.notify import post_dm
from .tools.jobs import unsent

log = logger()

#: The date in each report's header, e.g. ``Tue 06 Oct 2026``.
_HEADER_DATE_FORMAT = "%a %d %b %Y"

#: What the agent is asked. The ranking happens inside the agent that did the
#: searching: a separate merge-and-rank pass would be another model call per day
#: for a list the agent can already order itself.
#:
#: Worded for every agent with a digest. "Everything you cover" is every source
#: for a job agent and the whole referral list for the Referral Window, and the
#: closing line about what could not be checked keeps that agent's completeness
#: claim intact.
#:
#: The layout is pinned here rather than rendered in code because the reply is
#: prose — the ``JobPosting`` objects are gone by the time it exists. The fields
#: the model reformats were already identical across sources (``render_postings``),
#: so asking for the shape is enough to make every day's report read the same.
DIGEST_REQUEST = (
    "Daily job digest. Search everything you cover and report what is open, "
    "best first — at most {max_roles} roles. Prefer roles posted in the last "
    "few days. Roles an earlier digest sent are already left out of what your "
    "tools return.\n"
    "\n"
    "Format every role exactly like this, and put nothing else between them:\n"
    "\n"
    "1. *<role title>* — <company>\n"
    "    <one line on why it is worth a look>\n"
    "    <the posted date, exactly as the tool gave it> · <link>\n"
    "\n"
    "Repeat the tool's date word for word; where a source gives none, write "
    "'no date given' rather than guessing one. Plain Slack formatting only — "
    "no markdown links, no headings, no tables. Do not add a title or the "
    "date: the message already carries both.\n"
    "\n"
    "End with one line naming anything you could not check — a board that was "
    "down, a company with no board — or saying the sweep was complete. If "
    "nothing new came up, skip the list and say so in one line."
)


def digest_agents() -> list[AgentSpec]:
    """The specs that have a digest of their own.

    ``in_digest`` is the marker, and it is a question of its own rather than a
    read of ``tailor_with_resume``: the Referral Window searches a scope instead
    of a resume and still has something to report every morning.
    """
    return [spec for spec in AGENTS.values() if spec.in_digest]


def run_digest(spec: AgentSpec) -> str:
    """Run one agent's digest and return the message to send.

    A failure is reported rather than raised, so the morning DM still arrives
    and says what went wrong. ``OnFailure=`` on the unit stays the backstop for
    anything that breaks before this point, such as missing configuration.

    Args:
        spec: The agent whose digest to run.
    """
    return f"{_header(spec)}\n{_request_report(spec)}"


def main() -> None:
    """Run the active agent's digest and DM it to ``DIGEST_SLACK_USER``."""
    configure_logging()
    settings.require_digest_config()
    spec = _active_digest_spec()

    try:
        message = run_digest(spec)
        post_dm(settings.DIGEST_SLACK_USER, message)
        _remember_sent(spec, message)
    finally:
        # Export is batched on a background thread and this process is about to
        # exit, which is when Langfuse asks a short-lived app to shut down. Doing
        # it here also keeps any export complaint inside this run's journal.
        tracing.shutdown()


def _active_digest_spec() -> AgentSpec:
    """The spec this process runs, or exit naming the agents that have a digest."""
    spec = AGENTS.get(settings.ACTIVE_AGENT)
    if spec is not None and spec.in_digest:
        return spec

    available = ", ".join(sorted(candidate.key for candidate in digest_agents()))
    raise SystemExit(
        f"Agent {settings.ACTIVE_AGENT!r} has no digest. Digest agents: {available}. "
        "Pick one with `scout digest --agent KEY`, or set in_digest on its spec."
    )


def _request_report(spec: AgentSpec) -> str:
    """The agent's report, or a one-line failure note in its place."""
    log.info("Digest: running %s", spec.key)
    request = DIGEST_REQUEST.format(max_roles=settings.DIGEST_MAX_ROLES)
    thread = _thread_id(spec)
    try:
        agent = build_agent(spec)
        # Yesterday's turn holds nothing today needs: what was sent is in
        # shared_jobs. Starting clean also re-reads the résumé.
        agent.reset(thread)
        return agent.respond(thread, request)
    except Exception as exc:
        log.exception("Digest: %s failed", spec.key)
        return f":warning: failed — `{type(exc).__name__}: {exc}`"


def _thread_id(spec: AgentSpec) -> str:
    """The checkpointer thread a digest runs on, e.g. ``digest:bigtech``.

    Never a Slack user id, so it cannot collide with a conversation, and stable
    across runs, so each morning's reset clears yesterday's thread rather than
    leaving one behind per day. The prefix is the one ``referrals.owner_for`` and
    ``unsent.render_unsent`` match to recognise a digest turn.
    """
    return f"{DIGEST_THREAD_PREFIX}{spec.key}"


def _remember_sent(spec: AgentSpec, message: str) -> None:
    """Record the postings the DM carried, so the next digest skips them.

    Called after the post, never before: a DM that failed to send sent nothing.
    A store that can't be written costs tomorrow a repeat, not today's digest.
    """
    sent = unsent.take_sent(_thread_id(spec), message)
    try:
        kept = shared_jobs.record(spec.key, sent)
    except shared_jobs.SharedJobsError:
        log.exception("Digest: couldn't record what %s sent", spec.key)
        return
    log.info("Digest: recorded %d posting(s) sent by %s", kept, spec.key)


def _header(spec: AgentSpec) -> str:
    """The report's first line: the agent's name and today's date, in bold."""
    return f"*{spec.name} — {date.today().strftime(_HEADER_DATE_FORMAT)}*"


if __name__ == "__main__":
    main()
