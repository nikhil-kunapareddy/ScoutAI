"""Uber job search, via the JSON endpoint jobs.uber.com serves its own board from.

One request returns the whole US board — ``pagesize=500`` against 259 current US
roles — so there is no paging here and no need to search the keyword box several
times over. The filtering happens on titles, because Uber's ``search`` parameter
matches the full description and scores it: asking it for "machine learning"
returns roles whose text merely mentions ML.

Two things will silently return nothing if changed. ``countries`` wants the
display name — ``United States``, not ``USA``, which answers 200 with an empty
list. And the endpoint sits behind Cloudflare, which rejects a default
``requests`` user-agent; ``settings.TOOL_USER_AGENT`` is browser-shaped and gets
through, which is why this goes through ``fetch`` like every other source.
"""

from __future__ import annotations

from datetime import datetime

from ..registry import ToolRegistry
from . import fetch
from .posting import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    clamp_int,
    render_postings,
    take_newest,
)
from .relevance import is_ai_ml_role, matches_keywords

SEARCH_URL = "https://jobs.uber.com/api/jobs/search/"
JOB_BASE_URL = "https://jobs.uber.com"
ORGANIZATION = "Uber"

US = "United States"     # the display name; "USA" silently matches nothing
API_PAGE_SIZE = 500      # the whole US board in one response


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_uber_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Uber's careers site for recent US job openings relevant to the
        user's field (AI/ML engineering) and return title, date posted, and link.

        Args:
            keywords: Optional phrase to narrow titles (e.g. "machine learning").
                If empty, returns all AI/ML-relevant roles.
            limit: Maximum number of roles to return.
        """
        postings = search(keywords, limit)
        if postings is None:
            return f"Couldn't reach {ORGANIZATION}'s careers site right now. Try again later."
        if not postings:
            return (f"No relevant {ORGANIZATION} roles found right now. "
                    "Try again later or adjust your keywords.")
        return render_postings(
            f"*Latest {ORGANIZATION} AI/ML roles (most recent first) — {{count}} found:*",
            postings,
        )


def search(keywords: str = "", limit: int = DEFAULT_LIMIT) -> list[JobPosting] | None:
    """Uber's current US AI/ML openings, newest first, or None if unreachable.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    rows = fetch.get_rows(SEARCH_URL, "jobs", _params())
    if rows is None:
        return None
    terms = keywords.lower().split()
    postings = [
        posting
        for row in rows
        if (posting := _to_posting(row, terms)) is not None
    ]
    return take_newest(postings, limit)


def _params() -> dict[str, str | int]:
    return {"countries": US, "pagesize": API_PAGE_SIZE, "page": 1}


def _to_posting(job: dict, terms: list[str]) -> JobPosting | None:
    """Convert one API row, or None if it should be skipped."""
    title = (job.get("Title") or "").strip()
    if not is_ai_ml_role(title) or not matches_keywords(title, terms):
        return None
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=_job_url(job),
        location=_location_text(job.get("Locations")),
        date=_parse_display_date(job.get("DisplayDate")),
    )


def _job_url(job: dict) -> str:
    """Absolute URL from the first of the row's relative ``Urls``."""
    urls = job.get("Urls")
    if isinstance(urls, list) and urls and isinstance(urls[0], dict):
        path = (urls[0].get("Url") or "").strip()
        if path:
            return JOB_BASE_URL + path
    return ""


def _location_text(locations: object) -> str:
    """Where the role is. Multi-city roles are common, so they're joined."""
    if not isinstance(locations, list):
        return ""
    places = []
    for entry in locations:
        if not isinstance(entry, dict):
            continue
        parts = [(entry.get(key) or "").strip() for key in ("City", "Region")]
        place = ", ".join(part for part in parts if part)
        if place and place not in places:
            places.append(place)
    return "; ".join(places)


def _parse_display_date(raw: object) -> datetime | None:
    """Parse ``DisplayDate``, e.g. 2026-09-17T20:14:41Z.

    ``Z`` is normalised first: ``fromisoformat`` only learned to read it in 3.11,
    and this package supports 3.10.
    """
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
