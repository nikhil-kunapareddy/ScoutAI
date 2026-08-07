"""Google job-search tool.

Scrapes Google's public careers listings (server-rendered HTML) for US openings
relevant to the user's field. Google does not publish posting dates anywhere, so
results are returned newest-first (via sort_by=date) as title + link, without a date.
"""

from __future__ import annotations

import re

import requests

from ..core import settings
from ._jobs import is_relevant
from .registry import ToolRegistry

GOOGLE_SEARCH_URL = "https://www.google.com/about/careers/applications/jobs/results/"
GOOGLE_JOB_BASE = "https://www.google.com/about/careers/applications/"

DEFAULT_LIMIT = 15
MAX_LIMIT = 25

# Search terms that represent the user's field (AI/ML engineering). Google's
# search is keyword-based, so we run several and merge the results.
PROFILE_QUERIES = [
    "machine learning engineer",
    "applied scientist",
    "AI engineer",
    "software engineer machine learning",
    "generative AI",
]

# Each job is a "Learn more" anchor: href to jobs/results/{id}-{slug}, with the
# clean title in the aria-label.
_JOB_RE = re.compile(
    r'href="(jobs/results/\d+-[^"?]+)[^"]*"[^>]*aria-label="Learn more about ([^"]+)"'
)


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_google_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Google's careers site for recent US job openings relevant to
        the user's field (AI/ML engineering) and return each role's title and link.

        Google does not publish posting dates, so roles are listed newest-first
        (no date is available).

        Args:
            keywords: Optional search phrase. If empty, uses the user's profile
                (machine learning / applied scientist / AI engineer / etc.).
            limit: Maximum number of roles to return.
        """
        # Be defensive: small models sometimes pass junk values.
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = min(max(limit, 1), MAX_LIMIT)

        queries = [keywords] if keywords.strip() else PROFILE_QUERIES
        headers = {"User-Agent": settings.TOOL_USER_AGENT}

        seen: dict[str, dict] = {}
        rows: list[dict] = []
        for q in queries:
            try:
                resp = requests.get(
                    GOOGLE_SEARCH_URL,
                    params={"q": q, "location": "United States", "sort_by": "date"},
                    headers=headers, timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
            except Exception:
                continue

            for href, title in _JOB_RE.findall(resp.text):
                title = title.strip()
                job_id = href.split("/")[2].split("-")[0]  # jobs/results/{id}-{slug}
                if job_id in seen or not is_relevant(title):
                    continue
                row = {"title": title, "url": GOOGLE_JOB_BASE + href}
                seen[job_id] = row
                rows.append(row)

        rows = rows[:limit]
        if not rows:
            return "No relevant Google roles found right now. Try again later."

        lines = [f"*Latest Google AI/ML roles (most recent first) — {len(rows)} found:*", ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. *{r['title']}*")
            lines.append("    Organization: Google")
            lines.append(f"    Link: {r['url']}")
            lines.append("    Posted: not published by Google")
            lines.append("")
        lines.append("_Google doesn't publish posting dates; roles are listed newest-first._")
        return "\n".join(lines)
