"""Resume Parser Agent: turns the user's resume into a structured profile.

The producer half of a two-step pipeline, wired up in ``resume_tailored.py``:

    resume_parser  ──CandidateProfile──▶  job agent (bigtech, edu, …)

It defines no tools of its own — it reuses ``get_resume_profile`` to read the
file, and the model distils that text into the fields below. ``parse_profile``
turns the reply into a ``CandidateProfile``; ``to_search_brief`` renders it as
the hand-off message.

Run it alone to check the parsing step: ``scout run --agent resume``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..core.agent import AgentSpec
from ..tools import resume

# --- The profile handed off to the job agent -------------------------------


@dataclass
class CandidateProfile:
    """Distilled summary of the user's resume.

    ``keywords`` is the field that matters most for the hand-off: those phrases
    feed the ``keywords=`` argument of the job-search tools.
    """

    #: Target roles, e.g. ``"ML Engineer"``.
    titles: list[str] = field(default_factory=list)
    #: Notable skills and technologies.
    skills: list[str] = field(default_factory=list)
    #: Search phrases for the job tools.
    keywords: list[str] = field(default_factory=list)
    #: ``"entry"``, ``"mid"`` or ``"senior"``.
    seniority: str = ""
    #: One-line background.
    summary: str = ""

    def to_search_brief(self) -> str:
        """Render the profile as the message handed to the job agent."""
        lines = ["Candidate profile (use this to tailor and search for roles):"]
        if self.summary:
            lines.append(f"- Background: {self.summary}")
        if self.seniority:
            lines.append(f"- Seniority: {self.seniority}")
        if self.titles:
            lines.append(f"- Target titles: {', '.join(self.titles)}")
        if self.skills:
            lines.append(f"- Key skills: {', '.join(self.skills)}")
        if self.keywords:
            lines.append(f"- Search keywords: {', '.join(self.keywords)}")
        return "\n".join(lines)


# --- Turning the agent's reply into a profile -----------------------------


def parse_profile(reply: str) -> CandidateProfile:
    """Parse the parser agent's reply (expected to be JSON) into a profile.

    Tolerant on purpose: the model sometimes wraps the JSON in code fences or a
    sentence despite being told not to, so this takes the outermost ``{...}``
    and coerces types. Total failure returns an empty profile, degrading to an
    untailored search rather than erroring out.

    Args:
        reply: The parser agent's final message text.
    """
    data = _extract_json_object(reply)
    if data is None:
        return CandidateProfile()

    return CandidateProfile(
        titles=_as_list(data.get("titles")),
        skills=_as_list(data.get("skills")),
        keywords=_as_list(data.get("keywords")),
        seniority=_as_text(data.get("seniority")),
        summary=_as_text(data.get("summary")),
    )


def _extract_json_object(text: str) -> dict[str, object] | None:
    """Best-effort pull of the outermost JSON object from a text blob."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_list(value: object) -> list[str]:
    """Coerce a field to a list of non-empty strings, accepting "a, b" for ["a", "b"]."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    items = (str(item).strip() for item in value)
    return [item for item in items if item]


def _as_text(value: object) -> str:
    """Coerce a field to a stripped string, treating a missing value as empty."""
    return str(value or "").strip()


# --- The agent spec -------------------------------------------------------

SYSTEM_PROMPT = (
    "You are Resume Parser, a data-extraction agent. Your only job is to read "
    "the user's resume and summarize it as a structured profile.\n\n"
    "Steps:\n"
    "1. Call get_resume_profile to read the resume.\n"
    "2. Reply with ONLY a JSON object (no prose, no code fences) with these keys:\n"
    '   "titles": array of target job titles that fit the background,\n'
    '   "skills": array of the most notable skills/technologies,\n'
    '   "keywords": array of short search phrases for a job board,\n'
    '   "seniority": one of "entry", "mid", or "senior",\n'
    '   "summary": a one-sentence background summary.\n'
    "Base every field on the resume text only. Do not invent experience."
)

SPEC = AgentSpec(
    key="resume",
    name="Resume Parser",
    system_prompt=SYSTEM_PROMPT,
    tool_modules=[resume],
)
