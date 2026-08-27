"""The shared job-tool helpers: filtering, argument clamping, and rendering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scout.tools.jobs import (
    PROFILE_QUERIES,
    JobPosting,
    clamp_int,
    is_ai_ml_role,
    matches_keywords,
    render_postings,
    search_queries,
    take_newest,
)


@pytest.mark.parametrize("title", [
    "Machine Learning Engineer",
    "Applied Scientist II",
    "Senior Research Engineer, Personalization",
    "Software Engineer, AI",          # standalone token
    "LLM Infrastructure Engineer",
    "Data Scientist, Ads",
    "GenAI Solutions Architect",
])
def test_ai_ml_titles(title: str) -> None:
    assert is_ai_ml_role(title)


@pytest.mark.parametrize("title", [
    "Financial Analyst",
    "Supply Chain Manager",
    "Technical Program Manager",
    "Retail Sales Associate",
    "Mailroom Clerk",               # "ai" must not match inside another word
    "Chair of Sustainability",
])
def test_non_ai_ml_titles(title: str) -> None:
    assert not is_ai_ml_role(title)


@pytest.mark.parametrize("value,expected", [
    (5, 5),
    ("7", 7),          # models often send numbers as strings
    (0, 1),            # below the floor
    (999, 25),         # above the ceiling
    ("ten", 15),       # unparseable -> default
    (None, 15),
    (3.9, 3),          # truncated, not rounded
])
def test_clamp_int(value: object, expected: int) -> None:
    assert clamp_int(value, 15, 1, 25) == expected


def test_search_queries_falls_back_to_the_profile() -> None:
    assert search_queries("applied scientist") == ("applied scientist",)
    assert search_queries("   ") == PROFILE_QUERIES


def test_matches_keywords() -> None:
    assert matches_keywords("Machine Learning Engineer", [])          # no terms = match all
    assert matches_keywords("Machine Learning Engineer", ["learning"])
    assert not matches_keywords("Machine Learning Engineer", ["robotics"])


def test_posted_text_prefers_a_real_date() -> None:
    dated = JobPosting("T", "Org", "u", date=datetime(2026, 8, 3))
    labelled = JobPosting("T", "Org", "u", posted_label="Posted 5 Days Ago")
    unknown = JobPosting("T", "Org", "u")

    assert dated.posted_text == "Aug 03, 2026"
    assert labelled.posted_text == "Posted 5 Days Ago"
    assert unknown.posted_text == "not listed"


def test_take_newest_mixes_aware_and_naive_dates() -> None:
    """Regression: comparing tz-aware Greenhouse dates with naive Amazon ones
    raises, so ordering goes through a POSIX timestamp instead."""
    aware = JobPosting("aware", "Org", "u", date=datetime.now(timezone.utc))
    naive = JobPosting("naive", "Org", "u", date=datetime.now() - timedelta(days=5))
    undated = JobPosting("undated", "Org", "u")

    ordered = take_newest([undated, naive, aware], limit=10)
    assert [p.title for p in ordered] == ["aware", "naive", "undated"]


def test_take_newest_applies_the_limit() -> None:
    postings = [
        JobPosting(str(i), "Org", "u", date=datetime(2026, 1, i + 1)) for i in range(5)
    ]
    assert len(take_newest(postings, limit=2)) == 2


def test_render_postings_formats_a_slack_message() -> None:
    postings = [
        JobPosting("ML Engineer", "Netflix", "https://x/1", location="Los Gatos, CA",
                   date=datetime(2026, 8, 20)),
        JobPosting("Applied Scientist", "Netflix", "https://x/2"),
    ]
    out = render_postings("*Header — {count} found:*", postings, footer="_note_")

    assert out.startswith("*Header — 2 found:*")
    assert "1. *ML Engineer*" in out
    assert "    Organization: Netflix" in out
    assert "    Location: Los Gatos, CA" in out
    assert "    Link: https://x/1" in out
    assert "    Posted: Aug 20, 2026" in out
    # A posting with no location omits the line entirely rather than showing a blank.
    assert "    Location: \n" not in out
    assert "2. *Applied Scientist*" in out
    assert out.rstrip().endswith("_note_")


def test_render_postings_without_footer() -> None:
    out = render_postings("*H — {count}:*", [JobPosting("T", "Org", "u")])
    assert "_note_" not in out
    assert "*H — 1:*" in out
