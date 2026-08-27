"""Boston University job search, via the SilkRoad RSS feed.

BU's careers site has no search API but publishes every open role as RSS, so we
fetch the feed and filter here. Each item carries a real ``postingDate``.
"""

from __future__ import annotations

import html
import re
from datetime import datetime

import requests

from ...core import settings
from ..registry import ToolRegistry
from . import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    clamp_int,
    is_ai_ml_role,
    matches_keywords,
    render_postings,
    take_newest,
)

FEED_URL = "https://jobs.silkroad.com/BU/External/rss"
ORGANIZATION = "Boston University"

_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)


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
        limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)

        feed = _fetch_feed()
        if feed is None:
            return f"Couldn't reach {ORGANIZATION}'s careers feed right now. Try again later."

        terms = keywords.lower().split()
        postings = [
            posting
            for item in _ITEM_RE.findall(feed)
            if (posting := _to_posting(item, terms)) is not None
        ]

        postings = take_newest(postings, limit)
        if not postings:
            return (f"No relevant {ORGANIZATION} roles found right now. "
                    "Try again later or adjust your keywords.")
        return render_postings(
            f"*Latest {ORGANIZATION} roles (most recent first) — {{count}} found:*", postings
        )


def _fetch_feed() -> str | None:
    """Fetch the RSS feed, or None if BU can't be reached."""
    try:
        resp = requests.get(
            FEED_URL,
            headers={"User-Agent": settings.TOOL_USER_AGENT},
            timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def _to_posting(item: str, terms: list[str]) -> JobPosting | None:
    """Convert one RSS ``<item>``, or None if it should be skipped."""
    title = _tag_text(item, "title")
    # Explicit keywords win; otherwise fall back to the AI/ML filter, since the
    # feed has no server-side search.
    if terms:
        if not matches_keywords(title, terms):
            return None
    elif not is_ai_ml_role(title):
        return None

    raw_date = _tag_text(item, "postingDate")
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=_tag_text(item, "link"),
        location=_tag_text(item, "location"),
        date=_parse_posted_date(raw_date),
        posted_label=raw_date,  # shown verbatim when the date won't parse
    )


def _tag_text(item: str, tag: str) -> str:
    """Extract one RSS tag's text, unwrapping CDATA and decoding entities."""
    match = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", item, re.S)
    return html.unescape(match.group(1).strip()) if match else ""


def _parse_posted_date(raw: str) -> datetime | None:
    """Parse SilkRoad's ``postingDate`` (MM/DD/YYYY)."""
    try:
        return datetime.strptime(raw, "%m/%d/%Y")
    except ValueError:
        return None
