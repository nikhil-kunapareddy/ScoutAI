"""Hiding what a digest already sent, and spotting what it sends.

Outside a digest the job tools must answer exactly as before; inside one, a
posting the agent sent within the window must never reach the model at all.
"""

from __future__ import annotations

from datetime import datetime, timezone

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
    assert "_f_\n_1 more already sent in the last 30 days, hidden._" in out


def test_the_window_comes_from_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "DIGEST_DEDUPE_DAYS", 7)
    shared_jobs.record("bigtech", [SEEN])

    out = unsent.render_unsent("*{count} found:*", [SEEN], config=DIGEST)

    assert "in the last 7 days" in out


def test_another_agents_sends_are_not_hidden() -> None:
    shared_jobs.record("referral", [SEEN])

    out = unsent.render_unsent("*{count} found:*", [SEEN], config=DIGEST)

    assert "ML Engineer" in out
    assert "hidden" not in out


def test_an_unreadable_store_hides_nothing_and_says_so(monkeypatch) -> None:
    """A repeat is a better morning than no digest."""
    def broken(agent: str, days: int) -> set[str]:
        raise shared_jobs.SharedJobsError("state/shared.sqlite: disk I/O error")

    monkeypatch.setattr(shared_jobs, "recently_shared", broken)

    out = unsent.render_unsent("*{count} found:*", [SEEN], config=DIGEST)

    assert "ML Engineer" in out
    assert "Couldn't check which of these were already sent" in out
    assert "disk I/O error" in out


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
    now = datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp()
    fake = FakeRequests(FakeResponse({"positions": [
        {"id": 1, "name": "Data Analyst", "t_create": now},
        {"id": 2, "name": "Machine Learning Engineer", "t_create": now},
    ]}))
    stub_requests(monkeypatch, netflix, fake)
    tool = build_registry([netflix]).tools[0]
    shared_jobs.record("bigtech", [JobPosting("x", "Netflix", "", job_id="netflix:2")])

    in_chat = tool.invoke({}, config=CHAT)
    in_digest = tool.invoke({}, config=DIGEST)

    assert "Machine Learning Engineer" in in_chat
    assert "Machine Learning Engineer" not in in_digest
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
