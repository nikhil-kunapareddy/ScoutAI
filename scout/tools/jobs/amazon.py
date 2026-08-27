"""Amazon job search, via the public JSON endpoint amazon.jobs itself uses.

The API is keyword-based with no relevance filter, so we run each profile query
and filter the merged results here. Amazon does publish a posting date, which is
what makes the ``days`` window possible for this source.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import requests

from ...core import settings
from ..registry import ToolRegistry
from . import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    clamp_int,
    is_ai_ml_role,
    render_postings,
    search_queries,
    take_newest,
)

SEARCH_URL = "https://www.amazon.jobs/en/search.json"
JOB_BASE_URL = "https://www.amazon.jobs"

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
        limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
        cutoff = datetime.now() - timedelta(days=days)

        found: dict[str, JobPosting] = {}  # by job id, de-duped across queries
        for query in search_queries(keywords):
            for job in _fetch_jobs(query):
                posting = _to_posting(job, cutoff)
                if posting is not None:
                    found.setdefault(job["id"], posting)

        postings = take_newest(list(found.values()), limit)
        window = "last 24h" if days == 1 else f"last {days} days"
        if not postings:
            return (f"No relevant Amazon roles found in the {window}. "
                    "Try again later or widen the window.")
        return render_postings(
            f"*Latest Amazon AI/ML roles ({window}) — {{count}} found:*", postings
        )


def _fetch_jobs(query: str) -> list[dict]:
    """Run one keyword search. Returns [] if Amazon is unreachable, so the
    remaining profile queries can still produce an answer."""
    try:
        resp = requests.get(
            SEARCH_URL,
            params={"base_query": query, "normalized_country_code[]": "USA",
                    "sort": "recent", "result_limit": API_PAGE_SIZE, "offset": 0},
            headers={"User-Agent": settings.TOOL_USER_AGENT, "Accept": "application/json"},
            timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("jobs", [])
    except Exception:
        return []


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
        organization="Amazon",
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
