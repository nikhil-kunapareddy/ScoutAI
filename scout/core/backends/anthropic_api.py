"""Hosted Anthropic Claude backend (the platform default), via the official SDK.

Claude's wire format differs from Ollama/Llama in four ways, normalized here:
  - the system prompt is a request field, not a message;
  - tool schemas use ``input_schema``, not ``function.parameters``;
  - tool results are ``tool_result`` blocks in one *user* message per assistant turn;
  - the assistant turn is a list of content blocks (text / thinking / tool_use)
    that Claude requires echoed back unchanged, so ``ChatResult.raw`` keeps the
    SDK's own block objects and we inherit ``assistant_message``.
"""

from __future__ import annotations

import anthropic

from .. import settings
from .base import ChatBackend, ChatResult, ToolCall


class AnthropicBackend(ChatBackend):
    name = "anthropic"

    def __init__(self) -> None:
        self._sdk: anthropic.Anthropic | None = None

    @property
    def label(self) -> str:
        return f"Claude ({settings.ANTHROPIC_MODEL})"

    def _client(self) -> anthropic.Anthropic:
        """Build the client on first use, so a missing key breaks only this
        backend and not process start-up. A concurrent double-build is harmless."""
        if self._sdk is None:
            if not settings.ANTHROPIC_API_KEY:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Add it to .env to use the Claude "
                    "backend, or switch to the local model with --ollama."
                )
            self._sdk = anthropic.Anthropic(
                api_key=settings.ANTHROPIC_API_KEY,
                timeout=settings.MODEL_REQUEST_TIMEOUT_SECONDS,
            )
        return self._sdk

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        system, conversation = _split_system(messages)
        # `thinking` is left unset deliberately: Opus 5 runs adaptive thinking by
        # default, and `effort` is the knob that trades depth for latency.
        resp = self._client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=settings.ANTHROPIC_MAX_TOKENS,
            output_config={"effort": settings.ANTHROPIC_EFFORT},
            system=system,
            messages=conversation,
            tools=[_tool_schema(t) for t in tools],
        )

        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        calls = [
            ToolCall(id=b.id, name=b.name, args=b.input or {})
            for b in resp.content
            if b.type == "tool_use"
        ]
        if not text and not calls:  # e.g. a refusal, or a max_tokens cut-off
            text = f"Claude returned no reply (stop reason: {resp.stop_reason})."

        return ChatResult(
            text=text,
            tool_calls=calls,
            raw={"role": "assistant", "content": resp.content},
        )

    def tool_result_message(self, call: ToolCall, content: str) -> dict:
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": call.id, "content": content}
            ],
        }


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Lift the system prompt out of ``messages`` and merge tool-result turns.

    Claude takes the system prompt as its own field, and wants every
    ``tool_result`` answering one assistant turn in a single user message —
    splitting them teaches the model to stop calling tools in parallel. The agent
    loop appends one message per tool, so they're coalesced here rather than
    making every other backend care.
    """
    system: list[str] = []
    conversation: list[dict] = []
    for message in messages:
        if message["role"] == "system":
            system.append(message["content"])
            continue
        content = message["content"]
        previous = conversation[-1] if conversation else None
        both_blocks = (
            isinstance(content, list) and previous and isinstance(previous["content"], list)
        )
        if both_blocks and previous["role"] == message["role"] == "user":
            previous["content"] = [*previous["content"], *content]
        else:
            conversation.append({"role": message["role"], "content": content})
    return "\n\n".join(system), conversation


def _tool_schema(schema: dict) -> dict:
    """Convert one registry tool schema (Ollama's shape) to Claude's."""
    fn = schema["function"]
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
    }
