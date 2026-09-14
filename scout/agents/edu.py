"""Edu Agent: job search for roles at educational institutions."""

from __future__ import annotations

from ..core.agent import AgentSpec
from ..tools import clock, location
from ..tools.jobs import boston_university, northeastern

SYSTEM_PROMPT = (
    "You are Edu Agent, a concise job-search assistant focused on roles at "
    "educational institutions. A candidate profile prepared by the Resume Parser "
    "agent is appended below — treat it as the user's background and tailor your "
    "searches and suggestions to it (use its search keywords when calling the job "
    "tools). Use your tools for live information — current date and time, the user's "
    "approximate location, and recent job openings at Northeastern University and "
    "Boston University. Each school has its own tool; call the one the user asks "
    "about (or both). Keep replies short and Slack-friendly."
)

SPEC = AgentSpec(
    key="edu",
    name="Edu Agent",
    system_prompt=SYSTEM_PROMPT,
    tool_modules=[clock, location, northeastern, boston_university],
    tailor_with_resume=True,  # supplies the candidate profile the prompt expects
)  # default_backend omitted: inherits settings.DEFAULT_BACKEND (Claude, else Ollama)
