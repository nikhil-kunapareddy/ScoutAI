"""Which roles count, and what to search for when the model says nothing.

Every source filters on the title. The ones with a keyword box return whatever
matched anywhere in the posting — sales roles that mention AI — and the ones
without return their whole board, so the same title filter has to sit in front
of both.
"""

from __future__ import annotations

import re

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

_WORD_RE = re.compile(r"[a-z0-9]+")


def is_ai_ml_role(title: str) -> bool:
    """True if the title looks like an AI/ML role, by phrase or standalone token."""
    low = title.lower()
    if any(phrase in low for phrase in AI_ML_PHRASES):
        return True
    return bool(set(_WORD_RE.findall(low)) & AI_ML_TOKENS)


def matches_keywords(title: str, terms: list[str]) -> bool:
    """True if the title contains any term (or there are no terms).

    For sources with no server-side search, where filtering happens on titles.
    """
    if not terms:
        return True
    low = title.lower()
    return any(term in low for term in terms)


def search_queries(keywords: str) -> tuple[str, ...]:
    """The searches to run: the caller's phrase, or the user's field by default."""
    return (keywords.strip(),) if keywords.strip() else PROFILE_QUERIES
