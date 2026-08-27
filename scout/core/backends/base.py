"""Shared types and base classes for chat backends.

A backend turns chat messages + tool schemas into a normalized ``ChatResult``, so
the tool loop in ``scout/core/agent.py`` doesn't care which provider replied. It
also translates tool calls and results into that provider's wire format — the
three differ, and ``ChatBackend`` is where the contract lives.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

import requests

from .. import settings


@dataclass
class ToolCall:
    """A single tool the model asked us to run."""

    id: str     # provider's call id (synthesized for Ollama, which sends none)
    name: str
    args: dict  # already decoded to a dict


@dataclass
class ChatResult:
    """A normalized assistant turn from any backend."""

    #: Final text. May be empty when the model only asked for tools.
    text: str
    #: Empty means this is the final reply; otherwise the loop runs these.
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: The provider's own assistant message, echoed back on the next request.
    raw: dict = field(default_factory=dict)


class ChatBackend(ABC):
    """One model provider, normalized to a small interface.

    Subclasses set ``name`` (the id users switch with, e.g. ``--ollama``) and
    implement ``label`` and ``chat``. ``assistant_message`` defaults to echoing
    the provider's own message back, which Ollama accepts and Anthropic requires.
    """

    #: Registry key and user-facing backend id.
    name: ClassVar[str]

    @property
    @abstractmethod
    def label(self) -> str:
        """Name shown to users, including the model in use."""

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        """Send one request and return the assistant turn, normalized."""

    def assistant_message(self, result: ChatResult) -> dict:
        """The assistant turn to append before feeding tool results back."""
        return result.raw

    @abstractmethod
    def tool_result_message(self, call: ToolCall, content: str) -> dict:
        """Wrap one tool's output in the shape this provider expects."""


class HttpChatBackend(ChatBackend):
    """Base for backends that POST JSON to a chat endpoint.

    Holds the timeout and error check, so subclasses only describe what is
    provider-specific: the URL, the body, and how to read the response.
    """

    def _post(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=settings.MODEL_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
