"""Netflix job search, via the Eightfold-backed API explore.jobs.netflix.net uses.

This API does keyword matching, US-location filtering and recency sorting
server-side, so we hand it the terms and format what comes back.
"""

from __future__ import annotations

from datetime import datetime

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
from .relevance import is_ai_ml_role, search_queries
from .unsent import render_unsent

SEARCH_URL = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
JOB_BASE_URL = "https://explore.jobs.netflix.net/careers/job/"
ORGANIZATION = "Netflix"

#: Rows per query, before filtering.
API_PAGE_SIZE = 50


def register(reg: ToolRegistry) -> None:
    @reg.tool
    # `config` is injected by LangChain and kept out of the schema: no Args: entry.
    def search_netflix_jobs(  # noqa: D417
        keywords: str = "", limit: int = DEFAULT_LIMIT, *, config: RunnableConfig
    ) -> str:
        """Search Netflix's careers site for recent US job openings relevant to
        the user's field (AI/ML engineering) and return title, date posted, and link.

        Roles are listed newest-first.

        Args:
            keywords: Optional search phrase. If empty, uses the user's profile
                (machine learning / applied scientist / AI engineer / etc.).
            limit: Maximum number of roles to return.
        """
        postings = search(keywords, limit)
        if not postings:
            return (
                f"No relevant {ORGANIZATION} roles found right now. "
                "Try again later or widen your keywords."
            )
        return render_unsent(
            f"*Latest {ORGANIZATION} AI/ML roles (most recent first) — {{count}} found:*",
            postings,
            config=config,
        )


def search(keywords: str = "", limit: int = DEFAULT_LIMIT) -> list[JobPosting] | None:
    """Netflix's recent AI/ML openings, newest first, or None if it can't be reached.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    found = merge_queries(search_queries(keywords), _postings_for)
    return None if found is None else take_newest(found, limit)


def _postings_for(query: str) -> QueryResults:
    """One keyword search, as (job id, posting) pairs, or None if unreachable."""
    rows = fetch.get_rows(SEARCH_URL, "positions", _params(query))
    if rows is None:
        return None
    found = []
    for position in rows:
        job_id = str(position.get("id") or "")
        title = (position.get("name") or "").strip()
        if job_id and is_ai_ml_role(title):
            found.append((job_id, _to_posting(position, job_id, title)))
    return found


def _params(query: str) -> dict[str, str | int]:
    """The query string for one keyword search, newest US roles first."""
    return {
        "domain": "netflix.com",
        "query": query,
        "location": "United States",
        "sort_by": "timestamp",
        "num": API_PAGE_SIZE,
        "start": 0,
    }


def _to_posting(position: dict, job_id: str, title: str) -> JobPosting:
    """Convert one API row."""
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=position.get("canonicalPositionUrl") or (JOB_BASE_URL + job_id),
        location=(position.get("location") or "").strip(),
        date=_parse_created(position.get("t_create")),
        job_id=source_id("netflix", job_id),
    )


def _parse_created(timestamp: object) -> datetime | None:
    """Parse ``t_create`` (unix seconds, sometimes missing) as UTC."""
    return parse_unix_timestamp(timestamp)
