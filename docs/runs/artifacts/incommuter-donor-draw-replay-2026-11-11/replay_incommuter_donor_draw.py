"""Replay of the two in-commuter stages with the code of the main branch (old) and of the branch (new).

Measures what the donor-household draw of ADR-0191 changes in ``braunschweig.synthesis.incommuters`` and
``braunschweig.synthesis.student_incommuters``. Both stages run OUTSIDE synpp, on the cache of an existing production
run, once per code version. No MATSim run, no population synthesis: a stage replay, not a validation.

How it works
------------
* Each arm (``old``, ``new``) runs in its OWN Python subprocess (this script re-invokes itself with the hidden
  ``--arm`` and ``--code`` arguments), so the two code versions never share an interpreter. The code directory is put
  first on ``sys.path`` and the script checks that ``braunschweig`` was imported from it.
* A minimal stand-in for the synpp context supplies ``config`` (``configs/base_bs.yml`` of the code plus the run's
  ``run.yml``, defaults declared in ``configure`` as synpp does), ``stage`` (unpickles the run's cached result,
  ``<cache>/<stage>__<hash>.p``, the hash taken from the run log) and ``path``. Read-only on the cache and the run.
* The pt timetable check is SWITCHED OFF in both arms (config key
  ``braunschweig.incommuters.pt_connection_check.enabled: false``): it needs the matsim runtime and the pre-cut
  supply, which the replay does not rebuild. This is stated in ``summary.json`` and in the printed output. Everything
  else is the run's configuration.
* Old arm: every stage input is unpickled from the cache. New arm: the same, EXCEPT ``braunschweig.data.inkar.
  household_income`` and ``braunschweig.data.hts.mid_donor``, whose outputs changed on the branch: these two are
  executed with the new code through the same stand-in (they read their files from the run's data paths) and their
  results feed both in-commuter stages. A stage the code declares that has no pickle in the cache stops the arm with a
  message naming the stage.

Identity check (old arm, before anything is measured)
-----------------------------------------------------
The old arm cannot equal the cached stage output everywhere, because the cached run had the pt check ON: it moves
days, switches pt agents to car, re-draws stations and re-balances (which consumes random numbers before the fleet
draw), so modes, times, locations and vehicles can differ. What the pt check never touches is compared exactly:

1. the number of agents and the set of person ids (any difference: exit code 2);
2. the origin Kreis per agent where the frame carries it (worker: ``validation.ars5``, student:
   ``persons.orig_ars5``);
3. the donor id (``hts_id``) of every agent whose mode in the cached output equals its mode in the replay: the share
   with identical donors must be at least 99 %, else exit code 2;
4. STRONGER than the brief's minimum: every other column that both ``persons`` frames carry, except the two that
   follow the final mode (``car_availability``, ``number_of_cars``), over ALL agents, each at least 99 % identical.
   The donor draw runs before the pt check and the household columns derive from the donor only, so these columns
   must match exactly. The share per column is reported.

Not compared (informational only): trips, activities, locations, vehicles. The share of mode-equal agents with equal
first-trip times is reported. Limit of the check: it proves the old code reproduces the donors, origins and persons
of the cached run, not the times, modes, locations or fleet of agents the pt check changed.

Output
------
``<out>/summary.json`` (every figure with its unit; code commits, cache path as given, seed, UTC time),
``<out>/arm_old.json``, ``<out>/arm_new.json``, ``<out>/arm_old.log``, ``<out>/arm_new.log`` and a printed table.
Exit code 2 when the identity check fails (the new arm is then not run).

Usage (eqasim env), started in the checkout of the run, the directory holding its ``eqasim-data``, because relative
data paths of the run configuration resolve from the working directory; the code directories are two checkouts
(``git worktree`` or a bundle) of the main branch and of the branch:

  PYTHONDONTWRITEBYTECODE=1 nice python replay_incommuter_donor_draw.py \\
      --code-old <checkout of main> --code-new <checkout of the branch> \\
      --cache <run>/cache --run-yml <run>/run.yml --run-log <run>/logs/<run log file> \\
      --out <empty scratch dir>/out
  (repeat ``--stage <module>`` to replay one stage only; default: both in-commuter stages)

``python replay_incommuter_donor_draw.py --self-test`` runs the summary and identity functions on small synthetic
frames and needs no data.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import pickle
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("replay_incommuter_donor_draw")

NOT_AVAILABLE = "not available"
PT_CHECK_KEY = "braunschweig.incommuters.pt_connection_check.enabled"
PT_CHECK_STATEMENT = (f"pt timetable check SWITCHED OFF in both arms ({PT_CHECK_KEY}: false); the cached run had it "
                      f"on, so modes, times, stations and fleet of the replay are not those of the cached run")
DEFAULT_STAGES = ("braunschweig.synthesis.incommuters", "braunschweig.synthesis.student_incommuters")
#: Stages whose output changed on the branch: the new arm executes them instead of unpickling them.
RECOMPUTED_STAGES = ("braunschweig.data.inkar.household_income", "braunschweig.data.hts.mid_donor")
LOGGER_NAMES = ("braunschweig.synthesis.incommuters", "braunschweig.synthesis.student_incommuters",
                "braunschweig.data.cordon.donor_draw", "braunschweig.data.hts.mid_donor")
#: Persons columns that follow the FINAL mode, which the pt check can change: not part of the identity check.
MODE_DEPENDENT_PERSON_COLUMNS = ("car_availability", "number_of_cars")
#: Smallest share of agents (a fraction) that must agree in the identity check. ASSUMPTION: the bound of the brief.
MIN_SHARE_IDENTICAL = 0.99
EXIT_IDENTITY_FAILED = 2
QUANTILES = (10, 50, 90)
IDENTITY_LIMIT = ("The cached run had the pt check on; the replay has it off. Compared exactly: agent count, person "
                  "ids, origin Kreis, donor ids of agents with the same mode, and every persons column except the "
                  "mode-dependent ones. NOT compared: trips, activities, locations, vehicles of agents the pt check "
                  "changed (mode, day shift, station), and the fleet draw (random numbers consumed by the "
                  "re-balancing).")

_LINE = re.compile(r"(Loading cache for|Finished running|Executing stage) ([\w.]+)__([0-9a-f]{32})")
_UNSET = object()


class ReplayInputError(RuntimeError):
    """A stage input of the replay is missing from the cache (the message names the stage)."""


# --------------------------------------------------------------------------------------------------------------
# Stand-in for the synpp context
# --------------------------------------------------------------------------------------------------------------

def stems_before(run_log: Path, stage: str) -> dict:
    """``{stage name: stem}`` of every stage loaded or finished in the run log before ``stage`` finished or was loaded.

    The stem of the stage itself is returned under ``"__self__"``. A name seen with two hashes before that point
    maps to ``None`` (ambiguous): the replay would not know which variant the stage read.
    """
    seen: dict = {}
    with open(run_log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = _LINE.search(line)
            if not match:
                continue
            kind, name, digest = match.groups()
            if kind in ("Finished running", "Loading cache for") and name == stage:
                stems = {n: (None if flag == "ambiguous" else f"{n}__{h}") for n, (h, flag) in seen.items()}
                stems["__self__"] = f"{stage}__{digest}"
                return stems
            previous = seen.get(name)
            if previous and previous[0] != digest and name != stage:
                seen[name] = (digest, "ambiguous")
            elif not previous:
                seen[name] = (digest, "ok")
    raise ValueError(f"{run_log}: no 'Finished running {stage}__<hash>' or 'Loading cache for {stage}__<hash>' line")


class ReplayContext:
    """The parts of the synpp context the stages use: ``config``, ``stage``, ``path``.

    ``overrides`` maps a stage name to a result the stand-in returns instead of unpickling the cache entry.
    """

    def __init__(self, *, config: dict, stems: dict, cache: Path, out_dir: Path, aliases: dict | None = None,
                 overrides: dict | None = None):
        self._config, self._stems, self._cache, self._out = config, stems, cache, out_dir
        self._aliases = dict(aliases or {})
        self._overrides = dict(overrides or {})
        self._defaults: dict = {}
        self._loaded: dict = {}
        self._local_aliases: dict = {}
        self.declared: list = []
        self.stems_from_log = 0
        self.stems_from_cache_listing = 0
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

    def _name(self, stage) -> str:
        """The executed module behind ``stage``: synpp aliases (settings ``aliases``) name their target's cache."""
        name = stage if isinstance(stage, str) else stage.__name__
        seen = set()
        while name in self._aliases and name not in seen:
            seen.add(name)
            name = self._aliases[name]
        return name

    def _stem(self, name: str) -> str:
        if self._stems.get(name):
            self.stems_from_log += 1
            return self._stems[name]
        candidates = sorted(p.name[:-2] for p in self._cache.glob(f"{name}__*.p"))
        if len(candidates) != 1:
            raise ReplayInputError(
                f"stage {name!r} has no unique pickle in the cache {self._cache} (not named unambiguously by the run "
                f"log; cached variants: {candidates or 'none'}): the replay cannot read its result")
        logger.warning("stem of %s not named by the run log before the stage; the only cached one is used: %s",
                       name, candidates[0])
        self.stems_from_cache_listing += 1
        self._stems[name] = candidates[0]
        return candidates[0]

    def stem_report(self) -> str:
        total = self.stems_from_log + self.stems_from_cache_listing
        if not total:
            return "[replay] stage inputs: none read"
        return (f"[replay] stage input stems: primary (run log) {self.stems_from_log}/{total} "
                f"({100.0 * self.stems_from_log / total:.0f}%), fallback (only cached variant) "
                f"{self.stems_from_cache_listing} ({100.0 * self.stems_from_cache_listing / total:.0f}%)")

    def stage(self, stage, parameters=None, alias=None):
        if self.configuring:
            # configure(... alias="hts") makes execute read the parent as context.stage("hts").
            name = self._name(stage)
            if alias is not None:
                self._local_aliases[alias] = name
            if name not in self.declared:
                self.declared.append(name)
            return None
        name = self._local_aliases.get(stage) if isinstance(stage, str) else None
        name = name or self._name(stage)
        if name in self._overrides:
            return self._overrides[name]
        if name not in self._loaded:
            stem = self._stem(name)
            path = self._cache / f"{stem}.p"
            if not path.is_file():
                raise ReplayInputError(f"stage {name!r}: the cache entry {path} does not exist")
            t0 = time.time()
            with open(path, "rb") as handle:
                self._loaded[name] = pickle.load(handle)
            logger.info("loaded %s (%s) in %.0f s", name, stem, time.time() - t0)
        return self._loaded[name]

    def path(self, stage=None, parameters=None):
        if stage is None:
            return str(self._out)
        name = self._local_aliases.get(stage) if isinstance(stage, str) else None
        return str(self._cache / f"{self._stem(name or self._name(stage))}.cache")

    def check_declared_inputs(self, module_name: str) -> None:
        """Raise ``ReplayInputError`` naming every declared stage that has no pickle in the cache."""
        missing = []
        for name in self.declared:
            if name in self._overrides:
                continue
            try:
                stem = self._stem(name)
            except ReplayInputError as exc:
                missing.append(str(exc))
                continue
            if not (self._cache / f"{stem}.p").is_file():
                missing.append(f"stage {name!r}: the cache entry {self._cache / (stem + '.p')} does not exist")
        if missing:
            raise ReplayInputError(f"{module_name} declares stages the cache cannot serve: " + "; ".join(missing))


# --------------------------------------------------------------------------------------------------------------
# Summary of one stage's frames
# --------------------------------------------------------------------------------------------------------------

def jsonable(obj):
    """``obj`` with numpy and pandas values turned into JSON types (NaN becomes null)."""
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, pd.Series):
        return jsonable(obj.to_dict())
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def first_trips(trips: pd.DataFrame) -> pd.DataFrame:
    """The earliest trip of every person (the home -> first activity trip), indexed by ``person_id``."""
    ordered = trips.sort_values(["person_id", "departure_time"], kind="mergesort")
    return ordered.drop_duplicates("person_id", keep="first").set_index("person_id")


def _shares_pct(series: pd.Series) -> tuple:
    """``({category: share in percent of the non-missing values}, number of missing values)``."""
    valid = series.dropna()
    counts = valid.value_counts()
    shares = {str(key): 100.0 * float(count) / len(valid) for key, count in counts.items()} if len(valid) else {}
    return dict(sorted(shares.items())), int(series.isna().sum())


def summarise_stage(frames: dict, donor_persons: pd.DataFrame | None = None) -> dict:
    """The measurement of one stage result: ``{"metrics": {name: {"value", "unit"}}, "reports": {...}}``.

    ``donor_persons`` (the MiD donor persons of the arm) lets the economic status of the donors be looked up through
    ``hts_id`` when the persons frame carries no status column (the new code builds the fleet from it).
    """
    metrics: dict = {}
    reports = {key: jsonable(frames.get(key)) if frames.get(key) is not None else NOT_AVAILABLE
               for key in ("donor_draw", "day_bound")}

    def put(name, value, unit):
        metrics[name] = {"value": jsonable(value), "unit": unit}

    def put_distribution(name, values, unit):
        arr = np.asarray(values, dtype=float)
        arr = arr[np.isfinite(arr)]
        put(f"{name}.n_finite", int(len(arr)), "agents")
        if not len(arr):
            for label in ("mean",) + tuple(f"p{q}" for q in QUANTILES):
                put(f"{name}.{label}", NOT_AVAILABLE, unit)
            return
        put(f"{name}.mean", float(arr.mean()), unit)
        for q in QUANTILES:
            put(f"{name}.p{q}", float(np.percentile(arr, q)), unit)

    def put_shares(name, series, unit="%"):
        shares, n_missing = _shares_pct(series)
        for key, value in shares.items():
            put(f"{name}.{key}", value, unit)
        put(f"{name}.n_missing", n_missing, "agents")

    persons = frames.get("persons")
    n = 0 if persons is None else len(persons)
    put("n_agents", n, "agents")
    if n == 0:
        return {"metrics": metrics, "reports": reports}

    trips = frames.get("trips")
    if trips is not None and len(trips):
        first = first_trips(trips)
        put_distribution("departure_home_h", first["departure_time"].to_numpy(dtype=float) / 3600.0, "h of the day")
        put_distribution("arrival_first_activity_h", first["arrival_time"].to_numpy(dtype=float) / 3600.0,
                         "h of the day")
        put_shares("mode_share_pct", first["mode"])
    else:
        put("departure_home_h", NOT_AVAILABLE, "h of the day")
        put("mode_share_pct", NOT_AVAILABLE, "%")

    if "household_income_eur" in persons.columns:
        income = pd.to_numeric(persons["household_income_eur"], errors="coerce").to_numpy(dtype=float)
        put_distribution("household_income_eur", income, "EUR per month")
    else:
        put("household_income_eur", NOT_AVAILABLE, "EUR per month")

    if "economic_status" in persons.columns:
        status, source = persons["economic_status"], "persons column"
    elif (donor_persons is not None and {"person_id", "economic_status"} <= set(donor_persons.columns)
          and "hts_id" in persons.columns):
        lookup = donor_persons.drop_duplicates("person_id").set_index("person_id")["economic_status"]
        status, source = persons["hts_id"].map(lookup), "MiD donor persons through hts_id"
    else:
        status, source = None, None
    if status is None:
        put("economic_status_share_pct", NOT_AVAILABLE, "%")
    else:
        put("economic_status_source", source, "text")
        put_shares("economic_status_share_pct", status)

    if "household_size" in persons.columns:
        put_shares("household_size_share_pct", persons["household_size"].astype("Int64"))
    else:
        put("household_size_share_pct", NOT_AVAILABLE, "%")

    if "high_income" in persons.columns:
        put("high_income_share_pct", 100.0 * float(persons["high_income"].astype(bool).mean()), "%")
    else:
        put("high_income_share_pct", NOT_AVAILABLE, "%")

    if "hts_id" in persons.columns:
        per_donor = persons["hts_id"].value_counts()
        put("n_distinct_donors", int(len(per_donor)), "donors")
        put("largest_agents_per_donor", int(per_donor.max()), "agents per donor")
    else:
        put("n_distinct_donors", NOT_AVAILABLE, "donors")
        put("largest_agents_per_donor", NOT_AVAILABLE, "agents per donor")

    vehicles = frames.get("vehicles")
    for column in ("segment", "powertrain"):
        if vehicles is not None and column in vehicles.columns and vehicles[column].notna().any():
            put(f"fleet_{column}_n_vehicles", int(vehicles[column].notna().sum()), "vehicles")
            shares, _ = _shares_pct(vehicles[column])
            for key, value in shares.items():
                put(f"fleet_{column}_share_pct.{key}", value, "%")
        else:
            put(f"fleet_{column}_share_pct", NOT_AVAILABLE, "%")
    return {"metrics": metrics, "reports": reports}


# --------------------------------------------------------------------------------------------------------------
# Identity check of the old arm against the cached stage output
# --------------------------------------------------------------------------------------------------------------

def _origin_kreis(frames: dict) -> pd.Series | None:
    """Origin Kreis per agent (string), indexed by ``person_id``, where the frame carries it; else ``None``."""
    persons = frames["persons"]
    if "orig_ars5" in persons.columns:
        return pd.Series(persons["orig_ars5"].astype(str).to_numpy(), index=persons["person_id"].to_numpy())
    validation = frames.get("validation")
    if validation is not None and "ars5" in validation.columns and len(validation) == len(persons):
        # The validation frame has one row per agent in the order of the persons frame (same agent array).
        return pd.Series(validation["ars5"].astype(str).to_numpy(), index=persons["person_id"].to_numpy())
    return None


def _same(left: pd.Series, right: pd.Series) -> np.ndarray:
    """Element-wise equality of two aligned series, NaN equal to NaN."""
    return (left.eq(right) | (left.isna() & right.isna())).to_numpy(dtype=bool)


def identity_check(replay: dict, cached: dict, *, min_share: float = MIN_SHARE_IDENTICAL) -> dict:
    """Compare the replay with the cached stage output in what the pt check never changes (module docstring).

    Returns ``{"passed", "failures", "checks", "limit"}``; ``failures`` is a list of readable sentences.
    """
    failures: list = []
    checks: dict = {}
    report = {"passed": False, "failures": failures, "checks": checks, "limit": IDENTITY_LIMIT}
    rp, cp = replay.get("persons"), cached.get("persons")
    n_r, n_c = (0 if rp is None else len(rp)), (0 if cp is None else len(cp))
    checks["n_agents"] = {"replay": n_r, "cached": n_c}
    if n_r != n_c:
        failures.append(f"the number of agents differs: replay {n_r}, cached {n_c}")
        return report
    if n_r == 0:
        report["passed"] = True
        checks["note"] = "both outputs are empty (stage inactive or without agents)"
        return report
    if rp["person_id"].duplicated().any() or cp["person_id"].duplicated().any():
        failures.append("person ids are not unique in one of the outputs")
        return report
    ids_r, ids_c = rp["person_id"].to_numpy(), cp["person_id"].to_numpy()
    same_set = bool(np.array_equal(np.sort(ids_r), np.sort(ids_c)))
    checks["person_ids"] = {"same_set": same_set, "same_order": bool(np.array_equal(ids_r, ids_c))}
    if not same_set:
        only_r = int((~np.isin(ids_r, ids_c)).sum())
        failures.append(f"the person ids differ: {only_r} of the replay's {n_r} ids are not in the cached output")
        return report
    rp, cp = rp.set_index("person_id").sort_index(), cp.set_index("person_id").sort_index()

    origin_r, origin_c = _origin_kreis(replay), _origin_kreis(cached)
    if origin_r is None or origin_c is None:
        checks["origin_kreis"] = {"compared": False, "reason": "the frames carry no origin Kreis per agent"}
    else:
        share = float(_same(origin_r.sort_index(), origin_c.sort_index()).mean())
        checks["origin_kreis"] = {"compared": True, "identical_share_pct": 100.0 * share}
        if share < min_share:
            failures.append(f"origin Kreis identical for only {100.0 * share:.2f}% of the agents (need "
                            f"{100.0 * min_share:.0f}%)")

    mode_r = first_trips(replay["trips"])["mode"].reindex(rp.index)
    mode_c = first_trips(cached["trips"])["mode"].reindex(cp.index)
    same_mode = _same(mode_r, mode_c)
    n_comparable = int(same_mode.sum())
    checks["mode"] = {"agents_with_equal_mode": n_comparable, "agents_with_other_mode": int(n_r - n_comparable),
                      "note": "the pt check of the cached run switches some pt agents to car"}
    if "hts_id" not in rp.columns or "hts_id" not in cp.columns:
        failures.append("a persons frame has no hts_id: the donors cannot be compared")
    elif n_comparable == 0:
        failures.append("no agent has the same mode in the replay and in the cached output: nothing comparable")
    else:
        same_donor = _same(rp["hts_id"], cp["hts_id"])
        share = float(same_donor[same_mode].mean())
        checks["donor_id"] = {"identical_share_pct_of_equal_mode_agents": 100.0 * share,
                              "identical_share_pct_of_all_agents": 100.0 * float(same_donor.mean())}
        if share < min_share:
            failures.append(f"donor id identical for only {100.0 * share:.2f}% of the {n_comparable} agents with an "
                            f"equal mode (need {100.0 * min_share:.0f}%)")

    # Stronger form: the donor draw runs before the pt check and the household columns follow the donor only.
    columns, only_replay = {}, [c for c in rp.columns if c not in cp.columns]
    only_cached = [c for c in cp.columns if c not in rp.columns]
    for column in rp.columns:
        if column in cp.columns and column not in MODE_DEPENDENT_PERSON_COLUMNS:
            try:
                columns[column] = 100.0 * float(_same(rp[column], cp[column]).mean())
            except (TypeError, ValueError) as exc:
                columns[column] = f"not comparable: {exc}"
    checks["persons_columns_identical_share_pct"] = columns
    checks["persons_columns_only_in_replay"], checks["persons_columns_only_in_cached"] = only_replay, only_cached
    for column, share in columns.items():
        if isinstance(share, float) and share < 100.0 * min_share:
            failures.append(f"persons column {column!r} identical for only {share:.2f}% of the agents (need "
                            f"{100.0 * min_share:.0f}%)")

    # Informational: first-trip times of the agents with an equal mode (the pt check can shift a day).
    first_r = first_trips(replay["trips"]).reindex(rp.index)
    first_c = first_trips(cached["trips"]).reindex(cp.index)
    same_time = _same(first_r["departure_time"], first_c["departure_time"]) & _same(
        first_r["arrival_time"], first_c["arrival_time"])
    checks["first_trip_times"] = {"identical_share_pct_of_equal_mode_agents": 100.0 * float(same_time[same_mode].mean())
                                  if n_comparable else None, "gating": False}
    report["passed"] = not failures
    return report


# --------------------------------------------------------------------------------------------------------------
# Printed table
# --------------------------------------------------------------------------------------------------------------

def _format_value(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_table(stage: str, old: dict | None, new: dict | None) -> list:
    """Lines of the table ``metric | unit | old | new`` of one stage."""
    old_metrics = (old or {}).get("metrics", {})
    new_metrics = (new or {}).get("metrics", {})
    names = list(old_metrics) + [name for name in new_metrics if name not in old_metrics]
    rows = [(name, (old_metrics.get(name) or new_metrics.get(name))["unit"],
             _format_value((old_metrics.get(name) or {}).get("value", NOT_AVAILABLE)),
             _format_value((new_metrics.get(name) or {}).get("value", NOT_AVAILABLE))) for name in names]
    width = max([len(r[0]) for r in rows] + [6])
    unit_width = max([len(r[1]) for r in rows] + [4])
    lines = [f"=== {stage} ===", f"{'metric':<{width}}  {'unit':<{unit_width}}  {'old':>16}  {'new':>16}"]
    lines += [f"{name:<{width}}  {unit:<{unit_width}}  {o:>16}  {n:>16}" for name, unit, o, n in rows]
    for arm, summary in (("old", old), ("new", new)):
        for key, value in (summary or {}).get("reports", {}).items():
            lines.append(f"{key} [{arm}]: {json.dumps(value, sort_keys=True)}")
    return lines


# --------------------------------------------------------------------------------------------------------------
# One arm (runs in its own subprocess)
# --------------------------------------------------------------------------------------------------------------

class _CaptureHandler(logging.Handler):
    """Collects the records of the donor-draw related loggers, per stage (``mark`` starts a new stage)."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records: list = []

    def emit(self, record):
        self.records.append({"logger": record.name, "level": record.levelname, "message": record.getMessage()})

    def take(self) -> list:
        taken, self.records = self.records, []
        return taken


def run_module(module_name: str, *, config: dict, aliases: dict, cache: Path, run_log: Path, out: Path,
               overrides: dict, use_log_stems: bool = True) -> tuple:
    """Run ``configure`` and ``execute`` of a stage module with the stand-in context; ``(result, context, run stem)``."""
    import importlib  # noqa: PLC0415 (after sys.path was set by the arm)

    module = importlib.import_module(module_name)
    stems = stems_before(run_log, module_name) if use_log_stems else {"__self__": None}
    run_stem = stems.pop("__self__")
    stage_out = out / module_name
    stage_out.mkdir(parents=True)
    context = ReplayContext(config=config, stems=stems, cache=cache, out_dir=stage_out, aliases=aliases,
                            overrides=overrides)
    module.configure(context)
    context.configuring = False
    context.check_declared_inputs(module_name)
    t0 = time.time()
    logger.info("=== replay %s (run stem %s) ===", module_name, run_stem)
    result = module.execute(context)
    logger.info("=== %s finished in %.0f s ===", module_name, time.time() - t0)
    logger.info(context.stem_report())
    return result, context, run_stem


def run_arm(args) -> int:
    """Replay the stages with one code version; write ``<out>/arm_<arm>.json``. Exit code 2: identity check failed."""
    code, cache, out = Path(args.code).resolve(), Path(args.cache).expanduser(), Path(args.out)
    sys.path.insert(0, str(code))
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import braunschweig  # noqa: PLC0415

    imported_from = Path(braunschweig.__file__).resolve()
    if code not in imported_from.parents:
        raise RuntimeError(f"braunschweig was imported from {imported_from}, not from --code {code}")
    from braunschweig.synpp_fork import registry  # noqa: PLC0415

    capture = _CaptureHandler()
    for name in LOGGER_NAMES:
        logging.getLogger(name).setLevel(logging.INFO)
        logging.getLogger(name).addHandler(capture)

    settings = registry.load_settings(str(code / "configs" / "base_bs.yml"), str(Path(args.run_yml).expanduser()))
    config = dict(settings.get("config", {}) or {})
    config[PT_CHECK_KEY] = False
    aliases = dict(settings.get("aliases", {}) or {})
    run_log = Path(args.run_log).expanduser()
    logger.info("arm %s: code %s, cache %s, %d config keys, %d aliases; %s", args.arm, code, cache, len(config),
                len(aliases), PT_CHECK_STATEMENT)
    result = {"arm": args.arm, "code": str(code), "pt_timetable_check": PT_CHECK_STATEMENT,
              "random_seed": config.get("random_seed"), "sampling_rate": config.get("sampling_rate"),
              "recomputed_stages": {}, "stages": {}}

    overrides: dict = {}
    if args.arm == "new":
        for name in RECOMPUTED_STAGES:
            started = time.time()
            overrides[name], _context, _ = run_module(
                name, config=config, aliases=aliases, cache=cache, run_log=run_log, out=out / "recomputed",
                overrides={}, use_log_stems=False)
            result["recomputed_stages"][name] = {"seconds": round(time.time() - started)}
            capture.take()      # the log lines of these stages are kept in the log file, not per in-commuter stage
    exit_code = 0
    for module_name in args.stage:
        started = time.time()
        frames, context, run_stem = run_module(module_name, config=config, aliases=aliases, cache=cache,
                                               run_log=run_log, out=out / args.arm, overrides=overrides)
        entry = {"run_stem": run_stem, "seconds": round(time.time() - started)}
        if args.arm == "old":
            t0 = time.time()
            with open(cache / f"{run_stem}.p", "rb") as handle:
                cached = pickle.load(handle)
            entry["identity"] = jsonable(identity_check(frames, cached))
            entry["identity"]["load_seconds"] = round(time.time() - t0)
            del cached
            logger.info("identity check %s: %s", module_name, "PASSED" if entry["identity"]["passed"] else "FAILED")
            for failure in entry["identity"]["failures"]:
                logger.error("identity check %s: %s", module_name, failure)
            if not entry["identity"]["passed"]:
                entry["log_lines"] = capture.take()
                result["stages"][module_name] = entry
                exit_code = EXIT_IDENTITY_FAILED
                break
        donor_persons = None
        hts_name = context._name("braunschweig.data.hts.mid_donor")  # noqa: SLF001
        hts = overrides.get(hts_name) or context._loaded.get(hts_name)  # noqa: SLF001
        if hts is not None:
            donor_persons = hts[1]
        entry["summary"] = summarise_stage(frames, donor_persons)
        entry["log_lines"] = capture.take()
        result["stages"][module_name] = entry
        del frames
    with open(out / f"arm_{args.arm}.json", "w", encoding="utf-8") as handle:
        json.dump(jsonable(result), handle, indent=2)
    return exit_code


# --------------------------------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------------------------------

def git_state(code_dir: Path) -> dict:
    """The commit of a code directory (read-only ``git`` calls); ``unknown`` with the reason when git fails."""
    try:
        commit = subprocess.run(["git", "-C", str(code_dir), "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True, timeout=60).stdout.strip()
        changed = subprocess.run(["git", "-C", str(code_dir), "status", "--porcelain", "--untracked-files=no"],
                                 capture_output=True, text=True, check=True, timeout=60).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return {"commit": "unknown", "error": str(exc)}
    return {"commit": commit, "tracked_files_modified": len(changed)}


def start_arm(arm: str, code: str, args, out: Path) -> int:
    """Run one arm in its own interpreter, tee its output to ``arm_<arm>.log``; the arm's exit code."""
    command = [sys.executable, os.path.abspath(__file__), "--arm", arm, "--code", code, "--cache", args.cache,
               "--run-yml", args.run_yml, "--run-log", args.run_log, "--out", str(out)]
    for stage in args.stage:
        command += ["--stage", stage]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    with open(out / f"arm_{arm}.log", "w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", env=env)
        for line in process.stdout:
            log.write(line)
            log.flush()
            sys.stdout.write(line)
        return process.wait()


def orchestrate(args) -> int:
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    args.stage = list(args.stage or DEFAULT_STAGES)
    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "script": Path(__file__).name,
        "cache": args.cache, "run_yml": args.run_yml, "run_log": args.run_log,
        "pt_timetable_check": PT_CHECK_STATEMENT,
        "identity_check_limit": IDENTITY_LIMIT,
        "code": {"old": dict(path=args.code_old, **git_state(Path(args.code_old))),
                 "new": dict(path=args.code_new, **git_state(Path(args.code_new)))},
        "stages_replayed": args.stage,
        "recomputed_in_new_arm": list(RECOMPUTED_STAGES),
        "random_seed": None, "sampling_rate": None, "arms": {}, "identity": {}, "stages": {},
    }

    def finish(code: int) -> int:
        with open(out / "summary.json", "w", encoding="utf-8") as handle:
            json.dump(jsonable(summary), handle, indent=2)
        return code

    arms = {}
    for arm, code_dir in (("old", args.code_old), ("new", args.code_new)):
        rc = start_arm(arm, code_dir, args, out)
        arm_file = out / f"arm_{arm}.json"
        arms[arm] = json.loads(arm_file.read_text(encoding="utf-8")) if arm_file.is_file() else None
        summary["arms"][arm] = {"exit_code": rc}
        if arm == "old" and arms[arm]:
            summary["identity"] = {s: e.get("identity") for s, e in arms[arm]["stages"].items()}
            summary["random_seed"] = arms[arm].get("random_seed")
            summary["sampling_rate"] = arms[arm].get("sampling_rate")
        if rc == EXIT_IDENTITY_FAILED:
            print("IDENTITY CHECK FAILED: the old arm does not reproduce the cached output; the new arm was not run.")
            for stage, entry in (arms[arm] or {"stages": {}})["stages"].items():
                for failure in (entry.get("identity") or {}).get("failures", []):
                    print(f"  {stage}: {failure}")
            return finish(EXIT_IDENTITY_FAILED)
        if rc != 0:
            print(f"arm {arm} failed with exit code {rc}; see {out / f'arm_{arm}.log'}")
            return finish(rc)
    for stage in args.stage:
        old_entry, new_entry = arms["old"]["stages"].get(stage, {}), arms["new"]["stages"].get(stage, {})
        summary["stages"][stage] = {
            "old": {k: old_entry.get(k) for k in ("summary", "log_lines", "seconds")},
            "new": {k: new_entry.get(k) for k in ("summary", "log_lines", "seconds")}}
    finish(0)
    print("\n" + PT_CHECK_STATEMENT)
    print(f"seed {summary['random_seed']}, sampling rate {summary['sampling_rate']}; old {summary['code']['old']['commit']}, "
          f"new {summary['code']['new']['commit']}; {summary['created_utc']}")
    for stage, entry in summary["identity"].items():
        checks = (entry or {}).get("checks", {})
        donor = checks.get("donor_id", {})
        print(f"identity {stage}: {'passed' if (entry or {}).get('passed') else 'FAILED'}; donor id identical "
              f"{donor.get('identical_share_pct_of_equal_mode_agents', float('nan')):.2f}% of the agents with an "
              f"equal mode; limit: {IDENTITY_LIMIT}")
    for stage in args.stage:
        old_s = (summary["stages"][stage]["old"] or {}).get("summary")
        new_s = (summary["stages"][stage]["new"] or {}).get("summary")
        print("\n".join([""] + render_table(stage, old_s, new_s)))
    print(f"\nwritten: {out / 'summary.json'}")
    return 0


# --------------------------------------------------------------------------------------------------------------
# Self-test on synthetic frames
# --------------------------------------------------------------------------------------------------------------

def _synthetic_frames(n: int = 200, *, seed: int = 1, with_status: bool = True) -> dict:
    rng = np.random.default_rng(seed)
    ids = np.arange(1000, 1000 + n)
    depart = rng.uniform(5 * 3600, 9 * 3600, n)
    modes = np.where(rng.random(n) < 0.3, "pt", "car")
    persons = pd.DataFrame({
        "person_id": ids, "hts_id": rng.integers(1, 40, n),
        "household_income_eur": rng.choice([1500.0, 2500.0, 4200.0, 6100.0], n),
        "household_size": rng.integers(1, 5, n), "car_availability": np.where(modes == "car", "all", "none"),
        "number_of_cars": (modes == "car").astype(int)})
    persons["high_income"] = persons["household_income_eur"] >= 4000.0
    if with_status:
        persons["economic_status"] = rng.choice(["low", "mid", "high"], n)
    trips = pd.DataFrame({
        "person_id": np.repeat(ids, 2), "trip_index": np.tile([0, 1], n),
        "departure_time": np.column_stack([depart, depart + 9 * 3600]).ravel(),
        "arrival_time": np.column_stack([depart + 1800, depart + 9 * 3600 + 1800]).ravel(),
        "mode": np.repeat(modes, 2)})
    validation = pd.DataFrame({"ars5": rng.choice(["03154", "03101", "03153"], n), "mode": modes})
    vehicles = pd.DataFrame({"owner_id": ids, "segment": rng.choice(["kompakt", "suv"], n),
                             "powertrain": rng.choice(["petrol", "diesel", "bev"], n)})
    return {"persons": persons, "trips": trips, "validation": validation, "vehicles": vehicles,
            "donor_draw": {"n_agents": n, "tilted": True, "min_effective_size": 812.5},
            "day_bound": {"n": n, "n_days_moved": 0}}


def self_test() -> int:
    """Run the summary, identity, stand-in context and log parsing on synthetic data; raise on any failure."""
    frames = _synthetic_frames()
    summary = summarise_stage(frames)
    metrics = summary["metrics"]
    assert metrics["n_agents"] == {"value": 200, "unit": "agents"}, metrics["n_agents"]
    assert metrics["departure_home_h.p10"]["value"] <= metrics["departure_home_h.p50"]["value"] \
        <= metrics["departure_home_h.p90"]["value"]
    assert 5.0 <= metrics["departure_home_h.mean"]["value"] <= 9.0
    assert abs(sum(v["value"] for k, v in metrics.items() if k.startswith("mode_share_pct.")
                   and not k.endswith("n_missing")) - 100.0) < 1e-9
    assert metrics["household_income_eur.mean"]["unit"] == "EUR per month"
    assert metrics["n_distinct_donors"]["value"] <= 39
    assert "fleet_segment_share_pct.suv" in metrics and "fleet_powertrain_share_pct.bev" in metrics
    assert summary["reports"]["donor_draw"]["min_effective_size"] == 812.5
    # An old-style frame without status, donors or fleet: "not available", nothing invented.
    old_style = _synthetic_frames(with_status=False)
    del old_style["vehicles"], old_style["donor_draw"]
    old_metrics = summarise_stage(old_style)["metrics"]
    assert old_metrics["economic_status_share_pct"]["value"] == NOT_AVAILABLE
    assert old_metrics["fleet_segment_share_pct"]["value"] == NOT_AVAILABLE
    # The status of the new arm is looked up through hts_id in the donor persons.
    donors = pd.DataFrame({"person_id": np.arange(1, 40), "economic_status": ["mid"] * 39})
    looked_up = summarise_stage(old_style, donors)["metrics"]
    assert looked_up["economic_status_share_pct.mid"]["value"] == 100.0, looked_up
    assert summarise_stage({"persons": pd.DataFrame()})["metrics"]["n_agents"]["value"] == 0
    assert render_table("stage", summary, summarise_stage(old_style))[1].startswith("metric")

    # Identity: the pt check switches 5 pt agents to car (mode and car attributes differ, donors do not).
    cached = {key: (value.copy() if hasattr(value, "copy") else value) for key, value in frames.items()}
    flip = cached["persons"].index[(frames["persons"]["car_availability"] == "none")][:5]
    cached["persons"].loc[flip, ["car_availability", "number_of_cars"]] = ["all", 1]
    cached["trips"].loc[cached["trips"]["person_id"].isin(cached["persons"].loc[flip, "person_id"]), "mode"] = "car"
    report = identity_check(frames, cached)
    assert report["passed"], report["failures"]
    assert report["checks"]["mode"]["agents_with_other_mode"] == 5
    assert report["checks"]["origin_kreis"]["compared"] is True
    assert "income" not in " ".join(report["failures"])
    # Identity must FAIL on: other donors, other person ids, other agent count, other origin Kreis, other income.
    broken = {key: value.copy() if hasattr(value, "copy") else value for key, value in cached.items()}
    broken["persons"].loc[broken["persons"].index[:20], "hts_id"] += 1000
    assert not identity_check(frames, broken)["passed"]
    broken = {key: value.copy() if hasattr(value, "copy") else value for key, value in cached.items()}
    broken["persons"].loc[broken["persons"].index[:20], "household_income_eur"] += 1.0
    assert any("household_income_eur" in f for f in identity_check(frames, broken)["failures"])
    broken = {key: value.copy() if hasattr(value, "copy") else value for key, value in cached.items()}
    broken["validation"].loc[broken["validation"].index[:20], "ars5"] = "99999"
    assert any("origin Kreis" in f for f in identity_check(frames, broken)["failures"])
    broken = {key: value.copy() if hasattr(value, "copy") else value for key, value in cached.items()}
    broken["persons"].loc[broken["persons"].index[0], "person_id"] = 5
    assert any("person ids differ" in f for f in identity_check(frames, broken)["failures"])
    broken = dict(cached, persons=cached["persons"].iloc[:-1])
    assert any("number of agents" in f for f in identity_check(frames, broken)["failures"])
    assert identity_check({"persons": pd.DataFrame()}, {"persons": pd.DataFrame()})["passed"]
    assert jsonable({"a": np.float64("nan"), "b": np.int64(3)}) == {"a": None, "b": 3}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        digest_a, digest_b, digest_c = "a" * 32, "b" * 32, "c" * 32
        log = tmp_path / "run.log"
        log.write_text(f"Executing stage parent.one__{digest_a}\nFinished running parent.one__{digest_a}\n"
                       f"Loading cache for parent.two__{digest_b}\nFinished running my.stage__{digest_c}\n",
                       encoding="utf-8")
        stems = stems_before(log, "my.stage")
        assert stems["__self__"] == f"my.stage__{digest_c}" and stems["parent.two"] == f"parent.two__{digest_b}"
        cache = tmp_path / "cache"
        cache.mkdir()
        with open(cache / f"parent.one__{digest_a}.p", "wb") as handle:
            pickle.dump({"value": 1}, handle)
        context = ReplayContext(config={"k": 1}, stems=stems, cache=cache, out_dir=tmp_path)
        assert context.config("declared", 7) == 7      # configure phase: default declared
        context.stage("parent.one")
        context.stage("parent.two", alias="two")       # declared, but no pickle in the cache
        context.configuring = False
        assert context.stage("parent.one") == {"value": 1} and context.config("declared") == 7
        assert "primary (run log) 1/1" in context.stem_report()
        try:
            context.check_declared_inputs("my.stage")
        except ReplayInputError as exc:
            assert "parent.two" in str(exc), exc       # the message names the missing stage
        else:
            raise AssertionError("a declared stage without a pickle must raise")
        override = ReplayContext(config={}, stems=stems, cache=cache, out_dir=tmp_path,
                                 overrides={"parent.two": "from new code"})
        override.stage("parent.two")
        override.configuring = False
        override.check_declared_inputs("my.stage")     # the overridden stage needs no pickle
        assert override.stage("parent.two") == "from new code"
    print("self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code-old", help="checkout of the main branch")
    parser.add_argument("--code-new", help="checkout of this branch")
    parser.add_argument("--cache", help="the run's synpp cache directory (read only)")
    parser.add_argument("--run-yml", help="the run's run.yml (config overlay)")
    parser.add_argument("--run-log", help="the run's log file (names the hash of each cached stage)")
    parser.add_argument("--out", help="output directory (must not exist)")
    parser.add_argument("--stage", action="append", help="stage module to replay (repeatable; default both)")
    parser.add_argument("--self-test", action="store_true", help="run on synthetic frames and exit")
    parser.add_argument("--arm", choices=("old", "new"), help=argparse.SUPPRESS)
    parser.add_argument("--code", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    required = ("cache", "run_yml", "run_log", "out") + (("code",) if args.arm else ("code_old", "code_new"))
    missing = [f"--{name.replace('_', '-')}" for name in required if not getattr(args, name)]
    if missing:
        parser.error("required: " + ", ".join(missing))
    if args.arm:
        args.stage = list(args.stage or DEFAULT_STAGES)
        return run_arm(args)
    return orchestrate(args)


if __name__ == "__main__":
    sys.exit(main())
