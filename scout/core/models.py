"""The chat models agents run on, one per backend name.

Each is a LangChain chat model, so the graph in ``scout/core/agent.py`` binds
tools and sends messages the same way whichever provider answers. Conversation
history is therefore provider-agnostic message objects, which each integration
renders into its own wire format — that is what lets a user switch model
mid-conversation without losing context.

Models are built on first use and cached: ``ChatAnthropic`` raises without a key,
and one missing key must not stop the process from starting.

Adding a provider = one builder here plus one entry in ``_BUILDERS``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from . import settings

#: Backend ids — the names users switch with (``--claude``, ``--ollama``, ``--api``).
ANTHROPIC = "anthropic"
OLLAMA = "ollama"
LLAMA = "llama"


def _require_key(value: str, name: str, remedy: str) -> None:
    """Fail readably when a hosted provider has no credentials."""
    if not value:
        raise RuntimeError(f"{name} is not set. {remedy}")


def _anthropic() -> BaseChatModel:
    _require_key(
        settings.ANTHROPIC_API_KEY, "ANTHROPIC_API_KEY",
        "Add it to .env to use the Claude backend, or switch to the local model "
        "with --ollama.",
    )
    # `thinking` is left unset deliberately: Opus 5 runs adaptive thinking by
    # default, and `effort` (see _ANTHROPIC_OPTIONS) is the knob that trades
    # depth for latency.
    return ChatAnthropic(
        model=settings.ANTHROPIC_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=settings.ANTHROPIC_MAX_TOKENS,
        default_request_timeout=settings.MODEL_REQUEST_TIMEOUT_SECONDS,
    )


def _ollama() -> BaseChatModel:
    # No key to check — a local model that isn't running fails on first request,
    # which the fallback and the Slack error path already handle.
    return ChatOllama(
        model=settings.OLLAMA_MODEL,
        base_url=settings.OLLAMA_HOST,
        client_kwargs={"timeout": settings.MODEL_REQUEST_TIMEOUT_SECONDS},
    )


def _llama() -> BaseChatModel:
    _require_key(
        settings.LLAMA_API_KEY, "LLAMA_API_KEY",
        "Add it to .env to use the Meta Llama API, or switch with --claude or "
        "--ollama.",
    )
    # Meta serves an OpenAI-compatible endpoint, so the OpenAI integration is the
    # client rather than a bespoke one. See settings.LLAMA_BASE_URL.
    return ChatOpenAI(
        model=settings.LLAMA_MODEL,
        base_url=settings.LLAMA_BASE_URL,
        api_key=settings.LLAMA_API_KEY,
        timeout=settings.MODEL_REQUEST_TIMEOUT_SECONDS,
    )


_BUILDERS: dict[str, Callable[[], BaseChatModel]] = {
    ANTHROPIC: _anthropic,
    OLLAMA: _ollama,
    LLAMA: _llama,
}

_LABELS: dict[str, Callable[[], str]] = {
    ANTHROPIC: lambda: f"Claude ({settings.ANTHROPIC_MODEL})",
    OLLAMA: lambda: f"Ollama (local, {settings.OLLAMA_MODEL})",
    LLAMA: lambda: f"Meta Llama API ({settings.LLAMA_MODEL})",
}


def _anthropic_options() -> dict:
    """Per-request options for Claude.

    ``effort`` has no field on ``ChatAnthropic`` — passing it in ``model_kwargs``
    works but warns, so it is bound per request instead.
    """
    return {"output_config": {"effort": settings.ANTHROPIC_EFFORT}}


#: Provider-specific request options, applied after the tools are bound.
_REQUEST_OPTIONS: dict[str, Callable[[], dict]] = {ANTHROPIC: _anthropic_options}

_cache: dict[str, BaseChatModel] = {}
_cache_guard = threading.Lock()


def names() -> list[str]:
    """Every backend id, in registration order."""
    return list(_BUILDERS)


def exists(name: str) -> bool:
    """Whether ``name`` is a backend users can switch to."""
    return name in _BUILDERS


def label(name: str) -> str:
    """Human-readable name of a backend, including the model in use."""
    describe = _LABELS.get(name)
    return describe() if describe else name


def build(name: str) -> BaseChatModel:
    """The chat model for ``name``, built on first use and cached.

    Raises ``RuntimeError`` for an unknown backend, or when the provider needs a
    credential that isn't configured.
    """
    with _cache_guard:
        model = _cache.get(name)
        if model is None:
            builder = _BUILDERS.get(name)
            if builder is None:
                raise RuntimeError(f"Unknown backend '{name}'. Known: {', '.join(names())}.")
            model = builder()
            _cache[name] = model
        return model


def with_tools(
    name: str, tools: list[BaseTool]
) -> Runnable[LanguageModelInput, BaseMessage]:
    """The model for ``name`` with ``tools`` and its request options bound.

    Order matters: ``bind_tools`` discards kwargs bound before it, so the
    provider options have to go on last or they are silently dropped.
    """
    bound: Runnable[LanguageModelInput, BaseMessage] = build(name)
    if tools:
        bound = bound.bind_tools(tools)
    options = _REQUEST_OPTIONS.get(name)
    return bound.bind(**options()) if options else bound


def reset_cache() -> None:
    """Drop the cached models, so changed settings are picked up. For tests."""
    with _cache_guard:
        _cache.clear()
