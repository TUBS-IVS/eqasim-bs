"""Compare an input-consistency ladder arm with its base arm (issue #658, Part A, Task A7).

Reads two diagnostics directories written by
``python -m braunschweig.analysis.popsim_batch_diagnostics.run`` (``run_diag.sh`` of
``popsim-ladder-arm3-2026-11-18``), evaluates the gate checks of a checks JSON and writes into
``--out``: ``comparison.csv`` (long table: ``check, batch, arm, metric, value, limit, met``; one row
per check, batch and arm, ``batch`` = ``ALL`` for pooled checks) and ``verdict.json``
(``{"checks": {id: {"met": bool, ...}}, "all_met": bool}``). Pattern: ``compare_arm3.py``.

Every bound comes from the checks JSON; the script holds no reference value. Tables read:

* ``control_fit.csv`` (rows ``scope == "batch"``; columns ``batch, control, geography,
  target_sum, synthetic_sum, abs_error``), the fit estimator of ``fit.control_fit``;
* an input-consistency table (``input_consistency.csv`` of the diagnostics, or a copy of the
  stage's ``popsim_input_consistency.csv`` placed in the diagnostics directory; both have the
  columns ``batch, scope, key, check, cells, amount, base, share`` of
  ``input_consistency.summarize``, ``check`` = pair label, ``amount`` = size of the
  contradiction, ``share = amount / base``).

Check kinds (``met`` is judged on the new arm; the base arm is reported next to it):

* ``input_pairs`` (gate I): over the ``scope == "kreis"`` rows of ``file`` whose ``check`` is in
  ``pairs`` (optionally only ``batches``): value = max ``share`` (a row with ``base`` 0 and
  ``amount`` > 0 counts as infinite); met if value <= ``bound``. A pair with no row fails the check.
* ``fit_ratio`` (gates O-age, O-emp): per batch, value = sum ``synthetic_sum`` / sum ``target_sum``
  over the ``controls`` at ``geography``; met if ``|value - 1| <= bound``.
* ``fit_gap`` (gate O-own): per batch and control, value = gap / households, gap =
  ``|synthetic_sum - target_sum|`` (``measure: "net"``) or ``abs_error`` (``measure:
  "abs_error"``, the sum over zones), households = ``target_sum`` of ``households_control``
  (summed over its geographies in the batch); met if value <= ``bound``.
* ``wape_vs_base`` (gate N): per geography of ``deltas``, pooled over the batches, value =
  sum ``abs_error`` / sum ``target_sum`` over the controls with ``target_sum > 0`` that are not in
  ``exclude_controls`` and do not match ``exclude_regex``; met if new <= base + delta.

A listed control absent from ``control_fit.csv`` of a batch fails the check (``missing_controls``).
``batches`` at the top level (optional) restricts every check; default: the batches of the new arm.

Example (the plan's gates; control names illustrative)::

    {"batches": ["batch_000"],
     "checks": [
      {"id": "I-grid", "kind": "input_pairs", "file": "input_consistency.csv",
       "pairs": ["cars_0", "cars_1", "cars_2", "cars_3plus", "employed"], "bound": 1e-6},
      {"id": "O-age-0-17", "kind": "fit_ratio", "geography": "ZENSUS100m",
       "controls": ["M_AGE_0_9_agg_ZENSUS100m_target", "F_AGE_0_9_agg_ZENSUS100m_target"], "bound": 0.02},
      {"id": "O-own-cars", "kind": "fit_gap", "geography": "ZENSUS1km", "measure": "net",
       "controls": ["OWN_CARS_0_agg_ZENSUS1km_target"],
       "households_control": "Insgesamt_Haushalte_..._ZENSUS100m_target", "bound": 0.01},
      {"id": "O-emp", "kind": "fit_ratio", "geography": "ZENSUS100m",
       "controls": ["EMPLOYED_M_16_29_agg_ZENSUS100m_target"], "bound": 0.02},
      {"id": "N", "kind": "wape_vs_base", "deltas": {"ZENSUS100m": 0.02, "ZENSUS1km": 0.02, "KREIS": 0.01},
       "exclude_regex": "^(number_of_cars|number_of_bicycles|employed|education)_"}]}

Usage:
    python compare_input_arms.py --base-diag <dir> --new-diag <dir> --checks <json> --out <dir>
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import pandas as pd

KINDS = ("input_pairs", "fit_ratio", "fit_gap", "wape_vs_base")


def read_fit(diag: Path) -> pd.DataFrame:
    fit = pd.read_csv(diag / "control_fit.csv")
    return fit[fit["scope"] == "batch"]


def _batch_fit(fit, batch, geography=None):
    rows = fit[fit["batch"] == batch]
    return rows if geography is None else rows[rows["geography"] == geography]


def _missing(rows, controls):
    return sorted(set(controls) - set(rows["control"]))


def eval_input_pairs(check, arms, batches, emit):
    values = {}
    for arm, diag in arms.items():
        path = diag / check["file"]
        if not path.is_file():
            values[arm] = None
            continue
        table = pd.read_csv(path, dtype={"key": str})
        rows = table[(table["scope"] == "kreis") & table["check"].isin(check["pairs"])
                     & table["batch"].isin(batches)]
        absent = sorted(set(check["pairs"]) - set(rows["check"]))
        share = rows["share"].astype(float)
        zero_base = (rows["base"].astype(float) == 0) & (rows["amount"].astype(float) > 0)
        value = math.inf if zero_base.any() else (float(share.max()) if len(rows) else math.nan)
        values[arm] = {"value": value, "absent_pairs": absent}
        emit(check["id"], "ALL", arm, "max_share", value, check["bound"],
             not absent and value <= check["bound"])
    new = values.get("new")
    met = bool(new is not None and not new["absent_pairs"] and new["value"] <= check["bound"])
    return {"met": met, "bound": check["bound"], "values": values,
            "error": None if new is not None else f"{check['file']} absent in the new arm"}


def eval_fit_ratio(check, fits, batches, emit):
    out = {"bound": check["bound"], "values": {}, "missing_controls": {}}
    met = True
    for batch in batches:
        for arm, fit in fits.items():
            rows = _batch_fit(fit, batch, check["geography"])
            missing = _missing(rows, check["controls"])
            rows = rows[rows["control"].isin(check["controls"])]
            target = float(rows["target_sum"].sum())
            value = float(rows["synthetic_sum"].sum()) / target if target > 0 else math.nan
            ok = not missing and abs(value - 1.0) <= check["bound"]
            emit(check["id"], batch, arm, "synthetic_over_target", value, check["bound"], ok)
            out["values"][f"{arm}/{batch}"] = value
            if arm == "new":
                met = met and ok
                if missing:
                    out["missing_controls"][batch] = missing
    out["met"] = bool(met)
    return out


def eval_fit_gap(check, fits, batches, emit):
    measure = check["measure"]
    if measure not in ("net", "abs_error"):
        raise ValueError(f"check {check['id']}: measure must be 'net' or 'abs_error', got {measure!r}")
    out = {"bound": check["bound"], "measure": measure, "values": {}, "missing_controls": {}}
    met = True
    for batch in batches:
        for arm, fit in fits.items():
            batch_rows = _batch_fit(fit, batch)
            households = float(batch_rows.loc[batch_rows["control"] == check["households_control"],
                                              "target_sum"].sum())
            rows = batch_rows[batch_rows["geography"] == check["geography"]]
            missing = _missing(rows, check["controls"])
            if check["households_control"] not in set(batch_rows["control"]):
                missing.append(check["households_control"])
            for control in check["controls"]:
                row = rows[rows["control"] == control]
                if row.empty or not households > 0:
                    value = math.nan
                elif measure == "net":
                    value = abs(float(row["synthetic_sum"].sum() - row["target_sum"].sum())) / households
                else:
                    value = float(row["abs_error"].sum()) / households
                ok = not missing and value <= check["bound"]
                emit(check["id"], batch, arm, f"gap_over_households:{control}", value,
                     check["bound"], ok)
                out["values"][f"{arm}/{batch}/{control}"] = value
                if arm == "new":
                    met = met and ok
            if arm == "new" and missing:
                out["missing_controls"][batch] = missing
    out["met"] = bool(met)
    return out


def eval_wape_vs_base(check, fits, batches, emit):
    pattern = re.compile(check["exclude_regex"]) if check.get("exclude_regex") else None
    excluded = set(check.get("exclude_controls", ()))
    out = {"deltas": check["deltas"], "values": {}}
    met = True
    for geography, delta in check["deltas"].items():
        wape = {}
        for arm, fit in fits.items():
            rows = fit[fit["batch"].isin(batches) & (fit["geography"] == geography)
                       & (fit["target_sum"] > 0) & ~fit["control"].isin(excluded)]
            if pattern is not None:
                rows = rows[~rows["control"].astype(str).str.contains(pattern)]
            target = float(rows["target_sum"].sum())
            wape[arm] = float(rows["abs_error"].sum()) / target if target > 0 else math.nan
        limit = wape["base"] + delta
        ok = wape["new"] <= limit
        for arm in fits:
            emit(check["id"], "ALL", arm, f"wape_{geography}", wape[arm], limit, ok)
        out["values"][geography] = {"wape_base": wape["base"], "wape_new": wape["new"],
                                    "limit": limit, "met": bool(ok)}
        met = met and ok
    out["met"] = bool(met)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-diag", required=True, type=Path)
    parser.add_argument("--new-diag", required=True, type=Path)
    parser.add_argument("--checks", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    spec = json.loads(args.checks.read_text(encoding="utf-8"))
    arms = {"base": args.base_diag, "new": args.new_diag}
    fits = {arm: read_fit(diag) for arm, diag in arms.items()}
    batches = list(spec.get("batches") or sorted(fits["new"]["batch"].unique()))
    ids = [c["id"] for c in spec["checks"]]
    if len(set(ids)) != len(ids):
        raise ValueError(f"check ids must be unique: {ids}")

    rows = []

    def emit(check, batch, arm, metric, value, limit, met):
        rows.append({"check": check, "batch": batch, "arm": arm, "metric": metric,
                     "value": value, "limit": limit, "met": bool(met)})

    checks = {}
    for check in spec["checks"]:
        kind = check["kind"]
        if kind == "input_pairs":
            checks[check["id"]] = eval_input_pairs(check, arms, batches, emit)
        elif kind == "fit_ratio":
            checks[check["id"]] = eval_fit_ratio(check, fits, batches, emit)
        elif kind == "fit_gap":
            checks[check["id"]] = eval_fit_gap(check, fits, batches, emit)
        elif kind == "wape_vs_base":
            checks[check["id"]] = eval_wape_vs_base(check, fits, batches, emit)
        else:
            raise ValueError(f"check {check['id']}: unknown kind {kind!r}; known: {KINDS}")
        checks[check["id"]]["kind"] = kind

    args.out.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame(rows, columns=["check", "batch", "arm", "metric", "value", "limit", "met"])
    table.to_csv(args.out / "comparison.csv", index=False)
    verdict = {"base_diag": str(args.base_diag), "new_diag": str(args.new_diag),
               "checks_file": str(args.checks), "batches": batches, "checks": checks,
               "all_met": all(c["met"] for c in checks.values())}
    (args.out / "verdict.json").write_text(json.dumps(verdict, indent=2, default=float),
                                           encoding="utf-8")
    pd.set_option("display.width", 220)
    print(table.to_string(index=False))
    print(json.dumps({k: v["met"] for k, v in checks.items()}, indent=2))
    print(f"all_met: {verdict['all_met']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
