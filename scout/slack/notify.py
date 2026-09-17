"""Posting a message nobody asked for.

The bot always has an event to answer, and ``say`` already knows the channel.
The digest has no event — it runs from a timer — so it opens the DM itself.
Splitting is shared with the bot so a long digest arrives as several readable
messages instead of one Slack truncates.
"""

from __future__ import annotations

from slack_sdk import WebClient

from ..core import settings
from ..core.logging_config import logger
from .formatting import split_message

log = logger()


def post_dm(user_id: str, text: str) -> None:
    """DM ``text`` to ``user_id``, split across messages if it is long.

    Args:
        user_id: Slack user id, e.g. ``U012ABCDEF``. Slack resolves it to the
            bot's DM with that person, so no channel lookup is needed.
        text: The message body.
    """
    client = WebClient(token=settings.SLACK_BOT_TOKEN)
    chunks = split_message(text)
    for chunk in chunks:
        client.chat_postMessage(channel=user_id, text=chunk)
    log.info("Posted %d message(s) to %s", len(chunks), user_id)
