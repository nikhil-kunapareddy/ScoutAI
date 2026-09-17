"""Parsing the Resume Parser's reply, and the hand-off to the job agent.

The parser is deliberately forgiving: a small local model wraps JSON in code
fences, adds a sentence, or returns a comma-separated string where a list was
asked for. None of that should break the pipeline.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from scout.agents import resume_parser
from scout.agents.resume_parser import CandidateProfile, parse_profile
from scout.agents.resume_tailored import ResumeTailoredAgent
from scout.core.agent import ConversationalAgent
from scout.core.runner import STUCK_REPLY

from .conftest import calls_tool, texts

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
def pipeline(monkeypatch, chat_models, spec):
    """A ResumeTailoredAgent over the scripted models, sharing one of them."""
    monkeypatch.setattr(resume_parser.SPEC, "default_backend", "primary")
    return ResumeTailoredAgent(spec), chat_models["primary"]


def test_pipeline_satisfies_the_adapter_interface(pipeline) -> None:
    agent, _ = pipeline
    assert isinstance(agent, ConversationalAgent)
    assert agent.name == "Test Agent (resume-tailored)"


def test_first_turn_parses_the_resume_then_tailors_the_instructions(pipeline) -> None:
    agent, model = pipeline
    model.replies = [
        AIMessage('{"titles": ["ML Engineer"]}'),  # the parser stage
        AIMessage("here are some roles"),          # the job stage
    ]

    assert agent.respond("U1", "find me jobs") == "here are some roles"

    # The brief rides in the job agent's instructions, not in the user's turn.
    instructions, request = model.seen[1][0], model.seen[1][-1]
    assert instructions.type == "system"
    assert instructions.text.startswith("You are a test agent.")
    assert "- Target titles: ML Engineer" in instructions.text
    assert request.text == "find me jobs"


def test_the_parsers_own_turn_stays_out_of_the_job_history(pipeline) -> None:
    """The parser runs on its own thread, so its JSON reply and the request that
    produced it never reach the job agent's conversation."""
    agent, model = pipeline
    model.replies = [AIMessage('{"titles": ["ML Engineer"]}'), AIMessage("roles")]
    agent.respond("U1", "find me jobs")

    assert texts(model.seen[0])[1] == "Extract my candidate profile."
    job_turn = texts(model.seen[1])
    assert "Extract my candidate profile." not in job_turn
    assert '{"titles": ["ML Engineer"]}' not in job_turn


def test_brief_is_cached_after_the_first_turn(pipeline) -> None:
    agent, model = pipeline
    model.replies = [
        AIMessage('{"titles": ["ML Engineer"]}'),
        AIMessage("first"),
        AIMessage("second"),
    ]

    agent.respond("U1", "one")
    agent.respond("U1", "two")

    # Three model calls total: one parse plus two job turns (no re-parse).
    assert len(model.seen) == 3
    assert "- Target titles: ML Engineer" in model.seen[2][0].text


def test_reset_forces_a_re_parse(pipeline) -> None:
    agent, model = pipeline
    model.replies = [AIMessage('{"titles": ["A"]}'), AIMessage("ok")]
    agent.respond("U1", "one")

    agent.reset("U1")
    model.replies = [AIMessage('{"titles": ["B"]}'), AIMessage("ok")]
    agent.respond("U1", "two")

    assert "- Target titles: B" in model.seen[-1][0].text


def test_the_job_stage_still_gets_its_full_tool_budget(pipeline, monkeypatch) -> None:
    """The parse and job-agent nodes are extra super-steps on top of the tool
    loop, so the recursion limit has to leave room for them."""
    from scout.core import settings

    monkeypatch.setattr(settings, "MAX_TOOL_HOPS", 3)
    agent, model = pipeline
    model.replies = [
        AIMessage('{"titles": ["ML Engineer"]}'),      # the parse
        *[calls_tool("echo") for _ in range(10)],      # a job stage that never stops
    ]

    assert agent.respond("U1", "find me jobs") == STUCK_REPLY
    assert len(model.seen) == 1 + 3  # the parse, then three job-stage calls


def test_both_stages_switch_backend_together(monkeypatch, chat_models, spec) -> None:
    """A mid-conversation switch must not leave the pipeline half on one model."""
    monkeypatch.setattr(resume_parser.SPEC, "default_backend", "primary")
    primary, other = chat_models["primary"], chat_models["fallback"]
    agent = ResumeTailoredAgent(spec)

    assert agent.set_backend("U1", "fallback") is True
    assert agent.backend_label("U1") == "Scripted (fallback)"

    other.replies = [AIMessage('{"titles": ["X"]}'), AIMessage("ok")]
    agent.respond("U1", "jobs")

    assert len(other.seen) == 2  # both the parse and the job turn went to "fallback"
    assert primary.seen == []


def test_the_brief_carries_every_field_the_parser_filled() -> None:
    """The brief is the whole hand-off — a field dropped here is a field the job
    agent never sees."""
    brief = CandidateProfile(
        titles=["ML Engineer", "Applied Scientist"],
        skills=["PyTorch", "Spark"],
        keywords=["machine learning", "llm"],
        seniority="mid",
        summary="Two years on recommendation systems.",
    ).to_search_brief()

    assert "- Background: Two years on recommendation systems." in brief
    assert "- Seniority: mid" in brief
    assert "- Target titles: ML Engineer, Applied Scientist" in brief
    assert "- Key skills: PyTorch, Spark" in brief
    assert "- Search keywords: machine learning, llm" in brief
