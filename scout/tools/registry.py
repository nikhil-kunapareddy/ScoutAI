"""Registry of Python functions the model can call as tools.

Each registered function becomes a LangChain ``StructuredTool``, which the
``tools`` node of the agent graph runs in-process. Writing a tool is still
writing a normal, typed, documented function: the annotations become the
argument schema and a Google-style ``Args:`` block becomes the per-parameter
descriptions the model reads.

One consequence of handing validation to LangChain: arguments are now checked
against that schema before the function runs, so a small model passing
``limit="ten"`` gets a readable error back and one more hop rather than the
silent coercion ``clamp_int`` used to do on its own. ``clamp_int`` still guards
the bounds (``days=0``, ``limit=999``), which no schema expresses.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool


class ToolRegistry:
    """The tools one agent can call."""

    def __init__(self) -> None:
        self.tools: list[BaseTool] = []  # in registration order

    def tool(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register ``fn`` as a callable tool, building its schema from the
        signature and docstring. Used as a decorator in each module's ``register()``."""
        self.tools.append(
            StructuredTool.from_function(
                fn,
                parse_docstring=True,
                # A tool without an Args: block is fine — its whole docstring
                # becomes the description.
                error_on_invalid_docstring=False,
            )
        )
        return fn
