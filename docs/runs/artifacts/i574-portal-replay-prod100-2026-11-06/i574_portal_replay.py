"""#574 replay: the cut-timetable stage and the portal stage of prod100_20261031 re-run with the branch code.

Read-only on the run directory. The script runs ``configure`` and ``execute`` of the GIVEN code outside synpp with
a stand-in context (the pattern of ``docs/runs/artifacts/i573-check-replay-prod100-2026-11-05/i573_check_replay.py``):

* ``context.config(key)`` reads the composed configuration (``configs/base_bs.yml`` of the given code with the run
  overlay ``run.yml``), falling back to the default the stage declared in ``configure`` (as synpp does);
* ``context.stage(name)`` loads the run's cached result of that parent (``<cache>/<name>__<hash>.p``), the hash taken
  from the run log: the parent loaded or finished last before the PORTAL stage finished in that run;
* two parents are overridden: ``matsim.runtime.eqasim`` is the jar built from the Java branch (``--jar``), because
  the run's jar has neither the schedule-cut CLI nor the new cutter options, and ``braunschweig.data.transit.cut_schedule``
  (new in the branch, absent from the run) is replayed first and handed to the portal stage as its result;
* ``context.path(name)`` is the parent's cache directory (or the override's directory); ``context.path()`` of the
  replayed stage is a fresh directory under ``--out`` (nothing is written into the run's cache).

The portal stage then chooses the pt gates on the cut timetable with the hubs of the branch configuration; its
report (``portal_trips_report.json``) and the ``[pt-router]`` / ``[portal_trips]`` log lines are the before/after
against the run's own log (same cached inputs, the run's code reproduced the log exactly in the B2 probe).
Stages after the portal stage do not run; stage results are pickled to ``--out`` only.

Replay-only stand-in (the same as ``docs/runs/artifacts/i570-portal-replay-prod100-2026-11-05/i570_portal_replay.py``):
the run's trips were built before the home-office splice carried ``is_synthetic_closure`` (commit e8678cb4), so
spliced rows hold NaN there and the portal stage of the branch refuses them. For this replay those rows take the
flag from the donor ``trip_key`` suffix ``_closure`` (the plan repair's marker, ADR-0108); the count is logged.
Production does not need this: the splice now carries the flag.

Usage on felix (eqasim env), started in the RUN's checkout so that relative data paths resolve as in the run:
  cd ~/prod100_20261031/eqasim-bs && PYTHONDONTWRITEBYTECODE=1 nice python ~/scratch_i574/i574_portal_replay.py \
      --code ~/i574_replay/eqasim-bs --cache ~/prod100_20261031/cache --run-yml ~/prod100_20261031/run.yml \
      --run-log ~/prod100_20261031/logs/run_20261031T071313.log --out ~/scratch_i574/out_m1 \
      --jar ~/i574_replay/eqasim-java-bs/braunschweig/target/braunschweig-<version>.jar
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import pickle
import re
import sys
import time
from pathlib import Path

logger = logging.getLogger("i574_portal_replay")
_LINE = re.compile(r"(Loading cache for|Finished running|Executing stage) ([\w.]+)__([0-9a-f]{32})")
_UNSET = object()
TRIPS_STAGE = "braunschweig.synthesis.commute_day.trips_day_stage"
CLOSURE = "is_synthetic_closure"
CUT_STAGE = "braunschweig.data.transit.cut_schedule"


def restore_closure_flags(trips):
    """Replay stand-in: fill the NaN closure flags of the spliced rows from the ``trip_key`` suffix (see above)."""
    import numpy as np  # noqa: PLC0415

    flag = trips[CLOSURE]
    missing = flag.isna().to_numpy()
    from_key = trips["trip_key"].astype(str).str.endswith("_closure").to_numpy()
    logger.info("replay stand-in: %d/%d trips without a closure flag (spliced home-office rows); of them %d marked "
                "closure by trip_key", int(missing.sum()), len(trips), int((missing & from_key).sum()))
    trips = trips.copy()
    trips[CLOSURE] = np.where(missing, from_key, flag.fillna(False).astype(bool)).astype(bool)
    return trips
PORTAL_STAGE = "braunschweig.synthesis.portal_trips.stage"
EQASIM_STAGE = "matsim.runtime.eqasim"


def stems_before(run_log: Path, stage: str) -> dict:
    """``{stage name: stem}`` of every stage loaded or finished in the run log before ``stage`` finished."""
    seen: dict = {}
    with open(run_log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = _LINE.search(line)
            if not match:
                continue
            kind, name, digest = match.groups()
            if kind == "Finished running" and name == stage:
                return {n: (None if flag == "ambiguous" else f"{n}__{h}") for n, (h, flag) in seen.items()} \
                    | {"__self__": f"{stage}__{digest}"}
            previous = seen.get(name)
            if previous and previous[0] != digest and name != stage:
                seen[name] = (digest, "ambiguous")
            elif not previous:
                seen[name] = (digest, "ok")
    raise ValueError(f"{run_log}: no 'Finished running {stage}__<hash>' line")


class ReplayContext:
    """The parts of the synpp context the stages use: ``config``, ``stage``, ``path``; with result overrides."""

    aliases: dict = {}

    def __init__(self, *, config: dict, stems: dict, cache: Path, out_dir: Path, overrides: dict):
        self._config, self._stems, self._cache, self._out = config, stems, cache, out_dir
        self._overrides = overrides  # name -> (result, directory)
        self._defaults: dict = {}
        self._loaded: dict = {}
        self._local_aliases: dict = {}
        self.configuring = True

    def config(self, key, default=_UNSET, **_ignored):
        if self.configuring:
            if default is not _UNSET:
                self._defaults[key] = default
            return self._config.get(key, self._defaults.get(key))
        if key in self._config:
            return self._config[key]
        if key in self._defaults:
            return self._defaults[key]
        raise KeyError(f"config key {key!r} was not declared in configure")

    def _name(self, stage) -> str:
        name = stage if isinstance(stage, str) else stage.__name__
        seen = set()
        while name in self.aliases and name not in seen:
            seen.add(name)
            name = self.aliases[name]
        return name

    def _stem(self, name: str) -> str:
        if name in self._stems:
            return self._stems[name]
        candidates = sorted(p.name[:-2] for p in self._cache.glob(f"{name}__*.p"))
        if len(candidates) != 1:
            raise KeyError(f"no unique cached stem for {name!r} (log: none; cache: {candidates})")
        logger.warning("stem of %s not in the run log before the stage; the only cached one is used: %s",
                       name, candidates[0])
        self._stems[name] = candidates[0]
        return candidates[0]

    def stage(self, stage, parameters=None, alias=None):
        if self.configuring:
            if alias is not None:
                self._local_aliases[alias] = self._name(stage)
            return None
        name = self._local_aliases.get(stage, None) if isinstance(stage, str) else None
        name = name or self._name(stage)
        if name in self._overrides:
            return self._overrides[name][0]
        if name not in self._loaded:
            t0 = time.time()
            with open(self._cache / f"{self._stem(name)}.p", "rb") as handle:
                self._loaded[name] = pickle.load(handle)
            if name == TRIPS_STAGE:
                self._loaded[name] = restore_closure_flags(self._loaded[name])
            logger.info("loaded %s (%s) in %.0f s", name, self._stem(name), time.time() - t0)
        return self._loaded[name]

    def path(self, stage=None, parameters=None):
        if stage is None:
            return str(self._out)
        name = self._local_aliases.get(stage) if isinstance(stage, str) else None
        name = name or self._name(stage)
        if name in self._overrides:
            return str(self._overrides[name][1])
        return str(self._cache / f"{self._stem(name)}.cache")


def replay(module_name: str, *, stems: dict, cache: Path, config: dict, out: Path, overrides: dict):
    module = importlib.import_module(module_name)
    stage_out = out / module_name
    stage_out.mkdir(parents=True)
    context = ReplayContext(config=config, stems=dict(stems), cache=cache, out_dir=stage_out, overrides=overrides)
    module.configure(context)
    context.configuring = False
    t0 = time.time()
    logger.info("=== replay %s ===", module_name)
    result = module.execute(context)
    seconds = time.time() - t0
    logger.info("=== %s finished in %.0f s ===", module_name, seconds)
    with open(stage_out / "result.p", "wb") as handle:
        pickle.dump(result, handle)
    return result, stage_out, {"stage": module_name, "seconds": round(seconds)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--run-yml", required=True)
    parser.add_argument("--run-log", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--jar", required=True, help="the braunschweig jar built from the Java branch")
    args = parser.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(out / "replay.log"), logging.StreamHandler()])
    code, cache = Path(args.code).expanduser(), Path(args.cache).expanduser()
    jar = Path(args.jar).expanduser().resolve()
    if not jar.is_file():
        raise FileNotFoundError(jar)
    sys.path.insert(0, str(code))
    from braunschweig.synpp_fork import registry  # noqa: PLC0415

    settings = registry.load_settings(str(code / "configs" / "base_bs.yml"), str(Path(args.run_yml).expanduser()))
    config = dict(settings.get("config", {}) or {})
    ReplayContext.aliases = dict(settings.get("aliases", {}) or {})
    logger.info("cwd %s, code %s, jar %s", Path.cwd(), code, jar)
    logger.info("transit_cut_mode=%r hubs=%r", config.get("braunschweig.cutter.transit_cut_mode"),
                config.get("braunschweig.cutter.transit_hub_stop_names"))
    stems = stems_before(Path(args.run_log).expanduser(), PORTAL_STAGE)
    run_stem = stems.pop("__self__")
    ambiguous = [n for n, s in stems.items() if s is None]
    if ambiguous:
        raise ValueError(f"ambiguous parent stems before the portal stage: {ambiguous}")
    logger.info("portal stage of the run: %s", run_stem)
    overrides = {EQASIM_STAGE: (jar.name, jar.parent)}
    summary = []
    if (code / "braunschweig" / "data" / "transit" / "cut_schedule.py").exists():
        cut_result, cut_dir, entry = replay(CUT_STAGE, stems=stems, cache=cache, config=config, out=out,
                                            overrides=overrides)
        summary.append(entry | {"n_stops_pre_cut": cut_result["n_stops_pre_cut"],
                                "n_stops_kept": cut_result["n_stops_kept"],
                                "n_hub_stops": cut_result["n_hub_stops"], "mode": cut_result["mode"]})
        overrides[CUT_STAGE] = (cut_result, cut_dir)
    else:
        # The baseline arm: code without the cut-timetable stage (main before #574) routes on the pre-cut timetable.
        logger.info("baseline arm: %s has no %s; the portal stage runs as on main", code, CUT_STAGE)
    _, _, entry = replay(PORTAL_STAGE, stems=stems, cache=cache, config=config, out=out, overrides=overrides)
    summary.append(entry | {"run_stem": run_stem})
    with open(out / "summary.json", "w") as handle:
        json.dump(summary, handle, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
