"""Runs a ``ConversationalAgent`` as a Slack Socket Mode DM bot.

This layer owns everything Slack-shaped and nothing agent-shaped: DM filtering,
the ``--command`` vocabulary, message-length limits, error reporting. It talks to
the agent only through ``ConversationalAgent``, so a single agent and the
resume-tailored pipeline take the same path.
"""

from __future__ import annotations

import signal
from collections.abc import Callable
from dataclasses import dataclass

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from ..core import models, settings
from ..core.agent import ConversationalAgent
from ..core.logging_config import logger, quiet_third_party_loggers
from .formatting import split_message

log = logger()

#: Cap on exception text echoed to the user, so a huge provider error doesn't
#: become the whole reply.
MAX_ERROR_CHARS = 300


@dataclass(frozen=True)
class Command:
    """One ``--command`` a user can type.

    Slash commands don't work in DM threads for a socket-mode bot, so commands
    are ordinary messages we intercept.
    """

    names: tuple[str, ...]         # first is canonical, the rest are aliases
    help: str                      # one-line description, shown by --help
    handler: Callable[[str], str]  # takes the Slack user id, returns the reply

    @property
    def usage(self) -> str:
        """The command and its aliases, formatted for the help listing."""
        primary, *aliases = self.names
        if not aliases:
            return f"`{primary}`"
        return f"`{primary}` (or {', '.join(f'`{alias}`' for alias in aliases)})"


class SlackBot:
    """Wires one agent to Slack DMs.

    Construction builds the command table and the Bolt app; ``start()`` opens the
    connection and blocks until interrupted.
    """

    def __init__(self, agent: ConversationalAgent) -> None:
        self._agent = agent
        self._commands = self._build_commands()
        self._commands_by_name = {
            name: command for command in self._commands for name in command.names
        }
        self._app = self._build_app()

    # --- Commands -------------------------------------------------------

    def _build_commands(self) -> list[Command]:
        """The command table, in the order ``--help`` lists them."""
        return [
            Command(("--status",), "show which model you're talking to", self._show_model),
            Command(("--reset",), "clear your conversation history", self._reset_history),
            Command(("--help", "help"), "show this message", self._show_help),
        ]

    def _show_model(self, _user: str) -> str:
        return f"You're talking to *{models.label()}*."

    def _reset_history(self, user: str) -> str:
        self._agent.reset(user)
        return "Conversation history cleared."

    def _show_help(self, _user: str) -> str:
        lines = ["*Commands:*"]
        lines += [f"• {command.usage} — {command.help}" for command in self._commands]
        return "\n".join(lines)

    def _try_command(self, user: str, text: str) -> str | None:
        """Run ``text`` as a command, or return None if it isn't one."""
        command = self._commands_by_name.get(text.lower())
        return command.handler(user) if command else None

    # --- Slack plumbing -------------------------------------------------

    def _build_app(self) -> App:
        app = App(token=settings.SLACK_BOT_TOKEN)

        @app.event("message")
        def handle_message(event: dict, say: Callable[..., None]) -> None:
            self._handle_message(event, say)

        return app

    def _handle_message(self, event: dict, say: Callable[..., None]) -> None:
        """Respond to one message event, ignoring anything that isn't a user DM."""
        if event.get("channel_type") != "im":
            return
        # Skip other bots, plus edits/joins and friends, which carry a subtype.
        if event.get("bot_id") or event.get("subtype"):
            return

        user = event["user"]
        text = event.get("text", "").strip()
        if not text:
            return

        reply = self._try_command(user, text)
        if reply is None:
            reply = self._answer(user, text)

        for chunk in split_message(reply):
            say(chunk)

    def _answer(self, user: str, text: str) -> str:
        """Ask the agent for a reply, turning a failure into a reportable message."""
        log.info("DM from %s: %s", user, text[:80])
        try:
            return self._agent.respond(user, text)
        except Exception as e:
            log.exception("Chat failed")
            detail = str(e) or e.__class__.__name__
            return f":warning: Error: `{detail[:MAX_ERROR_CHARS]}`"

    # --- Running --------------------------------------------------------

    def start(self) -> None:
        """Open the Socket Mode connection and serve until stopped."""
        self._log_startup()
        handler = SocketModeHandler(self._app, settings.SLACK_APP_TOKEN)
        # slack-bolt sets levels on its own loggers as it builds them, so the
        # clamp has to be re-applied now that they exist.
        quiet_third_party_loggers()
        install_shutdown_handler()
        log.info("Connecting to Slack (Socket Mode)…")
        try:
            handler.start()
        except KeyboardInterrupt:
            log.info("Shutting down.")
        finally:
            handler.close()

    def _log_startup(self) -> None:
        log.info("Starting %s", self._agent.name)
        log.info("Tools available: %s", self._agent.tool_names())
        log.info("Model: %s (key %s)", models.label(),
                 "set" if settings.ANTHROPIC_API_KEY else "MISSING")


def install_shutdown_handler() -> None:
    """Make SIGTERM shut down the same way Ctrl-C does.

    Container platforms (ECS, Cloud Run, Docker, systemd) stop a process with
    SIGTERM, which by default kills Python outright — the websocket is never
    closed and ``start()``'s cleanup never runs. Raising KeyboardInterrupt from
    the handler unblocks the main thread through the normal shutdown path.
    """
    def shut_down(signum: int, _frame: object) -> None:
        log.info("Received %s.", signal.Signals(signum).name)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shut_down)
