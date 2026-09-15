"""Ashby job search — one implementation for every company hosted there.

Ashby publishes each customer's board at
``api.ashbyhq.com/posting-api/job-board/{slug}``, so adding a company is a line
in ``BOARDS``. Sharing an implementation is right here for the same reason it is
in ``greenhouse.py``: this is one platform, not one job source.

The API does no filtering, so we fetch the board and filter here — AI/ML
relevance, US location, newest-first. Unlike Greenhouse, the country comes back
structured, so that filter is exact rather than a reading of free text.

Note this is the *posting* API, not the company's careers page: whoop.com and
the like sit behind bot protection, while this answers plain JSON.
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
    json_rows,
    matches_keywords,
    render_postings,
    take_newest,
)

BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

# Board slug -> display name. Add a company = add a line. All verified live.
BOARDS = {
    "whoop": "WHOOP",
}

US = "United States"


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_ashby_jobs(
        company: str, keywords: str = "", limit: int = DEFAULT_LIMIT
    ) -> str:
        """Search a company's Ashby careers board for recent US AI/ML job
        openings and return title, date posted, and link.

        Args:
            company: Which company to search. Supported: whoop.
            keywords: Optional phrase to narrow titles (e.g. "machine learning").
                If empty, returns all AI/ML-relevant roles.
            limit: Maximum number of roles to return.
        """
        slug = (company or "").strip().lower()
        if slug not in BOARDS:
            supported = ", ".join(sorted(BOARDS))
            return f"Unknown company '{company}'. Supported Ashby companies: {supported}."

        name = BOARDS[slug]
        postings = search(slug, keywords, limit)
        if postings is None:
            return f"Couldn't reach {name}'s careers board right now. Try again later."
        if not postings:
            return (f"No relevant {name} roles found right now. "
                    "Try again later or adjust your keywords.")
        return render_postings(
            f"*Latest {name} AI/ML roles (most recent first) — {{count}} found:*", postings
        )


def search(
    company: str, keywords: str = "", limit: int = DEFAULT_LIMIT
) -> list[JobPosting] | None:
    """One board's AI/ML openings, newest first, or None if it can't be reached.

    ``company`` is a board slug — a key of ``BOARDS``; an unknown one finds no
    board and reads as unreachable. The postings rather than the rendered text,
    so a caller searching several companies at once can merge and count them —
    see ``jobs/directory.py``.
    """
    slug = (company or "").strip().lower()
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)

    jobs = _fetch_board(slug)
    if jobs is None:
        return None

    name = BOARDS.get(slug, company)
    terms = keywords.lower().split()
    postings = [
        posting
        for job in jobs
        if (posting := _to_posting(job, name, terms)) is not None
    ]
    return take_newest(postings, limit)


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
        return json_rows(resp.json(), "jobs")
    except Exception:
        return None


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
