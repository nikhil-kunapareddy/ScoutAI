"""Oracle job search, via the Oracle Cloud Recruiting board careers.oracle.com uses.

Oracle hosts its own board on its own product, and the endpoint is the best
behaved of the bespoke sources: one GET, no auth, a server-side US facet, a
server-side newest-first sort, and a real ISO date on every row.

Two things about it are unforgiving. The rows are nested a level down — ``items``
holds the *search*, and the postings hang off it as ``requisitionList`` — and
that key only appears if ``expand`` asked for it. Without ``expand`` the endpoint
still answers 200, with every count and facet intact and no postings at all,
which is exactly the silently-short list the Referral Window must never produce.
So a body whose rows are missing is treated as unreachable rather than empty.

The other is the query string. ``finder`` is Oracle's own mini-syntax, where the
``;`` and ``,`` are structure rather than data; handing it to ``requests`` as a
parameter dict re-encodes those and the filter quietly stops applying. It is
built here as a string and the URL is passed whole.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

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
from .relevance import is_ai_ml_role

API_URL = (
    "https://eeho.fa.us2.oraclecloud.com"
    "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
)
JOB_BASE_URL = "https://careers.oracle.com/en/sites/jobsearch/job"
ORGANIZATION = "Oracle"

SITE_NUMBER = "CX_45001"
US_FACET = "300000000149325"          # "United States"
DEFAULT_SEARCH = "machine learning"   # when the model passes no keywords
API_PAGE_SIZE = 200                   # one page covers the US AI/ML slice


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_oracle_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Oracle's careers site for recent US job openings relevant to
        the user's field (AI/ML engineering) and return title, date posted, and link.

        Args:
            keywords: Optional search phrase. If empty, defaults to AI/ML roles
                ("machine learning").
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
    """Oracle's recent US AI/ML openings, newest first, or None if unreachable.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    rows = _requisitions(keywords.strip() or DEFAULT_SEARCH)
    if rows is None:
        return None
    postings = [
        posting for row in rows if (posting := _to_posting(row)) is not None
    ]
    return take_newest(postings, limit)


def _requisitions(query: str) -> list[dict] | None:
    """The postings, or None if the board could not be read.

    A missing ``requisitionList`` means the request lost its ``expand`` — the
    endpoint answers 200 and simply omits the rows — so it is a failure to look,
    never a board with nothing on it.
    """
    body = fetch.get_json(_url(query))
    if not isinstance(body, dict):
        return None
    searches = fetch.json_rows(body, "items")
    if not searches:
        return None
    rows = searches[0].get("requisitionList")
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict)]


def _url(query: str) -> str:
    """The full URL, with ``finder`` built by hand so its syntax survives."""
    finder = (
        f"findReqs;siteNumber={SITE_NUMBER}"
        f",limit={API_PAGE_SIZE}"
        ",offset=0"
        f",keyword={quote(query, safe='')}"
        f",selectedLocationsFacet={US_FACET}"
        ",sortBy=POSTING_DATES_DESC"
    )
    return f"{API_URL}?onlyData=true&expand=requisitionList&finder={finder}"


def _to_posting(job: dict) -> JobPosting | None:
    """Convert one requisition, or None if it isn't an AI/ML role."""
    title = (job.get("Title") or "").strip()
    if not is_ai_ml_role(title):
        return None
    job_id = str(job.get("Id") or "").strip()
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=f"{JOB_BASE_URL}/{job_id}" if job_id else "",
        location=(job.get("PrimaryLocation") or "").strip(),
        date=_parse_posted(job.get("PostedDate")),
    )


def _parse_posted(raw: object) -> datetime | None:
    """Parse ``PostedDate``, a bare ISO day (e.g. "2026-09-18")."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d")
    except ValueError:
        return None
