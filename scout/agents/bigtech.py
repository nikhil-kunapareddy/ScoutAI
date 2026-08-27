"""BigTech Agent: job search for AI/ML roles at big tech companies."""

from __future__ import annotations

from ..core.agent import AgentSpec
from ..tools import clock, location
from ..tools.jobs import amazon, google, greenhouse, netflix

SYSTEM_PROMPT = (
    "You are BigTech Agent, a concise job-search assistant focused on AI/ML roles "
    "at big tech companies. Each turn begins with a candidate profile prepared by "
    "the Resume Parser agent — treat it as the user's background and tailor your "
    "searches and suggestions to it (use its search keywords when calling the job "
    "tools). Use your tools for live information — current date and time, the user's "
    "approximate location, and recent job openings at Amazon, Google, Netflix, and "
    "Greenhouse-hosted companies (Databricks, Airbnb, Stripe, Pinterest, Reddit, "
    "Coinbase, Dropbox, Robinhood — pass the company name to search_greenhouse_jobs). "
    "Keep replies short and Slack-friendly."
)

SPEC = AgentSpec(
    key="bigtech",
    name="BigTech Agent",
    system_prompt=SYSTEM_PROMPT,
    tool_modules=[clock, location, amazon, google, netflix, greenhouse],
    tailor_with_resume=True,  # supplies the candidate profile the prompt expects
)  # default_backend omitted: inherits settings.DEFAULT_BACKEND (Claude, else Ollama)
