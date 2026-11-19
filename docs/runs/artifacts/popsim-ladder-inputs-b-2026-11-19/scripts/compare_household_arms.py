"""Compare a household-universe ladder arm with its base arm (issue #659, Part B, Task B6).

Extends Part A's ``compare_input_arms.py`` (``popsim-ladder-inputs-a-2026-11-18/scripts``) by import:
its check kinds, its outputs (``comparison.csv``, ``verdict.json``) and its ``main`` are used as they
are; this script only registers three more kinds. Every bound comes from the checks JSON.

* ``composition``: reads ``household_composition.csv`` of the NEW arm's diagnostics
  (``braunschweig.analysis.popsim_batch_diagnostics.composition``, written with ``--cells-1km``).
  Per batch: met if ``|share_households_with_minor - census_share_families_with_minor|``
  <= ``bound_pp`` / 100 and ``one_adult_share_among_minor_households /
  census_lone_parent_share_among_families_with_minor`` lies in [``ratio_lo``, ``ratio_hi``]. A batch
  without a row, or with a NaN value, fails.
* ``cells``: reads ``<prepare_dir>/<batch>/<file>`` (default ``household_cells_check.json``, written
  by ``prepare_household_arm.py`` with ``move_persons``). Per batch: met if ``cells_left`` == 0 (the
  household-less cells below the bound that keep person targets; the donors in squares without
  households, ``cells_left_in_squares_without_households``, are reported, not gated) and
  ``max_abs_parent_sum_change_all`` == 0. A missing file fails.
* ``wape_controls_vs_base``: Part A's ``wape_vs_base`` (pooled WAPE over the zones and batches,
  ``sum abs_error / sum target_sum``) restricted to the controls matching ``include_regex`` at
  ``geography`` (default ``ZENSUS100m``); met if new <= base + ``delta`` and both arms carry the same
  matching controls.

Example::

    {"checks": [
      {"id": "C-comp", "kind": "composition", "bound_pp": 2.0, "ratio_lo": 0.8, "ratio_hi": 1.25},
      {"id": "C-cells", "kind": "cells", "prepare_dir": "<root>/arm_8"},
      {"id": "N-age-wape", "kind": "wape_controls_vs_base", "include_regex": "_AGE_", "delta": 0.0}]}

Usage:
    python compare_household_arms.py --base-diag <dir> --new-diag <dir> --checks <json> --out <dir>
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import pandas as pd

PART_A_SCRIPTS = Path(__file__).resolve().parents[2] / "popsim-ladder-inputs-a-2026-11-18" / "scripts"
if str(PART_A_SCRIPTS) not in sys.path:
    sys.path.append(str(PART_A_SCRIPTS))
import compare_input_arms as part_a  # noqa: E402  (Part A's comparison, extended, not forked)

COMPOSITION_FILE = "household_composition.csv"
CELLS_CHECK_FILE = "household_cells_check.json"


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def eval_composition(check, ctx, emit):
    path = ctx["arms"]["new"] / COMPOSITION_FILE
    if not path.is_file():
        emit(check["id"], "ALL", "new", "file_absent", math.nan, None, False)
        return {"met": False, "error": f"{path} absent"}
    table = pd.read_csv(path)
    bound = float(check["bound_pp"]) / 100.0
    lo, hi = float(check["ratio_lo"]), float(check["ratio_hi"])
    out = {"bound_pp": check["bound_pp"], "ratio_lo": lo, "ratio_hi": hi, "values": {}}
    met = True
    for batch in ctx["batches"]:
        rows = table[table["batch"] == batch]
        if len(rows) != 1:
            emit(check["id"], batch, "new", "rows", len(rows), 1, False)
            out["values"][batch] = f"{len(rows)} rows"
            met = False
            continue
        row = rows.iloc[0]
        gap = abs(_num(row["share_households_with_minor"]) - _num(row["census_share_families_with_minor"]))
        census_lone = _num(row["census_lone_parent_share_among_families_with_minor"])
        ratio = (_num(row["one_adult_share_among_minor_households"]) / census_lone
                 if census_lone and not math.isnan(census_lone) else math.nan)
        ok_gap = gap <= bound
        ok_ratio = lo <= ratio <= hi
        emit(check["id"], batch, "new", "abs_gap_share_with_minor", gap, bound, ok_gap)
        emit(check["id"], batch, "new", "one_adult_ratio_to_census", ratio, f"[{lo}, {hi}]", ok_ratio)
        out["values"][batch] = {"abs_gap": gap, "one_adult_ratio": ratio, "met": bool(ok_gap and ok_ratio)}
        met = met and ok_gap and ok_ratio
    out["met"] = bool(met)
    return out


def eval_cells(check, ctx, emit):
    root = Path(check["prepare_dir"])
    name = check.get("file", CELLS_CHECK_FILE)
    out = {"prepare_dir": str(root), "values": {}}
    met = True
    for batch in ctx["batches"]:
        path = root / batch / name
        if not path.is_file():
            emit(check["id"], batch, "new", "file_absent", math.nan, None, False)
            out["values"][batch] = f"{path} absent"
            met = False
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        left = int(record["cells_left"])
        donors = int(record.get("cells_left_in_squares_without_households", 0))
        change = float(record["max_abs_parent_sum_change_all"])
        emit(check["id"], batch, "new", "cells_left", left, 0, left == 0)
        emit(check["id"], batch, "new", "cells_left_in_squares_without_households", donors, None, None)
        emit(check["id"], batch, "new", "max_abs_parent_sum_change", change, 0, change == 0)
        ok = left == 0 and change == 0
        out["values"][batch] = {"cells_left": left, "cells_left_in_squares_without_households": donors,
                                "max_abs_parent_sum_change": change, "met": bool(ok)}
        met = met and ok
    out["met"] = bool(met)
    return out


def eval_wape_controls_vs_base(check, ctx, emit):
    """Part A's ``eval_wape_vs_base`` on the controls matching ``include_regex`` only."""
    pattern = re.compile(check["include_regex"])
    geography = check.get("geography", "ZENSUS100m")
    fits = {arm: fit[fit["control"].astype(str).str.contains(pattern)] for arm, fit in ctx["fits"].items()}
    empty = [arm for arm, fit in fits.items()
             if fit[fit["batch"].isin(ctx["batches"]) & (fit["geography"] == geography)].empty]
    if empty:
        emit(check["id"], "ALL", "new", f"wape_{geography}", math.nan, None, False)
        return {"met": False, "error": f"no control matches {check['include_regex']!r} at {geography} "
                                       f"in arm(s) {empty}"}
    inner = {"id": check["id"], "deltas": {geography: float(check["delta"])}}
    result = part_a.eval_wape_vs_base(inner, {**ctx, "fits": fits}, emit)
    result["include_regex"] = check["include_regex"]
    return result


EXTRA_EVALUATORS = {"composition": eval_composition, "cells": eval_cells,
                    "wape_controls_vs_base": eval_wape_controls_vs_base}
part_a.EVALUATORS.update(EXTRA_EVALUATORS)
part_a.KINDS = tuple(part_a.KINDS) + tuple(EXTRA_EVALUATORS)


def main(argv=None) -> int:
    return part_a.main(argv)


if __name__ == "__main__":
    sys.exit(main())
