"""Read the turn metrics back: how much the agents ran, and what it cost.

    python -m scout.stats                  # the last 7 days from the journal
    python -m scout.stats --days 1
    journalctl -u 'scout@*' -o cat | python -m scout.stats -

Parsing is deliberately forgiving: a line that doesn't look like a turn is
skipped rather than fatal, so this keeps working when the log format grows a
field.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

#: Units the journal query covers: every bot instance, plus the digest.
_UNITS = ("scout@*", "scout-digest")


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
    fields = dict(token.split("=", 1) for token in tokens if "=" in token)
    return fields or None


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
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _float(value: str | None) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
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
    for name, t in sorted(by_agent.items()):
        failed = t.turns - t.outcomes.get("ok", 0)
        rows.append(
            f"{name:<28} {t.turns:>6} {failed:>5} {t.tool_calls:>6} "
            f"{t.in_tokens:>9} {t.out_tokens:>8} "
            f"{t.median_seconds:>6.1f} {t.slowest:>6.1f} {t.usd:>8.2f}"
        )
    total_usd = sum(t.usd for t in by_agent.values())
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
    """The table for one window. ``source="-"`` reads log lines from stdin.

    The single place the window is turned into a report, so ``scout stats`` and
    ``python -m scout.stats`` cannot drift apart.
    """
    lines = sys.stdin if source == "-" else journal_lines(days)
    return render(summarise(lines))


def main() -> None:
    """``python -m scout.stats`` — kept working alongside ``scout stats``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="how far back to look")
    parser.add_argument(
        "source", nargs="?", help="'-' to read log lines from stdin instead"
    )
    args = parser.parse_args()
    print(report(args.days, args.source))


if __name__ == "__main__":
    main()
