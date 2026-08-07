"""
Chat backends the bot can talk to.

Each backend exposes the same small interface (see `base.py`): `chat()`,
`assistant_message()`, `tool_result_message()`, plus `name` and `label`.
`build_backends()` instantiates one of each so a user can switch between them
at runtime.
"""

from __future__ import annotations

from .base import ChatResult, ToolCall
from .llama_api import LlamaApiBackend
from .ollama import OllamaBackend

_BACKENDS = {
    OllamaBackend.name: OllamaBackend,
    LlamaApiBackend.name: LlamaApiBackend,
}


def build_backends() -> dict:
    """Instantiate every known backend, keyed by name."""
    return {name: cls() for name, cls in _BACKENDS.items()}


def backend_names() -> list[str]:
    return list(_BACKENDS)


__all__ = ["ChatResult", "ToolCall", "build_backends", "backend_names"]
