"""Start-up configuration: settings parsing, the agent registry, and logging."""

from __future__ import annotations

import importlib
import logging
import os
from logging.handlers import RotatingFileHandler

import pytest

from scout.agents import AGENTS, build_agent, get_spec
from scout.agents.resume_tailored import ResumeTailoredAgent
from scout.core import logging_config, paths, settings
from scout.core.agent import Agent, AgentSpec
from scout.core.logging_config import configure_logging
from scout.core.settings import _env_int

# --- Settings -------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [
    ("42", 42),
    ("0", 0),
    ("nonsense", 7),   # invalid falls back to the default
    ("", 7),
    (None, 7),         # unset falls back to the default
])
def test_env_int(monkeypatch, raw: str | None, expected: int) -> None:
    monkeypatch.delitem(os.environ, "SCOUT_TEST_INT", raising=False)
    if raw is not None:
        monkeypatch.setitem(os.environ, "SCOUT_TEST_INT", raw)
    assert _env_int("SCOUT_TEST_INT", 7) == expected


def test_missing_slack_tokens_exit_with_instructions(monkeypatch) -> None:
    """A missing token must be a readable message, not a KeyError traceback."""
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "SLACK_APP_TOKEN", "")

    with pytest.raises(SystemExit) as exc:
        settings.require_slack_credentials()

    message = str(exc.value)
    assert "SLACK_BOT_TOKEN" in message
    assert "SLACK_APP_TOKEN" in message
    assert ".env.example" in message


def test_only_the_missing_token_is_named(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-set")
    monkeypatch.setattr(settings, "SLACK_APP_TOKEN", "")

    with pytest.raises(SystemExit) as exc:
        settings.require_slack_credentials()

    assert "SLACK_APP_TOKEN" in str(exc.value)
    assert "SLACK_BOT_TOKEN" not in str(exc.value)


def test_configured_credentials_pass(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-set")
    monkeypatch.setattr(settings, "SLACK_APP_TOKEN", "xapp-set")
    assert settings.require_slack_credentials() is None


def test_settings_import_does_not_require_credentials() -> None:
    """The package must stay importable (and testable) with no .env present."""
    assert isinstance(settings.SLACK_BOT_TOKEN, str)
    assert settings.DEFAULT_BACKEND in ("anthropic", "ollama")


# --- Layered env files ----------------------------------------------------
#
# Each agent is its own Slack app, so the token pair cannot be shared: it comes
# from `.env.<agent>`, layered over the shared `.env`. Precedence must stay
# environment > .env.<agent> > .env — the deployed EnvironmentFile depends on
# the first half of that, and two bots running side by side on the second.


@pytest.fixture
def load_env(monkeypatch):
    """Re-import settings against a temp project root holding the given files.

    Reload is the only way to exercise module-scope loading. The real module is
    restored afterwards so later tests see the true configuration.
    """
    def _load(root, files: dict[str, str], agent: str | None = None,
              environ: dict[str, str] | None = None):
        for name, body in files.items():
            (root / name).write_text(body)
        # Tokens the real .env already put in os.environ would outrank both
        # files and mask what we are measuring. Cleared first, so `environ`
        # below is the only environment the reload sees.
        for key in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "AGENT"):
            monkeypatch.delenv(key, raising=False)
        if agent is not None:
            monkeypatch.setenv("AGENT", agent)
        for key, value in (environ or {}).items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(paths, "PROJECT_ROOT", root)
        return importlib.reload(settings)

    yield _load
    monkeypatch.undo()
    importlib.reload(settings)


def test_per_agent_file_supplies_the_token_pair(tmp_path, load_env) -> None:
    loaded = load_env(tmp_path, {
        ".env": "ANTHROPIC_MODEL=shared-model\n",
        ".env.edu": "SLACK_BOT_TOKEN=xoxb-edu\nSLACK_APP_TOKEN=xapp-edu\n",
    }, agent="edu")

    assert loaded.SLACK_BOT_TOKEN == "xoxb-edu"
    assert loaded.SLACK_APP_TOKEN == "xapp-edu"
    # The shared file still supplies everything that is not per-agent.
    assert loaded.ANTHROPIC_MODEL == "shared-model"


def test_per_agent_file_wins_over_the_shared_env(tmp_path, load_env) -> None:
    loaded = load_env(tmp_path, {
        ".env": "SLACK_BOT_TOKEN=xoxb-shared\n",
        ".env.edu": "SLACK_BOT_TOKEN=xoxb-edu\n",
    }, agent="edu")

    assert loaded.SLACK_BOT_TOKEN == "xoxb-edu"


def test_environment_beats_both_files(tmp_path, load_env) -> None:
    """What `AGENT=edu SLACK_BOT_TOKEN=... python run.py` relies on — and, on the
    box, what keeps systemd's EnvironmentFile ahead of the rsynced .env."""
    loaded = load_env(tmp_path, {
        ".env": "SLACK_BOT_TOKEN=xoxb-shared\n",
        ".env.edu": "SLACK_BOT_TOKEN=xoxb-edu\n",
    }, agent="edu", environ={"SLACK_BOT_TOKEN": "xoxb-from-environment"})

    assert loaded.SLACK_BOT_TOKEN == "xoxb-from-environment"


def test_agent_named_only_in_the_shared_env_still_picks_its_file(
    tmp_path, load_env
) -> None:
    """The chicken-and-egg case: AGENT lives in the file we have not read yet.

    Reading it back with dotenv_values is what makes this work — loading `.env`
    first to discover AGENT would let its tokens win before the per-agent file
    ever got a say.
    """
    loaded = load_env(tmp_path, {
        ".env": "AGENT=edu\nSLACK_BOT_TOKEN=xoxb-shared\n",
        ".env.edu": "SLACK_BOT_TOKEN=xoxb-edu\n",
    })

    assert loaded.ACTIVE_AGENT == "edu"
    assert loaded.SLACK_BOT_TOKEN == "xoxb-edu"


def test_a_missing_per_agent_file_is_not_an_error(tmp_path, load_env) -> None:
    """One agent, everything in .env — the setup before per-agent files existed."""
    loaded = load_env(tmp_path, {
        ".env": "SLACK_BOT_TOKEN=xoxb-shared\n",
    }, agent="bigtech")

    assert loaded.SLACK_BOT_TOKEN == "xoxb-shared"


# --- The agent registry ---------------------------------------------------


def test_registry_keys_match_their_specs() -> None:
    """Guards the copy-paste slip of registering a spec under the wrong key."""
    for key, spec in AGENTS.items():
        assert key == spec.key


def test_every_agent_is_runnable() -> None:
    for spec in AGENTS.values():
        assert spec.name
        assert spec.system_prompt
        # Every listed tool module must actually be one.
        for module in spec.tool_modules:
            assert callable(getattr(module, "register", None))


def test_unknown_agent_lists_the_available_ones() -> None:
    with pytest.raises(SystemExit) as exc:
        get_spec("nope")
    message = str(exc.value)
    assert "Unknown agent 'nope'" in message
    for key in AGENTS:
        assert key in message


def test_build_agent_wraps_only_the_tailored_specs() -> None:
    plain = AgentSpec(key="p", name="Plain", system_prompt="x")
    tailored = AgentSpec(key="t", name="Tailored", system_prompt="x",
                         tailor_with_resume=True)

    assert isinstance(build_agent(plain), Agent)
    assert isinstance(build_agent(tailored), ResumeTailoredAgent)


def test_shipped_job_agents_are_resume_tailored() -> None:
    assert AGENTS["bigtech"].tailor_with_resume
    assert AGENTS["edu"].tailor_with_resume
    # The parser is the producer; tailoring it would be circular.
    assert not AGENTS["resume"].tailor_with_resume


# --- Logging --------------------------------------------------------------


def test_logging_rotates_and_creates_its_directory(monkeypatch, tmp_path) -> None:
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(logging_config, "LOG_DIR", log_dir)
    root = logging.getLogger()
    previous = list(root.handlers)
    try:
        log = configure_logging()

        assert log.name == "scout"
        assert log_dir.is_dir()
        rotating = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(rotating) == 1
        assert rotating[0].maxBytes == settings.LOG_MAX_BYTES
        assert rotating[0].backupCount == settings.LOG_BACKUP_COUNT
        # Chatty third-party loggers are turned down so bot.log stays readable.
        assert logging.getLogger("slack_sdk").level == logging.WARNING
    finally:
        for handler in list(root.handlers):
            handler.close()
        root.handlers = previous
