"""Read the turn metrics back: how much the agents ran, and what it cost.

    scout stats                            # the last 7 days from the journal
    scout stats --days 1
    journalctl -u 'scout@*' -o cat | scout stats -

The command line lives in ``scout/cli.py``; what is here is the reading. Parsing
is deliberately forgiving: a line that doesn't look like a turn is skipped rather
than fatal, so this keeps working when the log format grows a field.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

#: Units the journal query covers: every bot instance, plus every digest
#: instance. Both are globs because both are systemd templates, one instance
#: per agent — see deploy/scout@.service and deploy/scout-digest@.service.
_UNITS = ("scout@*", "scout-digest@*")


@dataclass
class Totals:
    """Running totals for one bucket of turns."""

    turns: int = 0
    outcomes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tool_calls: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    usd: float = 0.0
    seconds: list[float] = field(default_factory=list)

    @property
    def slowest(self) -> float:
        return max(self.seconds, default=0.0)

    @property
    def median_seconds(self) -> float:
        if not self.seconds:
            return 0.0
        ordered = sorted(self.seconds)
        return ordered[len(ordered) // 2]


def parse(line: str) -> dict[str, str] | None:
    """Pull the key=value pairs out of one metrics line, or None if it isn't one."""
    marker = line.find("turn agent=")
    if marker < 0:
        return None
    try:
        tokens = shlex.split(line[marker + len("turn ") :])
    except ValueError:
        return None
    # The marker guarantees a first ``agent=`` token, so this is never empty.
    return dict(token.split("=", 1) for token in tokens if "=" in token)


def summarise(lines: Iterable[str]) -> dict[str, Totals]:
    """Aggregate metrics lines by agent."""
    by_agent: dict[str, Totals] = defaultdict(Totals)
    for line in lines:
        fields = parse(line)
        if not fields:
            continue
        totals = by_agent[fields.get("agent", "?")]
        totals.turns += 1
        totals.outcomes[fields.get("outcome", "?")] += 1
        totals.tool_calls += _int(fields.get("tool_calls"))
        totals.in_tokens += _int(fields.get("in_tokens"))
        totals.out_tokens += _int(fields.get("out_tokens"))
        totals.usd += _float(fields.get("usd"))
        totals.seconds.append(_float(fields.get("seconds")))
    return dict(by_agent)


def _int(value: str | None) -> int:
    """A field as an int, or 0 when it is missing or not a number."""
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _float(value: str | None) -> float:
    """A field as a float, or 0.0 when it is missing or not a number."""
    try:
        return float(value or 0.0)
    except ValueError:
        return 0.0


def render(by_agent: dict[str, Totals]) -> str:
    """A short table, one row per agent."""
    if not by_agent:
        return "No turns recorded in that window."

    rows = [
        (
            f"{'agent':<28} {'turns':>6} {'fail':>5} {'tools':>6} "
            f"{'in_tok':>9} {'out_tok':>8} {'med_s':>6} {'max_s':>6} {'usd':>8}"
        )
    ]
    for name, totals in sorted(by_agent.items()):
        failed = totals.turns - totals.outcomes.get("ok", 0)
        rows.append(
            f"{name:<28} {totals.turns:>6} {failed:>5} {totals.tool_calls:>6} "
            f"{totals.in_tokens:>9} {totals.out_tokens:>8} "
            f"{totals.median_seconds:>6.1f} {totals.slowest:>6.1f} {totals.usd:>8.2f}"
        )
    total_usd = sum(totals.usd for totals in by_agent.values())
    if total_usd:
        rows.append(
            f"{'':<28} {'':>6} {'':>5} {'':>6} {'':>9} {'':>8} {'':>6} "
            f"{'total':>6} {total_usd:>8.2f}"
        )
    return "\n".join(rows)


def journal_lines(days: int) -> list[str]:
    """Metrics lines from the systemd journal."""
    command = ["journalctl", "--no-pager", "-o", "cat", "--since", f"-{days}d"]
    for unit in _UNITS:
        command += ["-u", unit]
    # Fixed argv, no shell; `days` is an int by the time it reaches here.
    result = subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, check=False
    )
    return result.stdout.splitlines()


def report(days: int, source: str | None = None) -> str:
    """The table for one window. ``source="-"`` reads log lines from stdin."""
    lines = sys.stdin if source == "-" else journal_lines(days)
    return render(summarise(lines))
