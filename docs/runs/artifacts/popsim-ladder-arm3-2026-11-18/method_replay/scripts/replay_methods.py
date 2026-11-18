"""Replay captured simultaneous-integerizer inputs with upstream's rounding and both controlled methods (issue #646).

Engineering measurement on the real inputs of the simultaneous integerizer of a PopulationSim batch (saved by
``capture_simul_instances.py``). Every instance, one parent zone with its sub-zones, is integerized by the
fork's own entry point ``populationsim.integerizing.wrappers.do_simul_integerizing`` once per method:

* ``round``  : ``INTEGERIZER_ROUNDING: round``, upstream's linear programme and, where it fails, upstream's
  sequential path;
* ``search`` : ``INTEGERIZER_ROUNDING: controlled`` with ``INTEGERIZER_CONTROLLED_METHOD: search``;
* ``lp``     : ``INTEGERIZER_ROUNDING: controlled`` with ``INTEGERIZER_CONTROLLED_METHOD: lp``.

The draw settings are those of the batch (``gumbel`` and the batch's ``INTEGERIZER_RANDOM_SEED``); every other
fork setting keeps its default. All methods start from the same float weights, so the rows compare the
rounding alone. One CSV row per (instance, method).

Columns (sums over the sub-zones of the parent; ``F`` float cell total, ``I`` integer cell total):
``outcome``: the parent outcome the wrapper records (``simultaneous`` or a fallback); ``statuses``: the
status of every simultaneous integerizer call of the instance, in order; ``seconds``: wall time of the whole
wrapper call; ``rows_conserved_share``: household rows whose integer weights over the sub-zones sum to the
rounded sum of their float weights; ``conserve_deviation``: sum over rows of the absolute difference;
``parent_shift_max`` and ``parent_shift_sum``: per sub control (household total left out)
``|sum_z I - sum_z F|``; ``cell_distance_weighted``: ``sum q_c |I - F|`` with ``q_c`` the control importance
clipped to the fork's penalty floor and cap; ``cell_distance``: the same unweighted; ``target_distance`` and
``target_wape``: ``sum |I - target|`` and that sum over the target sum; ``household_total_deviation``:
``sum_z |households - control|``; ``distinct_rows``: rows with a positive integer weight;
``integer_households``: the sum of the integer weights.

Usage (the fork code under test importable, PopulationSim environment, single thread):
  python replay_methods.py --instances <dir> --out <csv> --random-seed <INTEGERIZER_RANDOM_SEED of the batch>
      [--methods round search lp] [--timeout 7200] [--largest N]
"""
import argparse
import csv
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from populationsim.core import config
from populationsim.integerizing import bs_options, simul_integerizer, wrappers

METHOD_SETTINGS = {
    "round": {"INTEGERIZER_ROUNDING": "round"},
    "search": {"INTEGERIZER_ROUNDING": "controlled", "INTEGERIZER_CONTROLLED_METHOD": "search"},
    "lp": {"INTEGERIZER_ROUNDING": "controlled", "INTEGERIZER_CONTROLLED_METHOD": "lp"},
}
FIELDS = ["instance", "method", "outcome", "statuses", "zones", "rows", "seconds", "rows_conserved_share",
          "conserve_deviation", "parent_shift_max", "parent_shift_sum", "cell_distance_weighted",
          "cell_distance", "target_distance", "target_wape", "household_total_deviation", "distinct_rows",
          "integer_households"]
SETTINGS = {}
SEEN = {"statuses": [], "outcomes": []}


def inject_settings(values: dict) -> None:
    """Make ``config.setting`` answer from ``values`` (the replay has no settings file)."""
    SETTINGS.clear()
    SETTINGS.update(values)
    config.setting = lambda key, default=None: SETTINGS.get(key, default)
    bs_options.reset_for_tests()


def install_observers() -> None:
    """Note the status of every simultaneous integerizer call and the parent outcome the wrapper records."""
    integerize = simul_integerizer.SimulIntegerizer.integerize
    record_outcome = wrappers._record_parent_outcome

    def observed_integerize(self):
        status = integerize(self)
        SEEN["statuses"].append(status)
        return status

    def observed_outcome(outcome, *args, **kwargs):
        SEEN["outcomes"].append(outcome)
        return record_outcome(outcome, *args, **kwargs)

    simul_integerizer.SimulIntegerizer.integerize = observed_integerize
    wrappers._record_parent_outcome = observed_outcome


def arrays_of(instance: dict) -> dict:
    """The numpy inputs, derived as ``SimulIntegerizer`` derives them."""
    incidence_df = instance["incidence_df"]
    sub_controls_df = instance["sub_controls_df"]
    control_spec = instance["control_spec"]
    sub_spec = control_spec[control_spec.target.isin(list(sub_controls_df.columns))]
    sub_controls = sub_controls_df[sub_spec.target.values]
    hh_index = int(sub_controls.columns.get_loc(instance["total_hh_control_col"]))
    targets = np.asanyarray(sub_controls).astype(np.float64)
    return {
        "sub_incidence": incidence_df[sub_controls.columns].values.astype(np.float64),
        "weights": instance["sub_weights"].values.transpose().astype(np.float64),
        "sub_importance": np.asanyarray(sub_spec.importance).astype(np.float64),
        "targets": targets,
        "household_controls": targets[:, hh_index].astype(np.int64),
        "hh_index": hh_index,
    }


def quality(integer: np.ndarray, arrays: dict, penalties) -> dict:
    """Outcome numbers of an integer weight matrix (zones x rows)."""
    weights = arrays["weights"]
    incidence = arrays["sub_incidence"]
    hh_index = arrays["hh_index"]
    row_target = np.rint(weights.sum(axis=0))
    row_deviation = np.abs(integer.sum(axis=0) - row_target)
    float_cells = weights @ incidence
    integer_cells = integer.astype(np.float64) @ incidence
    keep = np.ones(incidence.shape[1], dtype=bool)
    keep[hh_index] = False
    price = np.clip(arrays["sub_importance"], penalties.floor, penalties.cap)
    shift = np.abs(integer_cells.sum(axis=0) - float_cells.sum(axis=0))[keep]
    distance = np.abs(integer_cells - float_cells)[:, keep]
    target_distance = np.abs(integer_cells - arrays["targets"])[:, keep]
    target_sum = arrays["targets"][:, keep].sum()
    return {
        "rows_conserved_share": "%.6f" % float((row_deviation == 0).mean()),
        "conserve_deviation": "%.0f" % row_deviation.sum(),
        "parent_shift_max": "%.4f" % shift.max(),
        "parent_shift_sum": "%.4f" % shift.sum(),
        "cell_distance_weighted": "%.2f" % float((distance * price[keep]).sum()),
        "cell_distance": "%.2f" % distance.sum(),
        "target_distance": "%.2f" % target_distance.sum(),
        "target_wape": "%.6f" % (target_distance.sum() / target_sum if target_sum else float("nan")),
        "household_total_deviation": "%.0f" % np.abs(integer.sum(axis=1) - arrays["household_controls"]).sum(),
        "distinct_rows": int((integer.sum(axis=0) > 0).sum()),
        "integer_households": int(integer.sum()),
    }


def integer_weights(instance: dict) -> np.ndarray:
    """Integer weights (zones x rows) of the fork's wrapper for the injected settings."""
    sub_weights = instance["sub_weights"]
    sub_controls_df = instance["sub_controls_df"]
    first = str(sub_weights.columns[0])
    zone_ids = list(sub_controls_df.index)
    sub_geography = first[: len(first) - len(str(zone_ids[0])) - 1]
    names = ["%s_%s" % (sub_geography, z) for z in zone_ids]
    if list(sub_weights.columns) != names:
        raise ValueError("zone columns %s... do not match the control index" % list(sub_weights.columns)[:2])
    frame = wrappers.do_simul_integerizing(
        trace_label=instance["trace_label"], incidence_df=instance["incidence_df"], sub_weights=sub_weights,
        sub_controls_df=sub_controls_df, control_spec=instance["control_spec"],
        total_hh_control_col=instance["total_hh_control_col"], sub_geography=sub_geography,
        sub_control_zones=pd.Series(names, index=sub_controls_df.index))
    table = frame.pivot(index=sub_geography, columns=sub_weights.index.name, values="integer_weight")
    return table.reindex(index=zone_ids, columns=sub_weights.index).fillna(0).values.astype(np.int64)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instances", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--random-seed", required=True, type=int)
    parser.add_argument("--methods", nargs="+", default=["round", "search", "lp"], choices=sorted(METHOD_SETTINGS))
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--largest", type=int, default=0)
    args = parser.parse_args(argv)
    if args.out.exists():
        print("refuse: %s exists" % args.out, file=sys.stderr)
        return 1
    base_settings = {
        "INTEGERIZER_DRAW": "gumbel", "INTEGERIZER_RANDOM_SEED": args.random_seed,
        "KEEP_FRACTIONAL_SAMPLE_WEIGHTS": True, "INTEGERIZER_TIMEOUT": args.timeout,
        "INTEGERIZER_PATH_RECORD": False, "USE_CVXPY": False, "USE_SIMUL_INTEGERIZER": True,
        "INTEGERIZE_WITH_BACKSTOPPED_CONTROLS": True, "SUB_BALANCE_WITH_FLOAT_SEED_WEIGHTS": False,
    }
    install_observers()
    paths = [p for p in sorted(args.instances.glob("*.pkl")) if "_retry_" not in p.name]
    if args.largest:
        paths = sorted(paths, key=lambda p: -p.stat().st_size)[: args.largest]
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for path in paths:
            with path.open("rb") as source:
                instance = pickle.load(source)
            arrays = arrays_of(instance)
            for method in args.methods:
                inject_settings(dict(base_settings, **METHOD_SETTINGS[method]))
                penalties = bs_options.penalty_settings()
                SEEN["statuses"].clear()
                SEEN["outcomes"].clear()
                started = time.perf_counter()
                integer = integer_weights(instance)
                row = {"instance": path.stem, "method": method, "outcome": "|".join(SEEN["outcomes"]),
                       "statuses": "|".join(SEEN["statuses"]), "zones": arrays["weights"].shape[0],
                       "rows": arrays["weights"].shape[1], "seconds": "%.2f" % (time.perf_counter() - started)}
                row.update(quality(integer, arrays, penalties))
                writer.writerow(row)
                handle.flush()
                print("[replay] %s %s %s %s s" % (path.stem[:44], method, row["outcome"], row["seconds"]),
                      flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
