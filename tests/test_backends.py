"""Per-provider wire-format translation.

Each backend's job is to hide one provider's quirks, so these tests pin the
translations that the agent loop depends on.
"""

from __future__ import annotations

import pytest

from scout.core import settings
from scout.core.backends import backend_names, build_backends
from scout.core.backends.anthropic_api import AnthropicBackend, _split_system, _tool_schema
from scout.core.backends.base import ChatBackend, ChatResult, ToolCall
from scout.core.backends.llama_api import LlamaApiBackend
from scout.core.backends.ollama import OllamaBackend


def test_every_backend_implements_the_interface() -> None:
    backends = build_backends()
    assert set(backends) == set(backend_names())
    for backend in backends.values():
        assert isinstance(backend, ChatBackend)
        assert backend.label  # never blank: it's shown to users on --backend


# --- Anthropic ------------------------------------------------------------


def test_split_system_lifts_the_system_prompt_out() -> None:
    system, convo = _split_system([
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ])
    assert system == "be brief"
    assert convo == [{"role": "user", "content": "hi"}]


def test_split_system_joins_multiple_system_messages() -> None:
    system, convo = _split_system([
        {"role": "system", "content": "one"},
        {"role": "system", "content": "two"},
        {"role": "user", "content": "hi"},
    ])
    assert system == "one\n\ntwo"
    assert len(convo) == 1


def test_split_system_merges_consecutive_tool_results() -> None:
    """Claude wants every tool_result answering one turn in a single user
    message; splitting them teaches the model to stop calling tools in parallel."""
    _, convo = _split_system([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "tool_use"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b"}]},
    ])
    assert len(convo) == 3
    assert convo[-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "a"},
        {"type": "tool_result", "tool_use_id": "b"},
    ]


def test_split_system_leaves_plain_text_turns_alone() -> None:
    _, convo = _split_system([
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ])
    assert [m["content"] for m in convo] == ["one", "two", "three"]


def test_tool_schema_converts_to_claudes_shape() -> None:
    claude_tool = _tool_schema({
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search things.",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}},
                           "required": ["q"]},
        },
    })
    assert claude_tool == {
        "name": "search",
        "description": "Search things.",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}},
                         "required": ["q"]},
    }


def test_tool_schema_supplies_an_empty_input_schema() -> None:
    """Claude rejects a tool with no input_schema, even for a no-argument tool."""
    claude_tool = _tool_schema({"function": {"name": "get_time"}})
    assert claude_tool["input_schema"] == {"type": "object", "properties": {}}


def test_anthropic_tool_result_is_a_user_message_block() -> None:
    backend = AnthropicBackend()
    assert backend.tool_result_message(ToolCall("abc", "search", {}), "result") == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "abc", "content": "result"}],
    }


def test_anthropic_echoes_its_own_content_blocks_back() -> None:
    """Thinking blocks must come back unchanged, so the raw message is reused."""
    backend = AnthropicBackend()
    raw = {"role": "assistant", "content": ["<block objects>"]}
    assert backend.assistant_message(ChatResult(text="", raw=raw)) is raw


def test_anthropic_without_a_key_explains_itself(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        AnthropicBackend().chat([{"role": "user", "content": "hi"}], [])


# --- Ollama ---------------------------------------------------------------


def test_ollama_parses_a_plain_reply(monkeypatch) -> None:
    backend = OllamaBackend()
    monkeypatch.setattr(backend, "_post", lambda *a, **k: {"message": {"content": " hi "}})

    result = backend.chat([{"role": "user", "content": "hi"}], [])
    assert result.text == "hi"
    assert result.tool_calls == []


def test_ollama_synthesizes_tool_call_ids(monkeypatch) -> None:
    """Ollama assigns no call id, so position in the list becomes the id."""
    backend = OllamaBackend()
    monkeypatch.setattr(backend, "_post", lambda *a, **k: {"message": {
        "content": None,
        "tool_calls": [
            {"function": {"name": "a", "arguments": {"x": 1}}},
            {"function": {"name": "b"}},  # no arguments key at all
        ],
    }})

    result = backend.chat([], [])
    assert result.text == ""
    assert [(c.id, c.name, c.args) for c in result.tool_calls] == [
        ("0", "a", {"x": 1}),
        ("1", "b", {}),
    ]


def test_ollama_tool_result_is_keyed_by_name() -> None:
    call = ToolCall("0", "get_location", {})
    assert OllamaBackend().tool_result_message(call, "Boston") == {
        "role": "tool", "tool_name": "get_location", "content": "Boston",
    }


# --- Meta Llama API -------------------------------------------------------


def test_llama_without_a_key_explains_itself(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLAMA_API_KEY", "")
    with pytest.raises(RuntimeError, match="LLAMA_API_KEY is not set"):
        LlamaApiBackend().chat([], [])


def test_llama_unwraps_object_content_and_decodes_arguments(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLAMA_API_KEY", "test-key")
    backend = LlamaApiBackend()
    monkeypatch.setattr(backend, "_post", lambda *a, **k: {"completion_message": {
        "content": {"type": "text", "text": "working on it"},
        "tool_calls": [
            {"id": "call_1", "function": {"name": "search", "arguments": '{"q": "ml"}'}},
        ],
    }})

    result = backend.chat([], [])
    assert result.text == "working on it"
    assert result.tool_calls[0].args == {"q": "ml"}  # decoded from a JSON string


@pytest.mark.parametrize("arguments,expected", [
    ('{"q": "ml"}', {"q": "ml"}),
    ("not json", {}),      # malformed arguments must not kill the turn
    ("", {}),
    (None, {}),
    ({"q": "ml"}, {"q": "ml"}),  # already a dict
])
def test_llama_tool_call_argument_decoding(arguments: object, expected: dict) -> None:
    call = LlamaApiBackend._to_tool_call({"function": {"name": "search", "arguments": arguments}})
    assert call.args == expected
    assert call.id == "search"  # falls back to the name when no id is supplied


def test_llama_assistant_message_reencodes_arguments() -> None:
    result = ChatResult(text="hi", tool_calls=[ToolCall("call_1", "search", {"q": "ml"})])
    message = LlamaApiBackend().assistant_message(result)
    assert message == {
        "role": "assistant",
        "content": "hi",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q": "ml"}'},
        }],
    }


def test_llama_assistant_message_omits_empty_tool_calls() -> None:
    message = LlamaApiBackend().assistant_message(ChatResult(text="done"))
    assert message == {"role": "assistant", "content": "done"}
