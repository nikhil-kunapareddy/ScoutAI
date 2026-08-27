"""Tool-schema generation and dispatch.

The schema is the model's only description of a tool, so a wrong type or a
missing description degrades every turn silently. These tests pin the shape.
"""

from __future__ import annotations

from scout.tools import build_registry
from scout.tools.registry import ToolRegistry


def schema_for(reg: ToolRegistry, name: str) -> dict:
    return next(t["function"] for t in reg.schemas if t["function"]["name"] == name)


def test_annotations_map_to_json_types() -> None:
    """Regression: ``from __future__ import annotations`` used to make every
    parameter a string, because the annotation arrives as the text "int"."""
    reg = ToolRegistry()

    @reg.tool
    def sample(text: str, count: int, ratio: float, flag: bool, untyped=None) -> str:
        """Do a thing."""
        return ""

    props = schema_for(reg, "sample")["parameters"]["properties"]
    assert props["text"]["type"] == "string"
    assert props["count"]["type"] == "integer"
    assert props["ratio"]["type"] == "number"
    assert props["flag"]["type"] == "boolean"
    assert props["untyped"]["type"] == "string"  # unannotated falls back to string


def test_required_lists_only_parameters_without_defaults() -> None:
    reg = ToolRegistry()

    @reg.tool
    def sample(company: str, keywords: str = "", limit: int = 5) -> str:
        """Do a thing."""
        return ""

    assert schema_for(reg, "sample")["parameters"]["required"] == ["company"]


def test_docstring_splits_into_summary_and_parameter_descriptions() -> None:
    reg = ToolRegistry()

    @reg.tool
    def sample(keywords: str = "", limit: int = 5) -> str:
        """Search a thing and return results.

        Roles are listed newest-first.

        Args:
            keywords: Optional search phrase. If empty, uses
                the user's profile.
            limit: Maximum number of roles to return.

        Returns:
            Ignored by the schema.
        """
        return ""

    fn = schema_for(reg, "sample")
    assert fn["description"] == (
        "Search a thing and return results.\n\nRoles are listed newest-first."
    )
    props = fn["parameters"]["properties"]
    # The wrapped continuation line is joined back into one description.
    assert props["keywords"]["description"] == (
        "Optional search phrase. If empty, uses the user's profile."
    )
    assert props["limit"]["description"] == "Maximum number of roles to return."
    # The Returns: section is not swallowed into the last parameter.
    assert "Ignored" not in props["limit"]["description"]


def test_docstring_without_args_block_becomes_the_whole_description() -> None:
    reg = ToolRegistry()

    @reg.tool
    def sample() -> str:
        """Return the current time."""
        return ""

    fn = schema_for(reg, "sample")
    assert fn["description"] == "Return the current time."
    assert fn["parameters"] == {"type": "object", "properties": {}, "required": []}


def test_call_dispatches_and_stringifies() -> None:
    reg = ToolRegistry()

    @reg.tool
    def add(a: int = 0, b: int = 0) -> int:
        """Add two numbers."""
        return a + b

    assert reg.call("add", {"a": 2, "b": 3}) == "5"
    assert reg.call("add", {}) == "0"
    assert reg.call("add", None) == "0"


def test_unknown_tool_is_reported_not_raised() -> None:
    """The model gets a readable message and can correct itself next hop."""
    reg = ToolRegistry()
    assert reg.call("nope", {}) == "Unknown tool: nope"


def test_build_registry_runs_each_module_in_order(echo_tool_module) -> None:
    reg = build_registry([echo_tool_module])
    assert reg.names() == ["echo", "explode"]
