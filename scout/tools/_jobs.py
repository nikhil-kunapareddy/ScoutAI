"""Shared helpers for the job-search tools (Amazon, Google).

The relevance filtering is identical across providers, so it lives here. The
per-provider search queries differ slightly (e.g. Amazon's "software development
engineer" vs Google's "software engineer"), so those stay in each tool module.
"""

from __future__ import annotations

import re

# Title phrases / tokens that mark a role as relevant to the user's field
# (AI/ML engineering). Broad keyword searches drag in finance, supply-chain,
# hardware, PM, and sales noise; this strips it.
RELEVANT_TITLE_PHRASES = (
    "machine learning", "applied scientist", "research scientist",
    "research engineer", "data scientist", "data science", "deep learning",
    "generative", "genai", "recommendation", "agentic", "personalization",
    "conversational",
)
RELEVANT_TITLE_TOKENS = {"ai", "ml", "llm", "nlp"}


def is_relevant(title: str) -> bool:
    """True if ``title`` looks like an AI/ML role by phrase or standalone token."""
    low = title.lower()
    if any(phrase in low for phrase in RELEVANT_TITLE_PHRASES):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", low))
    return bool(tokens & RELEVANT_TITLE_TOKENS)
