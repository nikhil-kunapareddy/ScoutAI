"""A careers-board platform that hosts many companies.

Greenhouse and Ashby are not two job sources; they are one shape written twice —
a board per company at a predictable URL, no server-side filtering, one JSON row
per opening. This holds the shape: the slug lookup, the fetch, the title
filtering, and the three answers such a tool has to tell apart (company we don't
host, board that was down, board with nothing matching). A platform module fills
in only what differs: where its boards live, and how to read one of its rows.

Adding a company is a line in that module's ``BOARDS``. Adding a platform is one
more ``HostedBoard`` — no change here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from . import fetch
from .posting import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    Searcher,
    clamp_int,
    render_postings,
    take_newest,
)

#: Reads one board row into a posting, or None to skip it. ``organization`` is
#: the company's display name and ``terms`` the lower-cased words the caller
#: narrowed by — both already resolved, so a platform only parses.
RowReader = Callable[[dict, str, list[str]], JobPosting | None]


@dataclass(frozen=True)
class HostedBoard:
    """One board platform: where its boards are, and how to read a row."""

    platform: str          # display name, for the unknown-company message
    board_url: str         # formatted with the board slug
    rows_key: str          # the JSON key the openings sit under
    boards: dict[str, str] # board slug -> company display name
    read_row: RowReader

    def search(
        self, company: str, keywords: str = "", limit: int = DEFAULT_LIMIT
    ) -> list[JobPosting] | None:
        """One board's AI/ML openings, newest first, or None if unreachable.

        ``company`` is a board slug — a key of ``boards``; an unknown one simply
        finds no board and reads as unreachable. The postings rather than the
        rendered text, so a caller searching several companies at once can merge
        and count them — see ``jobs/directory.py``.
        """
        slug = _slug(company)
        rows = fetch.get_rows(self.board_url.format(slug=slug), self.rows_key)
        if rows is None:
            return None

        organization = self.boards.get(slug, company)
        terms = keywords.lower().split()
        postings = [
            posting
            for row in rows
            if (posting := self.read_row(row, organization, terms)) is not None
        ]
        return take_newest(postings, clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT))

    def searcher(self, slug: str) -> Searcher:
        """This platform's ``Searcher`` for one company. See ``jobs/directory.py``."""
        return partial(self.search, slug)

    def answer(self, company: str, keywords: str, limit: int) -> str:
        """The reply a tool returns: the roles, or which gap it hit."""
        slug = _slug(company)
        if slug not in self.boards:
            supported = ", ".join(sorted(self.boards))
            return (f"Unknown company '{company}'. "
                    f"Supported {self.platform} companies: {supported}.")

        name = self.boards[slug]
        postings = self.search(slug, keywords, limit)
        if postings is None:
            return f"Couldn't reach {name}'s careers board right now. Try again later."
        if not postings:
            return (f"No relevant {name} roles found right now. "
                    "Try again later or adjust your keywords.")
        return render_postings(
            f"*Latest {name} AI/ML roles (most recent first) — {{count}} found:*", postings
        )


def _slug(company: str) -> str:
    """The board key for whatever the model passed as a company name."""
    return (company or "").strip().lower()
