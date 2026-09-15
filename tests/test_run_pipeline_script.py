"""Behaviour tests for the server pipeline wrapper's JDK preflight."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "run_pipeline.sh"
BASH = shutil.which("bash")


def _prepare_harness(tmp_path: Path, *, java_version: str | None,
                     explicit_java_home: bool) -> tuple[dict[str, str], Path]:
    """Create only the wrapper's config, conda and optional JDK preconditions."""
    conda_root = tmp_path / "conda"
    conda_script = conda_root / "etc" / "profile.d" / "conda.sh"
    conda_script.parent.mkdir(parents=True)
    conda_script.write_text(
        "conda() { :; }\nexport JAVA_HOME=/conda-hook/stale-jdk\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yml").write_text("output_path: output\n", encoding="utf-8")
    (tmp_path / "run_pipeline.sh").write_bytes(
        SCRIPT.read_bytes().replace(b"\r\n", b"\n"))

    java_home = (tmp_path / "requested-jdk" if explicit_java_home
                 else tmp_path / "tools" / "jdk-25.0.3+9")
    if java_version is not None:
        java = java_home / "bin" / "java"
        java.parent.mkdir(parents=True)
        java.write_text(f'#!/bin/sh\necho \'openjdk version "{java_version}"\' >&2\n',
                        encoding="utf-8")
        java.chmod(0o755)

    environment = os.environ | {
        "EQASIM_REPO_DIR": str(tmp_path),
        "CONDA_ROOT": str(conda_root),
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
    }
    if explicit_java_home:
        environment["JAVA_HOME"] = str(java_home)
    else:
        environment.pop("JAVA_HOME", None)
    return environment, java_home


@pytest.mark.skipif(BASH is None, reason="run_pipeline.sh requires bash")
@pytest.mark.parametrize(
    ("java_version", "explicit_java_home", "expected"),
    [
        ("25.0.3", True, "Using JDK 25"),
        ("25.0.3", False, "Using JDK 25"),
        ("21.0.7", True, "requires JDK 25; found major version 21"),
        (None, False, "JDK 25 executable not found"),
    ],
)
def test_jdk_preflight_selects_or_rejects_the_configured_jdk(
        tmp_path: Path, java_version: str | None, explicit_java_home: bool,
        expected: str):
    """Accept JDK 25, but reject a stale or absent JDK before Maven can run."""
    environment, java_home = _prepare_harness(
        tmp_path, java_version=java_version, explicit_java_home=explicit_java_home)

    result = subprocess.run(
        [BASH, str(tmp_path / "run_pipeline.sh"), "config.yml"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert expected in output
    if java_version == "25.0.3":
        assert str(java_home) in output
        assert "Maven (mvn) not found" in output
