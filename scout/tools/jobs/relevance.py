"""Which roles count, and what to search for when the model says nothing.

Every source filters on the title. The ones with a keyword box return whatever
matched anywhere in the posting — sales roles that mention AI — and the ones
without return their whole board, so the same title filter has to sit in front
of both.
"""

from __future__ import annotations

import re

#: Title phrases that mark a role as one worth showing. Lower case, because the
#: title is lower-cased before matching. Broad keyword searches drag in sales,
#: hardware and PM roles that merely mention AI; this strips them.
AI_ML_PHRASES = (
    "ai engineer",
    "artificial intelligence engineer",
    "data analyst",
    "analyst",
    "machine learning",
    "applied artificial intelligence engineer",
    "data scientist",
    "software engineer",
    "backend software engineer",
    "software engineer 1",
    "software engineer 2",
    "software engineer i",
    "software engineer ii",
)

#: Title words and phrases that rule a role out, whatever else the title says:
#: an excluded title is dropped even when it also matches ``AI_ML_PHRASES``.
#: Matched as whole words, so "staff" does not drop "Staffing Coordinator".
EXCLUDED_PHRASES = (
    "member of technical staff",
    "senior",
    "staff",
    "architect",
    "principal",
    "director",
    "distinguished",
    "manager",
)

#: Standalone title words that mark a role as AI/ML. Matched as whole words, so
#: "ai" does not match "maintenance".
AI_ML_TOKENS = frozenset({"ai", "ml", "llm", "nlp"})

#: Stand-ins for "the user's field", used when the model passes no keywords.
PROFILE_QUERIES = (
    "machine learning engineer",
    "applied scientist",
    "AI engineer",
    "software engineer machine learning",
    "generative AI",
)

_WORD_RE = re.compile(r"[a-z0-9]+")
_EXCLUDED_RE = re.compile(r"\b(?:" + "|".join(map(re.escape, EXCLUDED_PHRASES)) + r")\b")


def is_excluded(title: str) -> bool:
    """True if the title carries a word or phrase from ``EXCLUDED_PHRASES``."""
    return _EXCLUDED_RE.search(title.lower()) is not None


def is_ai_ml_role(title: str) -> bool:
    """True if the title is a wanted role: not excluded, and matching a phrase or token."""
    if is_excluded(title):
        return False
    lowered = title.lower()
    if any(phrase in lowered for phrase in AI_ML_PHRASES):
        return True
    return not AI_ML_TOKENS.isdisjoint(_WORD_RE.findall(lowered))


def matches_keywords(title: str, terms: list[str]) -> bool:
    """True if the title contains any term (or there are no terms).

    For sources with no server-side search, where filtering happens on titles.
    """
    if not terms:
        return True
    lowered = title.lower()
    return any(term in lowered for term in terms)


def search_queries(keywords: str) -> tuple[str, ...]:
    """The searches to run: the caller's phrase, or the user's field by default."""
    phrase = keywords.strip()
    return (phrase,) if phrase else PROFILE_QUERIES
