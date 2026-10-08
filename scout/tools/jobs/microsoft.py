"""Microsoft job search, via the Eightfold board apply.careers.microsoft.com serves.

Microsoft moved off its old careers API: ``jobs.careers.microsoft.com`` now
redirects here, and the endpoint it used to call fails TLS outright. What
answers today is Eightfold's "PCSX" search, which is a plain unauthenticated GET
with a server-side ``location`` filter and a real per-row timestamp.

The two constraints worth knowing are both about volume. A page is fixed at ten
rows — ``num`` is accepted and ignored — and the endpoint rate-limits after a
handful of rapid requests, answering 429 with a plain-text body rather than
JSON. So this reads a small, fixed number of pages rather than crawling: a 429
raises inside ``fetch`` and comes back as ``None``, which is read here as "that
page was unreachable" and leaves any page that did arrive intact.

``sort_by`` is accepted and ignored for anonymous callers, so recency is applied
here rather than asked for.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from langchain_core.runnables import RunnableConfig

from ..registry import ToolRegistry
from . import fetch
from .posting import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    QueryResults,
    clamp_int,
    merge_queries,
    parse_unix_timestamp,
    source_id,
    take_newest,
)
from .relevance import is_ai_ml_role
from .unsent import render_unsent

SEARCH_URL = "https://apply.careers.microsoft.com/api/pcsx/search"
JOB_BASE_URL = "https://apply.careers.microsoft.com"
ORGANIZATION = "Microsoft"

DOMAIN = "microsoft.com"
US = "United States"
#: Searched when the model passes no keywords.
DEFAULT_SEARCH = "machine learning"
#: Rows per page, fixed by the API; ``num`` is ignored.
API_PAGE_SIZE = 10
#: Kept small: the endpoint rate-limits.
PAGES = 2


def register(reg: ToolRegistry) -> None:
    @reg.tool
    # `config` is injected by LangChain and kept out of the schema: no Args: entry.
    def search_microsoft_jobs(  # noqa: D417
        keywords: str = "", limit: int = DEFAULT_LIMIT, *, config: RunnableConfig
    ) -> str:
        """Search Microsoft's careers site for recent US job openings relevant to
        the user's field (AI/ML engineering) and return title, date posted, and link.

        Args:
            keywords: Optional search phrase. If empty, defaults to AI/ML roles
                ("machine learning").
            limit: Maximum number of roles to return.
        """
        postings = search(keywords, limit)
        if postings is None:
            return f"Couldn't reach {ORGANIZATION}'s careers site right now. Try again later."
        if not postings:
            return (
                f"No relevant {ORGANIZATION} roles found right now. "
                "Try again later or adjust your keywords."
            )
        return render_unsent(
            f"*Latest {ORGANIZATION} AI/ML roles (most recent first) — {{count}} found:*",
            postings,
            config=config,
        )


def search(keywords: str = "", limit: int = DEFAULT_LIMIT) -> list[JobPosting] | None:
    """Microsoft's recent US AI/ML openings, newest first, or None if unreachable.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.

    One page arriving is enough to call the answer real; only every page failing
    means the source is down.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    query = keywords.strip() or DEFAULT_SEARCH
    starts = range(0, PAGES * API_PAGE_SIZE, API_PAGE_SIZE)
    found = merge_queries(starts, partial(_postings_for, query))
    return None if found is None else take_newest(found, limit)


def _postings_for(query: str, start: int) -> QueryResults:
    """One page, as (requisition id, posting) pairs, or None if it could not be read."""
    rows = _rows_for(query, start)
    if rows is None:
        return None
    return [
        (_job_id(row, posting), posting)
        for row in rows
        if (posting := _to_posting(row)) is not None
    ]


def _rows_for(query: str, start: int) -> list[dict] | None:
    """One page of results, or None if that page could not be read.

    The rows sit under ``data``, so a body without it is the endpoint answering
    with something that isn't its API — a failure to look, not a report of
    nothing.
    """
    body = fetch.get_json(SEARCH_URL, _params(query, start))
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    return fetch.json_rows(data, "positions")


def _params(query: str, start: int) -> dict[str, str | int]:
    """The query string for one page of US results."""
    return {"domain": DOMAIN, "query": query, "location": US, "start": start}


def _job_id(job: dict, posting: JobPosting) -> str:
    """The requisition number, for de-duping one page against another."""
    return str(job.get("displayJobId") or job.get("id") or posting.url or posting.title)


def _to_posting(job: dict) -> JobPosting | None:
    """Convert one API row, or None if it isn't an AI/ML role."""
    title = (job.get("name") or "").strip()
    if not is_ai_ml_role(title):
        return None
    path = (job.get("positionUrl") or "").strip()
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=JOB_BASE_URL + path if path else "",
        location=_location_text(job.get("standardizedLocations")),
        date=_parse_posted(job.get("postedTs")),
        job_id=source_id("microsoft", job.get("displayJobId") or job.get("id")),
    )


def _location_text(locations: object) -> str:
    """Where the role is, from the row's already-normalised location list."""
    if not isinstance(locations, list):
        return ""
    return "; ".join(
        place.strip() for place in locations if isinstance(place, str) and place.strip()
    )


def _parse_posted(raw: object) -> datetime | None:
    """Parse ``postedTs``, a Unix timestamp in seconds (e.g. 1789415386)."""
    return parse_unix_timestamp(raw)
