"""Run-time check of the PopulationSim environment the popsim stage launches (issue #645, G7).

The stage runs PopulationSim as an external process, ``uv run --no-sync populationsim``
with the popsimprep directory as working directory
(``braunschweig.popsim.stage._prepare_batch_runner``). This module asks THAT environment
(same uv executable, same working directory) which ``populationsim`` it has installed, so
a run on an environment that lacks the configured fork fails before any seed is built.
Meaning and format of the configured version: ``KEY_POPSIM_VERSION`` in
``braunschweig.popsim.stage.config_keys``.
"""
from __future__ import annotations

import json
import logging
import subprocess

logger = logging.getLogger(__name__)

#: Wall-clock limit of the one version-report subprocess (seconds).
REPORT_TIMEOUT_S = 120

#: Keys of the report, in the order they are logged.
REPORT_KEYS = ("populationsim", "commit", "ortools", "numpy", "pandas", "python")

_SYNC_HINT = (
    "Run `uv sync --frozen` in `environments/populationsim` (the popsimprep "
    "environment) to install the configured PopulationSim.")

# Runs INSIDE the popsimprep environment and prints one JSON object. A package that is not
# installed raises (-> non-zero exit -> RuntimeError in the caller): nothing is guessed.
# The commit comes from the PEP 610 ``direct_url.json`` of a VCS install; "unknown" when the
# distribution was not installed from a VCS URL (the caller warns).
_REPORT_SCRIPT = """\
import json, platform
from importlib import metadata

commit = "unknown"
direct_url = metadata.distribution("populationsim").read_text("direct_url.json")
if direct_url:
    commit = json.loads(direct_url).get("vcs_info", {}).get("commit_id") or "unknown"
print(json.dumps({
    "populationsim": metadata.version("populationsim"),
    "commit": commit,
    "ortools": metadata.version("ortools"),
    "numpy": metadata.version("numpy"),
    "pandas": metadata.version("pandas"),
    "python": platform.python_version(),
}))
"""


def installed_tool_report(uv_path: str, project_dir: str, *,
                          subprocess_run=subprocess.run) -> dict:
    """Ask the popsimprep environment which PopulationSim stack is installed.

    One subprocess, ``[uv, "run", "--no-sync", "python", "-c", <script>]`` with
    ``cwd=project_dir`` (the command prefix and working directory of the stage's batch
    runner). Returns a dict with ``populationsim`` (PEP 440 version), ``commit`` (VCS commit
    of the install or ``"unknown"``), ``ortools``, ``numpy``, ``pandas`` and ``python``.

    ``subprocess_run`` is injectable so the parsing is testable without a uv environment.

    Raises:
        RuntimeError: non-zero exit, timeout, unparsable or incomplete output; the message
            carries the stderr excerpt and the sync hint.
    """
    command = [str(uv_path), "run", "--no-sync", "python", "-c", _REPORT_SCRIPT]
    try:
        completed = subprocess_run(
            command, cwd=str(project_dir), capture_output=True, text=True,
            timeout=REPORT_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"PopulationSim version check timed out after {REPORT_TIMEOUT_S} s in "
            f"{project_dir!r}. {_SYNC_HINT}") from exc
    except OSError as exc:
        raise RuntimeError(
            f"PopulationSim version check could not start {str(uv_path)!r} ({exc}). "
            f"Check the uv_path config key. {_SYNC_HINT}") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()[-1000:]
        raise RuntimeError(
            f"PopulationSim version check failed (exit {completed.returncode}) in "
            f"{project_dir!r}: {stderr}. {_SYNC_HINT}")
    try:
        report = json.loads(completed.stdout)
        missing = [key for key in REPORT_KEYS if key not in report]
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"PopulationSim version check printed unparsable output "
            f"({(completed.stdout or '')[:200]!r}). {_SYNC_HINT}") from exc
    if missing:
        raise RuntimeError(
            f"PopulationSim version check output lacks {missing}. {_SYNC_HINT}")
    return {key: report[key] for key in REPORT_KEYS}


def require_populationsim_version(installed: str, expected: str) -> None:
    """Raise unless the installed PopulationSim version equals the configured one.

    Compared as exact strings: the fork's local version ``0.10.0+bs.1`` must not be
    satisfied by the upstream ``0.10.0``.

    Raises:
        RuntimeError: naming both versions and how to sync the environment.
    """
    if installed != expected:
        raise RuntimeError(
            f"PopulationSim in the popsimprep environment is version {installed!r}, but the "
            f"config requires {expected!r}. {_SYNC_HINT}")


def check_populationsim_environment(context, uv_path: str, project_dir: str, expected: str, *,
                                    subprocess_run=subprocess.run) -> dict:
    """Verify the environment, log the report and store it as stage info.

    Always runs (no switch to bypass it). Logs the report at INFO, warns when the install
    carries no VCS commit, and stores the report as ``context.set_info(
    "populationsim_tool_report", report)``.

    Raises:
        RuntimeError: from :func:`installed_tool_report` or
            :func:`require_populationsim_version`.
    """
    report = installed_tool_report(uv_path, project_dir, subprocess_run=subprocess_run)
    if report["commit"] == "unknown":
        logger.warning(
            "[popsim.tool] the PopulationSim commit is unknown (no VCS entry in its "
            "direct_url.json); the run records only version %s.", report["populationsim"])
    logger.info("[popsim.tool] installed PopulationSim stack: %s",
                ", ".join(f"{key}={report[key]}" for key in REPORT_KEYS))
    require_populationsim_version(report["populationsim"], expected)
    context.set_info("populationsim_tool_report", report)
    return report
