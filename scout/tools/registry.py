"""
Registry of Python functions the model can call as tools.

Each registered function is stored by name and described with a JSON schema
(derived from its signature and docstring) in the format Ollama expects in the
`tools` field of a chat request. When the model asks to call a tool, the
registry looks it up by name and invokes it directly.
"""

from __future__ import annotations

import inspect
from typing import Callable

# Maps Python parameter annotations to JSON-schema type names.
_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


class ToolRegistry:
    def __init__(self) -> None:
        self._fns: dict[str, Callable] = {}
        self.tools: list[dict] = []  # Tool schemas in Ollama's expected format.

    def tool(self, fn: Callable) -> Callable:
        """Register `fn` as a callable tool and build its schema from the
        function's signature (parameter names/types) and docstring."""
        props, required = {}, []
        for name, p in inspect.signature(fn).parameters.items():
            props[name] = {"type": _TYPE_MAP.get(p.annotation, "string")}
            if p.default is inspect.Parameter.empty:
                required.append(name)
        self._fns[fn.__name__] = fn
        self.tools.append({
            "type": "function",
            "function": {
                "name": fn.__name__,
                "description": inspect.getdoc(fn) or "",
                "parameters": {
                    "type": "object", "properties": props, "required": required,
                },
            },
        })
        return fn

    def call_tool(self, name: str, args: dict) -> str:
        """Invoke the named tool with the given arguments and return its result."""
        fn = self._fns.get(name)
        if fn is None:
            return f"Unknown tool: {name}"
        return str(fn(**(args or {})))
