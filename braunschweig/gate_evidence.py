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
import subprocess
import xml.etree.ElementTree as ElementTree

#: Evidence directory relative to the checkout; covered by the ``.claude/*`` gitignore.
GATE_DIR = ".claude/gate"

#: pytest options that narrow the selected tests: a run using any of them is "focused".
SELECTING_OPTIONS = ("-k", "-x", "--exitfirst", "--lf", "--last-failed", "--ff",
                     "--failed-first", "--maxfail", "--co", "--collect-only", "--deselect",
                     "--ignore", "--ignore-glob", "--sw", "--stepwise", "-m")
#: pytest options whose value follows as a separate argument (not a test path).
VALUE_OPTIONS = ("-n", "-p", "-o", "-c", "-W", "-k", "-m", "--tb", "--durations",
                 "--junitxml", "--rootdir", "--basetemp", "--confcutdir", "--deselect",
                 "--ignore", "--ignore-glob", "--maxfail", "--dist")


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


def run_scope(pytest_args, pipeline: bool = False, marker=None) -> str:
    """``full`` for the whole default suite, ``focused`` when anything narrows the selection.

    ``pipeline`` and ``marker`` are the runner's own ``--pipeline`` / ``-m`` (it strips them
    from the pytest arguments). ``-n`` (xdist) only parallelises and keeps a run ``full``.
    """
    if pipeline or marker:
        return "focused"
    previous = None
    for arg in pytest_args:
        option = arg.split("=", 1)[0]
        if option in SELECTING_OPTIONS or any(
                arg.startswith(short) and len(arg) > 2 for short in ("-k", "-m")):
            return "focused"
        if not arg.startswith("-") and previous not in VALUE_OPTIONS:
            return "focused"  # a test path or node id
        previous = arg
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
