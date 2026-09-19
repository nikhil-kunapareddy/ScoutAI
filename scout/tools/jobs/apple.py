"""Apple job search, by reading the state its careers page renders itself from.

Apple's search API — a CSRF token followed by a POST to ``/api/v1/search`` — is
retired for anonymous callers. It still answers 200 with a valid token and
simply reports nothing, which is the worst possible failure: it looks like an
empty board. Do not build on it.

What does work is the page itself. Apple server-renders its results and leaves
them in ``window.__staticRouterHydrationData``, so this reads that blob rather
than parsing markup — the rows come back as the same JSON the API would have
returned, including a real timestamp.

``sort=relevance`` is load-bearing and is sent on every page. Apple's default
ordering answers a search for "machine learning" with seasonal retail roles, and
the parameter is dropped rather than inherited when paging, so the source looks
broken if it is set once and forgotten.
"""

from __future__ import annotations

import json
import re
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

SEARCH_URL = "https://jobs.apple.com/en-us/search"
JOB_BASE_URL = "https://jobs.apple.com/en-us/details"
ORGANIZATION = "Apple"

US_LOCATION = "united-states-USA"     # Apple's own id for the US filter
DEFAULT_SEARCH = "machine learning"   # when the model passes no keywords
API_PAGE_SIZE = 20                    # fixed by the page
PAGES = 2

#: The hydration blob is a JS string literal holding JSON, so it needs decoding
#: twice — once out of the literal, once as JSON.
_STATE_RE = re.compile(
    r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("(.*?)"\);', re.S
)


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_apple_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Apple's careers site for recent US job openings relevant to the
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
    """Apple's recent US AI/ML openings, newest first, or None if unreachable.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.

    A role open in several cities is listed once per city, so results are
    de-duped on the position id. One page arriving is enough to call the answer
    real.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    query = keywords.strip() or DEFAULT_SEARCH

    found: dict[str, JobPosting] = {}
    reached = False
    for page in range(1, PAGES + 1):
        rows = _results_for(query, page)
        if rows is None:
            continue
        reached = True
        for row in rows:
            posting = _to_posting(row)
            if posting is not None:
                found.setdefault(str(row.get("positionId")), posting)
    return take_newest(list(found.values()), limit) if reached else None


def _results_for(query: str, page: int) -> list[dict] | None:
    """One results page, or None if it could not be read."""
    html = fetch.get_text(SEARCH_URL, _params(query, page))
    if html is None:
        return None
    state = _hydration_state(html)
    if state is None:
        return None
    search_data = (state.get("loaderData") or {}).get("search")
    if not isinstance(search_data, dict):
        return None
    return fetch.json_rows(search_data, "searchResults")


def _hydration_state(html: str) -> dict | None:
    """The page's hydration blob as an object, or None if it isn't there.

    A page without it is Apple serving something that isn't the search — a
    failure to look, not a report of nothing.
    """
    found = _STATE_RE.search(html)
    if found is None:
        return None
    try:
        state = json.loads(json.loads(f'"{found.group(1)}"'))
    except ValueError:
        return None
    return state if isinstance(state, dict) else None


def _params(query: str, page: int) -> dict[str, str | int]:
    # sort=relevance on every page: Apple drops it when paging, and without it
    # the results are retail roles that merely mention the search term.
    return {
        "search": query,
        "location": US_LOCATION,
        "sort": "relevance",
        "page": page,
    }


def _to_posting(job: dict) -> JobPosting | None:
    """Convert one search result, or None if it isn't an AI/ML role."""
    title = (job.get("postingTitle") or "").strip()
    if not is_ai_ml_role(title):
        return None
    position_id = str(job.get("positionId") or "").strip()
    slug = (job.get("transformedPostingTitle") or "").strip()
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        url=f"{JOB_BASE_URL}/{position_id}/{slug}" if position_id and slug else "",
        location=_location_text(job.get("locations")),
        date=_parse_posted(job.get("postDateInGMT")),
    )


def _location_text(locations: object) -> str:
    """Where the role is, from Apple's per-row location list."""
    if not isinstance(locations, list):
        return ""
    places = []
    for entry in locations:
        if isinstance(entry, dict):
            name = (entry.get("name") or "").strip()
            if name and name not in places:
                places.append(name)
    return "; ".join(places)


def _parse_posted(raw: object) -> datetime | None:
    """Parse ``postDateInGMT``, e.g. 2026-09-16T22:06:11.615Z.

    ``Z`` is normalised first: ``fromisoformat`` only learned to read it in 3.11,
    and this package supports 3.10.
    """
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
