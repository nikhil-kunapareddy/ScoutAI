"""The agent graph: the tool loop, failed model calls, and per-user threads."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from scout.core import settings
from scout.core.agent import ConversationalAgent
from scout.core.runner import STUCK_REPLY, Agent

from .conftest import calls_tool, texts


def test_agent_satisfies_the_adapter_interface(spec, chat_model) -> None:
    assert isinstance(Agent(spec), ConversationalAgent)


def test_plain_reply_sends_the_instructions_first(spec, chat_model) -> None:
    chat_model.replies = [AIMessage("hello there")]
    agent = Agent(spec)

    assert agent.respond("U1", "hi") == "hello there"

    sent = chat_model.seen[0]
    assert sent[0].type == "system"
    assert sent[0].text == "You are a test agent."
    assert sent[1].type == "human"
    assert sent[1].text == "hi"


def test_tools_are_bound_for_the_model(spec, chat_model) -> None:
    chat_model.replies = [AIMessage("hi")]
    Agent(spec).respond("U1", "hi")
    assert chat_model.bound_tools == ["echo", "explode"]


def test_history_carries_across_turns(spec, chat_model) -> None:
    chat_model.replies = [AIMessage("one"), AIMessage("two")]
    agent = Agent(spec)

    agent.respond("U1", "first")
    agent.respond("U1", "second")

    assert texts(chat_model.seen[1]) == [
        "You are a test agent.", "first", "one", "second",
    ]


def test_tool_call_is_run_and_fed_back(spec, chat_model) -> None:
    chat_model.replies = [
        calls_tool("echo", {"text": "hi"}),
        AIMessage("the tool said hi"),
    ]
    agent = Agent(spec)

    assert agent.respond("U1", "use the tool") == "the tool said hi"

    # The second request carries the assistant turn plus the tool result.
    second_request = chat_model.seen[1]
    assert second_request[-1].type == "tool"
    assert second_request[-1].content == "echo:hi"


def test_failing_tool_becomes_a_message_for_the_model(spec, chat_model) -> None:
    chat_model.replies = [calls_tool("explode"), AIMessage("I saw the error")]
    agent = Agent(spec)

    assert agent.respond("U1", "break it") == "I saw the error"
    assert "Error running tool: tool blew up" in chat_model.seen[1][-1].content


def test_unknown_tool_is_reported_to_the_model(spec, chat_model) -> None:
    """The model gets a readable message and can correct itself on the next hop."""
    chat_model.replies = [calls_tool("nope"), AIMessage("ok")]
    agent = Agent(spec)

    agent.respond("U1", "call a missing tool")
    reported = chat_model.seen[1][-1]
    assert "nope is not a valid tool" in reported.content
    assert reported.status == "error"


def test_empty_reply_becomes_something_readable(spec, chat_model) -> None:
    """A refusal or a max_tokens cut-off must not be sent to Slack as ''."""
    chat_model.replies = [AIMessage("")]
    assert Agent(spec).respond("U1", "hi").startswith("The model returned an empty reply")


# --- The tool-hop limit ---------------------------------------------------


def test_tool_hop_limit_ends_the_turn(spec, chat_model, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAX_TOOL_HOPS", 3)
    # Always asks for another tool: the loop must stop on its own.
    chat_model.replies = [calls_tool("echo") for _ in range(10)]
    agent = Agent(spec)

    assert agent.respond("U1", "loop forever") == STUCK_REPLY
    assert len(chat_model.seen) == 3


def test_thread_survives_the_hop_limit(spec, chat_model, monkeypatch) -> None:
    """Regression guard: the abandoned tool calls have to be answered in the
    history, or the user's *next* message is rejected by the provider."""
    monkeypatch.setattr(settings, "MAX_TOOL_HOPS", 2)
    chat_model.replies = [calls_tool("echo") for _ in range(10)]
    agent = Agent(spec)
    agent.respond("U1", "loop forever")

    chat_model.replies = [AIMessage("back to normal")]
    assert agent.respond("U1", "hello again") == "back to normal"

    # Every tool call in the replayed history has a matching result.
    sent = chat_model.seen[-1]
    requested = [c["id"] for m in sent for c in getattr(m, "tool_calls", [])]
    answered = [m.tool_call_id for m in sent if m.type == "tool"]
    assert sorted(requested) == sorted(answered)


# --- A failed model call ------------------------------------------------


def test_a_failed_model_call_fails_the_turn(spec, chat_model) -> None:
    """There is no other model to retry on: the error reaches the Slack layer,
    which reports it."""
    chat_model.fails = True

    with pytest.raises(RuntimeError, match="model exploded"):
        Agent(spec).respond("U1", "hi")


def test_a_failed_call_leaves_the_thread_usable(spec, chat_model) -> None:
    """A node that raises commits nothing, so the thread keeps the tool results
    the turn already fetched and the user's next message goes through."""
    # The first call succeeds and runs the tool; the second one fails.
    chat_model.replies = [calls_tool("echo", {"text": "hi"})]
    chat_model.fail_after = 1
    agent = Agent(spec)
    with pytest.raises(RuntimeError):
        agent.respond("U1", "use the tool")

    chat_model.fail_after = None
    chat_model.replies = [AIMessage("back to normal")]
    assert agent.respond("U1", "hello again") == "back to normal"

    sent = chat_model.seen[-1]
    assert "echo:hi" in texts(sent)
    requested = [c["id"] for m in sent for c in getattr(m, "tool_calls", [])]
    answered = [m.tool_call_id for m in sent if m.type == "tool"]
    assert sorted(requested) == sorted(answered)


# --- Per-user state -------------------------------------------------------


def test_reset_clears_only_that_user(spec, chat_model) -> None:
    chat_model.replies = [AIMessage(t) for t in ("a", "b", "c")]
    agent = Agent(spec)

    agent.respond("U1", "remember me")
    agent.respond("U2", "and me")
    agent.reset("U1")
    agent.respond("U1", "who am I")

    assert texts(chat_model.seen[2]) == ["You are a test agent.", "who am I"]


def test_users_do_not_share_history(spec, chat_model) -> None:
    chat_model.replies = [AIMessage(t) for t in ("a", "b")]
    agent = Agent(spec)

    agent.respond("U1", "mine")
    agent.respond("U2", "theirs")

    assert "mine" not in texts(chat_model.seen[1])


def test_trimmed_history_still_starts_with_a_user_message(spec, chat_model, monkeypatch) -> None:
    """Providers reject a conversation that opens on the assistant's side, so the
    window has to advance to a user message however it falls."""
    monkeypatch.setattr(settings, "MAX_TURNS", 1)  # room for one exchange
    chat_model.replies = [AIMessage("a"), AIMessage("b")]
    agent = Agent(spec)

    agent.respond("U1", "first")
    agent.respond("U1", "second")

    sent = chat_model.seen[1]
    assert sent[0].type == "system"
    assert sent[1].type == "human"


def test_tool_names_reports_the_registered_tools(spec, chat_model) -> None:
    assert Agent(spec).tool_names() == ["echo", "explode"]
