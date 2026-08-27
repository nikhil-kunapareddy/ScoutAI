"""Greenhouse job search — one implementation for every company hosted there.

Many large tech companies publish their board at
``boards-api.greenhouse.io/v1/boards/{slug}/jobs``, so adding an org is a line in
``BOARDS``. Sharing an implementation is right here because it is one platform,
not one job source.

The board API has no server-side filtering, so we fetch the full list and filter
here: AI/ML relevance, US-ish location, newest-first.
"""

from __future__ import annotations

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

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

# Board slug -> display name. Add an org = add a line. All verified live.
BOARDS = {
    "databricks": "Databricks",
    "airbnb": "Airbnb",
    "stripe": "Stripe",
    "pinterest": "Pinterest",
    "reddit": "Reddit",
    "coinbase": "Coinbase",
    "dropbox": "Dropbox",
    "robinhood": "Robinhood",
}

# Markers of a non-US role (Greenhouse locations are free text).
_NON_US = (
    "india", "canada", "united kingdom", " uk", "ireland", "germany", "france",
    "netherlands", "israel", "singapore", "australia", "japan", "china", "brazil",
    "mexico", "spain", "poland", "costa rica", "argentina", "emea", "apac", "romania",
    "dublin", "london", "berlin", "toronto", "bengaluru", "bangalore", "tokyo",
    "amsterdam", "sydney", "são paulo", "sao paulo",
)


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_greenhouse_jobs(
        company: str, keywords: str = "", limit: int = DEFAULT_LIMIT
    ) -> str:
        """Search a big-tech company's Greenhouse careers board for recent US
        AI/ML job openings and return title, date posted, and link.

        Args:
            company: Which company to search. Supported: databricks, airbnb,
                stripe, pinterest, reddit, coinbase, dropbox, robinhood.
            keywords: Optional phrase to narrow titles (e.g. "machine learning").
                If empty, returns all AI/ML-relevant roles.
            limit: Maximum number of roles to return.
        """
        slug = (company or "").strip().lower()
        if slug not in BOARDS:
            supported = ", ".join(sorted(BOARDS))
            return f"Unknown company '{company}'. Supported Greenhouse companies: {supported}."

        name = BOARDS[slug]
        limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)

        jobs = _fetch_board(slug)
        if jobs is None:
            return f"Couldn't reach {name}'s careers board right now. Try again later."

        terms = keywords.lower().split()
        postings = [
            posting
            for job in jobs
            if (posting := _to_posting(job, name, terms)) is not None
        ]

        postings = take_newest(postings, limit)
        if not postings:
            return (f"No relevant {name} roles found right now. "
                    "Try again later or adjust your keywords.")
        return render_postings(
            f"*Latest {name} AI/ML roles (most recent first) — {{count}} found:*", postings
        )


def _fetch_board(slug: str) -> list[dict] | None:
    """Fetch a board's full job list, or None if it can't be reached.

    None and [] mean different things to the user: "the board is down" versus
    "the board has nothing matching".
    """
    try:
        resp = requests.get(
            BOARD_URL.format(slug=slug),
            headers={"User-Agent": settings.TOOL_USER_AGENT, "Accept": "application/json"},
            timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("jobs", [])
    except Exception:
        return None


def _to_posting(job: dict, organization: str, terms: list[str]) -> JobPosting | None:
    """Convert one board row, or None if it should be skipped."""
    title = (job.get("title") or "").strip()
    if not is_ai_ml_role(title) or not matches_keywords(title, terms):
        return None
    location = ((job.get("location") or {}).get("name") or "").strip()
    if location and not _is_us_location(location):
        return None
    return JobPosting(
        title=title,
        organization=organization,
        url=job.get("absolute_url", ""),
        location=location,
        date=_parse_published(job),
    )


def _is_us_location(name: str) -> bool:
    """Best-effort: keep US and generic-remote roles, drop clearly-foreign ones."""
    low = name.lower()
    if "united states" in low or "usa" in low or "u.s." in low:
        return True
    # What's left is a US city/state or a bare "Remote" — treat as US-eligible.
    return not any(marker in low for marker in _NON_US)


def _parse_published(job: dict) -> datetime | None:
    """Parse the posting date: ISO 8601 with an offset, e.g. 2026-07-01T18:31:32-04:00."""
    raw = job.get("first_published") or job.get("updated_at") or ""
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
