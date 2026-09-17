"""Lenovo job search, via the RSS feed behind the Avature careers portal.

``jobs.lenovo.com`` runs on Avature, whose search page publishes the same query
as RSS at ``SearchJobs/feed/``. That is worth taking over the HTML: the feed
carries a real ``pubDate``, where the page renders "Posted 15-Sep-2026" inside
four levels of nested div that a redesign would move.

What the feed leaves out is the location. The Country/Region facet is pinned to
the United States in the query instead — the same job the location filter does
for the other sources, done server-side.

The search box is the ``search`` parameter, so this runs the profile queries and
merges them the way Amazon and Netflix do.
"""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from ..registry import ToolRegistry
from . import feeds, fetch
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

FEED_URL = "https://jobs.lenovo.com/en_US/careers/SearchJobs/feed/"
ORGANIZATION = "Lenovo"

#: Country/Region = United States of America. The numbers are Avature's own
#: field and option ids, which is why they read like nothing at all.
US_FACET = {"13036": "[12016802]", "13036_format": "6621"}

#: The feed's own cap: it returns 20 items however large ``jobRecordsPerPage``
#: is. Asking for more than one page per query would buy little — the profile
#: queries already overlap.
API_PAGE_SIZE = 20

#: .../JobDetail/{slug}/{id} — the id, for de-duping across queries.
_JOB_ID_RE = re.compile(r"/JobDetail/[^/]+/(\d+)")


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_lenovo_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Lenovo's careers site for recent US job openings relevant to
        the user's field (AI/ML engineering) and return title, date posted, and link.

        Roles are listed newest-first. Lenovo's feed does not publish a location,
        but the search is restricted to the United States.

        Args:
            keywords: Optional search phrase. If empty, uses the user's profile
                (machine learning / applied scientist / AI engineer / etc.).
            limit: Maximum number of roles to return.
        """
        postings = search(keywords, limit)
        if not postings:
            return (f"No relevant {ORGANIZATION} roles found right now. "
                    "Try again later or widen your keywords.")
        return render_postings(
            f"*Latest {ORGANIZATION} AI/ML roles (most recent first) — {{count}} found:*",
            postings,
        )


def search(keywords: str = "", limit: int = DEFAULT_LIMIT) -> list[JobPosting] | None:
    """Lenovo's recent AI/ML openings, newest first, or None if it can't be reached.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    found = merge_queries(search_queries(keywords), _postings_for)
    return None if found is None else take_newest(found, limit)


def _postings_for(query: str) -> QueryResults:
    """One keyword search, as (job id, posting) pairs, or None if unreachable."""
    feed = fetch.get_text(FEED_URL, _params(query))
    if feed is None:
        return None
    return [
        (_job_id(posting.url), posting)
        for item in feeds.items(feed)
        if (posting := _to_posting(item)) is not None
    ]


def _params(query: str) -> dict[str, str | int]:
    return {
        **US_FACET,
        "listFilterMode": 1,
        "jobRecordsPerPage": API_PAGE_SIZE,
        "search": query,
    }


def _to_posting(item: str) -> JobPosting | None:
    """Convert one RSS ``<item>``, or None if it should be skipped.

    The feed's keyword search matches the whole posting, so it returns sales and
    support roles that merely mention AI; the shared title filter is what keeps
    the list to the user's field.
    """
    title = feeds.tag_text(item, "title")
    if not is_ai_ml_role(title):
        return None
    raw_date = feeds.tag_text(item, "pubDate")
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=feeds.tag_text(item, "link"),
        date=_parse_pub_date(raw_date),
        posted_label=raw_date,  # shown verbatim when the date won't parse
    )


def _job_id(url: str) -> str:
    """The numeric id in a job URL, falling back to the URL itself."""
    match = _JOB_ID_RE.search(url)
    return match.group(1) if match else url


def _parse_pub_date(raw: str) -> datetime | None:
    """Parse the RFC 822 ``pubDate``, e.g. "Thu, 07 Aug 2025 00:00:00 +0000"."""
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
