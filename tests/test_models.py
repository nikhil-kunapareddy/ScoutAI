"""The model layer: the backend registry, labels, and credential errors.

No test here makes a request — the integrations are only constructed, which is
what has to work with one provider's key missing.
"""

from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from scout.core import models, settings


@pytest.fixture(autouse=True)
def clear_cache():
    """Each test builds its own models, so settings changes are picked up."""
    models.reset_cache()
    yield
    models.reset_cache()


@pytest.fixture
def keys(monkeypatch):
    """Credentials for both hosted providers."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(settings, "LLAMA_API_KEY", "llama-test")


# --- The registry ---------------------------------------------------------


def test_every_switchable_backend_is_known() -> None:
    """The ids the Slack commands switch to have to resolve to a model."""
    assert models.names() == ["anthropic", "ollama", "llama"]
    assert all(models.exists(name) for name in models.names())
    assert not models.exists("gpt")


def test_labels_name_the_model_in_use(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "claude-opus-5")
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setattr(settings, "LLAMA_MODEL", "Llama-4")

    assert models.label("anthropic") == "Claude (claude-opus-5)"
    assert models.label("ollama") == "Ollama (local, llama3.2:3b)"
    assert models.label("llama") == "Meta Llama API (Llama-4)"


def test_unknown_backend_is_reported_not_guessed() -> None:
    with pytest.raises(RuntimeError, match="Unknown backend 'gpt'"):
        models.build("gpt")


def test_each_backend_builds_its_own_integration(keys) -> None:
    assert isinstance(models.build("anthropic"), ChatAnthropic)
    assert isinstance(models.build("ollama"), ChatOllama)
    assert isinstance(models.build("llama"), ChatOpenAI)


def test_models_are_cached(keys) -> None:
    assert models.build("ollama") is models.build("ollama")


# --- Credentials ----------------------------------------------------------


def test_missing_anthropic_key_names_the_variable_and_a_way_out(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError) as exc:
        models.build("anthropic")

    assert "ANTHROPIC_API_KEY is not set" in str(exc.value)
    assert "--ollama" in str(exc.value)


def test_missing_llama_key_names_the_variable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLAMA_API_KEY", "")
    with pytest.raises(RuntimeError, match="LLAMA_API_KEY is not set"):
        models.build("llama")


def test_the_local_backend_needs_no_credentials(monkeypatch) -> None:
    """With no keys at all, Ollama must still build — it is the fallback."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(settings, "LLAMA_API_KEY", "")
    assert isinstance(models.build("ollama"), ChatOllama)


# --- Settings reaching the client ----------------------------------------


def test_anthropic_takes_its_limits_from_settings(keys, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_MAX_TOKENS", 1234)
    monkeypatch.setattr(settings, "MODEL_REQUEST_TIMEOUT_SECONDS", 42)

    model = models.build("anthropic")
    assert model.max_tokens == 1234
    assert model.default_request_timeout == 42


def test_llama_uses_the_openai_compatible_base_url(keys, monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLAMA_BASE_URL", "https://example.test/compat/v1")
    assert models.build("llama").openai_api_base == "https://example.test/compat/v1"


# --- Binding tools --------------------------------------------------------


def test_tools_and_request_options_both_survive_binding(keys, monkeypatch) -> None:
    """Regression: ``bind_tools`` discards kwargs bound before it, so binding the
    Claude ``effort`` option first would drop it silently."""
    monkeypatch.setattr(settings, "ANTHROPIC_EFFORT", "high")

    from scout.tools import build_registry, clock

    bound = models.with_tools("anthropic", build_registry([clock]).tools)

    assert bound.kwargs["output_config"] == {"effort": "high"}
    assert [t["name"] for t in bound.kwargs["tools"]] == [
        "get_current_time", "get_current_date",
    ]


def test_a_backend_with_no_options_still_binds_its_tools(keys) -> None:
    from scout.tools import build_registry, clock

    bound = models.with_tools("ollama", build_registry([clock]).tools)
    assert "tools" in bound.kwargs
    assert "output_config" not in bound.kwargs


def test_a_toolless_agent_gets_the_bare_model(keys) -> None:
    """The registry is empty for an agent that declares no tool modules."""
    assert models.with_tools("ollama", []) is models.build("ollama")
