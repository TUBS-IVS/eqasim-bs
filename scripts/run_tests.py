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
    args, pytest_args = parser.parse_known_args(argv)
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
    command.extend(["-m", marker])
    return subprocess.call(command, cwd=REPO_ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
