"""Northeastern University job-search tool.

Northeastern's careers site runs on Workday, which exposes a clean public JSON
endpoint (the same one the myworkdayjobs.com site calls). Workday filters by
``searchText`` server-side, so we hand it the search terms and format the
results. ``postedOn`` is a relative string ("Posted 5 Days Ago"), not a date."""

from __future__ import annotations

import requests

from ..core import settings
from .registry import ToolRegistry

# Workday CXS jobs endpoint: POST https://{host}/wday/cxs/{tenant}/{site}/jobs
NEU_HOST = "northeastern.wd1.myworkdayjobs.com"
NEU_TENANT = "northeastern"
NEU_SITE = "Careers"
NEU_JOBS_URL = f"https://{NEU_HOST}/wday/cxs/{NEU_TENANT}/{NEU_SITE}/jobs"

DEFAULT_LIMIT = 15
MAX_LIMIT = 25
WORKDAY_PAGE_LIMIT = 20  # Workday's CXS endpoint caps page size at 20


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_northeastern_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Northeastern University's careers site for recent job openings
        and return each role's title, location, date posted, and link.

        Args:
            keywords: Optional search phrase. If empty, defaults to AI/ML roles
                ("machine learning").
            limit: Maximum number of roles to return.
        """
        # Be defensive: small models sometimes pass junk values.
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = min(max(limit, 1), MAX_LIMIT)

        body = {"appliedFacets": {}, "limit": min(limit, WORKDAY_PAGE_LIMIT), "offset": 0,
                "searchText": keywords.strip() or "machine learning"}
        try:
            resp = requests.post(
                NEU_JOBS_URL, json=body,
                headers={"User-Agent": settings.TOOL_USER_AGENT,
                         "Content-Type": "application/json", "Accept": "application/json"},
                timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            postings = resp.json().get("jobPostings", [])
        except Exception:
            return "Couldn't reach Northeastern's careers site right now. Try again later."

        rows = []
        for j in postings[:limit]:
            path = j.get("externalPath", "")
            rows.append({
                "title": (j.get("title") or "").strip(),
                "location": (j.get("locationsText") or "").strip(),
                "url": f"https://{NEU_HOST}/{NEU_SITE}{path}" if path else "",
                "posted": (j.get("postedOn") or "").strip(),
            })
        if not rows:
            return ("No relevant Northeastern roles found right now. "
                    "Try again later or adjust your keywords.")

        lines = [f"*Latest Northeastern University roles — {len(rows)} found:*", ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. *{r['title']}*")
            lines.append("    Organization: Northeastern University")
            if r["location"]:
                lines.append(f"    Location: {r['location']}")
            lines.append(f"    Link: {r['url']}")
            lines.append(f"    Posted: {r['posted']}" if r["posted"] else "    Posted: not listed")
            lines.append("")
        return "\n".join(lines)
