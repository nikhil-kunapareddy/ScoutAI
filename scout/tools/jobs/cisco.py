"""Cisco job search, via the widget endpoint careers.cisco.com renders itself from.

Cisco is no longer on Taleo — ``jobs.cisco.com`` redirects to a Phenom-backed
site whose search is one unauthenticated POST. It is a good source: a
server-side country filter, a real ISO timestamp per row, and a hundred rows in
a single request.

A Workday tenant exists behind it too (the apply links point there), but it is
strictly worse and deliberately unused: its dates are relative text and its
locations collapse to "2 Locations", so it can offer neither recency nor a
reliable US filter.

Like every keyword box in this package, Cisco's is fuzzy — it matches the whole
posting, so "machine learning" returns account executives — and every title is
re-gated through ``relevance.is_ai_ml_role``.
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
from .relevance import is_ai_ml_role

WIDGETS_URL = "https://careers.cisco.com/widgets"
JOB_BASE_URL = "https://careers.cisco.com/global/en/job"
ORGANIZATION = "Cisco"

US = "United States of America"       # the exact string the country facet wants
DEFAULT_SEARCH = "machine learning"   # when the model passes no keywords
API_PAGE_SIZE = 100


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_cisco_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Cisco's careers site for recent US job openings relevant to the
        user's field (AI/ML engineering) and return title, date posted, and link.

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
    """Cisco's recent US AI/ML openings, newest first, or None if unreachable.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    rows = _jobs(keywords.strip() or DEFAULT_SEARCH)
    if rows is None:
        return None
    postings = [
        posting for row in rows if (posting := _to_posting(row)) is not None
    ]
    return take_newest(postings, limit)


def _jobs(query: str) -> list[dict] | None:
    """The rows, or None if the widget could not be read.

    They sit two levels down, under the ``ddoKey`` the request asked for, so a
    body shaped any other way is the site answering with something that isn't
    its search.
    """
    body = fetch.post_json(WIDGETS_URL, _payload(query))
    if not isinstance(body, dict):
        return None
    refine = body.get("refineSearch")
    if not isinstance(refine, dict):
        return None
    data = refine.get("data")
    if not isinstance(data, dict):
        return None
    return fetch.json_rows(data, "jobs")


def _payload(query: str) -> dict[str, object]:
    return {
        "lang": "en_global",
        "deviceType": "desktop",
        "country": "global",
        "pageName": "search-results",
        "ddoKey": "refineSearch",
        "sortBy": "most_recent",
        "from": 0,
        "size": API_PAGE_SIZE,
        "jobs": True,
        "counts": True,
        "siteType": "external",
        "keywords": query,
        "global": True,
        "selected_fields": {"country": [US]},
        "locationData": {},
    }


def _to_posting(job: dict) -> JobPosting | None:
    """Convert one row, or None if it isn't an AI/ML role."""
    title = (job.get("title") or "").strip()
    if not is_ai_ml_role(title):
        return None
    seq_no = (job.get("jobSeqNo") or "").strip()
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=f"{JOB_BASE_URL}/{seq_no}" if seq_no else "",
        location=(job.get("location") or "").strip(),
        date=_parse_posted(job.get("postedDate")),
    )


def _parse_posted(raw: object) -> datetime | None:
    """Parse ``postedDate``, e.g. 2026-09-01T00:00:00.000+0000.

    ``strptime`` rather than ``fromisoformat``: the offset has no colon, which
    3.10 will not read.
    """
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        return None
