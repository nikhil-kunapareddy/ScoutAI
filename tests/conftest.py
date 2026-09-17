"""Shared fixtures and test doubles.

The suite never touches the network or Slack: chat models are scripted, and HTTP
calls in the job tools are stubbed per-test. That keeps it runnable in CI with no
credentials — which is also why ``scout.core.settings`` must not raise on import
when ``.env`` is absent.

The scripted models are installed into ``scout.core.models`` as if they were real
backends, so the graph reaches them through ``build``/``with_tools``/``label``
exactly as it reaches Claude or Ollama.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from scout.core import models as models_module
from scout.core import settings
from scout.core.agent import AgentSpec
from scout.tools import build_registry
from scout.tools.jobs import fetch as jobs_fetch

# Importing ``scout.core.settings`` loads the developer's ``.env``, which may
# switch LangSmith tracing on — and a test run must not ship anything to a
# hosted service. Switched off here, after the import that could enable it, so
# the suite behaves the same on a laptop as it does in CI.
for _tracing in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
    os.environ[_tracing] = "false"

# Langfuse is the other way round: Scout reads the key pair itself (see
# ``scout.core.tracing``), and that pair is the switch. Both copies are pinned —
# the environment, because ``test_config.py`` reloads ``settings`` and dotenv
# will not overwrite a key that is already set, and the already-loaded module,
# because the import above has read it once.
#
# The host is pinned for a sharper reason: the SDK reads ``LANGFUSE_BASE_URL``
# from the environment *ahead* of the host it is handed, so a developer with a
# real one in ``.env`` had the one test that builds a real client posting to
# Langfuse Cloud (a 401, with fake keys — but egress all the same).
for _key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_HOST"):
    os.environ[_key] = ""
settings.LANGFUSE_PUBLIC_KEY = ""
settings.LANGFUSE_SECRET_KEY = ""


class ScriptedModel(BaseChatModel):
    """A chat model that replays a canned list of ``AIMessage``s.

    Records every message list it was handed, so tests can assert on what the
    graph actually sent: the instructions, the trimming, the tool results.
    """

    backend: str = "scripted"
    replies: list[AIMessage] = []
    fails: bool = False
    #: Start failing once this many calls have been made (None = never).
    fail_after: int | None = None
    seen: list[list[BaseMessage]] = []
    bound_tools: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> ScriptedModel:
        """Record the tool set and stay this object, so ``seen`` keeps working."""
        self.bound_tools = [tool.name for tool in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(list(messages))
        if self.fails or (self.fail_after is not None and len(self.seen) > self.fail_after):
            raise RuntimeError("backend exploded")
        reply = (
            self.replies.pop(0) if self.replies else AIMessage("no more scripted replies")
        )
        return ChatResult(generations=[ChatGeneration(message=reply)])


def calls_tool(name: str, args: dict | None = None, call_id: str = "1") -> AIMessage:
    """A reply that asks for one tool."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}],
    )


def texts(messages: list[BaseMessage]) -> list[str]:
    """The text of each message, for asserting on what a model was sent."""
    return [message.text for message in messages]


@pytest.fixture
def chat_models(monkeypatch) -> dict[str, ScriptedModel]:
    """Install scripted models as the only backends, with a fallback configured."""
    registry = {
        "primary": ScriptedModel(backend="primary"),
        "fallback": ScriptedModel(backend="fallback"),
    }
    monkeypatch.setattr(
        models_module,
        "_BUILDERS",
        {name: (lambda model=model: model) for name, model in registry.items()},
    )
    monkeypatch.setattr(
        models_module,
        "_LABELS",
        {name: (lambda n=name: f"Scripted ({n})") for name in registry},
    )
    monkeypatch.setattr(models_module, "_REQUEST_OPTIONS", {})
    monkeypatch.setattr(settings, "FALLBACK_BACKEND", "fallback")
    models_module.reset_cache()
    yield registry
    models_module.reset_cache()


@pytest.fixture
def echo_tool_module():
    """A minimal tool module: one ``echo`` tool and one that always raises."""

    class Module:
        @staticmethod
        def register(reg) -> None:
            @reg.tool
            def echo(text: str = "") -> str:
                """Echo back the given text.

                Args:
                    text: What to echo.
                """
                return f"echo:{text}"

            @reg.tool
            def explode() -> str:
                """Always fail, to exercise tool error handling."""
                raise ValueError("tool blew up")

    return Module


@pytest.fixture
def spec(echo_tool_module) -> AgentSpec:
    return AgentSpec(
        key="test",
        name="Test Agent",
        system_prompt="You are a test agent.",
        tool_modules=[echo_tool_module],
        default_backend="primary",
    )


# --- Stubbed HTTP, for the tools that fetch ---------------------------------


class FakeResponse:
    def __init__(self, json_data: dict | None = None, text: str = "") -> None:
        self._json = json_data or {}
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._json


class FakeRequests:
    """A stand-in for the ``requests`` module, recording every call."""

    def __init__(self, response: FakeResponse | None = None,
                 error: Exception | None = None) -> None:
        self.response = response or FakeResponse()
        self.error = error
        self.calls: list[dict] = []

    def _handle(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self.error:
            raise self.error
        return self.response

    get = _handle
    post = _handle


def stub_requests(monkeypatch, module, fake: FakeRequests) -> None:
    """Point ``module``'s HTTP at ``fake``.

    Job sources reach the network through ``scout.tools.jobs.fetch``, so that is
    the one seam to stub; a module that still calls ``requests`` itself (the
    location tool) is stubbed in place.
    """
    target = module if hasattr(module, "requests") else jobs_fetch
    monkeypatch.setattr(target, "requests", fake)


def registered_tools(*modules) -> dict:
    """Every tool the given modules register, by name, in registration order."""
    return {tool.name: tool for tool in build_registry(list(modules)).tools}


def call_tool(module, tool_name: str, fake: FakeRequests, monkeypatch, **args) -> str:
    """Register ``module``'s tools against a stubbed ``requests`` and call one."""
    stub_requests(monkeypatch, module, fake)
    registry = build_registry([module])
    tool = next(t for t in registry.tools if t.name == tool_name)
    return tool.invoke(args)
