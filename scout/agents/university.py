"""University Agent: a job-search assistant for university (higher-ed) roles."""

from __future__ import annotations

from ..core.agent import AgentSpec
from ..tools import bu_jobs, datetime_tools, location, northeastern_jobs

SYSTEM_PROMPT = (
    "You are University Agent, a concise job-search assistant focused on roles at "
    "universities. If a candidate profile is provided, treat it as the user's "
    "background and tailor your searches and suggestions to it (use its search "
    "keywords when calling the job tools). Use your tools for live information — "
    "current date and time, the user's approximate location, and recent job "
    "openings at Northeastern University and Boston University. Each school has its "
    "own tool; call the one the user asks about (or both). Keep replies short and "
    "Slack-friendly."
)

SPEC = AgentSpec(
    key="university",
    name="University Agent",
    system_prompt=SYSTEM_PROMPT,
    tool_modules=[datetime_tools, location, northeastern_jobs, bu_jobs],
    default_backend="ollama",
)
