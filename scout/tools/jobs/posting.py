"""One opening, and the ways a pile of them becomes one answer.

Every source normalises its rows into a ``JobPosting``, which is what lets a
single renderer format a reply mixing several sources, and what lets a caller
searching many companies at once — the Referral Window — merge and count what
it got back.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeVar

#: Shared result-count bounds. Sources with a date window define their own days.
DEFAULT_LIMIT = 15
MAX_LIMIT = 25

#: Whatever one request to a source is keyed by: a search phrase, a page number.
Query = TypeVar("Query")


@dataclass
class JobPosting:
    """One opening, normalized so every source renders the same."""

    title: str
    organization: str
    url: str
    location: str = ""
    #: When the source publishes a real date.
    date: datetime | None = None
    #: The source's own wording, when it doesn't.
    posted_label: str = ""
    #: The source's own id for the opening, namespaced by ``source_id``.
    job_id: str = ""

    @property
    def key(self) -> str | None:
        """What a digest remembers this opening by, or None if nothing is stable.

        The source's id where it has one, the link where it doesn't. A posting
        with neither can't be told from the next one, so it is never hidden and
        never recorded.
        """
        if self.job_id:
            return self.job_id
        return f"url:{self.url}" if self.url else None

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


def source_id(source: str, native: object) -> str:
    """A ``job_id`` namespaced by source, e.g. ``amazon:2876543``, or "" without one.

    The prefix keeps two sources' ids apart: Oracle's 344271 and another board's
    344271 are different openings.
    """
    text = str(native or "").strip()
    return f"{source}:{text}" if text else ""


def clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    """Coerce to an int within bounds.

    The model will sometimes pass ``limit="ten"`` or ``days=0``, and a tool that
    raises on junk input wastes a whole turn.
    """
    try:
        number = int(value) if isinstance(value, int | float | str) else default
    except ValueError:  # "ten", "", "1.2.3"
        number = default
    return min(max(number, minimum), maximum)


def merge_queries(
    queries: Iterable[Query], postings_for: Callable[[Query], QueryResults]
) -> list[JobPosting] | None:
    """Run every query against one source and de-dupe the results, first wins.

    A query is whatever one request is keyed by: a profile phrase for the sources
    searched several times over, or a page for the ones read a page at a time.
    Either way the merge is the same, so it lives here.

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
    for rank, posting in enumerate(postings, 1):
        lines.append(f"{rank}. *{posting.title}*")
        lines.append(f"    Organization: {posting.organization}")
        if posting.location:
            lines.append(f"    Location: {posting.location}")
        lines.append(f"    Link: {posting.url}")
        lines.append(f"    Posted: {posting.posted_text}")
        lines.append("")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def parse_iso_timestamp(raw: object) -> datetime | None:
    """Parse an ISO 8601 timestamp, or None if ``raw`` isn't one.

    ``Z`` is normalised first: ``fromisoformat`` only learned to read it in 3.11,
    and this package supports 3.10.
    """
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_unix_timestamp(raw: object) -> datetime | None:
    """Parse Unix seconds as a UTC datetime, or None if ``raw`` isn't one.

    ``bool`` is refused although Python counts it as an int, and a value too large
    for the platform's clock reads as missing rather than raising.
    """
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    try:
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
