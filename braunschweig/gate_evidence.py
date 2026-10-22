"""Evidence files for the local push gate (stdlib only).

``scripts/run_tests.py`` and ``python -m braunschweig.documentation check`` record what
they verified in ``<checkout>/.claude/gate/*.json``; a personal ``PreToolUse`` hook reads
these files before ``git push`` / ``git pr`` (docs/codebase/notes/agent-process-automation.md).
A file is only evidence for the commit in ``head`` and only when ``dirty`` is ``False``.

``dirty`` deliberately ignores line-ending-only differences: inside WSL a Windows
checkout shows thousands of CRLF-only "modified" files, ``git status`` reports all of
them and needs 20+ seconds (docs/codebase/notes/linux-parity-testing.md). ``git diff
--ignore-cr-at-eol --quiet HEAD`` compares content against HEAD (staged and unstaged,
tracked files only) and answers through its exit code. ``--name-only`` is not used:
git 2.43 lists CRLF-only files there despite ``--ignore-cr-at-eol``.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import xml.etree.ElementTree as ElementTree

#: Evidence directory relative to the checkout; covered by the ``.claude/*`` gitignore.
GATE_DIR = ".claude/gate"

#: pytest options that only change reporting or parallelism: the ONLY options a ``full``
#: run may carry (fail-closed allowlist; anything else may narrow or skip the suite).
NEUTRAL_FLAGS = {"-q", "-qq", "-v", "-vv", "-l", "--showlocals", "-s"}
NEUTRAL_VALUE_OPTIONS = {"-n", "-p", "-W", "--tb", "--durations", "--junitxml", "--dist", "--color",
                         "--maxprocesses", "--durations-min", "--basetemp"}


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def platform_tag() -> str:
    """``linux`` / ``windows``: one evidence file per platform, so runs never overwrite each other."""
    return platform.system().lower()


def git_state(checkout, timeout: float = 60.0) -> dict:
    """``{"head", "dirty", "warning"}`` of ``checkout``; ``None`` values mean "not evidence"."""
    state = {"head": None, "dirty": None, "warning": None}
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True,
                              text=True, timeout=timeout)
        if head.returncode:
            state["warning"] = f"git rev-parse HEAD failed: {head.stderr.strip()}"
            return state
        state["head"] = head.stdout.strip()
        diff = subprocess.run(["git", "diff", "--ignore-cr-at-eol", "--quiet", "HEAD"],
                              cwd=checkout, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        state["warning"] = f"git failed: {error}"
        return state
    if diff.returncode in (0, 1):
        state["dirty"] = diff.returncode == 1
    else:
        state["warning"] = f"git diff failed: {diff.stderr.strip()}"
    return state


def run_scope(pytest_args, pipeline: bool = False, marker=None, environ=None) -> str:
    """``full`` only for the whole default suite with reporting/parallelism options; else ``focused``.

    Fail-closed: test paths, node ids, ``-x``/``-k``/``-m`` (also bundled as ``-xq``),
    ``-o``/``-c``, ``--fixtures``/``--help`` and any option not on the allowlist make a run
    ``focused``, as do the runner's own ``--pipeline``/``-m`` and a set ``PYTEST_ADDOPTS``.
    """
    environ = os.environ if environ is None else environ
    if pipeline or marker or environ.get("PYTEST_ADDOPTS"):
        return "focused"
    args = list(pytest_args)
    index = 0
    while index < len(args):
        arg = args[index]
        option = arg.split("=", 1)[0]
        if arg in NEUTRAL_FLAGS or re.fullmatch(r"-r\w+", arg):
            index += 1
        elif option in NEUTRAL_VALUE_OPTIONS or re.fullmatch(r"-[npW].+", arg):
            index += 1 if ("=" in arg or len(option) > 2 and not option.startswith("--")) else 2
        else:
            return "focused"
    return "full"


def junit_counts(path) -> dict:
    """passed/failed/errors/skipped from a JUnit XML report; ``None`` values when absent."""
    counts = {"passed": None, "failed": None, "errors": None, "skipped": None}
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError):
        return counts
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = {key: sum(int(suite.get(key, 0)) for suite in suites)
             for key in ("tests", "failures", "errors", "skipped")}
    counts.update(failed=total["failures"], errors=total["errors"], skipped=total["skipped"],
                  passed=total["tests"] - total["failures"] - total["errors"] - total["skipped"])
    return counts


def write_evidence(checkout, name: str, payload: dict) -> Path:
    """Write ``payload`` (plus ``schema``) atomically to ``<checkout>/.claude/gate/<name>``."""
    directory = Path(checkout) / GATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({"schema": 1, **payload}, indent=2) + "\n",
                         encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return path
