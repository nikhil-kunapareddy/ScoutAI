"""Greenhouse job search — one implementation for every company hosted there.

Many large tech companies publish their board at
``boards-api.greenhouse.io/v1/boards/{slug}/jobs``, so adding an org is a line in
``BOARDS``. The plumbing is ``HostedBoard``, which Ashby shares; what is here is
what is Greenhouse's own: where the boards live, and how to read a row.

The board API has no server-side filtering, so the whole list comes back and the
filtering happens here: AI/ML relevance, US-ish location, newest-first.
"""

from __future__ import annotations

from datetime import datetime

from ..registry import ToolRegistry
from .hosted_board import HostedBoard
from .posting import DEFAULT_LIMIT, JobPosting
from .relevance import is_ai_ml_role, matches_keywords

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


BOARD = HostedBoard(
    platform="Greenhouse",
    board_url=BOARD_URL,
    rows_key="jobs",
    boards=BOARDS,
    read_row=_to_posting,
)

#: This source's ``Searcher``, taking the board slug first. See ``directory.py``.
search = BOARD.search


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
        return BOARD.answer(company, keywords, limit)
