"""One opening, and the ways a pile of them becomes one answer.

Every source normalises its rows into a ``JobPosting``, which is what lets a
single renderer format a reply mixing several sources, and what lets a caller
searching many companies at once — the Referral Window — merge and count what
it got back.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

# Shared result-count bounds. Sources with a date window define their own days.
DEFAULT_LIMIT = 15
MAX_LIMIT = 25


@dataclass
class JobPosting:
    """One opening, normalized so every source renders the same."""

    title: str
    organization: str
    url: str
    location: str = ""
    date: datetime | None = None  # when the source publishes a real date
    posted_label: str = ""        # the source's own wording, when it doesn't

    @property
    def sort_key(self) -> float:
        """Recency key; undated postings sort last.

        A timestamp rather than the datetime, because sources mix tz-aware
        (Greenhouse, Netflix) and naive (Amazon, BU) dates, which can't compare.
        """
        return self.date.timestamp() if self.date else 0.0

    @property
    def posted_text(self) -> str:
        """What to show on the "Posted:" line."""
        if self.date:
            return f"{self.date:%b %d, %Y}"
        return self.posted_label or "not listed"


#: Every source module exposes its search twice: as a registered tool returning
#: Slack text, and as a ``search`` of this shape returning the postings
#: themselves. The second is what lets one caller search several sources and
#: merge the results — see ``jobs/directory.py``. ``None`` means the source could
#: not be reached, which is never the same answer as "nothing found".
Searcher = Callable[[str, int], list[JobPosting] | None]

#: One query's results, each paired with the id it de-dupes on. ``None`` means
#: that query never reached the source.
QueryResults = list[tuple[str, JobPosting]] | None


def clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    """Coerce to an int within bounds.

    Small models routinely pass ``limit="ten"`` or ``days=0``, and a tool that
    raises on junk input wastes a whole turn.
    """
    number = default
    if isinstance(value, int | float | str):
        try:
            number = int(value)
        except ValueError:  # "ten", "", "1.2.3"
            number = default
    return min(max(number, minimum), maximum)


def merge_queries(
    queries: Iterable[str], postings_for: Callable[[str], QueryResults]
) -> list[JobPosting] | None:
    """Run every query against one source and de-dupe the results, first wins.

    The sources with a keyword box are searched several times over — once per
    profile query — so the merge is the same everywhere and lives here.

    Every query failing means the source is down, which a caller may need to
    report differently from "the source has nothing"; one query getting through
    is enough to call the answer real.
    """
    found: dict[str, JobPosting] = {}
    reached = False
    for query in queries:
        results = postings_for(query)
        if results is None:
            continue
        reached = True
        for job_id, posting in results:
            found.setdefault(job_id, posting)
    return list(found.values()) if reached else None


def take_newest(postings: list[JobPosting], limit: int) -> list[JobPosting]:
    """Sort newest-first and take at most ``limit``."""
    return sorted(postings, key=lambda posting: posting.sort_key, reverse=True)[:limit]


def render_postings(header: str, postings: list[JobPosting], footer: str = "") -> str:
    """Format postings as a Slack message. ``header`` may use ``{count}``.

    One renderer for every source, since a single reply often mixes results
    from several tools.
    """
    lines = [header.format(count=len(postings)), ""]
    for i, posting in enumerate(postings, 1):
        lines.append(f"{i}. *{posting.title}*")
        lines.append(f"    Organization: {posting.organization}")
        if posting.location:
            lines.append(f"    Location: {posting.location}")
        lines.append(f"    Link: {posting.url}")
        lines.append(f"    Posted: {posting.posted_text}")
        lines.append("")
    if footer:
        lines.append(footer)
    return "\n".join(lines)
