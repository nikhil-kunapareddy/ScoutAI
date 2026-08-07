"""Generic Greenhouse job-search tool.

Many big/large tech companies host their careers on Greenhouse, which exposes a
clean public JSON board API:

    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs

One implementation therefore covers every Greenhouse company — adding an org is a
one-line entry in ``GREENHOUSE_BOARDS`` below. The board API returns *all* roles
with no server-side keyword/location/date filtering, so (like the Google tool)
we fetch the full list once and filter client-side: AI/ML relevance, US-ish
location, newest-first by publish date."""

from __future__ import annotations

from datetime import datetime

import requests

from ..core import settings
from ._jobs import is_relevant
from .registry import ToolRegistry

GREENHOUSE_BOARDS_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

DEFAULT_LIMIT = 15
MAX_LIMIT = 25

# Supported companies: slug (Greenhouse board id) -> display name.
# Add an org = add a line here. Verified live and AI/ML-heavy.
GREENHOUSE_BOARDS = {
    "databricks": "Databricks",
    "airbnb": "Airbnb",
    "stripe": "Stripe",
    "pinterest": "Pinterest",
    "reddit": "Reddit",
    "coinbase": "Coinbase",
    "dropbox": "Dropbox",
    "robinhood": "Robinhood",
}

# Location strings that mark a role as non-US (Greenhouse location.name is free text).
_NON_US = (
    "india", "canada", "united kingdom", " uk", "ireland", "germany", "france",
    "netherlands", "israel", "singapore", "australia", "japan", "china", "brazil",
    "mexico", "spain", "poland", "costa rica", "argentina", "emea", "apac", "romania",
    "dublin", "london", "berlin", "toronto", "bengaluru", "bangalore", "tokyo",
    "amsterdam", "sydney", "são paulo", "sao paulo",
)


def _is_us_location(name: str) -> bool:
    """Best-effort: keep US and generic-remote roles, drop clearly-foreign ones."""
    low = name.lower()
    if "united states" in low or "usa" in low or "u.s." in low:
        return True
    if any(marker in low for marker in _NON_US):
        return False
    # Left with US cities/states or a bare "Remote" — treat as US-eligible.
    return True


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_greenhouse_jobs(company: str, keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search a big-tech company's Greenhouse careers board for recent US
        AI/ML job openings and return title, date posted, and link.

        Args:
            company: Which company to search. Supported: databricks, airbnb,
                stripe, pinterest, reddit, coinbase, dropbox, robinhood.
            keywords: Optional phrase to narrow titles (e.g. "machine learning").
                If empty, returns all AI/ML-relevant roles.
            limit: Maximum number of roles to return.
        """
        slug = (company or "").strip().lower()
        if slug not in GREENHOUSE_BOARDS:
            supported = ", ".join(sorted(GREENHOUSE_BOARDS))
            return f"Unknown company '{company}'. Supported Greenhouse companies: {supported}."

        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = min(max(limit, 1), MAX_LIMIT)

        name = GREENHOUSE_BOARDS[slug]
        headers = {"User-Agent": settings.TOOL_USER_AGENT, "Accept": "application/json"}
        try:
            resp = requests.get(
                GREENHOUSE_BOARDS_URL.format(slug=slug),
                headers=headers, timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])
        except Exception:
            return f"Couldn't reach {name}'s careers board right now. Try again later."

        terms = keywords.lower().split()
        rows: list[dict] = []
        for j in jobs:
            title = (j.get("title") or "").strip()
            location = (j.get("location") or {}).get("name", "").strip()
            low = title.lower()
            if not is_relevant(title):
                continue
            if terms and not any(t in low for t in terms):
                continue
            if location and not _is_us_location(location):
                continue
            # first_published / updated_at are ISO 8601 with an offset, e.g. 2026-07-01T18:31:32-04:00
            raw = j.get("first_published") or j.get("updated_at") or ""
            try:
                date = datetime.fromisoformat(raw)
            except ValueError:
                date = None
            rows.append({"title": title, "location": location,
                         "url": j.get("absolute_url", ""), "date": date})

        # Newest-first; tz-aware and naive dates don't compare, so sort on a timestamp.
        rows.sort(key=lambda r: r["date"].timestamp() if r["date"] else 0.0, reverse=True)
        rows = rows[:limit]
        if not rows:
            return (f"No relevant {name} roles found right now. "
                    "Try again later or adjust your keywords.")

        lines = [f"*Latest {name} AI/ML roles (most recent first) — {len(rows)} found:*", ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. *{r['title']}*")
            lines.append(f"    Organization: {name}")
            if r["location"]:
                lines.append(f"    Location: {r['location']}")
            lines.append(f"    Link: {r['url']}")
            lines.append(f"    Posted: {r['date']:%b %d, %Y}" if r["date"] else "    Posted: not listed")
            lines.append("")
        return "\n".join(lines)
