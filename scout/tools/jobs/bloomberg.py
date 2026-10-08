"""Bloomberg job search, by scraping the Avature board behind careers.bloomberg.com.

``careers.bloomberg.com`` is a dead end — it redirects onto ``bloomberg.com``,
which answers a plain request with a bot challenge. The board itself lives on
Avature, is server-rendered, and has no protection at all, so this reads the
search results page directly.

Bloomberg publishes no posting date. Not in the listing, and not on the job page
either: there is no ``datePosted`` and no JSON-LD to read one from. The RSS feed
does carry one, but it is a *different* result set — capped at twenty items,
ignoring the offset, and overlapping the HTML results by well under half — so
joining the two would date some roles and silently mis-date others. Undated is
the honest answer, and it is shown the way ``google.py`` shows its own.

The location filter is applied here rather than asked for: the board renders a
"Location" facet, but it is decorative — every value returns the identical set.
"""

from __future__ import annotations

import html
import re
from functools import partial

from langchain_core.runnables import RunnableConfig

from ..registry import ToolRegistry
from . import fetch
from .posting import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    QueryResults,
    clamp_int,
    merge_queries,
)
from .relevance import is_ai_ml_role
from .unsent import render_unsent

SEARCH_URL = "https://bloomberg.avature.net/careers/SearchJobs/"
ORGANIZATION = "Bloomberg"

#: Matched against the rendered location text.
US = "united states"
#: Searched when the model passes no keywords.
DEFAULT_SEARCH = "machine learning"
#: Rows per page, fixed by the board; the per-page parameter is ignored.
API_PAGE_SIZE = 12
PAGES = 3

#: Shown instead of a date, so the model doesn't invent one.
NO_DATE_LABEL = "not published by Bloomberg"

_ARTICLE_RE = re.compile(r'<article class="article article--result".*?</article>', re.S)
_TITLE_RE = re.compile(
    r'article__header__text__title[^>]*>\s*<a class="link" href="([^"]+)">\s*(.*?)\s*</a>',
    re.S,
)
_LOCATION_RE = re.compile(r'<span class="list-item-location">(.*?)</span>', re.S)


def register(reg: ToolRegistry) -> None:
    @reg.tool
    # `config` is injected by LangChain and kept out of the schema: no Args: entry.
    def search_bloomberg_jobs(  # noqa: D417
        keywords: str = "", limit: int = DEFAULT_LIMIT, *, config: RunnableConfig
    ) -> str:
        """Search Bloomberg's careers site for US job openings relevant to the
        user's field (AI/ML engineering) and return each role's title and link.

        Bloomberg does not publish posting dates, so no date is available for
        these roles.

        Args:
            keywords: Optional search phrase. If empty, defaults to AI/ML roles
                ("machine learning").
            limit: Maximum number of roles to return.
        """
        postings = search(keywords, limit)
        if postings is None:
            return f"Couldn't reach {ORGANIZATION}'s careers site right now. Try again later."
        if not postings:
            return (
                f"No relevant {ORGANIZATION} roles found right now. "
                "Try again later or adjust your keywords."
            )
        return render_unsent(
            f"*Latest {ORGANIZATION} AI/ML roles — {{count}} found:*",
            postings,
            footer="_Bloomberg doesn't publish posting dates._",
            config=config,
        )


def search(keywords: str = "", limit: int = DEFAULT_LIMIT) -> list[JobPosting] | None:
    """Bloomberg's current US AI/ML openings, or None if the board is unreachable.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.

    Never re-sorted: with no dates, the board's own ordering is the only signal.
    One page arriving is enough to call the answer real.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    query = keywords.strip() or DEFAULT_SEARCH
    offsets = range(0, PAGES * API_PAGE_SIZE, API_PAGE_SIZE)
    found = merge_queries(offsets, partial(_postings_for, query))
    return None if found is None else found[:limit]


def _postings_for(query: str, offset: int) -> QueryResults:
    """One results page, as (url, posting) pairs, or None if it could not be read."""
    page = fetch.get_text(SEARCH_URL, _params(query, offset))
    if page is None:
        return None
    return [
        (posting.url, posting)
        for article in _ARTICLE_RE.findall(page)
        if (posting := _to_posting(article)) is not None
    ]


def _params(query: str, offset: int) -> dict[str, str | int]:
    """The query string for one results page."""
    return {"search": query, "jobOffset": offset}


def _to_posting(article: str) -> JobPosting | None:
    """Convert one rendered result, or None if it should be skipped."""
    found = _TITLE_RE.search(article)
    if found is None:
        return None
    url, title = found.group(1).strip(), html.unescape(found.group(2)).strip()
    if not is_ai_ml_role(title):
        return None
    location = _location(article)
    if US not in location.lower():
        return None
    return JobPosting(
        title=title,
        organization=ORGANIZATION,
        # Avature renders these absolute.
        url=url,
        location=location,
        posted_label=NO_DATE_LABEL,
    )


def _location(article: str) -> str:
    """The rendered location, or "" when the board omits one."""
    found = _LOCATION_RE.search(article)
    return html.unescape(found.group(1)).strip() if found else ""
