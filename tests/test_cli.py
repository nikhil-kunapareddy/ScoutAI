"""The command line: what each subcommand runs, and the one ordering rule.

``--agent`` has to reach the environment *before* ``scout.core.settings`` is
imported, because that import is what layers ``.env.<agent>`` over ``.env``.
Both the behaviour and the structure that allows it are asserted here.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from scout import cli
from scout.core.metrics import TurnMetrics


@pytest.fixture(autouse=True)
def _no_agent_env(monkeypatch):
    """Start each test with no AGENT set, whatever the developer's .env says."""
    monkeypatch.delenv(cli.AGENT_ENV, raising=False)


# --- Agent selection ------------------------------------------------------


def test_the_agent_flag_is_exported_before_the_command_runs(monkeypatch) -> None:
    seen: list[str] = []

    def fake_run(_args) -> int:
        seen.append(os.environ[cli.AGENT_ENV])
        return 0

    monkeypatch.setattr(cli, "_run", fake_run)

    cli.main(["run", "--agent", "edu"])

    assert seen == ["edu"]


def test_without_the_flag_the_environment_is_left_alone(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda _args: 0)

    cli.main(["run"])

    assert cli.AGENT_ENV not in os.environ


def test_settings_is_never_imported_at_module_scope() -> None:
    """The invariant that makes ``--agent`` work.

    ``scout.core.settings`` reads AGENT at import time and cannot be told
    afterwards, so nothing at this module's top level may pull it in — directly
    or through a package that does.
    """
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    imported = [
        node
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]
    names = [
        name.name for node in imported for name in node.names
    ] + [
        node.module or "" for node in imported if isinstance(node, ast.ImportFrom)
    ]

    assert names, "the parse found no imports, so it is not testing anything"
    assert all(not name.startswith(".") and "scout" not in name for name in names), (
        f"cli.py imports {names} at module scope; keep package imports inside "
        "the command functions"
    )


# --- Dispatch -------------------------------------------------------------


def test_no_subcommand_starts_the_bot(monkeypatch) -> None:
    """``scout`` alone is ``scout run``, matching ``python run.py``."""
    calls: list[str] = []
    monkeypatch.setattr(cli, "_run", lambda _args: calls.append("run") or 0)

    assert cli.main([]) == 0
    assert calls == ["run"]


def test_run_builds_the_agent_and_starts_slack(monkeypatch) -> None:
    from scout.core import settings

    started: list[str] = []

    class FakeBot:
        def __init__(self, agent) -> None:
            started.append(agent.name)

        def start(self) -> None:
            started.append("started")

    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "SLACK_APP_TOKEN", "xapp-x")
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "referral")
    monkeypatch.setattr("scout.slack.SlackBot", FakeBot)

    assert cli.main(["run"]) == 0
    assert started == ["Referral Window", "started"]


def test_run_reports_missing_credentials_instead_of_connecting(monkeypatch) -> None:
    from scout.core import settings

    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "SLACK_APP_TOKEN", "")

    with pytest.raises(SystemExit, match="SLACK_BOT_TOKEN"):
        cli.main(["run"])


def test_digest_runs_the_digest_entry_point(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("scout.digest.main", lambda: calls.append("digest"))

    assert cli.main(["digest"]) == 0
    assert calls == ["digest"]


def test_stats_reads_the_window_it_was_asked_for(monkeypatch, capsys) -> None:
    """The window reaches the journal query, and a real turn line renders."""
    windows: list[int] = []
    turn = TurnMetrics(
        agent="BigTech Agent",
        thread="U1",
        backend="anthropic",
        answered_by="anthropic",
        outcome="ok",
        seconds=1.0,
        model_calls=1,
        tool_calls=2,
        input_tokens=100,
        output_tokens=10,
    )

    def fake_journal(days: int) -> list[str]:
        windows.append(days)
        return [turn.as_line()]

    monkeypatch.setattr("scout.stats.journal_lines", fake_journal)

    assert cli.main(["stats", "--days", "3"]) == 0
    assert windows == [3]
    assert "BigTech Agent" in capsys.readouterr().out


def test_agents_lists_every_registered_agent(capsys) -> None:
    from scout.agents import AGENTS

    assert cli.main(["agents"]) == 0

    out = capsys.readouterr().out
    for key, spec in AGENTS.items():
        assert key in out
        assert spec.name in out
    # The tool names are the point of the listing.
    assert "get_resume_profile" in out


def test_doctor_exits_nonzero_when_the_bot_could_not_start(monkeypatch, capsys) -> None:
    from scout.core import settings

    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "SLACK_APP_TOKEN", "")

    assert cli.main(["doctor"]) == 1
    assert "Not ready to start" in capsys.readouterr().out


def test_doctor_is_happy_with_a_configured_checkout(monkeypatch, capsys) -> None:
    from scout.core import settings

    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "SLACK_APP_TOKEN", "xapp-x")
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "bigtech")

    assert cli.main(["doctor"]) == 0
    assert "Ready to start" in capsys.readouterr().out


def test_an_unknown_command_is_an_argparse_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["nope"])
    assert exit_info.value.code == 2
