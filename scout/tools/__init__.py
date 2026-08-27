"""Tool library.

Every tool module defines ``register(reg)``, which attaches its tools to a
``ToolRegistry``. ``build_registry()`` assembles one from the modules a given
agent asks for (see each ``AgentSpec``). The job sources live in ``jobs/``.
"""

from __future__ import annotations

from types import ModuleType

from .registry import ToolRegistry


def build_registry(modules: list[ModuleType]) -> ToolRegistry:
    """Create a registry and run ``register()`` for each tool module."""
    registry = ToolRegistry()
    for module in modules:
        module.register(registry)
    return registry


__all__ = ["ToolRegistry", "build_registry"]
