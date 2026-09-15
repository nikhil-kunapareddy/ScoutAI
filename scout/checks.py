"""``scout doctor``: what is configured here, and what is missing.

Every check is a local read — a file that exists, a setting that is filled in.
Nothing here calls Slack, a model provider, or a job board, so the report is
about *this checkout* and cannot be reddened by someone else's outage. That is
what makes it the first thing to run in a fresh clone, and the first thing to
run on a box that has gone quiet.

The exit status is the part scripts want: non-zero only when something is
missing that stops the bot from starting at all. A warning means an agent will
run with less than it could — an untailored search, no daily digest — which is
a choice, not a fault.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .agents import AGENTS
from .core import models, referrals, settings
from .core.paths import PROJECT_ROOT, under_root
from .tools.resume import RESUME_EXTENSIONS, resume_dir, resumes_in

#: Levels. Only FAIL sets the exit status; WARN describes a reduced setup.
OK = "ok"
WARN = "warn"
FAIL = "fail"

_SYMBOLS = {OK: "✓", WARN: "!", FAIL: "✗"}


@dataclass(frozen=True)
class Check:
    """One line of the report."""

    label: str
    detail: str
    level: str = OK

    def render(self) -> str:
        return f"  {_SYMBOLS[self.level]} {self.label:<18} {self.detail}"


@dataclass(frozen=True)
class Report:
    """Every check, in the order they are worth reading."""

    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the bot can start at all."""
        return not any(check.level == FAIL for check in self.checks)

    def render(self) -> str:
        lines = [f"scout doctor — {PROJECT_ROOT}", ""]
        lines += [check.render() for check in self.checks]
        lines.append("")
        lines.append(self._verdict())
        return "\n".join(lines)

    def _verdict(self) -> str:
        warnings = sum(check.level == WARN for check in self.checks)
        if not self.ok:
            return (
                "Not ready to start. Fix the ✗ lines above — see "
                "docs/getting-started.md."
            )
        if warnings:
            noun = "thing" if warnings == 1 else "things"
            return f"Ready to start, with {warnings} {noun} worth knowing about."
        return "Ready to start."


def run() -> Report:
    """Collect every check."""
    return Report(
        [
            _agent(),
            _env_files(),
            _slack(),
            _backends(),
            _fallback(),
            _resume(),
            _state(),
            _referrals(),
            _digest(),
            _tracing(),
        ]
    )


def _agent() -> Check:
    key = settings.ACTIVE_AGENT
    if key not in AGENTS:
        return Check(
            "agent",
            f"AGENT={key!r} is not registered. Known: {', '.join(sorted(AGENTS))}.",
            FAIL,
        )
    return Check("agent", f"{key} — {AGENTS[key].name}")


def _env_files() -> Check:
    """Which env files were found, since that ordering explains everything else."""
    # Listed in the order they are consulted: the per-agent file wins, and a
    # real environment variable beats both.
    candidates = (f".env.{settings.ACTIVE_AGENT}", ".env")
    found = [name for name in candidates if (PROJECT_ROOT / name).is_file()]
    if not found:
        return Check(
            "env files",
            "none found — settings come from the real environment only",
            WARN,
        )
    return Check("env files", " > ".join(found) + " (first wins)")


def _slack() -> Check:
    missing = [
        name
        for name, value in (
            ("SLACK_BOT_TOKEN", settings.SLACK_BOT_TOKEN),
            ("SLACK_APP_TOKEN", settings.SLACK_APP_TOKEN),
        )
        if not value
    ]
    if missing:
        return Check(
            "slack",
            f"missing {', '.join(missing)} — put them in "
            f".env.{settings.ACTIVE_AGENT}",
            FAIL,
        )
    return Check("slack", "bot and app tokens present")


def _backends() -> Check:
    """Which model backends a user could switch to right now."""
    usable = []
    if settings.ANTHROPIC_API_KEY:
        usable.append(f"anthropic ({settings.ANTHROPIC_MODEL})")
    if settings.LLAMA_API_KEY:
        usable.append(f"llama ({settings.LLAMA_MODEL})")
    # Ollama needs no key; whether it is running is a network question, and this
    # report deliberately asks none.
    usable.append(f"ollama ({settings.OLLAMA_MODEL} at {settings.OLLAMA_HOST})")

    detail = f"default={settings.DEFAULT_BACKEND}; available: {', '.join(usable)}"
    if not settings.ANTHROPIC_API_KEY:
        return Check(
            "models",
            f"{detail} — no ANTHROPIC_API_KEY, so every turn runs on the local model",
            WARN,
        )
    return Check("models", detail)


def _fallback() -> Check:
    name = settings.FALLBACK_BACKEND
    if not name:
        return Check("fallback", "disabled — a failed turn fails once, cleanly")
    if not models.exists(name):
        return Check(
            "fallback",
            f"FALLBACK_BACKEND={name!r} is not a backend "
            f"({', '.join(models.names())}); the retry will be skipped",
            WARN,
        )
    if name == settings.DEFAULT_BACKEND:
        # Retrying the backend that just failed on the same call buys nothing,
        # so the runtime skips it. Worth saying, or the line reads as cover
        # that isn't there.
        return Check("fallback", f"{name} is also the default, so the retry is a no-op")
    return Check("fallback", f"{name}, for one retry inside a failed turn")


def _resume() -> Check:
    """Whether the Resume Parser has something to read."""
    directory = resume_dir()
    resumes = resumes_in(directory)
    if not resumes:
        extensions = ", ".join(sorted(RESUME_EXTENSIONS))
        missing = "no folder at" if not directory.is_dir() else f"nothing ({extensions}) in"
        return Check(
            "resume",
            f"{missing} {directory} — searches run untailored",
            WARN,
        )
    newest = max(resumes, key=lambda p: p.stat().st_mtime)
    extra = f" (+{len(resumes) - 1} more, newest wins)" if len(resumes) > 1 else ""
    return Check("resume", f"{directory.name}/{newest.name}{extra}")


def _state() -> Check:
    if not settings.CHECKPOINT_DB:
        return Check(
            "state",
            "in memory — history and the parsed resume reset on restart",
        )
    path = under_root(settings.CHECKPOINT_DB)
    where = "exists" if path.is_file() else "will be created"
    return Check("state", f"sqlite at {path} ({where})")


def _referrals() -> Check:
    path = referrals.store_path()
    where = "exists" if path.is_file() else "not created yet"
    return Check("referrals", f"{path} ({where})")


def _digest() -> Check:
    if not settings.DIGEST_SLACK_USER:
        return Check(
            "digest",
            "DIGEST_SLACK_USER unset — `scout digest` has nobody to DM",
            WARN,
        )
    return Check(
        "digest",
        f"DMs {settings.DIGEST_SLACK_USER}, {settings.DIGEST_MAX_ROLES} roles per agent",
    )


def _tracing() -> Check:
    # Read from the environment, not settings: LangSmith reads these itself, so
    # the platform never needs them and does not carry them.
    if os.environ.get("LANGSMITH_TRACING", "").lower() in {"true", "1"}:
        project = os.environ.get("LANGSMITH_PROJECT", "default")
        return Check("tracing", f"LangSmith on, project {project!r}")
    return Check("tracing", "off (set LANGSMITH_TRACING=true to trace turns)")
