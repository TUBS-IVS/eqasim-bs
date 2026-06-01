"""Per-RegioStaR-7 gravity-decay calibration (identified full-panel GLM).

Estimates one distance-decay slope per RegioStaR-7 (RS7) class from a single
Poisson GLM fitted to BA Pendleratlas Kreis-pair flows over an adaptive ring
of anchor Kreise around ZGB:

    log E[flow_ij] = origin_FE_i + dest_FE_j
                     + sum_c delta_c * d_ij * 1[RS7(origin_i) = c]

A per-origin fit with destination fixed effects is rank deficient on this data
(one flow row per origin-destination pair makes distance collinear with the
per-destination dummies), so all anchor origins are pooled into one panel:
each per-RS7 slope ``delta_c`` is identified from the within-origin distance
variation shared across the many origins of class c. See
``fit_panel_rs7_slopes``.

The anchor set is chosen by ``select_ring_anchors``: the radius around the ZGB
centroid grows until every RS7 code present in ZGB has at least
``--min-anchors-per-rs7`` Kreise.

Caveat (see ``scripts/calibrate_gravity_decay.py``): the BA Pendleratlas
contains only cross-Kreis commuters, so the raw slope level is biased flat.
The slopes are therefore rescaled so their flow-weighted mean over the
ZGB-relevant codes equals ``--rescale-to`` (the established ``gravity_slope``,
-0.065), preserving the regional mean commute level while differentiating by
RS7. The commute-distance KPI itself is MiD-P13-overridden downstream.

Usage
-----
    python -m scripts.calibrate_gravity_per_rs7 --anchor-scope ring \
        --min-anchors-per-rs7 5

Output
------
- JSON: ``eqasim-data/cache_bs/calibration/gravity_beta_per_rs7.json``
- A ``gravity_slope_by_regiostar7`` YAML snippet (paste into config_*.yml).
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from scripts.calibrate_gravity_decay import (
    DATA_DIR,
    ZGB8,
    kreis_distance_matrix,
    load_kreis_centroids,
    load_pendler,
)

REPO = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO / "eqasim-data" / "cache_bs" / "calibration" / "gravity_beta_per_rs7.json"
REGIOSTAR_XLSX = DATA_DIR / "regiostar" / "regiostar_referenzdatei.xlsx"
REGIOSTAR_SHEET = "ReferenzGebietsstand2020"

# Kept in sync with braunschweig.data.bbsr.regiostar.REGIOSTAR7_LABELS.
RS7_LABELS = {
    71: "Metropole",
    72: "Regiopole/Großstadt",
    73: "Mittelstadt/städt. Raum",
    74: "Kleinstadt/dörfl. Raum",
    75: "Zentrale Stadt (ländlich)",
    76: "Mittelstadt (ländlich)",
    77: "Kleinstadt/dörfl. (ländlich)",
}

log = logging.getLogger("calibrate_gravity_per_rs7")


def load_kreis_to_rs7() -> pd.DataFrame:
    """Return DataFrame[ars5, dominant_rs7, n_gemeinden, label]."""
    raw = pd.read_excel(REGIOSTAR_XLSX, sheet_name=REGIOSTAR_SHEET, header=0)
    df = pd.DataFrame({
        "commune_id": raw["gem_20"].astype("Int64").astype(str).str.zfill(8),
        "regiostar7": pd.to_numeric(raw["RegioStaR7"], errors="coerce").astype("Int64"),
    }).dropna()
    df["ars5"] = df["commune_id"].str[:5]
    # Dominant (most-frequent) RS7 per Kreis. Ties broken by lower code
    # (i.e. more-urban) to avoid arbitrary flips.
    counts = (
        df.groupby(["ars5", "regiostar7"]).size().rename("n").reset_index()
          .sort_values(["ars5", "n", "regiostar7"], ascending=[True, False, True])
    )
    out = counts.groupby("ars5", as_index=False).first()
    out["regiostar7"] = out["regiostar7"].astype(int)
    out = out.rename(columns={"n": "n_gemeinden", "regiostar7": "dominant_rs7"})
    out["label"] = out["dominant_rs7"].map(RS7_LABELS)
    return out[["ars5", "dominant_rs7", "n_gemeinden", "label"]]


def kreis_distance_to_zgb(kreise, zgb=ZGB8):
    """Euclidean distance (km) of each Kreis centroid to the ZGB centroid.

    The ZGB centroid is the unweighted mean of the ZGB-8 Kreis centroids.
    ``kreise`` must have columns ``ars5`` and ``centroid`` (shapely points in a
    metric CRS, as returned by ``load_kreis_centroids``).
    """
    zgb_pts = [p for a, p in zip(kreise["ars5"], kreise["centroid"]) if a in set(zgb)]
    cx = float(np.mean([p.x for p in zgb_pts]))
    cy = float(np.mean([p.y for p in zgb_pts]))
    return pd.DataFrame({
        "ars5": list(kreise["ars5"]),
        "dist_km": [float(np.hypot(p.x - cx, p.y - cy) / 1000.0) for p in kreise["centroid"]],
    })


def select_ring_anchors(dist_to_zgb, kreis_to_rs7, required_rs7,
                        min_anchors, max_radius_km, step_km=25.0):
    """Smallest radius (km) around ZGB s.t. every required RS7 code has at
    least ``min_anchors`` Kreise within it; returns (radius, anchor_ars5_list,
    per_code_counts). If the cap is hit before all codes are satisfied, the cap
    radius and whatever was collected are returned.
    """
    merged = dist_to_zgb.merge(kreis_to_rs7[["ars5", "dominant_rs7"]], on="ars5", how="inner")
    required = set(int(c) for c in required_rs7)

    def counts_within(radius):
        within = merged[merged["dist_km"] <= radius]
        in_req = within[within["dominant_rs7"].isin(required)]
        return within, in_req.groupby("dominant_rs7").size().to_dict()

    radius = step_km
    while radius <= max_radius_km:
        within, counts = counts_within(radius)
        if all(counts.get(c, 0) >= min_anchors for c in required):
            return float(radius), sorted(within["ars5"].tolist()), counts
        radius += step_km

    within, counts = counts_within(max_radius_km)
    return float(max_radius_km), sorted(within["ars5"].tolist()), counts


def fit_panel_rs7_slopes(df, origins, kreis_to_rs7, max_distance_km, rescale_to=-0.065,
                         rescale_codes=None):
    """Identified per-RS7 distance-decay slopes from ONE full-panel Poisson GLM.

    Model::

        log E[flow_ij] = origin_FE_i + dest_FE_j
                         + sum_c delta_c * d_ij * 1[RS7(origin_i) = c]

    A per-origin fit with destination fixed effects is rank deficient on the BA
    Pendleratlas (one flow row per origin-destination pair): for a single origin
    the per-destination dummies span the full row space, so distance is
    collinear with them and delta is not identified. Pooling all anchor origins
    fixes this -- each per-RS7 slope ``delta_c`` is identified from the
    within-origin distance variation shared across the many origins of class c.

    Parameters
    ----------
    df : DataFrame[orig_ars, dest_ars, distance_km, flow]
    origins : iterable of origin ARS to include as panel rows.
    kreis_to_rs7 : DataFrame[ars5, dominant_rs7] -- each origin's RS7 class.
    max_distance_km : float -- distance-band cap (km).
    rescale_to : float or None. If set, slopes are scaled so their
        flow-weighted mean equals this value (preserving the regional mean
        commute distance); None returns the raw identified slopes.

    Returns
    -------
    (slopes, diagnostics): ``slopes`` is ``{rs7_code: slope}``; ``diagnostics``
    carries raw slopes, standard errors, the flow-weighted mean, the rescale
    factor, n_obs, n_params, convergence flag, and per-code flow totals.
    """
    panel = df[(df["orig_ars"].isin(set(origins)))
               & (df["distance_km"] <= max_distance_km)
               & (df["flow"] > 0)].copy()
    panel = panel.merge(
        kreis_to_rs7[["ars5", "dominant_rs7"]].rename(columns={"ars5": "orig_ars"}),
        on="orig_ars", how="left",
    ).dropna(subset=["dominant_rs7"])
    panel["dominant_rs7"] = panel["dominant_rs7"].astype(int)

    origin_fe = pd.get_dummies(panel["orig_ars"], prefix="O", drop_first=True).astype(float)
    dest_fe = pd.get_dummies(panel["dest_ars"], prefix="G", drop_first=True).astype(float)
    codes = [int(c) for c in sorted(panel["dominant_rs7"].unique())]
    slope_cols = {
        f"d_rs{c}": (panel["distance_km"] * (panel["dominant_rs7"] == c)).astype(float).values
        for c in codes
    }
    design = sm.add_constant(
        pd.concat([origin_fe, dest_fe, pd.DataFrame(slope_cols, index=panel.index)], axis=1),
        has_constant="add",
    )
    result = sm.GLM(
        panel["flow"].astype(float).to_numpy(),
        design.to_numpy(),
        family=sm.families.Poisson(),
    ).fit(maxiter=300, tol=1e-8)

    raw = {c: float(result.params[design.columns.get_loc(f"d_rs{c}")]) for c in codes}
    se = {c: float(result.bse[design.columns.get_loc(f"d_rs{c}")]) for c in codes}
    flow_by_code = panel.groupby("dominant_rs7")["flow"].sum()
    # Anchor the level on the codes that are actually applied downstream
    # (``rescale_codes``, e.g. the RS7 codes present in ZGB). Including codes
    # that never occur in the study area (e.g. 71 Metropole) in the mean would
    # bias the applied codes' mean away from ``rescale_to``.
    mean_codes = [c for c in codes if (rescale_codes is None or c in set(rescale_codes))]
    weighted_mean = float(np.average([raw[c] for c in mean_codes],
                                     weights=[flow_by_code[c] for c in mean_codes]))
    scale = (rescale_to / weighted_mean) if (rescale_to is not None and weighted_mean != 0.0) else 1.0
    slopes = {c: round(raw[c] * scale, 4) for c in codes}
    diagnostics = {
        "raw_slope": raw,
        "standard_error": se,
        "flow_weighted_mean_raw": weighted_mean,
        "rescale_factor": scale,
        "n_obs": int(design.shape[0]),
        "n_params": int(design.shape[1]),
        "converged": bool(result.converged),
        "flow_total_by_code": {c: int(flow_by_code[c]) for c in codes},
    }
    return slopes, diagnostics


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-distance", type=float, default=250.0)
    parser.add_argument(
        "--rescale-to", type=float, default=-0.065,
        help=("Rescale per-RS7 β so the flow-weighted mean equals this "
              "value (the current synth gravity_slope). Pass 0 to disable."),
    )
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--anchor-scope", choices=["zgb", "nds", "ring", "germany"],
                        default="ring",
                        help="Which origin Kreise to fit. 'ring' grows a radius "
                             "around ZGB until each ZGB RS7 code has "
                             "--min-anchors-per-rs7 anchors.")
    parser.add_argument("--min-anchors-per-rs7", type=int, default=5)
    parser.add_argument("--max-radius-km", type=float, default=600.0)
    args = parser.parse_args()
    rescale = None if args.rescale_to == 0 else args.rescale_to

    log.info("Loading BA Pendleratlas + Kreis distances …")
    flows = load_pendler()
    kreise = load_kreis_centroids()
    distances = kreis_distance_matrix(kreise)
    df = flows.merge(distances, on=["orig_ars", "dest_ars"], how="inner")

    log.info("Loading RegioStaR Kreis→RS7 mapping …")
    kreis_to_rs7 = load_kreis_to_rs7()
    rs7_zgb = kreis_to_rs7[kreis_to_rs7["ars5"].isin(ZGB8)]
    log.info("ZGB-8 dominant RS7:")
    for _, row in rs7_zgb.sort_values("ars5").iterrows():
        log.info("  %s → %d %s", row["ars5"], row["dominant_rs7"], row["label"])

    # RS7 codes actually present among ZGB-8 Gemeinden (per-Gemeinde): the
    # codes the config dict must cover.
    raw_rs7 = pd.read_excel(REGIOSTAR_XLSX, sheet_name=REGIOSTAR_SHEET, header=0)
    raw_rs7 = pd.DataFrame({
        "ars5": raw_rs7["gem_20"].astype("Int64").astype(str).str.zfill(8).str[:5],
        "rs7": pd.to_numeric(raw_rs7["RegioStaR7"], errors="coerce").astype("Int64"),
    }).dropna()
    required_rs7 = set(int(c) for c in raw_rs7[raw_rs7["ars5"].isin(ZGB8)]["rs7"].unique())
    log.info("RS7 codes present in ZGB-8: %s", sorted(required_rs7))

    dist_to_zgb = kreis_distance_to_zgb(kreise)

    if args.anchor_scope == "zgb":
        anchors = list(ZGB8)
    elif args.anchor_scope == "nds":
        anchors = [a for a in sorted(df["orig_ars"].unique()) if a.startswith("03")]
    elif args.anchor_scope == "germany":
        anchors = sorted(df["orig_ars"].unique())
    else:  # ring
        radius, anchors, counts = select_ring_anchors(
            dist_to_zgb, kreis_to_rs7, required_rs7,
            min_anchors=args.min_anchors_per_rs7, max_radius_km=args.max_radius_km)
        log.info("Adaptive ring radius = %.0f km; per-RS7 anchor counts = %s", radius, counts)
        missing = [c for c in required_rs7 if counts.get(c, 0) < args.min_anchors_per_rs7]
        if missing:
            log.warning("RS7 codes still under-filled at max radius: %s", missing)

    log.info("Fitting full-panel Poisson-GLM (origin FE + dest FE + RS7xdistance), "
             "max_d=%.0f km, scope=%s, n_anchors=%d", args.max_distance, args.anchor_scope, len(anchors))
    slopes, diag = fit_panel_rs7_slopes(df, anchors, kreis_to_rs7, args.max_distance,
                                        rescale_to=rescale, rescale_codes=required_rs7)
    log.info("panel: n_obs=%d n_params=%d converged=%s flow_wtd_mean_raw=%.5f rescale_factor=%.3f",
             diag["n_obs"], diag["n_params"], diag["converged"],
             diag["flow_weighted_mean_raw"], diag["rescale_factor"])

    # Cross-scope sensitivity (same identified estimator on nds / germany).
    all_orig = sorted(df["orig_ars"].unique())
    nds = [a for a in all_orig if a.startswith("03")]
    sens = pd.DataFrame({
        "scope": pd.Series(slopes),
        "nds": pd.Series(fit_panel_rs7_slopes(df, nds, kreis_to_rs7, args.max_distance,
                                              rescale_to=rescale, rescale_codes=required_rs7)[0]),
        "germany": pd.Series(fit_panel_rs7_slopes(df, all_orig, kreis_to_rs7, args.max_distance,
                                                  rescale_to=rescale, rescale_codes=required_rs7)[0]),
    }).sort_index()
    print("\n=== per-RS7 slope sensitivity (identified panel, rescaled), by scope ===")
    print(sens.round(4).to_string())

    print("\n=== Per-RS7 distance slope (identified full-panel GLM) ===")
    print(f"  scope={args.anchor_scope}  n_obs={diag['n_obs']}  n_params={diag['n_params']}  "
          f"converged={diag['converged']}  rescale_factor={diag['rescale_factor']:+.3f}")
    for c in sorted(slopes):
        print(f"  {c} {RS7_LABELS.get(c, ''):35s} slope={slopes[c]:+.4f}  "
              f"raw={diag['raw_slope'][c]:+.5f}  se={diag['standard_error'][c]:.5f}  "
              f"flow={diag['flow_total_by_code'][c]}")

    print("\n=== YAML snippet for config (gravity_slope_by_regiostar7) ===")
    if rescale is not None:
        print(f"# Identified full-panel GLM (origin FE + dest FE + RS7xdistance), scope={args.anchor_scope}.")
        print(f"# Rescaled so flow-weighted mean slope equals {rescale:+.4f}.")
    print("gravity_slope_by_regiostar7:")
    for c in sorted(slopes):
        print(f"  {c}: {slopes[c]:+.4f}  # {RS7_LABELS.get(c, '')} (raw={diag['raw_slope'][c]:+.5f})")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "method": "full_panel_poisson_glm_origin_dest_fe_rs7_distance_interaction",
        "anchor_scope": args.anchor_scope,
        "max_distance_km": args.max_distance,
        "rescale_to": rescale,
        "slopes": slopes,
        "diagnostics": diag,
        "anchors": anchors,
    }
    args.out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info("Calibration written to %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
