"""Parsing the Resume Parser's reply, and the hand-off to the job agent.

The parser is deliberately forgiving: a small local model wraps JSON in code
fences, adds a sentence, or returns a comma-separated string where a list was
asked for. None of that should break the pipeline.
"""

from __future__ import annotations

import pytest

from scout.agents import resume_parser
from scout.agents.resume_parser import CandidateProfile, parse_profile
from scout.agents.resume_tailored import ResumeTailoredAgent
from scout.core import agent as agent_module
from scout.core import settings
from scout.core.agent import ConversationalAgent
from scout.core.backends.base import ChatResult

from .conftest import ScriptedBackend

FULL_JSON = """
{
  "titles": ["ML Engineer", "Applied Scientist"],
  "skills": ["PyTorch", "Spark"],
  "keywords": ["machine learning engineer"],
  "seniority": "entry",
  "summary": "Recent MS graduate in AI."
}
"""


def test_parses_a_clean_json_reply() -> None:
    profile = parse_profile(FULL_JSON)
    assert profile.titles == ["ML Engineer", "Applied Scientist"]
    assert profile.skills == ["PyTorch", "Spark"]
    assert profile.seniority == "entry"
    assert profile.summary == "Recent MS graduate in AI."


def test_parses_json_wrapped_in_fences_and_prose() -> None:
    raw = f"Sure! Here's the profile:\n```json\n{FULL_JSON}\n```\nHope that helps."
    assert parse_profile(raw).titles == ["ML Engineer", "Applied Scientist"]


def test_coerces_comma_separated_strings_into_lists() -> None:
    profile = parse_profile('{"titles": "ML Engineer, Data Scientist", "skills": ""}')
    assert profile.titles == ["ML Engineer", "Data Scientist"]
    assert profile.skills == []


def test_drops_blank_list_entries_and_coerces_non_strings() -> None:
    profile = parse_profile('{"skills": ["PyTorch", "", "  ", 42]}')
    assert profile.skills == ["PyTorch", "42"]


@pytest.mark.parametrize("raw", [
    "",
    "I could not read the resume.",
    "{not valid json}",
    "[1, 2, 3]",             # JSON, but not an object
    "{",
])
def test_unparseable_replies_degrade_to_an_empty_profile(raw: str) -> None:
    """An empty profile means an untailored search — better than an error."""
    assert parse_profile(raw) == CandidateProfile()


def test_search_brief_lists_only_the_fields_present() -> None:
    brief = CandidateProfile(
        titles=["ML Engineer"], keywords=["machine learning"], summary="MS grad."
    ).to_search_brief()

    assert brief.splitlines() == [
        "Candidate profile (use this to tailor and search for roles):",
        "- Background: MS grad.",
        "- Target titles: ML Engineer",
        "- Search keywords: machine learning",
    ]


def test_empty_profile_still_renders_a_header() -> None:
    assert CandidateProfile().to_search_brief().startswith("Candidate profile")


# --- The resume -> job-search hand-off ------------------------------------


@pytest.fixture
def pipeline(monkeypatch, spec):
    """A ResumeTailoredAgent wired to one scripted backend."""
    backend = ScriptedBackend("primary")
    monkeypatch.setattr(agent_module, "build_backends", lambda: {"primary": backend})
    monkeypatch.setattr(settings, "FALLBACK_BACKEND", "primary")  # no fallback
    monkeypatch.setattr(resume_parser.SPEC, "default_backend", "primary")
    return ResumeTailoredAgent(spec), backend


def test_pipeline_satisfies_the_adapter_interface(pipeline) -> None:
    agent, _ = pipeline
    assert isinstance(agent, ConversationalAgent)
    assert agent.name == "Test Agent (resume-tailored)"


def test_first_turn_parses_the_resume_then_prefixes_the_brief(pipeline) -> None:
    agent, backend = pipeline
    backend.results = [
        ChatResult(text='{"titles": ["ML Engineer"]}'),  # the parser stage
        ChatResult(text="here are some roles"),          # the job stage
    ]

    assert agent.respond("U1", "find me jobs") == "here are some roles"

    job_prompt = backend.seen[1][-1]["content"]
    assert job_prompt.startswith("Candidate profile")
    assert "- Target titles: ML Engineer" in job_prompt
    assert job_prompt.endswith("User request: find me jobs")


def test_brief_is_cached_after_the_first_turn(pipeline) -> None:
    agent, backend = pipeline
    backend.results = [
        ChatResult(text='{"titles": ["ML Engineer"]}'),
        ChatResult(text="first"),
        ChatResult(text="second"),
    ]

    agent.respond("U1", "one")
    agent.respond("U1", "two")

    # Three model calls total: one parse plus two job turns (no re-parse).
    assert len(backend.seen) == 3
    assert "- Target titles: ML Engineer" in backend.seen[2][-1]["content"]


def test_reset_forces_a_re_parse(pipeline) -> None:
    agent, backend = pipeline
    backend.results = [ChatResult(text='{"titles": ["A"]}'), ChatResult(text="ok")]
    agent.respond("U1", "one")

    agent.reset("U1")
    backend.results = [ChatResult(text='{"titles": ["B"]}'), ChatResult(text="ok")]
    agent.respond("U1", "two")

    assert "- Target titles: B" in backend.seen[-1][-1]["content"]


def test_both_stages_switch_backend_together(monkeypatch, spec) -> None:
    """A mid-conversation switch must not leave the pipeline half on one model."""
    primary, other = ScriptedBackend("primary"), ScriptedBackend("other")
    monkeypatch.setattr(agent_module, "build_backends",
                        lambda: {"primary": primary, "other": other})
    monkeypatch.setattr(settings, "FALLBACK_BACKEND", "primary")
    monkeypatch.setattr(resume_parser.SPEC, "default_backend", "primary")
    agent = ResumeTailoredAgent(spec)

    assert agent.set_backend("U1", "other") is True
    assert agent.backend_label("U1") == "Scripted (other)"

    other.results = [ChatResult(text='{"titles": ["X"]}'), ChatResult(text="ok")]
    agent.respond("U1", "jobs")

    assert len(other.seen) == 2  # both the parse and the job turn went to "other"
    assert primary.seen == []
