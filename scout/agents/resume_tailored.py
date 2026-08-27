"""The resume-parser → job-search hand-off.

``ResumeTailoredAgent`` composes two plain ``Agent``s into one
``ConversationalAgent``:

    resume_parser  ──CandidateProfile──▶  job agent (bigtech, university, …)

The parser runs once per user; the rendered brief is cached and prepended to
every later job-search turn. Job agents therefore never read the resume
themselves — the profile only reaches them through this hand-off.

Opting in is one flag on an ``AgentSpec``: ``tailor_with_resume=True``.
"""

from __future__ import annotations

import logging

from ..core.agent import Agent, AgentSpec, ConversationalAgent
from .resume_parser import SPEC as RESUME_SPEC
from .resume_parser import parse_profile

log = logging.getLogger("scout")

#: What we ask the parser for; its system prompt does the real work.
_PARSE_REQUEST = "Extract my candidate profile."


class ResumeTailoredAgent(ConversationalAgent):
    """Runs ``job_spec`` with every turn prefixed by the user's resume profile."""

    def __init__(self, job_spec: AgentSpec) -> None:
        self.name = f"{job_spec.name} (resume-tailored)"
        self._parser = Agent(RESUME_SPEC)
        self._jobs = Agent(job_spec)
        # user_id -> rendered brief. dict get/set is atomic under the GIL; a
        # same-user race just re-parses, and Agent serializes each user's turns.
        self._briefs: dict[str, str] = {}

    def respond(self, user_id: str, prompt: str) -> str:
        brief = self._brief_for(user_id)
        return self._jobs.respond(user_id, f"{brief}\n\nUser request: {prompt}")

    def _brief_for(self, user_id: str) -> str:
        """The user's cached brief, parsing their resume on first use."""
        brief = self._briefs.get(user_id)
        if brief is None:
            raw = self._parser.respond(user_id, _PARSE_REQUEST)
            brief = parse_profile(raw).to_search_brief()
            self._briefs[user_id] = brief
            log.info("Parsed resume profile for %s", user_id)
        return brief

    def reset(self, user_id: str) -> None:
        self._parser.reset(user_id)
        self._jobs.reset(user_id)
        self._briefs.pop(user_id, None)  # re-parse on the next message

    def set_backend(self, user_id: str, name: str) -> bool:
        # Both stages move together, so a mid-conversation switch can't leave the
        # pipeline half on one model.
        self._parser.set_backend(user_id, name)
        return self._jobs.set_backend(user_id, name)

    def backend_name(self, user_id: str) -> str:
        return self._jobs.backend_name(user_id)

    def backend_label(self, user_id: str) -> str:
        return self._jobs.backend_label(user_id)

    def last_backend(self, user_id: str) -> str:
        # The job stage produces the visible reply and both share a backend, so
        # its view is the one worth reporting.
        return self._jobs.last_backend(user_id)

    def tool_names(self) -> list[str]:
        # The parser's only tool is the resume reader; the job tools are the ones
        # worth logging at start-up.
        return self._jobs.tool_names()
