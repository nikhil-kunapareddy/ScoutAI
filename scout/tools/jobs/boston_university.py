"""Boston University job search, via the SilkRoad RSS feed.

BU's careers site has no search API but publishes every open role as RSS, so we
fetch the feed and filter here. Each item carries a real ``postingDate``.
"""

from __future__ import annotations

from datetime import datetime

from ..registry import ToolRegistry
from . import feeds, fetch
from .posting import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    clamp_int,
    render_postings,
    take_newest,
)
from .relevance import is_ai_ml_role, matches_keywords

FEED_URL = "https://jobs.silkroad.com/BU/External/rss"
ORGANIZATION = "Boston University"


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_boston_university_jobs(
        keywords: str = "", limit: int = DEFAULT_LIMIT
    ) -> str:
        """Search Boston University's careers site for recent job openings and
        return each role's title, location, date posted, and link.

        Roles are listed newest-first.

        Args:
            keywords: Optional search phrase to match in titles. If empty,
                defaults to AI/ML-relevant roles.
            limit: Maximum number of roles to return.
        """
        postings = search(keywords, limit)
        if postings is None:
            return f"Couldn't reach {ORGANIZATION}'s careers feed right now. Try again later."
        if not postings:
            return (f"No relevant {ORGANIZATION} roles found right now. "
                    "Try again later or adjust your keywords.")
        return render_postings(
            f"*Latest {ORGANIZATION} roles (most recent first) — {{count}} found:*", postings
        )


def search(keywords: str = "", limit: int = DEFAULT_LIMIT) -> list[JobPosting] | None:
    """BU's current openings, newest first, or None if the feed can't be reached.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)

    feed = fetch.get_text(FEED_URL)
    if feed is None:
        return None

    terms = keywords.lower().split()
    postings = [
        posting
        for item in feeds.items(feed)
        if (posting := _to_posting(item, terms)) is not None
    ]
    return take_newest(postings, limit)


def _to_posting(item: str, terms: list[str]) -> JobPosting | None:
    """Convert one RSS ``<item>``, or None if it should be skipped."""
    title = feeds.tag_text(item, "title")
    if not _is_wanted(title, terms):
        return None

    raw_date = feeds.tag_text(item, "postingDate")
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=feeds.tag_text(item, "link"),
        location=feeds.tag_text(item, "location"),
        date=_parse_posted_date(raw_date),
        posted_label=raw_date,  # shown verbatim when the date won't parse
    )


def _is_wanted(title: str, terms: list[str]) -> bool:
    """Explicit keywords win; otherwise fall back to the AI/ML filter.

    The feed has no server-side search, so one of the two always applies — asking
    BU for "custodian" should find one, without the AI/ML filter dropping it.
    """
    return matches_keywords(title, terms) if terms else is_ai_ml_role(title)


def _parse_posted_date(raw: str) -> datetime | None:
    """Parse SilkRoad's ``postingDate`` (MM/DD/YYYY)."""
    try:
        return datetime.strptime(raw, "%m/%d/%Y")
    except ValueError:
        return None
