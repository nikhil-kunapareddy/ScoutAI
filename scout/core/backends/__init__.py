"""Chat backends the bot can talk to.

Each subclasses ``ChatBackend`` (see ``base.py``). ``build_backends()`` creates
one of each so a user can switch at runtime; which one they start on, and which
answers when that one fails, comes from ``settings.DEFAULT_BACKEND`` and
``settings.FALLBACK_BACKEND``.

Adding a provider = one module here plus one line in ``_BACKEND_CLASSES``.
"""

from __future__ import annotations

from .anthropic_api import AnthropicBackend
from .base import ChatBackend, ChatResult, HttpChatBackend, ToolCall
from .llama_api import LlamaApiBackend
from .ollama import OllamaBackend

_BACKEND_CLASSES: dict[str, type[ChatBackend]] = {
    AnthropicBackend.name: AnthropicBackend,
    OllamaBackend.name: OllamaBackend,
    LlamaApiBackend.name: LlamaApiBackend,
}


def build_backends() -> dict[str, ChatBackend]:
    """Instantiate every backend, keyed by name.

    Construction is cheap and credential-free: a backend that needs a key raises
    only when used, so one missing key can't stop the process from starting.
    """
    return {name: cls() for name, cls in _BACKEND_CLASSES.items()}


def backend_names() -> list[str]:
    """Names of every registered backend, in registration order."""
    return list(_BACKEND_CLASSES)


__all__ = [
    "ChatBackend",
    "ChatResult",
    "HttpChatBackend",
    "ToolCall",
    "backend_names",
    "build_backends",
]
