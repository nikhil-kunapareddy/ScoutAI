"""Amazon job-search tool.

Queries Amazon's public careers search API (the same JSON endpoint the
amazon.jobs site uses) for recent US openings relevant to the user's field,
and returns them formatted as Slack links with the date posted."""

from __future__ import annotations

from datetime import datetime, timedelta

import requests

from ..core import settings
from ._jobs import is_relevant
from .registry import ToolRegistry

AMAZON_SEARCH_URL = "https://www.amazon.jobs/en/search.json"

DEFAULT_DAYS = 1     # last 24h
MAX_DAYS = 30
DEFAULT_LIMIT = 15
MAX_LIMIT = 25
API_RESULT_LIMIT = 100  # rows requested per query before relevance/recency filtering

# Search terms that represent the user's field (AI/ML engineering). Amazon's
# search is keyword-based, so we run several and merge the results.
PROFILE_QUERIES = [
    "machine learning engineer",
    "applied scientist",
    "AI engineer",
    "software development engineer machine learning",
    "generative AI",
]


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_amazon_jobs(
        keywords: str = "", days: int = DEFAULT_DAYS, limit: int = DEFAULT_LIMIT
    ) -> str:
        """Search Amazon's careers site for recent US job openings relevant to
        the user's field (AI/ML engineering) and return title, date posted, and link.

        Args:
            keywords: Optional search phrase. If empty, uses the user's profile
                (machine learning / applied scientist / AI engineer / etc.).
            days: Only include roles posted within this many days (default 1 = last 24h).
            limit: Maximum number of roles to return.
        """
        # Be defensive: small models often pass junk values (days=0, strings).
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = DEFAULT_DAYS
        days = min(max(days, 1), MAX_DAYS)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = min(max(limit, 1), MAX_LIMIT)

        queries = [keywords] if keywords.strip() else PROFILE_QUERIES
        cutoff = datetime.now() - timedelta(days=days)
        headers = {"User-Agent": settings.TOOL_USER_AGENT, "Accept": "application/json"}

        seen: dict[str, dict] = {}
        for q in queries:
            try:
                resp = requests.get(
                    AMAZON_SEARCH_URL,
                    params={"base_query": q, "normalized_country_code[]": "USA",
                            "sort": "recent", "result_limit": API_RESULT_LIMIT, "offset": 0},
                    headers=headers, timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
            except Exception:
                continue

            for j in jobs:
                try:
                    posted = datetime.strptime(" ".join(j["posted_date"].split()), "%B %d, %Y")
                except Exception:
                    continue
                if posted < cutoff or j.get("is_intern"):
                    continue
                title = j["title"].strip()
                if not is_relevant(title):
                    continue
                seen.setdefault(j["id"], {
                    "title": title,
                    "date": posted,
                    "url": "https://www.amazon.jobs" + j["job_path"],
                })

        rows = sorted(seen.values(), key=lambda r: r["date"], reverse=True)[:limit]
        if not rows:
            window = "last 24 hours" if days == 1 else f"last {days} days"
            return (f"No relevant Amazon roles found in the {window}. "
                    "Try again later or widen the window.")

        window = "last 24h" if days == 1 else f"last {days} days"
        lines = [f"*Latest Amazon AI/ML roles ({window}) — {len(rows)} found:*", ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. *{r['title']}*")
            lines.append("    Organization: Amazon")
            lines.append(f"    Link: {r['url']}")
            lines.append(f"    Posted: {r['date']:%b %d, %Y}")
            lines.append("")
        return "\n".join(lines)
