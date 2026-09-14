"""Where conversation state lives.

The checkpointer *is* the history: one thread per Slack user, holding the
provider-agnostic messages that let a user switch model mid-conversation. Which
one a process gets is the only thing that decides whether that history outlives
the process.

``InMemorySaver`` is the default, so tests and local runs stay exactly as they
were — no file, no cleanup, and a restart is a clean slate. Setting
``CHECKPOINT_DB`` swaps in SQLite, which is what a deployed bot wants: history
and the parsed resume profile survive restarts and redeploys, so the resume is
parsed once rather than once per restart.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from . import settings
from .paths import PROJECT_ROOT

log = logging.getLogger("scout")

#: How long to wait for a concurrent writer before raising "database is locked".
#: Generous because a turn holds no transaction open while the model thinks — the
#: writes are short, so waiting is almost always better than failing the turn.
_BUSY_TIMEOUT_SECONDS = 30


def build_checkpointer() -> BaseCheckpointSaver:
    """The checkpointer this process should use, per ``CHECKPOINT_DB``.

    Call it once per saver that needs its own thread space — a
    ``ResumeTailoredAgent`` wants two (the job conversation and the parser's),
    and they share one database file safely because their thread ids differ.
    """
    if not settings.CHECKPOINT_DB:
        return InMemorySaver()

    path = _resolve(settings.CHECKPOINT_DB)
    log.info("Checkpointing conversation state to %s", path)
    return SqliteSaver(_connect(path))


def _resolve(configured: str) -> Path:
    """Absolute path to the database file.

    A relative ``CHECKPOINT_DB`` is taken against the project root, not the
    working directory: systemd starts the bot from ``/``, so a bare
    ``state/scout.sqlite`` would otherwise land somewhere nobody expects.
    """
    path = Path(configured).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _connect(path: Path) -> sqlite3.Connection:
    """Open the database, configured for a threaded bot process."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path,
        # slack-bolt dispatches events on a thread pool, so the connection is
        # used from whichever worker picked up the turn. Per-user locks keep one
        # user's turns in order; SQLite serializes the rest.
        check_same_thread=False,
        timeout=_BUSY_TIMEOUT_SECONDS,
    )
    # WAL so a reader never blocks the writer — two savers share this file, and
    # the digest run reads it while the bot is mid-turn.
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
