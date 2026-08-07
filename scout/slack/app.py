"""Slack adapter: wires an Agent to Slack DM events and text commands."""

from __future__ import annotations

import logging
from typing import Callable

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from ..agents import bigtech, resume_parser
from ..agents.resume_parser import parse_profile
from ..core import settings
from ..core.agent import Agent, AgentSpec
from ..core.paths import LOG_DIR

log = logging.getLogger("scout")


class TailoredJobAgent:
    """Resume-parser → job-search hand-off, exposing the same interface the Slack
    layer uses for a plain ``Agent`` (respond/reset/set_backend/backend_*), so
    ``create_app`` and the command handler work with it unchanged.

    On a user's first message we run the Resume Parser once, cache the resulting
    search brief, and prepend it to every job-search turn. ``bigtech`` no longer
    reads the resume itself — the profile only reaches it through this hand-off.
    """

    name = "BigTech Agent (resume-tailored)"

    def __init__(self) -> None:
        self._resume = Agent(resume_parser.SPEC)
        self._jobs = Agent(bigtech.SPEC)
        # user_id -> rendered search brief. dict get/set is atomic under the GIL;
        # a same-user race just re-parses (harmless), and Agent already serializes
        # each user's turns internally.
        self._briefs: dict[str, str] = {}

    def respond(self, user_id: str, prompt: str) -> str:
        brief = self._briefs.get(user_id)
        if brief is None:
            raw = self._resume.respond(user_id, "Extract my candidate profile.")
            brief = parse_profile(raw).to_search_brief()
            self._briefs[user_id] = brief
            log.info("Parsed resume profile for %s", user_id)
        return self._jobs.respond(user_id, f"{brief}\n\nUser request: {prompt}")

    def reset(self, user_id: str) -> None:
        self._resume.reset(user_id)
        self._jobs.reset(user_id)
        self._briefs.pop(user_id, None)  # re-parse the resume on the next message

    def set_backend(self, user_id: str, name: str) -> bool:
        self._resume.set_backend(user_id, name)
        return self._jobs.set_backend(user_id, name)

    def backend_name(self, user_id: str) -> str:
        return self._jobs.backend_name(user_id)

    def backend_label(self, user_id: str) -> str:
        return self._jobs.backend_label(user_id)


def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "bot.log"),
        ],
    )


# Text commands (typed as plain DM messages; Slack slash commands can't run in threads).
HELP_TEXT = (
    "*Commands:*\n"
    "• `--ollama` — use the local Ollama model\n"
    "• `--api` (or `--llama`) — use the hosted Meta Llama API\n"
    "• `--backend` — show which model you're currently using\n"
    "• `--reset` — clear your conversation history\n"
    "• `--help` — show this message"
)


def _handle_command(agent: "Agent | TailoredJobAgent", user: str, text: str, say: Callable[[str], None]) -> bool:
    """Handle a `--command`. Returns True if the text was a command we consumed."""
    cmd = text.lower()
    if cmd == "--reset":
        agent.reset(user)
        say("Conversation history cleared.")
    elif cmd == "--ollama":
        agent.set_backend(user, "ollama")
        say(f"Switched to *{agent.backend_label(user)}*.")
    elif cmd in ("--api", "--llama"):
        agent.set_backend(user, "llama")
        warning = "" if settings.LLAMA_API_KEY else (
            "\n:warning: `LLAMA_API_KEY` isn't set, so requests will fail until it's added to `.env`."
        )
        say(f"Switched to *{agent.backend_label(user)}*.{warning}")
    elif cmd in ("--backend", "--status"):
        say(f"You're currently using *{agent.backend_label(user)}*.\nSwitch with `--ollama` or `--api`.")
    elif cmd in ("--help", "help"):
        say(HELP_TEXT)
    else:
        return False
    return True


def create_app(agent: "Agent | TailoredJobAgent") -> App:
    app = App(token=settings.SLACK_BOT_TOKEN)

    @app.event("message")
    def handle_dm(event, say):
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id") or event.get("subtype"):
            return

        user = event["user"]
        text = event.get("text", "").strip()
        if not text:
            return

        if _handle_command(agent, user, text, say):
            return

        log.info("DM from %s (%s): %s", user, agent.backend_name(user), text[:80])
        try:
            say(agent.respond(user, text))
        except Exception as e:
            log.exception("Chat failed")
            say(f":warning: Error: `{e}`")

    return app


def _serve(agent: "Agent | TailoredJobAgent") -> None:
    """Start the Slack Socket Mode loop for an agent-shaped object."""
    log.info("Backends: ollama=%s/%s, llama=%s",
             settings.OLLAMA_HOST, settings.OLLAMA_MODEL, settings.LLAMA_MODEL)
    app = create_app(agent)
    SocketModeHandler(app, settings.SLACK_APP_TOKEN).start()


def run_agent(spec: AgentSpec) -> None:
    """Build the single agent described by ``spec`` and run it as a Slack bot."""
    _setup_logging()
    agent = Agent(spec)
    log.info("Tools available: %s", [t["function"]["name"] for t in agent.tools.tools])
    log.info("Starting %s (default backend=%s)", spec.name, spec.default_backend)
    _serve(agent)


def run_pipeline() -> None:
    """Run the resume-parser → job-search pipeline as a Slack bot."""
    _setup_logging()
    agent = TailoredJobAgent()
    log.info("Job tools available: %s",
             [t["function"]["name"] for t in agent._jobs.tools.tools])
    log.info("Starting %s (%s → %s)",
             agent.name, resume_parser.SPEC.name, bigtech.SPEC.name)
    _serve(agent)
