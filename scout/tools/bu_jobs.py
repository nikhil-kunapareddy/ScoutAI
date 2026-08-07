"""Boston University job-search tool.

BU's careers site runs on SilkRoad, which has no clean search API but does
publish an RSS feed of all open roles. Like the Google tool, we fetch the full
feed once and filter client-side. Each item carries a real ``postingDate``
(MM/DD/YYYY), so results are returned newest-first."""

from __future__ import annotations

import html
import re
from datetime import datetime

import requests

from ..core import settings
from ._jobs import is_relevant
from .registry import ToolRegistry

BU_RSS_URL = "https://jobs.silkroad.com/BU/External/rss"

DEFAULT_LIMIT = 15
MAX_LIMIT = 25

_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)


def _tag(block: str, tag: str) -> str:
    """Extract one RSS tag's text, unwrapping CDATA and decoding entities."""
    m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block, re.S)
    return html.unescape(m.group(1).strip()) if m else ""


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_bu_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Boston University's careers site for recent job openings and
        return each role's title, location, date posted, and link.

        Roles are listed newest-first.

        Args:
            keywords: Optional search phrase to match in titles. If empty,
                defaults to AI/ML-relevant roles.
            limit: Maximum number of roles to return.
        """
        # Be defensive: small models sometimes pass junk values.
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = min(max(limit, 1), MAX_LIMIT)

        try:
            resp = requests.get(
                BU_RSS_URL, headers={"User-Agent": settings.TOOL_USER_AGENT},
                timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except Exception:
            return "Couldn't reach Boston University's careers feed right now. Try again later."

        terms = keywords.lower().split()
        rows = []
        for block in _ITEM_RE.findall(resp.text):
            title = _tag(block, "title")
            low = title.lower()
            # With explicit keywords, match on them; otherwise fall back to the
            # AI/ML profile filter (the SilkRoad feed has no server-side search).
            if terms:
                if not any(t in low for t in terms):
                    continue
            elif not is_relevant(title):
                continue
            raw = _tag(block, "postingDate")
            try:
                date = datetime.strptime(raw, "%m/%d/%Y")
            except ValueError:
                date = None
            rows.append({
                "title": title,
                "location": _tag(block, "location"),
                "url": _tag(block, "link"),
                "posted": raw,
                "date": date,
            })

        # Newest-first; roles without a parseable date sort last.
        rows.sort(key=lambda r: r["date"] or datetime.min, reverse=True)
        rows = rows[:limit]
        if not rows:
            return ("No relevant Boston University roles found right now. "
                    "Try again later or adjust your keywords.")

        lines = [f"*Latest Boston University roles (most recent first) — {len(rows)} found:*", ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. *{r['title']}*")
            lines.append("    Organization: Boston University")
            if r["location"]:
                lines.append(f"    Location: {r['location']}")
            lines.append(f"    Link: {r['url']}")
            lines.append(f"    Posted: {r['posted']}" if r["posted"] else "    Posted: not listed")
            lines.append("")
        return "\n".join(lines)
