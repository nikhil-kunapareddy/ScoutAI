"""The shared store: what each agent's digest already sent, and for how long it
stays hidden. ``conftest.shared_db`` gives every test its own file."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from scout.core import settings, shared_jobs
from scout.tools.jobs.posting import JobPosting

NOW = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)


def posting(job_id: str = "amazon:1", url: str = "https://jobs/1") -> JobPosting:
    return JobPosting("ML Engineer", "Amazon", url, job_id=job_id)


def test_what_was_sent_is_hidden_inside_the_window() -> None:
    shared_jobs.record("bigtech", [posting()], now=NOW - timedelta(days=29))

    assert shared_jobs.recently_shared("bigtech", 30, now=NOW) == {"amazon:1"}


def test_what_was_sent_comes_back_after_the_window() -> None:
    shared_jobs.record("bigtech", [posting()], now=NOW - timedelta(days=31))

    assert shared_jobs.recently_shared("bigtech", 30, now=NOW) == set()
    assert shared_jobs.recently_shared("bigtech", 40, now=NOW) == {"amazon:1"}


def test_each_agent_has_its_own_record() -> None:
    """BigTech and the Referral Window answer different questions, so a role one
    sent is still news from the other."""
    shared_jobs.record("bigtech", [posting()], now=NOW)

    assert shared_jobs.recently_shared("referral", 30, now=NOW) == set()


def test_sending_again_restarts_the_window() -> None:
    """A role still open a month on can come back once, then hides again."""
    shared_jobs.record("bigtech", [posting()], now=NOW - timedelta(days=45))
    shared_jobs.record("bigtech", [posting()], now=NOW)

    assert shared_jobs.recently_shared("bigtech", 30, now=NOW) == {"amazon:1"}
    with sqlite3.connect(shared_jobs.store_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shared_jobs").fetchone() == (1,)


def test_a_posting_without_an_id_is_remembered_by_its_link() -> None:
    kept = shared_jobs.record("bigtech", [posting(job_id="", url="https://bb/7")], now=NOW)

    assert kept == 1
    assert shared_jobs.recently_shared("bigtech", 30, now=NOW) == {"url:https://bb/7"}


def test_a_posting_with_nothing_stable_is_not_recorded() -> None:
    """With neither an id nor a link, tomorrow has nothing to match it by."""
    kept = shared_jobs.record("bigtech", [posting(job_id="", url="")], now=NOW)

    assert kept == 0
    assert shared_jobs.recently_shared("bigtech", 30, now=NOW) == set()


def test_the_row_carries_what_a_person_would_want_to_read_back() -> None:
    shared_jobs.record("bigtech", [posting()], now=NOW)

    with sqlite3.connect(shared_jobs.store_path()) as conn:
        row = conn.execute("SELECT * FROM shared_jobs").fetchone()
    assert row == (
        "bigtech", "amazon:1", "ML Engineer", "Amazon", "https://jobs/1",
        "2026-10-08T15:00:00+00:00",
    )


def test_the_real_clock_is_used_by_default() -> None:
    shared_jobs.record("bigtech", [posting()])

    assert shared_jobs.recently_shared("bigtech", 1) == {"amazon:1"}


def test_a_store_that_cannot_be_opened_says_where(monkeypatch, tmp_path) -> None:
    """A directory where the file should be: SQLite cannot open it."""
    monkeypatch.setattr(settings, "SHARED_DB", str(tmp_path))

    with pytest.raises(shared_jobs.SharedJobsError, match=str(tmp_path)):
        shared_jobs.recently_shared("bigtech", 30)


def test_a_relative_path_is_taken_against_the_project_root(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SHARED_DB", "state/shared.sqlite")

    path = shared_jobs.store_path()

    assert path.is_absolute()
    assert path.parts[-2:] == ("state", "shared.sqlite")
