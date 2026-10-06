"""Reads the user's resume from ``data/`` so the model can reason about it."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from ..core import settings
from ..core.paths import under_root
from .registry import ToolRegistry

#: The one file the Resume Parser reads. A fixed name rather than "whichever
#: file is newest", so which resume a profile came from is never a guess.
RESUME_FILE = "resume.pdf"


def resume_dir() -> Path:
    """Where to look for the resume, per ``RESUME_DIR``.

    Resolved on each call rather than at import, so a test — or an operator
    moving the folder — does not have to reload the module.
    """
    return under_root(settings.RESUME_DIR)


def resume_path() -> Path:
    """The resume itself: ``resume.pdf`` in ``RESUME_DIR``."""
    return resume_dir() / RESUME_FILE


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def get_resume_profile() -> str:
        """Read the user's resume (resume.pdf in the data/ folder) and return its full text.

        Use this to understand the user's skills, experience, and field before
        searching for jobs or answering questions about their background."""
        path = resume_path()
        where = f"{path.parent.name}/{RESUME_FILE}"
        if not path.is_file():
            return f"No resume found at {where}. Save the resume there, as a PDF under that name."

        try:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        except Exception as e:
            return f"Could not read resume '{where}': {e}"

        text = text.strip()
        if not text:
            return f"Resume '{where}' appears to be empty or unreadable (scanned image?)."
        return f"Resume file: {where}\n\n{text}"
