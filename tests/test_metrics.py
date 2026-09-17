"""Per-turn metrics: what gets counted, and what gets priced."""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from scout.core import metrics, settings
from scout.core.runner import Agent

from .conftest import calls_tool


def _reply(text: str, *, inp: int = 0, out: int = 0) -> AIMessage:
    return AIMessage(
        text,
        usage_metadata={
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": inp + out,
        },
    )


def _measure(messages, outcome=metrics.OK):
    return metrics.measure(
        agent="A", thread="U1", backend="primary", answered_by="primary",
        outcome=outcome, seconds=1.0, messages=messages,
    )


def test_only_this_turn_is_counted() -> None:
    m = _measure([
        HumanMessage("old"),
        _reply("old answer", inp=999, out=999),
        HumanMessage("new"),
        _reply("new answer", inp=10, out=5),
    ])
    assert m.model_calls == 1
    assert m.input_tokens == 10
    assert m.output_tokens == 5


def test_tool_hops_are_counted(chat_models) -> None:
    m = _measure([
        HumanMessage("find jobs"),
        calls_tool("echo"),
        ToolMessage(content="echo:x", tool_call_id="1", name="echo"),
        _reply("done", inp=20, out=8),
    ])
    assert m.model_calls == 2
    assert m.tool_calls == 1
    assert m.input_tokens == 20


def test_missing_usage_is_not_an_error() -> None:
    # Not every provider reports usage; the line should still be written.
    m = _measure([HumanMessage("hi"), AIMessage("no usage here")])
    assert m.input_tokens == 0
    assert "in_tokens=0" in m.as_line()


def test_cost_is_absent_until_rates_are_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "USD_PER_MTOK_IN", 0.0)
    monkeypatch.setattr(settings, "USD_PER_MTOK_OUT", 0.0)
    m = _measure([HumanMessage("hi"), _reply("x", inp=1000, out=1000)])
    assert m.usd is None
    assert "usd=" not in m.as_line()


def test_cost_uses_the_configured_rates(monkeypatch) -> None:
    monkeypatch.setattr(settings, "USD_PER_MTOK_IN", 15.0)
    monkeypatch.setattr(settings, "USD_PER_MTOK_OUT", 75.0)
    m = _measure([HumanMessage("hi"), _reply("x", inp=1_000_000, out=1_000_000)])
    assert m.usd == 90.0
    assert "usd=90.0000" in m.as_line()


def test_a_real_turn_logs_a_line(spec, chat_models, caplog) -> None:
    chat_models["primary"].replies = [_reply("hello", inp=12, out=3)]
    with caplog.at_level(logging.INFO, logger="scout"):
        Agent(spec).respond("U1", "hi")

    line = next(r.message for r in caplog.records if r.message.startswith("turn "))
    assert 'agent="Test Agent"' in line
    assert "thread=U1" in line
    assert "outcome=ok" in line
    assert "in_tokens=12" in line
    assert "out_tokens=3" in line


def test_a_fallback_turn_names_the_backend_that_answered(
    spec, chat_models, caplog
) -> None:
    chat_models["primary"].fails = True
    chat_models["fallback"].replies = [_reply("rescued", inp=5, out=2)]

    with caplog.at_level(logging.INFO, logger="scout"):
        Agent(spec).respond("U1", "hi")

    line = next(r.message for r in caplog.records if r.message.startswith("turn "))
    assert "backend=primary" in line
    assert "answered_by=fallback" in line


def test_a_stuck_turn_is_recorded_as_such(spec, chat_models, caplog, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAX_TOOL_HOPS", 1)
    chat_models["primary"].replies = [calls_tool("echo")] * 5

    with caplog.at_level(logging.INFO, logger="scout"):
        Agent(spec).respond("U1", "hi")

    line = next(r.message for r in caplog.records if r.message.startswith("turn "))
    assert "outcome=stuck" in line


def test_measuring_never_breaks_a_reply(spec, chat_models, monkeypatch) -> None:
    monkeypatch.setattr(
        metrics, "measure", lambda **_: (_ for _ in ()).throw(ValueError("boom"))
    )
    chat_models["primary"].replies = [AIMessage("still answered")]
    assert Agent(spec).respond("U1", "hi") == "still answered"


def test_a_thread_with_no_user_message_is_measured_whole() -> None:
    """Defensive: every turn starts with a human message, but a thread repaired
    by hand (or a future caller) must still produce a line rather than raise."""
    measured = metrics.measure(
        agent="Test Agent",
        thread="U1",
        backend="primary",
        answered_by="primary",
        outcome=metrics.OK,
        seconds=0.5,
        messages=[AIMessage("orphaned")],
    )

    assert measured.model_calls == 1
