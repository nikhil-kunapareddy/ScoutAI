"""Netflix job search, via the Eightfold-backed API explore.jobs.netflix.net uses.

This API does keyword matching, US-location filtering and recency sorting
server-side, so we hand it the terms and format what comes back.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from ...core import settings
from ..registry import ToolRegistry
from . import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    clamp_int,
    is_ai_ml_role,
    json_rows,
    render_postings,
    search_queries,
    take_newest,
)

SEARCH_URL = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
JOB_BASE_URL = "https://explore.jobs.netflix.net/careers/job/"

API_PAGE_SIZE = 50  # rows per query, before filtering


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_netflix_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
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
            return ("No relevant Netflix roles found right now. "
                    "Try again later or widen your keywords.")
        return render_postings(
            "*Latest Netflix AI/ML roles (most recent first) — {count} found:*", postings
        )


def search(keywords: str = "", limit: int = DEFAULT_LIMIT) -> list[JobPosting] | None:
    """Netflix's recent AI/ML openings, newest first, or None if it can't be reached.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)

    found: dict[str, JobPosting] = {}  # by job id, de-duped across queries
    reached = False
    for query in search_queries(keywords):
        positions = _fetch_positions(query)
        if positions is None:
            continue
        reached = True
        for position in positions:
            job_id = str(position.get("id") or "")
            title = (position.get("name") or "").strip()
            if not job_id or job_id in found or not is_ai_ml_role(title):
                continue
            found[job_id] = _to_posting(position, job_id, title)

    # Every query failing means the API is down, which a caller may need to
    # report differently from "Netflix has nothing".
    if not reached:
        return None
    return take_newest(list(found.values()), limit)


def _fetch_positions(query: str) -> list[dict] | None:
    """Run one keyword search. Returns None if Netflix is unreachable, so the
    remaining profile queries can still produce an answer."""
    params: dict[str, str | int] = {
        "domain": "netflix.com",
        "query": query,
        "location": "United States",
        "sort_by": "timestamp",
        "num": API_PAGE_SIZE,
        "start": 0,
    }
    try:
        resp = requests.get(
            SEARCH_URL,
            params=params,
            headers={"User-Agent": settings.TOOL_USER_AGENT, "Accept": "application/json"},
            timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return json_rows(resp.json(), "positions")
    except Exception:
        return None


def _to_posting(position: dict, job_id: str, title: str) -> JobPosting:
    """Convert one API row."""
    return JobPosting(
        title=title,
        organization="Netflix",
        url=position.get("canonicalPositionUrl") or (JOB_BASE_URL + job_id),
        location=(position.get("location") or "").strip(),
        date=_parse_created(position.get("t_create")),
    )


def _parse_created(timestamp: object) -> datetime | None:
    """Parse ``t_create`` (unix seconds, sometimes missing) as UTC."""
    if not isinstance(timestamp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
