"""Hosted Meta Llama API backend (https://api.llama.com).

Differences from Ollama, normalized here:
  - the assistant turn is under ``completion_message``, not ``message``;
  - ``content`` may be a string or a ``{"type": "text", "text": ...}`` object;
  - tool-call ``arguments`` arrive as a JSON *string*;
  - tool results are keyed by ``tool_call_id``, not ``tool_name``.
"""

from __future__ import annotations

import json

from .. import settings
from .base import ChatResult, HttpChatBackend, ToolCall


class LlamaApiBackend(HttpChatBackend):
    name = "llama"

    @property
    def label(self) -> str:
        return f"Meta Llama API ({settings.LLAMA_MODEL})"

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        if not settings.LLAMA_API_KEY:
            raise RuntimeError(
                "LLAMA_API_KEY is not set. Add it to .env to use the Llama API backend, "
                "or switch back with --ollama."
            )

        body = self._post(
            settings.LLAMA_API_URL,
            {
                "model": settings.LLAMA_MODEL,
                "messages": messages,
                "tools": tools,
                "stream": False,
            },
            headers={
                "Authorization": f"Bearer {settings.LLAMA_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        message = body["completion_message"]

        content = message.get("content")
        if isinstance(content, dict):  # native format wraps text
            content = content.get("text", "")

        calls = [self._to_tool_call(c) for c in message.get("tool_calls") or []]
        return ChatResult(text=(content or "").strip(), tool_calls=calls, raw=message)

    @staticmethod
    def _to_tool_call(payload: dict) -> ToolCall:
        """Normalize one tool call, decoding its JSON-string arguments."""
        fn = payload["function"]
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {}
        return ToolCall(id=payload.get("id") or fn["name"], name=fn["name"], args=args or {})

    def assistant_message(self, result: ChatResult) -> dict:
        # Rebuilt rather than echoed: the raw completion_message carries extra
        # fields, and arguments have to go back as a JSON string.
        message: dict = {"role": "assistant", "content": result.text}
        if result.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.args)},
                }
                for call in result.tool_calls
            ]
        return message

    def tool_result_message(self, call: ToolCall, content: str) -> dict:
        return {"role": "tool", "tool_call_id": call.id, "content": content}
