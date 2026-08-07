"""
Registry of available agents.

To add an agent: create a module here that defines a ``SPEC`` (an ``AgentSpec``),
import it below, and add it to ``AGENTS``. Select which one runs with the
``AGENT`` env var (see ``scout.core.settings.ACTIVE_AGENT``).
"""

from __future__ import annotations

from ..core.agent import AgentSpec
from . import bigtech, resume_parser, university

AGENTS: dict[str, AgentSpec] = {
    bigtech.SPEC.key: bigtech.SPEC,
    resume_parser.SPEC.key: resume_parser.SPEC,
    university.SPEC.key: university.SPEC,
}


def get_spec(key: str) -> AgentSpec:
    """Look up an agent spec by key, or exit with a helpful message."""
    if key not in AGENTS:
        available = ", ".join(sorted(AGENTS))
        raise SystemExit(f"Unknown agent '{key}'. Available: {available}")
    return AGENTS[key]
