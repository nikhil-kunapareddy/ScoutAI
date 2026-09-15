"""One log line per turn, so a running bot can be watched without a debugger.

Every turn goes through ``GraphRunner.respond``, which is the one place that
sees a whole turn — the model calls, the tool hops, and which backend ended up
answering. That makes it the only sensible place to measure from.

The line is ``key=value`` pairs on purpose: greppable with ``journalctl``,
parseable by CloudWatch Logs Insights if the box ever gets an IAM role, and
readable as-is.

Cost is reported only when ``USD_PER_MTOK_IN``/``OUT`` are set. Rates are
configuration rather than a table baked in here: they differ per backend, they
change, and a stale hard-coded number is worse than no number at all. Tokens are
always reported, so the spend is derivable after the fact either way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from . import settings

log = logging.getLogger("scout")

#: Turn outcomes worth telling apart when reading the log back.
OK = "ok"
STUCK = "stuck"        # ran out of tool hops
ERROR = "error"        # raised out of the graph


@dataclass(frozen=True)
class TurnMetrics:
    """What one turn cost, in time, calls, and tokens."""

    agent: str
    thread: str
    backend: str
    answered_by: str
    outcome: str
    seconds: float
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int

    @property
    def usd(self) -> float | None:
        """Estimated spend, or None when no rates are configured."""
        if not (settings.USD_PER_MTOK_IN or settings.USD_PER_MTOK_OUT):
            return None
        return (
            self.input_tokens * settings.USD_PER_MTOK_IN
            + self.output_tokens * settings.USD_PER_MTOK_OUT
        ) / 1_000_000

    def as_line(self) -> str:
        fields = [
            f'turn agent="{self.agent}"',
            f"thread={self.thread}",
            f"backend={self.backend}",
            f"answered_by={self.answered_by}",
            f"outcome={self.outcome}",
            f"seconds={self.seconds:.1f}",
            f"model_calls={self.model_calls}",
            f"tool_calls={self.tool_calls}",
            f"in_tokens={self.input_tokens}",
            f"out_tokens={self.output_tokens}",
        ]
        if (usd := self.usd) is not None:
            fields.append(f"usd={usd:.4f}")
        return " ".join(fields)


def measure(
    agent: str,
    thread: str,
    backend: str,
    answered_by: str,
    outcome: str,
    seconds: float,
    messages: list[BaseMessage],
) -> TurnMetrics:
    """Summarise a turn from the messages it added.

    Args:
        agent: Display name of the agent that ran.
        thread: Checkpointer thread — the Slack user id, or a digest thread.
        backend: The backend the turn was asked to use.
        answered_by: The backend that actually replied; differs on a fallback.
        outcome: ``OK``, ``STUCK``, or ``ERROR``.
        seconds: Wall-clock duration of the turn.
        messages: The full thread after the turn; only what this turn added is
            counted.
    """
    added = _added_this_turn(messages)
    replies = [m for m in added if isinstance(m, AIMessage)]
    return TurnMetrics(
        agent=agent,
        thread=thread,
        backend=backend,
        answered_by=answered_by,
        outcome=outcome,
        seconds=seconds,
        model_calls=len(replies),
        tool_calls=sum(len(m.tool_calls or []) for m in replies),
        input_tokens=sum(_usage(m, "input_tokens") for m in replies),
        output_tokens=sum(_usage(m, "output_tokens") for m in replies),
    )


def _added_this_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    """The tail of the thread belonging to the turn that just ran.

    A turn starts at the message the user sent, and there is exactly one per
    turn, so the last human message marks the boundary — no need to read the
    checkpoint again to compare lengths.
    """
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i:]
    return messages


def _usage(message: AIMessage, field: str) -> int:
    """One usage figure, or 0 when the provider didn't report it.

    ``usage_metadata`` is a TypedDict with known keys, so it is widened to a
    plain dict to be read by name — providers also vary in which keys they fill.
    """
    usage: dict[str, object] = dict(message.usage_metadata or {})
    value = usage.get(field, 0)
    return value if isinstance(value, int) else 0


def record(metrics: TurnMetrics) -> None:
    """Write the turn's line to the log."""
    log.info("%s", metrics.as_line())
