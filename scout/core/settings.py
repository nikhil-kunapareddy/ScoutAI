"""Platform configuration, loaded once from ``.env`` and ``.env.<agent>``.

Agent-specific settings (system prompt, tool set, default backend) live on each
``AgentSpec`` in ``scout/agents/``; this module holds only what all agents share
— plus the one thing that cannot be shared, the Slack token pair, which comes
from the per-agent file because each agent is a separate Slack app.

Nothing here raises on import — a missing credential leaves an empty string, so
the package stays importable (and testable) without a ``.env``. Credentials are
checked at start-up by ``require_slack_credentials()``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from dotenv import dotenv_values, load_dotenv

from .paths import PROJECT_ROOT

_ENV = PROJECT_ROOT / ".env"

# --- Active agent ---
# Which agent this process runs; see scout/agents/. One process per agent.
# Read before loading ``.env`` — it decides *which* token file layers on top —
# so peek at the file with dotenv_values rather than mutating the environment.
ACTIVE_AGENT = os.environ.get("AGENT") or dotenv_values(_ENV).get("AGENT") or "bigtech"

# Each agent is its own Slack app, so each needs its own bot and app token.
# ``.env.<agent>`` holds that pair; ``.env`` holds everything the agents share.
# load_dotenv never overrides a key that is already set, so first file to define
# one wins: real environment > .env.<agent> > .env. That ordering is what keeps
# the deployed EnvironmentFile authoritative over the rsynced .env.
load_dotenv(PROJECT_ROOT / f".env.{ACTIVE_AGENT}")
load_dotenv(_ENV)


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


def _env_any(names: Sequence[str], default: str) -> str:
    """The first of ``names`` that is set and non-empty, else ``default``.

    For settings an SDK also reads itself, under more than one name. Reading the
    same names it does, in the same order, is what keeps ``scout doctor`` from
    reporting one value while the library uses another.
    """
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _env_bool(name: str, default: bool) -> bool:
    """Read a flag from the environment: ``true``/``1``/``yes``/``on``, any case.

    Anything else set is false, which is the readable outcome for a typo in a
    switch — ``HIDE_CONTENT=ture`` must not read as "hide".
    """
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"true", "1", "yes", "on"}


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

# --- Resumes ---
# Where the Resume Parser looks. A relative path is taken against the project
# root, so a checkout needs no configuration; point it somewhere real if the
# package is installed rather than run from a clone, because then the project
# root is inside site-packages.
RESUME_DIR = os.environ.get("RESUME_DIR", "data")

# --- Referral list ---
# Companies the user has a connection at. Unlike CHECKPOINT_DB this defaults to
# a real path: history is disposable, but a list the user typed by hand should
# not vanish on restart. Sits in state/ so deploy.sh's rsync leaves it alone.
# See scout/core/referrals.py.
REFERRALS_FILE = os.environ.get("REFERRALS_FILE", "state/referrals.json")

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

# --- Langfuse (agent tracing) ---
# The full picture of a turn — every node, model call and tool call — grouped by
# conversation. Unlike the LANGSMITH_ variables, which LangSmith reads itself,
# these are read by us (see core/tracing.py): the key pair is the switch, and a
# half-configured pair leaves tracing off rather than half on.
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
# Two names each, because the SDK reads these itself as well, and its own
# order puts LANGFUSE_BASE_URL ahead of the host passed to the constructor.
# Reading what it reads means the value doctor reports is the value that gets
# used — a US-region key pair under a default EU host is otherwise silent.
LANGFUSE_HOST = _env_any(
    ("LANGFUSE_BASE_URL", "LANGFUSE_HOST"), "https://cloud.langfuse.com"
)
# Separates the deployed box from a laptop pointed at the same project. Empty
# leaves the SDK's own default, which is "default".
LANGFUSE_ENVIRONMENT = _env_any(("LANGFUSE_ENVIRONMENT", "LANGFUSE_TRACING_ENVIRONMENT"), "")
# Traces carry prompts and completions, the resume profile in the system prompt
# included. Set this to keep the call tree, latencies and token counts while
# leaving the content on the box.
LANGFUSE_HIDE_CONTENT = _env_bool("LANGFUSE_HIDE_CONTENT", False)

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


#: What each entry point cannot start without. ``scout doctor`` reports the same
#: lists, so a check and a start-up failure can never disagree about what is
#: required — see ``missing_for`` and ``scout/checks.py``.
BOT_REQUIRES = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")
DIGEST_REQUIRES = ("SLACK_BOT_TOKEN", "DIGEST_SLACK_USER")


def missing_for(required: Sequence[str]) -> list[str]:
    """Which of ``required`` are unset or empty, in the order given.

    Looked up by name in this module, so callers name a setting the way the
    operator does and read whatever the process actually has. An unknown name
    raises rather than reporting itself missing, which would be a typo quietly
    turning into a start-up failure.
    """
    configured = globals()
    return [name for name in required if not configured[name]]


def require_slack_credentials() -> None:
    """Fail readably when the tokens needed to connect are missing.

    Called from the entry point, not at import, so tests and tooling can import
    the package without a configured ``.env``.
    """
    _require(
        BOT_REQUIRES,
        "Copy .env.example to .env and fill in your Slack tokens "
        "(see docs/getting-started.md), then run `scout doctor`.",
    )


def require_digest_config() -> None:
    """Fail readably when the digest has no bot token or nobody to send to.

    Separate from ``require_slack_credentials`` because the two entry points need
    different things: the digest posts over the Web API and never opens a socket,
    so it wants ``SLACK_BOT_TOKEN`` but not ``SLACK_APP_TOKEN``.
    """
    _require(
        DIGEST_REQUIRES,
        "The digest needs a bot token and the Slack user id to DM; see "
        "the 'Daily digest' section of docs/operations.md.",
    )


def _require(required: Sequence[str], remedy: str) -> None:
    """Exit with the missing names and what to do about them, or return."""
    missing = missing_for(required)
    if missing:
        raise SystemExit(f"Missing required setting(s): {', '.join(missing)}.\n{remedy}")
