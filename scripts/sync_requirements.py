#!/usr/bin/env python3
"""Regenerate requirements.txt from pyproject.toml.

``pyproject.toml`` is the single source of truth for what Scout depends on, but
the Dockerfile and ``deploy/deploy.sh`` both install from a requirements file —
a plain list needs no build backend on the target. So the file is generated, not
maintained, and ``tests/test_packaging.py`` fails if it is stale.

    python scripts/sync_requirements.py           # write the files
    python scripts/sync_requirements.py --check   # fail if they are out of date

Needs Python 3.11+ for ``tomllib``; the package itself supports 3.10.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Sorted as third-party because the project targets 3.10, where tomllib is not
# in the standard library yet. It is, from 3.11 on.
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
RUNTIME = ROOT / "requirements.txt"
DEV = ROOT / "requirements-dev.txt"

HEADER = """\
# Generated from pyproject.toml — do not edit by hand.
#   python scripts/sync_requirements.py
#
# Why both: pyproject.toml is the source of truth, and this flat list is what
# the Dockerfile and deploy/deploy.sh install from, since a plain list needs no
# build backend on the target.
"""

DEV_HEADER = """\
# Generated from pyproject.toml — do not edit by hand.
#   python scripts/sync_requirements.py
#
# Development and CI dependencies; the runtime ones come in via the -r line.
-r requirements.txt
"""


def render() -> tuple[str, str]:
    """The contents both requirements files should have."""
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    runtime = "".join(f"{spec}\n" for spec in config["dependencies"])
    dev = "".join(f"{spec}\n" for spec in config["optional-dependencies"]["dev"])
    return f"{HEADER}\n{runtime}", f"{DEV_HEADER}\n{dev}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail instead of writing"
    )
    args = parser.parse_args()

    runtime, dev = render()
    wanted = {RUNTIME: runtime, DEV: dev}
    stale = [
        path
        for path, content in wanted.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != content
    ]

    if args.check:
        for path in stale:
            print(f"{path.name} is out of date", file=sys.stderr)
        if stale:
            print("Run: python scripts/sync_requirements.py", file=sys.stderr)
        return 1 if stale else 0

    for path in stale:
        path.write_text(wanted[path], encoding="utf-8")
        print(f"wrote {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
