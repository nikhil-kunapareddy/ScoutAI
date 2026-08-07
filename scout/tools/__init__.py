"""
Shared tool library.

Each tool module defines a ``register(reg)`` function that attaches its tools to
a ``ToolRegistry``. ``build_registry()`` builds a registry from the specific set
of tool modules an agent asks for (see each agent's ``AgentSpec``).
"""

from __future__ import annotations

from types import ModuleType

from .registry import ToolRegistry


def build_registry(modules: list[ModuleType]) -> ToolRegistry:
    """Create a registry and run ``register()`` for each given tool module."""
    reg = ToolRegistry()
    for module in modules:
        module.register(reg)
    return reg
