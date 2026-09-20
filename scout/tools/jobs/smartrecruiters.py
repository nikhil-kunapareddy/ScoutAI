"""SmartRecruiters job search — one implementation for every company hosted there.

The third platform to fit ``HostedBoard``, after Greenhouse and Ashby: a board
per company at a predictable URL, one JSON row per opening. What is here is
SmartRecruiters' own — where the boards live, and how to read a row.

Two things it does better than the other two. The country is a structured field
*and* a server-side filter, so ``?country=us`` is applied before the rows are
sent rather than guessed at from free text. And the canonical job URL can be
built from the row itself: ``company.identifier`` carries the capitalised board
name (``ServiceNow``) that ``jobs.smartrecruiters.com`` wants in its path, which
is what lets one reader serve every company without knowing which slug it was
called with.

Note the board URL carries its own query string. ``country=us`` is not optional
decoration — it is the US filter, and ``limit=100`` is the API's own ceiling
(asking for 200 silently returns 100).
"""

from __future__ import annotations

from datetime import datetime

from ..registry import ToolRegistry
from .hosted_board import HostedBoard
from .posting import DEFAULT_LIMIT, JobPosting
from .relevance import is_ai_ml_role, matches_keywords

BOARD_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&country=us"
JOB_BASE_URL = "https://jobs.smartrecruiters.com"

# Board slug -> display name. Add a company = add a line. All verified live.
BOARDS = {
    "servicenow": "ServiceNow",
}

US = "us"


def _to_posting(job: dict, organization: str, terms: list[str]) -> JobPosting | None:
    """Convert one board row, or None if it should be skipped."""
    # Titles routinely carry stray padding (" AI Architect "), which would
    # otherwise reach both the keyword match and the rendered reply.
    title = (job.get("name") or "").strip()
    if not is_ai_ml_role(title) or not matches_keywords(title, terms):
        return None
    location = job.get("location") or {}
    # Belt and braces: the URL already pins country=us, so a row that disagrees
    # means the filter was ignored rather than that the role is worth showing.
    country = (location.get("country") or "").strip().lower()
    if country and country != US:
        return None
    return JobPosting(
        title=title,
        organization=organization,
        url=_job_url(job),
        location=_location_text(location),
        date=_parse_released(job.get("releasedDate")),
    )


def _job_url(job: dict) -> str:
    """The public posting URL, built from the row's own board identifier.

    ``jobs.smartrecruiters.com`` wants the capitalised board name, which is
    exactly what ``company.identifier`` holds — so this works for any company on
    the platform, not just the ones in ``BOARDS``.
    """
    identifier = ((job.get("company") or {}).get("identifier") or "").strip()
    job_id = str(job.get("id") or "").strip()
    return f"{JOB_BASE_URL}/{identifier}/{job_id}" if identifier and job_id else ""


def _location_text(location: dict) -> str:
    """Somewhere to show. ``fullLocation`` when the board fills it in, else built."""
    full = (location.get("fullLocation") or "").strip()
    if full:
        return full
    parts = [(location.get(key) or "").strip() for key in ("city", "region")]
    return ", ".join(part for part in parts if part)


def _parse_released(raw: object) -> datetime | None:
    """Parse ``releasedDate``, e.g. 2026-09-19T02:10:29.545Z.

    ``Z`` is normalised first: ``fromisoformat`` only learned to read it in 3.11,
    and this package supports 3.10.
    """
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


BOARD = HostedBoard(
    platform="SmartRecruiters",
    board_url=BOARD_URL,
    rows_key="content",
    boards=BOARDS,
    read_row=_to_posting,
)

#: This source's ``Searcher``, taking the board slug first. See ``directory.py``.
search = BOARD.search


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_smartrecruiters_jobs(
        company: str, keywords: str = "", limit: int = DEFAULT_LIMIT
    ) -> str:
        """Search a company's SmartRecruiters careers board for recent US AI/ML
        job openings and return title, date posted, and link.

        Args:
            company: Which company to search. Supported: servicenow.
            keywords: Optional phrase to narrow titles (e.g. "machine learning").
                If empty, returns all AI/ML-relevant roles.
            limit: Maximum number of roles to return.
        """
        return BOARD.answer(company, keywords, limit)
