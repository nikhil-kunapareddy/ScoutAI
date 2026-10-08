"""The model layer: building Claude, its label, and the credential error.

No test here makes a request — the integration is only constructed, which is
what has to work before a key is checked anywhere else.
"""

from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic

from scout.core import models, settings


@pytest.fixture(autouse=True)
def clear_cache():
    """Each test builds its own model, so settings changes are picked up."""
    models.reset_cache()
    yield
    models.reset_cache()


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")


def test_the_label_names_the_model_in_use(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "claude-opus-5")
    assert models.label() == "Claude (claude-opus-5)"


def test_the_model_is_claude(key) -> None:
    assert isinstance(models.build(), ChatAnthropic)


def test_the_model_is_cached(key) -> None:
    assert models.build() is models.build()


def test_a_missing_key_names_the_variable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        models.build()


def test_the_model_takes_its_limits_from_settings(key, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_MAX_TOKENS", 1234)
    monkeypatch.setattr(settings, "MODEL_REQUEST_TIMEOUT_SECONDS", 42)

    model = models.build()
    assert model.max_tokens == 1234
    assert model.default_request_timeout == 42


# --- Binding tools --------------------------------------------------------


def test_tools_and_request_options_both_survive_binding(key, monkeypatch) -> None:
    """Regression: ``bind_tools`` discards kwargs bound before it, so binding the
    Claude ``effort`` option first would drop it silently."""
    monkeypatch.setattr(settings, "ANTHROPIC_EFFORT", "high")

    from scout.tools import build_registry, clock

    bound = models.with_tools(build_registry([clock]).tools)

    assert bound.kwargs["output_config"] == {"effort": "high"}
    assert [t["name"] for t in bound.kwargs["tools"]] == [
        "get_current_time", "get_current_date",
    ]


def test_a_toolless_agent_still_gets_its_request_options(key, monkeypatch) -> None:
    """The registry is empty for an agent that declares no tool modules."""
    monkeypatch.setattr(settings, "ANTHROPIC_EFFORT", "medium")

    bound = models.with_tools([])

    assert "tools" not in bound.kwargs
    assert bound.kwargs["output_config"] == {"effort": "medium"}


def test_an_unset_effort_is_not_sent(key, monkeypatch) -> None:
    """Haiku 4.5 rejects ``effort``, so an empty setting must leave it off the
    request entirely rather than send an empty value."""
    monkeypatch.setattr(settings, "ANTHROPIC_EFFORT", "")

    bound = models.with_tools([])

    assert "output_config" not in bound.kwargs
