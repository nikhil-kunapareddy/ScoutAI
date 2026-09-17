"""Splitting a reply into Slack-sized messages."""

from __future__ import annotations

from scout.slack.formatting import MAX_MESSAGE_CHARS, split_message


def test_short_text_is_one_chunk() -> None:
    assert split_message("hello") == ["hello"]


def test_split_breaks_on_line_boundaries() -> None:
    text = "\n".join(["a" * 40] * 10)
    chunks = split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "\n".join(chunks) == text


def test_an_over_long_single_line_is_kept_whole() -> None:
    """A job link must not be cut in half, so an unsplittable line is emitted intact."""
    line = "x" * 250
    chunks = split_message(f"short\n{line}", limit=100)
    assert line in chunks


def test_the_default_limit_is_slacks() -> None:
    """Nothing splits until a reply passes the limit the bot actually uses."""
    assert split_message("a" * MAX_MESSAGE_CHARS) == ["a" * MAX_MESSAGE_CHARS]
    assert len(split_message("a\n" * MAX_MESSAGE_CHARS)) > 1


def test_a_reply_of_only_blank_lines_sends_nothing() -> None:
    """Nothing to say is better sent as no message than as an empty one."""
    assert split_message("\n" * (MAX_MESSAGE_CHARS + 1)) == []
