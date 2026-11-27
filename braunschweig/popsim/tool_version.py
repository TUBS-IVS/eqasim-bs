"""Run-time check of the PopulationSim environment the popsim stage launches (issue #645, G7).

The stage runs PopulationSim as an external process, ``uv run --no-sync populationsim``
with the popsimprep directory as working directory
(``braunschweig.popsim.stage._prepare_batch_runner``). This module asks THAT environment
(same uv executable, same working directory) which ``populationsim`` it has installed, so
a run on an environment that lacks the configured fork fails before any seed is built.
Meaning and format of the configured version: ``KEY_POPSIM_VERSION`` in
``braunschweig.popsim.stage.config_keys``. The whole installed environment, not only the
PopulationSim stack, is checked against ``uv.lock`` (:func:`require_environment_in_sync`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

#: Wall-clock limit of the one version-report subprocess (seconds).
REPORT_TIMEOUT_S = 120

#: Repository root: a relative ``popsimprep_dir`` is resolved against it, never against the
#: process working directory (the stage may be started from anywhere).
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Lock file of the popsimprep project that pins the PopulationSim commit.
LOCK_FILE_NAME = "uv.lock"

# The ``[[package]] name = "populationsim"`` block of a uv lock file, up to the next package.
_LOCK_BLOCK = re.compile(
    r'^\[\[package\]\]\s*\nname = "populationsim"\s*\n(?P<body>.*?)(?=^\[\[package\]\]|\Z)',
    re.DOTALL | re.MULTILINE)
# ``source = { git = "<url>?rev=<sha>#<sha>" }``: the commit uv resolved is the part after '#'.
_LOCK_GIT_SOURCE = re.compile(r'^source = \{ git = "[^"#]*#(?P<commit>[0-9a-f]{40})" \}',
                              re.MULTILINE)

#: Solver-stack packages whose installed version must equal the lock (besides PopulationSim).
_LOCKED_STACK = ("ortools", "numpy", "pandas")

#: Arguments of the read-only whole-environment check ``uv <args>`` (exit 0 = the installed
#: environment equals the lock, non-zero = uv would change something).
#: ``--frozen``: use ``uv.lock`` as it is, never re-resolve or rewrite it.
#: ``--check``: change nothing in the environment, only report whether a sync would.
#: ``--offline``: no network at stage start (a check must not need an index to answer).
SYNC_CHECK_ARGS = ("sync", "--frozen", "--check", "--offline")

#: Keys of the report, in the order they are logged.
REPORT_KEYS = ("populationsim", "commit", "ortools", "numpy", "pandas", "python")


def _sync_hint(project_dir) -> str:
    """The fix for a wrong or broken environment, naming the directory that was asked."""
    return (
        f"Run `uv sync --frozen` in {str(project_dir)!r} (the popsimprep environment the "
        "stage launches; the tracked project is environments/populationsim).")


def resolve_project_dir(configured, *, key: str) -> Path:
    """Return the popsimprep project directory as an absolute, existing directory.

    A relative ``configured`` path is resolved against the repository root (the tracked
    default ``environments/populationsim`` is repository-relative), not against the process
    working directory. ``key`` names the config key in the error message.

    Raises:
        RuntimeError: ``configured`` is empty or no directory.
    """
    if not configured:
        raise RuntimeError(f"config key {key!r} is empty: set the popsimprep project directory")
    path = Path(str(configured))
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_dir():
        raise RuntimeError(
            f"config key {key!r} = {str(configured)!r} resolves to {str(path)!r}, which is "
            "not a directory (a relative path is resolved against the repository root)")
    return path


def expected_populationsim_commit(project_dir) -> str:
    """Return the PopulationSim git commit that ``<project_dir>/uv.lock`` pins.

    Reads the ``populationsim`` package entry of the lock file; its
    ``source = { git = "...?rev=<sha>#<sha>" }`` carries the resolved commit after ``#``.

    Raises:
        RuntimeError: no lock file, no ``populationsim`` entry, or an entry without a git
            source with a 40-character commit.
    """
    lock_path = Path(project_dir) / LOCK_FILE_NAME
    if not lock_path.is_file():
        raise RuntimeError(
            f"no {LOCK_FILE_NAME} in {str(project_dir)!r}: the expected PopulationSim commit "
            "is read from it")
    block = _LOCK_BLOCK.search(lock_path.read_text(encoding="utf-8"))
    match = _LOCK_GIT_SOURCE.search(block.group("body")) if block else None
    if match is None:
        raise RuntimeError(
            f"{str(lock_path)!r} has no [[package]] entry 'populationsim' with a git source "
            "pinned to a commit (source = { git = \"...#<40-hex commit>\" }); the project must "
            "install the group's fork by commit")
    return match.group("commit")


def _read_lock_text(project_dir) -> str:
    """Return the text of ``<project_dir>/uv.lock`` (universal newlines: CRLF reads as LF)."""
    lock_path = Path(project_dir) / LOCK_FILE_NAME
    if not lock_path.is_file():
        raise RuntimeError(
            f"no {LOCK_FILE_NAME} in {str(project_dir)!r}: the locked PopulationSim stack is "
            "read from it")
    with open(lock_path, encoding="utf-8", newline=None) as handle:
        return handle.read()


def lock_content_hash(project_dir) -> str:
    """Return the sha256 (hex) of ``<project_dir>/uv.lock`` with LF line endings.

    The lock pins the whole solver stack (PopulationSim commit, ortools, numpy, pandas,
    ...). A CRLF checkout of the same lock hashes the same as an LF one.

    Raises:
        RuntimeError: no lock file.
    """
    return hashlib.sha256(_read_lock_text(project_dir).encode("utf-8")).hexdigest()


def locked_package_versions(project_dir, names) -> dict[str, str]:
    """Return ``{name: version}`` of the ``[[package]]`` entries of ``<project_dir>/uv.lock``.

    Raises:
        RuntimeError: no lock file, or a package of ``names`` has no entry with a version.
    """
    text = _read_lock_text(project_dir)
    versions = {}
    for name in names:
        block = re.search(
            r'^\[\[package\]\]\s*\nname = "' + re.escape(name) + r'"\s*\n(?P<body>.*?)'
            r'(?=^\[\[package\]\]|\Z)', text, re.DOTALL | re.MULTILINE)
        match = (re.search(r'^version = "(?P<v>[^"]+)"', block.group("body"), re.MULTILINE)
                 if block else None)
        if match is None:
            raise RuntimeError(
                f"{LOCK_FILE_NAME} in {str(project_dir)!r} has no [[package]] entry {name!r} "
                "with a version")
        versions[name] = match.group("v")
    return versions


# Runs INSIDE the popsimprep environment and prints one JSON object. A package that is not
# installed raises (-> non-zero exit -> RuntimeError in the caller): nothing is guessed.
# The commit comes from the PEP 610 ``direct_url.json`` of a VCS install; "unknown" when the
# distribution was not installed from a VCS URL (the caller refuses it).
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
            f"{project_dir!r}. {_sync_hint(project_dir)}") from exc
    except OSError as exc:
        raise RuntimeError(
            f"PopulationSim version check could not start {str(uv_path)!r} ({exc}). "
            f"Check the uv_path config key. {_sync_hint(project_dir)}") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()[-1000:]
        raise RuntimeError(
            f"PopulationSim version check failed (exit {completed.returncode}) in "
            f"{project_dir!r}: {stderr}. {_sync_hint(project_dir)}")
    try:
        report = json.loads(completed.stdout)
        missing = [key for key in REPORT_KEYS if key not in report]
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"PopulationSim version check printed unparsable output "
            f"({(completed.stdout or '')[:200]!r}). {_sync_hint(project_dir)}") from exc
    if missing:
        raise RuntimeError(
            f"PopulationSim version check output lacks {missing}. {_sync_hint(project_dir)}")
    return {key: report[key] for key in REPORT_KEYS}


def require_environment_in_sync(uv_path: str, project_dir, *,
                                subprocess_run=subprocess.run) -> None:
    """Raise unless the installed popsimprep environment equals ``uv.lock`` in every package.

    One read-only subprocess, ``[uv, *SYNC_CHECK_ARGS]`` with ``cwd=project_dir``. It catches
    what the report of :func:`installed_tool_report` cannot: a differing version of any
    package outside the PopulationSim stack (for example one raised for a security fix).

    Raises:
        RuntimeError: non-zero exit (the message carries the tail of uv's output, which lists
            the would-be changes), timeout, or uv could not be started; each with the sync hint.
    """
    command = [str(uv_path), *SYNC_CHECK_ARGS]
    try:
        completed = subprocess_run(
            command, cwd=str(project_dir), capture_output=True, text=True,
            timeout=REPORT_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"environment check (uv sync --frozen --check) timed out after "
            f"{REPORT_TIMEOUT_S} s in {str(project_dir)!r}. {_sync_hint(project_dir)}") from exc
    except OSError as exc:
        raise RuntimeError(
            f"environment check could not start {str(uv_path)!r} ({exc}). "
            f"Check the uv_path config key. {_sync_hint(project_dir)}") from exc
    if completed.returncode != 0:
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()[-1000:]
        raise RuntimeError(
            f"the installed popsimprep environment differs from {LOCK_FILE_NAME} "
            f"(uv sync --frozen --check, exit {completed.returncode}) in "
            f"{str(project_dir)!r}: {output}. {_sync_hint(project_dir)}")
    logger.info("[popsim.tool] installed environment equals %s (uv sync --frozen --check)",
                LOCK_FILE_NAME)


def require_populationsim_version(installed: str, expected: str, *,
                                  project_dir) -> None:
    """Raise unless the installed PopulationSim version equals the configured one.

    Compared as exact strings: the fork's local version ``0.10.0+bs.1`` must not be
    satisfied by the upstream ``0.10.0``.

    ``project_dir`` is the environment that was asked; it names the directory to sync.

    Raises:
        RuntimeError: naming both versions and how to sync the environment.
    """
    if installed != expected:
        raise RuntimeError(
            f"PopulationSim in the popsimprep environment is version {installed!r}, but the "
            f"config requires {expected!r}. {_sync_hint(project_dir)}")


def check_populationsim_environment(context, uv_path: str, project_dir: str, expected: str, *,
                                    subprocess_run=subprocess.run) -> dict:
    """Verify the environment, log the report and store it as stage info.

    Always runs (no switch to bypass it). Two checks: the installed version equals the
    configured one, and the installed git commit equals the commit ``<project_dir>/uv.lock``
    pins (:func:`expected_populationsim_commit`): the version string alone cannot tell two
    builds of the fork apart. Then the whole environment must equal the lock
    (:func:`require_environment_in_sync`). Logs the report at INFO and stores it as
    ``context.set_info("populationsim_tool_report", report)``.

    Returns: the report; after this returns, ``report["commit"]`` is the locked commit.

    Raises:
        RuntimeError: from :func:`installed_tool_report`,
            :func:`require_populationsim_version` or :func:`expected_populationsim_commit`,
            or when the installed commit is unknown or differs from the locked one, or when the
            installed ortools, numpy or pandas version differs from the lock, or from
            :func:`require_environment_in_sync` when any other installed package differs.
    """
    report = installed_tool_report(uv_path, project_dir, subprocess_run=subprocess_run)
    logger.info("[popsim.tool] installed PopulationSim stack: %s",
                ", ".join(f"{key}={report[key]}" for key in REPORT_KEYS))
    require_populationsim_version(report["populationsim"], expected, project_dir=project_dir)
    expected_commit = expected_populationsim_commit(project_dir)
    if report["commit"] != expected_commit:
        raise RuntimeError(
            f"PopulationSim in the popsimprep environment is commit {report['commit']!r} "
            f"('unknown' = not installed from a git URL), but {LOCK_FILE_NAME} pins "
            f"{expected_commit!r}. {_sync_hint(project_dir)}")
    locked = locked_package_versions(project_dir, _LOCKED_STACK)
    for name in _LOCKED_STACK:
        if report[name] != locked[name]:
            raise RuntimeError(
                f"{name} in the popsimprep environment is version {report[name]!r}, but "
                f"{LOCK_FILE_NAME} pins {locked[name]!r}. {_sync_hint(project_dir)}")
    require_environment_in_sync(uv_path, project_dir, subprocess_run=subprocess_run)
    context.set_info("populationsim_tool_report", report)
    return report
