"""Tool-schema generation and dispatch.

The schema is the model's only description of a tool, so a wrong type or a
missing description degrades every turn silently. These tests pin the shape
LangChain derives from the signature and the Google-style docstring.
"""

from __future__ import annotations

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from scout.tools.registry import ToolRegistry

from .conftest import registered_tools


def schema_for(reg: ToolRegistry, name: str) -> dict:
    """The tool's schema as a provider sees it."""
    tool = next(t for t in reg.tools if t.name == name)
    return convert_to_openai_tool(tool)["function"]


def test_annotations_map_to_json_types() -> None:
    reg = ToolRegistry()

    @reg.tool
    def sample(text: str, count: int, ratio: float, flag: bool) -> str:
        """Do a thing."""
        return ""

    props = schema_for(reg, "sample")["parameters"]["properties"]
    assert props["text"]["type"] == "string"
    assert props["count"]["type"] == "integer"
    assert props["ratio"]["type"] == "number"
    assert props["flag"]["type"] == "boolean"


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
    assert fn["description"].startswith("Search a thing and return results.")
    assert "Roles are listed newest-first." in fn["description"]

    props = fn["parameters"]["properties"]
    # The wrapped continuation line is joined back into one description.
    assert props["keywords"]["description"] == (
        "Optional search phrase. If empty, uses the user's profile."
    )
    assert props["limit"]["description"] == "Maximum number of roles to return."
    # The Returns: section is not swallowed into the last parameter.
    assert "Ignored" not in props["limit"]["description"]


def test_docstring_without_args_block_becomes_the_whole_description() -> None:
    """A no-argument tool must still register — most of ours have no Args: block."""
    reg = ToolRegistry()

    @reg.tool
    def sample() -> str:
        """Return the current time."""
        return ""

    fn = schema_for(reg, "sample")
    assert fn["description"] == "Return the current time."
    assert fn["parameters"]["properties"] == {}


def test_tools_are_callable_with_their_arguments() -> None:
    reg = ToolRegistry()

    @reg.tool
    def add(a: int = 0, b: int = 0) -> int:
        """Add two numbers."""
        return a + b

    tool = reg.tools[0]
    assert tool.invoke({"a": 2, "b": 3}) == 5
    assert tool.invoke({}) == 0


def test_arguments_are_validated_before_the_tool_runs() -> None:
    """Junk the schema forbids is rejected, so it reaches the model as an error
    (the ``tools`` node turns this into a ToolMessage) rather than the tool."""
    reg = ToolRegistry()

    @reg.tool
    def add(a: int = 0) -> int:
        """Add a number."""
        return a

    assert reg.tools[0].invoke({"a": "5"}) == 5  # coercible, so it goes through
    with pytest.raises(ValidationError):
        reg.tools[0].invoke({"a": "five"})


def test_build_registry_runs_each_module_in_order(echo_tool_module) -> None:
    assert list(registered_tools(echo_tool_module)) == ["echo", "explode"]
