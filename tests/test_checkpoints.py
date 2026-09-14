"""Durable checkpointing: what survives a restart, and what a reset removes."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from scout.agents import resume_parser
from scout.agents.resume_tailored import ResumeTailoredAgent
from scout.core import settings
from scout.core.agent import Agent
from scout.core.checkpoints import _resolve, build_checkpointer
from scout.core.paths import PROJECT_ROOT


@pytest.fixture
def persisted(monkeypatch, tmp_path):
    """Point checkpointing at a scratch database for the duration of a test."""
    path = tmp_path / "state" / "scout.sqlite"
    monkeypatch.setattr(settings, "CHECKPOINT_DB", str(path))
    return path


def test_default_keeps_state_in_memory(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CHECKPOINT_DB", "")
    assert isinstance(build_checkpointer(), InMemorySaver)


def test_a_configured_path_gets_sqlite_and_creates_the_file(persisted) -> None:
    assert isinstance(build_checkpointer(), SqliteSaver)
    assert persisted.exists()


def test_relative_path_is_taken_against_the_project_root() -> None:
    # systemd starts the bot from /, so a bare path must not follow the cwd.
    assert _resolve("state/scout.sqlite") == PROJECT_ROOT / "state" / "scout.sqlite"


def test_absolute_path_is_left_alone(tmp_path) -> None:
    assert _resolve(str(tmp_path / "s.sqlite")) == tmp_path / "s.sqlite"


def test_history_survives_a_restart(spec, chat_models, persisted) -> None:
    chat_models["primary"].replies = [AIMessage("noted"), AIMessage("blue")]
    Agent(spec).respond("U1", "remember: blue")

    # A restart is a fresh Agent over the same database.
    Agent(spec).respond("U1", "what colour?")

    sent = chat_models["primary"].seen[-1]
    assert [m.text for m in sent if m.type == "human"] == [
        "remember: blue",
        "what colour?",
    ]


def test_reset_clears_the_persisted_thread(spec, chat_models, persisted) -> None:
    chat_models["primary"].replies = [AIMessage("noted"), AIMessage("fresh")]
    agent = Agent(spec)
    agent.respond("U1", "remember: blue")
    agent.reset("U1")

    Agent(spec).respond("U1", "what colour?")

    sent = chat_models["primary"].seen[-1]
    assert [m.text for m in sent if m.type == "human"] == ["what colour?"]


def test_one_users_reset_leaves_another_alone(spec, chat_models, persisted) -> None:
    chat_models["primary"].replies = [AIMessage("a"), AIMessage("b"), AIMessage("c")]
    agent = Agent(spec)
    agent.respond("U1", "mine")
    agent.respond("U2", "theirs")
    agent.reset("U1")

    agent.respond("U2", "still there?")

    sent = chat_models["primary"].seen[-1]
    assert [m.text for m in sent if m.type == "human"] == ["theirs", "still there?"]


def test_parsed_profile_survives_a_restart(
    monkeypatch, spec, chat_models, persisted
) -> None:
    """The point of persisting: no re-parse, so no extra model call per restart."""
    monkeypatch.setattr(resume_parser.SPEC, "default_backend", "primary")
    model = chat_models["primary"]
    model.replies = [
        AIMessage('{"titles": ["ML Engineer"]}'),  # parser stage
        AIMessage("here are some roles"),          # job stage
        AIMessage("more roles"),                   # job stage, after the restart
    ]

    ResumeTailoredAgent(spec).respond("U1", "find me work")
    calls_before = len(model.seen)

    ResumeTailoredAgent(spec).respond("U1", "anything new?")

    # One call, not two: the parser stage was skipped entirely.
    assert len(model.seen) == calls_before + 1
    # And the brief it produced is still in the instructions.
    assert "ML Engineer" in model.seen[-1][0].text
