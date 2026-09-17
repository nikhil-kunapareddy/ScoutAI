"""The hosted board platforms: Greenhouse and Ashby.

Greenhouse and Ashby are one shape — a board per company at a predictable URL,
no server-side filtering — so they share ``HostedBoard`` and are tested
together. Two things are being pinned: the shape's own behaviour (an unknown
company, a board that was down, a board with nothing matching, all told apart),
and each platform's own row reader, where the location rules differ.
"""

from __future__ import annotations

import pytest

from scout.tools.jobs import ashby, greenhouse

from .conftest import FakeRequests, FakeResponse, call_tool

# --- Greenhouse -----------------------------------------------------------

GREENHOUSE_JOBS = {
    "jobs": [
        {"title": "Machine Learning Engineer", "absolute_url": "https://gh/1",
         "location": {"name": "San Francisco, CA"}, "first_published": "2026-08-20T10:00:00-04:00"},
        {"title": "Applied Scientist", "absolute_url": "https://gh/2",
         "location": {"name": "Bengaluru, India"}, "first_published": "2026-08-21T10:00:00-04:00"},
        {"title": "Office Manager", "absolute_url": "https://gh/3",
         "location": {"name": "Remote"}, "first_published": "2026-08-22T10:00:00-04:00"},
        {"title": "Data Scientist, Growth", "absolute_url": "https://gh/4",
         "location": {"name": "Remote"}, "updated_at": "not-a-date"},
    ]
}


def test_greenhouse_filters_by_relevance_and_location(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(GREENHOUSE_JOBS))
    out = call_tool(greenhouse, "search_greenhouse_jobs", fake, monkeypatch,
               company="databricks")

    assert "Latest Databricks AI/ML roles" in out
    assert "Machine Learning Engineer" in out
    assert "Data Scientist, Growth" in out
    assert "Applied Scientist" not in out   # India
    assert "Office Manager" not in out      # not AI/ML
    assert "Posted: Aug 20, 2026" in out
    assert "Posted: not listed" in out      # unparseable date
    assert "databricks" in fake.calls[0]["url"]


def test_greenhouse_narrows_by_keyword(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(GREENHOUSE_JOBS))
    out = call_tool(greenhouse, "search_greenhouse_jobs", fake, monkeypatch,
               company="databricks", keywords="data scientist")
    assert "Data Scientist, Growth" in out
    assert "Machine Learning Engineer" not in out


def test_greenhouse_accepts_any_casing_of_the_company(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(GREENHOUSE_JOBS))
    out = call_tool(greenhouse, "search_greenhouse_jobs", fake, monkeypatch,
               company="  Databricks  ")
    assert "Latest Databricks AI/ML roles" in out


def test_greenhouse_lists_supported_companies_for_an_unknown_one(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(GREENHOUSE_JOBS))
    out = call_tool(greenhouse, "search_greenhouse_jobs", fake, monkeypatch, company="acme")
    assert "Unknown company 'acme'" in out
    assert "databricks" in out
    assert fake.calls == []  # no pointless request


def test_greenhouse_distinguishes_down_from_empty(monkeypatch) -> None:
    """"Board unreachable" and "no matching roles" are different answers."""
    down = call_tool(greenhouse, "search_greenhouse_jobs",
                FakeRequests(error=OSError("503")), monkeypatch, company="stripe")
    empty = call_tool(greenhouse, "search_greenhouse_jobs",
                 FakeRequests(FakeResponse({"jobs": []})), monkeypatch, company="stripe")

    assert "Couldn't reach Stripe's careers board" in down
    assert "No relevant Stripe roles found" in empty


@pytest.mark.parametrize(("location", "is_us"), [
    ("San Francisco, CA", True),
    ("United States", True),
    ("Remote", True),
    ("Remote - USA", True),
    ("London, United Kingdom", False),
    ("Bengaluru, India", False),
    ("EMEA", False),
])
def test_greenhouse_location_heuristic(location: str, is_us: bool) -> None:
    assert greenhouse._is_us_location(location) is is_us



# --- Ashby ----------------------------------------------------------------

ASHBY_JOBS = {
    "jobs": [
        {"title": "Senior Machine Learning Engineer", "jobUrl": "https://ashby/1",
         "location": "Boston, MA", "publishedAt": "2026-07-15T10:00:00.500+00:00",
         "isListed": True,
         "address": {"postalAddress": {"addressCountry": "United States"}}},
        {"title": "Data Scientist, Growth", "jobUrl": "https://ashby/2",
         "location": "Mumbai, India", "publishedAt": "2026-08-01T10:00:00.000+00:00",
         "isListed": True,
         "address": {"postalAddress": {"addressCountry": "India"}}},
        {"title": "Physical Therapist", "jobUrl": "https://ashby/3",
         "location": "Boston, MA", "publishedAt": "2026-08-02T10:00:00.000Z",
         "isListed": True,
         "address": {"postalAddress": {"addressCountry": "United States"}}},
        {"title": "ML Engineer, Perception", "jobUrl": "https://ashby/4",
         "location": "Boston, MA", "publishedAt": "2026-08-03T10:00:00.000Z",
         "isListed": False,
         "address": {"postalAddress": {"addressCountry": "United States"}}},
        {"title": "AI Research Engineer", "applyUrl": "https://ashby/5/application",
         "location": "Remote", "publishedAt": "nonsense", "isListed": True},
    ]
}


def test_ashby_filters_by_relevance_listing_and_country(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(ASHBY_JOBS))
    out = call_tool(ashby, "search_ashby_jobs", fake, monkeypatch, company="whoop")

    assert "Latest WHOOP AI/ML roles" in out
    assert "Senior Machine Learning Engineer" in out
    assert "Data Scientist, Growth" not in out   # India
    assert "Physical Therapist" not in out       # not AI/ML
    assert "ML Engineer, Perception" not in out  # pulled from the board
    assert "Location: Boston, MA" in out
    assert "Posted: Jul 15, 2026" in out
    # No structured country: kept, since a missing country is not evidence of
    # a foreign role. An unparseable timestamp is not worth echoing at the user.
    assert "AI Research Engineer" in out
    assert "Posted: not listed" in out
    # A row with no jobUrl falls back to the apply link.
    assert "https://ashby/5/application" in out
    assert "whoop" in fake.calls[0]["url"]


def test_ashby_narrows_by_keyword(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(ASHBY_JOBS))
    out = call_tool(ashby, "search_ashby_jobs", fake, monkeypatch,
                    company="whoop", keywords="research")
    assert "AI Research Engineer" in out
    assert "Senior Machine Learning Engineer" not in out


def test_ashby_lists_supported_companies_for_an_unknown_one(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(ASHBY_JOBS))
    out = call_tool(ashby, "search_ashby_jobs", fake, monkeypatch, company="acme")
    assert "Unknown company 'acme'" in out
    assert "whoop" in out
    assert fake.calls == []  # no pointless request


@pytest.mark.parametrize("value", [None, "", "not-a-timestamp", 17_000_000_000])
def test_ashby_tolerates_an_unparseable_published_date(value: object) -> None:
    """A row Scout cannot date is still a role worth showing, undated."""
    assert ashby._parse_published(value) is None


def test_ashby_distinguishes_down_from_empty(monkeypatch) -> None:
    down = call_tool(ashby, "search_ashby_jobs",
                     FakeRequests(error=OSError("503")), monkeypatch, company="whoop")
    empty = call_tool(ashby, "search_ashby_jobs",
                      FakeRequests(FakeResponse({"jobs": []})), monkeypatch, company="whoop")

    assert "Couldn't reach WHOOP's careers board" in down
    assert "No relevant WHOOP roles found" in empty


