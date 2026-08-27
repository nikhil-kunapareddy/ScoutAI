"""Current date and time, in the host's local timezone."""

from __future__ import annotations

from datetime import datetime

from .registry import ToolRegistry


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def get_current_time() -> str:
        """Return the current local time (with timezone)."""
        return datetime.now().astimezone().strftime("%H:%M:%S %Z")

    @reg.tool
    def get_current_date() -> str:
        """Return today's date, e.g. 'Wednesday, June 03, 2026'."""
        return datetime.now().astimezone().strftime("%A, %B %d, %Y")
