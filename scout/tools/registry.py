"""Registry of Python functions the model can call as tools.

Each registered function is stored by name and described with a JSON schema
built from its signature and docstring, in the format Ollama and the Llama API
expect (the Anthropic backend converts that shape to Claude's). When the model
asks for a tool, the registry looks it up by name and calls it in-process.

So writing a tool means writing a normal, typed, documented function: the
annotations become JSON types, and a Google-style ``Args:`` block becomes the
per-parameter descriptions the model reads.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any, get_type_hints

# Python annotation -> JSON-schema type. Anything else is described as a string.
_JSON_TYPES: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}

# Google-style docstring sections. Args: is parsed for parameter descriptions;
# the rest only mark where it ends.
_ARGS_HEADER_RE = re.compile(r"^(?:Args|Arguments|Parameters)\s*:\s*$")
_SECTION_HEADER_RE = re.compile(r"^(?:Returns?|Raises|Yields?|Examples?|Notes?)\s*:\s*$")
# "keywords: Optional search phrase." / "days (int): Only include roles ..."
_PARAM_RE = re.compile(r"^([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:\s*(.*)$")

# Parameter kinds that can't be expressed in a JSON-schema object.
_VARIADIC_KINDS = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)


class ToolRegistry:
    """The tools one agent can call, plus the schemas describing them."""

    def __init__(self) -> None:
        self._functions: dict[str, Callable[..., Any]] = {}
        self.schemas: list[dict] = []  # tool schemas, in registration order

    def tool(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register ``fn`` as a callable tool, building its schema from the
        signature and docstring. Used as a decorator in each module's ``register()``."""
        summary, param_docs = _parse_docstring(fn)
        self._functions[fn.__name__] = fn
        self.schemas.append({
            "type": "function",
            "function": {
                "name": fn.__name__,
                "description": summary,
                "parameters": _parameter_schema(fn, param_docs),
            },
        })
        return fn

    def names(self) -> list[str]:
        """Names of every registered tool, in registration order."""
        return [schema["function"]["name"] for schema in self.schemas]

    def call(self, name: str, args: dict) -> str:
        """Call the named tool and return its result.

        An unknown name is reported back to the model rather than raised, so it
        can correct itself on the next hop.
        """
        fn = self._functions.get(name)
        if fn is None:
            return f"Unknown tool: {name}"
        return str(fn(**(args or {})))


def _parameter_schema(fn: Callable[..., Any], param_docs: dict[str, str]) -> dict:
    """Build the JSON-schema object describing ``fn``'s parameters.

    Annotations are resolved with ``get_type_hints`` rather than read off the
    signature: every module here uses ``from __future__ import annotations``,
    which leaves ``Parameter.annotation`` as the *string* "int", so a direct
    lookup would silently type every parameter as a string.
    """
    try:
        hints = get_type_hints(fn)
    except Exception:  # unresolvable annotation — fall back to plain strings
        hints = {}

    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind in _VARIADIC_KINDS:
            continue
        schema = {"type": _JSON_TYPES.get(hints.get(name, param.annotation), "string")}
        if name in param_docs:
            schema["description"] = param_docs[name]
        properties[name] = schema
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {"type": "object", "properties": properties, "required": required}


def _parse_docstring(fn: Callable[..., Any]) -> tuple[str, dict[str, str]]:
    """Split a docstring into its summary and its ``Args:`` descriptions.

    Best-effort: without an ``Args:`` block the whole docstring is the summary.
    """
    doc = inspect.getdoc(fn) or ""
    lines = doc.splitlines()

    args_start = next(
        (i for i, line in enumerate(lines) if _ARGS_HEADER_RE.match(line.strip())),
        None,
    )
    if args_start is None:
        return doc.strip(), {}

    summary = "\n".join(lines[:args_start]).strip()
    return summary, _parse_args_block(lines[args_start + 1:])


def _parse_args_block(lines: list[str]) -> dict[str, str]:
    """Parse ``name: description`` entries, joining wrapped lines."""
    params: dict[str, str] = {}
    current: str | None = None
    param_indent: int | None = None

    for line in lines:
        if not line.strip():
            continue
        if _SECTION_HEADER_RE.match(line.strip()):  # a later section: Args ended
            break

        indent = len(line) - len(line.lstrip())
        is_continuation = param_indent is not None and indent > param_indent
        match = None if is_continuation else _PARAM_RE.match(line.strip())

        if match:
            current = match.group(1)
            params[current] = match.group(2).strip()
            param_indent = indent if param_indent is None else param_indent
        elif current:
            params[current] = f"{params[current]} {line.strip()}".strip()

    return params
