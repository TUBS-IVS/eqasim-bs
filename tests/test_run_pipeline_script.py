"""Behaviour tests for the server pipeline wrapper's JDK preflight."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "run_pipeline.sh"
BASH = shutil.which("bash")


def _prepare_harness(tmp_path: Path, *, java_version: str | None,
                     explicit_java_home: bool, java_runs: bool,
                     java_home_has_spaces: bool,
                     config_contents: str = "config: {}\n") -> tuple[dict[str, str], Path]:
    """Create only the wrapper's config, conda and optional JDK preconditions."""
    conda_root = tmp_path / "conda"
    conda_script = conda_root / "etc" / "profile.d" / "conda.sh"
    conda_script.parent.mkdir(parents=True)
    conda_script.write_text(
        "conda() { :; }\nexport JAVA_HOME=/conda-hook/stale-jdk\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yml").write_text(config_contents, encoding="utf-8")
    (tmp_path / "run_pipeline.sh").write_bytes(
        SCRIPT.read_bytes().replace(b"\r\n", b"\n"))

    requested_jdk_name = "requested jdk" if java_home_has_spaces else "requested-jdk"
    java_home = (tmp_path / requested_jdk_name if explicit_java_home
                 else tmp_path / "tools" / "jdk-25.0.3+9")
    if java_version is not None:
        java = java_home / "bin" / "java"
        java.parent.mkdir(parents=True)
        exit_command = "" if java_runs else "exit 23\n"
        java.write_text(f'#!/bin/sh\necho \'openjdk version "{java_version}"\' >&2\n{exit_command}',
                        encoding="utf-8")
        java.chmod(0o755)

    harness_bin = tmp_path / "harness-bin"
    harness_bin.mkdir()
    (harness_bin / "python").symlink_to(sys.executable)

    environment = os.environ | {
        "EQASIM_REPO_DIR": str(tmp_path),
        "CONDA_ROOT": str(conda_root),
        "HOME": str(tmp_path),
        "PATH": str(harness_bin),
    }
    if explicit_java_home:
        environment["JAVA_HOME"] = str(java_home)
    else:
        environment.pop("JAVA_HOME", None)
    return environment, java_home


@pytest.mark.skipif(os.name != "posix" or BASH is None,
                    reason="run_pipeline.sh harness requires POSIX bash")
@pytest.mark.parametrize(
    ("java_version", "explicit_java_home", "java_runs", "java_home_has_spaces",
     "config_contents", "expected"),
    [
        ("25.0.3", True, True, True, "config: {}\n", "Using JDK 25"),
        ("25.0.3", False, True, False, "config: {}\n", "Using JDK 25"),
        ("25.0.3", True, True, False, "config:\n  java_binary: java\n", "Using JDK 25"),
        ("21.0.7", True, True, False, "config: {}\n", "requires JDK 25; found major version 21"),
        (None, False, True, False, "config: {}\n", "JDK 25 executable not found"),
        ("25.0.3", True, False, False, "config: {}\n", "could not execute Java"),
        ("25.0.3", True, True, False,
         "config:\n  java_home: /different/jdk-25\n", "config java_home does not match"),
        ("25.0.3", True, True, False,
         "config:\n  java_binary: /bin/sh\n", "config java_binary does not match"),
        ("25.0.3", True, True, False,
         "config: []\n", "field 'config' must contain a mapping"),
    ],
)
def test_jdk_preflight_selects_or_rejects_the_configured_jdk(
        tmp_path: Path, java_version: str | None, explicit_java_home: bool,
        java_runs: bool, java_home_has_spaces: bool, config_contents: str,
        expected: str):
    """Accept JDK 25, but reject a stale or absent JDK before Maven can run."""
    environment, java_home = _prepare_harness(
        tmp_path, java_version=java_version, explicit_java_home=explicit_java_home,
        java_runs=java_runs, java_home_has_spaces=java_home_has_spaces,
        config_contents=config_contents)

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
    if java_version == "25.0.3" and java_runs and expected == "Using JDK 25":
        assert str(java_home) in output
        assert "Maven (mvn) not found" in output


def _prepare_stubbed_run(tmp_path: Path, synpp_exit_code: int) -> dict[str, str]:
    """Harness that gets past every preflight: stub mvn, sampler and run_synpp.py record what they see."""
    environment, _ = _prepare_harness(
        tmp_path, java_version="25.0.3", explicit_java_home=True, java_runs=True,
        java_home_has_spaces=False)
    harness_bin = tmp_path / "harness-bin"
    for tool in ("bash", "mkdir", "date", "tee", "sleep"):
        (harness_bin / tool).symlink_to(shutil.which(tool))
    mvn = harness_bin / "mvn"
    mvn.write_text("#!/bin/sh\n", encoding="utf-8")
    mvn.chmod(0o755)
    (tmp_path / "overlay.yml").write_text("config: {}\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    # The sampler stub records its pid and then idles, like the real background sampler.
    (scripts / "sample_load.sh").write_text("echo $$ > sampler.pid\nexec sleep 300\n", encoding="utf-8")
    (scripts / "run_synpp.py").write_text(
        "import os, sys, time\n"
        # Sync point: the background sampler must have written its pid before this run ends, or the
        # script's cleanup could kill the sampler before the pid exists and the test would read nothing.
        "deadline = time.monotonic() + 5.0\n"
        "while not os.path.exists('sampler.pid') and time.monotonic() < deadline:\n"
        "    time.sleep(0.02)\n"
        "open('synpp_args.txt', 'w').write('\\n'.join(sys.argv[1:]))\n"
        f"raise SystemExit({synpp_exit_code})\n", encoding="utf-8")
    return environment | {"EQASIM_SKIP_VERIFY": "1"}


@pytest.mark.skipif(os.name != "posix" or BASH is None,
                    reason="run_pipeline.sh harness requires POSIX bash")
@pytest.mark.parametrize("synpp_exit_code", [0, 7])
def test_load_sampler_is_stopped_and_run_status_is_kept(tmp_path: Path, synpp_exit_code: int):
    """No sampler process outlives the script, also when the run fails under `set -e`."""
    environment = _prepare_stubbed_run(tmp_path, synpp_exit_code)

    result = subprocess.run(
        [BASH, str(tmp_path / "run_pipeline.sh"), "config.yml"],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False)

    assert (result.returncode == 0) == (synpp_exit_code == 0), result.stdout + result.stderr
    if synpp_exit_code != 0:
        assert result.returncode == synpp_exit_code
    sampler_pid = int((tmp_path / "sampler.pid").read_text())
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(sampler_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(sampler_pid, signal.SIGKILL)
        pytest.fail("the load sampler outlived run_pipeline.sh")


@pytest.mark.skipif(os.name != "posix" or BASH is None,
                    reason="run_pipeline.sh harness requires POSIX bash")
def test_all_config_arguments_are_forwarded_to_run_synpp(tmp_path: Path):
    """The composed base+overlay form must reach run_synpp.py, not just the first path."""
    environment = _prepare_stubbed_run(tmp_path, 0)

    subprocess.run(
        [BASH, str(tmp_path / "run_pipeline.sh"), "config.yml", "overlay.yml"],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=True)

    assert (tmp_path / "synpp_args.txt").read_text().split("\n") == ["config.yml", "overlay.yml"]


@pytest.mark.skipif(os.name != "posix" or BASH is None,
                    reason="run_pipeline.sh harness requires POSIX bash")
def test_every_composed_config_is_preflight_checked(tmp_path: Path):
    """A java_home override in the overlay (second path) is rejected like one in the base."""
    environment, _ = _prepare_harness(
        tmp_path, java_version="25.0.3", explicit_java_home=True, java_runs=True,
        java_home_has_spaces=False)
    (tmp_path / "overlay.yml").write_text(
        "config:\n  java_home: /different/jdk-25\n", encoding="utf-8")

    result = subprocess.run(
        [BASH, str(tmp_path / "run_pipeline.sh"), "config.yml", "overlay.yml"],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False)

    assert result.returncode == 1
    assert "config java_home does not match" in result.stdout + result.stderr
