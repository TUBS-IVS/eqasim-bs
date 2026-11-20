"""Compare ladder arm 4 of issue #657 (the tenure rule) with arm 3, per batch, with the pre-registered checks.

Purpose: apply the checks fixed in the run manifest before the arm-4 runs (O-ten-a/b/c, N-a/b/c/d) to the
diagnostics of arm 3 and arm 4 and the two work directories. Pattern: ``compare_arm3.py`` of
``popsim-ladder-arm3-2026-11-18`` (its G3, G4 and solver-path readings are reused, see the comments at
the checks).

Inputs (``python compare_tenure_arm.py --help``): the diagnostics directories of arm 3 and arm 4
(``control_fit.csv``, ``rounding_shift.csv``, ``solver_paths.csv``, ``household_conservation.csv``), their
work directories (``<batch>/output/integerizer_paths.csv``) and the directory with the prepare logs of
``prepare_tenure_arm.py``: ``<batch>.rule_on.json`` and ``<batch>.rule_off.json`` per batch.
Outputs in ``--out``: ``comparison.csv`` (one row per batch x check: batch, check, gated, arm3_value,
arm4_value, bound, met) and ``verdict.json`` (per check met / not met, ``overall_met`` = every gated check
met, and the reported figures in the block ``reported``). Needs the eqasim-bs code on PYTHONPATH.
Exit code 0 when ``overall_met``, else 1. Issue #657.

An input that is missing for a gated check (a batch absent from ``rounding_shift.csv`` of either arm, a
level absent from ``household_conservation.csv`` or ``solver_paths.csv``) makes that check not met and is
named in ``missing_inputs`` of ``verdict.json``; a check never passes on an empty input. A missing file
raises. ``verdict.json`` also records the input paths as given, the sha256 of every input read and the
library commit(s) named in the prepare logs.

Checks (gated unless noted):

* O-ten-a: the rule-on prepare log changed only the two tenure columns of the two control tables, and the
  rule-off log changed nothing; both logs have no ``problems`` and no ``ok: false``. Value = number of
  problems and not-ok logs (both logs) + changed files (rule-off).
* O-ten-b (``HH_EXCESS_BATCHES``): household excess (synthetic minus target of the household-total control
  at ZENSUS100m, ``control_fit.csv`` scope batch) of arm 4 <= arm 3.
* O-ten-c: pooled ZENSUS100m WAPE of the two tenure controls = sum(abs_error) / sum(target_sum) over their
  two rows of ``control_fit.csv``, arm 4 <= arm 3 + ``TENURE_WAPE_SLACK``.
* N-a (one row per geography): WAPE over every control but the two tenure controls, arm 4 <= arm 3 + the
  slack of ``WAPE_SLACK``.
* N-b: G4 of arm 3 (no ZENSUS100m control with a target sum of at least 40, household total left out, with
  a net rounding shift above 0.10: ``rounding_shift.csv``): a batch that met it in arm 3 still meets it in
  arm 4. A batch that missed it in arm 3 is not gated (``gated`` false), its G4 is reported per control.
* N-c: no sequential fallback in arm 4: no non-simultaneous parent at any level of ``solver_paths.csv``;
  every simultaneous call controlled, OPTIMAL or FEASIBLE and converged (the stage's own reading,
  ``solver_path_gate.summarize_controlled_calls``). Value = number of violations.
* N-d (one row per level): household conservation not worse, ``abs_deviation_sum`` of
  ``household_conservation.csv`` arm 4 <= arm 3.

Reported without a bound: ``hh_excess``, ``zone_shortfall`` and ``row_shortfall`` of the solver paths of
both arms, the 100 m tenure targets and synthetic sums, G4 per control.
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import pandas as pd

from braunschweig.popsim import control_spec
from braunschweig.popsim.stage import solver_path_gate

# Pre-registered bounds (run manifest ``docs/runs/popsim-ladder-tenure-2026-11-18.yml``).
# O-ten-c: pooled ZENSUS100m WAPE of the two tenure controls may rise by at most this (absolute, 2 pp).
TENURE_WAPE_SLACK = 0.02
# N-a: WAPE per geography over the other controls may rise by at most this; the values of G3 of arm 3.
WAPE_SLACK = {"ZENSUS100m": 0.02, "ZENSUS1km": 0.02, "KREIS": 0.01}
# N-b: G4 as in compare_arm3.py: net rounding shift bound and smallest target (read from rounding_shift.csv).
G4_BOUND = 0.10
G4_MIN_TARGET = 40.0
# O-ten-b is pre-registered for this batch only (household excess is the case of batch_000).
HH_EXCESS_BATCHES = ("batch_000",)
LEVEL_100M = "ZENSUS100m"
HOUSEHOLD_TOTAL_PREFIX = "Insgesamt_Haushalte"
BUILDING_TYPE_PREFIX = "building_type_"
TENURE_BASES = tuple(name for name, _ in control_spec._TIER2_TENURE_ENTRIES)
SOLVER_LEVELS = ("KREIS", "ZENSUS1km", "ZENSUS100m")
# Float noise allowance of every "<= bound" test, so that a value exactly at its bound (0.15 + 0.02 against
# 0.17) is met whatever the last binary digit says. Far below the resolution of any figure compared.
BOUND_TOLERANCE = 1e-9
ARMS = ("arm3", "arm4")


def is_tenure(control: pd.Series) -> pd.Series:
    """Rows of ``control_fit.csv`` / ``rounding_shift.csv`` that belong to a tenure control."""
    return control.str.startswith(TENURE_BASES)


def batch_fit(diag: Path, batch: str) -> pd.DataFrame:
    """Batch-scope rows of ``control_fit.csv``."""
    fit = pd.read_csv(diag / "control_fit.csv")
    return fit[(fit["scope"] == "batch") & (fit["batch"] == batch)]


def wape(rows: pd.DataFrame) -> float:
    """sum(abs_error) / sum(target_sum); NaN for no target."""
    total = rows["target_sum"].sum()
    return float(rows["abs_error"].sum() / total) if total > 0 else math.nan


def other_controls_wape(fit: pd.DataFrame, geography: str) -> float:
    """WAPE of one geography over its controls but the tenure ones.

    The definition of ``compare_arm3.py`` (G3: controls with a target above 0, without ``building_type_*``,
    sum of |synthetic - target| over sum of targets) with the two tenure controls also left out.
    """
    rows = fit[(fit["geography"] == geography) & (fit["target_sum"] > 0)]
    rows = rows[~rows["control"].str.startswith(BUILDING_TYPE_PREFIX) & ~is_tenure(rows["control"])]
    return wape(rows)


def household_excess(fit: pd.DataFrame) -> float:
    """Synthetic minus target of the household-total control at ZENSUS100m."""
    rows = fit[fit["control"].str.startswith(HOUSEHOLD_TOTAL_PREFIX) & (fit["geography"] == LEVEL_100M)]
    if len(rows) != 1:
        raise ValueError(f"expected one household-total control at {LEVEL_100M}, found {len(rows)}")
    return float(rows["synthetic_sum"].iloc[0] - rows["target_sum"].iloc[0])


def tenure_rows(fit: pd.DataFrame) -> pd.DataFrame:
    """The two tenure rows at ZENSUS100m, WITHOUT the ``target_sum > 0`` filter of the G3 reading.

    O-ten-c is pre-registered as the plain pooled ratio over exactly these two rows. A tenure control with
    a zero target keeps its error in the numerator: the rule moves owners into renters, and error on a
    control without a target is what the check has to see, not hide. Both rows must exist.
    """
    rows = fit[(fit["geography"] == LEVEL_100M) & is_tenure(fit["control"])]
    if len(rows) != len(TENURE_BASES):
        raise ValueError(f"expected {len(TENURE_BASES)} tenure controls at {LEVEL_100M}, found {len(rows)}")
    return rows


def g4_per_control(diag: Path, batch: str):
    """``{control: met}`` from ``rounding_shift.csv`` (``within_bound`` as ``compare_arm3.py`` G4 reads it).

    ``None`` when the batch has no row at all: an empty table would read as "no miss" and pass N-b.
    """
    shift = pd.read_csv(diag / "rounding_shift.csv")
    shift = shift[shift["batch"] == batch]
    if shift.empty:
        return None
    return {r["control"]: bool(r["within_bound"]) for _, r in shift.iterrows()}


def conservation_deviation(diag: Path, batch: str, level: str):
    """``abs_deviation_sum`` of one batch and level in ``household_conservation.csv``; ``None`` if absent."""
    table = pd.read_csv(diag / "household_conservation.csv")
    rows = table[(table["batch"] == batch) & (table["level"] == level)]
    return float(rows["abs_deviation_sum"].iloc[0]) if len(rows) else None


def solver_path_figures(diag: Path, work: Path, batch: str) -> dict:
    """The solver-path reading of one batch: non-simultaneous parents and the controlled-call totals.

    ``proof_error`` is the message of ``summarize_controlled_calls`` when the record does not prove a
    controlled run (as ``compare_arm3.py``), else ``None``.
    """
    solver = pd.read_csv(diag / "solver_paths.csv")
    solver = solver[(solver["batch"] == batch) & solver["level"].isin(SOLVER_LEVELS)]
    figures = {"non_simultaneous": {r["level"]: int(r["non_simultaneous"]) for _, r in solver.iterrows()},
               "proof_error": None, "totals": None}
    figures["missing_levels"] = [level for level in SOLVER_LEVELS if level not in figures["non_simultaneous"]]
    path = work / batch / "output" / "integerizer_paths.csv"
    if not path.is_file():
        figures["proof_error"] = f"{path} is missing"
        return figures
    record = pd.read_csv(path, dtype=str, keep_default_na=False)
    record["batch"] = batch
    try:
        figures["totals"] = solver_path_gate.summarize_controlled_calls(record, "controlled")
    except RuntimeError as error:
        figures["proof_error"] = str(error)
    return figures


def solver_violations(figures: dict) -> int:
    """Number of violations of N-c in one batch.

    A record that does not prove a controlled run counts once; so does every level of ``SOLVER_LEVELS``
    that ``solver_paths.csv`` has no row for.
    """
    if figures["proof_error"] is not None or figures["totals"] is None:
        return 1 + len(figures["missing_levels"])
    totals = figures["totals"]
    simultaneous = figures["non_simultaneous"]
    calls = totals["controlled_simul_calls"]
    return (sum(simultaneous.values()) + len(figures["missing_levels"]) + (1 if calls == 0 else 0)
            + abs(calls - totals["search_calls"]) + totals["controlled_non_success_calls"]
            + totals["search_not_converged_calls"])


def read_prepare_log(directory: Path, batch: str, rule: str) -> dict:
    return json.loads((directory / f"{batch}.rule_{rule}.json").read_text(encoding="utf-8"))


def clean(value):
    """JSON-safe value: NaN to None, numpy scalars to Python."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "item"):
        value = value.item()
    return None if isinstance(value, float) and math.isnan(value) else value


def at_most(value: float, bound: float) -> bool:
    """``value <= bound`` within ``BOUND_TOLERANCE``; NaN (no value) is never within a bound."""
    return bool(value <= bound + BOUND_TOLERANCE)


def log_problem_count(log: dict) -> int:
    """Problems named in a prepare log, plus one for ``ok: false``; a log without ``problems`` raises."""
    return len(log["problems"]) + (0 if log.get("ok", True) else 1)


def evaluate(args) -> tuple:
    """``(rows, reported, missing)``: the comparison rows, the reported figures, the missing inputs."""
    rows, reported, missing = [], {}, []

    def add(batch, check, gated, arm3, arm4, bound, met):
        rows.append({"batch": batch, "check": check, "gated": gated, "arm3_value": arm3,
                     "arm4_value": arm4, "bound": bound, "met": bool(met)})

    for batch in args.batches:
        fit3, fit4 = batch_fit(args.base_diag, batch), batch_fit(args.new_diag, batch)
        log_on = read_prepare_log(args.prepare_logs, batch, "on")
        log_off = read_prepare_log(args.prepare_logs, batch, "off")
        off_changed = sum(1 for entry in log_off["files"].values() if entry["changed"])
        # Both logs must be clean: no problems and no ok:false, not only "no file changed".
        n_bad = log_problem_count(log_on) + log_problem_count(log_off) + off_changed
        add(batch, "O-ten-a", True, math.nan, n_bad, 0, n_bad == 0)

        if batch in HH_EXCESS_BATCHES:
            e3, e4 = household_excess(fit3), household_excess(fit4)
            add(batch, "O-ten-b", True, e3, e4, e3, at_most(e4, e3))
        t3, t4 = wape(tenure_rows(fit3)), wape(tenure_rows(fit4))
        add(batch, "O-ten-c", True, t3, t4, t3 + TENURE_WAPE_SLACK, at_most(t4, t3 + TENURE_WAPE_SLACK))
        for geography, slack in WAPE_SLACK.items():
            w3, w4 = other_controls_wape(fit3, geography), other_controls_wape(fit4, geography)
            add(batch, f"N-a_{geography}", True, w3, w4, w3 + slack, at_most(w4, w3 + slack))

        g4_3, g4_4 = g4_per_control(args.base_diag, batch), g4_per_control(args.new_diag, batch)
        if g4_3 is None or g4_4 is None:
            for arm, g4 in zip(ARMS, (g4_3, g4_4)):
                if g4 is None:
                    missing.append(f"{batch}: no rows in rounding_shift.csv of {arm}")
            add(batch, "N-b", True, math.nan if g4_3 is None else sum(not v for v in g4_3.values()),
                math.nan if g4_4 is None else sum(not v for v in g4_4.values()), 0, False)
            g4_3, g4_4 = g4_3 or {}, g4_4 or {}
        else:
            miss3, miss4 = sum(not v for v in g4_3.values()), sum(not v for v in g4_4.values())
            gated = miss3 == 0
            add(batch, "N-b", gated, miss3, miss4, 0 if gated else math.nan, miss4 == 0 or not gated)

        paths3 = solver_path_figures(args.base_diag, args.base_work, batch)
        paths4 = solver_path_figures(args.new_diag, args.new_work, batch)
        for arm, paths in zip(ARMS, (paths3, paths4)):
            missing.extend(f"{batch}: no row of level {level} in solver_paths.csv of {arm}"
                           for level in paths["missing_levels"])
        v4 = solver_violations(paths4)
        add(batch, "N-c", True, solver_violations(paths3), v4, 0, v4 == 0)

        # The full level set must be present in BOTH arms; a missing level is not met and named.
        for level in SOLVER_LEVELS:
            d3 = conservation_deviation(args.base_diag, batch, level)
            d4 = conservation_deviation(args.new_diag, batch, level)
            for arm, d in zip(ARMS, (d3, d4)):
                if d is None:
                    missing.append(f"{batch}: no row of level {level} in household_conservation.csv of {arm}")
            if d3 is None or d4 is None:
                add(batch, f"N-d_{level}", True, math.nan if d3 is None else d3,
                    math.nan if d4 is None else d4, math.nan, False)
            else:
                add(batch, f"N-d_{level}", True, d3, d4, d3, at_most(d4, d3))

        def tenure_sums(fit):
            return {r["control"]: {"target_sum": r["target_sum"], "synthetic_sum": r["synthetic_sum"]}
                    for _, r in tenure_rows(fit).iterrows()}

        def reported_totals(figures):
            totals = figures["totals"] or {}
            return {k: totals.get(k) for k in ("hh_excess", "zone_shortfall", "row_shortfall")}

        reported[batch] = {
            "tenure_100m_arm3": tenure_sums(fit3), "tenure_100m_arm4": tenure_sums(fit4),
            "solver_path_sums_arm3": reported_totals(paths3), "solver_path_sums_arm4": reported_totals(paths4),
            "solver_path_proof_error_arm4": paths4["proof_error"],
            "g4_per_control_arm3": g4_3, "g4_per_control_arm4": g4_4,
            "rule_report": log_on["rule_report"]}
    return rows, reported, missing


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def input_record(args) -> dict:
    """The inputs of the comparison: the paths as given and the sha256 of every file read."""
    files = {}
    for diag in (args.base_diag, args.new_diag):
        for name in ("control_fit.csv", "rounding_shift.csv", "solver_paths.csv", "household_conservation.csv"):
            files[str(diag / name)] = diag / name
    for work in (args.base_work, args.new_work):
        for batch in args.batches:
            path = work / batch / "output" / "integerizer_paths.csv"
            files[str(path)] = path
    for batch in args.batches:
        for rule in ("on", "off"):
            path = args.prepare_logs / f"{batch}.rule_{rule}.json"
            files[str(path)] = path
    commits = sorted({read_prepare_log(args.prepare_logs, b, r).get("library_commit")
                      for b in args.batches for r in ("on", "off")}, key=str)
    return {"paths": {key: str(getattr(args, key.replace("-", "_"))) for key in
                      ("base-diag", "new-diag", "base-work", "new-work", "prepare-logs", "out")},
            "files_sha256": {name: sha256(path) if path.is_file() else None for name, path in files.items()},
            "library_commits_in_prepare_logs": commits}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-diag", required=True, type=Path, help="diagnostics directory of arm 3")
    parser.add_argument("--new-diag", required=True, type=Path, help="diagnostics directory of arm 4")
    parser.add_argument("--base-work", required=True, type=Path, help="work directory of arm 3")
    parser.add_argument("--new-work", required=True, type=Path, help="work directory of arm 4")
    parser.add_argument("--prepare-logs", required=True, type=Path,
                        help="directory with <batch>.rule_on.json and <batch>.rule_off.json")
    parser.add_argument("--batches", nargs="+", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    rows, reported, missing = evaluate(args)
    args.out.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame(rows, columns=["batch", "check", "gated", "arm3_value", "arm4_value", "bound", "met"])
    table.to_csv(args.out / "comparison.csv", index=False)
    overall = bool(table[table["gated"]]["met"].all()) and not missing
    verdict = {
        "bounds": {"tenure_wape_slack": TENURE_WAPE_SLACK, "wape_slack": WAPE_SLACK, "g4_bound": G4_BOUND,
                   "g4_min_target": G4_MIN_TARGET, "hh_excess_batches": list(HH_EXCESS_BATCHES),
                   "bound_tolerance": BOUND_TOLERANCE},
        "checks": {f"{r['batch']}/{r['check']}": {"gated": r["gated"], "met": r["met"]} for r in rows},
        "missing_inputs": missing, "overall_met": overall, "inputs": input_record(args),
        "reported": reported}
    (args.out / "verdict.json").write_text(json.dumps(clean(verdict), indent=2), encoding="utf-8")
    pd.set_option("display.width", 200)
    print(table.to_string(index=False))
    print(f"overall_met: {overall}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
