"""#678 B9 step 2: replay of the prepare steps of the branch on a COPY of the prepared population of prod100_20261031.

Runs, in the order of ``braunschweig.matsim.simulation.prepare.execute`` and with the code of the given clone:
the jar check (``mode_choice_reach.require_border_vehicle_constraint``), ``mode_choice_reach.apply`` with the loop
settings, ``initial_mode_choice.run`` with ``reach=initial_settings``, ``frozen_pt_walk_lift.run`` and
``plan_check.run`` with the lift report. synpp is replaced by ``StandInContext`` (config(), path(), stage()):
``config`` returns the values of the clone's ``configs/base_bs.yml`` (the production values, branch defaults of #678
included) with three operational overrides (java binary, heap, threads), ``path()`` is the stage directory, and the
runtime stage ``matsim.runtime.eqasim`` resolves to the given jar.

The stage directory holds a COPY of the run's final config and of its prepared population (both replaced in place by
the steps) and symbolic links to the other files of the run's prepare stage directory (read-only). The population
after the initial mode choice is kept as a hard link (``snapshots/after_initial_mode_choice.xml.gz``) before the lift
replaces the stage file (``os.replace`` never writes into the old inode).

Excluded subpopulations: the list of the branch's ``prepare._initial_mode_choice_excluded_subpopulations`` for
the stand-in context (cordon_enabled and freight_enabled of base_bs.yml): the in-commuter subpopulations and freight
(the freight fix of commit 884627f0). prod100_20261031 predates #566: its in-commuters carry no subpopulation
attribute, so the in-commuter names match nobody (the runner warns per name) and the in-commuters are re-chosen here,
unlike in a post-#566 prepare; freight matches its 56,364 persons (stated in the manifest).

Usage: python i678_reach_replay.py <eqasim-bs clone> <stage dir> <jar> <java binary> <threads> <Xmx>
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

clone, stage_dir, jar, java_binary, threads, memory = sys.argv[1:7]
sys.path.append(clone)  # append, never insert at 0: tests/ would shadow braunschweig
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

import yaml  # noqa: E402

from braunschweig.matsim import initial_mode_choice  # noqa: E402
from braunschweig.matsim.simulation import frozen_pt_walk_lift, mode_choice_reach, plan_check  # noqa: E402
from braunschweig.matsim.simulation import prepare  # noqa: E402

CONFIG_NAME = "braunschweig_100pct_prod20261031_config.xml"
logger = logging.getLogger("i678_reach_replay")


class StandInContext:
    """The three synpp context calls the four steps make (config, path, stage), without synpp."""

    def __init__(self, values: dict, stage_path: str, jar_path: str):
        self._values = values
        self._stage_path = stage_path
        self._jar_dir, self._jar_name = os.path.split(jar_path)

    def config(self, key):
        # One argument, as synpp's ExecuteContext.config: a key the YAML lacks raises instead of taking a default.
        if key in self._values:
            return self._values[key]
        raise KeyError(f"stand-in context: config key {key!r} is not in base_bs.yml or the overrides")

    def path(self, stage=None):
        if stage is None:
            return self._stage_path
        if stage == "matsim.runtime.eqasim":
            return self._jar_dir
        raise KeyError(f"stand-in context: no path for stage {stage!r}")

    def stage(self, name):
        if name == "matsim.runtime.eqasim":
            return self._jar_name
        if name == "matsim.runtime.java":
            return None
        raise KeyError(f"stand-in context: no stage {name!r}")


def main() -> int:
    stage = Path(stage_dir)
    values = yaml.safe_load(open(os.path.join(clone, "configs", "base_bs.yml"), encoding="utf-8"))["config"]
    values.update({"java_binary": java_binary, "java_memory": memory, "matsim_threads": int(threads)})
    context = StandInContext(values, str(stage), jar)
    config_path = stage / CONFIG_NAME
    population_path = Path(plan_check.resolve_population_path(str(config_path)))
    snapshots = stage.parent / "snapshots"
    snapshots.mkdir(exist_ok=True)
    loop = mode_choice_reach.loop_settings(context)
    initial = mode_choice_reach.initial_settings(context)
    summary = {"clone": clone, "jar": jar, "threads": int(threads), "xmx": memory,
               "loop_settings": vars(loop), "initial_settings": vars(initial),
               "lift_all_modes": values[frozen_pt_walk_lift.KEY_ALL_MODES],
               "max_share_other_modes": values[frozen_pt_walk_lift.KEY_MAX_SHARE_OTHER_MODES],
               "excluded_subpopulations": None, "steps": {}}

    def record(name, started, **extra):
        persons = initial_mode_choice.count_persons(population_path)
        summary["steps"][name] = dict(extra, seconds=round(time.monotonic() - started, 1), persons_after=persons)
        logger.info("[replay] step %s done in %.0f s; persons in the population file: %d", name,
                    time.monotonic() - started, persons)
        (stage.parent / "replay_summary.json").write_text(json.dumps(summary, indent=2, default=str), "utf-8")

    summary["persons_input"] = initial_mode_choice.count_persons(population_path)
    try:
        t = time.monotonic()
        if loop.isolate_outside_trips or (values[initial_mode_choice.KEY_ENABLED] and initial.isolate_outside_trips):
            mode_choice_reach.require_border_vehicle_constraint(jar)
        record("jar_check", t)
        t = time.monotonic()
        changes = mode_choice_reach.apply(config_path, loop)
        record("mode_choice_reach_apply", t, changes=changes)
        t = time.monotonic()
        excluded = prepare._initial_mode_choice_excluded_subpopulations(context)
        summary["excluded_subpopulations"] = list(excluded)
        report = initial_mode_choice.run(context, CONFIG_NAME, excluded, reach=initial)
        os.link(population_path, snapshots / "after_initial_mode_choice.xml.gz")
        record("initial_mode_choice", t, report=report)
        t = time.monotonic()
        lift_report = frozen_pt_walk_lift.run(context, CONFIG_NAME)
        record("frozen_pt_walk_lift", t, report=lift_report)
        t = time.monotonic()
        check = plan_check.run(context, CONFIG_NAME, lift_report=lift_report)
        record("plan_check", t, threshold=check["threshold"], reach=check["reach"])
    except Exception as error:  # recorded and re-raised: the summary names the failing step
        summary["error"] = "".join(traceback.format_exception(error))
        (stage.parent / "replay_summary.json").write_text(json.dumps(summary, indent=2, default=str), "utf-8")
        raise
    (stage.parent / "REPLAY_DONE").write_text("ok\n", "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
