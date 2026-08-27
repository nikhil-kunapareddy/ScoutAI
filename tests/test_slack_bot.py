"""The Slack adapter: DM filtering, the command vocabulary, and message limits."""

from __future__ import annotations

import os
import signal

import pytest

from scout.core import settings
from scout.core.agent import ConversationalAgent
from scout.slack import bot as slack_bot
from scout.slack.bot import (
    MAX_MESSAGE_CHARS,
    SlackBot,
    _split_message,
    install_shutdown_handler,
)


class FakeApp:
    """Stands in for ``slack_bolt.App``, which would verify the token on init."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self.handler = None

    def event(self, _name: str):
        def register(fn):
            self.handler = fn
            return fn
        return register


class FakeAgent(ConversationalAgent):
    """Records what the adapter asked of it, and can be told to fail."""

    name = "Fake Agent"

    def __init__(self, reply: str = "the answer", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.prompts: list[tuple[str, str]] = []
        self.resets: list[str] = []
        self.backend = "anthropic"
        self.answered_with = "anthropic"

    def respond(self, user_id: str, prompt: str) -> str:
        self.prompts.append((user_id, prompt))
        if self.error:
            raise self.error
        return self.reply

    def reset(self, user_id: str) -> None:
        self.resets.append(user_id)

    def set_backend(self, user_id: str, name: str) -> bool:
        self.backend = name
        return True

    def backend_name(self, user_id: str) -> str:
        return self.backend

    def backend_label(self, user_id: str) -> str:
        return f"Label({self.backend})"

    def last_backend(self, user_id: str) -> str:
        return self.answered_with

    def tool_names(self) -> list[str]:
        return ["echo"]


@pytest.fixture
def bot(monkeypatch):
    """A SlackBot over a fake Bolt app, plus the agent and the messages sent."""
    monkeypatch.setattr(slack_bot, "App", FakeApp)
    agent = FakeAgent()
    bot = SlackBot(agent)
    sent: list[str] = []

    def dispatch(**event_fields) -> list[str]:
        sent.clear()
        event = {"channel_type": "im", "user": "U1", **event_fields}
        bot._app.handler(event, sent.append)
        return sent

    return bot, agent, dispatch


# --- Event filtering ------------------------------------------------------


@pytest.mark.parametrize("event", [
    {"channel_type": "channel", "text": "hi"},   # not a DM
    {"bot_id": "B1", "text": "hi"},              # another bot
    {"subtype": "message_changed", "text": "hi"},  # an edit, not a new message
    {"text": "   "},                             # nothing but whitespace
    {},                                          # no text at all
])
def test_ignored_events(bot, event: dict) -> None:
    _, agent, dispatch = bot
    assert dispatch(**event) == []
    assert agent.prompts == []


def test_a_dm_reaches_the_agent(bot) -> None:
    _, agent, dispatch = bot
    assert dispatch(text="latest amazon jobs") == ["the answer"]
    assert agent.prompts == [("U1", "latest amazon jobs")]


# --- Commands -------------------------------------------------------------


def test_reset_clears_history_without_calling_the_model(bot) -> None:
    _, agent, dispatch = bot
    assert dispatch(text="--reset") == ["Conversation history cleared."]
    assert agent.resets == ["U1"]
    assert agent.prompts == []


def test_commands_are_case_insensitive(bot) -> None:
    _, agent, dispatch = bot
    dispatch(text="--RESET")
    assert agent.resets == ["U1"]


@pytest.mark.parametrize("text,expected", [
    ("--claude", "anthropic"),
    ("--anthropic", "anthropic"),
    ("--ollama", "ollama"),
    ("--api", "llama"),
    ("--llama", "llama"),
])
def test_backend_switch_commands_and_aliases(bot, monkeypatch, text: str, expected: str) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "key")
    monkeypatch.setattr(settings, "LLAMA_API_KEY", "key")
    _, agent, dispatch = bot

    reply = dispatch(text=text)
    assert agent.backend == expected
    assert reply == [f"Switched to *Label({expected})*."]


def test_switching_to_claude_warns_when_the_key_is_missing(bot, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    _, _, dispatch = bot
    assert "ANTHROPIC_API_KEY` isn't set" in dispatch(text="--claude")[0]


def test_switching_to_llama_warns_when_the_key_is_missing(bot, monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLAMA_API_KEY", "")
    _, _, dispatch = bot
    assert "LLAMA_API_KEY` isn't set" in dispatch(text="--api")[0]


def test_backend_command_reports_the_current_model(bot) -> None:
    _, _, dispatch = bot
    assert "You're currently using *Label(anthropic)*." in dispatch(text="--backend")[0]


def test_help_lists_every_command_and_its_aliases(bot) -> None:
    bot_obj, _, dispatch = bot
    help_text = dispatch(text="--help")[0]

    for command in bot_obj._commands:
        for name in command.names:
            assert f"`{name}`" in help_text
    assert help_text.startswith("*Commands:*")


def test_unknown_dashed_text_is_treated_as_a_prompt(bot) -> None:
    """"--jobs at netflix" is a question, not a typo'd command."""
    _, agent, dispatch = bot
    dispatch(text="--jobs at netflix")
    assert agent.prompts == [("U1", "--jobs at netflix")]


# --- Replies --------------------------------------------------------------


def test_fallback_is_disclosed_in_the_reply(bot) -> None:
    _, agent, dispatch = bot
    agent.answered_with = "ollama"  # the chosen backend failed

    reply = dispatch(text="hi")[0]
    assert reply.startswith("the answer")
    assert "Label(anthropic) failed" in reply
    assert "`ollama` fallback" in reply


def test_no_disclosure_when_the_chosen_backend_answered(bot) -> None:
    _, _, dispatch = bot
    assert dispatch(text="hi") == ["the answer"]


def test_agent_failure_is_reported_to_the_user(bot, monkeypatch) -> None:
    monkeypatch.setattr(slack_bot, "App", FakeApp)
    agent = FakeAgent(error=RuntimeError("connection refused"))
    sent: list[str] = []
    SlackBot(agent)._app.handler(
        {"channel_type": "im", "user": "U1", "text": "hi"}, sent.append
    )
    assert sent == [":warning: Error: `connection refused`"]


def test_error_text_is_truncated(bot, monkeypatch) -> None:
    monkeypatch.setattr(slack_bot, "App", FakeApp)
    agent = FakeAgent(error=RuntimeError("x" * 5000))
    sent: list[str] = []
    SlackBot(agent)._app.handler(
        {"channel_type": "im", "user": "U1", "text": "hi"}, sent.append
    )
    assert len(sent[0]) < 400


def test_error_without_a_message_falls_back_to_the_class_name(bot, monkeypatch) -> None:
    monkeypatch.setattr(slack_bot, "App", FakeApp)
    agent = FakeAgent(error=TimeoutError())
    sent: list[str] = []
    SlackBot(agent)._app.handler(
        {"channel_type": "im", "user": "U1", "text": "hi"}, sent.append
    )
    assert sent == [":warning: Error: `TimeoutError`"]


def test_long_replies_are_split_across_messages(bot, monkeypatch) -> None:
    monkeypatch.setattr(slack_bot, "App", FakeApp)
    long_reply = "\n".join(f"{i}. *A role with a longish title*" for i in range(400))
    agent = FakeAgent(reply=long_reply)
    sent: list[str] = []
    SlackBot(agent)._app.handler(
        {"channel_type": "im", "user": "U1", "text": "hi"}, sent.append
    )

    assert len(sent) > 1
    assert all(len(chunk) <= MAX_MESSAGE_CHARS for chunk in sent)
    # Nothing is lost or duplicated in the split.
    assert "\n".join(sent) == long_reply


# --- Chunking -------------------------------------------------------------


def test_short_text_is_one_chunk() -> None:
    assert _split_message("hello") == ["hello"]


def test_split_breaks_on_line_boundaries() -> None:
    text = "\n".join(["a" * 40] * 10)
    chunks = _split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "\n".join(chunks) == text


def test_an_over_long_single_line_is_kept_whole() -> None:
    """A job link must not be cut in half, so an unsplittable line is emitted intact."""
    line = "x" * 250
    chunks = _split_message(f"short\n{line}", limit=100)
    assert line in chunks


# --- Shutdown -------------------------------------------------------------


def test_sigterm_triggers_the_normal_shutdown_path() -> None:
    """Container platforms stop the process with SIGTERM, which would otherwise
    kill Python outright and skip the websocket close."""
    previous = signal.getsignal(signal.SIGTERM)
    try:
        install_shutdown_handler()
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, previous)
