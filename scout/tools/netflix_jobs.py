"""Netflix job-search tool.

Queries Netflix's public careers API (the Eightfold-backed JSON endpoint the
explore.jobs.netflix.net site uses) for recent US openings relevant to the
user's field. Unlike the Amazon/Google tools, this API does keyword matching,
US-location filtering, and recency sorting server-side, so we hand it the
search terms directly and format what comes back."""

from __future__ import annotations

from datetime import datetime

import requests

from ..core import settings
from ._jobs import is_relevant
from .registry import ToolRegistry

NETFLIX_SEARCH_URL = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
NETFLIX_JOB_BASE = "https://explore.jobs.netflix.net/careers/job/"

DEFAULT_LIMIT = 15
MAX_LIMIT = 25
API_RESULT_LIMIT = 50  # rows requested per query before relevance filtering

# Search terms that represent the user's field (AI/ML engineering). The API is
# keyword-based, so we run several and merge, matching the Amazon/Google tools.
PROFILE_QUERIES = [
    "machine learning engineer",
    "applied scientist",
    "AI engineer",
    "software engineer machine learning",
    "generative AI",
]


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_netflix_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Netflix's careers site for recent US job openings relevant to
        the user's field (AI/ML engineering) and return title, date posted, and link.

        Roles are listed newest-first.

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
        headers = {"User-Agent": settings.TOOL_USER_AGENT, "Accept": "application/json"}

        seen: dict[str, dict] = {}
        for q in queries:
            try:
                resp = requests.get(
                    NETFLIX_SEARCH_URL,
                    params={"domain": "netflix.com", "query": q, "location": "United States",
                            "sort_by": "timestamp", "num": API_RESULT_LIMIT, "start": 0},
                    headers=headers, timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
                positions = resp.json().get("positions", [])
            except Exception:
                continue

            for p in positions:
                job_id = str(p.get("id") or "")
                title = (p.get("name") or "").strip()
                if not job_id or job_id in seen or not is_relevant(title):
                    continue
                ts = p.get("t_create")
                seen[job_id] = {
                    "title": title,
                    "location": (p.get("location") or "").strip(),
                    # t_create is a unix timestamp (seconds); may be missing.
                    "date": datetime.utcfromtimestamp(ts) if ts else None,
                    "url": p.get("canonicalPositionUrl") or (NETFLIX_JOB_BASE + job_id),
                }

        # Newest-first; roles without a date sort last.
        rows = sorted(seen.values(),
                      key=lambda r: r["date"] or datetime.min, reverse=True)[:limit]
        if not rows:
            return "No relevant Netflix roles found right now. Try again later or widen your keywords."

        lines = [f"*Latest Netflix AI/ML roles (most recent first) — {len(rows)} found:*", ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. *{r['title']}*")
            lines.append("    Organization: Netflix")
            if r["location"]:
                lines.append(f"    Location: {r['location']}")
            lines.append(f"    Link: {r['url']}")
            lines.append(f"    Posted: {r['date']:%b %d, %Y}" if r["date"] else "    Posted: not listed")
            lines.append("")
        return "\n".join(lines)
