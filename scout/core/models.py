"""The chat model every agent runs on: Claude, through ``ChatAnthropic``.

The graph in ``scout/core/agent.py`` binds tools and sends messages through
LangChain's chat-model interface, so conversation history is stored as LangChain
message objects — the form the checkpointer serializes and the Langfuse handler
reads.

The model is built on first use and cached: ``ChatAnthropic`` raises without a
key, and a missing key must not stop the process from starting — ``scout
doctor`` still has to be able to say what is missing.
"""

from __future__ import annotations

import threading

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from . import settings


def label() -> str:
    """Human-readable name of the model in use."""
    return f"Claude ({settings.ANTHROPIC_MODEL})"


def _claude() -> BaseChatModel:
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env — Claude is the only model "
            "Scout runs on."
        )
    # `thinking` is left unset deliberately: Opus 5 runs adaptive thinking by
    # default, and `effort` (see _request_options) is the knob that trades depth
    # for latency.
    return ChatAnthropic(
        model=settings.ANTHROPIC_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=settings.ANTHROPIC_MAX_TOKENS,
        default_request_timeout=settings.MODEL_REQUEST_TIMEOUT_SECONDS,
    )


def _request_options() -> dict:
    """Per-request options for Claude, applied after the tools are bound.

    ``effort`` has no field on ``ChatAnthropic`` — passing it in ``model_kwargs``
    works but warns, so it is bound per request instead.
    """
    return {"output_config": {"effort": settings.ANTHROPIC_EFFORT}}


_model: BaseChatModel | None = None
_model_guard = threading.Lock()


def build() -> BaseChatModel:
    """The chat model, built on first use and cached.

    Raises ``RuntimeError`` when ``ANTHROPIC_API_KEY`` isn't configured.
    """
    global _model
    with _model_guard:
        if _model is None:
            _model = _claude()
        return _model


def with_tools(tools: list[BaseTool]) -> Runnable[LanguageModelInput, BaseMessage]:
    """The model with ``tools`` and its request options bound.

    Order matters: ``bind_tools`` discards kwargs bound before it, so the
    request options have to go on last or they are silently dropped.
    """
    model = build()
    bound: Runnable[LanguageModelInput, BaseMessage] = (
        model.bind_tools(tools) if tools else model
    )
    return bound.bind(**_request_options())


def reset_cache() -> None:
    """Drop the cached model, so changed settings are picked up. For tests."""
    global _model
    with _model_guard:
        _model = None
