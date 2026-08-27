"""Northeastern University job search, via the public Workday (CXS) endpoint.

Workday filters by ``searchText`` server-side and returns its own relevance
order. ``postedOn`` is a relative string ("Posted 5 Days Ago"), not a date, so
there is nothing to sort on and the order is left as Workday gave it.
"""

from __future__ import annotations

import requests

from ...core import settings
from ..registry import ToolRegistry
from . import DEFAULT_LIMIT, MAX_LIMIT, JobPosting, clamp_int, render_postings

# POST https://{host}/wday/cxs/{tenant}/{site}/jobs
HOST = "northeastern.wd1.myworkdayjobs.com"
TENANT = "northeastern"
SITE = "Careers"
JOBS_URL = f"https://{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"

ORGANIZATION = "Northeastern University"
DEFAULT_SEARCH = "machine learning"  # when the model passes no keywords
API_PAGE_SIZE = 20                   # Workday caps page size at 20


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_northeastern_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Northeastern University's careers site for recent job openings
        and return each role's title, location, date posted, and link.

        Args:
            keywords: Optional search phrase. If empty, defaults to AI/ML roles
                ("machine learning").
            limit: Maximum number of roles to return.
        """
        limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)

        jobs = _fetch_jobs(keywords.strip() or DEFAULT_SEARCH, limit)
        if jobs is None:
            return "Couldn't reach Northeastern's careers site right now. Try again later."

        postings = [_to_posting(job) for job in jobs[:limit]]
        if not postings:
            return (f"No relevant {ORGANIZATION} roles found right now. "
                    "Try again later or adjust your keywords.")
        return render_postings(
            f"*Latest {ORGANIZATION} roles — {{count}} found:*", postings
        )


def _fetch_jobs(search_text: str, limit: int) -> list[dict] | None:
    """Run one Workday search, or None if the site can't be reached."""
    try:
        resp = requests.post(
            JOBS_URL,
            json={"appliedFacets": {}, "limit": min(limit, API_PAGE_SIZE),
                  "offset": 0, "searchText": search_text},
            headers={"User-Agent": settings.TOOL_USER_AGENT,
                     "Content-Type": "application/json", "Accept": "application/json"},
            timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("jobPostings", [])
    except Exception:
        return None


def _to_posting(job: dict) -> JobPosting:
    """Convert one Workday row."""
    path = job.get("externalPath", "")
    return JobPosting(
        title=(job.get("title") or "").strip(),
        organization=ORGANIZATION,
        url=f"https://{HOST}/{SITE}{path}" if path else "",
        location=(job.get("locationsText") or "").strip(),
        posted_label=(job.get("postedOn") or "").strip(),  # relative, not a date
    )
