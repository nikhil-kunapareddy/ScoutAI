"""Slack adapter: runs an agent as a Slack Socket Mode bot."""

from __future__ import annotations

from .bot import SlackBot
from .formatting import MAX_MESSAGE_CHARS, split_message
from .notify import post_dm

__all__ = ["MAX_MESSAGE_CHARS", "SlackBot", "post_dm", "split_message"]
