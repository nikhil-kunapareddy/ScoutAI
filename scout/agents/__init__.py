"""Registry of available agents.

To add one: create a module here defining ``SPEC`` (an ``AgentSpec``), import it
below, and add its ``SPEC`` to ``_SPECS``. Pick which runs with ``scout run --agent KEY``
or the ``AGENT`` env var.

``build_agent`` is the single place that decides *how* a spec runs — directly, or
behind the resume-parser hand-off.
"""

from __future__ import annotations

from ..core.agent import AgentSpec, ConversationalAgent
from ..core.runner import Agent
from . import bigtech, edu, referral, resume_parser
from .resume_tailored import ResumeTailoredAgent

__all__ = ["AGENTS", "ResumeTailoredAgent", "build_agent", "get_spec"]

#: The agents Scout ships. Adding one is one line here.
_SPECS: tuple[AgentSpec, ...] = (
    bigtech.SPEC,
    edu.SPEC,
    referral.SPEC,
    resume_parser.SPEC,
)

#: Every registered agent, by key. The CLI, the Slack bot and the digest all
#: resolve their agent here. Keyed off each spec so a key cannot drift from it.
AGENTS: dict[str, AgentSpec] = {spec.key: spec for spec in _SPECS}


def get_spec(key: str) -> AgentSpec:
    """Look up an agent spec by key, or exit naming the keys that exist.

    Args:
        key: The agent's registry key, e.g. ``"bigtech"``.
    """
    spec = AGENTS.get(key)
    if spec is None:
        available = ", ".join(sorted(AGENTS))
        raise SystemExit(f"Unknown agent {key!r}. Available: {available}")
    return spec


def build_agent(spec: AgentSpec) -> ConversationalAgent:
    """Build the runnable agent for ``spec``.

    A spec with ``tailor_with_resume`` runs behind the parser hand-off, which
    adds the resume profile to its instructions; everything else runs
    standalone. Both satisfy ``ConversationalAgent``, so callers needn't know
    which.

    Args:
        spec: The agent to build.
    """
    if spec.tailor_with_resume:
        return ResumeTailoredAgent(spec)
    return Agent(spec)
