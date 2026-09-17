"""Google job search, by scraping the server-rendered careers listings.

Google publishes no jobs API and no posting dates, so results keep the order
``sort_by=date`` returns them in — newest first, with no date to show.
"""

from __future__ import annotations

import re

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
)
from .relevance import is_ai_ml_role, search_queries

SEARCH_URL = "https://www.google.com/about/careers/applications/jobs/results/"
JOB_BASE_URL = "https://www.google.com/about/careers/applications/"
ORGANIZATION = "Google"

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
        postings = search(keywords, limit)
        if not postings:
            return f"No relevant {ORGANIZATION} roles found right now. Try again later."
        return render_postings(
            f"*Latest {ORGANIZATION} AI/ML roles (most recent first) — {{count}} found:*",
            postings,
            footer="_Google doesn't publish posting dates; roles are listed newest-first._",
        )


def search(keywords: str = "", limit: int = DEFAULT_LIMIT) -> list[JobPosting] | None:
    """Google's current AI/ML listings, or None if the site can't be reached.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.

    Never re-sorted: without dates, the source's own ordering is the only
    recency signal available, and ``merge_queries`` preserves it.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    found = merge_queries(search_queries(keywords), _postings_for)
    return None if found is None else found[:limit]


def _postings_for(query: str) -> QueryResults:
    """One results page, as (job id, posting) pairs, or None if unreachable."""
    html = fetch.get_text(SEARCH_URL, _params(query))
    if html is None:
        return None
    found = []
    for href, raw_title in _JOB_RE.findall(html):
        title = raw_title.strip()
        if is_ai_ml_role(title):
            found.append((_job_id(href), _to_posting(href, title)))
    return found


def _job_id(href: str) -> str:
    """The numeric id in ``jobs/results/{id}-{slug}``, for de-duping queries."""
    return href.split("/")[2].split("-")[0]


def _params(query: str) -> dict[str, str]:
    return {"q": query, "location": "United States", "sort_by": "date"}


def _to_posting(href: str, title: str) -> JobPosting:
    """Convert one "Learn more" anchor."""
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=JOB_BASE_URL + href,
        posted_label=NO_DATE_LABEL,
    )
