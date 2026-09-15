"""Filesystem paths shared across the platform.

Deliberately free of environment and configuration dependencies, so any module
can import it without pulling in ``settings`` — and therefore without loading a
``.env``. Anything a user can move lives in ``settings`` and is resolved through
``under_root``.
"""

from __future__ import annotations

from pathlib import Path

# scout/core/paths.py -> parents[2] is the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"


def under_root(configured: str) -> Path:
    """Absolute path for a configured file location.

    A relative path is taken against the project root, not the working
    directory: systemd starts the bot from ``/``, so a bare ``state/scout.sqlite``
    would otherwise land somewhere nobody expects.
    """
    path = Path(configured).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path
