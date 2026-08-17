"""Quality A/B: integer-seed (SUB_BALANCE_WITH_FLOAT_SEED_WEIGHTS=false + USE_NUMBA)
vs float-seed reference PopulationSim batch on IDENTICAL inputs (issue: validate the
2026-07-10 40x perf settings scientifically, not just for speed; ADR-0056).

Both variants must be COMPLETED PopulationSim batch folders (configs/ + data/ + output/)
whose data/ inputs are byte-identical (verified upstream of this script). The comparison
axes:

1. ZENSUS100m control fit: realised-vs-target per (cell, control) for every 100m-geography
   control, via braunschweig.analysis.integerizer_quality.cell_error (control definitions
   from control_spec, NOT re-encoded). Reported per control: MAE, RMSE, SRMSE (canonical:
   RMSE / mean of ALL targets, matching population_validation.quality_assessment.assess)
   plus srmse_pos (this script's original RMSE / mean of POSITIVE targets only, kept for
   internal A/B consistency but NOT cross-tool comparable), total realised vs total
   target, and the paired per-(cell,control) win/tie/loss counts between the variants.
2. 1km household totals: households per 1km parent (via the batch geo_cross_walk),
   compared to each other (expected to match closely -- small integerization differences
   between the two regimes are possible; NOT bit-identical).
3. Donor diversity: distinct donor households drawn, top-share, top10-share, HHI.
4. Person marginals: age-band x sex composition of the expanded synthetic persons.

Run on the eqasim conda env from a repo checkout at the RUN commit (read-only import):
  cd /home/felix/wt-kreis-run && conda activate eqasim && \
  python ~/ab_quality/compare_seed_weight_quality.py \
      --variant-a intseed=/home/felix/eqasim-bs/eqasim-data/popsim_work_allfeat_opt/batch_000 \
      --variant-b float=/home/felix/bench_batch_float \
      --mid-dir eqasim-data/data/braunschweig/popsim/mid2023_raw \
      --random-seed 1234 --tiers tier0,tier1,tier2,tier3 --employment-grid --weekend \
      --out-dir ~/ab_quality/results

Self-test (harness validation, run BEFORE the real A/B): pass the same folder as both
variants; every between-variant difference must be exactly zero or the script exits 1.

Outputs (out_dir): ab_control_fit_by_control.csv, ab_cell_level_paired.csv,
ab_1km_household_totals.csv, ab_donor_diversity.csv, ab_person_marginals.csv,
ab_summary.md. All numbers are DESCRIPTIVE; the accept/reject decision (int-seed fit
not worse than float reference) is made by the reader against these tables.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ab_quality")

AGE_BANDS = [(0, 17), (18, 29), (30, 39), (40, 49), (50, 59), (60, 69), (70, 200)]


def parse_variant(spec: str) -> tuple[str, Path]:
    name, _, path = spec.partition("=")
    if not path:
        raise argparse.ArgumentTypeError(f"variant must be NAME=PATH, got {spec!r}")
    folder = Path(path).expanduser()
    for required in ("output/synthetic_households.csv",
                     "data/control_totals_ZENSUS100m.csv",
                     "data/geo_cross_walk.csv",
                     "output/final_expanded_household_ids.csv"):
        if not (folder / required).is_file():
            raise argparse.ArgumentTypeError(f"variant {name}: missing {folder / required}")
    return name, folder


def load_donor(mid_dir: str, random_seed: int, weekend: bool):
    """One completed-donor load shared by both variants (the slow phase)."""
    from braunschweig.popsim import mid as midmod, seed as seedmod

    rng = np.random.RandomState(random_seed + 74513)
    day_filter = seedmod.ALL_REPORTING_KERNWO if weekend else None
    donor_hh, donor_p, _c, _m = midmod.load_completed_donor(
        mid_dir, completion_rng=rng, day_filter_values=day_filter)
    hh_type5 = seedmod.derive_hh_type5(donor_p, household_id_col="H_ID", age_col="HP_ALTER")
    donor_hh = donor_hh.copy()
    donor_hh["hh_type5"] = donor_hh["H_ID"].map(hh_type5)
    logger.info("donor loaded: %d households, %d persons", len(donor_hh), len(donor_p))
    return donor_hh, donor_p


def control_fit(folder: Path, donor_hh, donor_p, controls) -> pd.DataFrame:
    """Long [zensus100m, control, realised, target, abs_error] for one variant."""
    from braunschweig.analysis.integerizer_quality import cell_error

    syn_hh = pd.read_csv(folder / "output" / "synthetic_households.csv")
    realised, n_resolved, n_skipped = cell_error.realised_counts(
        syn_hh, donor_hh, donor_p, controls)
    logger.info("%s: %d controls resolved, %d skipped", folder, n_resolved, n_skipped)
    if n_resolved == 0:
        raise RuntimeError(f"{folder}: no control expression resolved (harness broken)")
    target = cell_error._load_targets(folder / "data" / "control_totals_ZENSUS100m.csv")
    merged = target.merge(realised, on=["zensus100m", "control"], how="left")
    merged["realised"] = merged["realised"].fillna(0).astype(int)
    merged["abs_error"] = (merged["realised"] - merged["target"]).abs()
    # Key-alignment guard (same rationale as cell_error.cell_error_table).
    pos = merged["target"] > 0
    share = float((merged.loc[pos, "realised"] > 0).mean()) if pos.any() else 0.0
    if pos.any() and share < 0.01:
        raise RuntimeError(f"{folder}: realised~0 on positive-target cells "
                           f"({share:.2%}) -- key mismatch, refusing fabricated errors")
    return merged


def per_control_metrics(fit: pd.DataFrame, variant: str) -> pd.DataFrame:
    def agg(group: pd.DataFrame) -> pd.Series:
        target_mean_all = group["target"].mean()
        target_mean_pos = group.loc[group["target"] > 0, "target"].mean()
        rmse = float(np.sqrt((group["realised"] - group["target"]).pow(2).mean()))
        return pd.Series({
            "mae": float(group["abs_error"].mean()),
            "rmse": rmse,
            # Canonical SRMSE = RMSE / mean(ALL targets, incl. zero-target cells) --
            # see braunschweig.analysis.population_validation.quality_assessment.assess.
            "srmse": rmse / target_mean_all if target_mean_all and target_mean_all > 0 else np.nan,
            # Non-standard denominator (this script's original "srmse"): RMSE / mean of
            # POSITIVE targets only. mean(positive-only) >= mean(all), so this is
            # systematically SMALLER than the canonical "srmse" above; kept for internal
            # A/B consistency only, NOT comparable to SRMSE reported by other tools.
            "srmse_pos": rmse / target_mean_pos if target_mean_pos and target_mean_pos > 0 else np.nan,
            "total_realised": int(group["realised"].sum()),
            "total_target": int(group["target"].sum()),
            "n_cells": int(len(group)),
        })

    out = fit.groupby("control").apply(agg).reset_index()
    out.insert(0, "variant", variant)
    return out


def households_per_1km(folder: Path) -> pd.DataFrame:
    # final_expanded_household_ids.csv carries the ZENSUS1km parent directly
    # (columns STAAT,KREIS,ZENSUS1km,ZENSUS100m,H_ID) -- no crosswalk join needed.
    ids = pd.read_csv(folder / "output" / "final_expanded_household_ids.csv")
    if ids["ZENSUS1km"].isna().any():
        raise RuntimeError(f"{folder}: expanded households with missing ZENSUS1km parent")
    return ids.groupby("ZENSUS1km").size().rename("households").reset_index()


def donor_diversity(folder: Path, variant: str) -> dict:
    ids = pd.read_csv(folder / "output" / "final_expanded_household_ids.csv")
    counts = ids["H_ID"].value_counts()
    shares = counts / counts.sum()
    return {
        "variant": variant,
        "n_synthetic_households": int(counts.sum()),
        "n_distinct_donors": int(counts.size),
        "top_donor_share": float(shares.iloc[0]),
        "top10_donor_share": float(shares.iloc[:10].sum()),
        "hhi": float((shares ** 2).sum()),
    }


def person_marginals(folder: Path, donor_p: pd.DataFrame, variant: str) -> pd.DataFrame:
    ids = pd.read_csv(folder / "output" / "final_expanded_household_ids.csv")
    persons = ids[["H_ID"]].merge(
        donor_p[["H_ID", "HP_ALTER", "HP_SEX"]], on="H_ID", how="left")
    # Rows whose donor H_ID is missing from donor_p, or whose donor has NaN age/sex,
    # get dropped further down by groupby(..., observed=True) (NaN keys are excluded).
    # That drop is intentional (an unresolvable donor cannot contribute to the age/sex
    # marginal) but must not be silent -- no-silent-fallback rule.
    missing_donor = persons["HP_ALTER"].isna() | persons["HP_SEX"].isna()
    n_missing_donor = int(missing_donor.sum())
    if n_missing_donor:
        share_missing = n_missing_donor / len(persons) if len(persons) else 0.0
        logger.warning(
            "%s: %d/%d synthetic-household persons (%.2f%%) have no resolvable donor "
            "age/sex (missing H_ID join or NaN HP_ALTER/HP_SEX) and are dropped from "
            "the person marginals", variant, n_missing_donor, len(persons), 100.0 * share_missing)
    bands = pd.cut(persons["HP_ALTER"],
                   bins=[b[0] - 0.5 for b in AGE_BANDS] + [AGE_BANDS[-1][1] + 0.5],
                   labels=[f"{a}-{b}" for a, b in AGE_BANDS])
    table = (persons.assign(age_band=bands)
             .groupby(["age_band", "HP_SEX"], observed=True).size()
             .rename("persons").reset_index())
    table["share"] = table["persons"] / table["persons"].sum()
    table.insert(0, "variant", variant)
    return table


def person_marginal_max_diff(marginals: pd.DataFrame, name_a: str, name_b: str) -> tuple[float, int]:
    """Max |share diff| per (age_band, HP_SEX) cell, plus the count of cells present
    in only one variant.

    pivot_table only creates an (age_band, HP_SEX) row for combinations that occur in
    at least one variant, so a cell drawn by only one variant becomes NaN for the
    other. That NaN is a real coverage gap -- the missing variant's share there is
    genuinely 0, not an unknown to be skipped -- so it is filled with 0 before diffing;
    otherwise pandas' default skipna max() would silently drop exactly the cells where
    the two variants disagree the most (one has a share, the other has none at all).
    """
    shares = marginals.pivot_table(index=["age_band", "HP_SEX"], columns="variant",
                                   values="share", observed=True)
    for name in (name_a, name_b):
        if name not in shares.columns:
            raise RuntimeError(f"person marginals: variant '{name}' has no (age_band, "
                               f"HP_SEX) cells at all -- donor merge likely failed")
    n_variant_only = int((shares[name_a].isna() ^ shares[name_b].isna()).sum())
    filled = shares[[name_a, name_b]].fillna(0.0)
    max_diff = float((filled[name_a] - filled[name_b]).abs().max())
    return max_diff, n_variant_only


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant-a", type=parse_variant, required=True)
    parser.add_argument("--variant-b", type=parse_variant, required=True)
    parser.add_argument("--mid-dir", required=True)
    parser.add_argument("--random-seed", type=int, required=True)
    parser.add_argument("--tiers", required=True)
    parser.add_argument("--employment-grid", action="store_true")
    parser.add_argument("--weekend", action="store_true")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--self-test", action="store_true",
                        help="assert every between-variant difference is exactly zero")
    args = parser.parse_args()

    from braunschweig.popsim import control_spec

    name_a, folder_a = args.variant_a
    name_b, folder_b = args.variant_b
    if name_a == name_b:
        parser.error("variant names must differ (they key the output columns); "
                     "for a self-test use e.g. selfA=<path> selfB=<same path>")
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    tiers = tuple(t.strip() for t in args.tiers.split(",") if t.strip())
    controls = [c for c in control_spec.full_catalog(
        include_tiers=tiers, include_employment_grid=args.employment_grid)
        if getattr(c, "geography", None) == "ZENSUS100m"]
    logger.info("%d ZENSUS100m controls active", len(controls))

    donor_hh, donor_p = load_donor(args.mid_dir, args.random_seed, args.weekend)

    # 1. Control fit per variant + paired cell-level comparison.
    fit_a = control_fit(folder_a, donor_hh, donor_p, controls)
    fit_b = control_fit(folder_b, donor_hh, donor_p, controls)
    metrics = pd.concat([per_control_metrics(fit_a, name_a),
                         per_control_metrics(fit_b, name_b)], ignore_index=True)
    metrics.to_csv(out_dir / "ab_control_fit_by_control.csv", index=False)

    paired = fit_a.merge(fit_b, on=["zensus100m", "control"],
                         suffixes=(f"_{name_a}", f"_{name_b}"), how="outer", indicator=True)
    if (paired["_merge"] != "both").any():
        raise RuntimeError("paired frames do not align on (cell, control) -- "
                           "the two variants did not balance the same targets")
    paired = paired.drop(columns="_merge")
    ea, eb = paired[f"abs_error_{name_a}"], paired[f"abs_error_{name_b}"]
    paired.to_csv(out_dir / "ab_cell_level_paired.csv", index=False)
    wins = int((ea < eb).sum())
    ties = int((ea == eb).sum())
    losses = int((ea > eb).sum())

    # 2. 1km household totals.
    km_a = households_per_1km(folder_a).rename(columns={"households": name_a})
    km_b = households_per_1km(folder_b).rename(columns={"households": name_b})
    km = km_a.merge(km_b, on="ZENSUS1km", how="outer").fillna(0)
    km["diff"] = km[name_a] - km[name_b]
    km.to_csv(out_dir / "ab_1km_household_totals.csv", index=False)

    # 3. Donor diversity.
    diversity = pd.DataFrame([donor_diversity(folder_a, name_a),
                              donor_diversity(folder_b, name_b)])
    diversity.to_csv(out_dir / "ab_donor_diversity.csv", index=False)

    # 4. Person marginals (age band x sex shares).
    marginals = pd.concat([person_marginals(folder_a, donor_p, name_a),
                           person_marginals(folder_b, donor_p, name_b)], ignore_index=True)
    marginals.to_csv(out_dir / "ab_person_marginals.csv", index=False)
    max_share_diff, n_variant_only_cells = person_marginal_max_diff(marginals, name_a, name_b)
    if n_variant_only_cells:
        logger.warning(
            "%d (age_band, HP_SEX) cells are present in only one variant's person "
            "marginals; filled 0 for the missing side before computing the max diff "
            "(not skipped)", n_variant_only_cells)

    # Summary.
    mean_mae = metrics.groupby("variant")["mae"].mean()
    mean_srmse = metrics.groupby("variant")["srmse"].mean()
    lines = [
        "# Quality A/B: %s vs %s" % (name_a, name_b),
        "",
        "Inputs verified byte-identical upstream; settings differ ONLY in",
        "SUB_BALANCE_WITH_FLOAT_SEED_WEIGHTS (+USE_NUMBA, numerically identical to 1e-13).",
        "",
        "| metric | %s | %s |" % (name_a, name_b),
        "|---|---|---|",
        "| mean per-control MAE (100m) | %.4f | %.4f |" % (mean_mae[name_a], mean_mae[name_b]),
        "| mean per-control SRMSE (100m, canonical = RMSE / mean(all targets)) | %.4f | %.4f |"
        % (mean_srmse[name_a], mean_srmse[name_b]),
        "| paired cells: lower error / tie / higher | %d / %d / %d |" % (wins, ties, losses),
        "| 1km household totals max |diff| | %d |" % int(km["diff"].abs().max()),
        "| distinct donors | %d | %d |" % (diversity.iloc[0]["n_distinct_donors"],
                                           diversity.iloc[1]["n_distinct_donors"]),
        "| donor HHI | %.5f | %.5f |" % (diversity.iloc[0]["hhi"], diversity.iloc[1]["hhi"]),
        "| max person-marginal share |diff| | %.5f |" % max_share_diff,
        "",
        "Full tables: ab_control_fit_by_control.csv, ab_cell_level_paired.csv,",
        "ab_1km_household_totals.csv, ab_donor_diversity.csv, ab_person_marginals.csv.",
    ]
    (out_dir / "ab_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))

    if args.self_test:
        problems = []
        if not (ea == eb).all():
            problems.append("cell-level abs errors differ")
        if int(km["diff"].abs().max()) != 0:
            problems.append("1km household totals differ")
        if diversity.iloc[0]["n_distinct_donors"] != diversity.iloc[1]["n_distinct_donors"]:
            problems.append("donor diversity differs")
        if problems:
            logger.error("SELF-TEST FAILED: %s", "; ".join(problems))
            return 1
        logger.info("SELF-TEST PASSED: all between-variant differences are exactly zero.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
