"""Hiding what a digest already sent or what is too old, and spotting what it sends.

Outside a digest the job tools must answer exactly as before; inside one, a
posting the agent ever sent, or one posted before the cutoff, must never reach
the model at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scout.core import settings, shared_jobs
from scout.tools import build_registry
from scout.tools.jobs import (
    amazon,
    apple,
    ashby,
    bloomberg,
    boston_university,
    cisco,
    google,
    greenhouse,
    lenovo,
    microsoft,
    netflix,
    northeastern,
    oracle,
    smartrecruiters,
    uber,
    unsent,
    workday,
)
from scout.tools.jobs.posting import JobPosting, render_postings

from .conftest import FakeRequests, FakeResponse, stub_requests

DIGEST = {"configurable": {"thread_id": "digest:bigtech"}}
CHAT = {"configurable": {"thread_id": "U012ABCDEF"}}

SEEN = JobPosting("ML Engineer", "Amazon", "https://jobs/1", job_id="amazon:1")
FRESH = JobPosting("AI Engineer", "Amazon", "https://jobs/2", job_id="amazon:2")


@pytest.fixture(autouse=True)
def no_leftover_offers():
    """Offers live for the process, so any digest-thread test elsewhere in the
    suite (the referral tool's, say) leaves some behind. Start and end clean."""
    unsent._offered.clear()
    yield
    unsent._offered.clear()


def test_outside_a_digest_nothing_is_hidden_or_offered() -> None:
    shared_jobs.record("bigtech", [SEEN])

    out = unsent.render_unsent("*{count} found:*", [SEEN, FRESH], config=CHAT)

    assert out == render_postings("*{count} found:*", [SEEN, FRESH])
    assert unsent._offered == {}


def test_a_config_with_no_thread_is_not_a_digest() -> None:
    out = unsent.render_unsent("*{count} found:*", [SEEN], config={})

    assert out == render_postings("*{count} found:*", [SEEN])


def test_a_digest_hides_what_this_agent_already_sent() -> None:
    shared_jobs.record("bigtech", [SEEN])

    out = unsent.render_unsent("*{count} found:*", [SEEN, FRESH], footer="_f_", config=DIGEST)

    assert "*1 found:*" in out
    assert "AI Engineer" in out
    assert "ML Engineer" not in out
    assert "_f_\n_1 more already sent in an earlier digest, hidden._" in out


def test_what_was_sent_long_ago_is_still_hidden() -> None:
    """No window: "not in a previous run" means any previous run."""
    shared_jobs.record("bigtech", [SEEN], now=datetime.now(timezone.utc) - timedelta(days=365))

    out = unsent.render_unsent("*{count} found:*", [SEEN], config=DIGEST)

    assert "ML Engineer" not in out


# --- The age cutoff ----------------------------------------------------------


def days_ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


OLD = JobPosting("Old Scientist", "Netflix", "https://jobs/old", date=days_ago(40))
WEEK_OLD = JobPosting("Week Scientist", "Netflix", "https://jobs/week", date=days_ago(6.5))


def test_a_digest_hides_what_was_posted_before_the_cutoff() -> None:
    out = unsent.render_unsent("*{count} found:*", [OLD, WEEK_OLD], footer="_f_", config=DIGEST)

    assert "*1 found:*" in out
    assert "Week Scientist" in out
    assert "Old Scientist" not in out
    assert "_f_\n_1 more posted over 7 days ago, hidden._" in out
    assert list(unsent._offered["digest:bigtech"]) == ["https://jobs/week"]


def test_the_cutoff_comes_from_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "DIGEST_MAX_AGE_DAYS", 60)

    out = unsent.render_unsent("*{count} found:*", [OLD], config=DIGEST)

    assert "Old Scientist" in out
    assert "hidden" not in out


def test_a_workday_label_is_held_to_the_cutoff() -> None:
    stale = JobPosting("Stale", "Workday Co", "https://wd/1", posted_label="Posted 30+ Days Ago")
    recent = JobPosting("Recent", "Workday Co", "https://wd/2", posted_label="Posted 3 Days Ago")

    out = unsent.render_unsent("*{count} found:*", [stale, recent], config=DIGEST)

    assert "Recent" in out
    assert "Stale" not in out


def test_an_undated_posting_passes_once() -> None:
    """Google and Bloomberg publish no date. Such a role is shown, and once sent
    it is gone for good: the store does what the cutoff cannot."""
    undated = JobPosting(
        "ML Engineer", "Google", "https://g/1", posted_label="not published by Google",
    )

    first = unsent.render_unsent("*{count} found:*", [undated], config=DIGEST)
    shared_jobs.record("bigtech", unsent.take_sent("digest:bigtech", "https://g/1"))
    second = unsent.render_unsent("*{count} found:*", [undated], config=DIGEST)

    assert "ML Engineer" in first
    assert "ML Engineer" not in second


def test_each_rule_counts_what_it_hid() -> None:
    """An old role that was also sent counts once, as old."""
    shared_jobs.record("bigtech", [SEEN, OLD])

    out = unsent.render_unsent("*{count} found:*", [SEEN, OLD, FRESH], config=DIGEST)

    assert "_1 more posted over 7 days ago, hidden._\n" in out
    assert "_1 more already sent in an earlier digest, hidden._" in out


def test_chat_shows_old_postings() -> None:
    """The cutoff is the digest's rule. Asked in chat, every open role counts."""
    out = unsent.render_unsent("*{count} found:*", [OLD], config=CHAT)

    assert "Old Scientist" in out


def test_another_agents_sends_are_not_hidden() -> None:
    shared_jobs.record("referral", [SEEN])

    out = unsent.render_unsent("*{count} found:*", [SEEN], config=DIGEST)

    assert "ML Engineer" in out
    assert "hidden" not in out


def test_an_unreadable_store_hides_nothing_and_says_so(monkeypatch) -> None:
    """A repeat is a better morning than no digest."""
    def broken(agent: str) -> set[str]:
        raise shared_jobs.SharedJobsError("state/shared.sqlite: disk I/O error")

    monkeypatch.setattr(shared_jobs, "already_sent", broken)

    out = unsent.render_unsent("*{count} found:*", [SEEN, OLD], config=DIGEST)

    assert "ML Engineer" in out
    assert "Couldn't check which of these were already sent" in out
    assert "disk I/O error" in out
    assert "Old Scientist" not in out  # the age rule needs no store


def test_what_the_dm_links_to_is_what_was_sent() -> None:
    unsent.render_unsent("*{count} found:*", [SEEN, FRESH], config=DIGEST)
    message = "1. *AI Engineer* — Amazon\n    New this week\n    Oct 07 · https://jobs/2."

    sent = unsent.take_sent("digest:bigtech", message)

    assert sent == [FRESH]  # the trailing full stop is not part of the link


def test_the_offers_are_forgotten_once_taken() -> None:
    unsent.render_unsent("*{count} found:*", [FRESH], config=DIGEST)
    unsent.take_sent("digest:bigtech", "https://jobs/2")

    assert unsent.take_sent("digest:bigtech", "https://jobs/2") == []


def test_a_link_inside_another_does_not_count() -> None:
    """https://jobs/1 is a prefix of https://jobs/12, not a mention of it."""
    unsent.render_unsent("*{count} found:*", [SEEN], config=DIGEST)

    assert unsent.take_sent("digest:bigtech", "https://jobs/12") == []


def test_a_posting_with_no_link_is_never_offered() -> None:
    linkless = JobPosting("Data Scientist", "Bloomberg", "")

    unsent.render_unsent("*{count} found:*", [linkless], config=DIGEST)

    assert unsent._offered == {"digest:bigtech": {}}


# --- Through the real tools --------------------------------------------------


def test_a_real_tool_hides_what_was_sent_in_a_digest_turn(monkeypatch) -> None:
    """The whole chain: LangChain injects the config, the tool hands it on, and
    the role the digest already sent never reaches the model."""
    fake = FakeRequests(FakeResponse({"positions": [
        {"id": 1, "name": "Data Analyst", "t_create": days_ago(1).timestamp()},
        {"id": 2, "name": "Machine Learning Engineer", "t_create": days_ago(1).timestamp()},
        {"id": 3, "name": "Data Scientist", "t_create": days_ago(60).timestamp()},
    ]}))
    stub_requests(monkeypatch, netflix, fake)
    tool = build_registry([netflix]).tools[0]
    shared_jobs.record("bigtech", [JobPosting("x", "Netflix", "", job_id="netflix:2")])

    in_chat = tool.invoke({}, config=CHAT)
    in_digest = tool.invoke({}, config=DIGEST)

    assert "Machine Learning Engineer" in in_chat
    assert "Data Scientist" in in_chat
    assert "Machine Learning Engineer" not in in_digest
    assert "Data Scientist" not in in_digest
    assert "Data Analyst" in in_digest


JOB_TOOL_MODULES = [
    amazon, apple, ashby, bloomberg, boston_university, cisco, google, greenhouse,
    lenovo, microsoft, netflix, northeastern, oracle, smartrecruiters, uber, workday,
]


@pytest.mark.parametrize("module", JOB_TOOL_MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_no_job_tool_asks_the_model_for_its_config(module) -> None:
    """``config: RunnableConfig`` is injected only while the annotation is exactly
    that; anything else turns it into a parameter the model is asked to fill."""
    (tool,) = build_registry([module]).tools

    assert "config" not in tool.args
    assert {"keywords", "limit"} <= set(tool.args)
