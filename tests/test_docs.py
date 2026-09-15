"""The documentation's internal links.

A broken link is the cheapest kind of rot and the most annoying to a newcomer,
and the docs cross-reference each other and the source heavily. This walks every
relative link in every tracked Markdown file and checks that what it points at
exists — the file, and the heading when there is an anchor.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: [text](target) — skipping images, which are checked the same way below.
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
IMAGE = re.compile(r'<img[^>]*src="([^"]+)"')
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)

#: Directories with no documentation in them.
SKIP = {".venv", ".git", "node_modules", ".pytest_cache", ".ruff_cache", ".mypy_cache"}


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not set(path.relative_to(ROOT).parts) & SKIP
    )


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, punctuation dropped, spaces hyphenated."""
    text = re.sub(r"`|\*|_", "", heading).strip().lower()
    kept = [
        char
        for char in text
        if char.isalnum() or char in {" ", "-", "_"} or unicodedata.combining(char)
    ]
    return "".join(kept).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    return {slug(heading) for heading in HEADING.findall(path.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("doc", markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_resolves(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    targets = LINK.findall(text) + IMAGE.findall(text)

    for target in targets:
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path_part, _, anchor = target.partition("#")
        resolved = (doc.parent / path_part).resolve()

        assert resolved.exists(), f"{doc.name} links to missing {target}"
        if anchor and resolved.suffix == ".md":
            assert anchor in anchors(resolved), (
                f"{doc.name} links to {target}, but that heading is not in "
                f"{resolved.name}"
            )


@pytest.mark.parametrize("doc", markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_in_page_anchors_resolve(doc: Path) -> None:
    """The tables of contents, which are written by hand and drift first."""
    text = doc.read_text(encoding="utf-8")
    here = anchors(doc)

    for target in LINK.findall(text):
        if not target.startswith("#"):
            continue
        assert target[1:] in here, f"{doc.name} links to {target}, which is not a heading"


def test_the_docs_index_lists_every_page() -> None:
    """A page nobody links to is a page nobody reads."""
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    pages = {path.name for path in (ROOT / "docs").glob("*.md")} - {"README.md"}

    missing = {page for page in pages if page not in index}
    assert not missing, f"docs/README.md does not link to {sorted(missing)}"
