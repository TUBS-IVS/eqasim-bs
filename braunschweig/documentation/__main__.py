"""CLI for the model registry and documentation system.

    python -m braunschweig.documentation check [--quiet] [--no-dag]
    python -m braunschweig.documentation build
    python -m braunschweig.documentation dag [pipeline ...]

``check`` resolves every declared pointer (registries, ADRs, run manifests,
resolved production config, DAG snapshots, README references) and exits 1 when
any finding is FAIL; WARN and SKIP never fail the command -- an honest "not
measured yet" must stay reportable. ``build`` regenerates docs/generated/*.
``dag`` re-extracts the synpp DAG snapshots (requires the scientific env).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

#: Repository root: this file is <root>/braunschweig/documentation/__main__.py
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m braunschweig.documentation",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "build", "dag"))
    parser.add_argument("pipelines", nargs="*",
                        help="dag only: subset of pipeline snapshots to re-extract")
    parser.add_argument("--repo-root", default=REPO_ROOT)
    parser.add_argument("--quiet", action="store_true",
                        help="check: print only WARN and FAIL findings")
    parser.add_argument("--no-dag", action="store_true",
                        help="check: skip the DAG-freshness re-extraction (CI mode)")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Registry notes legitimately quote Unicode from the historical documents;
    # Windows consoles default to a legacy code page that cannot print them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    if args.command == "dag":
        from braunschweig.documentation import dag
        written = dag.write_snapshots(args.repo_root, args.pipelines or None)
        print(f"extracted {len(written)} DAG snapshot(s)")
        return 0

    from braunschweig.documentation.checks import (FAIL, OK, SKIP, WARN,
                                                   CheckContext, run_all_checks)

    context = CheckContext(args.repo_root, use_dag_extraction=not args.no_dag)

    if args.command == "build":
        from braunschweig.documentation import render
        written = render.write_all(context, args.repo_root)
        for path in written:
            print(f"wrote {path}")
        return 0

    findings = run_all_checks(context)
    for finding in findings:
        if args.quiet and finding.severity in (OK, SKIP):
            continue
        print(finding)
    counts = {level: sum(1 for f in findings if f.severity == level)
              for level in (OK, WARN, FAIL, SKIP)}
    print(f"\n{len(context.features)} features, {len(context.stages)} stages, "
          f"{len(context.datasets)} datasets, {len(context.adrs)} ADRs, "
          f"{len(context.manifests)} run manifests")
    print(f"{counts[OK]} OK, {counts[WARN]} WARN, {counts[FAIL]} FAIL, "
          f"{counts[SKIP]} SKIP")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
