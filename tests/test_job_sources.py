"""Per-source job tools: request handling, filtering, and formatting.

Every source is exercised through its registered tool with the network stubbed,
because that is the layer the model actually calls — including the "source is
down" path, which has to return readable text rather than raise. The two hosted
board platforms share an implementation and are tested in
``test_hosted_boards.py``; what they owe a caller searching several sources at
once is pinned at the bottom of this file, for every one of them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

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
    workday,
)
from scout.tools.jobs.posting import JobPosting

from .conftest import FakeRequests, FakeResponse, call_tool, stub_requests


def days_ago(days: int) -> str:
    """A date in Amazon's ``posted_date`` format."""
    return (datetime.now() - timedelta(days=days)).strftime("%B %d, %Y")


def apple_state_page(state: object) -> str:
    """A page carrying ``state`` as its hydration blob — a JSON string in a JS literal."""
    literal = json.dumps(json.dumps(state))[1:-1]
    return ('<script>window.__staticRouterHydrationData = JSON.parse('
            f'"{literal}");</script>')


def apple_page(results: list[dict]) -> str:
    """A search page the way Apple renders one."""
    return apple_state_page({"loaderData": {"search": {"searchResults": results}}})


# --- Amazon ---------------------------------------------------------------

AMAZON_ROWS = {
    "jobs": [
        {"id": "1", "title": "Machine Learning Engineer", "job_path": "/en/jobs/1",
         "posted_date": days_ago(0), "location": "USA, WA, Seattle"},
        {"id": "2", "title": "Supply Chain Manager", "job_path": "/en/jobs/2",
         "posted_date": days_ago(0)},
        {"id": "3", "title": "ML Engineer Intern", "job_path": "/en/jobs/3",
         "posted_date": days_ago(0), "is_intern": True},
        {"id": "4", "title": "Applied Scientist", "job_path": "/en/jobs/4",
         "posted_date": days_ago(20)},
    ]
}


def test_amazon_filters_and_formats(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(AMAZON_ROWS))
    out = call_tool(amazon, "search_amazon_jobs", fake, monkeypatch)

    # Only the relevant, recent, non-intern role survives — and it is not
    # duplicated even though every profile query returned the same rows.
    assert "1 found" in out
    assert "*Machine Learning Engineer*" in out
    assert "https://www.amazon.jobs/en/jobs/1" in out
    assert "Location: USA, WA, Seattle" in out
    assert "Supply Chain Manager" not in out
    assert "Intern" not in out
    assert "Applied Scientist" not in out  # outside the default 24h window


def test_amazon_widens_the_window_on_request(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(AMAZON_ROWS))
    out = call_tool(amazon, "search_amazon_jobs", fake, monkeypatch, days=30)
    assert "last 30 days" in out
    assert "Applied Scientist" in out


def test_amazon_runs_every_profile_query_by_default(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse({"jobs": []}))
    call_tool(amazon, "search_amazon_jobs", fake, monkeypatch)
    assert len(fake.calls) == len(amazon.search_queries(""))


def test_amazon_uses_explicit_keywords_only(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse({"jobs": []}))
    call_tool(amazon, "search_amazon_jobs", fake, monkeypatch, keywords="applied scientist")
    assert len(fake.calls) == 1
    assert fake.calls[0]["params"]["base_query"] == "applied scientist"


def test_amazon_reports_an_empty_window(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse({"jobs": []}))
    out = call_tool(amazon, "search_amazon_jobs", fake, monkeypatch)
    assert "No relevant Amazon roles found in the last 24h" in out


def test_amazon_survives_an_unreachable_api(monkeypatch) -> None:
    """A dead source must answer in words, not raise into the tool loop."""
    fake = FakeRequests(error=OSError("connection reset"))
    out = call_tool(amazon, "search_amazon_jobs", fake, monkeypatch)
    assert "No relevant Amazon roles" in out


@pytest.mark.parametrize(("raw", "expected"), [
    ("June 03, 2026", datetime(2026, 6, 3)),
    ("  June   03,  2026 ", datetime(2026, 6, 3)),  # odd spacing from the API
    ("2026-06-03", None),
    ("", None),
    (None, None),
])
def test_amazon_date_parsing(raw: str | None, expected: datetime | None) -> None:
    assert amazon._parse_posted_date(raw) == expected


# --- Google ---------------------------------------------------------------

GOOGLE_HTML = """
<a href="jobs/results/12345-machine-learning-engineer?q=x"
   aria-label="Learn more about Machine Learning Engineer, Search">Learn more</a>
<a href="jobs/results/67890-account-manager"
   aria-label="Learn more about Account Manager, Ads">Learn more</a>
"""


def test_google_parses_listings_and_notes_the_missing_dates(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(text=GOOGLE_HTML))
    out = call_tool(google, "search_google_jobs", fake, monkeypatch)

    assert "*Machine Learning Engineer, Search*" in out
    assert "jobs/results/12345-machine-learning-engineer" in out
    assert "Account Manager" not in out
    assert "Posted: not published by Google" in out
    assert out.rstrip().endswith("_Google doesn't publish posting dates; "
                                 "roles are listed newest-first._")


def test_google_respects_the_limit(monkeypatch) -> None:
    listings = "\n".join(
        f'<a href="jobs/results/{i}-ml-engineer" '
        f'aria-label="Learn more about ML Engineer {i}">x</a>'
        for i in range(20)
    )
    fake = FakeRequests(FakeResponse(text=listings))
    out = call_tool(google, "search_google_jobs", fake, monkeypatch, limit=3)
    assert "3 found" in out


def test_google_survives_an_unreachable_site(monkeypatch) -> None:
    fake = FakeRequests(error=OSError("timeout"))
    out = call_tool(google, "search_google_jobs", fake, monkeypatch)
    assert "No relevant Google roles found right now" in out


# --- Netflix --------------------------------------------------------------


def test_netflix_parses_positions_and_sorts_newest_first(monkeypatch) -> None:
    newer = datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp()
    older = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()
    fake = FakeRequests(FakeResponse({"positions": [
        {"id": 1, "name": "Applied Scientist", "location": "Los Gatos, CA",
         "t_create": older, "canonicalPositionUrl": "https://netflix/1"},
        {"id": 2, "name": "Machine Learning Engineer", "t_create": newer},
        {"id": 3, "name": "Payroll Specialist", "t_create": newer},
        {"id": 4, "name": "ML Engineer, Studio"},  # no t_create at all
    ]}))
    out = call_tool(netflix, "search_netflix_jobs", fake, monkeypatch)

    assert out.index("Machine Learning Engineer") < out.index("Applied Scientist")
    assert "Payroll Specialist" not in out
    assert "Posted: Aug 20, 2026" in out
    assert "Posted: not listed" in out  # the undated role still shows up
    assert "https://netflix/1" in out
    # A position without a canonical URL falls back to the job-id URL.
    assert f"{netflix.JOB_BASE_URL}2" in out


def test_netflix_survives_an_unreachable_api(monkeypatch) -> None:
    fake = FakeRequests(error=OSError("dns failure"))
    out = call_tool(netflix, "search_netflix_jobs", fake, monkeypatch)
    assert "No relevant Netflix roles found right now" in out


@pytest.mark.parametrize("value", [None, "", "not-a-number", 10**20])
def test_netflix_tolerates_bad_timestamps(value: object) -> None:
    assert netflix._parse_created(value) is None


# --- Boston University ----------------------------------------------------

BU_TOOL = "search_boston_university_jobs"

BU_RSS = """<rss><channel>
<item>
  <title><![CDATA[Research Scientist, Machine Learning]]></title>
  <link>https://bu/1</link>
  <location>Boston, MA</location>
  <postingDate>08/20/2026</postingDate>
</item>
<item>
  <title>Data Scientist &amp; Analyst</title>
  <link>https://bu/2</link>
  <location>Boston, MA</location>
  <postingDate>08/22/2026</postingDate>
</item>
<item>
  <title>Groundskeeper</title>
  <link>https://bu/3</link>
  <postingDate>08/23/2026</postingDate>
</item>
<item>
  <title>ML Engineer</title>
  <link>https://bu/4</link>
  <postingDate>not-a-date</postingDate>
</item>
</channel></rss>"""


def test_boston_university_parses_the_feed_newest_first(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(text=BU_RSS))
    out = call_tool(boston_university, BU_TOOL, fake, monkeypatch)

    # CDATA unwrapped, entities decoded, irrelevant roles dropped.
    assert "Research Scientist, Machine Learning" in out
    assert "Data Scientist & Analyst" in out
    assert "Groundskeeper" not in out
    assert out.index("Data Scientist") < out.index("Research Scientist")  # newer first
    assert "Location: Boston, MA" in out
    # An unparseable date is shown as the source wrote it, not silently dropped.
    assert "Posted: not-a-date" in out


def test_boston_university_keyword_search_overrides_the_profile_filter(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(text=BU_RSS))
    out = call_tool(boston_university, BU_TOOL, fake, monkeypatch, keywords="groundskeeper")
    assert "Groundskeeper" in out
    assert "Research Scientist" not in out


def test_boston_university_distinguishes_down_from_empty(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(text="<rss><channel></channel></rss>"))
    out = call_tool(boston_university, BU_TOOL, fake, monkeypatch)
    assert f"No relevant {boston_university.ORGANIZATION} roles found" in out


def test_boston_university_survives_an_unreachable_feed(monkeypatch) -> None:
    fake = FakeRequests(error=OSError("refused"))
    out = call_tool(boston_university, BU_TOOL, fake, monkeypatch)
    assert "Couldn't reach Boston University's careers feed" in out


# --- Northeastern ---------------------------------------------------------


def test_northeastern_formats_workdays_relative_dates(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse({"jobPostings": [
        {"title": "Research Scientist", "locationsText": "Boston, MA",
         "externalPath": "/job/Boston/Research-Scientist_R123", "postedOn": "Posted 5 Days Ago"},
        {"title": "Data Engineer", "externalPath": "", "postedOn": ""},
    ]}))
    out = call_tool(northeastern, "search_northeastern_jobs", fake, monkeypatch)

    assert "Latest Northeastern University roles — 2 found" in out
    assert "Posted: Posted 5 Days Ago" in out
    assert "Posted: not listed" in out
    assert f"https://{northeastern.HOST}/Careers/job/Boston" in out


def test_northeastern_defaults_the_search_text(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse({"jobPostings": []}))
    call_tool(northeastern, "search_northeastern_jobs", fake, monkeypatch)
    assert fake.calls[0]["json"]["searchText"] == northeastern.DEFAULT_SEARCH


def test_northeastern_caps_the_workday_page_size(monkeypatch) -> None:
    """Workday rejects a page size above 20, so a bigger limit must be clamped."""
    fake = FakeRequests(FakeResponse({"jobPostings": []}))
    call_tool(northeastern, "search_northeastern_jobs", fake, monkeypatch, limit=25)
    assert fake.calls[0]["json"]["limit"] == northeastern.API_PAGE_SIZE


def test_northeastern_survives_an_unreachable_site(monkeypatch) -> None:
    fake = FakeRequests(error=OSError("refused"))
    out = call_tool(northeastern, "search_northeastern_jobs", fake, monkeypatch)
    assert "Couldn't reach Northeastern's careers site" in out


# --- Lenovo ---------------------------------------------------------------

LENOVO_FEED = """<rss><channel>
<item>
  <title><![CDATA[Advisory AI Software Engineer]]></title>
  <link>https://jobs.lenovo.com/careers/JobDetail/Advisory-AI-Software-Engineer/70001</link>
  <pubDate>Fri, 06 Mar 2026 00:00:00 +0000</pubDate>
</item>
<item>
  <title><![CDATA[Senior Machine Learning Engineer]]></title>
  <link>https://jobs.lenovo.com/careers/JobDetail/Senior-ML-Engineer/70002</link>
  <pubDate>Wed, 01 Jul 2026 00:00:00 +0000</pubDate>
</item>
<item>
  <title>ISG Field Specialist, Southeast</title>
  <link>https://jobs.lenovo.com/careers/JobDetail/ISG-Field-Specialist/70003</link>
  <pubDate>Tue, 15 Sep 2026 00:00:00 +0000</pubDate>
</item>
<item>
  <title>AI Prototyping Engineer</title>
  <link>https://jobs.lenovo.com/careers/JobDetail/AI-Prototyping-Engineer/70004</link>
  <pubDate>not-a-date</pubDate>
</item>
</channel></rss>"""


def test_lenovo_parses_the_feed_newest_first(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(text=LENOVO_FEED))
    out = call_tool(lenovo, "search_lenovo_jobs", fake, monkeypatch)

    assert "Senior Machine Learning Engineer" in out
    assert "Advisory AI Software Engineer" in out
    assert out.index("Machine Learning") < out.index("Advisory")  # newer first
    assert "ISG Field Specialist" not in out  # a sales role the search dragged in
    assert "Posted: Jul 01, 2026" in out
    # An unparseable date is shown as the feed wrote it, not silently dropped.
    assert "Posted: not-a-date" in out


def test_lenovo_searches_the_us_only(monkeypatch) -> None:
    """The feed has no location, so the country facet is what keeps it US-only."""
    fake = FakeRequests(FakeResponse(text=LENOVO_FEED))
    call_tool(lenovo, "search_lenovo_jobs", fake, monkeypatch, keywords="applied scientist")

    params = fake.calls[0]["params"]
    assert params["13036"] == "[12016802]"
    assert params["search"] == "applied scientist"


def test_lenovo_runs_every_profile_query_and_de_dupes(monkeypatch) -> None:
    """Every query returns the same feed; a role must appear once."""
    fake = FakeRequests(FakeResponse(text=LENOVO_FEED))
    out = call_tool(lenovo, "search_lenovo_jobs", fake, monkeypatch)

    assert len(fake.calls) == len(lenovo.search_queries(""))
    assert out.count("Senior Machine Learning Engineer") == 1
    assert "3 found" in out


def test_lenovo_survives_an_unreachable_feed(monkeypatch) -> None:
    fake = FakeRequests(error=OSError("timeout"))
    out = call_tool(lenovo, "search_lenovo_jobs", fake, monkeypatch)
    assert "No relevant Lenovo roles found right now" in out


# --- What every source owes a caller searching several at once ------------

#: Each source and an argument list for its ``search``, with an empty response
#: in the shape that source returns.
SOURCES = [
    (amazon, (), FakeResponse({"jobs": []})),
    (google, (), FakeResponse(text="")),
    (netflix, (), FakeResponse({"positions": []})),
    (lenovo, (), FakeResponse(text="<rss><channel></channel></rss>")),
    (greenhouse, ("stripe",), FakeResponse({"jobs": []})),
    (ashby, ("whoop",), FakeResponse({"jobs": []})),
    (smartrecruiters, ("servicenow",), FakeResponse({"content": []})),
    (workday, ("nvidia",), FakeResponse({"jobPostings": []})),
    (northeastern, (), FakeResponse({"jobPostings": []})),
    (boston_university, (), FakeResponse(text="<rss></rss>")),
    (microsoft, (), FakeResponse({"data": {"positions": []}})),
    (apple, (), FakeResponse(text=apple_page([]))),
    (oracle, (), FakeResponse({"items": [{"requisitionList": []}]})),
    (uber, (), FakeResponse({"jobs": []})),
    (cisco, (), FakeResponse({"refineSearch": {"data": {"jobs": []}}})),
    (bloomberg, (), FakeResponse(text="<html></html>")),
]
SOURCE_IDS = [module.__name__.rsplit(".", 1)[-1] for module, _, _ in SOURCES]


@pytest.mark.parametrize(("module", "args", "_response"), SOURCES, ids=SOURCE_IDS)
def test_a_source_that_cannot_be_reached_returns_none(
    module, args: tuple, _response: FakeResponse, monkeypatch
) -> None:
    """The referral search has to tell "the board is down" from "nothing is
    open" — per source, that distinction is None rather than an empty list."""
    stub_requests(monkeypatch, module, FakeRequests(error=OSError("down")))
    assert module.search(*args) is None


@pytest.mark.parametrize(("module", "args", "response"), SOURCES, ids=SOURCE_IDS)
def test_a_source_with_nothing_open_returns_an_empty_list(
    module, args: tuple, response: FakeResponse, monkeypatch
) -> None:
    stub_requests(monkeypatch, module, FakeRequests(response))
    assert module.search(*args) == []


class FlakyRequests(FakeRequests):
    """Succeeds, then fails — a source that rate-limits partway through paging."""

    def __init__(self, response: FakeResponse, ok_calls: int) -> None:
        super().__init__(response)
        self.ok_calls = ok_calls

    def _handle(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if len(self.calls) > self.ok_calls:
            raise OSError("429 Please try again later")
        return self.response

    get = _handle
    post = _handle


# --- Microsoft ------------------------------------------------------------

MICROSOFT_BODY = {"data": {"positions": [
    {"displayJobId": "200055309", "name": "Principal Machine Learning Scientist",
     "positionUrl": "/careers/job/1", "standardizedLocations": ["Redmond, WA, US"],
     "postedTs": 1789415386},
    {"displayJobId": "200055310", "name": "Office Manager",
     "positionUrl": "/careers/job/2", "standardizedLocations": ["Redmond, WA, US"],
     "postedTs": 1789415000},
    # No URL, a location list that isn't one, and a timestamp that isn't a number.
    {"displayJobId": "200055311", "name": "Applied Scientist",
     "positionUrl": "", "standardizedLocations": "Redmond", "postedTs": "soon"},
]}}


def test_microsoft_filters_by_relevance_and_reads_an_epoch_date(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(MICROSOFT_BODY))
    out = call_tool(microsoft, "search_microsoft_jobs", fake, monkeypatch)

    assert "Latest Microsoft AI/ML roles" in out
    assert "Principal Machine Learning Scientist" in out
    assert "Applied Scientist" in out
    assert "Office Manager" not in out           # not AI/ML
    assert "Posted: Sep 14, 2026" in out         # 1789415386, read as UTC
    assert "Posted: not listed" in out           # postedTs was not a number
    assert "https://apply.careers.microsoft.com/careers/job/1" in out
    assert "Location: Redmond, WA, US" in out
    # The US filter and the default query are sent, not guessed at.
    assert fake.calls[0]["params"]["location"] == "United States"
    assert fake.calls[0]["params"]["query"] == microsoft.DEFAULT_SEARCH


def test_microsoft_reads_a_fixed_number_of_pages_and_de_dupes(monkeypatch) -> None:
    """The page size is fixed at ten and the endpoint rate-limits, so paging is bounded."""
    fake = FakeRequests(FakeResponse(MICROSOFT_BODY))
    out = call_tool(microsoft, "search_microsoft_jobs", fake, monkeypatch)

    assert len(fake.calls) == microsoft.PAGES
    assert [call["params"]["start"] for call in fake.calls] == [0, 10]
    assert out.count("*Principal Machine Learning Scientist*") == 1
    assert "2 found" in out


def test_microsoft_keeps_the_page_it_got_when_the_next_is_rate_limited(monkeypatch) -> None:
    """A 429 on page two must not throw away page one."""
    fake = FlakyRequests(FakeResponse(MICROSOFT_BODY), ok_calls=1)
    out = call_tool(microsoft, "search_microsoft_jobs", fake, monkeypatch)
    assert "Principal Machine Learning Scientist" in out


def test_microsoft_distinguishes_down_from_empty(monkeypatch) -> None:
    down = call_tool(microsoft, "search_microsoft_jobs", FakeRequests(error=OSError("429")),
                     monkeypatch)
    empty = call_tool(microsoft, "search_microsoft_jobs",
                      FakeRequests(FakeResponse({"data": {"positions": []}})), monkeypatch)

    assert "Couldn't reach Microsoft's careers site" in down
    assert "No relevant Microsoft roles found" in empty


@pytest.mark.parametrize("body", [
    [],                       # not an object at all
    {"positions": []},        # rows not under `data`
    {"data": []},             # `data` is not an object
])
def test_microsoft_treats_a_body_it_cannot_navigate_as_unreachable(body, monkeypatch) -> None:
    """Answering with something that isn't the API is a failure to look."""
    out = call_tool(microsoft, "search_microsoft_jobs", FakeRequests(FakeResponse(body)),
                    monkeypatch)
    assert "Couldn't reach Microsoft's careers site" in out


@pytest.mark.parametrize(("row", "expected"), [
    ({"displayJobId": "200055309", "id": 7}, "200055309"),
    ({"id": 7}, "7"),
    ({}, "https://job/1"),
])
def test_microsoft_identifies_a_row_by_requisition_then_id_then_url(row, expected) -> None:
    posting = JobPosting(title="MLE", organization="Microsoft", url="https://job/1")
    assert microsoft._job_id(row, posting) == expected


def test_microsoft_identifies_a_row_by_title_as_a_last_resort() -> None:
    posting = JobPosting(title="MLE", organization="Microsoft", url="")
    assert microsoft._job_id({}, posting) == "MLE"


@pytest.mark.parametrize("value", [None, "soon", True, 10**20])
def test_microsoft_tolerates_an_unusable_timestamp(value: object) -> None:
    """``True`` is an int in Python, and a huge one overflows the clock."""
    assert microsoft._parse_posted(value) is None


# --- Apple ----------------------------------------------------------------

APPLE_RESULTS = [
    {"postingTitle": "Machine Learning Engineer", "positionId": "200684201",
     "transformedPostingTitle": "machine-learning-engineer",
     "postDateInGMT": "2026-09-16T22:06:11.615Z",
     "locations": ["junk", {"name": ""}, {"name": "Cupertino"}, {"name": "Seattle"},
                   {"name": "Cupertino"}]},
    {"postingTitle": "Specialist: Seasonal", "positionId": "200684202",
     "transformedPostingTitle": "specialist", "postDateInGMT": "2026-09-15T10:00:00.000Z",
     "locations": [{"name": "Austin"}]},
    # No slug, locations that aren't a list, and a timestamp that will not parse.
    {"postingTitle": "Applied Scientist", "positionId": "200684203",
     "transformedPostingTitle": "", "postDateInGMT": "nonsense", "locations": "Cupertino"},
]


def test_apple_reads_the_roles_out_of_the_pages_own_state(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(text=apple_page(APPLE_RESULTS)))
    out = call_tool(apple, "search_apple_jobs", fake, monkeypatch)

    assert "Latest Apple AI/ML roles" in out
    assert "Machine Learning Engineer" in out
    assert "Applied Scientist" in out
    assert "Specialist: Seasonal" not in out   # not AI/ML
    assert "Posted: Sep 16, 2026" in out
    assert "Posted: not listed" in out         # unparseable timestamp
    assert "https://jobs.apple.com/en-us/details/200684201/machine-learning-engineer" in out
    # Repeated cities are collapsed; a non-list locations field is simply empty.
    assert "Location: Cupertino; Seattle" in out


def test_apple_always_sends_the_relevance_sort(monkeypatch) -> None:
    """Apple drops the sort when paging, and without it the results are retail roles."""
    fake = FakeRequests(FakeResponse(text=apple_page(APPLE_RESULTS)))
    call_tool(apple, "search_apple_jobs", fake, monkeypatch)

    assert len(fake.calls) == apple.PAGES
    assert [call["params"]["sort"] for call in fake.calls] == ["relevance"] * apple.PAGES
    assert [call["params"]["page"] for call in fake.calls] == [1, 2]
    assert fake.calls[0]["params"]["location"] == apple.US_LOCATION


def test_apple_de_dupes_a_role_listed_once_per_city(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(text=apple_page(APPLE_RESULTS)))
    out = call_tool(apple, "search_apple_jobs", fake, monkeypatch)
    assert out.count("*Machine Learning Engineer*") == 1
    assert "2 found" in out


def test_apple_distinguishes_down_from_empty(monkeypatch) -> None:
    down = call_tool(apple, "search_apple_jobs", FakeRequests(error=OSError("503")),
                     monkeypatch)
    empty = call_tool(apple, "search_apple_jobs",
                      FakeRequests(FakeResponse(text=apple_page([]))), monkeypatch)

    assert "Couldn't reach Apple's careers site" in down
    assert "No relevant Apple roles found" in empty


@pytest.mark.parametrize(("page", "why"), [
    ("<html>no state here</html>", "blob missing"),
    ('<script>window.__staticRouterHydrationData = JSON.parse("{bad json}");</script>',
     "blob will not decode"),
    (apple_state_page([]), "blob is not an object"),
    (apple_state_page({}), "no loaderData"),
    (apple_state_page({"loaderData": {"search": []}}), "search is not an object"),
])
def test_apple_treats_a_page_without_its_state_as_unreachable(page: str, why: str,
                                                              monkeypatch) -> None:
    """The API is retired and answers empty; a scrape that fails must not look empty too."""
    out = call_tool(apple, "search_apple_jobs", FakeRequests(FakeResponse(text=page)),
                    monkeypatch)
    assert "Couldn't reach Apple's careers site" in out, why


@pytest.mark.parametrize("value", [None, "", "nonsense", 1789415386])
def test_apple_tolerates_an_unparseable_posted_date(value: object) -> None:
    assert apple._parse_posted(value) is None


# --- Oracle ---------------------------------------------------------------

ORACLE_BODY = {"items": [{"requisitionList": [
    {"Id": "344271", "Title": "Principal Applied Scientist",
     "PostedDate": "2026-09-14", "PrimaryLocation": "United States"},
    {"Id": "344272", "Title": "Global Engineering Operations Analyst",
     "PostedDate": "2026-09-18", "PrimaryLocation": "Nashville, TN, United States"},
    # No id, and a date that will not parse.
    {"Title": "Senior Machine Learning Engineer", "PostedDate": "not-a-date",
     "PrimaryLocation": "United States"},
    "not even a row",
]}]}


def test_oracle_filters_by_relevance_and_sorts_newest_first(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(ORACLE_BODY))
    out = call_tool(oracle, "search_oracle_jobs", fake, monkeypatch)

    assert "Latest Oracle AI/ML roles" in out
    assert "Principal Applied Scientist" in out
    assert "Senior Machine Learning Engineer" in out
    assert "Global Engineering Operations Analyst" not in out  # keyword hit, not AI/ML
    assert "Posted: Sep 14, 2026" in out
    assert "Posted: not listed" in out
    assert "https://careers.oracle.com/en/sites/jobsearch/job/344271" in out


def test_oracle_keeps_the_finder_syntax_intact(monkeypatch) -> None:
    """The ``;`` and ``,`` are structure; re-encoding them drops the filters."""
    fake = FakeRequests(FakeResponse(ORACLE_BODY))
    call_tool(oracle, "search_oracle_jobs", fake, monkeypatch, keywords="machine learning")

    url = fake.calls[0]["url"]
    assert fake.calls[0]["params"] is None      # the URL is passed whole
    assert "finder=findReqs;siteNumber=CX_45001," in url
    assert "keyword=machine%20learning" in url
    assert f"selectedLocationsFacet={oracle.US_FACET}" in url
    assert "sortBy=POSTING_DATES_DESC" in url
    assert "expand=requisitionList" in url      # without this there are no rows at all


def test_oracle_distinguishes_down_from_empty(monkeypatch) -> None:
    down = call_tool(oracle, "search_oracle_jobs", FakeRequests(error=OSError("503")),
                     monkeypatch)
    empty = call_tool(oracle, "search_oracle_jobs",
                      FakeRequests(FakeResponse({"items": [{"requisitionList": []}]})),
                      monkeypatch)

    assert "Couldn't reach Oracle's careers site" in down
    assert "No relevant Oracle roles found" in empty


@pytest.mark.parametrize("body", [
    [],                          # not an object
    {"items": []},               # the search block is missing
    {"items": [{}]},             # `expand` was dropped: 200, every count intact, no rows
])
def test_oracle_treats_missing_rows_as_unreachable(body, monkeypatch) -> None:
    """A dropped ``expand`` answers 200 with no postings — the silently-short list."""
    out = call_tool(oracle, "search_oracle_jobs", FakeRequests(FakeResponse(body)),
                    monkeypatch)
    assert "Couldn't reach Oracle's careers site" in out


@pytest.mark.parametrize("value", [None, "", "not-a-date", 20260914])
def test_oracle_tolerates_an_unparseable_posted_date(value: object) -> None:
    assert oracle._parse_posted(value) is None


# --- Uber -----------------------------------------------------------------

UBER_ROWS = {"jobs": [
    {"Title": "Sr Machine Learning Engineer", "DisplayDate": "2026-09-17T20:14:41Z",
     "Urls": [{"Url": "/en/jobs/302446/"}],
     "Locations": [{"City": "San Francisco", "Region": "California"},
                   {"City": "New York City", "Region": "New York"},
                   {"City": "San Francisco", "Region": "California"}]},
    {"Title": "Account Executive", "DisplayDate": "2026-09-18T00:00:00Z",
     "Urls": [{"Url": "/en/jobs/1/"}], "Locations": []},
    # A row with no usable URL, a locations field that isn't a list, and a bad date.
    {"Title": "Applied Scientist", "DisplayDate": "nonsense",
     "Urls": [{"Url": ""}], "Locations": "San Francisco"},
    # Urls not a list at all, and location entries that are junk or blank.
    {"Title": "AI Research Engineer", "DisplayDate": "2026-09-10T00:00:00Z",
     "Urls": "nope", "Locations": [{"City": "", "Region": ""}, "junk"]},
]}


def test_uber_filters_by_relevance_and_sorts_newest_first(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(UBER_ROWS))
    out = call_tool(uber, "search_uber_jobs", fake, monkeypatch)

    assert "Latest Uber AI/ML roles" in out
    assert "Sr Machine Learning Engineer" in out
    assert "Applied Scientist" in out
    assert "Account Executive" not in out  # not AI/ML
    assert "Posted: Sep 17, 2026" in out
    assert "Posted: not listed" in out     # unparseable DisplayDate
    assert "https://jobs.uber.com/en/jobs/302446/" in out
    # Repeated cities are collapsed; junk and blank entries drop out.
    assert "Location: San Francisco, California; New York City, New York" in out


def test_uber_asks_for_us_roles_by_display_name(monkeypatch) -> None:
    """``countries=USA`` answers 200 with nothing — it has to be the display name."""
    fake = FakeRequests(FakeResponse(UBER_ROWS))
    call_tool(uber, "search_uber_jobs", fake, monkeypatch)

    assert len(fake.calls) == 1  # the whole US board in one request
    assert fake.calls[0]["params"]["countries"] == "United States"
    assert fake.calls[0]["params"]["pagesize"] == uber.API_PAGE_SIZE


def test_uber_narrows_by_keyword(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(UBER_ROWS))
    out = call_tool(uber, "search_uber_jobs", fake, monkeypatch, keywords="research")
    assert "AI Research Engineer" in out
    assert "Sr Machine Learning Engineer" not in out


def test_uber_distinguishes_down_from_empty(monkeypatch) -> None:
    down = call_tool(uber, "search_uber_jobs", FakeRequests(error=OSError("403")),
                     monkeypatch)
    empty = call_tool(uber, "search_uber_jobs", FakeRequests(FakeResponse({"jobs": []})),
                      monkeypatch)

    assert "Couldn't reach Uber's careers site" in down
    assert "No relevant Uber roles found" in empty


@pytest.mark.parametrize("value", [None, "", "nonsense", 1789415386])
def test_uber_tolerates_an_unparseable_display_date(value: object) -> None:
    assert uber._parse_display_date(value) is None


# --- Cisco ----------------------------------------------------------------

CISCO_BODY = {"refineSearch": {"data": {"jobs": [
    {"title": "Senior Machine Learning Engineer", "jobSeqNo": "SEQ1",
     "postedDate": "2026-09-01T00:00:00.000+0000",
     "location": "San Jose, California, United States of America"},
    {"title": "Account Executive - Splunk", "jobSeqNo": "SEQ2",
     "postedDate": "2026-09-18T00:00:00.000+0000",
     "location": "Austin, Texas, United States of America"},
    # No sequence number to link with, and a date that will not parse.
    {"title": "Applied AI Scientist", "jobSeqNo": "", "postedDate": "nope",
     "location": "Milpitas, California, United States of America"},
]}}}


def test_cisco_filters_by_relevance_and_sorts_newest_first(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(CISCO_BODY))
    out = call_tool(cisco, "search_cisco_jobs", fake, monkeypatch)

    assert "Latest Cisco AI/ML roles" in out
    assert "Senior Machine Learning Engineer" in out
    assert "Applied AI Scientist" in out
    assert "Account Executive - Splunk" not in out  # fuzzy keyword hit, not AI/ML
    assert "Posted: Sep 01, 2026" in out
    assert "Posted: not listed" in out
    assert "https://careers.cisco.com/global/en/job/SEQ1" in out


def test_cisco_asks_for_us_roles_with_the_exact_country_string(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(CISCO_BODY))
    call_tool(cisco, "search_cisco_jobs", fake, monkeypatch, keywords="machine learning")

    body = fake.calls[0]["json"]
    assert body["selected_fields"] == {"country": ["United States of America"]}
    assert body["keywords"] == "machine learning"
    assert body["ddoKey"] == "refineSearch"


def test_cisco_distinguishes_down_from_empty(monkeypatch) -> None:
    down = call_tool(cisco, "search_cisco_jobs", FakeRequests(error=OSError("503")),
                     monkeypatch)
    empty = call_tool(cisco, "search_cisco_jobs",
                      FakeRequests(FakeResponse({"refineSearch": {"data": {"jobs": []}}})),
                      monkeypatch)

    assert "Couldn't reach Cisco's careers site" in down
    assert "No relevant Cisco roles found" in empty


@pytest.mark.parametrize("body", [
    [],                                 # not an object
    {"data": {"jobs": []}},             # not under the ddoKey
    {"refineSearch": []},               # the widget block is not an object
    {"refineSearch": {"data": []}},     # its data is not an object
])
def test_cisco_treats_a_body_it_cannot_navigate_as_unreachable(body, monkeypatch) -> None:
    out = call_tool(cisco, "search_cisco_jobs", FakeRequests(FakeResponse(body)), monkeypatch)
    assert "Couldn't reach Cisco's careers site" in out


@pytest.mark.parametrize("value", [None, "", "nope", 20260901])
def test_cisco_tolerates_an_unparseable_posted_date(value: object) -> None:
    assert cisco._parse_posted(value) is None


# --- Bloomberg ------------------------------------------------------------

def bloomberg_article(title: str, url: str, location: str | None) -> str:
    """One rendered search result, the way Avature returns it."""
    where = (f'<span class="list-item-location">{location}</span>'
             if location is not None else "")
    return ('<article class="article article--result" id="article--1">'
            '<div class="article__header"><div class="article__header__text">'
            '<h3 class="article__header__text__title title title--04">'
            f'<a class="link" href="{url}"> {title} </a></h3>'
            f'<div class="article__header__text__subtitle">{where}</div>'
            "</div></div></article>")


US_PLACE = "New York, New York, United States of America"
BLOOMBERG_PAGE = "".join([
    bloomberg_article("Senior Machine Learning Engineer", "https://bb/1", US_PLACE),
    bloomberg_article("Office Manager", "https://bb/2", US_PLACE),
    bloomberg_article("AI Research Engineer", "https://bb/3", "London, United Kingdom"),
    bloomberg_article("Data Scientist", "https://bb/4", None),  # no location rendered
    '<article class="article article--result">no title anchor here</article>',
])


def test_bloomberg_filters_by_relevance_and_location(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(text=BLOOMBERG_PAGE))
    out = call_tool(bloomberg, "search_bloomberg_jobs", fake, monkeypatch)

    assert "Latest Bloomberg AI/ML roles" in out
    assert "Senior Machine Learning Engineer" in out
    assert "Office Manager" not in out          # not AI/ML
    assert "AI Research Engineer" not in out    # United Kingdom
    assert "Data Scientist" not in out          # no location to confirm it is US
    assert f"Location: {US_PLACE}" in out
    assert "https://bb/1" in out


def test_bloomberg_says_it_has_no_dates_rather_than_inventing_one(monkeypatch) -> None:
    """The board publishes none, and the RSS feed that does is a different result set."""
    fake = FakeRequests(FakeResponse(text=BLOOMBERG_PAGE))
    out = call_tool(bloomberg, "search_bloomberg_jobs", fake, monkeypatch)

    assert f"Posted: {bloomberg.NO_DATE_LABEL}" in out
    assert "doesn't publish posting dates" in out


def test_bloomberg_pages_and_de_dupes(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse(text=BLOOMBERG_PAGE))
    out = call_tool(bloomberg, "search_bloomberg_jobs", fake, monkeypatch)

    assert len(fake.calls) == bloomberg.PAGES
    assert [call["params"]["jobOffset"] for call in fake.calls] == [0, 12, 24]
    assert out.count("*Senior Machine Learning Engineer*") == 1
    assert "1 found" in out


def test_bloomberg_keeps_the_page_it_got_when_a_later_one_fails(monkeypatch) -> None:
    fake = FlakyRequests(FakeResponse(text=BLOOMBERG_PAGE), ok_calls=1)
    out = call_tool(bloomberg, "search_bloomberg_jobs", fake, monkeypatch)
    assert "Senior Machine Learning Engineer" in out


def test_bloomberg_distinguishes_down_from_empty(monkeypatch) -> None:
    down = call_tool(bloomberg, "search_bloomberg_jobs", FakeRequests(error=OSError("503")),
                     monkeypatch)
    empty = call_tool(bloomberg, "search_bloomberg_jobs",
                      FakeRequests(FakeResponse(text="<html>no results</html>")), monkeypatch)

    assert "Couldn't reach Bloomberg's careers site" in down
    assert "No relevant Bloomberg roles found" in empty
