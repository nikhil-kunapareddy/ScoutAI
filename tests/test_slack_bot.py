"""The Slack adapter: DM filtering, the command vocabulary, and message limits."""

from __future__ import annotations

import os
import signal

import pytest

from scout.core import settings
from scout.core.agent import ConversationalAgent
from scout.slack import bot as slack_bot
from scout.slack.bot import SlackBot, install_shutdown_handler
from scout.slack.formatting import MAX_MESSAGE_CHARS


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


@pytest.mark.parametrize(("text", "expected"), [
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


def test_an_unknown_backend_is_reported_not_silently_ignored(bot, monkeypatch) -> None:
    """``set_backend`` returning False means the model name is gone or misspelt;
    the user has to be told, or they keep talking to the old one."""
    monkeypatch.setattr(slack_bot, "App", FakeApp)

    class Refusing(FakeAgent):
        def set_backend(self, user_id: str, name: str) -> bool:
            return False

    sent: list[str] = []
    SlackBot(Refusing())._app.handler(
        {"channel_type": "im", "user": "U1", "text": "--ollama"}, sent.append
    )

    assert sent == [":warning: Unknown backend `ollama`."]


# --- Running --------------------------------------------------------------


class FakeHandler:
    """Stands in for ``SocketModeHandler``, which would open a websocket."""

    def __init__(self, app, token: str, fail: Exception | None = None) -> None:
        self.app = app
        self.token = token
        self.fail = fail
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True
        if self.fail:
            raise self.fail

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def handlers(monkeypatch):
    """Capture the socket-mode handler the bot builds, and restore SIGTERM."""
    monkeypatch.setattr(slack_bot, "App", FakeApp)
    built: list[FakeHandler] = []

    def build(app, token: str) -> FakeHandler:
        built.append(FakeHandler(app, token, fail=KeyboardInterrupt()))
        return built[-1]

    monkeypatch.setattr(slack_bot, "SocketModeHandler", build)
    previous = signal.getsignal(signal.SIGTERM)
    yield built
    signal.signal(signal.SIGTERM, previous)


def test_start_connects_with_the_app_token_and_serves(handlers, monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_APP_TOKEN", "xapp-test")

    SlackBot(FakeAgent()).start()

    (handler,) = handlers
    assert handler.token == "xapp-test"
    assert handler.started


def test_ctrl_c_closes_the_connection(handlers) -> None:
    """Ctrl-C is the ordinary way to stop it, and must not leak the websocket."""
    SlackBot(FakeAgent()).start()

    assert handlers[0].closed


def test_a_crash_still_closes_the_connection(handlers, monkeypatch) -> None:
    monkeypatch.setattr(
        slack_bot,
        "SocketModeHandler",
        lambda app, token: handlers.append(FakeHandler(app, token, OSError("gone")))
        or handlers[-1],
    )

    with pytest.raises(OSError, match="gone"):
        SlackBot(FakeAgent()).start()

    assert handlers[-1].closed


def test_startup_logs_name_the_agent_and_its_tools(handlers, caplog) -> None:
    """The first thing read when a deployed bot misbehaves."""
    with caplog.at_level("INFO", logger="scout"):
        SlackBot(FakeAgent()).start()

    logged = "\n".join(caplog.messages)
    assert "Fake Agent" in logged
    assert "echo" in logged
    assert "Default backend" in logged
