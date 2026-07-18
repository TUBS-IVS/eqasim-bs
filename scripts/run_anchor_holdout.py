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
    assign_folds, heldout_conditional_tvd,
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

    # P38.2 per-Kreis vs the MiD 2023 reference (#193 Task 8 decision 3):
    # reuses the TESTED loader p38_2_band_target (drops the
    # d_unplausibel_keine_angabe nonresponse column, renormalises per region;
    # ars5-keyed incl. the "03ZGB" scope aggregate). P38_2_BANDS' band order
    # equals P38_BAND_EDGES_KM's, so emd_on_bands compares aligned vectors.
    # DIRECTIONAL evidence only (thin n per Kreis -- robust-references rule),
    # never a gate in verdict(); a missing reference file is a LOUD warning
    # (the axis is then absent from this run), not a crash.
    from braunschweig.analysis.population_validation.trip_coherence import (  # noqa: PLC0415
        P38_2_BANDS, p38_2_band_target,
    )
    p38_verdict_line = None
    try:
        p38_ref_shares, _p38_ref_means = p38_2_band_target(cfg["data_path"])
    except (FileNotFoundError, ValueError) as exc:
        p38_ref_shares = None
        print(
            "[run_anchor_holdout] WARNING: P38.2 MiD reference unavailable -- "
            f"directional axis missing from this run: {exc}"
        )
    if p38_ref_shares is not None:
        band_cols = [col for col, _, _ in P38_2_BANDS]
        p38_rows, p38_regions_without_ref = [], set()
        for label, df in (("baseline", baseline), ("anchored", anchored)):
            d = df.copy()
            d["km"] = apply_detour(pd.Series(
                [dist.get((o, dd), np.nan)
                 for o, dd in zip(d["origin_id"], d["destination_id"])]).to_numpy())
            d = d.dropna(subset=["km"])
            d["kreis"] = d["origin_id"].astype(str).str[:5]
            groups = list(d.groupby("kreis")) + [("03ZGB", d)]
            for kr, grp in groups:
                if kr not in p38_ref_shares:
                    p38_regions_without_ref.add(kr)
                    continue
                model_shares = p38_band_shares(grp["km"].to_numpy(),
                                               grp["flow"].to_numpy())
                ref_vec = np.array([p38_ref_shares[kr][c] for c in band_cols])
                p38_rows.append({
                    "variant": label, "region": kr,
                    "emd_vs_mid": float(emd_on_bands(model_shares, ref_vec)),
                    **{f"model_{c}": s for c, s in zip(band_cols, model_shares)},
                    **{f"mid_{c}": r for c, r in zip(band_cols, ref_vec)},
                })
        p38_df = pd.DataFrame(p38_rows)
        p38_df.to_csv(os.path.join(args.out, "p38_per_kreis_vs_mid.csv"),
                      index=False)
        pivot = p38_df.pivot(index="region", columns="variant",
                             values="emd_vs_mid")
        n_improved = int((pivot["anchored"] < pivot["baseline"]).sum())
        zgb_note = (
            f"; 03ZGB {pivot.loc['03ZGB', 'baseline']:.4f} -> "
            f"{pivot.loc['03ZGB', 'anchored']:.4f}"
            if "03ZGB" in pivot.index else ""
        )
        skipped_note = (
            f"; {len(p38_regions_without_ref)} model region(s) without a MiD "
            f"P38.2 row skipped: {sorted(p38_regions_without_ref)[:5]}"
            if p38_regions_without_ref else ""
        )
        print(
            "[run_anchor_holdout] P38.2 vs MiD (directional, thin n): EMD "
            f"improved in {n_improved}/{len(pivot)} regions{zgb_note}{skipped_note}"
        )
        p38_verdict_line = (
            "- P38.2 vs MiD reference (DIRECTIONAL, thin n -- never a gate): "
            f"EMD improved in {n_improved}/{len(pivot)} regions{zgb_note}{skipped_note}"
        )

    # --- (4) k-fold CV -------------------------------------------------------
    folds = assign_folds(rows, k=args.folds, seed=args.seed)
    print(
        f"[run_anchor_holdout] {int((folds == -1).sum())} observed relations "
        "always-train (single destination, unsplittable)"
    )
    # Observed AO margin vector, built ONCE (mirrors ao_margin_diagnostic's
    # construction) so each fold's AO srmse is one margin_check call without
    # re-printing that diagnostic's coverage lines k times.
    from braunschweig.analysis.verbindungen_validation import margin_check  # noqa: PLC0415
    _zone_of_cell = df_cell_zone.set_index("cell_id")["zone_id"]
    _m = df_margins.copy()
    _m["zone_id"] = _m["cell_id"].map(_zone_of_cell)
    ao_ref = (_m.dropna(subset=["zone_id"])
              .groupby("zone_id")["workers_at_workplace"].sum(min_count=1))

    def _ao_srmse(zone_od):
        inflow = zone_od.groupby("destination_zone_id")["commuters"].sum()
        return float(margin_check(inflow.reindex(ao_ref.index).fillna(0.0),
                                  ao_ref.astype("Float64"))["srmse"])

    cv_base_vals, cv_anch_vals = [], []
    ao_fold_srmse = []
    p13_fold_by_rs7 = {}   # rs7 -> [per-fold EMD of the anchored fold variant]
    for fold in range(args.folds):
        held = folds == fold
        train_ref = df_ref_zones[~held.to_numpy()]
        t_fold, _ = build_anchor_targets(train_ref, df_zones, min_obs)
        anch_fold, _ = apply_inner_anchor(
            baseline, df_zone_map, df_zones, t_fold)
        anch_fold_zone_od = to_zone_od(anch_fold)
        cv_base_vals.append(heldout_conditional_tvd(
            to_zone_od(baseline), df_ref_zones, held))
        cv_anch_vals.append(heldout_conditional_tvd(
            anch_fold_zone_od, df_ref_zones, held))
        ao_fold_srmse.append(_ao_srmse(anch_fold_zone_od))
        for rs7_key, fold_emd in p13_emds(anch_fold).items():
            p13_fold_by_rs7.setdefault(rs7_key, []).append(fold_emd)

    # A stratification group smaller than k leaves some folds with zero
    # held-out rows: heldout_conditional_tvd returns float("nan") for an
    # empty held set, and an RS7 class can drop out of a fold's p13_emds()
    # entirely. Left unguarded, a NaN would silently poison the measured
    # noise scales (rule v2's (i')/(ii) gates would pass VACUOUSLY -- a
    # broken measurement masquerading as a valid one, CLAUDE.md fallback
    # transparency). Non-finite per-fold values are dropped with an explicit
    # logged count; too few finite folds RAISES rather than feeding NaN
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
    ao_fold_finite = _finite_folds("ao_fold_srmse", ao_fold_srmse)
    ao_noise = float(np.std(ao_fold_finite, ddof=1))
    # Rule v2 (ii): each RS7 class is gated against ITS OWN measured
    # perturbation noise (v1 used the std of the cross-class MEAN, which
    # understates per-class variability).
    p13_noise_by_rs7 = {
        rs7_key: float(np.std(
            _finite_folds(f"p13_fold_emd[rs7={rs7_key}]",
                          p13_fold_by_rs7.get(rs7_key, [])), ddof=1))
        for rs7_key in sorted(p13_base)
    }

    # HARNESS-LEAK DETECTOR (rule v2): the anchor never touches held-out
    # flows, so per fold cv_anchored must equal cv_baseline EXACTLY (see
    # test_heldout_cv_is_inert_by_construction). Any gap means the CV
    # harness leaked training information into the held-out evaluation (or
    # conservation broke) -- refuse to report a verdict from a leaking
    # harness. Checked PER FOLD (means could cancel opposite-sign leaks).
    per_fold_gaps = [abs(b - a) for b, a in zip(cv_base_vals, cv_anch_vals)
                     if np.isfinite(b) and np.isfinite(a)]
    max_cv_gap = max(per_fold_gaps) if per_fold_gaps else float("nan")
    if not per_fold_gaps or not np.isfinite(max_cv_gap) or max_cv_gap > 1e-12:
        raise RuntimeError(
            "[run_anchor_holdout] held-out CV leak detected: max per-fold "
            f"|baseline - anchored| = {max_cv_gap} over {len(per_fold_gaps)} "
            "comparable fold(s) -- the harness leaked training information "
            "(or the anchor stopped conserving); aborting the verdict"
        )
    cv_value = float(np.mean(cv_base_finite))
    print(
        "[run_anchor_holdout] held-out CV leak check PASS: baseline == "
        f"anchored == {cv_value:.4f} (max per-fold gap {max_cv_gap:.1e}; "
        "equality is the DESIGNED expectation, see anchor_holdout.py)"
    )

    # --- (5) verdict (pre-registered rule v2) ---------------------------------
    v = verdict(float(ao["before"]["srmse"]), float(ao["after"]["srmse"]),
                ao_noise, p13_base, p13_anch, p13_noise_by_rs7)
    p13_noise_str = {k: round(nv, 4) for k, nv in p13_noise_by_rs7.items()}
    lines = [
        "# Anchor holdout verdict (#193, pre-registered rule v2)", "",
        "- RULE v2 (amended 2026-07-17, BEFORE any measurement run produced "
        "a verdict; v1's held-out-CV criterion (i) was proven structurally "
        "inert for this in-sample anchor -- see the module docstring and "
        "test_heldout_cv_is_inert_by_construction): the default flips only "
        "if (i') the AO-margin srmse improves beyond its measured fold "
        "noise AND (ii) no P13-by-RS7 EMD regresses beyond its class's "
        "measured fold noise. All noise scales measured, never invented.",
        f"- rows anchorable: {tstats['n_rows_anchorable']}/{tstats['n_rows_total']} "
        f"(coverage min_observed={min_obs}); anchored mass share "
        f"{astats['anchored_mass_share']:.3f}",
        f"- fit axis (LABELLED FIT): weighted TVD {fit_base['weighted_tvd']:.4f} "
        f"-> {fit_anch['weighted_tvd']:.4f}",
        f"- held-out CV leak check (k={args.folds}): PASS -- baseline == "
        f"anchored == {cv_value:.4f} (max per-fold gap {max_cv_gap:.1e}). "
        "Equality is the DESIGNED expectation for this in-sample anchor; "
        "the CV is retained ONLY as a harness-integrity check, NOT a "
        "decision criterion (rule v1's criterion (i), removed 2026-07-17).",
        f"- (i') AO-margin corroboration (non-fitted axis): srmse before "
        f"{v['ao_srmse_before']:.4f} -> after {v['ao_srmse_after']:.4f} "
        f"(measured fold noise {v['ao_noise']:.4f}; corroborates: "
        f"{v['ao_improves']})",
        f"- (ii) P13-by-RS7 EMD baseline {p13_base} -> anchored {p13_anch} "
        f"(per-class fold noise {p13_noise_str}; regressions: "
        f"{v['p13_regressions']})",
        (p38_verdict_line if p38_verdict_line is not None else
         "- P38.2 vs MiD reference: UNAVAILABLE in this run (see the WARNING "
         "in the log) -- the directional axis is missing, not silently fine"),
        f"- censored-bound (data-quality context; anchor-INVARIANT, cannot "
        f"discriminate): mass share {censored_summary['censored_mass_share']:.4f}, "
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
