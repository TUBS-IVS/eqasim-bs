#!/usr/bin/env python3
"""Run ONE synpp stage against an existing run cache without writing into that cache.

Why: the isolated-run tool copies the whole cache (cp -a) before a run, and on 2026-11-27 the run
server had less free disk than the cache of popsim100_20261120 takes. The analysis stage
braunschweig.analysis.synthesis.students_vs_srv only READS the pickles of its dependencies and the completed_donor cache directory, so this
driver builds the synpp registry for the composed config exactly as the launcher does
(braunschweig.synpp_fork.registry), checks that every dependency of the target stage is cached in
the source cache under the hash the registry computes (synpp's stage hash covers the stage name and
its config; the CODE of the upstream stages is checked separately by running
scripts/explain_stale_stages.py on the same config and source cache first, which must report every
dependency as a HIT), and calls the stage's execute() through synpp's own ExecuteContext. The source cache is only opened
for reading; the stage's own cache path and its outputs go to --stage-cache-dir and output_path.
The driver does not write into the source cache by design (it passes the source only as synpp's working
directory for loading); this was verified afterwards with find -newer, see source_cache_readonly_check.txt.

    python run_stage_readonly.py --base configs/base_bs.yml --overlay RUN/run.yml \
        --source-cache <run cache> --stage-cache-dir <new dir> --stage <descriptor>

Run it from the eqasim-bs worktree of the code state to measure (cwd = repository root), with the
eqasim conda env. Exit 0 on success; a missing dependency pickle raises before execute().
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True)
    parser.add_argument("--overlay", required=True)
    parser.add_argument("--source-cache", required=True, help="existing synpp working directory (read only)")
    parser.add_argument("--stage-cache-dir", required=True, help="new directory for the stage's own cache path")
    parser.add_argument("--stage", required=True, help="descriptor of the stage to execute")
    parser.add_argument("--manifest", help="write the resolved dependency ids here (JSON)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("run_stage_readonly")
    sys.path.insert(0, os.getcwd())

    from braunschweig.synpp_fork import registry
    from synpp.pipeline import ExecuteContext

    source = Path(args.source_cache).resolve()
    own = Path(args.stage_cache_dir).resolve()
    if own == source or source in own.parents:
        raise SystemExit("--stage-cache-dir must lie outside the source cache")
    if own.exists() and any(own.iterdir()):
        raise SystemExit("--stage-cache-dir %s exists and is not empty" % own)
    own.mkdir(parents=True, exist_ok=True)

    settings = registry.load_settings(args.base, args.overlay)
    stages = registry.build_registry_from_settings(settings)
    matches = [(stage_id, stage) for stage_id, stage in stages.items()
               if stage["descriptor"] == args.stage
               or getattr(stage["descriptor"], "__name__", None) == args.stage]
    if len(matches) != 1:
        raise SystemExit("stage %s: %d registry entries, expected 1" % (args.stage, len(matches)))
    stage_id, stage = matches[0]

    missing = [dependency for dependency in stage["dependencies"]
               if not (source / ("%s.p" % dependency)).is_file()]
    logger.info("target %s; %d dependencies, missing in the source cache: %s", stage_id,
                len(stage["dependencies"]), missing)
    if missing:
        raise SystemExit("dependencies not cached under the hash of this code state: %s" % missing)
    if args.manifest:
        with open(args.manifest, "w", encoding="utf-8") as handle:
            json.dump({"stage": stage_id, "dependencies": sorted(stage["dependencies"]),
                       "aliases": stage.get("aliases", {})}, handle, indent=2, sort_keys=True)

    context = ExecuteContext(stage["config"], stage["required_stages"], stage["aliases"], str(source),
                             stage["dependencies"], str(own), {}, logger, {}, {})
    stage["wrapper"].execute(context)
    logger.info("executed %s; stage cache path %s", stage_id, own)
    return 0


if __name__ == "__main__":
    sys.exit(main())
