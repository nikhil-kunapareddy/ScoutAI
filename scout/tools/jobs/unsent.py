"""What a digest already sent you, kept out of what it searches.

Every job tool renders through ``render_unsent``. Outside a digest it is
``render_postings`` and nothing more, so a chat reply is exactly what it was. In
a digest turn — a thread named ``digest:<agent>`` — it drops every posting that
agent sent within ``DIGEST_DEDUPE_DAYS`` before the model sees the list, which
makes a repeat impossible rather than merely discouraged, and notes the rest as
offered.

After the DM is posted, ``take_sent`` matches the links in it against what was
offered. That is how the digest learns what it actually sent: the model picks
from what it was shown, and only its picks are recorded, so a role it ranked out
today is still new tomorrow. A link the model mangled fails soft — that one role
may come back.
"""

from __future__ import annotations

import re
import threading

from langchain_core.runnables import RunnableConfig

from ...core import settings, shared_jobs
from ...core.logging_config import logger
from ...core.referrals import DIGEST_THREAD_PREFIX
from .posting import JobPosting, render_postings

log = logger()

#: Postings shown to the model in a digest turn, by thread, then by link. Tools
#: run in parallel, so every touch holds the lock.
_offered: dict[str, dict[str, JobPosting]] = {}
_offered_lock = threading.Lock()

#: A bare link as it appears in a Slack message.
_LINK_RE = re.compile(r"https?://[^\s<>|]+")
#: Punctuation a sentence can leave stuck to the end of a link.
_TRAILING_PUNCTUATION = ".,;:!?)'\""


def render_unsent(
    header: str, postings: list[JobPosting], footer: str = "", *, config: RunnableConfig
) -> str:
    """``render_postings``, minus what this digest already sent you.

    A store that can't be read hides nothing and says so, so the model can pass
    it on: some roles may repeat, which beats a digest that never arrives.

    Args:
        header: As for ``render_postings``; may use ``{count}``.
        postings: What the source returned.
        footer: As for ``render_postings``.
        config: The tool's run config, which names the thread.
    """
    thread = _thread_id(config)
    if not thread.startswith(DIGEST_THREAD_PREFIX):
        return render_postings(header, postings, footer)

    agent = thread.removeprefix(DIGEST_THREAD_PREFIX)
    days = settings.DIGEST_DEDUPE_DAYS
    try:
        sent = shared_jobs.recently_shared(agent, days)
    except shared_jobs.SharedJobsError as e:
        log.warning("Digest %s: couldn't read what was already sent: %s", agent, e)
        unsent = postings
        note = f"_Couldn't check which of these were already sent ({e}); some may repeat._"
    else:
        unsent = [posting for posting in postings if posting.key not in sent]
        hidden = len(postings) - len(unsent)
        note = f"_{hidden} more already sent in the last {days} days, hidden._" if hidden else ""

    _offer(thread, unsent)
    return render_postings(header, unsent, "\n".join(part for part in (footer, note) if part))


def take_sent(thread: str, message: str) -> list[JobPosting]:
    """The postings offered on ``thread`` whose link appears in ``message``.

    Forgets the thread's offers either way, so a second call finds nothing.

    Args:
        thread: The digest's thread, e.g. ``digest:bigtech``.
        message: The DM as it was posted.
    """
    with _offered_lock:
        offered = _offered.pop(thread, {})
    links = set()
    for link in _LINK_RE.findall(message):
        links.update({link, link.rstrip(_TRAILING_PUNCTUATION)})
    return [posting for url, posting in offered.items() if url in links]


def _offer(thread: str, postings: list[JobPosting]) -> None:
    """Note what the model was shown. A posting with no link can't be matched later."""
    with _offered_lock:
        offered = _offered.setdefault(thread, {})
        for posting in postings:
            if posting.url:
                offered.setdefault(posting.url, posting)


def _thread_id(config: RunnableConfig) -> str:
    """The thread this tool call belongs to, or "" outside a graph run."""
    return str((config.get("configurable") or {}).get("thread_id") or "")
