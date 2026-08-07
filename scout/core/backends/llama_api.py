"""Hosted Meta Llama API backend (https://api.llama.com).

Differs from Ollama in a few ways we normalize here:
  - the assistant turn is under `completion_message`, not `message`;
  - `content` may be a string or a {"type": "text", "text": ...} object;
  - tool-call `arguments` arrive as a JSON *string*, not a dict;
  - tool results are echoed back keyed by `tool_call_id`, not `tool_name`.
"""

from __future__ import annotations

import json

import requests

from .. import settings
from .base import ChatResult, ToolCall


class LlamaApiBackend:
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

        resp = requests.post(
            settings.LLAMA_API_URL,
            headers={
                "Authorization": f"Bearer {settings.LLAMA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.LLAMA_MODEL,
                "messages": messages,
                "tools": tools,
                "stream": False,
            },
            timeout=settings.MODEL_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        msg = resp.json()["completion_message"]

        content = msg.get("content")
        if isinstance(content, dict):  # native format wraps text: {"type": "text", "text": ...}
            content = content.get("text", "")

        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c["function"]
            args = fn.get("arguments")
            if isinstance(args, str):  # Llama API returns arguments as a JSON string
                try:
                    args = json.loads(args or "{}")
                except json.JSONDecodeError:
                    args = {}
            calls.append(ToolCall(id=c.get("id") or fn["name"], name=fn["name"], args=args or {}))

        return ChatResult(text=(content or "").strip(), tool_calls=calls, raw=msg)

    def assistant_message(self, result: ChatResult) -> dict:
        # Reconstruct a clean assistant message (re-encoding arguments as a JSON string)
        # rather than echoing the raw completion_message, which carries extra fields.
        msg: dict = {"role": "assistant", "content": result.text}
        if result.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.args)},
                }
                for c in result.tool_calls
            ]
        return msg

    def tool_result_message(self, call: ToolCall, content: str) -> dict:
        return {"role": "tool", "tool_call_id": call.id, "content": content}
