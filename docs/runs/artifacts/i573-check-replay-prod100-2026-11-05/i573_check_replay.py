"""#573 replay: the timetable-check stages of prod100_20261031 re-run with the branch code on the run's cached inputs.

Read-only on the run directory. For each given synpp stage module, the script runs ``configure`` and ``execute`` of
the GIVEN code outside synpp with a minimal stand-in context:

* ``context.config(key)`` reads the composed configuration (``configs/base_bs.yml`` of the given code with the run
  overlay ``run.yml``), falling back to the default the stage declared in ``configure`` (as synpp does);
* ``context.stage(name)`` loads the run's cached result of that parent (``<cache>/<name>__<hash>.p``), the hash taken
  from the run log: the parent loaded or finished last before the stage finished in that run;
* ``context.path(name)`` is the parent's cache directory; ``context.path()`` of the replayed stage is a fresh
  directory under ``--out`` (nothing is written into the run's cache).

Each stage runs once with the branch default (``braunschweig.transit_connections.latest_arrival_check`` true). The
per-call log lines of ``connection_mask`` carry the count ``late arrival N``: the tasks the bound rejects that the
old rule accepted, so the FIRST call of each stage (every checked task) is a direct before/after of the rule.
Stages after the replayed ones do not run; stage results are pickled to ``--out`` only.

Usage on felix (eqasim env):
  PYTHONDONTWRITEBYTECODE=1 nice python i573_check_replay.py --code ~/i573_replay/eqasim-bs \
      --cache ~/prod100_20261031/cache --run-yml ~/prod100_20261031/run.yml \
      --run-log ~/prod100_20261031/logs/run_20261031T071313.log --out ~/scratch_i573/out \
      --stage braunschweig.synthesis.incommuters --stage braunschweig.synthesis.student_incommuters \
      --stage braunschweig.synthesis.portal_trips.stage --stage braunschweig.synthesis.outside_tour_modes.stage
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

logger = logging.getLogger("i573_check_replay")
_LINE = re.compile(r"(Loading cache for|Finished running|Executing stage) ([\w.]+)__([0-9a-f]{32})")
_UNSET = object()


def stems_before(run_log: Path, stage: str) -> dict:
    """``{stage name: stem}`` of every stage loaded or finished in the run log before ``stage`` finished.

    A name seen with two hashes before that point raises: the replay would not know which variant the stage read.
    """
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
    """The parts of the synpp context the check stages use: ``config``, ``stage``, ``path``."""

    def __init__(self, *, config: dict, stems: dict, cache: Path, out_dir: Path):
        self._config, self._stems, self._cache, self._out = config, stems, cache, out_dir
        self._defaults: dict = {}
        self._loaded: dict = {}
        self._local_aliases: dict = {}
        self.configuring = True

    def config(self, key, default=_UNSET, **_ignored):
        if self.configuring:
            if default is not _UNSET:
                self._defaults[key] = default
            # synpp: a key declared earlier with a default resolves to it in later configure calls too.
            return self._config.get(key, self._defaults.get(key))
        if key in self._config:
            return self._config[key]
        if key in self._defaults:
            return self._defaults[key]
        raise KeyError(f"config key {key!r} was not declared in configure")

    aliases: dict = {}

    def _name(self, stage) -> str:
        """The executed module behind ``stage``: synpp aliases (settings ``aliases``) name their target's cache."""
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
            # configure(... alias="hts") makes execute read the parent as context.stage("hts").
            if alias is not None:
                self._local_aliases[alias] = self._name(stage)
            return None
        name = self._local_aliases.get(stage, None) if isinstance(stage, str) else None
        name = name or self._name(stage)
        if name not in self._loaded:
            t0 = time.time()
            with open(self._cache / f"{self._stem(name)}.p", "rb") as handle:
                self._loaded[name] = pickle.load(handle)
            logger.info("loaded %s (%s) in %.0f s", name, self._stem(name), time.time() - t0)
        return self._loaded[name]

    def path(self, stage=None, parameters=None):
        if stage is None:
            return str(self._out)
        name = self._local_aliases.get(stage) if isinstance(stage, str) else None
        return str(self._cache / f"{self._stem(name or self._name(stage))}.cache")


def replay(module_name: str, *, code: Path, cache: Path, run_log: Path, config: dict, out: Path) -> dict:
    module = importlib.import_module(module_name)
    stems = stems_before(run_log, module_name)
    run_stem = stems.pop("__self__")
    ambiguous = [n for n, s in stems.items() if s is None]
    if ambiguous:
        raise ValueError(f"{module_name}: ambiguous parent stems {ambiguous}")
    stage_out = out / module_name
    stage_out.mkdir(parents=True)
    context = ReplayContext(config=config, stems=stems, cache=cache, out_dir=stage_out)
    module.configure(context)
    context.configuring = False
    t0 = time.time()
    logger.info("=== replay %s (run stem %s) ===", module_name, run_stem)
    result = module.execute(context)
    seconds = time.time() - t0
    logger.info("=== %s finished in %.0f s ===", module_name, seconds)
    with open(stage_out / "result.p", "wb") as handle:
        pickle.dump(result, handle)
    return {"stage": module_name, "run_stem": run_stem, "seconds": round(seconds)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--run-yml", required=True)
    parser.add_argument("--run-log", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--stage", action="append", required=True)
    args = parser.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(out / "replay.log"), logging.StreamHandler()])
    code, cache = Path(args.code).expanduser(), Path(args.cache).expanduser()
    sys.path.insert(0, str(code))
    from braunschweig.synpp_fork import registry  # noqa: PLC0415

    settings = registry.load_settings(str(code / "configs" / "base_bs.yml"), str(Path(args.run_yml).expanduser()))
    config = dict(settings.get("config", {}) or {})
    ReplayContext.aliases = dict(settings.get("aliases", {}) or {})
    logger.info("aliases %d", len(ReplayContext.aliases))
    # Relative data paths resolve from the working directory: start the script in the RUN's checkout (its gitignored
    # eqasim-data is what the run read), while --code puts the branch code first on sys.path.
    logger.info("cwd %s, code %s", Path.cwd(), code)
    logger.info("config keys %d; latest_arrival_check=%r", len(config),
                config.get("braunschweig.transit_connections.latest_arrival_check"))
    summary = []
    for name in args.stage:
        summary.append(replay(name, code=code, cache=cache, run_log=Path(args.run_log).expanduser(), config=config,
                              out=out))
        with open(out / "summary.json", "w") as handle:
            json.dump(summary, handle, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
