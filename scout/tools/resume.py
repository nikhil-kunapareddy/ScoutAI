"""
Resume reading tool.

Reads the user's resume from the data/ folder so the model can reason about
their skills and background (e.g. before searching for jobs).
"""

from __future__ import annotations

from pathlib import Path

import docx2txt
from pypdf import PdfReader

from ..core.paths import DATA_DIR
from .registry import ToolRegistry

RESUME_EXTS = {".pdf", ".docx", ".txt", ".md"}


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == ".docx":
        return docx2txt.process(str(path)) or ""
    # .txt / .md and other plain text
    return path.read_text(encoding="utf-8", errors="ignore")


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def get_resume_profile() -> str:
        """Read the user's resume from the data/ folder and return its full text.

        Use this to understand the user's skills, experience, and field before
        searching for jobs or answering questions about their background. Picks
        the most recently modified resume file if several are present."""
        if not DATA_DIR.is_dir():
            return "No data/ folder found. Add your resume there (PDF, DOCX, TXT, or MD)."

        candidates = [
            p for p in DATA_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in RESUME_EXTS
        ]
        if not candidates:
            return (
                "No resume found in data/. Add a resume file "
                "(PDF, DOCX, TXT, or MD) to that folder."
            )

        resume = max(candidates, key=lambda p: p.stat().st_mtime)
        try:
            text = _extract_text(resume).strip()
        except Exception as e:
            return f"Could not read resume '{resume.name}': {e}"

        if not text:
            return f"Resume '{resume.name}' appears to be empty or unreadable (scanned image?)."
        return f"Resume file: {resume.name}\n\n{text}"
