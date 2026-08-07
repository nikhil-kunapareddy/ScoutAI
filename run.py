"""
Entry point for the Scout agent platform.

By default (AGENT=bigtech) runs the resume-parser → job-search pipeline: the
Resume Parser distills the resume into a profile, which is handed off to the
BigTech job agent. Any other AGENT value runs that single agent standalone
(e.g. AGENT=resume to test the parser on its own).
    python run.py
"""

from __future__ import annotations

from scout.agents import get_spec
from scout.core import settings
from scout.slack.app import run_agent, run_pipeline

if __name__ == "__main__":
    if settings.ACTIVE_AGENT == "bigtech":
        run_pipeline()
    else:
        run_agent(get_spec(settings.ACTIVE_AGENT))
