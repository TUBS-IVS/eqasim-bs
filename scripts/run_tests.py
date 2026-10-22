"""Run the same regression command on Linux, WSL2 and native Windows.

Use ``--check`` for interpreter/import diagnostics, ``--pipeline`` for the
existing real-data opt-in tests. Remaining arguments are passed to pytest.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from braunschweig import gate_evidence  # noqa: E402  (needs the repository on sys.path)
# WSL mounts Windows drive ``X:`` at ``/mnt/x``; overridable for tests.
WSL_DRIVE_MOUNT = Path("/mnt")
WINDOWS_GITDIR = re.compile(r"^gitdir:\s*([A-Za-z]):[\\/](.*?)\s*$")


def repair_worktree_pointer(checkout, drive_mount=None):
    """Make a linked worktree's ``.git`` pointer readable by git inside WSL.

    Windows git writes ``gitdir: C:/.../.git/worktrees/<id>`` into the ``.git`` file of
    a linked worktree. POSIX git treats ``C:/...`` as a relative path, so every
    git-dependent test fails under WSL in a Windows-created worktree
    (docs/codebase/notes/linux-parity-testing.md). A pointer relative to the checkout is
    read correctly by both gits; the back-pointer in ``.git/worktrees/<id>/gitdir`` is
    Windows-owned and stays untouched. Returns the new relative pointer, or ``None`` when
    nothing was rewritten: a main checkout, an already resolvable pointer, or a pointer
    whose admin dir cannot be found under ``drive_mount`` (reported on stderr, never
    rewritten blindly).
    """
    pointer_file = Path(checkout) / ".git"
    if not pointer_file.is_file():
        return None
    content = pointer_file.read_text(encoding="utf-8")
    match = WINDOWS_GITDIR.match(content)
    if match is None:
        return None
    drive, rest = match.groups()
    mount = WSL_DRIVE_MOUNT if drive_mount is None else Path(drive_mount)
    admin_dir = mount / drive.lower() / rest.replace("\\", "/")
    if not (admin_dir / "commondir").is_file():
        print(f"cannot repair worktree pointer {content.strip()!r}: {admin_dir} is not a "
              "worktree admin dir, git inside WSL will not find this repository",
              file=sys.stderr)
        return None
    relative = os.path.relpath(admin_dir, Path(checkout)).replace("\\", "/")
    pointer_file.write_text(f"gitdir: {relative}\n", encoding="utf-8", newline="\n")
    print(f"repaired worktree pointer: {content.strip()} -> gitdir: {relative} "
          "(git inside WSL cannot follow Windows paths)", flush=True)
    return relative


def check_environment():
    """Report the actual runtime and reject known interpreter/import conflicts."""
    sys.path.insert(0, str(REPO_ROOT))
    errors = []
    if sys.version_info[:2] != (3, 10):
        errors.append("Use Python 3.10 from the eqasim environment.")
    spec = importlib.util.find_spec("matsim")
    locations = [] if spec is None else list(spec.submodule_search_locations or [])
    expected = (REPO_ROOT / "matsim").resolve()
    if not any(Path(path).resolve() == expected for path in locations):
        errors.append(
            "The repository matsim package is shadowed or missing. Use the locked "
            "eqasim environment; inspect/remove the conflicting matsim-tools "
            "installation in that environment rather than patching sys.modules."
        )
    versions = {}
    for package in ("pytest", "numpy", "scipy", "pandas", "geopandas", "synpp", "chainsolvers"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
            errors.append(f"Missing {package}; install the project environment.")
    return {
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "repository": str(REPO_ROOT),
        "matsim_locations": locations,
        "packages": versions,
        "errors": errors,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Print environment diagnostics only.")
    parser.add_argument("--pipeline", action="store_true", help="Select real-data pipeline tests only.")
    parser.add_argument("--keep-worktree-pointer", action="store_true",
                        help="Under Linux/WSL, leave a Windows-absolute .git pointer of a linked "
                             "worktree as it is instead of rewriting it to a relative path.")
    parser.add_argument("--no-evidence", action="store_true",
                        help="Do not write .claude/gate/tests-<platform>.json (for probe runs "
                             "that must not replace the checkout's push-gate evidence).")
    args, pytest_args = parser.parse_known_args(argv)
    if platform.system() == "Linux" and not args.keep_worktree_pointer:
        repair_worktree_pointer(REPO_ROOT)
    report = check_environment()
    if args.check:
        print(json.dumps(report, indent=2))
    else:
        print(f"Python: {report['python']} ({report['python_version']})", flush=True)
    if report["errors"]:
        for error in report["errors"]:
            print(error, file=sys.stderr)
        return 2
    if args.check:
        return 0
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    # Pytest keeps only the last -m. Extract the caller's expression (including
    # passthrough arguments after --) and intersect it with our run boundary.
    marker_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    marker_parser.add_argument("-m", dest="expression")
    marker_args, pytest_args = marker_parser.parse_known_args(pytest_args)
    env = dict(os.environ, PYTHONUTF8="1")
    if args.pipeline:
        env["EQASIM_BS_RUN_PIPELINE"] = "1"
        if not any(flag in pytest_args for flag in ("--collect-only", "--co")):
            result = subprocess.call(
                [sys.executable, str(REPO_ROOT / "scripts" / "verify_braunschweig_inputs.py"),
                 "--matsim"], cwd=REPO_ROOT, env=env,
            )
            if result:
                return result
            if shutil.which("java") is None or shutil.which("mvn") is None:
                print("Pipeline tests require JDK 25 and Maven on PATH.", file=sys.stderr)
                return 2
            java = subprocess.run(["java", "-version"], capture_output=True, text=True)
            if java.returncode or not re.search(r'version "25(?:[.\"]|$)', java.stdout + java.stderr):
                print("Pipeline tests require JDK 25 on PATH.", file=sys.stderr)
                return 2
    else:
        env.pop("EQASIM_BS_RUN_PIPELINE", None)
    # Keep the real-data boundary explicit even when the caller's shell has an
    # old opt-in variable set. Preserve other pytest arguments, including -k.
    marker = "pipeline" if args.pipeline else "not pipeline"
    if marker_args.expression:
        marker = f"({marker}) and ({marker_args.expression})"
    command = [sys.executable, "-u", "-m", "pytest", "-c", str(REPO_ROOT / "pytest.ini")]
    command.extend(pytest_args or ["-q"])
    junitxml = next((arg.split("=", 1)[1] for arg in pytest_args if arg.startswith("--junitxml=")),
                    None)
    if junitxml is None and not args.no_evidence:
        junitxml = str(Path(REPO_ROOT) / gate_evidence.GATE_DIR
                       / f"junit-{gate_evidence.platform_tag()}.xml")
        command.append(f"--junitxml={junitxml}")
    command.extend(["-m", marker])
    started_at = gate_evidence.now_iso()
    exit_code = subprocess.call(command, cwd=REPO_ROOT, env=env)
    if not args.no_evidence:
        record_test_evidence(command, pytest_args, args.pipeline, marker_args.expression,
                             exit_code, junitxml, started_at)
    return exit_code


def record_test_evidence(command, pytest_args, pipeline, marker, exit_code, junitxml, started_at):
    """Write ``.claude/gate/tests-<platform>.json`` for the local push gate.

    The gate (docs/codebase/notes/agent-process-automation.md) accepts only a ``full``
    run of a clean HEAD; a writer failure is reported and never changes the exit code.
    """
    try:
        junit_path = Path(junitxml)
        if not junit_path.is_absolute():
            junit_path = Path(REPO_ROOT) / junit_path
        scope = gate_evidence.run_scope(pytest_args, pipeline=pipeline, marker=marker)
        payload = {**gate_evidence.git_state(REPO_ROOT),
                   "platform": gate_evidence.platform_tag(), "python": sys.executable,
                   "command": command, "scope": scope, "exit": exit_code,
                   **gate_evidence.junit_counts(junit_path), "junitxml": str(junit_path),
                   "started_at": started_at, "finished_at": gate_evidence.now_iso()}
        path = gate_evidence.write_evidence(REPO_ROOT, f"tests-{payload['platform']}.json", payload)
        print(f"evidence: {path} (scope={scope}, exit={exit_code}, head={payload['head']}, "
              f"dirty={payload['dirty']})", flush=True)
    except Exception as error:  # evidence is advisory for the runner; never mask pytest's result
        print(f"could not write test evidence: {error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
