"""Entry point at the repo root: ``python run.py`` is ``scout run``.

Kept because it is the shortest thing to type in a fresh checkout, and because
the deployed systemd units call this file by path. Every flag ``scout run``
takes works here too::

    python run.py                     # the agent named by AGENT (default bigtech)
    python run.py --agent edu         # same as AGENT=edu python run.py

Everything it does lives in ``scout/cli.py``.
"""

from __future__ import annotations

import sys

from scout.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["run", *sys.argv[1:]]))
