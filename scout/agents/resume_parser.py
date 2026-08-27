"""Resume Parser Agent: turns the user's resume into a structured profile.

The producer half of a two-step pipeline, wired up in ``resume_tailored.py``:

    resume_parser  ──CandidateProfile──▶  job agent (bigtech, university, …)

It defines no tools of its own — it reuses ``get_resume_profile`` to read the
file, and the model distils that text into the fields below. ``parse_profile``
turns the reply into a ``CandidateProfile``; ``to_search_brief`` renders it as
the hand-off message.

Run it alone to check the parsing step: ``AGENT=resume python run.py``.
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

    titles: list[str] = field(default_factory=list)    # target roles, e.g. "ML Engineer"
    skills: list[str] = field(default_factory=list)    # notable skills / technologies
    keywords: list[str] = field(default_factory=list)  # search phrases for the job tools
    seniority: str = ""                                # "entry" / "mid" / "senior"
    summary: str = ""                                  # one-line background

    def to_search_brief(self) -> str:
        """Render the profile as the message handed to the job agent."""
        parts = ["Candidate profile (use this to tailor and search for roles):"]
        if self.summary:
            parts.append(f"- Background: {self.summary}")
        if self.seniority:
            parts.append(f"- Seniority: {self.seniority}")
        if self.titles:
            parts.append(f"- Target titles: {', '.join(self.titles)}")
        if self.skills:
            parts.append(f"- Key skills: {', '.join(self.skills)}")
        if self.keywords:
            parts.append(f"- Search keywords: {', '.join(self.keywords)}")
        return "\n".join(parts)


# --- Turning the agent's reply into a profile -----------------------------


def parse_profile(raw: str) -> CandidateProfile:
    """Parse the parser agent's reply (expected to be JSON) into a profile.

    Tolerant on purpose: small local models wrap JSON in code fences or add a
    sentence around it, so we take the outermost ``{...}`` and coerce types. Total
    failure returns an empty profile, degrading to an untailored search rather
    than erroring out.
    """
    data = _extract_json_object(raw)
    if data is None:
        return CandidateProfile()

    return CandidateProfile(
        titles=_as_list(data.get("titles")),
        skills=_as_list(data.get("skills")),
        keywords=_as_list(data.get("keywords")),
        seniority=str(data.get("seniority") or "").strip(),
        summary=str(data.get("summary") or "").strip(),
    )


def _as_list(value: object) -> list[str]:
    """Coerce a field to a list of strings, accepting "a, b" for ["a", "b"]."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _extract_json_object(raw: str) -> dict | None:
    """Best-effort pull of the outermost JSON object from a text blob."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


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
)  # default_backend omitted: inherits settings.DEFAULT_BACKEND (Claude, else Ollama)
