#!/usr/bin/env python3
"""Run a few synpp stages in order on top of an existing run cache, never writing into that cache.

Why: the run server had less free disk on 2026-11-27 than the cache copy of scripts/isolated_run.py takes
(see docs/runs/student-measurement-by-area-popsim100-20261120-20261127.yml, which used the one-stage
predecessor of this driver). Here two stages must run (the plan re-draw of the seed and the PopulationSim
stage), so the driver builds a VIEW directory: for every dependency of the requested stages that the source
cache holds (``<id>.p`` and ``<id>.cache``) it places a symlink in the view; each requested stage is then
executed through synpp's own ExecuteContext with the view as working directory, and its result is pickled
to ``<view>/<id>.p`` (its cache path is ``<view>/<id>.cache``), so the next stage loads it like synpp would.
The source cache is only read through the symlinks; nothing in this driver opens a source file for writing.

Stage hashes come from braunschweig.synpp_fork.registry for the composed config, exactly as the launcher
computes them; synpp's stage hash covers the stage name and its config. The CODE of the cached upstream
stages is checked separately by scripts/explain_stale_stages.py on the same config and source cache, which
must report every stage that is not requested here as a HIT.

    python run_stages_readonly.py --base configs/base_bs.yml --overlay RUN/run.yml \
        --source-cache <run cache> --view-dir <new dir> --stages A,B [--expect-stop ExceptionName]

``--expect-stop`` names an exception class that the LAST stage raises by design (the PopulationSim stage's
AssembleBatchesOnlyStop of the smoke mode); it then counts as success and nothing is pickled for that stage.
Run from the eqasim-bs worktree of the code state to test (cwd = repository root), eqasim conda env.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True)
    parser.add_argument("--overlay", required=True)
    parser.add_argument("--source-cache", required=True, help="existing synpp working directory (read only)")
    parser.add_argument("--view-dir", required=True, help="new, empty directory (symlinks + new stage outputs)")
    parser.add_argument("--stages", required=True, help="comma-separated descriptors, in execution order")
    parser.add_argument("--expect-stop", help="exception class name the last stage raises by design")
    parser.add_argument("--manifest", help="write the resolved stage and dependency ids here (JSON)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("run_stages_readonly")
    sys.path.insert(0, os.getcwd())

    from braunschweig.synpp_fork import registry
    from synpp.pipeline import ExecuteContext

    source = Path(args.source_cache).resolve()
    view = Path(args.view_dir).resolve()
    if view == source or source in view.parents or view in source.parents:
        raise SystemExit("--view-dir must lie outside the source cache")
    if view.exists() and any(view.iterdir()):
        raise SystemExit("--view-dir %s exists and is not empty" % view)
    view.mkdir(parents=True, exist_ok=True)

    settings = registry.load_settings(args.base, args.overlay)
    stages = registry.build_registry_from_settings(settings)
    by_descriptor = {}
    for stage_id, stage in stages.items():
        by_descriptor.setdefault(str(stage["descriptor"]), []).append(stage_id)
    order = []
    for name in [item.strip() for item in args.stages.split(",") if item.strip()]:
        ids = by_descriptor.get(name, [])
        if len(ids) != 1:
            raise SystemExit("stage %s: %d registry entries, expected 1" % (name, len(ids)))
        order.append(ids[0])

    produced = set()
    linked = []
    for stage_id in order:
        for dependency in stages[stage_id]["dependencies"]:
            if dependency in produced or (view / ("%s.p" % dependency)).exists():
                continue
            if not (source / ("%s.p" % dependency)).is_file():
                raise SystemExit("%s: dependency %s is neither requested nor cached in the source cache"
                                 % (stage_id, dependency))
            for suffix in (".p", ".cache"):
                target = source / (dependency + suffix)
                if target.exists():
                    os.symlink(target, view / (dependency + suffix), target_is_directory=target.is_dir())
            linked.append(dependency)
        produced.add(stage_id)
    logger.info("stages %s; dependencies linked read-only from the source cache: %s", order, linked)
    if args.manifest:
        with open(args.manifest, "w", encoding="utf-8") as handle:
            json.dump({"stages": order, "linked_from_source_cache": sorted(linked),
                       "dependencies": {s: sorted(stages[s]["dependencies"]) for s in order}},
                      handle, indent=2, sort_keys=True)

    for index, stage_id in enumerate(order):
        stage = stages[stage_id]
        cache_path = view / ("%s.cache" % stage_id)
        cache_path.mkdir()
        context = ExecuteContext(stage["config"], stage["required_stages"], stage["aliases"], str(view),
                                 stage["dependencies"], str(cache_path), {}, logger, {}, {})
        last = index == len(order) - 1
        try:
            result = stage["wrapper"].execute(context)
        except Exception as error:  # only the one named, designed stop of the last stage is accepted
            if last and args.expect_stop and type(error).__name__ == args.expect_stop:
                logger.info("%s stopped as designed (%s): %s", stage_id, args.expect_stop, error)
                return 0
            raise
        with open(view / ("%s.p" % stage_id), "wb") as handle:
            pickle.dump(result, handle, protocol=4)
        logger.info("executed %s; result pickled into the view", stage_id)
    if args.expect_stop:
        raise SystemExit("the last stage did not raise %s" % args.expect_stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
