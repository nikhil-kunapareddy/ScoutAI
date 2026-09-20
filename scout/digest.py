"""The daily digest: one agent, one DM, in that agent's own Slack window.

    systemd timer ──▶ python -m scout.digest ──▶ one agent ──▶ one DM
    (scout-digest@<key>)   (AGENT=<key>)                     (that bot's app)

One process per agent, which is the same rule ``scout run`` follows and the
reason each report lands in its own conversation: the Slack token comes from
``.env.<agent>``, so the BigTech app posts the BigTech digest and the Referral
Window posts its own. A single process could only ever hold one bot's token,
which is why the three used to arrive stacked in one window.

Which agents *have* a digest is derived, not listed: any spec with ``in_digest``
is one, so a new job agent gets a morning report by existing. What it takes to
deliver that report is a timer instance — see ``deploy/scout-digest@.service``.

Each agent runs on its own thread (``digest:<key>``), kept apart from the
threads the Slack bot uses. Two things follow from that. The digest never
consumes the chat history's ``MAX_TURNS`` window — you can ask the bot something
right after a digest and it has no idea one happened. And because the thread
persists (given ``CHECKPOINT_DB``), each agent can see what it reported on
previous days and skip repeats, which is the only dedupe available here: agents
return prose, not the structured ``JobPosting`` objects their tools built.
"""

from __future__ import annotations

from datetime import date

from .agents import AGENTS, build_agent
from .core import settings, tracing
from .core.agent import AgentSpec
from .core.logging_config import configure_logging, logger
from .slack.notify import post_dm

log = logger()

#: One thread per agent, never a real Slack user id, so it cannot collide with
#: one. Kept stable across runs so the cached profile and the record of what was
#: already reported both survive.
DIGEST_THREAD = "digest:{key}"

#: What the agent is asked. Deliberately does the ranking inside the agent that
#: did the searching: a separate merge-and-rank pass would be another model call
#: per day for a list the agent could already order itself.
#:
#: Worded for every agent that has a digest, not just the resume-tailored ones.
#: "everything you cover" is every source for a job agent and the whole referral
#: list for the Referral Window, and the line about what could not be checked is
#: what keeps that agent's completeness claim intact in a digest.
#:
#: The layout is pinned here rather than rendered in code because what comes
#: back from an agent is prose — the structured ``JobPosting`` objects its tools
#: built are gone by the time the reply exists, which is the same reason the
#: thread is the only dedupe. Asking for the shape is the cheap half of that
#: trade: a report that reads the same tomorrow, and the same on Ollama as on
#: Claude. What it cannot enforce, ``render_postings`` already did — the fields
#: the model is reformatting were identical across every source to begin with.
DIGEST_REQUEST = (
    "Daily job digest. Search everything you cover and report what is open, "
    "best first — at most {max_roles} roles. Prefer roles posted in the last "
    "few days, and leave out anything you already reported earlier in this "
    "conversation.\n"
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
    """
    log.info("Digest: running %s", spec.key)
    try:
        reply = build_agent(spec).respond(
            DIGEST_THREAD.format(key=spec.key),
            DIGEST_REQUEST.format(max_roles=settings.DIGEST_MAX_ROLES),
        )
    except Exception as e:
        log.exception("Digest: %s failed", spec.key)
        reply = f":warning: failed — `{e.__class__.__name__}: {e}`"

    return f"*{spec.name} — {date.today():%a %d %b %Y}*\n{reply}"


def main() -> None:
    configure_logging()
    settings.require_digest_config()

    spec = AGENTS.get(settings.ACTIVE_AGENT)
    if spec is None or not spec.in_digest:
        have = ", ".join(sorted(s.key for s in digest_agents()))
        raise SystemExit(
            f"Agent {settings.ACTIVE_AGENT!r} has no digest. Digest agents: {have}. "
            "Pick one with `scout digest --agent KEY`, or set in_digest on its spec."
        )

    post_dm(settings.DIGEST_SLACK_USER, run_digest(spec))
    # Traces are exported in batches on a background thread, and this process is
    # about to exit — which is the case Langfuse asks a short-lived application
    # to shut down for, rather than leave to the interpreter. It also puts any
    # complaint about sending them inside this run's own journal window.
    tracing.shutdown()


if __name__ == "__main__":
    main()
