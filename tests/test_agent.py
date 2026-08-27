"""The Agent runtime: the tool loop, the fallback replay, and per-user state."""

from __future__ import annotations

import pytest

from scout.core import agent as agent_module
from scout.core import settings
from scout.core.agent import Agent, ConversationalAgent
from scout.core.backends.base import ChatResult

from .conftest import ScriptedBackend, tool_call


@pytest.fixture
def backends(monkeypatch) -> dict[str, ScriptedBackend]:
    """Install scripted backends in place of the real ones."""
    registry = {
        "primary": ScriptedBackend("primary"),
        "fallback": ScriptedBackend("fallback"),
    }
    monkeypatch.setattr(agent_module, "build_backends", lambda: registry)
    monkeypatch.setattr(settings, "FALLBACK_BACKEND", "fallback")
    return registry


def test_agent_satisfies_the_adapter_interface(spec, backends) -> None:
    assert isinstance(Agent(spec), ConversationalAgent)


def test_plain_reply_sends_the_system_prompt_first(spec, backends) -> None:
    backends["primary"].results = [ChatResult(text="hello there")]
    agent = Agent(spec)

    assert agent.respond("U1", "hi") == "hello there"

    sent = backends["primary"].seen[0]
    assert sent[0] == {"role": "system", "content": "You are a test agent."}
    assert sent[1] == {"role": "user", "content": "hi"}


def test_history_carries_across_turns(spec, backends) -> None:
    backends["primary"].results = [ChatResult(text="one"), ChatResult(text="two")]
    agent = Agent(spec)

    agent.respond("U1", "first")
    agent.respond("U1", "second")

    second_turn = backends["primary"].seen[1]
    assert [m["content"] for m in second_turn] == [
        "You are a test agent.", "first", "one", "second",
    ]


def test_tool_call_is_run_and_fed_back(spec, backends) -> None:
    backends["primary"].results = [
        ChatResult(text="", tool_calls=[tool_call("echo", {"text": "hi"})]),
        ChatResult(text="the tool said hi"),
    ]
    agent = Agent(spec)

    assert agent.respond("U1", "use the tool") == "the tool said hi"

    # Second request carries the assistant turn plus the tool result.
    second_request = backends["primary"].seen[1]
    assert second_request[-1] == {
        "role": "tool", "tool_name": "echo", "content": "echo:hi",
    }


def test_failing_tool_becomes_a_message_for_the_model(spec, backends) -> None:
    backends["primary"].results = [
        ChatResult(text="", tool_calls=[tool_call("explode")]),
        ChatResult(text="I saw the error"),
    ]
    agent = Agent(spec)

    assert agent.respond("U1", "break it") == "I saw the error"
    assert "Error running tool: tool blew up" in backends["primary"].seen[1][-1]["content"]


def test_unknown_tool_is_reported_to_the_model(spec, backends) -> None:
    backends["primary"].results = [
        ChatResult(text="", tool_calls=[tool_call("nope")]),
        ChatResult(text="ok"),
    ]
    agent = Agent(spec)

    agent.respond("U1", "call a missing tool")
    assert backends["primary"].seen[1][-1]["content"] == "Unknown tool: nope"


def test_tool_hop_limit_ends_the_turn(spec, backends, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAX_TOOL_HOPS", 3)
    # Always asks for another tool: the loop must stop on its own.
    backends["primary"].results = [
        ChatResult(text="", tool_calls=[tool_call("echo")]) for _ in range(10)
    ]
    agent = Agent(spec)

    reply = agent.respond("U1", "loop forever")
    assert "got stuck calling my tools" in reply
    assert len(backends["primary"].seen) == 3


def test_failed_turn_is_replayed_on_the_fallback(spec, backends) -> None:
    backends["primary"].fails = True
    backends["fallback"].results = [ChatResult(text="fallback answer")]
    agent = Agent(spec)

    assert agent.respond("U1", "hi") == "fallback answer"
    # The user's choice is untouched, so the next turn tries Claude again...
    assert agent.backend_name("U1") == "primary"
    # ...but we can still tell who actually answered.
    assert agent.last_backend("U1") == "fallback"


def test_fallback_sees_no_trace_of_the_failed_attempt(spec, backends) -> None:
    backends["primary"].fails = True
    backends["fallback"].results = [ChatResult(text="ok")]
    agent = Agent(spec)

    agent.respond("U1", "hi")
    assert [m["content"] for m in backends["fallback"].seen[0]] == [
        "You are a test agent.", "hi",
    ]


def test_failure_propagates_when_there_is_no_fallback(spec, backends, monkeypatch) -> None:
    monkeypatch.setattr(settings, "FALLBACK_BACKEND", "primary")  # same as chosen
    backends["primary"].fails = True
    agent = Agent(spec)

    with pytest.raises(RuntimeError):
        agent.respond("U1", "hi")


def test_switching_backend(spec, backends) -> None:
    backends["fallback"].results = [ChatResult(text="from the other one")]
    agent = Agent(spec)

    assert agent.set_backend("U1", "fallback") is True
    assert agent.backend_name("U1") == "fallback"
    assert agent.backend_label("U1") == "Scripted (fallback)"
    assert agent.respond("U1", "hi") == "from the other one"


def test_switching_to_an_unknown_backend_is_refused(spec, backends) -> None:
    agent = Agent(spec)
    assert agent.set_backend("U1", "gpt") is False
    assert agent.backend_name("U1") == "primary"


def test_reset_clears_only_that_user(spec, backends) -> None:
    backends["primary"].results = [ChatResult(text=t) for t in ("a", "b", "c")]
    agent = Agent(spec)

    agent.respond("U1", "remember me")
    agent.respond("U2", "and me")
    agent.reset("U1")
    agent.respond("U1", "who am I")

    assert [m["content"] for m in backends["primary"].seen[2]] == [
        "You are a test agent.", "who am I",
    ]


def test_users_do_not_share_history(spec, backends) -> None:
    backends["primary"].results = [ChatResult(text=t) for t in ("a", "b")]
    agent = Agent(spec)

    agent.respond("U1", "mine")
    agent.respond("U2", "theirs")

    assert "mine" not in [m["content"] for m in backends["primary"].seen[1]]


def test_trimmed_history_still_starts_with_a_user_message(spec, backends, monkeypatch) -> None:
    """Regression: the bounded deque evicts the oldest message on append, which
    could leave an assistant reply at the front — a shape providers reject."""
    monkeypatch.setattr(settings, "MAX_TURNS", 1)  # room for one exchange
    backends["primary"].results = [ChatResult(text="a"), ChatResult(text="b")]
    agent = Agent(spec)

    agent.respond("U1", "first")
    agent.respond("U1", "second")

    sent = backends["primary"].seen[1]
    assert sent[0]["role"] == "system"
    assert sent[1]["role"] == "user"
    assert all(m["role"] != "assistant" for m in sent[:2])


def test_tool_names_reports_the_registered_tools(spec, backends) -> None:
    assert Agent(spec).tool_names() == ["echo", "explode"]
