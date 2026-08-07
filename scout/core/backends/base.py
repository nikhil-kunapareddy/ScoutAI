"""
Shared types for chat backends.

A backend turns a list of chat messages + tool schemas into a normalized
`ChatResult`, so the tool-call loop in `app/llm.py` doesn't care whether the
model came from local Ollama or the hosted Meta Llama API. Each backend is also
responsible for translating tool calls/results back into the wire format that
backend expects (the two providers differ — see the backend modules).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolCall:
    """A single tool the model asked us to run."""
    id: str         # provider-supplied call id (or a synthesized one for Ollama)
    name: str
    args: dict      # arguments already decoded to a dict


@dataclass
class ChatResult:
    """A normalized assistant turn from any backend."""
    text: str                   # final text content (may be empty when only tools were requested)
    tool_calls: list[ToolCall]  # empty means this is the final reply
    raw: dict                   # the provider's raw assistant message, echoed back on the next request
