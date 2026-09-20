"""Workday job search — one implementation for every tenant that hosts on it.

Workday is to the enterprise what Greenhouse is to the startup: NVIDIA,
Salesforce and Adobe all answer the same CXS endpoint, differing only in the
host, tenant and site their URL is built from. So this is one shape filled in
three times rather than three modules — the same call ``hosted_board`` makes for
Greenhouse and Ashby. It is a separate shape from ``HostedBoard`` because
nothing about the two lines up: Workday takes a POST body rather than a slug in
a path, and its URL needs three parts rather than one.

Two things about Workday drive the design here:

**It publishes no date.** ``postedOn`` is the site's own wording — "Posted 6
Days Ago" — so there is nothing to sort on, and the string goes into
``posted_label`` exactly as ``northeastern.py`` (also Workday) already does.
Undated postings sort last, which is correct: Scout cannot claim they're recent.

**Its keyword box is not a title filter.** ``searchText`` matches the whole
posting, so "machine learning" returns account executives whose description
mentions it. Every title is re-gated through ``relevance.is_ai_ml_role``.

Paging is deliberately absent. Workday hard-caps a page at 20 rows (21 is a
400), and NVIDIA's endpoint *wraps* an out-of-range offset back to page one
rather than returning nothing — so a "page until empty" loop never terminates.
Instead each profile query fetches one page, and ``merge_queries`` de-dupes the
union, which is the same thing ``amazon`` and ``google`` do.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from ..registry import ToolRegistry
from . import fetch
from .posting import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    QueryResults,
    Searcher,
    clamp_int,
    merge_queries,
    render_postings,
)
from .relevance import is_ai_ml_role, search_queries

#: Workday rejects a page larger than this with a 400.
API_PAGE_SIZE = 20


@dataclass(frozen=True)
class WorkdayTenant:
    """One company's Workday site, and how to ask it for US roles only."""

    organization: str
    host: str
    tenant: str
    site: str
    #: The facet that means "country". Genuinely per-tenant: NVIDIA calls it
    #: ``locationHierarchy1``, Adobe ``locationCountry``, and Salesforce uses a
    #: custom field with a 70-character name. There is no common spelling.
    country_facet: str
    #: That facet's id for the United States.
    us_facet_id: str

    @property
    def jobs_url(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"

    @property
    def site_url(self) -> str:
        return f"https://{self.host}/{self.site}"

    def search(
        self, keywords: str = "", limit: int = DEFAULT_LIMIT
    ) -> list[JobPosting] | None:
        """This tenant's US AI/ML openings, or None if Workday can't be reached.

        Not sorted: Workday publishes no date, so the order it returns — which
        is its own relevance ranking — is the only signal there is.
        """
        limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
        found = merge_queries(search_queries(keywords), partial(self._postings_for))
        return None if found is None else found[:limit]

    def searcher(self) -> Searcher:
        """This tenant's ``Searcher``. See ``jobs/directory.py``."""
        return self.search

    def _postings_for(self, query: str) -> QueryResults:
        """One page for one query, as (req id, posting) pairs, or None if down."""
        rows = fetch.post_rows(self.jobs_url, "jobPostings", self._body(query))
        if rows is None:
            return None
        return [
            (_req_id(row, posting), posting)
            for row in rows
            if (posting := self._to_posting(row)) is not None
        ]

    def _body(self, query: str) -> dict[str, object]:
        return {
            "appliedFacets": {self.country_facet: [self.us_facet_id]},
            "limit": API_PAGE_SIZE,
            "offset": 0,
            "searchText": query,
        }

    def _to_posting(self, job: dict) -> JobPosting | None:
        """Convert one Workday row, or None if it isn't an AI/ML role."""
        title = (job.get("title") or "").strip()
        if not is_ai_ml_role(title):
            return None
        path = job.get("externalPath", "")
        return JobPosting(
            title=title,
            organization=self.organization,
            url=f"{self.site_url}{path}" if path else "",
            # Often "3 Locations" rather than a place; the US filter is the
            # server-side facet above, so this is display only.
            location=(job.get("locationsText") or "").strip(),
            posted_label=(job.get("postedOn") or "").strip(),  # relative, not a date
        )

    def answer(self, keywords: str, limit: int) -> str:
        """The reply a tool returns: the roles, or which gap it hit."""
        postings = self.search(keywords, limit)
        if postings is None:
            return f"Couldn't reach {self.organization}'s careers site right now. Try again later."
        if not postings:
            return (f"No relevant {self.organization} roles found right now. "
                    "Try again later or adjust your keywords.")
        return render_postings(
            f"*Latest {self.organization} AI/ML roles — {{count}} found:*",
            postings,
            footer=f"_{self.organization} publishes how long ago a role went up, not a date._",
        )


def _req_id(job: dict, posting: JobPosting) -> str:
    """The requisition id, for de-duping one query's results against another's.

    ``bulletFields`` carries it ("JR2024968") on every tenant seen; the URL is
    the fallback, because a row Scout cannot identify must not silently collapse
    into another one.
    """
    fields = job.get("bulletFields")
    if isinstance(fields, list) and fields and isinstance(fields[0], str):
        return fields[0]
    return posting.url or posting.title


# Tenant key -> its site. Adding a company is a line here. All verified live.
TENANTS: dict[str, WorkdayTenant] = {
    "nvidia": WorkdayTenant(
        organization="NVIDIA",
        host="nvidia.wd5.myworkdayjobs.com",
        tenant="nvidia",
        site="NVIDIAExternalCareerSite",
        country_facet="locationHierarchy1",
        us_facet_id="2fcb99c455831013ea52fb338f2932d8",
    ),
    "salesforce": WorkdayTenant(
        organization="Salesforce",
        host="salesforce.wd12.myworkdayjobs.com",
        tenant="salesforce",
        site="External_Career_Site",
        country_facet=(
            "CF_-_REC_-_LRV_-_Job_Posting_Anchor_-_Country_from_Job_Posting_Location_Extended"
        ),
        us_facet_id="bc33aa3152ec42d4995f4791a106ed09",
    ),
    "adobe": WorkdayTenant(
        organization="Adobe",
        host="adobe.wd5.myworkdayjobs.com",
        tenant="adobe",
        site="external_experienced",
        country_facet="locationCountry",
        us_facet_id="bc33aa3152ec42d4995f4791a106ed09",
    ),
}


#: This source's ``Searcher``, taking the tenant key first, matching the other
#: multi-company platforms. See ``directory.py``.
def search(
    company: str, keywords: str = "", limit: int = DEFAULT_LIMIT
) -> list[JobPosting] | None:
    """One tenant's US AI/ML openings, or None if it is unknown or unreachable."""
    site = TENANTS.get((company or "").strip().lower())
    return None if site is None else site.search(keywords, limit)


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_workday_jobs(
        company: str, keywords: str = "", limit: int = DEFAULT_LIMIT
    ) -> str:
        """Search a big-tech company's Workday careers site for US AI/ML job
        openings and return each role's title, location, and link.

        These sites publish how long ago a role went up ("Posted 5 Days Ago")
        rather than a date, so results cannot be sorted by recency.

        Args:
            company: Which company to search. Supported: nvidia, salesforce, adobe.
            keywords: Optional search phrase. If empty, uses the user's profile
                (machine learning / applied scientist / AI engineer / etc.).
            limit: Maximum number of roles to return.
        """
        key = (company or "").strip().lower()
        site = TENANTS.get(key)
        if site is None:
            supported = ", ".join(sorted(TENANTS))
            return (f"Unknown company '{company}'. "
                    f"Supported Workday companies: {supported}.")
        return site.answer(keywords, limit)
