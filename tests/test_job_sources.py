"""Per-source job tools: request handling, filtering, and formatting.

Every source is exercised through its registered tool with the network stubbed,
because that is the layer the model actually calls — including the "source is
down" path, which has to return readable text rather than raise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scout.tools.jobs import (
    amazon,
    ashby,
    boston_university,
    google,
    greenhouse,
    lenovo,
    netflix,
    northeastern,
)

from .conftest import FakeRequests, FakeResponse, call_tool


def days_ago(days: int) -> str:
    """A date in Amazon's ``posted_date`` format."""
    return (datetime.now() - timedelta(days=days)).strftime("%B %d, %Y")


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


def test_ashby_distinguishes_down_from_empty(monkeypatch) -> None:
    down = call_tool(ashby, "search_ashby_jobs",
                     FakeRequests(error=OSError("503")), monkeypatch, company="whoop")
    empty = call_tool(ashby, "search_ashby_jobs",
                      FakeRequests(FakeResponse({"jobs": []})), monkeypatch, company="whoop")

    assert "Couldn't reach WHOOP's careers board" in down
    assert "No relevant WHOOP roles found" in empty


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
    (northeastern, (), FakeResponse({"jobPostings": []})),
    (boston_university, (), FakeResponse(text="<rss></rss>")),
]
SOURCE_IDS = [module.__name__.rsplit(".", 1)[-1] for module, _, _ in SOURCES]


@pytest.mark.parametrize(("module", "args", "_response"), SOURCES, ids=SOURCE_IDS)
def test_a_source_that_cannot_be_reached_returns_none(
    module, args: tuple, _response: FakeResponse, monkeypatch
) -> None:
    """The referral search has to tell "the board is down" from "nothing is
    open" — per source, that distinction is None rather than an empty list."""
    monkeypatch.setattr(module, "requests", FakeRequests(error=OSError("down")))
    assert module.search(*args) is None


@pytest.mark.parametrize(("module", "args", "response"), SOURCES, ids=SOURCE_IDS)
def test_a_source_with_nothing_open_returns_an_empty_list(
    module, args: tuple, response: FakeResponse, monkeypatch
) -> None:
    monkeypatch.setattr(module, "requests", FakeRequests(response))
    assert module.search(*args) == []
