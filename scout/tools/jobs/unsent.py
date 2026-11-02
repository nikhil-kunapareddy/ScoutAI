"""What a digest may show you: recent, and not sent before.

Every job tool renders through ``render_unsent``. Outside a digest it is
``render_postings`` and nothing more, so a chat reply is exactly what it was. In
a digest turn — a thread named ``digest:<agent>`` — it drops, before the model
sees the list:

- every posting older than ``DIGEST_MAX_AGE_DAYS``, by the source's date or its
  "Posted 5 Days Ago" label. One the source gives no age for passes; and
- every posting that agent's digest has ever sent.

Both are rules rather than requests. Asking the model to "prefer roles posted in
the last few days" let it fill a 25-role report with August's roles whenever the
week was quiet. What survives is noted as offered.

After the DM is posted, ``take_sent`` matches the links in it against what was
offered. That is how the digest learns what it actually sent: the model picks
from what it was shown, and only its picks are recorded, so a role it ranked out
today is still new tomorrow. A link the model mangled fails soft — that one role
may come back.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone

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
    """``render_postings``, minus what is too old or already sent, in a digest.

    A store that can't be read hides nothing on that count and says so, so the
    model can pass it on: some roles may repeat, which beats a digest that never
    arrives. The age rule needs no store and holds either way.

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
    max_age = settings.DIGEST_MAX_AGE_DAYS
    recent = [posting for posting in postings if not _older_than(posting, max_age)]
    notes = [footer]
    if stale := len(postings) - len(recent):
        notes.append(f"_{stale} more posted over {max_age} days ago, hidden._")

    try:
        sent = shared_jobs.already_sent(agent)
    except shared_jobs.SharedJobsError as e:
        log.warning("Digest %s: couldn't read what was already sent: %s", agent, e)
        unsent = recent
        notes.append(f"_Couldn't check which of these were already sent ({e}); some may repeat._")
    else:
        unsent = [posting for posting in recent if posting.key not in sent]
        if repeats := len(recent) - len(unsent):
            notes.append(f"_{repeats} more already sent in an earlier digest, hidden._")

    _offer(thread, unsent)
    return render_postings(header, unsent, "\n".join(note for note in notes if note))


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


def _older_than(posting: JobPosting, days: int) -> bool:
    """Whether the source says ``posting`` went up more than ``days`` days ago."""
    age = posting.age_days(datetime.now(timezone.utc))
    return age is not None and age > days


def _thread_id(config: RunnableConfig) -> str:
    """The thread this tool call belongs to, or "" outside a graph run."""
    return str((config.get("configurable") or {}).get("thread_id") or "")
