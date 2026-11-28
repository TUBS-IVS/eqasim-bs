#!/usr/bin/env python3
"""Run a CHAIN of synpp stages against an existing run cache without writing into that cache.

Extension of the single-stage driver of the Phase 3 measurement
(docs/runs/artifacts/student-measurement-by-area-popsim100-20261120-20261127/scripts/run_stage_readonly.py),
written for Phase 4 of the student layer (issue #140), because the run server has less free disk than a
full copy of the popsim100_20261120 cache takes and Phase 4 changes several stages that depend on each other.

How it works:
- The registry is built for the composed configuration exactly as the launcher does
  (``braunschweig.synpp_fork.registry``).
- A new working directory (``--work-dir``, outside the source cache) gets one SYMLINK per dependency entry
  that the chain needs and that is not produced by the chain itself (``<id>.p`` and, where present,
  ``<id>.cache``). The symlinks are only ever opened for reading: synpp's ``ExecuteContext.stage`` reads
  ``<working>/<id>.p``, and a stage's own outputs go to its own new ``<id>.cache`` directory and ``<id>.p``,
  whose names carry the new hash of this code state, so no write lands on a link.
- The stages of ``--stages`` run in the given order; each result is pickled to ``<work-dir>/<id>.p`` (as
  synpp does), so the next stage of the chain loads it from there.
- A dependency that is neither produced by the chain nor cached in the source under the hash this code
  state computes raises before anything runs. Whether the CODE of the cached upstream stages matches is
  checked separately with ``scripts/explain_stale_stages.py`` on the same configuration and source cache,
  which must report every non-chain dependency as a HIT.

    python run_stages_readonly.py --base configs/base_bs.yml --overlay RUN/run.yml \
        --source-cache <run cache> --work-dir <new dir> --stages <descriptor> [<descriptor> ...] \
        --manifest <json>

Run from the eqasim-bs worktree of the code state to measure (cwd = repository root), with the eqasim conda
env. Exit 0 on success.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
import time
from pathlib import Path


def _find(stages: dict, descriptor: str):
    matches = [(stage_id, stage) for stage_id, stage in stages.items()
               if stage["descriptor"] == descriptor
               or getattr(stage["descriptor"], "__name__", None) == descriptor]
    if len(matches) != 1:
        raise SystemExit("stage %s: %d registry entries, expected 1" % (descriptor, len(matches)))
    return matches[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True)
    parser.add_argument("--overlay", required=True)
    parser.add_argument("--source-cache", required=True, help="existing synpp working directory (read only)")
    parser.add_argument("--work-dir", required=True, help="new, empty working directory for the chain")
    parser.add_argument("--stages", required=True, nargs="+", help="descriptors, executed in this order")
    parser.add_argument("--manifest", required=True, help="write stage ids, links and timings here (JSON)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("run_stages_readonly")
    sys.path.insert(0, os.getcwd())

    from braunschweig.synpp_fork import registry
    from synpp.pipeline import ExecuteContext

    source = Path(args.source_cache).resolve()
    work = Path(args.work_dir).resolve()
    if work == source or source in work.parents or work in source.parents:
        raise SystemExit("--work-dir must lie outside the source cache")
    if work.exists() and any(work.iterdir()):
        raise SystemExit("--work-dir %s exists and is not empty" % work)
    work.mkdir(parents=True, exist_ok=True)

    settings = registry.load_settings(args.base, args.overlay)
    stages = registry.build_registry_from_settings(settings)
    chain = [_find(stages, descriptor) for descriptor in args.stages]
    produced = {stage_id for stage_id, _ in chain}

    needed = sorted({dependency for _, stage in chain for dependency in stage["dependencies"]} - produced)
    missing = [dependency for dependency in needed if not (source / ("%s.p" % dependency)).is_file()]
    if missing:
        raise SystemExit("dependencies not cached under the hash of this code state: %s" % missing)
    links = []
    for dependency in needed:
        for suffix in (".p", ".cache"):
            target = source / (dependency + suffix)
            if target.exists():
                os.symlink(target, work / (dependency + suffix), target_is_directory=target.is_dir())
                links.append(dependency + suffix)
    logger.info("chain %s; %d dependency entries linked read-only from %s", [s for s, _ in chain], len(links),
                source)

    report = {"source_cache": "<source cache>", "chain": [], "links": links}
    for stage_id, stage in chain:
        started = time.time()
        own = work / ("%s.cache" % stage_id)
        own.mkdir()
        context = ExecuteContext(stage["config"], stage["required_stages"], stage["aliases"], str(work),
                                 stage["dependencies"], str(own), {}, logger, {}, {})
        result = stage["wrapper"].execute(context)
        with open(work / ("%s.p" % stage_id), "wb") as handle:
            pickle.dump(result, handle, protocol=pickle.HIGHEST_PROTOCOL)
        seconds = round(time.time() - started, 1)
        logger.info("executed %s in %.1f s", stage_id, seconds)
        report["chain"].append({"stage": stage_id, "dependencies": sorted(stage["dependencies"]),
                                "seconds": seconds})
    with open(args.manifest, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
