"""Hold-out CV + A/B + verdict for the inner VerBindungen anchor (#193).

Read-only against a COMPLETED working directory (never runs synpp). Run on
the server (local raw data partially missing since 2026-07-16):

    python scripts/run_anchor_holdout.py \
        --cache ~/eqasim-bs/eqasim-data/cache_bs_100pct_allfeat_popsim \
        --config config_server_braunschweig_100pct_allfeat_popsim.yml \
        --out ~/wt/verbindungen-anchor/holdout_out
"""
from __future__ import annotations

import argparse
import glob
import os
import pickle
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from braunschweig.calibration.anchor_holdout import (            # noqa: E402
    P38_BAND_EDGES_KM, assign_folds, heldout_conditional_tvd,
    p38_band_shares, verdict,
)
from braunschweig.calibration.metrics import (                    # noqa: E402
    apply_detour, band_shares, emd_on_bands,
)
from braunschweig.calibration.targets import (                    # noqa: E402
    load_p13_band_shares_by_rs7,
)
from braunschweig.data.verbindungen.zones import (                # noqa: E402
    build_comparison_zones, build_zones_frames,
)
from braunschweig.data.verbindungen.work_od import (              # noqa: E402
    clip_qzm_to_cells, read_qzm_csv,
)
from braunschweig.data.verbindungen.margins import (              # noqa: E402
    build_margins_frame, read_statisch_csv,
)
from braunschweig.gravity.model import (                          # noqa: E402
    DEFAULT_GRAVITY_MAX_ITERATIONS, _calibrate, _synthesise_intra_kreis,
    compute_work_od,
)
from braunschweig.gravity.verbindungen_anchor import (            # noqa: E402
    ao_margin_diagnostic, apply_inner_anchor, build_anchor_targets,
    censored_bound_diagnostic, collapse_od_to_zones,
    intra_kreis_diagnostic,
)


def _load_stage(wd, stage_name):
    hits = glob.glob(os.path.join(wd, f"{stage_name}__*.p"))
    if not hits:
        raise RuntimeError(f"no cached pickle for '{stage_name}' in {wd}")
    path = max(hits, key=os.path.getmtime)
    print(f"  LOAD {stage_name} <- {os.path.basename(path)}")
    with open(path, "rb") as fh:
        return pickle.load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260716)
    parser.add_argument("--min-observed", type=float, default=None,
                        help="coverage threshold; default = the config's "
                             "anchor_min_observed_commuters or 30")
    args = parser.parse_args()
    if args.folds < 2:
        raise SystemExit("--folds must be >= 2 (k-fold CV)")
    os.makedirs(args.out, exist_ok=True)

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)["config"]
    scope = [str(p) for p in cfg["braunschweig.political_prefix"]]
    min_obs = args.min_observed if args.min_observed is not None else \
        cfg.get("braunschweig.verbindungen.anchor_min_observed_commuters", 30)

    # --- cached inputs (read-only) ------------------------------------------
    df_population_raw = _load_stage(args.cache, "braunschweig.popsim.stage")
    df_employees_raw = _load_stage(args.cache, "braunschweig.data.census.employees")
    df_municipalities = _load_stage(args.cache, "data.spatial.municipalities")
    df_distances = _load_stage(args.cache, "eqasim_common.gravity.distance_matrix")
    df_regiostar = _load_stage(args.cache, "braunschweig.data.bbsr.regiostar")
    df_employment = _load_stage(args.cache, "braunschweig.data.census.employment")
    df_pendler = _load_stage(args.cache, "braunschweig.data.census.pendler")

    # --- reference from raw files -------------------------------------------
    data_dir = os.path.join("eqasim-data", "data", "verbindungen")
    gdf_raw = gpd.read_file(
        "zip://" + os.path.join(data_dir, "Shapefiles_VerBindungen_Zellen.zip")
        + "!verbindungen-verkehrszellen.shp")
    df_cells, df_cell_commune, _ = build_zones_frames(
        gdf_raw, df_municipalities, scope=scope, max_fallback_share=0.10)
    df_ref_cells, _ = clip_qzm_to_cells(
        read_qzm_csv(os.path.join(
            data_dir, "QZM-Berufspendler-VerBindungen-Verkehrszellen.csv")),
        set(df_cells["cell_id"]))
    df_margins = build_margins_frame(
        read_statisch_csv(os.path.join(
            data_dir, "SvBaGeB_Statisch_WO_Verkehrszellen.csv"), "workers_at_home"),
        read_statisch_csv(os.path.join(
            data_dir, "SvBaGeB_Statisch_AO_Verkehrszellen.csv"), "workers_at_workplace"),
        cell_ids=list(df_cells["cell_id"]))

    df_zone_map, df_cell_zone, df_zones = build_comparison_zones(
        df_cells, df_cell_commune)
    df_ref_zones = collapse_od_to_zones(df_ref_cells, df_cell_zone)

    # --- (1) coverage measurement (informs the Task-8 default) --------------
    kreis = df_zones.set_index("zone_id")["kreis_id"]
    rows = df_ref_zones.assign(dest_kreis=df_ref_zones["destination_zone_id"].map(kreis))
    row_mass = rows.groupby(["origin_zone_id", "dest_kreis"])["commuters"].sum()
    cov = row_mass.describe(percentiles=[.1, .25, .5, .75, .9])
    cov.to_csv(os.path.join(args.out, "coverage_row_observed_commuters.csv"))
    print("\n=== per-row observed-commuter coverage ===")
    print(cov.to_string())

    # --- baseline calibrated OD (offline, population production) -----------
    df_pop = (df_population_raw
              .rename(columns={"commune_id": "origin_id", "weight": "population"})
              .groupby("origin_id")["population"].sum().reset_index())
    df_emp = df_employees_raw.rename(columns={
        "commune_id": "destination_id", "weight": "employees"})[
        ["destination_id", "employees"]]
    od = compute_work_od(
        df_population=df_pop, df_employees=df_emp, df_distances=df_distances,
        df_regiostar=df_regiostar, rs7_by_zone=None,
        slope=cfg["gravity_slope"], constant=cfg["gravity_constant"],
        diagonal=cfg["gravity_diagonal"],
        slope_overrides=cfg.get("gravity_slope_by_regiostar7"),
        friction_factors=cfg.get("gravity_friction_factors"),
        max_iterations=cfg.get("gravity_max_iterations",
                               DEFAULT_GRAVITY_MAX_ITERATIONS))
    mask = df_pendler["orig_ars"].isin(scope) | df_pendler["dest_ars"].isin(scope)
    pendler_cal = _synthesise_intra_kreis(df_pendler[mask].copy(),
                                          df_employment, scope)
    baseline = _calibrate(
        od, df_pop.rename(columns={"origin_id": "commune_id",
                                   "population": "weight"}), pendler_cal)

    def to_zone_od(df):
        z = df.copy()
        zmap = df_zone_map.set_index("commune_id")["zone_id"]
        z["origin_zone_id"] = z["origin_id"].astype(str).map(zmap)
        z["destination_zone_id"] = z["destination_id"].astype(str).map(zmap)
        z = z.dropna(subset=["origin_zone_id", "destination_zone_id"])
        return (z.groupby(["origin_zone_id", "destination_zone_id"])["flow"]
                .sum().rename("commuters").reset_index())

    # --- (2) full-anchor A/B + diagnostics ----------------------------------
    targets, tstats = build_anchor_targets(df_ref_zones, df_zones, min_obs)
    anchored, astats = apply_inner_anchor(baseline, df_zone_map, df_zones, targets)
    z_base, z_anch = to_zone_od(baseline), to_zone_od(anchored)

    from braunschweig.analysis.verbindungen_validation import conditional_od_check
    # conditional_od_check groups by origin_cell_id/destination_cell_id; our
    # frames are zone-keyed, so rename to the columns it expects. The check is
    # column-name-generic, so this yields the zone-level conditional TVD (fit).
    def _cell_named(df):
        return df.rename(columns={"origin_zone_id": "origin_cell_id",
                                  "destination_zone_id": "destination_cell_id"})
    _, fit_base = conditional_od_check(_cell_named(z_base), _cell_named(df_ref_zones))
    _, fit_anch = conditional_od_check(_cell_named(z_anch), _cell_named(df_ref_zones))
    censored_df, censored_summary = censored_bound_diagnostic(z_anch, df_ref_zones)
    censored_df.to_csv(os.path.join(args.out, "censored_bound.csv"), index=False)
    ao = ao_margin_diagnostic(z_base, z_anch, df_margins, df_cell_zone)
    intra = intra_kreis_diagnostic(baseline, df_ref_zones, df_zones, df_zone_map)
    intra.to_csv(os.path.join(args.out, "intra_kreis_diagnostic.csv"), index=False)

    # --- (3) distance axes ---------------------------------------------------
    dist = df_distances.set_index(["origin_id", "destination_id"])["distance_km"]
    rs7 = df_regiostar.set_index("commune_id")["regiostar7"]
    # Normalise target keys to str(int) ONCE -- the loader may key by int or
    # str depending on the CSV dtype; a silent key-type mismatch would yield
    # an empty EMD dict (no metric) instead of an error.
    p13_targets = {
        str(int(k)): v
        for k, v in load_p13_band_shares_by_rs7(
            os.path.join(cfg["data_path"], "braunschweig", "mid")).items()
    }
    if not p13_targets:
        raise RuntimeError("P13-by-RS7 targets empty -- check the mid CSV path")

    from braunschweig.data.bbsr.regiostar import ars_to_ags8

    # The regiostar frame keys on 8-digit AGS while the OD carries 12-digit
    # ARS -- convert via ars_to_ags8 (ARS[0:5] + ARS[9:12]), NEVER a plain
    # [:8] slice (that mixes in the Verbandsgemeinde block).
    def p13_emds(df):
        d = df.copy()
        d["km"] = apply_detour(pd.Series(
            [dist.get((o, dd), np.nan)
             for o, dd in zip(d["origin_id"], d["destination_id"])]).to_numpy())
        d["rs7"] = d["origin_id"].astype(str).map(ars_to_ags8).map(
            lambda a: rs7.get(a, np.nan))
        d = d.dropna(subset=["km", "rs7"])
        out = {}
        for code, grp in d.groupby("rs7"):
            key = str(int(code))
            if key not in p13_targets:
                continue
            shares = band_shares(grp["km"].to_numpy(),
                                 weights=grp["flow"].to_numpy())
            out[key] = float(emd_on_bands(shares, p13_targets[key]))
        return out

    p13_base, p13_anch = p13_emds(baseline), p13_emds(anchored)

    # P38.2 per-Kreis MODEL DRIFT table: baseline-vs-anchored MODEL band
    # shares only. This does NOT load the MiD P38.2 reference CSV, so it is
    # NOT (yet) a directional comparison against observed data.
    # TODO(#193 Task 8): load the MiD P38.2-by-Kreis reference
    # (mid2023_P38_2_commute_distance_by_kreis.csv: drop
    # d_unplausibel_keine_angabe, renormalise per Kreis) for a TRUE
    # directional comparison vs observed; deferred to the server run where
    # the CSV is available and testable. NOTE:
    # braunschweig.analysis.population_validation.trip_coherence
    # .p38_2_band_target() already implements exactly this drop+renormalise
    # step (ARS5-keyed, including the "03ZGB" aggregate) -- reuse it rather
    # than re-deriving the parsing.
    p38_rows = []
    for label, df in (("baseline", baseline), ("anchored", anchored)):
        d = df.copy()
        d["km"] = apply_detour(pd.Series(
            [dist.get((o, dd), np.nan)
             for o, dd in zip(d["origin_id"], d["destination_id"])]).to_numpy())
        d["kreis"] = d["origin_id"].astype(str).str[:5]
        for kr, grp in d.dropna(subset=["km"]).groupby("kreis"):
            shares = p38_band_shares(grp["km"].to_numpy(),
                                     grp["flow"].to_numpy())
            p38_rows.append({"variant": label, "kreis": kr,
                             **{f"band_{i}": s for i, s in enumerate(shares)}})
    pd.DataFrame(p38_rows).to_csv(
        os.path.join(args.out, "p38_per_kreis_model_drift.csv"), index=False)

    # --- (4) k-fold CV -------------------------------------------------------
    folds = assign_folds(rows, k=args.folds, seed=args.seed)
    print(
        f"[run_anchor_holdout] {int((folds == -1).sum())} observed relations "
        "always-train (single destination, unsplittable)"
    )
    cv_base_vals, cv_anch_vals, p13_fold_emds = [], [], []
    for fold in range(args.folds):
        held = folds == fold
        train_ref = df_ref_zones[~held.to_numpy()]
        t_fold, _ = build_anchor_targets(train_ref, df_zones, min_obs)
        anch_fold, _ = apply_inner_anchor(
            baseline, df_zone_map, df_zones, t_fold)
        cv_base_vals.append(heldout_conditional_tvd(
            to_zone_od(baseline), df_ref_zones, held))
        cv_anch_vals.append(heldout_conditional_tvd(
            to_zone_od(anch_fold), df_ref_zones, held))
        fold_p13 = list(p13_emds(anch_fold).values())
        p13_fold_emds.append(float(np.mean(fold_p13)) if fold_p13 else float("nan"))

    # A stratification group smaller than k leaves some folds with zero
    # held-out rows: heldout_conditional_tvd returns float("nan") for an
    # empty held set, and an empty p13_emds() dict does the same for
    # p13_fold_emds above. Left unguarded, a NaN cv_* silently makes
    # verdict()'s `improves` False, and a NaN p13_noise NaN-poisons every `>`
    # regression comparison in verdict() so `regressions={}` VACUOUSLY -- the
    # entire distance-regression half of the pre-registered rule would
    # silently "pass" without ever truly being evaluated. CLAUDE.md fallback
    # transparency: a broken measurement must never masquerade as a valid
    # one, so non-finite per-fold values are dropped with an explicit logged
    # count, and too few finite folds raises rather than feeding a NaN
    # forward into verdict().
    def _finite_folds(label, values):
        arr = np.asarray(values, dtype=float)
        finite = arr[np.isfinite(arr)]
        print(f"[run_anchor_holdout] {label}: {len(finite)}/{len(arr)} folds finite")
        if len(finite) < 2:
            raise RuntimeError(
                f"[run_anchor_holdout] {label}: only {len(finite)}/{len(arr)} folds "
                "produced a finite value -- refusing to feed a NaN-poisoned mean/std "
                "into verdict(). Likely cause: a stratification group smaller than "
                "--folds leaves some folds with zero held-out rows. Reduce --folds "
                "or inspect the coverage measurement printed above.")
        return finite

    cv_base_finite = _finite_folds("cv_baseline", cv_base_vals)
    cv_anch_finite = _finite_folds("cv_anchored", cv_anch_vals)
    p13_fold_finite = _finite_folds("p13_fold_emd", p13_fold_emds)
    p13_noise = float(np.std(p13_fold_finite, ddof=1))

    # --- (5) verdict ----------------------------------------------------------
    v = verdict(float(np.nanmean(cv_base_finite)), float(np.nanmean(cv_anch_finite)),
                p13_base, p13_anch, p13_noise)
    lines = [
        "# Anchor holdout verdict (#193, pre-registered rule)", "",
        f"- rows anchorable: {tstats['n_rows_anchorable']}/{tstats['n_rows_total']} "
        f"(coverage min_observed={min_obs}); anchored mass share "
        f"{astats['anchored_mass_share']:.3f}",
        f"- fit axis (LABELLED FIT): weighted TVD {fit_base['weighted_tvd']:.4f} "
        f"-> {fit_anch['weighted_tvd']:.4f}",
        f"- held-out conditional TVD (k={args.folds}): "
        f"{v['cv_baseline']:.4f} -> {v['cv_anchored']:.4f} "
        f"(improves: {v['cv_improves']})",
        f"- P13-by-RS7 EMD baseline {p13_base} -> anchored {p13_anch} "
        f"(fold noise {p13_noise:.4f}; regressions: {v['p13_regressions']})",
        f"- AO-margin srmse before {ao['before']['srmse']:.4f} -> after "
        f"{ao['after']['srmse']:.4f}",
        f"- censored-bound: mass share {censored_summary['censored_mass_share']:.4f}, "
        f">1x {censored_summary['share_ratio_gt_1']:.4f}, "
        f">5x {censored_summary['share_ratio_gt_5']:.4f}",
        "",
        f"**default_flip_supported: {v['default_flip_supported']}** "
        "(human decision + ADR follow; this script never flips anything)",
    ]
    with open(os.path.join(args.out, "verdict.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
