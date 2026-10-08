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
    # Pinned rather than inherited: a developer with a key in .env would
    # otherwise exercise a different branch here than CI does.
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "claude-opus-5")
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "")
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
    # The remedy names the one file that would count.
    assert "resume.pdf" in report.render()
    assert "untailored" in report.render()


def test_a_resume_under_another_name_does_not_count(configured) -> None:
    """The parser reads resume.pdf and nothing else, so doctor has to agree."""
    (configured / "resume.pdf").rename(configured / "sai_resume0807.pdf")

    assert levels(checks.run())["resume"] == checks.WARN


def test_no_anthropic_key_fails(configured, monkeypatch) -> None:
    """Claude is the only model, so without a key no turn can be answered."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    report = checks.run()

    assert not report.ok
    assert levels(report)["model"] == checks.FAIL
    assert "ANTHROPIC_API_KEY" in report.render()


def test_the_model_line_names_the_model_in_use(configured) -> None:
    detail = {c.label: c.detail for c in checks.run().checks}["model"]

    assert detail == "Claude (claude-opus-5)"


def test_no_digest_recipient_warns(configured, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "")

    report = checks.run()

    assert report.ok
    assert levels(report)["digest"] == checks.WARN
    assert "1 thing worth knowing" in report.render()


def test_the_digest_line_names_the_agent_whose_window_it_lands_in(configured) -> None:
    """One process per agent, posting with that agent's token, so the report is
    about the agent `doctor` is running as — not about every agent at once."""
    detail = {c.label: c.detail for c in checks.run().checks}["digest"]

    assert "BigTech Agent" in detail
    assert "U1" in detail


def test_an_agent_without_a_digest_says_so(configured, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "resume")

    report = checks.run()

    assert "has no digest" in report.render()
    # Not every agent needs one; that is a fact, not a fault.
    assert levels(report)["digest"] == checks.OK


def test_an_unregistered_agent_does_not_break_the_digest_line(
    configured, monkeypatch
) -> None:
    """`_agent` already fails the report for this; the digest line must still
    render rather than raise on the way there."""
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "nope")

    assert "has no digest" in checks.run().render()


def test_a_configured_checkpoint_db_is_reported_as_a_path(configured, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CHECKPOINT_DB", "state/test.sqlite")

    report = checks.run()

    assert levels(report)["state"] == checks.OK
    assert "state/test.sqlite" in report.render()


def test_the_shared_store_is_reported_as_a_path(configured, monkeypatch, tmp_path) -> None:
    """Not created until the first digest sends something, which is not a fault."""
    path = tmp_path / "shared.sqlite"
    monkeypatch.setattr(settings, "SHARED_DB", str(path))

    before = checks.run()
    path.write_bytes(b"")
    after = checks.run()

    assert levels(before)["shared"] == checks.OK
    assert f"{path} (not created yet)" in before.render()
    assert f"{path} (exists)" in after.render()


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


# --- Langfuse -------------------------------------------------------------
#
# Tracing is Scout's own (see scout/core/tracing.py), so the report reads the
# settings the runtime reads and the two cannot disagree.


def test_langfuse_off_says_what_would_turn_it_on(configured) -> None:
    report = checks.run()

    detail = {check.label: check.detail for check in report.checks}["langfuse"]
    assert "LANGFUSE_PUBLIC_KEY" in detail
    assert "LANGFUSE_SECRET_KEY" in detail
    # Off is a choice, not a fault.
    assert levels(report)["langfuse"] == checks.OK


@pytest.mark.parametrize("configured_key", ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"])
def test_half_a_key_pair_is_a_warning(configured, monkeypatch, configured_key: str) -> None:
    """The one state that reads as "on" in the .env while tracing nothing."""
    monkeypatch.setattr(settings, configured_key, "lf-x")
    missing = {"LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"} - {configured_key}

    report = checks.run()

    assert levels(report)["langfuse"] == checks.WARN
    assert f"{missing.pop()} is missing" in report.render()


def test_langfuse_on_names_the_host_and_warns_about_the_prompts(
    configured, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-lf-x")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-lf-x")
    monkeypatch.setattr(settings, "LANGFUSE_HOST", "https://langfuse.test")
    monkeypatch.setattr(settings, "LANGFUSE_ENVIRONMENT", "")
    monkeypatch.setattr(settings, "LANGFUSE_HIDE_CONTENT", False)

    report = checks.run()

    assert levels(report)["langfuse"] == checks.OK
    assert "tracing to https://langfuse.test (prompts included)" in report.render()


def test_langfuse_names_its_environment_and_its_mask(configured, monkeypatch) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-lf-x")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-lf-x")
    monkeypatch.setattr(settings, "LANGFUSE_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "LANGFUSE_HIDE_CONTENT", True)

    rendered = checks.run().render()

    assert "environment 'production'" in rendered
    assert "(content masked)" in rendered
