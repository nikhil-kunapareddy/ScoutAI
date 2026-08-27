"""Shared fixtures and test doubles.

The suite never touches the network or Slack: model backends are scripted, and
HTTP calls in the job tools are stubbed per-test. That keeps it runnable in CI
with no credentials — which is also why ``scout.core.settings`` must not raise on
import when ``.env`` is absent.
"""

from __future__ import annotations

import pytest

from scout.core.agent import AgentSpec
from scout.core.backends.base import ChatBackend, ChatResult, ToolCall


class ScriptedBackend(ChatBackend):
    """A backend that replays a canned list of ``ChatResult``s.

    Records every ``messages`` list it was handed, so tests can assert on what
    the agent loop actually sent (system prompt placement, tool-result shape).
    """

    def __init__(
        self,
        name: str = "scripted",
        results: list[ChatResult] | None = None,
        *,
        fails: bool = False,
    ) -> None:
        self.name = name  # shadows the class-level attribute; fine for a double
        self.results = list(results or [])
        self.fails = fails
        self.seen: list[list[dict]] = []

    @property
    def label(self) -> str:
        return f"Scripted ({self.name})"

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        self.seen.append([dict(m) for m in messages])
        if self.fails:
            raise RuntimeError("backend exploded")
        if self.results:
            return self.results.pop(0)
        return ChatResult(text="no more scripted results")

    def tool_result_message(self, call: ToolCall, content: str) -> dict:
        return {"role": "tool", "tool_name": call.name, "content": content}


def tool_call(name: str, args: dict | None = None, call_id: str = "1") -> ToolCall:
    return ToolCall(id=call_id, name=name, args=args or {})


@pytest.fixture
def echo_tool_module():
    """A minimal tool module: one ``echo`` tool and one that always raises."""

    class Module:
        @staticmethod
        def register(reg):
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
