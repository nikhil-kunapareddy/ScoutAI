"""Report a failed unit to Slack, because that is where the user looks.

Wired up as systemd's ``OnFailure=scout-alert@%n.service``. Slack rather than
CloudWatch on purpose: the box has no IAM role, so it cannot push metrics or
logs to AWS, and an alert that needs a console visit is an alert nobody reads.

Note what this does and does not catch. ``Restart=always`` means the bot
restarting is normal and silent; systemd only marks it failed once it exceeds
its restart burst, which is the signal worth waking up for. A bot that is
running but has quietly lost its websocket looks healthy here — the daily digest
is the backstop for that: if it stops arriving, something is wrong.
"""

from __future__ import annotations

import logging
import subprocess
import sys

from .core import settings
from .core.logging_config import configure_logging
from .slack.notify import post_dm

log = logging.getLogger("scout")

#: Journal lines to quote, enough to show a traceback's last frames.
_CONTEXT_LINES = 20


def recent_log(unit: str) -> str:
    """The tail of ``unit``'s journal, or a note saying why we have none."""
    try:
        return subprocess.run(
            ["journalctl", "-u", unit, "-n", str(_CONTEXT_LINES), "--no-pager", "-o", "cat"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"(could not read the journal: {e})"


def main() -> None:
    configure_logging()
    unit = sys.argv[1] if len(sys.argv) > 1 else "a scout unit"

    if not (settings.SLACK_BOT_TOKEN and settings.DIGEST_SLACK_USER):
        # Nothing to alert through; say so in the journal and stop.
        raise SystemExit("No SLACK_BOT_TOKEN/DIGEST_SLACK_USER, so no alert sent.")

    body = f":rotating_light: *{unit}* failed on the bot host.\n\n```\n{recent_log(unit)}\n```"
    post_dm(settings.DIGEST_SLACK_USER, body)


if __name__ == "__main__":
    main()
