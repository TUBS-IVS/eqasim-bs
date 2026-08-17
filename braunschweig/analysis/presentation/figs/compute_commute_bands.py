# Compute the commute distance-band distribution (model vs MiD P13) for the deck figure.
#
# Methodology = the project's committed distance-fit convention:
#   - model: synthetic home->work straight-line distance (commutes.gpkg, EPSG:25832 metres)
#     scaled by the documented detour factor 1.3 (euclidean -> routed axis,
#     braunschweig/calibration/metrics.py DETOUR_FACTOR)
#   - reference: MiD 2023 Tabelle A P13, ZGB aggregate row (committed CSV
#     eqasim-data/data/braunschweig/mid/mid2023_P13.csv), d_0 merged into the 0-5 band,
#     keine_feste_arbeit / keine_angabe excluded, renormalised (braunschweig/calibration/targets.py)
#   - bands: BAND_EDGES_KM = (0, 5, 10, 20, 30, 50, 100, inf)
#   - EMD: mean |CDF diff| / (n_bands - 1)  (braunschweig/calibration/metrics.py emd_on_bands)
import json

import numpy as np
import pandas as pd
import geopandas as gpd

GPKG = "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim/braunschweig_100pct_allfeat_popsim_commutes.gpkg"
P13 = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/eqasim-data/data/braunschweig/mid/mid2023_P13.csv"
OUT = "C:/Users/BIENZE~1/AppData/Local/Temp/claude/c--Users-bienzeisler-Documents-GitHub-eqasim-bs/b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/commute_bands.json"

DETOUR_FACTOR = 1.3  # committed convention (braunschweig.calibration.metrics.DETOUR_FACTOR)
EDGES_KM = [0.0, 5.0, 10.0, 20.0, 30.0, 50.0, 100.0, float("inf")]

# --- model: straight-line home->work lengths ---
gdf = gpd.read_file(GPKG)
n_total = len(gdf)
length_km = gdf.geometry.length.to_numpy() / 1000.0  # metres (EPSG:25832) -> km
routed_km = length_km * DETOUR_FACTOR

bands = np.digitize(routed_km, EDGES_KM[1:-1], right=False)  # 0..6
counts = np.bincount(bands, minlength=7).astype(float)
sim_shares = counts / counts.sum()

# --- reference: MiD P13 ZGB aggregate ---
p13 = pd.read_csv(P13, comment="#")
row = p13[p13["ars5"] == "03ZGB"].iloc[0]
mid_bands = np.array([
    row["d_0"] + row["d_0_5"], row["d_5_10"], row["d_10_20"],
    row["d_20_30"], row["d_30_50"], row["d_50_100"], row["d_100p"],
], dtype=float)
mid_shares = mid_bands / mid_bands.sum()

# --- EMD on band shares (project convention) ---
cdf_diff = np.cumsum(sim_shares) - np.cumsum(mid_shares)
emd = float(np.abs(cdf_diff[:-1]).sum() / (len(sim_shares) - 1))

result = {
    "n_commutes": int(n_total),
    "detour_factor": DETOUR_FACTOR,
    "edges_km": EDGES_KM[:-1] + ["inf"],
    "sim_shares_pct": [round(100 * s, 2) for s in sim_shares],
    "mid_shares_pct": [round(100 * s, 2) for s in mid_shares],
    "emd": round(emd, 4),
    "sim_mean_routed_km": round(float(routed_km.mean()), 2),
    "sim_mean_euclid_km": round(float(length_km.mean()), 2),
    "mid_mittel_km": float(row["mittel"]),
    "mid_n_weighted": float(row["n_weighted"]),
    "mid_n_unweighted": float(row["n_unweighted"]),
}
with open(OUT, "w") as f:
    json.dump(result, f, indent=1)
print(json.dumps(result, indent=1))
