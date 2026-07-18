"""Diagnose the anchor's P13 distance shift for ONE RegioStaR-7 class (#193).

Context: the 2026-07-17 holdout verdict flagged a small P13-by-RS7 regression
for class 72 (Braunschweig city: EMD 0.1724 -> 0.1760) while 5/6 classes
improved. This script explains that shift MECHANICALLY: the anchor conserves
every (origin zone, dest Kreis) block total, so all it can do is redistribute
flow BETWEEN destination zones WITHIN a block -- this script shows which
blocks redistribute how much mass, how that moves the class's commute-
distance band shares relative to the MiD P13 reference, and which individual
Gemeinde relations move the most flow.

Outputs (to --out):
- band_shift_rs7<C>.csv          per band: base/anchored/target share, the
                                 anchored-minus-base delta and the change of
                                 the |model - target| gap per band
- dest_kreis_decomposition_rs7<C>.csv
                                 per origin-zone x dest-Kreis block: conserved
                                 block mass, mass redistributed within the
                                 block, flow-weighted mean km before/after,
                                 and a leave-one-in EMD contribution (only
                                 this block anchored, all others baseline)
- top_movers_rs7<C>.csv          top Gemeinde relations by |flow delta| with
                                 their distances

Read-only against a COMPLETED working directory (never runs synpp); the
load/rebuild block mirrors scripts/run_anchor_holdout.py so the ODs are
IDENTICAL to the ones behind the verdict (self-check: the printed base and
anchored EMDs must reproduce the verdict's numbers exactly).

Run on the server:

    python scripts/diagnose_anchor_p13.py \
        --cache ~/eqasim-bs/eqasim-data/cache_bs_100pct_allfeat_popsim \
        --config config_server_braunschweig_100pct_allfeat_popsim.yml \
        --out ~/wt/verbindungen-anchor/diag_p13_72 --rs7 72
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

from braunschweig.calibration.metrics import (                     # noqa: E402
    apply_detour, band_shares, emd_on_bands, BAND_EDGES_KM,
)
from braunschweig.calibration.targets import (                     # noqa: E402
    load_p13_band_shares_by_rs7,
)
from braunschweig.data.bbsr.regiostar import ars_to_ags8           # noqa: E402
from braunschweig.data.verbindungen.zones import (                 # noqa: E402
    build_comparison_zones, build_zones_frames,
)
from braunschweig.data.verbindungen.work_od import (               # noqa: E402
    clip_qzm_to_cells, read_qzm_csv,
)
from braunschweig.gravity.model import (                           # noqa: E402
    DEFAULT_GRAVITY_MAX_ITERATIONS, _calibrate, _synthesise_intra_kreis,
    compute_work_od,
)
from braunschweig.gravity.verbindungen_anchor import (             # noqa: E402
    apply_inner_anchor, build_anchor_targets, collapse_od_to_zones,
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
    parser.add_argument("--rs7", type=int, default=72,
                        help="RegioStaR-7 class of the ORIGIN Gemeinden to "
                             "diagnose (default 72 = Braunschweig city)")
    parser.add_argument("--min-observed", type=float, default=None,
                        help="coverage threshold; default = the config's "
                             "anchor_min_observed_commuters or 30")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)["config"]
    scope = [str(p) for p in cfg["braunschweig.political_prefix"]]
    min_obs = args.min_observed if args.min_observed is not None else \
        cfg.get("braunschweig.verbindungen.anchor_min_observed_commuters", 30)

    # --- cached inputs + baseline/anchored rebuild (mirrors the holdout CLI
    # so the ODs are IDENTICAL to the verdict's; fold-free, so seed-free) ----
    df_population_raw = _load_stage(args.cache, "braunschweig.popsim.stage")
    df_employees_raw = _load_stage(args.cache, "braunschweig.data.census.employees")
    df_municipalities = _load_stage(args.cache, "data.spatial.municipalities")
    df_distances = _load_stage(args.cache, "eqasim_common.gravity.distance_matrix")
    df_regiostar = _load_stage(args.cache, "braunschweig.data.bbsr.regiostar")
    df_employment = _load_stage(args.cache, "braunschweig.data.census.employment")
    df_pendler = _load_stage(args.cache, "braunschweig.data.census.pendler")

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
    df_zone_map, df_cell_zone, df_zones = build_comparison_zones(
        df_cells, df_cell_commune)
    df_ref_zones = collapse_od_to_zones(df_ref_cells, df_cell_zone)

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
    targets, _ = build_anchor_targets(df_ref_zones, df_zones, min_obs)
    anchored, _ = apply_inner_anchor(baseline, df_zone_map, df_zones, targets)

    # --- class selection: origins whose Gemeinde RS7 == --rs7 ---------------
    dist = df_distances.set_index(["origin_id", "destination_id"])["distance_km"]
    rs7 = df_regiostar.set_index("commune_id")["regiostar7"]
    zmap = df_zone_map.set_index("commune_id")["zone_id"]
    kreis = df_zones.set_index("zone_id")["kreis_id"]

    def enrich(df):
        d = df.copy()
        d["km"] = apply_detour(pd.Series(
            [dist.get((o, dd), np.nan)
             for o, dd in zip(d["origin_id"], d["destination_id"])]).to_numpy())
        d["rs7"] = d["origin_id"].astype(str).map(ars_to_ags8).map(
            lambda a: rs7.get(a, np.nan))
        d["oz"] = d["origin_id"].astype(str).map(zmap)
        d["dz"] = d["destination_id"].astype(str).map(zmap)
        d["dk"] = d["dz"].map(kreis)
        return d

    base = enrich(baseline)
    anch = enrich(anchored)
    sel_base = base[(base["rs7"] == args.rs7) & base["km"].notna()].copy()
    sel_anch = anch[(anch["rs7"] == args.rs7) & anch["km"].notna()].copy()
    if sel_base.empty:
        raise RuntimeError(f"no origins with RS7 == {args.rs7} in the OD")
    print(f"[diagnose_anchor_p13] rs7={args.rs7}: {len(sel_base)} relations, "
          f"{sel_base['origin_id'].nunique()} origin Gemeinden, total flow "
          f"{sel_base['flow'].sum():.0f}")

    # --- (1) band shift vs the MiD P13 target -------------------------------
    p13_targets = {
        str(int(k)): v
        for k, v in load_p13_band_shares_by_rs7(
            os.path.join(cfg["data_path"], "braunschweig", "mid")).items()
    }
    target = np.asarray(p13_targets[str(args.rs7)], dtype=float)
    sh_base = band_shares(sel_base["km"].to_numpy(), weights=sel_base["flow"].to_numpy())
    sh_anch = band_shares(sel_anch["km"].to_numpy(), weights=sel_anch["flow"].to_numpy())
    emd_base = float(emd_on_bands(sh_base, target))
    emd_anch = float(emd_on_bands(sh_anch, target))
    # Self-check: these must reproduce the verdict's class EMDs exactly
    # (the full-anchor A/B is fold-free, hence seed-free).
    print(f"[diagnose_anchor_p13] class {args.rs7} EMD baseline {emd_base:.6f} "
          f"-> anchored {emd_anch:.6f} (verdict said 0.172374 -> 0.175972 "
          "for rs7=72; MUST match when --rs7 72 on the verdict cache)")
    edges = list(BAND_EDGES_KM)
    band_labels = [f"{edges[i]:g}-{edges[i + 1]:g}km" for i in range(len(edges) - 1)]
    df_bands = pd.DataFrame({
        "band": band_labels,
        "share_baseline": sh_base,
        "share_anchored": sh_anch,
        "share_mid_target": target,
        "delta_anchored_minus_baseline": sh_anch - sh_base,
        "abs_gap_change_vs_target": np.abs(sh_anch - target) - np.abs(sh_base - target),
    })
    df_bands.to_csv(os.path.join(args.out, f"band_shift_rs7{args.rs7}.csv"),
                    index=False)
    worst = df_bands.sort_values("abs_gap_change_vs_target", ascending=False)
    print("[diagnose_anchor_p13] bands moving AWAY from the target (top 3):")
    print(worst.head(3).to_string(index=False))

    # --- (2) per (origin zone, dest Kreis) block decomposition --------------
    key = ["origin_id", "destination_id"]
    merged = sel_base.merge(
        sel_anch[key + ["flow"]].rename(columns={"flow": "flow_anch"}),
        on=key, how="outer", validate="one_to_one")
    merged["flow"] = merged["flow"].fillna(0.0)
    merged["flow_anch"] = merged["flow_anch"].fillna(0.0)
    merged["delta"] = merged["flow_anch"] - merged["flow"]

    rows = []
    for (oz, dk), grp in merged.dropna(subset=["oz", "dk"]).groupby(["oz", "dk"]):
        mass_before = float(grp["flow"].sum())
        mass_after = float(grp["flow_anch"].sum())
        # The anchor conserves every block total (asserted inside
        # apply_inner_anchor); verify the decomposition sees the same.
        if mass_before > 0 and abs(mass_after - mass_before) / mass_before > 1e-6:
            raise RuntimeError(
                f"block ({oz}, {dk}) not conserved in the decomposition: "
                f"{mass_before} -> {mass_after}")
        redistributed = 0.5 * float(grp["delta"].abs().sum())
        km_before = (float((grp["flow"] * grp["km"]).sum() / mass_before)
                     if mass_before else np.nan)
        km_after = (float((grp["flow_anch"] * grp["km"]).sum() / mass_after)
                    if mass_after else np.nan)
        # Leave-one-in attribution: only THIS block anchored, all others
        # baseline -> the class EMD delta attributable to this block
        # (indicative; blocks interact only through the shared normalisation).
        hybrid = sel_base.copy()
        in_block = (hybrid["oz"] == oz) & (hybrid["dk"] == dk)
        block_anch = grp.set_index(key)["flow_anch"]
        hybrid_flow = hybrid["flow"].copy()
        idx = pd.MultiIndex.from_frame(hybrid.loc[in_block, key])
        hybrid_flow.loc[in_block] = block_anch.reindex(idx).to_numpy()
        sh_hybrid = band_shares(hybrid["km"].to_numpy(), weights=hybrid_flow.to_numpy())
        rows.append({
            "origin_zone": oz, "dest_kreis": dk,
            "block_mass": mass_before,
            "mass_redistributed_within_block": redistributed,
            "redistributed_share": redistributed / mass_before if mass_before else 0.0,
            "mean_km_baseline": km_before, "mean_km_anchored": km_after,
            "mean_km_shift": (km_after - km_before)
            if np.isfinite(km_before) and np.isfinite(km_after) else np.nan,
            "leave_one_in_emd_delta": float(emd_on_bands(sh_hybrid, target)) - emd_base,
        })
    df_blocks = (pd.DataFrame(rows)
                 .sort_values("leave_one_in_emd_delta", ascending=False))
    df_blocks.to_csv(
        os.path.join(args.out, f"dest_kreis_decomposition_rs7{args.rs7}.csv"),
        index=False)
    print("[diagnose_anchor_p13] blocks by leave-one-in EMD contribution "
          "(positive = pushes AWAY from MiD):")
    print(df_blocks.head(8).to_string(index=False))

    # --- (3) top Gemeinde-relation movers ------------------------------------
    movers = merged.reindex(
        merged["delta"].abs().sort_values(ascending=False).index)[
        ["origin_id", "destination_id", "oz", "dz", "dk", "km",
         "flow", "flow_anch", "delta"]].head(15)
    movers.to_csv(os.path.join(args.out, f"top_movers_rs7{args.rs7}.csv"),
                  index=False)
    print("[diagnose_anchor_p13] top movers (|delta flow|):")
    print(movers.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
