"""
Resume Parser Agent: reads the user's resume and produces a structured
``CandidateProfile`` that the job-search agent (see ``bigtech.py``) consumes.

This is the *producer* half of a two-step pipeline:

    resume_parser  ──CandidateProfile──▶  bigtech (job search)

It defines no new tools — it reuses the existing ``resume`` tool
(``get_resume_profile``) to read the file, then the model distills that raw
text into the fields below. ``parse_profile`` turns the agent's JSON reply into
a ``CandidateProfile``; ``to_search_brief`` renders it as the hand-off message
fed to the job agent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..core.agent import AgentSpec
from ..tools import resume

# --- The structured profile handed off to the job agent -------------------


@dataclass
class CandidateProfile:
    """Distilled, reusable summary of the user's resume.

    ``keywords`` is the important field for the hand-off: those phrases feed the
    ``keywords=`` argument of the job-search tools (``search_google_jobs`` etc.).
    """

    titles: list[str] = field(default_factory=list)      # target roles, e.g. "ML Engineer"
    skills: list[str] = field(default_factory=list)      # notable skills / technologies
    keywords: list[str] = field(default_factory=list)    # search phrases for the job tools
    seniority: str = ""                                  # e.g. "entry" / "mid" / "senior"
    summary: str = ""                                    # one-line background summary

    def to_search_brief(self) -> str:
        """Render the profile as the message handed off to the job agent."""
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

_LIST_FIELDS = ("titles", "skills", "keywords")


def parse_profile(raw: str) -> CandidateProfile:
    """Parse the resume agent's reply (expected to be JSON) into a profile.

    Tolerant on purpose: small local models often wrap JSON in ```code fences```
    or add a sentence around it, so we extract the outermost ``{...}`` and coerce
    types. On total failure we return an empty profile rather than raising, so
    the pipeline degrades to an untailored search instead of erroring out.
    """
    data = _extract_json_object(raw)
    if data is None:
        return CandidateProfile()

    def as_list(v) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str) and v.strip():
            return [s.strip() for s in v.split(",") if s.strip()]
        return []

    return CandidateProfile(
        titles=as_list(data.get("titles")),
        skills=as_list(data.get("skills")),
        keywords=as_list(data.get("keywords")),
        seniority=str(data.get("seniority") or "").strip(),
        summary=str(data.get("summary") or "").strip(),
    )


def _extract_json_object(raw: str) -> dict | None:
    """Best-effort pull of the outermost JSON object from a text blob."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(raw[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


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
    tool_modules=[resume],          # reuses the existing get_resume_profile tool
    default_backend="ollama",
)
