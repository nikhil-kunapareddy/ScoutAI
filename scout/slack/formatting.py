"""Shaping a reply into Slack-sized messages.

Its own module because both senders need it and neither should import the
other: ``bot.py`` answers an event with ``say``, while ``notify.py`` opens a DM
nobody asked for. Splitting the same way in both is what makes a long digest
arrive as several readable messages instead of one Slack truncates.
"""

from __future__ import annotations

#: Slack collapses very long messages, so replies are split below this.
MAX_MESSAGE_CHARS = 3500


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split ``text`` into Slack-sized chunks on line boundaries.

    A single line longer than ``limit`` is emitted whole: job listings put each
    link on its own line, and a hard split would break the link.

    Args:
        text: The reply to send.
        limit: Maximum characters per chunk.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if current and len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
