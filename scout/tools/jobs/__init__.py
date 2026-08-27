"""Job-search tools, one module per source, plus the pieces they all share.

Each source gets its own tool because the platforms differ too much to share an
implementation: JSON search APIs (Amazon, Netflix), scraped HTML (Google), a
board API (Greenhouse), Workday (Northeastern), RSS (Boston University).

What every source *does* share lives here: the AI/ML title filter, argument
clamping, and one Slack output format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# Title phrases and tokens that mark a role as AI/ML. Broad keyword searches drag
# in finance, supply-chain, hardware, PM and sales roles; this strips them.
AI_ML_PHRASES = (
    "machine learning", "applied scientist", "research scientist",
    "research engineer", "data scientist", "data science", "deep learning",
    "generative", "genai", "recommendation", "agentic", "personalization",
    "conversational",
)
AI_ML_TOKENS = {"ai", "ml", "llm", "nlp"}

# Stand-ins for "the user's field", used when the model passes no keywords.
PROFILE_QUERIES = (
    "machine learning engineer",
    "applied scientist",
    "AI engineer",
    "software engineer machine learning",
    "generative AI",
)

# Shared result-count bounds. Sources with a date window define their own days.
DEFAULT_LIMIT = 15
MAX_LIMIT = 25


def is_ai_ml_role(title: str) -> bool:
    """True if the title looks like an AI/ML role, by phrase or standalone token."""
    low = title.lower()
    if any(phrase in low for phrase in AI_ML_PHRASES):
        return True
    return bool(set(re.findall(r"[a-z0-9]+", low)) & AI_ML_TOKENS)


def clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    """Coerce to an int within bounds.

    Small models routinely pass ``limit="ten"`` or ``days=0``, and a tool that
    raises on junk input wastes a whole turn.
    """
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return min(max(number, minimum), maximum)


def search_queries(keywords: str) -> tuple[str, ...]:
    """The searches to run: the caller's phrase, or the user's field by default."""
    return (keywords.strip(),) if keywords.strip() else PROFILE_QUERIES


def matches_keywords(title: str, terms: list[str]) -> bool:
    """True if the title contains any term (or there are no terms).

    For sources with no server-side search, where filtering happens on titles.
    """
    if not terms:
        return True
    low = title.lower()
    return any(term in low for term in terms)


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


def take_newest(postings: list[JobPosting], limit: int) -> list[JobPosting]:
    """Sort newest-first and take at most ``limit``."""
    return sorted(postings, key=lambda p: p.sort_key, reverse=True)[:limit]


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
