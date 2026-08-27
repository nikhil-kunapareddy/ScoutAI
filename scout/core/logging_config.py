"""Logging setup for the bot process.

Separate from the Slack adapter because logging belongs to the process, not to
any one transport: the entry point configures it once, before anything logs.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import settings
from .paths import LOG_DIR

#: The logger every module in this package writes to.
LOGGER_NAME = "scout"

# Third-party loggers that are noisy at INFO. Slack's socket-mode client logs
# every routine reconnect, which would drown out our own lines. Their warnings
# and errors still come through.
_NOISY_LOGGERS = ("slack_bolt", "slack_sdk", "urllib3", "httpx", "httpcore", "anthropic")


def configure_logging() -> logging.Logger:
    """Set up console + rotating-file logging and return the ``scout`` logger.

    Rotation matters in production: without it ``bot.log`` grows forever.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        LOG_DIR / "bot.log",
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), file_handler],
        force=True,  # replace any handlers a library installed first
    )
    quiet_third_party_loggers()

    return logging.getLogger(LOGGER_NAME)


def quiet_third_party_loggers() -> None:
    """Turn down the noisy third-party loggers, unless we're debugging.

    Call again after the Slack app is built: slack-bolt sets a level on each of
    its loggers as it creates them, overriding anything set on the parent. Child
    loggers that already exist are clamped too, for the same reason.
    """
    if settings.LOG_LEVEL == "DEBUG":
        return  # the operator asked for everything

    existing = list(logging.root.manager.loggerDict)
    for name in _NOISY_LOGGERS:
        for target in [name] + [n for n in existing if n.startswith(f"{name}.")]:
            logging.getLogger(target).setLevel(logging.WARNING)
