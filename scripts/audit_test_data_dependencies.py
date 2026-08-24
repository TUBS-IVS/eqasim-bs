"""Find tests whose result depends on local-only data without declaring it.

Why this exists
---------------
Most of this project's inputs are gitignored: a fresh clone (and every git
worktree) carries only the committed, allowlisted tables. A test that reads one of
the local-only inputs without declaring that dependency misleads in one of two
directions:

* **False RED** -- it fails on every checkout without the data drop, which reads
  like a code regression on ``main``. Two tests did exactly that until 2026-08-24,
  and diagnosing it took a full baseline investigation.
* **Vacuous GREEN** -- it passes because the code under test silently fell back to
  a substitute method, so the green says nothing about the method the test claims
  to cover. This is the failure class CLAUDE.md's fallback-transparency rule exists
  for.

The rule this tool enforces is therefore: a test that reads a local-only input must
declare that dependency (a ``pytest.mark.skipif`` naming the missing file), so its
absence produces an honest SKIP instead of either lie.

How it works
------------
Two halves in one file. As a **pytest plugin** it wraps ``os.path.exists``,
``pathlib.Path.exists`` and ``open`` to record, per test, every probe of a path
under ``eqasim-data`` that is NOT there, and writes one tab-separated line per test
(outcome, test id, absent paths). The wrappers only observe: they delegate to the
originals and change no behaviour. Run **directly** on that report, it classifies
the findings.

Usage (from the repository root, in a tree WITHOUT the local-only data -- a git
worktree is exactly that)::

    python -m pytest tests/ -p scripts.audit_test_data_dependencies -q
    python scripts/audit_test_data_dependencies.py test_data_probe_audit.txt

``AUDIT_OUTPUT`` overrides the report path.

Limits, stated rather than hidden: a probe is only seen if it goes through the three
wrapped entry points, so a reader that stats via another route is invisible; and a
recorded probe is evidence that the test ASKED for an absent input, never by itself
proof that its assertion rests on it. The candidate class is a reading list, not a
verdict.
"""
from __future__ import annotations

import builtins
import collections
import os
import pathlib
import sys

# Paths under this directory are the project's data inputs; probes elsewhere (temp
# directories, site-packages, the repository's own source) are not of interest.
DATA_MARKER = "eqasim-data"

# A path segment tests use deliberately to denote "an input that is not there", for
# the assertions that a missing input must raise with context. Those probes are
# by-design, and counting them as findings would bury the real ones.
BY_DESIGN_SEGMENTS = ("_does_not_exist", "does_not_exist", "missing_dir", "nonexistent")

CLASS_FALSE_RED = "false_red"
CLASS_CANDIDATE = "candidate_vacuous_green"
CLASS_DECLARED = "declared_dependency"
CLASS_BY_DESIGN = "absent_by_design"

_real_os_exists = os.path.exists
_real_path_exists = pathlib.Path.exists
_real_open = builtins.open

_current_probes: set = set()
_records: dict = {}
_recording = False


# --------------------------------------------------------------------------- #
# Recording half (pytest plugin)
# --------------------------------------------------------------------------- #
def _note_absent(path):
    if not _recording:
        return
    try:
        text = os.fspath(path)
    except TypeError:
        return
    if not isinstance(text, str):
        return
    normalised = text.replace("\\", "/")
    if DATA_MARKER not in normalised or _real_os_exists(text):
        return
    _current_probes.add(normalised[normalised.find(DATA_MARKER):])


def _observing_os_exists(path):
    result = _real_os_exists(path)
    if not result:
        _note_absent(path)
    return result


def _observing_path_exists(self, *args, **kwargs):
    result = _real_path_exists(self, *args, **kwargs)
    if not result:
        _note_absent(self)
    return result


def _observing_open(file, *args, **kwargs):
    try:
        return _real_open(file, *args, **kwargs)
    except (FileNotFoundError, NotADirectoryError):
        _note_absent(file)
        raise


def pytest_configure(config):
    global _recording
    os.path.exists = _observing_os_exists
    pathlib.Path.exists = _observing_path_exists
    builtins.open = _observing_open
    _recording = False


def pytest_unconfigure(config):
    os.path.exists = _real_os_exists
    pathlib.Path.exists = _real_path_exists
    builtins.open = _real_open
    write_report(os.environ.get("AUDIT_OUTPUT", "test_data_probe_audit.txt"))


def pytest_runtest_setup(item):
    global _recording
    _current_probes.clear()
    _recording = True


def pytest_runtest_teardown(item, nextitem):
    global _recording
    _recording = False


def pytest_runtest_logreport(report):
    """Keep one row per test: its outcome plus every absent input it probed."""
    if report.when != "call" and not (report.when == "setup" and report.skipped):
        return
    outcome = report.outcome
    probes = set(_current_probes)
    previous = _records.get(report.nodeid)
    if previous is not None:
        # A skip decided in setup must not be overwritten by the call phase.
        outcome = previous[0] if previous[0] != "passed" else outcome
        probes |= set(previous[1])
    _records[report.nodeid] = (outcome, sorted(probes))


def write_report(destination):
    with _real_open(destination, "w", encoding="utf-8") as handle:
        for test_id in sorted(_records):
            outcome, probes = _records[test_id]
            handle.write("%s\t%s\t%s\n" % (outcome, test_id, ";".join(probes)))
    return destination


# --------------------------------------------------------------------------- #
# Classifying half (run the script directly on a report)
# --------------------------------------------------------------------------- #
def read_report(path):
    """``[(outcome, test_id, [absent paths])]`` from a report written by the plugin."""
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 3:
                continue
            outcome, test_id, probes = fields
            rows.append((outcome, test_id, [p for p in probes.split(";") if p]))
    return rows


def _is_by_design(probes):
    """Do ALL probed paths denote an input that is deliberately not there?"""
    return bool(probes) and all(
        any(segment in path for segment in BY_DESIGN_SEGMENTS) for path in probes)


def classify(rows) -> dict:
    """Group recorded tests into the four classes that need different answers.

    Tests that probed nothing absent are not classified at all: the audit must
    report findings, not every passing test.
    """
    classified = {CLASS_FALSE_RED: [], CLASS_CANDIDATE: [],
                  CLASS_DECLARED: [], CLASS_BY_DESIGN: []}
    for outcome, test_id, probes in rows:
        if not probes:
            continue
        if _is_by_design(probes):
            classified[CLASS_BY_DESIGN].append((test_id, probes))
        elif outcome in ("failed", "error"):
            classified[CLASS_FALSE_RED].append((test_id, probes))
        elif outcome == "skipped":
            classified[CLASS_DECLARED].append((test_id, probes))
        else:
            classified[CLASS_CANDIDATE].append((test_id, probes))
    return classified


def render(rows, classified) -> str:
    """Human-readable report: the finding classes, grouped by test module."""
    lines = ["tests recorded: %d, of which probed an absent input: %d"
             % (len(rows), sum(1 for row in rows if row[2])), ""]
    headings = {
        CLASS_FALSE_RED: "FALSE RED -- undeclared dependency, fails without the data drop",
        CLASS_CANDIDATE: "CANDIDATES -- passed while asking for an absent input (read these)",
        CLASS_BY_DESIGN: "BY DESIGN -- the probed path is meant to be absent",
        CLASS_DECLARED: "DECLARED -- skipped with the dependency named (the correct shape)",
    }
    for name in (CLASS_FALSE_RED, CLASS_CANDIDATE, CLASS_BY_DESIGN, CLASS_DECLARED):
        entries = classified[name]
        lines.append("== %s: %d ==" % (headings[name], len(entries)))
        by_module = collections.defaultdict(list)
        for test_id, probes in entries:
            by_module[test_id.split("::")[0]].append((test_id, probes))
        for module in sorted(by_module):
            inputs = sorted({os.path.basename(path)
                             for _, probes in by_module[module] for path in probes})
            lines.append("   %s  [%s]" % (module, ", ".join(inputs[:4])))
            if name in (CLASS_FALSE_RED, CLASS_CANDIDATE):
                for test_id, _ in sorted(by_module[module]):
                    lines.append("      - %s" % test_id.split("::", 1)[-1])
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: python scripts/audit_test_data_dependencies.py <report.txt>",
              file=sys.stderr)
        return 2
    if not os.path.isfile(argv[0]):
        print("report not found: %s (run the plugin first, see the module docstring)"
              % argv[0], file=sys.stderr)
        return 1

    rows = read_report(argv[0])
    classified = classify(rows)
    print(render(rows, classified))
    return 1 if classified[CLASS_FALSE_RED] else 0


if __name__ == "__main__":
    raise SystemExit(main())
