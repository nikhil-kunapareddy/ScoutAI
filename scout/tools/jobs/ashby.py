"""Ashby job search — one implementation for every company hosted there.

Ashby publishes each customer's board at
``api.ashbyhq.com/posting-api/job-board/{slug}``, so adding a company is a line
in ``BOARDS``. The plumbing is ``HostedBoard``, shared with Greenhouse; what is
here is Ashby's own: where the boards live, and how to read a row.

Unlike Greenhouse, the country comes back structured, so that filter is exact
rather than a reading of free text.

Note this is the *posting* API, not the company's careers page: whoop.com and
the like sit behind bot protection, while this answers plain JSON.
"""

from __future__ import annotations

from datetime import datetime

from ..registry import ToolRegistry
from .hosted_board import HostedBoard
from .posting import DEFAULT_LIMIT, JobPosting
from .relevance import is_ai_ml_role, matches_keywords

BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

# Board slug -> display name. Add a company = add a line. All verified live.
BOARDS = {
    "whoop": "WHOOP",
    "openai": "OpenAI",
    "snowflake": "Snowflake",
}

US = "United States"


def _to_posting(job: dict, organization: str, terms: list[str]) -> JobPosting | None:
    """Convert one board row, or None if it should be skipped."""
    title = (job.get("title") or "").strip()
    if not is_ai_ml_role(title) or not matches_keywords(title, terms):
        return None
    if job.get("isListed") is False:  # pulled from the board but still in the feed
        return None
    if not _is_us(job):
        return None
    return JobPosting(
        title=title,
        organization=organization,
        url=job.get("jobUrl") or job.get("applyUrl", ""),
        location=(job.get("location") or "").strip(),
        date=_parse_published(job.get("publishedAt")),
    )


def _is_us(job: dict) -> bool:
    """Whether a row is a US role, by Ashby's structured country.

    A row with no country is kept: every board seen so far fills it in, and
    dropping a role that might be local costs more than showing one that isn't.
    """
    address = (job.get("address") or {}).get("postalAddress") or {}
    country = (address.get("addressCountry") or "").strip()
    return not country or country == US


def _parse_published(raw: object) -> datetime | None:
    """Parse ``publishedAt``, e.g. 2026-07-17T19:03:39.500+00:00.

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
    platform="Ashby",
    board_url=BOARD_URL,
    rows_key="jobs",
    boards=BOARDS,
    read_row=_to_posting,
)

#: This source's ``Searcher``, taking the board slug first. See ``directory.py``.
search = BOARD.search


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_ashby_jobs(
        company: str, keywords: str = "", limit: int = DEFAULT_LIMIT
    ) -> str:
        """Search a company's Ashby careers board for recent US AI/ML job
        openings and return title, date posted, and link.

        Args:
            company: Which company to search. Supported: whoop, openai,
                snowflake.
            keywords: Optional phrase to narrow titles (e.g. "machine learning").
                If empty, returns all AI/ML-relevant roles.
            limit: Maximum number of roles to return.
        """
        return BOARD.answer(company, keywords, limit)
