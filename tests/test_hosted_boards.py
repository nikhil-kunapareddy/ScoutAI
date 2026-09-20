"""The platforms that host many companies: Greenhouse, Ashby, SmartRecruiters, Workday.

The first three are one shape — a board per company at a predictable URL — so
they share ``HostedBoard`` and are tested together. Workday is a second shape
(a POST, and a URL built from three parts) but the same idea, so it is tested
here rather than alongside the one-company sources.

Two things are being pinned throughout: the shape's own behaviour (an unknown
company, a board that was down, a board with nothing matching, all told apart),
and each platform's own row reader, where the location and date rules differ.
"""

from __future__ import annotations

import pytest

from scout.tools.jobs import ashby, greenhouse, smartrecruiters, workday
from scout.tools.jobs.posting import JobPosting

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



# --- SmartRecruiters ------------------------------------------------------

SMARTRECRUITERS_JOBS = {
    "content": [
        # Padded title: SmartRecruiters ships stray whitespace on real rows.
        {"id": "1", "name": "  Machine Learning Engineer  ",
         "company": {"identifier": "ServiceNow"},
         "location": {"country": "us",
                      "fullLocation": "Santa Clara, California, United States"},
         "releasedDate": "2026-09-19T02:10:29.545Z"},
        {"id": "2", "name": "Data Scientist, Growth",
         "company": {"identifier": "ServiceNow"},
         "location": {"country": "us", "city": "San Diego", "region": "California"},
         "releasedDate": "2026-09-18T02:10:29.545Z"},
        {"id": "3", "name": "Office Manager",
         "company": {"identifier": "ServiceNow"},
         "location": {"country": "us"}, "releasedDate": "2026-09-17T02:10:29.545Z"},
        {"id": "4", "name": "ML Engineer, Platform",
         "company": {"identifier": "ServiceNow"},
         "location": {"country": "in", "city": "Hyderabad"},
         "releasedDate": "2026-09-16T02:10:29.545Z"},
        # No id and no board identifier: still a role, just without a link.
        {"name": "AI Research Engineer", "company": {}, "location": {},
         "releasedDate": "nonsense"},
    ]
}


def test_smartrecruiters_filters_by_relevance_and_country(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(SMARTRECRUITERS_JOBS))
    out = call_tool(smartrecruiters, "search_smartrecruiters_jobs", fake, monkeypatch,
                    company="servicenow")

    assert "Latest ServiceNow AI/ML roles" in out
    assert "Machine Learning Engineer" in out
    assert "Office Manager" not in out          # not AI/ML
    assert "ML Engineer, Platform" not in out   # country is not us
    assert "Posted: Sep 19, 2026" in out
    assert "Posted: not listed" in out          # unparseable timestamp
    # The padding is gone from the rendered title.
    assert "*Machine Learning Engineer*" in out
    # fullLocation when the board gives one, city/region built when it doesn't.
    assert "Location: Santa Clara, California, United States" in out
    assert "Location: San Diego, California" in out
    # The country filter is applied by the request, not guessed at.
    assert "country=us" in fake.calls[0]["url"]


def test_smartrecruiters_builds_the_job_url_from_the_row(monkeypatch) -> None:
    """``company.identifier`` carries the capitalisation the job host wants."""
    fake = FakeRequests(FakeResponse(SMARTRECRUITERS_JOBS))
    out = call_tool(smartrecruiters, "search_smartrecruiters_jobs", fake, monkeypatch,
                    company="servicenow")
    assert "https://jobs.smartrecruiters.com/ServiceNow/1" in out
    # A row with neither an id nor an identifier is shown without a link.
    assert "Link: \n" in out or "Link: " in out


def test_smartrecruiters_narrows_by_keyword(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(SMARTRECRUITERS_JOBS))
    out = call_tool(smartrecruiters, "search_smartrecruiters_jobs", fake, monkeypatch,
                    company="servicenow", keywords="data scientist")
    assert "Data Scientist, Growth" in out
    assert "Machine Learning Engineer" not in out


def test_smartrecruiters_lists_supported_companies_for_an_unknown_one(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(SMARTRECRUITERS_JOBS))
    out = call_tool(smartrecruiters, "search_smartrecruiters_jobs", fake, monkeypatch,
                    company="acme")
    assert "Unknown company 'acme'" in out
    assert "servicenow" in out
    assert fake.calls == []  # no pointless request


def test_smartrecruiters_distinguishes_down_from_empty(monkeypatch) -> None:
    down = call_tool(smartrecruiters, "search_smartrecruiters_jobs",
                     FakeRequests(error=OSError("503")), monkeypatch, company="servicenow")
    empty = call_tool(smartrecruiters, "search_smartrecruiters_jobs",
                      FakeRequests(FakeResponse({"content": []})), monkeypatch,
                      company="servicenow")

    assert "Couldn't reach ServiceNow's careers board" in down
    assert "No relevant ServiceNow roles found" in empty


@pytest.mark.parametrize("value", [None, "", "not-a-timestamp", 17_000_000_000])
def test_smartrecruiters_tolerates_an_unparseable_released_date(value: object) -> None:
    assert smartrecruiters._parse_released(value) is None


# --- Workday --------------------------------------------------------------

WORKDAY_JOBS = {
    "jobPostings": [
        {"title": "Machine Learning Engineer",
         "externalPath": "/job/US-CA-Santa-Clara/MLE_JR1",
         "locationsText": "US, CA, Santa Clara", "postedOn": "Posted 6 Days Ago",
         "bulletFields": ["JR1"]},
        {"title": "Office Manager", "externalPath": "/job/x/OM_JR2",
         "locationsText": "2 Locations", "postedOn": "Posted Today",
         "bulletFields": ["JR2"]},
        # No requisition id: de-duping falls back to the URL.
        {"title": "Applied Scientist", "externalPath": "/job/US-CA/AS_JR3",
         "locationsText": "2 Locations", "postedOn": "", "bulletFields": []},
    ]
}


def test_workday_filters_by_relevance_and_reports_no_date(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(WORKDAY_JOBS))
    out = call_tool(workday, "search_workday_jobs", fake, monkeypatch,
                    company="nvidia", keywords="machine learning")

    assert "Latest NVIDIA AI/ML roles" in out
    assert "Machine Learning Engineer" in out
    assert "Applied Scientist" in out
    assert "Office Manager" not in out  # not AI/ML
    # Workday's own wording is repeated, never turned into a date.
    assert "Posted: Posted 6 Days Ago" in out
    assert "Posted: not listed" in out  # the row with no postedOn
    assert "publishes how long ago a role went up, not a date" in out


def test_workday_asks_for_us_roles_with_the_tenants_own_facet(monkeypatch) -> None:
    """The facet name differs per tenant; a wrong one silently widens the search."""
    fake = FakeRequests(FakeResponse(WORKDAY_JOBS))
    call_tool(workday, "search_workday_jobs", fake, monkeypatch,
              company="salesforce", keywords="machine learning")

    body = fake.calls[0]["json"]
    facet = workday.TENANTS["salesforce"].country_facet
    assert body["appliedFacets"] == {facet: ["bc33aa3152ec42d4995f4791a106ed09"]}
    assert body["searchText"] == "machine learning"
    assert body["limit"] == workday.API_PAGE_SIZE
    assert "salesforce.wd12.myworkdayjobs.com/wday/cxs" in fake.calls[0]["url"]


def test_workday_builds_the_job_url_from_the_site_not_the_api(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(WORKDAY_JOBS))
    out = call_tool(workday, "search_workday_jobs", fake, monkeypatch,
                    company="adobe", keywords="machine learning")
    assert ("https://adobe.wd5.myworkdayjobs.com/external_experienced"
            "/job/US-CA-Santa-Clara/MLE_JR1") in out


def test_workday_searches_the_profile_and_de_dupes_the_results(monkeypatch) -> None:
    """With no keywords every profile query runs, and they overlap heavily."""
    fake = FakeRequests(FakeResponse(WORKDAY_JOBS))
    out = call_tool(workday, "search_workday_jobs", fake, monkeypatch, company="nvidia")

    assert len(fake.calls) == 5              # one per profile query
    assert out.count("*Machine Learning Engineer*") == 1  # merged, not repeated
    assert "2 found" in out


def test_workday_lists_supported_companies_for_an_unknown_one(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(WORKDAY_JOBS))
    out = call_tool(workday, "search_workday_jobs", fake, monkeypatch, company="acme")
    assert "Unknown company 'acme'" in out
    assert "nvidia" in out
    assert fake.calls == []  # no pointless request


def test_workday_distinguishes_down_from_empty(monkeypatch) -> None:
    down = call_tool(workday, "search_workday_jobs", FakeRequests(error=OSError("503")),
                     monkeypatch, company="nvidia", keywords="machine learning")
    empty = call_tool(workday, "search_workday_jobs",
                      FakeRequests(FakeResponse({"jobPostings": []})), monkeypatch,
                      company="nvidia", keywords="machine learning")

    assert "Couldn't reach NVIDIA's careers site" in down
    assert "No relevant NVIDIA roles found" in empty


@pytest.mark.parametrize(("row", "expected"), [
    ({"bulletFields": ["JR1"]}, "JR1"),
    ({"bulletFields": []}, "https://site/job/1"),      # no id: the URL identifies it
    ({"bulletFields": "JR1"}, "https://site/job/1"),   # not a list
    ({"bulletFields": [7]}, "https://site/job/1"),     # not a string
    ({}, "https://site/job/1"),
])
def test_workday_identifies_a_row_by_requisition_then_url(row: dict, expected: str) -> None:
    """De-duping must never collapse two rows it cannot tell apart."""
    posting = JobPosting(title="MLE", organization="NVIDIA", url="https://site/job/1")
    assert workday._req_id(row, posting) == expected


def test_workday_falls_back_to_the_title_when_there_is_no_url() -> None:
    posting = JobPosting(title="MLE", organization="NVIDIA", url="")
    assert workday._req_id({}, posting) == "MLE"
