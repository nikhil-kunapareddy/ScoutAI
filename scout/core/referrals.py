"""The user's referral list: companies where they know someone.

Deliberately *not* in the checkpointer. History is disposable — ``--reset``
drops a thread and trimming rewrites it — but this is a list the user typed once
and expects to find again. So it lives beside the checkpoint database in
``state/``, which ``deploy.sh`` leaves out of its rsync: a redeploy cannot
overwrite the box's list with whatever happens to be on a laptop.

One JSON document keyed by owner, rewritten whole on every change. A person has
a handful of referrals, so the simplest thing that cannot half-write a file is
the right one.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from . import settings
from .paths import under_root

log = logging.getLogger("scout")

#: The digest runs each agent on its own thread (``digest:<key>``) rather than
#: the user's, so a digest turn asking for referrals would otherwise look up an
#: owner that has none. Those turns belong to whoever the digest is addressed to.
DIGEST_THREAD_PREFIX = "digest:"

#: One writer at a time. slack-bolt dispatches on a thread pool, and the
#: per-user locks in GraphRunner only order *one* user's turns — two users
#: editing at once would otherwise read-modify-write over each other.
_LOCK = threading.RLock()


class ReferralStoreError(RuntimeError):
    """The store could not be read. Tools turn this into a sentence."""


@dataclass(frozen=True)
class Referral:
    """One company the user has a connection at."""

    company: str
    contact: str = ""
    note: str = ""
    added: str = ""

    def render(self) -> str:
        """One Slack-friendly line."""
        line = f"*{self.company}*"
        if self.contact:
            line += f" — {self.contact}"
        if self.note:
            line += f" ({self.note})"
        if self.added:
            line += f" · added {self.added}"
        return line


def store_path() -> Path:
    """Where the list lives, per ``REFERRALS_FILE``."""
    return under_root(settings.REFERRALS_FILE)


def owner_for(config: RunnableConfig | None) -> str:
    """Whose list a turn is acting on.

    The thread id is the Slack user id for a real conversation. A digest thread
    is not a person, so it resolves to the user the digest is sent to.
    """
    thread = ((config or {}).get("configurable") or {}).get("thread_id") or ""
    if thread.startswith(DIGEST_THREAD_PREFIX):
        return settings.DIGEST_SLACK_USER or thread
    return thread


def list_for(owner: str) -> list[Referral]:
    """Every referral ``owner`` has recorded, in the order they were added."""
    with _LOCK:
        return _read().get(owner, [])


def add(owner: str, company: str, contact: str = "", note: str = "") -> tuple[Referral, bool]:
    """Record a referral, returning it and whether it was newly created.

    Adding a company that is already listed updates it rather than duplicating
    it — the model re-adds freely, and two Stripes in the list helps nobody.
    """
    with _LOCK:
        data = _read()
        existing = data.get(owner, [])
        found = _find(existing, company)
        if found is None:
            referral = Referral(company.strip(), contact.strip(), note.strip(),
                                date.today().isoformat())
            data[owner] = [*existing, referral]
            _write(data)
            return referral, True

        # Keep what the caller did not mention; an update should not blank a
        # contact just because this turn only carried a note.
        merged = Referral(
            company=found.company,
            contact=contact.strip() or found.contact,
            note=note.strip() or found.note,
            added=found.added,
        )
        data[owner] = [merged if r is found else r for r in existing]
        _write(data)
        return merged, False


def remove(owner: str, company: str) -> Referral | None:
    """Drop a referral, returning it, or None if it was not listed."""
    with _LOCK:
        data = _read()
        existing = data.get(owner, [])
        found = _find(existing, company)
        if found is None:
            return None
        data[owner] = [r for r in existing if r is not found]
        _write(data)
        return found


def _find(referrals: list[Referral], company: str) -> Referral | None:
    """Match on the company name, ignoring case and stray whitespace.

    The user types "stripe" and the model sends "Stripe"; both mean the entry
    already in the list.
    """
    wanted = company.strip().casefold()
    return next((r for r in referrals if r.company.casefold() == wanted), None)


def _read() -> dict[str, list[Referral]]:
    """The whole store. A missing file is an empty one — nothing to migrate."""
    path = store_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            owner: [Referral(**entry) for entry in entries]
            for owner, entries in raw.items()
        }
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        # Refusing beats silently starting over: the file is the user's data,
        # and a bad parse is far more likely to be a bug than a real reset.
        raise ReferralStoreError(f"{path} is not readable ({e})") from e


def _write(data: dict[str, list[Referral]]) -> None:
    """Replace the store atomically.

    Write-then-rename, so a crash mid-write leaves the previous list intact
    rather than a truncated file that ``_read`` would then refuse.
    """
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        owner: [asdict(r) for r in entries]
        for owner, entries in data.items() if entries
    }
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
