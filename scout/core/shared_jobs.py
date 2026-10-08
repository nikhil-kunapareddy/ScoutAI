"""The jobs each agent's digest has already sent, so the next one can skip them.

Deliberately *not* in the checkpointer, for the referral list's reason: a thread
is disposable, and the digest now resets its own every morning, but what was
sent is a record the next run depends on. It lives in ``SHARED_DB``, one SQLite
file in ``state/`` that every agent's process opens — ``deploy.sh`` leaves the
folder out of its rsync, so a redeploy cannot overwrite the box's copy.

One row per job per agent, keyed ``(agent, job_id)``. The same opening can reach
you once from BigTech and once from the Referral Window, since they answer
different questions, but never twice from one agent inside the window. Sending
it again refreshes ``shared_at``, so a role still open a month on can come back
once and then hides for another window.

    CREATE TABLE shared_jobs (agent, job_id, title, company, url, shared_at,
                              PRIMARY KEY (agent, job_id))

Every call opens its own short connection. The digest touches this twice a run,
so there is nothing worth pooling, and nothing to share across threads.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..tools.jobs.posting import JobPosting
from . import settings
from .paths import under_root

#: How long to wait for another process's write before giving up. The writes are
#: a few rows each, so this only matters if two digests overlap.
_BUSY_TIMEOUT_SECONDS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_jobs (
    agent     TEXT NOT NULL,
    job_id    TEXT NOT NULL,
    title     TEXT NOT NULL,
    company   TEXT NOT NULL,
    url       TEXT NOT NULL,
    shared_at TEXT NOT NULL,
    PRIMARY KEY (agent, job_id)
)
"""

_RECORD = """
INSERT INTO shared_jobs (agent, job_id, title, company, url, shared_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (agent, job_id) DO UPDATE SET
    title = excluded.title,
    company = excluded.company,
    url = excluded.url,
    shared_at = excluded.shared_at
"""


class SharedJobsError(RuntimeError):
    """The store could not be read or written. Callers turn this into a log line
    or a sentence; a broken store costs a repeat, never the digest."""


def store_path() -> Path:
    """Where the store lives, per ``SHARED_DB``."""
    return under_root(settings.SHARED_DB)


def recently_shared(agent: str, days: int, now: datetime | None = None) -> set[str]:
    """The keys of every job ``agent`` sent in the last ``days`` days.

    Args:
        agent: The agent's registry key, e.g. ``"bigtech"``.
        days: How far back to look.
        now: The current time; defaults to the real one.
    """
    cutoff = _timestamp((now or _utc_now()) - timedelta(days=days))
    with _connection() as conn:
        rows = conn.execute(
            "SELECT job_id FROM shared_jobs WHERE agent = ? AND shared_at >= ?",
            (agent, cutoff),
        ).fetchall()
    return {job_id for (job_id,) in rows}


def record(agent: str, postings: Iterable[JobPosting], now: datetime | None = None) -> int:
    """Remember that ``agent`` just sent ``postings``, and return how many were kept.

    A posting with no stable key is skipped: there is nothing to match it by
    tomorrow.

    Args:
        agent: The agent's registry key, e.g. ``"bigtech"``.
        postings: What went out in the DM.
        now: The current time; defaults to the real one.
    """
    shared_at = _timestamp(now or _utc_now())
    rows = [
        (agent, key, posting.title, posting.organization, posting.url, shared_at)
        for posting in postings
        if (key := posting.key) is not None
    ]
    with _connection() as conn, conn:
        conn.executemany(_RECORD, rows)
    return len(rows)


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """A connection to the store, with the table in place, closed afterwards."""
    path = store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=_BUSY_TIMEOUT_SECONDS)) as conn:
            # WAL so a digest writing never blocks another process reading.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)
            yield conn
    except (sqlite3.Error, OSError) as e:
        raise SharedJobsError(f"{path}: {e}") from e


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(moment: datetime) -> str:
    """ISO 8601 in UTC, which sorts in time order as plain text."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")
