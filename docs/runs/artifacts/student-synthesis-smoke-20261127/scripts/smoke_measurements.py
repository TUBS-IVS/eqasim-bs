"""Measurements of the one-batch smoke of the student synthesis bundle (#140, Task 10 Step 3).

Reads, for ONE batch folder, the ON run (the bundle, branch code) and the PROD run (production
run popsim100_20261120, same batch, same geo cross-walk) and writes side-by-side CSVs for the
Braunschweig (Kreis 03101) part of the batch:

1. ``fit_03101.csv``: per control the target, the synthetic count and the WAPE in the 03101 part
   (KREIS controls: the 03101 row; 100 m and 1 km controls: the cells of the 03101 part, WAPE =
   sum over cells |synthetic - target| / sum target, cells with a target or synthetic households
   counted as ``control_fit`` does), ON next to PROD, plus the pre-registered "a hard control
   loses its fit" flag (see ``HARD_MIN_IMPORTANCE`` and the two bounds below).
   ``fit_batch_check.csv`` re-computes the batch-level synthetic sums and compares them with the
   ``control_fit.csv`` of the batch diagnostics (this proves the control specification used here,
   including the reconstructed PROD one, is the one the diagnostics evaluated).
   ``tier_wape_batch.csv`` / ``tier_wape_03101.csv``: WAPE per importance tier.
2. ``new_entries_03101.csv``: the four new student entries per label (target vs synthetic).
3. ``student_donors_weights.csv``: distinct seed households with a student, weight dispersion of
   their expansion weights and the closest observable of the maximum expansion factor.
4. ``students_household_type_03101.csv``: students of the 03101 part by household type and
   "lives with a person 18 years older" (definitions: ``student_measurement._household_labels``)
   against the SrV Braunschweig row.
``smoke_measurements_summary.json`` collects the headline numbers.

PROD controls.csv is not in the PROD folder: the PROD specification is rebuilt from the PROD
``control_fit.csv`` (names, geographies, importance) with the expressions of the ON
``controls.csv``; the two PROD-only controls (education_18plus) use the age-band expression of
``braunschweig.popsim.kreis_attribute_control`` (``education_flag`` with age >= 18). The
batch check above fails the run if any recomputed batch sum disagrees with the diagnostics.

Usage (eqasim environment, repository root as working directory)::

    python docs/runs/artifacts/student-synthesis-smoke-20261127/scripts/smoke_measurements.py \\
        --root <folder with on/ and prod/ batch folders> \\
        --diag-dir docs/runs/artifacts/student-synthesis-smoke-20261127 \\
        --srv eqasim-data/data/braunschweig/srv/srv2023_student_parameters_by_area.csv \\
        --kreis-targets <student_kreis_targets.csv> \\
        --out-dir docs/runs/artifacts/student-synthesis-smoke-20261127
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import (BatchFolder,
                                                                         household_incidence)
from braunschweig.analysis.student_measurement import _household_labels
from braunschweig.calibration.mid_student_reference import STUDENT_P_TAET
from braunschweig.calibration.student_behaviour_labels import MIN_ADULT_AGE

LOG = logging.getLogger("smoke_measurements")

#: Hard tiers = importance >= 2000 (control_spec IMPORTANCE_PROFILES: anchor 1e9, son 20000,
#: six / employed / kreis_hard 2000; uniform 1000 and grid_shape 500, age / hhtype 200 are soft).
HARD_MIN_IMPORTANCE = 2000.0
#: "A hard control loses its fit" (fixed BEFORE the numbers were read; ASSUMPTION, owner may
#: tighten): WAPE in the 03101 part more than this many percentage points above production ...
LOSS_MIN_DIFFERENCE_PP = 2.0
#: ... AND above this WAPE (percent).
LOSS_MIN_WAPE_PERCENT = 5.0
#: Weight inflation thresholds reported next to the maximum expansion factor of the settings.
INFLATION_THRESHOLDS = (10.0, 50.0)

KREIS_CODE = "03101"
NEW_ENTRIES = ("student_education_18_29", "student_education_30plus", "student_work_leg",
               "student_season_ticket")
GEOGRAPHIES = ("KREIS", "ZENSUS1km", "ZENSUS100m")
EDUCATION_18PLUS = {
    "education_18plus_edu_KREIS_target":
        "(persons.education_flag == 'edu') & (persons.HP_ALTER >= 18)",
    "education_18plus_noedu_KREIS_target":
        "(persons.education_flag == 'noedu') & (persons.HP_ALTER >= 18)",
}


# --------------------------------------------------------------------------- loading
def _on_spec(on_dir: Path) -> pd.DataFrame:
    return pd.read_csv(on_dir / "controls.csv", comment="#")


def _prod_spec(on_spec: pd.DataFrame, diag_prod: pd.DataFrame) -> pd.DataFrame:
    """The PROD control specification (see module docstring)."""
    prod = diag_prod[diag_prod["scope"] == "batch"][["control", "geography", "importance"]]
    prod = prod.rename(columns={"control": "target"})
    known = on_spec.set_index("target")
    rows = []
    for item in prod.itertuples():
        if item.target in known.index:
            source = known.loc[item.target]
            if source["geography"] != item.geography or float(source["importance"]) != \
                    float(item.importance):
                raise ValueError("control %s: geography/importance differ between ON controls "
                                 "and PROD diagnostics" % item.target)
            rows.append({"target": item.target, "geography": item.geography,
                         "seed_table": source["seed_table"], "importance": item.importance,
                         "control_field": source["control_field"],
                         "expression": source["expression"]})
        elif item.target in EDUCATION_18PLUS:
            rows.append({"target": item.target, "geography": item.geography,
                         "seed_table": "persons", "importance": item.importance,
                         "control_field": item.target[:-len("_target")],
                         "expression": EDUCATION_18PLUS[item.target]})
        else:
            raise ValueError("PROD control %s has no expression source" % item.target)
    return pd.DataFrame(rows)


class Side:
    """One run (ON or PROD) of the batch: seed, expanded output, controls and totals."""

    def __init__(self, name: str, folder: Path, spec: pd.DataFrame):
        self.name = name
        self.folder = folder
        data = folder / "data"
        households = pd.read_csv(data / "seed_households.csv")
        persons = pd.read_csv(data / "seed_persons.csv")
        persons["H_ID"] = pd.to_numeric(persons["H_ID"], errors="raise").astype(
            households["H_ID"].dtype)
        self.households, self.persons, self.spec = households, persons, spec
        self.expanded = pd.read_csv(folder / "final_expanded_household_ids.csv")
        self.synthetic_households = pd.read_csv(folder / "synthetic_households.csv")
        self.synthetic_persons = pd.read_csv(folder / "synthetic_persons.csv")
        if not (self.synthetic_households["H_ID"].to_numpy()
                == self.expanded["H_ID"].to_numpy()).all():
            raise ValueError("%s: synthetic_households and expanded rows disagree in H_ID order"
                             % name)
        self.crosswalk = pd.read_csv(data / "geo_cross_walk.csv", dtype=str)
        self.totals = {}
        for geography in GEOGRAPHIES:
            frame = pd.read_csv(data / ("control_totals_%s.csv" % geography),
                                dtype={geography: str, "KREIS": str}, index_col=geography)
            self.totals[geography] = frame
        batch = BatchFolder(
            name=name, path=folder, seed_households=households, seed_persons=persons,
            control_spec=spec, expanded=self.expanded, total_hh_control="", household_id_col="H_ID",
            weight_col="H_GEW", geographies=GEOGRAPHIES, has_store=False, seed_fingerprint="")
        self.incidence = household_incidence(batch)
        self.zone = {g: self.expanded[g].astype(str) for g in GEOGRAPHIES if g != "KREIS"}
        self.zone["KREIS"] = self.expanded["KREIS"].map(lambda k: "%05d" % int(k))
        self.in_part = (self.zone["KREIS"] == KREIS_CODE).to_numpy()
        # persons of the synthetic population: all seed members of every expanded household
        self.n_synthetic_persons_expected = int(
            self.persons.groupby("H_ID").size().reindex(self.expanded["H_ID"]).sum())
        if self.n_synthetic_persons_expected != len(self.synthetic_persons):
            raise ValueError("%s: synthetic_persons has %d rows, the seed members of the "
                             "expanded households are %d" % (
                                 name, len(self.synthetic_persons),
                                 self.n_synthetic_persons_expected))

    def zone_counts(self, control: str, mask: np.ndarray, geography: str) -> pd.Series:
        values = self.incidence[control].reindex(self.expanded["H_ID"]).to_numpy()
        return pd.Series(values[mask]).groupby(self.zone[geography][mask].to_numpy()).sum()

    def part_cells(self, geography: str) -> pd.Index:
        """Zones of ``geography`` in the 03101 part: crosswalk cells of 03101 plus used cells."""
        if geography == "KREIS":
            return pd.Index([KREIS_CODE])
        mapping = self.crosswalk[[geography, "KREIS"]].drop_duplicates()
        if mapping[geography].duplicated().any():
            raise ValueError("%s cells span several Kreise in the crosswalk" % geography)
        cells = mapping.loc[mapping["KREIS"] == KREIS_CODE, geography]
        used = pd.Index(self.zone[geography][self.in_part].unique())
        return pd.Index(cells).union(used)


# --------------------------------------------------------------------------- fit
def _fit_row(side: Side, control, scope: str) -> dict:
    """Target, synthetic and WAPE of one control for ``scope`` ('part' = 03101, 'batch')."""
    geography = control.geography
    totals = side.totals[geography]
    field = control.control_field
    if field not in totals.columns:
        raise ValueError("%s: totals of %s lack %s" % (side.name, geography, field))
    if scope == "part":
        zones = side.part_cells(geography)
        mask = side.in_part
    else:
        zones = totals.index.union(pd.Index(side.zone[geography].unique()))
        mask = np.ones(len(side.expanded), dtype=bool)
    target = totals[field].reindex(zones).fillna(0.0)
    synthetic = side.zone_counts(control.target, mask, geography).reindex(zones).fillna(0.0)
    abs_error = float((synthetic - target).abs().sum())
    target_sum = float(target.sum())
    synthetic_sum = float(synthetic.sum())
    return {"control": control.target, "geography": geography,
            "importance": float(control.importance), "n_zones": int(len(zones)),
            "target_sum": target_sum, "synthetic_sum": synthetic_sum, "abs_error": abs_error,
            "bias": synthetic_sum / target_sum - 1.0 if target_sum else float("nan"),
            "wape": abs_error / target_sum if target_sum else float("nan")}


def fit_table(side: Side, scope: str) -> pd.DataFrame:
    return pd.DataFrame([_fit_row(side, c, scope) for c in side.spec.itertuples()])


def batch_check(side: Side, diag: pd.DataFrame) -> pd.DataFrame:
    """Recomputed batch-level sums against the diagnostics' control_fit (scope 'batch')."""
    mine = fit_table(side, "batch").set_index("control")
    theirs = diag[diag["scope"] == "batch"].set_index("control")
    if set(mine.index) != set(theirs.index):
        raise ValueError("%s: control sets differ from the diagnostics" % side.name)
    out = pd.DataFrame({
        "side": side.name,
        "target_sum_here": mine["target_sum"], "target_sum_diag": theirs["target_sum"],
        "synthetic_sum_here": mine["synthetic_sum"],
        "synthetic_sum_diag": theirs["synthetic_sum"],
        "abs_error_here": mine["abs_error"], "abs_error_diag": theirs["abs_error"]})
    out["max_abs_deviation"] = np.maximum.reduce([
        (out["target_sum_here"] - out["target_sum_diag"]).abs(),
        (out["synthetic_sum_here"] - out["synthetic_sum_diag"]).abs(),
        (out["abs_error_here"] - out["abs_error_diag"]).abs()])
    worst = float(out["max_abs_deviation"].max())
    LOG.info("[batch check] %s: %d controls, worst deviation from the diagnostics %.3g",
             side.name, len(out), worst)
    if worst > 1e-6 * max(1.0, float(out["target_sum_diag"].max())):
        raise ValueError("%s: recomputed batch sums disagree with the diagnostics (worst %g); "
                         "the control specification is not the evaluated one"
                         % (side.name, worst))
    return out.reset_index()


def side_by_side(on: pd.DataFrame, prod: pd.DataFrame) -> pd.DataFrame:
    keys = ["control", "geography", "importance"]
    merged = on.merge(prod, on=keys, how="outer", suffixes=("_on", "_prod"), indicator=True)
    merged["in"] = merged["_merge"].map({"both": "both", "left_only": "on_only",
                                         "right_only": "prod_only"}).astype(str)
    merged = merged.drop(columns="_merge")
    merged["hard"] = merged["importance"] >= HARD_MIN_IMPORTANCE
    merged["wape_diff_pp"] = 100.0 * (merged["wape_on"] - merged["wape_prod"])
    merged["hard_control_loses_fit"] = (
        merged["hard"] & (merged["in"] == "both")
        & (merged["wape_diff_pp"] > LOSS_MIN_DIFFERENCE_PP)
        & (100.0 * merged["wape_on"] > LOSS_MIN_WAPE_PERCENT))
    return merged


def tier_table(frame: pd.DataFrame, suffix_on: str, suffix_prod: str, common_only: bool):
    rows = []
    for importance, group in frame.groupby("importance"):
        entry = {"importance": importance}
        for label, suffix in (("on", suffix_on), ("prod", suffix_prod)):
            part = group[group["in"] == "both"] if common_only else group
            part = part[part["abs_error" + suffix].notna()]
            entry["n_controls_" + label] = int(len(part))
            target = float(part["target_sum" + suffix].sum())
            entry["wape_" + label] = (float(part["abs_error" + suffix].sum()) / target
                                      if target else float("nan"))
        entry["universe"] = "controls on both sides" if common_only else "all controls of the side"
        rows.append(entry)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- students
def _is_student(persons: pd.DataFrame) -> pd.Series:
    code = pd.to_numeric(persons["P_TAET"], errors="coerce")
    age = pd.to_numeric(persons["HP_ALTER"], errors="coerce")
    return (code == STUDENT_P_TAET) & (age >= MIN_ADULT_AGE)


def donors_and_weights(side: Side) -> list:
    """Student households used, their weight dispersion and the expansion-factor observable."""
    seed_students = _is_student(side.persons).groupby(side.persons["H_ID"]).sum()
    student_hh = seed_students[seed_students > 0].index
    sum_h_gew = float(side.households["H_GEW"].sum())
    h_gew = side.households.set_index("H_ID")["H_GEW"]
    rows = []
    for scope, mask in (("part_03101", side.in_part),
                        ("batch", np.ones(len(side.expanded), dtype=bool))):
        copies = side.expanded.loc[mask, "H_ID"].value_counts()
        n_expanded = int(mask.sum())
        expected = n_expanded * h_gew.reindex(copies.index) / sum_h_gew
        ratio = copies / expected
        for group, ids in (("households_with_student", copies.index.isin(student_hh)),
                           ("all_households", np.ones(len(copies), dtype=bool))):
            c, r = copies[ids], ratio[ids]
            row = {"side": side.name, "scope": scope, "group": group,
                   "n_expanded_households": n_expanded,
                   "distinct_seed_households_used": int(len(c)),
                   "copies_mean": float(c.mean()), "copies_std_population": float(c.std(ddof=0)),
                   "copies_cv": float(c.std(ddof=0) / c.mean()), "copies_max": int(c.max()),
                   "copies_max_over_mean": float(c.max() / c.mean()),
                   "inflation_ratio_max": float(r.max()),
                   "inflation_ratio_median": float(r.median()),
                   "households_at_inflation_100": int((r >= 100.0).sum()),
                   "households_at_inflation_100_note": (
                       "copies >= 100 x expected copies N_part x H_GEW / sum(H_GEW over the "
                       "seed); proxy, PopulationSim's float weights are not written")}
            for threshold in INFLATION_THRESHOLDS:
                row["households_at_inflation_%d" % threshold] = int((r >= threshold).sum())
            rows.append(row)
        # expanded student persons in the scope
        n_student_persons = int(seed_students.reindex(side.expanded.loc[mask, "H_ID"]).sum())
        rows.append({"side": side.name, "scope": scope, "group": "student_persons_expanded",
                     "n_expanded_households": n_expanded,
                     "distinct_seed_households_used": int(len(student_hh)),
                     "copies_mean": float(n_student_persons)})
        rows[-1]["households_at_inflation_100_note"] = (
            "copies_mean column = number of expanded adult students (P_TAET 10, age 18+); "
            "distinct_seed_households_used = student households in the whole seed")
    return rows


def synthetic_person_frame(side: Side) -> pd.DataFrame:
    """Synthetic persons of the 03101 part: the seed members of each expanded household."""
    expanded = pd.DataFrame({"H_ID": side.expanded["H_ID"].to_numpy(),
                             "household_id": side.synthetic_households["household_id"].to_numpy()
                             })[side.in_part]
    persons = expanded.merge(side.persons[["H_ID", "HP_ALTER", "P_TAET"]], on="H_ID", how="left",
                             validate="m:m")
    persons["person_id"] = np.arange(len(persons))
    persons["age"] = pd.to_numeric(persons["HP_ALTER"], errors="coerce")
    return persons


def household_type_table(side: Side, srv: pd.DataFrame) -> pd.DataFrame:
    persons = synthetic_person_frame(side)
    students = persons.loc[_is_student(persons)].reset_index(drop=True)
    labels = _household_labels(students, persons)
    rows = []
    for parameter, series in labels.items():
        known = series.dropna()
        for label, count in known.value_counts().items():
            rows.append({"side": side.name, "parameter": parameter, "label": label,
                         "n": int(count), "n_base": int(len(known)),
                         "n_students": int(len(students)), "n_undetermined": int(series.isna().sum()),
                         "share": float(count / len(known))})
    return pd.DataFrame(rows)


def students_vs_srv(on: pd.DataFrame, prod: pd.DataFrame, srv: pd.DataFrame) -> pd.DataFrame:
    row = srv[(srv["area"] == "braunschweig")
              & srv["parameter"].isin(["household_type", "lives_with_person_18_years_older"])]
    row = row[["parameter", "label", "n_unweighted", "share", "se_share"]].rename(columns={
        "n_unweighted": "srv_n_unweighted", "share": "srv_share", "se_share": "srv_se_share"})
    key = ["parameter", "label"]
    out = row.merge(on.drop(columns="side").add_suffix("_on").rename(
        columns={"parameter_on": "parameter", "label_on": "label"}), on=key, how="left")
    out = out.merge(prod.drop(columns="side").add_suffix("_prod").rename(
        columns={"parameter_prod": "parameter", "label_prod": "label"}), on=key, how="left")
    for side in ("on", "prod"):
        out["difference_in_srv_se_" + side] = (out["share_" + side] - out["srv_share"]) \
            / out["srv_se_share"]
    return out


# --------------------------------------------------------------------------- main
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--diag-dir", required=True, type=Path)
    parser.add_argument("--srv", required=True, type=Path)
    parser.add_argument("--kreis-targets", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    diag_on = pd.read_csv(args.diag_dir / "diag_on" / "control_fit.csv")
    diag_prod = pd.read_csv(args.diag_dir / "diag_prod" / "control_fit.csv")
    on_spec = _on_spec(args.root / "on")
    prod_spec = _prod_spec(on_spec, diag_prod)
    on = Side("on", args.root / "on", on_spec)
    prod = Side("prod", args.root / "prod", prod_spec)
    if not (on.crosswalk.equals(prod.crosswalk)):
        raise ValueError("ON and PROD geo cross-walks differ")
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    checks = pd.concat([batch_check(on, diag_on), batch_check(prod, diag_prod)])
    checks.to_csv(out / "fit_batch_check.csv", index=False)

    fit = side_by_side(fit_table(on, "part"), fit_table(prod, "part"))
    fit.to_csv(out / "fit_03101.csv", index=False)
    lost = fit[fit["hard_control_loses_fit"]]
    LOG.info("[fit 03101] hard controls %d, both sides %d, losing the fit %d",
             int(fit["hard"].sum()), int((fit["hard"] & (fit["in"] == "both")).sum()), len(lost))

    batch_on, batch_prod = fit_table(on, "batch"), fit_table(prod, "batch")
    batch = side_by_side(batch_on, batch_prod)
    pd.concat([tier_table(batch, "_on", "_prod", False).assign(scope="batch"),
               tier_table(batch, "_on", "_prod", True).assign(scope="batch")]
              ).to_csv(out / "tier_wape_batch.csv", index=False)
    pd.concat([tier_table(fit, "_on", "_prod", False).assign(scope="part_03101"),
               tier_table(fit, "_on", "_prod", True).assign(scope="part_03101")]
              ).to_csv(out / "tier_wape_03101.csv", index=False)

    # the four new entries
    kreis_targets = pd.read_csv(args.kreis_targets, dtype={"ars5": str})
    kreis_targets = kreis_targets[kreis_targets["ars5"] == KREIS_CODE]
    entries = fit[fit["control"].str.startswith(NEW_ENTRIES) & (fit["in"] == "on_only")].copy()
    entries["entry"] = entries["control"].map(
        lambda name: next(e for e in NEW_ENTRIES if name.startswith(e)))
    entries["label"] = [n[len(e) + 1:-len("_KREIS_target")] for n, e
                        in zip(entries["control"], entries["entry"])]
    entries = entries.merge(kreis_targets[["control", "category", "target_persons",
                                           "universe_persons"]].rename(
        columns={"control": "entry", "category": "label",
                 "target_persons": "kreis_target_persons_03101",
                 "universe_persons": "kreis_universe_persons_03101"}),
        on=["entry", "label"], how="left")
    entries["difference"] = entries["synthetic_sum_on"] - entries["target_sum_on"]
    entries[["entry", "label", "control", "importance", "target_sum_on", "synthetic_sum_on",
             "difference", "bias_on", "wape_on", "kreis_target_persons_03101",
             "kreis_universe_persons_03101"]].to_csv(out / "new_entries_03101.csv", index=False)

    donors = pd.DataFrame(donors_and_weights(on) + donors_and_weights(prod))
    donors.to_csv(out / "student_donors_weights.csv", index=False)

    srv = pd.read_csv(args.srv, comment="#")
    household = students_vs_srv(household_type_table(on, srv), household_type_table(prod, srv),
                                srv)
    household.to_csv(out / "students_household_type_03101.csv", index=False)

    summary = {
        "batch_check_worst_deviation": float(checks["max_abs_deviation"].max()),
        "hard_controls": int(fit["hard"].sum()),
        "hard_controls_on_both_sides": int((fit["hard"] & (fit["in"] == "both")).sum()),
        "hard_controls_losing_fit": lost["control"].tolist(),
        "rule": {"hard_min_importance": HARD_MIN_IMPORTANCE,
                 "min_wape_difference_pp": LOSS_MIN_DIFFERENCE_PP,
                 "min_wape_percent": LOSS_MIN_WAPE_PERCENT},
        "controls_on_only": fit.loc[fit["in"] == "on_only", "control"].tolist(),
        "controls_prod_only": fit.loc[fit["in"] == "prod_only", "control"].tolist(),
        "n_expanded_households_part_on": int(on.in_part.sum()),
        "n_expanded_households_part_prod": int(prod.in_part.sum()),
    }
    (out / "smoke_measurements_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    LOG.info("[done] wrote CSVs and summary to %s", out)


if __name__ == "__main__":
    main()
