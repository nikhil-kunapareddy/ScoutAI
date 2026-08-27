"""Google job search, by scraping the server-rendered careers listings.

Google publishes no jobs API and no posting dates, so results keep the order
``sort_by=date`` returns them in — newest first, with no date to show.
"""

from __future__ import annotations

import re

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
)

SEARCH_URL = "https://www.google.com/about/careers/applications/jobs/results/"
JOB_BASE_URL = "https://www.google.com/about/careers/applications/"

# Shown instead of a date, so the model doesn't invent one.
NO_DATE_LABEL = "not published by Google"

# Each job is a "Learn more" anchor: href to jobs/results/{id}-{slug}, with the
# clean title in the aria-label.
_JOB_RE = re.compile(
    r'href="(jobs/results/\d+-[^"?]+)[^"]*"[^>]*aria-label="Learn more about ([^"]+)"'
)


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_google_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Google's careers site for recent US job openings relevant to
        the user's field (AI/ML engineering) and return each role's title and link.

        Google does not publish posting dates, so roles are listed newest-first
        (no date is available).

        Args:
            keywords: Optional search phrase. If empty, uses the user's profile
                (machine learning / applied scientist / AI engineer / etc.).
            limit: Maximum number of roles to return.
        """
        limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)

        # Insertion-ordered and never re-sorted: without dates, the source's own
        # ordering is the only recency signal available.
        found: dict[str, JobPosting] = {}
        for query in search_queries(keywords):
            for href, raw_title in _JOB_RE.findall(_fetch_html(query)):
                title = raw_title.strip()
                job_id = href.split("/")[2].split("-")[0]  # jobs/results/{id}-{slug}
                if job_id in found or not is_ai_ml_role(title):
                    continue
                found[job_id] = JobPosting(
                    title=title,
                    organization="Google",
                    url=JOB_BASE_URL + href,
                    posted_label=NO_DATE_LABEL,
                )

        postings = list(found.values())[:limit]
        if not postings:
            return "No relevant Google roles found right now. Try again later."
        return render_postings(
            "*Latest Google AI/ML roles (most recent first) — {count} found:*",
            postings,
            footer="_Google doesn't publish posting dates; roles are listed newest-first._",
        )


def _fetch_html(query: str) -> str:
    """Fetch one results page. Returns "" if Google is unreachable, so the
    remaining profile queries can still produce an answer."""
    try:
        resp = requests.get(
            SEARCH_URL,
            params={"q": query, "location": "United States", "sort_by": "date"},
            headers={"User-Agent": settings.TOOL_USER_AGENT},
            timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.text
    except Exception:
        return ""
