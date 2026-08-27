"""Local Ollama backend, via Ollama's /api/chat.

The thinnest of the three: Ollama's tool format is the one the registry emits, so
messages pass straight through.
"""

from __future__ import annotations

from .. import settings
from .base import ChatResult, HttpChatBackend, ToolCall


class OllamaBackend(HttpChatBackend):
    name = "ollama"

    @property
    def label(self) -> str:
        return f"Ollama (local, {settings.OLLAMA_MODEL})"

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        body = self._post(
            f"{settings.OLLAMA_HOST}/api/chat",
            {
                "model": settings.OLLAMA_MODEL,
                "messages": messages,
                "tools": tools,
                "stream": False,
            },
        )
        message = body["message"]

        # Arguments already arrive as a dict. Ollama assigns no call id, so we use
        # the position in the list.
        calls = [
            ToolCall(
                id=str(i),
                name=call["function"]["name"],
                args=call["function"].get("arguments") or {},
            )
            for i, call in enumerate(message.get("tool_calls") or [])
        ]
        return ChatResult(
            text=(message.get("content") or "").strip(), tool_calls=calls, raw=message
        )

    def tool_result_message(self, call: ToolCall, content: str) -> dict:
        return {"role": "tool", "tool_name": call.name, "content": content}
