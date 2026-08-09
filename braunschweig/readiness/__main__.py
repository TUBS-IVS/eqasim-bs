"""CLI for the feature-readiness register.

    python -m braunschweig.readiness check     # resolve every declared pointer
    python -m braunschweig.readiness render    # regenerate docs/readiness/README.md

``check`` exits 1 when any finding is FAIL, so it can gate a workflow; WARN and SKIP
never fail the command (an honest "not measured yet" must stay reportable).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from braunschweig.readiness.checks import FAIL, OK, SKIP, WARN, CheckContext, run_all_checks, matrix_coverage
from braunschweig.readiness.registry import load_registry
from braunschweig.readiness.render import render_table, write_table

#: Repository root: this file is <root>/braunschweig/readiness/__main__.py
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m braunschweig.readiness",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "render"))
    parser.add_argument("--repo-root", default=REPO_ROOT,
                        help="repository root (default: inferred from this file's location)")
    parser.add_argument("--quiet", action="store_true", help="print only WARN and FAIL findings")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Declarations legitimately quote the STATUS matrix's Unicode legend symbols
    # (e.g. the green-circle "on" marker) in their notes. Windows consoles default
    # to a legacy code page (cp1252) that cannot encode them, which would otherwise
    # crash this CLI on the very findings it exists to report. Re-encode instead of
    # failing; this never touches file I/O, only what this process prints.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    declarations = load_registry(args.repo_root)
    context = CheckContext(args.repo_root)
    findings = run_all_checks(declarations, context)

    if args.command == "render":
        path = write_table(render_table(declarations, findings, context), args.repo_root)
        print(f"rendered {len(declarations)} feature(s) to {path}")
        return 0

    shown = [f for f in findings if not (args.quiet and f.severity in (OK, SKIP))]
    for finding in shown:
        print(finding)

    covered, active = matrix_coverage(declarations, context)
    counts = {level: sum(1 for f in findings if f.severity == level) for level in (OK, WARN, FAIL, SKIP)}
    print(f"\n{len(declarations)} feature(s) declared, matrix coverage {covered}/{active} active rows")
    print(f"{counts[OK]} OK, {counts[WARN]} WARN, {counts[FAIL]} FAIL, {counts[SKIP]} SKIP")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
