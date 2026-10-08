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
    # Short enough already: send it as one message.
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []  # the finished parts
    current = ""  # the part being filled
    for line in text.splitlines():
        # The current part with this line added.
        candidate = f"{current}\n{line}" if current else line
        # Too long with this line: close the current part and start a new one
        # with this line.
        if current and len(candidate) > limit:
            chunks.append(current)
            current = line
        # Still fits: keep the line in the current part.
        else:
            current = candidate
    # Don't drop the last part.
    if current:
        chunks.append(current)
    return chunks
