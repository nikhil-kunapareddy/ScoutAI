"""Local Ollama backend (calls Ollama's /api/chat)."""

from __future__ import annotations

import requests

from .. import settings
from .base import ChatResult, ToolCall


class OllamaBackend:
    name = "ollama"

    @property
    def label(self) -> str:
        return f"Ollama (local, {settings.OLLAMA_MODEL})"

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        resp = requests.post(
            f"{settings.OLLAMA_HOST}/api/chat",
            json={
                "model": settings.OLLAMA_MODEL,
                "messages": messages,
                "tools": tools,
                "stream": False,
            },
            timeout=settings.MODEL_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        msg = resp.json()["message"]

        # Ollama already returns tool arguments as a dict, and assigns no call id,
        # so we synthesize one from the position in the list.
        calls = [
            ToolCall(
                id=str(i),
                name=c["function"]["name"],
                args=c["function"].get("arguments") or {},
            )
            for i, c in enumerate(msg.get("tool_calls") or [])
        ]
        return ChatResult(text=(msg.get("content") or "").strip(), tool_calls=calls, raw=msg)

    def assistant_message(self, result: ChatResult) -> dict:
        # Ollama accepts its own returned message verbatim on the next request.
        return result.raw

    def tool_result_message(self, call: ToolCall, content: str) -> dict:
        return {"role": "tool", "tool_name": call.name, "content": content}
