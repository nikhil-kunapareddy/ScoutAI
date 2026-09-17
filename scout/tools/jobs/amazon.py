"""Amazon job search, via the public JSON endpoint amazon.jobs itself uses.

The API is keyword-based with no relevance filter, so we run each profile query
and filter the merged results here. Amazon does publish a posting date, which is
what makes the ``days`` window possible for this source.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import partial

from ..registry import ToolRegistry
from . import fetch
from .posting import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    QueryResults,
    clamp_int,
    merge_queries,
    render_postings,
    take_newest,
)
from .relevance import is_ai_ml_role, search_queries

SEARCH_URL = "https://www.amazon.jobs/en/search.json"
JOB_BASE_URL = "https://www.amazon.jobs"
ORGANIZATION = "Amazon"

DEFAULT_DAYS = 1  # last 24h
MAX_DAYS = 30
API_PAGE_SIZE = 100  # rows per query, before filtering


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_amazon_jobs(
        keywords: str = "", days: int = DEFAULT_DAYS, limit: int = DEFAULT_LIMIT
    ) -> str:
        """Search Amazon's careers site for recent US job openings relevant to
        the user's field (AI/ML engineering) and return title, date posted, and link.

        Args:
            keywords: Optional search phrase. If empty, uses the user's profile
                (machine learning / applied scientist / AI engineer / etc.).
            days: Only include roles posted within this many days (default 1 = last 24h).
            limit: Maximum number of roles to return.
        """
        days = clamp_int(days, DEFAULT_DAYS, 1, MAX_DAYS)
        window = "last 24h" if days == 1 else f"last {days} days"

        postings = search(keywords, limit, days)
        if not postings:
            return (f"No relevant {ORGANIZATION} roles found in the {window}. "
                    "Try again later or widen the window.")
        return render_postings(
            f"*Latest {ORGANIZATION} AI/ML roles ({window}) — {{count}} found:*", postings
        )


def search(
    keywords: str = "", limit: int = DEFAULT_LIMIT, days: int = DEFAULT_DAYS
) -> list[JobPosting] | None:
    """Amazon's recent AI/ML openings, newest first, or None if it can't be reached.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``. The
    tool above is this search with one format applied.
    """
    days = clamp_int(days, DEFAULT_DAYS, 1, MAX_DAYS)
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    cutoff = datetime.now() - timedelta(days=days)

    found = merge_queries(search_queries(keywords), partial(_postings_for, cutoff=cutoff))
    return None if found is None else take_newest(found, limit)


def _postings_for(query: str, cutoff: datetime) -> QueryResults:
    """One keyword search, as (job id, posting) pairs, or None if unreachable."""
    rows = fetch.get_rows(SEARCH_URL, "jobs", _params(query))
    if rows is None:
        return None
    return [
        (str(row.get("id", "")), posting)
        for row in rows
        if (posting := _to_posting(row, cutoff)) is not None
    ]


def _params(query: str) -> dict[str, str | int]:
    return {
        "base_query": query,
        "normalized_country_code[]": "USA",
        "sort": "recent",
        "result_limit": API_PAGE_SIZE,
        "offset": 0,
    }


def _to_posting(job: dict, cutoff: datetime) -> JobPosting | None:
    """Convert one API row, or None if it should be skipped."""
    if job.get("is_intern"):
        return None
    title = (job.get("title") or "").strip()
    if not is_ai_ml_role(title):
        return None
    posted = _parse_posted_date(job.get("posted_date"))
    if posted is None or posted < cutoff:
        return None
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=JOB_BASE_URL + job.get("job_path", ""),
        location=(job.get("location") or "").strip(),
        date=posted,
    )


def _parse_posted_date(raw: str | None) -> datetime | None:
    """Parse ``posted_date`` ("June 03, 2026"), tolerating odd spacing."""
    if not raw:
        return None
    try:
        return datetime.strptime(" ".join(raw.split()), "%B %d, %Y")
    except ValueError:
        return None
