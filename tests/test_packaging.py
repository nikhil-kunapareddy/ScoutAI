"""The packaging metadata: installable, and consistent with itself.

Three things can silently drift apart in a project that is both a package and a
deployed rsync: the dependency lists, the version, and the console script. Each
one is cheap to assert and annoying to discover in production.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import scout

ROOT = Path(__file__).resolve().parents[1]

tomllib = pytest.importorskip(
    "tomllib", reason="tomllib ships with Python 3.11+; CI checks this there"
)


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_requirements_files_match_pyproject() -> None:
    """pyproject is the source of truth; the flat lists are generated from it.

    The Dockerfile and deploy.sh install from requirements.txt, so a dependency
    added only to pyproject would be missing on the box — which is how
    langgraph-checkpoint-sqlite could once have gone absent from an install.
    """
    check = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "sync_requirements.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr


def test_the_version_is_declared_once(pyproject) -> None:
    """Read from the package, so an sdist and ``scout.__version__`` agree."""
    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "scout.__version__"
    }
    assert scout.__version__


def test_the_console_script_points_at_something_importable(pyproject) -> None:
    """``pip install scout`` puts a ``scout`` command on PATH — this is it.

    It used to name ``run:main``, a module that is not part of the package and
    so is absent from an install: the command existed and could not start.
    """
    from scout.cli import main

    assert pyproject["project"]["scripts"] == {"scout": "scout.cli:main"}
    assert callable(main)
