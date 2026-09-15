"""``scout doctor``: each check's level, and that nothing in it touches a network.

The report is what a new user is told to run first, so the levels carry weight:
FAIL means "this will not start", WARN means "this will run with less than it
could". Confusing the two makes the command useless.
"""

from __future__ import annotations

import pytest

from scout import checks
from scout.core import settings


@pytest.fixture
def configured(monkeypatch, tmp_path):
    """A fully configured checkout, for tests that then remove one thing.

    The filesystem is a temporary one rather than this repository: the report
    reads ``.env`` and ``data/``, and the suite has to say the same thing on a
    developer's machine and in CI, where neither exists.
    """
    root = tmp_path / "checkout"
    data = root / "data"
    data.mkdir(parents=True)
    (root / ".env").write_text("", encoding="utf-8")
    (data / "resume.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(checks, "PROJECT_ROOT", root)
    monkeypatch.setattr(settings, "RESUME_DIR", str(data))

    monkeypatch.setattr(settings, "ACTIVE_AGENT", "bigtech")
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "SLACK_APP_TOKEN", "xapp-x")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setattr(settings, "FALLBACK_BACKEND", "ollama")
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "U1")
    monkeypatch.setattr(settings, "CHECKPOINT_DB", "")
    return data


def levels(report: checks.Report) -> dict[str, str]:
    return {check.label: check.level for check in report.checks}


def test_a_configured_checkout_is_ready(configured) -> None:
    report = checks.run()

    assert report.ok
    assert set(levels(report).values()) == {checks.OK}
    assert "Ready to start." in report.render()


def test_no_env_file_at_all_is_worth_saying(configured, monkeypatch) -> None:
    """Every setting coming from the real environment is valid, and unusual."""
    (checks.PROJECT_ROOT / ".env").unlink()

    report = checks.run()

    assert report.ok
    assert levels(report)["env files"] == checks.WARN
    assert "real environment only" in report.render()


def test_missing_slack_tokens_are_a_failure(configured, monkeypatch) -> None:
    """The one condition that stops the bot from starting at all."""
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "")

    report = checks.run()

    assert not report.ok
    assert levels(report)["slack"] == checks.FAIL
    assert "SLACK_BOT_TOKEN" in report.render()


def test_an_unregistered_agent_is_a_failure(configured, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "nope")

    report = checks.run()

    assert not report.ok
    assert levels(report)["agent"] == checks.FAIL
    # The remedy is the list of names that would work.
    assert "bigtech" in report.render()


def test_no_resume_warns_rather_than_failing(configured, monkeypatch) -> None:
    """A job agent without a resume still answers; it just answers untailored."""
    for path in configured.iterdir():
        path.unlink()

    report = checks.run()

    assert report.ok
    assert levels(report)["resume"] == checks.WARN
    assert "untailored" in report.render()


def test_no_anthropic_key_warns_about_the_local_model(configured, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    report = checks.run()

    assert report.ok
    assert levels(report)["models"] == checks.WARN
    assert "local model" in report.render()


def test_an_unknown_fallback_backend_warns(configured, monkeypatch) -> None:
    """A typo here is invisible at runtime: the retry is simply skipped."""
    monkeypatch.setattr(settings, "FALLBACK_BACKEND", "ollamma")

    report = checks.run()

    assert levels(report)["fallback"] == checks.WARN
    assert "ollamma" in report.render()


def test_a_fallback_equal_to_the_default_is_called_a_no_op(configured, monkeypatch) -> None:
    """With no hosted key the default *is* Ollama, and the retry buys nothing."""
    monkeypatch.setattr(settings, "DEFAULT_BACKEND", "ollama")
    monkeypatch.setattr(settings, "FALLBACK_BACKEND", "ollama")

    report = checks.run()

    assert levels(report)["fallback"] == checks.OK
    assert "no-op" in report.render()


def test_an_empty_fallback_backend_is_a_deliberate_choice(configured, monkeypatch) -> None:
    """The container sets it empty on purpose — that is not a warning."""
    monkeypatch.setattr(settings, "FALLBACK_BACKEND", "")

    report = checks.run()

    assert levels(report)["fallback"] == checks.OK
    assert "disabled" in report.render()


def test_no_digest_recipient_warns(configured, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "")

    report = checks.run()

    assert report.ok
    assert levels(report)["digest"] == checks.WARN
    assert "1 thing worth knowing" in report.render()


def test_a_configured_checkpoint_db_is_reported_as_a_path(configured, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CHECKPOINT_DB", "state/test.sqlite")

    report = checks.run()

    assert levels(report)["state"] == checks.OK
    assert "state/test.sqlite" in report.render()


def test_the_report_asks_nothing_of_the_network(configured, monkeypatch) -> None:
    """The point of doctor: it describes this checkout, not the internet.

    A stubbed-out ``requests`` proves no check reaches for a job board or a
    model provider — which is what makes the command safe on a broken box.
    """
    import requests

    def explode(*_args, **_kwargs):
        raise AssertionError("doctor made a network call")

    monkeypatch.setattr(requests, "get", explode)
    monkeypatch.setattr(requests, "post", explode)

    assert checks.run().ok
