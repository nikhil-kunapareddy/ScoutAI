"""Platform configuration, loaded once from the project ``.env``.

Agent-specific settings (system prompt, tool set, default backend) live on each
``AgentSpec`` in ``scout/agents/``; this module holds only what all agents share.

Nothing here raises on import — a missing credential leaves an empty string, so
the package stays importable (and testable) without a ``.env``. Credentials are
checked at start-up by ``require_slack_credentials()``.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from .paths import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back if unset or invalid."""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back if unset or invalid."""
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


# --- Active agent ---
# Which agent this process runs; see scout/agents/. One process per agent.
ACTIVE_AGENT = os.environ.get("AGENT", "bigtech")

# --- Slack ---
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")

# --- Anthropic Claude (hosted, the platform default) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
ANTHROPIC_MAX_TOKENS = _env_int("ANTHROPIC_MAX_TOKENS", 16000)
# Thinking depth / token spend: low | medium | high | xhigh | max. "medium" keeps
# Slack replies snappy across a multi-hop tool loop.
ANTHROPIC_EFFORT = os.environ.get("ANTHROPIC_EFFORT", "medium")

# --- Ollama (local) ---
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

# --- Meta Llama API (hosted) ---
# Reached through Meta's OpenAI-compatible endpoint, so this is a *base* URL and
# not the full chat-completions path the old hand-rolled POST needed. Replaces
# LLAMA_API_URL, which is ignored: trimming it would yield a non-compat base
# that fails at request time instead of here.
LLAMA_API_KEY = os.environ.get("LLAMA_API_KEY", "")
LLAMA_MODEL = os.environ.get("LLAMA_MODEL", "Llama-4-Maverick-17B-128E-Instruct-FP8")
LLAMA_BASE_URL = os.environ.get("LLAMA_BASE_URL", "https://api.llama.com/compat/v1")

# --- Backend selection ---
# Agents start on Claude and fall back to the local model for one turn when a
# Claude request fails. With no key there is nothing to fall back *from*, so
# Ollama becomes the default and the fallback is a no-op.
DEFAULT_BACKEND = "anthropic" if ANTHROPIC_API_KEY else "ollama"
FALLBACK_BACKEND = os.environ.get("FALLBACK_BACKEND", "ollama")

# --- Conversation ---
MAX_TURNS = _env_int("MAX_TURNS", 20)         # message pairs retained per user
MAX_TOOL_HOPS = _env_int("MAX_TOOL_HOPS", 5)  # tool round-trips per message

# --- Conversation state ---
# Where LangGraph checkpoints live. Empty (the default) keeps history in memory,
# so a restart is a clean slate and the tests need no files. Set it to a path —
# absolute, or relative to the project root — and history plus the parsed resume
# profile survive restarts, which is what a deployed bot wants. See
# scout/core/checkpoints.py.
CHECKPOINT_DB = os.environ.get("CHECKPOINT_DB", "")

# --- Daily digest ---
# Slack user id the scheduled digest DMs (e.g. U012ABCDEF) — yours, not the
# bot's. Found under your Slack profile, "Copy member ID".
DIGEST_SLACK_USER = os.environ.get("DIGEST_SLACK_USER", "")
# How many roles to ask each agent for. Each agent contributes its own section,
# so the message holds this many per agent, not in total.
DIGEST_MAX_ROLES = _env_int("DIGEST_MAX_ROLES", 5)

# --- Turn metrics ---
# Dollars per million tokens, used to price each turn in the metrics line. Left
# at 0 the line reports tokens only — rates differ per backend and change over
# time, so they are configuration rather than a table baked into the code. Fill
# them from your provider's pricing page to get a usd= field.
USD_PER_MTOK_IN = _env_float("USD_PER_MTOK_IN", 0.0)
USD_PER_MTOK_OUT = _env_float("USD_PER_MTOK_OUT", 0.0)

# --- Logging ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = _env_int("LOG_MAX_BYTES", 5 * 1024 * 1024)
LOG_BACKUP_COUNT = _env_int("LOG_BACKUP_COUNT", 3)

# --- Model HTTP requests ---
# Generous: generation on a local Ollama model can take minutes.
MODEL_REQUEST_TIMEOUT_SECONDS = _env_int("MODEL_REQUEST_TIMEOUT_SECONDS", 300)

# --- Tool HTTP requests ---
TOOL_USER_AGENT = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36"
TOOL_REQUEST_TIMEOUT_SECONDS = _env_int("TOOL_REQUEST_TIMEOUT_SECONDS", 30)  # job APIs
GEO_REQUEST_TIMEOUT_SECONDS = _env_int("GEO_REQUEST_TIMEOUT_SECONDS", 10)    # ip-api


def require_slack_credentials() -> None:
    """Fail readably when the tokens needed to connect are missing.

    Called from the entry point, not at import, so tests and tooling can import
    the package without a configured ``.env``.
    """
    missing = [
        name
        for name, value in (
            ("SLACK_BOT_TOKEN", SLACK_BOT_TOKEN),
            ("SLACK_APP_TOKEN", SLACK_APP_TOKEN),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            f"Missing required setting(s): {', '.join(missing)}.\n"
            "Copy .env.example to .env and fill in your Slack tokens "
            "(see the 'One-time Slack setup' section of the README)."
        )


def require_digest_config() -> None:
    """Fail readably when the digest has no bot token or nobody to send to.

    Separate from ``require_slack_credentials`` because the two entry points need
    different things: the digest posts over the Web API and never opens a socket,
    so it wants ``SLACK_BOT_TOKEN`` but not ``SLACK_APP_TOKEN``.
    """
    missing = [
        name
        for name, value in (
            ("SLACK_BOT_TOKEN", SLACK_BOT_TOKEN),
            ("DIGEST_SLACK_USER", DIGEST_SLACK_USER),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            f"Missing required setting(s): {', '.join(missing)}.\n"
            "The digest needs a bot token and the Slack user id to DM; see the "
            "'Daily digest' section of the README."
        )
