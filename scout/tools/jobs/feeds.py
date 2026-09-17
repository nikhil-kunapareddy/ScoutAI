"""Reading an RSS job feed.

Two sources publish one — Lenovo's Avature portal and Boston University's
SilkRoad board — and neither needs a parser: the feeds are flat lists of
``<item>`` with every field on the surface. Regex rather than ElementTree
because the bodies carry unescaped HTML often enough that a strict parser would
drop a whole feed over one malformed item.
"""

from __future__ import annotations

import html
import re

_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)


def items(feed: str) -> list[str]:
    """Each ``<item>`` body in the feed, in the order it was published."""
    return _ITEM_RE.findall(feed)


def tag_text(item: str, tag: str) -> str:
    """One tag's text, unwrapping CDATA and decoding entities. "" if absent."""
    match = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", item, re.S)
    return html.unescape(match.group(1).strip()) if match else ""
