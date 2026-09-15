"""Behavioural tests for the server bootstrap recovery contract."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


BASH = None if os.name == "nt" else shutil.which("bash")
SCRIPT = Path(__file__).parents[1] / "scripts" / "bootstrap_server.sh"


@pytest.fixture
def bootstrap_harness(tmp_path: Path) -> dict[str, str]:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    environment_dir = repo / "environments"
    environment_dir.mkdir()
    (environment_dir / "server-linux-64.lock").write_text("@EXPLICIT\n")
    (environment_dir / "requirements-server-linux-64.txt").write_text("pytest==9.1.1\n")
    conda_root = tmp_path / "conda"
    profile_dir = conda_root / "etc" / "profile.d"
    profile_dir.mkdir(parents=True)
    (profile_dir / "conda.sh").write_text("\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    for name, body in {
        "git": "echo git:$* >> \"$FAKE_LOG\"\nexit 0\n",
        "conda": """echo conda:$* >> \"$FAKE_LOG\"
if [ \"$1\" = env ] && [ \"$2\" = list ]; then echo \"$FAKE_ENV\"; fi
if [[ \"$1\" = run && \"$*\" == *\"pip install\"* && \"${FAIL_PIP:-0}\" = 1 ]]; then exit 42; fi
exit 0
""",
    }.items():
        command = bin_dir / name
        command.write_text("#!/usr/bin/env bash\n" + body)
        command.chmod(0o755)
    (bin_dir / "mamba").write_text((bin_dir / "conda").read_text())
    (bin_dir / "mamba").chmod(0o755)
    script = tmp_path / "bootstrap_server.sh"
    script.write_bytes(SCRIPT.read_bytes().replace(b"\r\n", b"\n"))
    script.chmod(0o755)
    return {
        "repo": str(repo),
        "conda_root": str(conda_root),
        "bin": str(bin_dir),
        "log": str(log),
        "script": str(script),
    }


def run_bootstrap(harness: dict[str, str], *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {
        "EQASIM_REPO_DIR": harness["repo"],
        "CONDA_ROOT": harness["conda_root"],
        "FAKE_LOG": harness["log"],
        "PATH": harness["bin"] + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
    } | extra
    return subprocess.run([BASH, harness["script"], *args], text=True, capture_output=True, env=environment, check=False)


@pytest.mark.skipif(BASH is None, reason="requires a POSIX bash runner")
def test_failed_first_pip_install_is_repaired_only_on_request(bootstrap_harness: dict[str, str]) -> None:
    failed = run_bootstrap(bootstrap_harness, FAKE_ENV="", FAIL_PIP="1")
    assert failed.returncode == 1
    assert "--repair-pip" in failed.stderr
    assert "pip check" not in Path(bootstrap_harness["log"]).read_text()

    repaired = run_bootstrap(bootstrap_harness, "--repair-pip", FAKE_ENV="eqasim")
    assert repaired.returncode == 0
    log = Path(bootstrap_harness["log"]).read_text()
    assert log.count("pip install") == 2
    assert "conda:create" in log
    assert "pip check" in log


@pytest.mark.skipif(BASH is None, reason="requires a POSIX bash runner")
def test_existing_environment_is_preserved_without_repair_option(bootstrap_harness: dict[str, str]) -> None:
    result = run_bootstrap(bootstrap_harness, FAKE_ENV="eqasim")
    assert result.returncode == 0
    log = Path(bootstrap_harness["log"]).read_text()
    assert "pip install" not in log
    assert "conda:create" not in log
    assert "pip check" in log


@pytest.mark.skipif(BASH is None, reason="requires a POSIX bash runner")
def test_unknown_option_fails_before_git_mutation(bootstrap_harness: dict[str, str]) -> None:
    result = run_bootstrap(bootstrap_harness, "--unknown", FAKE_ENV="")
    assert result.returncode == 2
    assert "unknown option" in result.stderr
    assert not Path(bootstrap_harness["log"]).exists()
