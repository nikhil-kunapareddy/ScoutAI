"""Reading the metrics back: parsing, aggregation, and the summary table."""

from __future__ import annotations

import pytest

from scout import alert
from scout.core import settings
from scout.stats import parse, render, summarise

LINE = (
    '2026-09-14 03:00:00 INFO scout turn agent="BigTech Agent" thread=U1 '
    "backend=anthropic answered_by=anthropic outcome=ok seconds=12.4 "
    "model_calls=3 tool_calls=4 in_tokens=8123 out_tokens=512 usd=0.1600"
)


def test_parses_a_line_out_of_a_log_prefix() -> None:
    fields = parse(LINE)
    assert fields["agent"] == "BigTech Agent"  # quoted value, spaces kept
    assert fields["outcome"] == "ok"
    assert fields["in_tokens"] == "8123"


def test_unrelated_lines_are_skipped() -> None:
    assert parse("Connecting to Slack (Socket Mode)...") is None
    assert summarise(["nothing to see", ""]) == {}


def test_totals_add_up_per_agent() -> None:
    other = LINE.replace("BigTech Agent", "Edu Agent")
    totals = summarise([LINE, LINE, other])
    assert totals["BigTech Agent"].turns == 2
    assert totals["BigTech Agent"].in_tokens == 8123 * 2
    assert round(totals["BigTech Agent"].usd, 4) == 0.32
    assert totals["Edu Agent"].turns == 1


def test_failures_are_counted_apart_from_successes() -> None:
    stuck = LINE.replace("outcome=ok", "outcome=stuck")
    totals = summarise([LINE, stuck, stuck])["BigTech Agent"]
    assert totals.outcomes["ok"] == 1
    assert totals.outcomes["stuck"] == 2


def test_an_unknown_field_does_not_break_parsing() -> None:
    # The format is allowed to grow; old readers should cope.
    totals = summarise([LINE + " new_field=7"])
    assert totals["BigTech Agent"].turns == 1


def test_empty_window_says_so() -> None:
    assert "No turns recorded" in render(summarise([]))


def test_table_has_a_row_per_agent() -> None:
    table = render(summarise([LINE, LINE.replace("BigTech", "Edu")]))
    assert "BigTech Agent" in table
    assert "Edu Agent" in table


def test_alert_refuses_without_somewhere_to_send(monkeypatch) -> None:
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "")
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr("sys.argv", ["scout.alert", "scout@bigtech.service"])

    with pytest.raises(SystemExit, match="no alert sent"):
        alert.main()


def test_alert_dms_the_unit_name_and_its_log(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "U1")
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(alert, "post_dm", lambda u, t: sent.append((u, t)))
    monkeypatch.setattr(alert, "recent_log", lambda unit: "Traceback: boom")
    monkeypatch.setattr("sys.argv", ["scout.alert", "scout@bigtech.service"])

    alert.main()

    user, body = sent[0]
    assert user == "U1"
    assert "scout@bigtech.service" in body
    assert "Traceback: boom" in body


def test_a_missing_journal_is_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setattr(
        alert.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no journalctl"))
    )
    assert "could not read the journal" in alert.recent_log("scout@bigtech.service")
