"""Reads the user's resume from ``data/`` so the model can reason about it."""

from __future__ import annotations

from pathlib import Path

import docx2txt
from pypdf import PdfReader

from ..core.paths import DATA_DIR
from .registry import ToolRegistry

RESUME_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def get_resume_profile() -> str:
        """Read the user's resume from the data/ folder and return its full text.

        Use this to understand the user's skills, experience, and field before
        searching for jobs or answering questions about their background. Picks
        the most recently modified resume file if several are present."""
        if not DATA_DIR.is_dir():
            return "No data/ folder found. Add your resume there (PDF, DOCX, TXT, or MD)."

        resumes = [
            path for path in DATA_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in RESUME_EXTENSIONS
        ]
        if not resumes:
            return ("No resume found in data/. Add a resume file "
                    "(PDF, DOCX, TXT, or MD) to that folder.")

        newest = max(resumes, key=lambda p: p.stat().st_mtime)
        try:
            text = _extract_text(newest).strip()
        except Exception as e:
            return f"Could not read resume '{newest.name}': {e}"

        if not text:
            return f"Resume '{newest.name}' appears to be empty or unreadable (scanned image?)."
        return f"Resume file: {newest.name}\n\n{text}"


def _extract_text(path: Path) -> str:
    """Pull plain text out of a resume file, by extension."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        return docx2txt.process(str(path)) or ""
    return path.read_text(encoding="utf-8", errors="ignore")  # .txt / .md
