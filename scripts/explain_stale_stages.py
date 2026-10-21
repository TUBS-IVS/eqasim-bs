"""Explain which synpp stages a run would recompute, and why, before launching it.

    python scripts/explain_stale_stages.py configs/base_bs.yml configs/overlays/test_100pct.yml \
        [--working-directory <dir>] [--expect stage[,stage...]|@file] [--json out.json]

Read-only. Exit 0: every stage stale for its own reasons is in --expect (run targets, which synpp
re-executes on every run, never count as unexpected); exit 2: at least one is not; exit 1: error.
Stages marked STALE-IF are valid now and would be reused by the fork's early cutoff if the named
ancestor's output is unchanged; synpp 1.6.2 recomputes them (see braunschweig/synpp_fork/staleness.py,
EARLY_CUTOFF_ACTIVE). Stages marked RECOMPUTE have a stale ancestor with no recorded output hash and
will recompute for certain. Uncached ephemeral stages are marked EPHEMERAL and never count as
unexpected. --expect is a gate only when given: `--expect ""` means NOTHING may be stale
(besides run targets), whereas the launcher key `synpp_expected_stale_stages: []` means
"no gate" (scripts/run_synpp.py, explain_before_run).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def parse_expect(value: str | None) -> set:
    if not value:
        return set()
    if value.startswith("@"):
        with open(value[1:], encoding="utf-8") as handle:
            return {line.strip() for line in handle if line.strip() and not line.startswith("#")}
    return {item.strip() for item in value.split(",") if item.strip()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base")
    parser.add_argument("overlay", nargs="?")
    parser.add_argument("--working-directory")
    parser.add_argument(
        "--expect",
        help="stage names allowed to be stale, comma-separated or @file. Given but empty "
             '(--expect "") means NOTHING may be stale (besides run targets); this differs from the '
             "launcher key synpp_expected_stale_stages: [], which means no gate.")
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args(argv)

    from braunschweig.synpp_fork import metadata, registry, staleness
    settings = registry.load_settings(args.base, args.overlay)
    working_directory = args.working_directory or settings.get("working_directory")
    if not working_directory:
        print("error: no working_directory in the config and none given", file=sys.stderr)
        return 1
    stages = registry.build_registry_from_settings(settings)
    meta = metadata.read_meta(working_directory)
    assessments = staleness.assess(stages, working_directory, meta)
    print(staleness.format_report(assessments))
    if args.json_out:
        payload = {a.stage: {"hash": a.hash, "reasons": a.reasons, "legacy_rules": a.legacy_rules,
                             "stale_if": a.stale_if, "certain_recompute": a.certain_recompute,
                             "unhashed_ancestors": a.unhashed_ancestors,
                             "stale_if_hashes": a.stale_if_hashes,
                             "unhashed_ancestor_hashes": a.unhashed_ancestor_hashes,
                             "ephemeral_uncached": a.ephemeral_uncached,
                             "config_diff": {k: list(v) for k, v in a.config_diff.items()}}
                   for a in assessments.values()}
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
    unexpected = staleness.unexpected(assessments, parse_expect(args.expect))
    if args.expect is not None and unexpected:
        print("# UNEXPECTED stale stages: " + ", ".join(unexpected))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
