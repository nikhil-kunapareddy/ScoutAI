"""deploy/pull-secrets.sh: Parameter Store to the env files the units read.

The script runs on the box as root, where a mistake either leaves the bots on
an old key without saying so or hands systemd a line it parses differently from
what was stored. `aws` is replaced with a script that prints canned
`get-parameters-by-path` output, so nothing here touches AWS.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "pull-secrets.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="pull-secrets.sh needs bash and jq, as the box has",
)


def run(tmp_path: Path, parameters: list[dict] | None) -> subprocess.CompletedProcess:
    """Run the script into `tmp_path/dest`; `None` makes `aws` fail as on AccessDenied."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    dest = tmp_path / "dest"
    dest.mkdir(exist_ok=True)
    fake = bin_dir / "aws"
    if parameters is None:
        fake.write_text("#!/bin/sh\necho 'AccessDeniedException' >&2\nexit 254\n")
    else:
        canned = tmp_path / "ssm.json"
        canned.write_text(json.dumps({"Parameters": parameters}))
        fake.write_text(f"#!/bin/sh\ncat '{canned}'\n")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), str(dest)],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def param(name: str, value: str) -> dict:
    return {"Name": name, "Value": value, "Type": "SecureString"}


def test_shared_and_per_agent_keys_land_in_their_own_files(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        [
            param("/scout/shared/ANTHROPIC_API_KEY", "sk-ant-1"),
            param("/scout/edu/SLACK_BOT_TOKEN", "xoxb-edu"),
            param("/scout/shared/LANGFUSE_PUBLIC_KEY", "pk-lf-1"),
        ],
    )

    assert result.returncode == 0, result.stderr
    dest = tmp_path / "dest"
    assert (dest / "secrets.env").read_text() == (
        "ANTHROPIC_API_KEY='sk-ant-1'\nLANGFUSE_PUBLIC_KEY='pk-lf-1'\n"
    )
    assert (dest / "secrets-edu.env").read_text() == "SLACK_BOT_TOKEN='xoxb-edu'\n"
    assert (dest / "secrets.env").stat().st_mode & 0o777 == 0o600


def test_the_log_names_keys_and_never_prints_a_value(tmp_path: Path) -> None:
    result = run(tmp_path, [param("/scout/shared/ANTHROPIC_API_KEY", "sk-ant-secret")])

    assert "ANTHROPIC_API_KEY" in result.stdout
    assert "sk-ant-secret" not in result.stdout + result.stderr


def test_a_value_reads_back_unchanged_when_sourced(tmp_path: Path) -> None:
    """Single quotes: no `$` expansion and no backslash escapes, in bash or systemd."""
    value = r"sk-ant-a$HOME \x"
    run(tmp_path, [param("/scout/shared/KEY", value)])

    sourced = subprocess.run(  # noqa: S603
        ["bash", "-c", f"set -a; . '{tmp_path / 'dest' / 'secrets.env'}'; printf %s \"$KEY\""],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    assert sourced.stdout == value


def test_a_file_whose_parameters_are_gone_is_removed(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "secrets-referral.env").write_text("SLACK_BOT_TOKEN='old'\n")
    (dest / "scout.env").write_text("DIGEST_SLACK_USER=U1\n")

    result = run(tmp_path, [param("/scout/shared/KEY", "v")])

    assert result.returncode == 0, result.stderr
    assert not (dest / "secrets-referral.env").exists()
    assert (dest / "scout.env").read_text() == "DIGEST_SLACK_USER=U1\n"  # hand-kept


@pytest.mark.parametrize(
    ("parameters", "why"),
    [
        (None, "AccessDenied"),
        ([param("/scout/shared/KEY", "it's")], "quote"),
        ([param("/scout/shared/KEY", "a\nb")], "line break"),
        ([param("/scout/KEY", "v")], "expected /scout/shared/NAME"),
        ([param("/scout/shared/NOT-A-NAME", "v")], "not an environment variable"),
    ],
)
def test_a_failure_writes_nothing_and_fails_the_deploy(
    tmp_path: Path, parameters: list[dict] | None, why: str
) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "secrets.env").write_text("KEY='previous'\n")

    result = run(tmp_path, parameters)

    assert result.returncode != 0
    assert why in result.stderr
    assert (dest / "secrets.env").read_text() == "KEY='previous'\n"
    assert sorted(p.name for p in dest.iterdir()) == ["secrets.env"]  # no temp dir left
