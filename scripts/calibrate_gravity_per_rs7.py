"""Per-RegioStaR-7 gravity-decay calibration.

Estimates one Poisson-GLM distance-decay slope β_k per ZGB-8 origin
Kreis on BA Pendleratlas flows, aggregates the β_k by the dominant
RegioStaR-7 class of the origin (population/Gemeinde-count-weighted),
and emits a YAML snippet ready to paste under
``gravity_slope_by_regiostar7`` in the local config.

Methodology
-----------
For each origin Kreis k in ZGB-8:

    log E[flow_{k,j}] = γ_j + β_k · d_{k,j}

with destination fixed effects γ_j over all Kreise j != k within the
configured max-distance band. β_k is the Poisson-MLE distance slope.

Aggregation per RS7 c:

    β_c = Σ_k w_k · β_k    over Kreise k whose dominant RS7 = c
                            with w_k = (Σ_j flow_{k,j}) / Σ_{k' in c} ...

i.e. flow-volume-weighted mean. The dominant RS7 of a Kreis is the
mode of the per-Gemeinde RegioStaR-7 codes from the BMV reference
(``eqasim-data/data/regiostar/regiostar_referenzdatei.xlsx``).

Caveats — read the long methodological note in
``scripts/calibrate_gravity_decay.py`` first. BA Pendleratlas only
contains cross-Kreis commuters, so β_c governs the *cross-Kreis*
distance distribution. Synth-level mean commute km vs. MiD P13 still
needs the empirical synth-interpolation step from the existing
calibration script.

Usage
-----
    python -m scripts.calibrate_gravity_per_rs7 [--max-distance 250]

Output
------
- JSON: ``eqasim-data/cache_bs/calibration/gravity_beta_per_rs7.json``
- YAML snippet printed to stdout, e.g.::

    gravity_slope_by_regiostar7:
      72: -0.058
      73: -0.072
      74: -0.081
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
MID_P13_CSV = DATA_DIR / "braunschweig" / "mid" / "mid2023_P13.csv"

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


def fit_per_kreis_beta(
    df: pd.DataFrame, origin: str, max_distance_km: float
) -> dict | None:
    """Fit Poisson-GLM with destination FE for one origin Kreis."""
    sub = df[(df["orig_ars"] == origin) & (df["distance_km"] <= max_distance_km)]
    sub = sub[sub["flow"] > 0]
    if len(sub) < 10:
        log.warning("  %s: only %d obs, skipping", origin, len(sub))
        return None
    D = pd.get_dummies(sub["dest_ars"], prefix="D", drop_first=True).astype(float)
    X = pd.concat([D, sub[["distance_km"]]], axis=1)
    X = sm.add_constant(X, has_constant="add")
    y = sub["flow"].astype(float).to_numpy()
    res = sm.GLM(y, X.to_numpy(), family=sm.families.Poisson()).fit(maxiter=200, tol=1e-8)
    beta_idx = X.columns.get_loc("distance_km")
    beta = float(res.params[beta_idx])
    se = float(res.bse[beta_idx])
    flow_total = int(sub["flow"].sum())
    mean_d = float((sub["flow"] * sub["distance_km"]).sum() / sub["flow"].sum())
    return {
        "ars5": origin,
        "beta": beta,
        "se": se,
        "z": beta / se if se > 0 else float("nan"),
        "n_obs": int(len(sub)),
        "flow_total": flow_total,
        "mean_distance_km_observed": round(mean_d, 2),
        "converged": bool(res.converged),
    }


def aggregate_by_rs7(
    per_kreis: list[dict], kreis_to_rs7: pd.DataFrame, rescale_to: float | None,
) -> pd.DataFrame:
    df = pd.DataFrame(per_kreis).merge(kreis_to_rs7, on="ars5", how="left")
    if df["dominant_rs7"].isna().any():
        missing = df.loc[df["dominant_rs7"].isna(), "ars5"].tolist()
        raise RuntimeError(f"No RS7 mapping for Kreise: {missing}")
    rows = []
    for (rs7, label), g in df.groupby(["dominant_rs7", "label"], sort=True):
        w = g["flow_total"].to_numpy()
        rows.append({
            "dominant_rs7": int(rs7),
            "label": label,
            "n_kreise": int(len(g)),
            "kreise": ",".join(sorted(g["ars5"].tolist())),
            "flow_total": int(g["flow_total"].sum()),
            "mean_d_km_obs": round(float(np.average(g["mean_distance_km_observed"], weights=w)), 2),
            "beta_rs7_raw": float(np.average(g["beta"], weights=w)),
        })
    out = pd.DataFrame(rows).sort_values("dominant_rs7").reset_index(drop=True)
    if rescale_to is not None:
        # Rescale so the flow-weighted mean of β_rs7 equals ``rescale_to``
        # (the current synth gravity_slope). This preserves the synth-level
        # mean commute distance while letting RS7 classes diverge in
        # relative steepness.
        w_all = out["flow_total"].to_numpy()
        mean_raw = float(np.average(out["beta_rs7_raw"], weights=w_all))
        if mean_raw == 0:
            raise RuntimeError("Mean raw β is zero, cannot rescale.")
        scale = rescale_to / mean_raw
        out["beta_rs7"] = (out["beta_rs7_raw"] * scale).round(4)
        out["rescale_factor"] = round(scale, 4)
    else:
        out["beta_rs7"] = out["beta_rs7_raw"].round(4)
        out["rescale_factor"] = 1.0
    return out


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


def load_mid_mean() -> pd.DataFrame:
    df = pd.read_csv(MID_P13_CSV, dtype={"ars5": str})
    return df[["ars5", "kreis", "mittel"]].rename(columns={"mittel": "mid_mean_km"})


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

    log.info("Fitting Poisson-GLM per origin Kreis (max_d=%.0f km)", args.max_distance)
    per_kreis: list[dict] = []
    for ars in ZGB8:
        fit = fit_per_kreis_beta(df, ars, args.max_distance)
        if fit is None:
            continue
        log.info(
            "  %s: β=%+.4f ± %.4f (z=%5.1f, n=%3d, obs_mean_d=%.1f km)",
            ars, fit["beta"], fit["se"], fit["z"], fit["n_obs"],
            fit["mean_distance_km_observed"],
        )
        per_kreis.append(fit)

    rs7_table = aggregate_by_rs7(per_kreis, kreis_to_rs7, rescale_to=rescale)
    mid_means = load_mid_mean()
    df_kreis = (
        pd.DataFrame(per_kreis)
          .merge(kreis_to_rs7[["ars5", "dominant_rs7", "label"]], on="ars5", how="left")
          .merge(mid_means, on="ars5", how="left")
          .sort_values("ars5")
    )

    print("\n=== Per-Kreis β fits (BA Pendleratlas, cross-Kreis flows) ===")
    print(df_kreis[[
        "ars5", "dominant_rs7", "label", "beta", "se", "n_obs", "flow_total",
        "mean_distance_km_observed", "mid_mean_km",
    ]].round(4).to_string(index=False))

    print("\n=== Aggregated β per RegioStaR-7 (flow-weighted) ===")
    if rescale is not None:
        print(f"  rescale_to = {rescale:+.4f}  (factor = {rs7_table['rescale_factor'].iloc[0]:+.3f})")
    print(rs7_table[[
        "dominant_rs7", "label", "n_kreise", "flow_total",
        "mean_d_km_obs", "beta_rs7_raw", "beta_rs7",
    ]].round(4).to_string(index=False))

    print("\n=== YAML snippet for config (gravity_slope_by_regiostar7) ===")
    if rescale is not None:
        print(f"# Rescaled so flow-weighted mean β equals {rescale:+.4f} "
              "(= current default gravity_slope).")
    print("gravity_slope_by_regiostar7:")
    for _, row in rs7_table.iterrows():
        print(f"  {int(row['dominant_rs7'])}: {row['beta_rs7']:+.4f}  # {row['label']} (n_kreise={int(row['n_kreise'])}, raw={row['beta_rs7_raw']:+.4f})")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "per_kreis": df_kreis.to_dict(orient="records"),
        "per_rs7": rs7_table.to_dict(orient="records"),
        "max_distance_km": args.max_distance,
        "scope": list(ZGB8),
    }
    args.out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info("Calibration written to %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
