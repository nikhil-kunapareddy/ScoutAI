"""Registry of available agents.

To add one: create a module here defining ``SPEC`` (an ``AgentSpec``), import it
below, and add it to ``AGENTS``. Pick which runs with the ``AGENT`` env var.

``build_agent`` is the single place that decides *how* a spec runs — directly, or
behind the resume-parser hand-off.
"""

from __future__ import annotations

from ..core.agent import Agent, AgentSpec, ConversationalAgent
from . import bigtech, resume_parser, university
from .resume_tailored import ResumeTailoredAgent

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


def build_agent(spec: AgentSpec) -> ConversationalAgent:
    """Build the runnable agent for ``spec``.

    Job-search agents ask to be tailored to the user's resume, which means
    running them behind the parser hand-off; everything else runs standalone.
    Both satisfy ``ConversationalAgent``, so callers needn't know which.
    """
    if spec.tailor_with_resume:
        return ResumeTailoredAgent(spec)
    return Agent(spec)


__all__ = ["AGENTS", "ResumeTailoredAgent", "build_agent", "get_spec"]
