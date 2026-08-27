"""Runs a ``ConversationalAgent`` as a Slack Socket Mode DM bot.

This layer owns everything Slack-shaped and nothing agent-shaped: DM filtering,
the ``--command`` vocabulary, message-length limits, error reporting. It talks to
the agent only through ``ConversationalAgent``, so a single agent and the
resume-tailored pipeline take the same path.
"""

from __future__ import annotations

import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from ..core import settings
from ..core.agent import ConversationalAgent
from ..core.logging_config import quiet_third_party_loggers

log = logging.getLogger("scout")

#: Slack collapses very long messages, so replies are split below this.
MAX_MESSAGE_CHARS = 3500

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
        return f"`{primary}` (or {', '.join(f'`{a}`' for a in aliases)})"


class SlackBot:
    """Wires one agent to Slack DMs.

    Construction builds the command table and the Bolt app; ``start()`` opens the
    connection and blocks until interrupted.
    """

    def __init__(self, agent: ConversationalAgent) -> None:
        self._agent = agent
        self._commands = self._build_commands()
        self._commands_by_name = {n: c for c in self._commands for n in c.names}
        self._app = self._build_app()

    # --- Commands -------------------------------------------------------

    def _build_commands(self) -> list[Command]:
        """The command table, in the order ``--help`` lists them."""
        return [
            Command(("--claude", "--anthropic"),
                    "use the hosted Anthropic Claude API (default)", self._use_claude),
            Command(("--ollama",), "use the local Ollama model", self._use_ollama),
            Command(("--api", "--llama"),
                    "use the hosted Meta Llama API", self._use_llama),
            Command(("--backend", "--status"),
                    "show which model you're currently using", self._show_backend),
            Command(("--reset",), "clear your conversation history", self._reset_history),
            Command(("--help", "help"), "show this message", self._show_help),
        ]

    def _switch_backend(self, user: str, backend: str, warning: str = "") -> str:
        """Move the user to ``backend`` and describe the result."""
        if not self._agent.set_backend(user, backend):
            return f":warning: Unknown backend `{backend}`."
        return f"Switched to *{self._agent.backend_label(user)}*.{warning}"

    def _use_claude(self, user: str) -> str:
        warning = "" if settings.ANTHROPIC_API_KEY else (
            "\n:warning: `ANTHROPIC_API_KEY` isn't set, so every turn will fall back "
            "to the local model until it's added to `.env`."
        )
        return self._switch_backend(user, "anthropic", warning)

    def _use_ollama(self, user: str) -> str:
        return self._switch_backend(user, "ollama")

    def _use_llama(self, user: str) -> str:
        warning = "" if settings.LLAMA_API_KEY else (
            "\n:warning: `LLAMA_API_KEY` isn't set, so requests will fail until it's "
            "added to `.env`."
        )
        return self._switch_backend(user, "llama", warning)

    def _show_backend(self, user: str) -> str:
        return (f"You're currently using *{self._agent.backend_label(user)}*."
                "\nSwitch with `--claude`, `--ollama`, or `--api`.")

    def _reset_history(self, user: str) -> str:
        self._agent.reset(user)
        return "Conversation history cleared."

    def _show_help(self, _user: str) -> str:
        lines = ["*Commands:*"]
        lines += [f"• {c.usage} — {c.help}" for c in self._commands]
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

        for chunk in _split_message(reply):
            say(chunk)

    def _answer(self, user: str, text: str) -> str:
        """Ask the agent for a reply, turning a failure into a reportable message."""
        log.info("DM from %s (%s): %s", user, self._agent.backend_name(user), text[:80])
        try:
            reply = self._agent.respond(user, text)
        except Exception as e:
            log.exception("Chat failed")
            detail = str(e) or e.__class__.__name__
            return f":warning: Error: `{detail[:MAX_ERROR_CHARS]}`"

        # Disclose a fallback: the models differ enough that swapping silently
        # would be misleading.
        answered_by = self._agent.last_backend(user)
        if answered_by != self._agent.backend_name(user):
            reply += (f"\n\n_:warning: {self._agent.backend_label(user)} failed — "
                      f"this reply came from the `{answered_by}` fallback._")
        return reply

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
        log.info("Backends: anthropic=%s (key %s), ollama=%s/%s, llama=%s",
                 settings.ANTHROPIC_MODEL,
                 "set" if settings.ANTHROPIC_API_KEY else "MISSING",
                 settings.OLLAMA_HOST, settings.OLLAMA_MODEL, settings.LLAMA_MODEL)
        log.info("Default backend: %s (fallback: %s)",
                 settings.DEFAULT_BACKEND, settings.FALLBACK_BACKEND)


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


def _split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split ``text`` into Slack-sized chunks on line boundaries.

    A single line longer than ``limit`` is emitted whole: job listings put each
    link on its own line, and a hard split would break the link.
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
