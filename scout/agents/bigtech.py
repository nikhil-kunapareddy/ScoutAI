"""BigTech Agent: a job-search assistant for big-tech AI/ML roles."""

from __future__ import annotations

from ..core.agent import AgentSpec
from ..tools import (
    amazon_jobs,
    datetime_tools,
    google_jobs,
    greenhouse_jobs,
    location,
    netflix_jobs,
)

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
    tool_modules=[datetime_tools, location, amazon_jobs, google_jobs,
                  netflix_jobs, greenhouse_jobs],
    default_backend="ollama",
)
