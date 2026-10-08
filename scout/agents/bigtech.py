"""BigTech Agent: job search for AI/ML roles at big tech companies."""

from __future__ import annotations

from ..core.agent import AgentSpec
from ..tools import clock, location
from ..tools.jobs import (
    amazon,
    apple,
    ashby,
    bloomberg,
    cisco,
    google,
    greenhouse,
    lenovo,
    microsoft,
    netflix,
    oracle,
    smartrecruiters,
    uber,
    workday,
)

SYSTEM_PROMPT = (
    "You are BigTech Agent, a concise job-search assistant focused on AI/ML roles "
    "at big tech companies. A candidate profile prepared by the Resume Parser agent "
    "is appended below — treat it as the user's background and tailor your "
    "searches and suggestions to it (use its search keywords when calling the job "
    "tools). Use your tools for live information — current date and time, the user's "
    "approximate location, and recent job openings. "
    "Each company has one tool: Amazon, Google, Netflix, Lenovo, Microsoft, Apple, "
    "Oracle, Uber, Cisco and Bloomberg have their own; the rest are reached by "
    "passing a company name to a shared tool — search_greenhouse_jobs (databricks, "
    "airbnb, stripe, pinterest, reddit, coinbase, dropbox, robinhood, anthropic, "
    "doordashusa), search_ashby_jobs (whoop, openai, snowflake), "
    "search_smartrecruiters_jobs (servicenow), and search_workday_jobs (nvidia, "
    "salesforce, adobe). "
    "Two sources cannot give you a posting date: the Workday companies report only "
    "how long ago a role went up, and Bloomberg publishes nothing at all. Repeat "
    "what the tool tells you and never invent a date. "
    "You do not see the user's referral list and never rank by it: rank on fit "
    "and recency alone. Who they know somewhere is the Referral Window's "
    "question, and it answers it across the whole list in one pass. "
    "Keep replies short and Slack-friendly."
)

#: Companies with a careers site of their own, one tool each.
_COMPANY_SOURCES = (
    amazon,
    google,
    netflix,
    lenovo,
    microsoft,
    apple,
    oracle,
    uber,
    cisco,
    bloomberg,
)

#: Platforms that host many companies' boards; each tool takes a company name.
_PLATFORM_SOURCES = (greenhouse, ashby, smartrecruiters, workday)

SPEC = AgentSpec(
    key="bigtech",
    name="BigTech Agent",
    system_prompt=SYSTEM_PROMPT,
    # No referral tool, not even referrals_read: job agents rank on fit and
    # recency, and the referral list is the Referral Window's scope.
    tool_modules=[clock, location, *_COMPANY_SOURCES, *_PLATFORM_SOURCES],
    # Supplies the candidate profile the prompt refers to.
    tailor_with_resume=True,
    # Sweeps every source into its own morning DM.
    in_digest=True,
)
