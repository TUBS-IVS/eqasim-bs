"""Prepare aggregates for the income-in-space deck figure.

Inputs (100% run, popsim_mid all-features):
- households CSV (semicolon): household_id, household_income_eur (monthly EUR)
- homes.gpkg: household_id, Point EPSG:25832
- simwrapper/kreis_socio.geojson: Kreis polygons (ars5), EPSG:4326
- analysis/population_validation/agg_kreis.csv: per-Kreis income mean/median (cross-check)

Outputs (scratchpad): income_prep.npz + kreis boundaries reprojected gpkg + stats printout.
"""
import json

import numpy as np
import pandas as pd
import geopandas as gpd

BASE = "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim/"
FD = "C:/Users/BIENZE~1/AppData/Local/Temp/claude/c--Users-bienzeisler-Documents-GitHub-eqasim-bs/b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/"

hh = pd.read_csv(BASE + "braunschweig_100pct_allfeat_popsim_households.csv", sep=";",
                 usecols=["household_id", "household_income_eur"])
print("households:", len(hh), "income NA:", hh.household_income_eur.isna().sum())
print(hh.household_income_eur.describe())

homes = gpd.read_file(BASE + "braunschweig_100pct_allfeat_popsim_homes.gpkg")
print("homes:", len(homes), "cols:", list(homes.columns), "crs:", homes.crs)

df = homes.merge(hh, on="household_id", how="inner")
print("joined:", len(df))
x = df.geometry.x.to_numpy()
y = df.geometry.y.to_numpy()
inc = df.household_income_eur.to_numpy(float)

# Kreis polygons
kr = gpd.read_file(BASE + "simwrapper/kreis_socio.geojson")
print("kreis cols:", list(kr.columns)[:15], "crs:", kr.crs)
kr = kr.to_crs(25832)
kr.to_file(FD + "kreis_25832.gpkg", driver="GPKG")

# Per-Kreis realized means via point-in-polygon (spatial join)
pts = gpd.GeoDataFrame(df[["household_income_eur"]], geometry=df.geometry, crs=25832)
name_col = [c for c in kr.columns if c.lower() in ("gen", "name", "kreis_name", "nuts_name")]
print("candidate name cols:", name_col)
join = gpd.sjoin(pts, kr, how="inner", predicate="within")
ars_col = "ars5" if "ars5" in kr.columns else None
per_kreis = join.groupby(ars_col)["household_income_eur"].agg(["mean", "median", "count"])
print(per_kreis)

# Cross-check vs agg_kreis.csv
agg = pd.read_csv(BASE + "analysis/population_validation/agg_kreis.csv",
                  usecols=["ars5", "household_income_eur_mean", "household_income_eur_median", "n_households"],
                  dtype={"ars5": str})
agg = agg.set_index("ars5")
cmp = per_kreis.join(agg, how="outer")
cmp["diff_mean"] = cmp["mean"] - cmp["household_income_eur_mean"]
print(cmp[["mean", "household_income_eur_mean", "diff_mean", "count", "n_households"]])
cmp.to_csv(FD + "per_kreis_crosscheck.csv")


def grid_stats(x, y, inc, cell, x0, y0, x1, y1):
    ix = np.floor((x - x0) / cell).astype(int)
    iy = np.floor((y - y0) / cell).astype(int)
    nx = int(np.ceil((x1 - x0) / cell))
    ny = int(np.ceil((y1 - y0) / cell))
    m = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    flat = iy[m] * nx + ix[m]
    cnt = np.bincount(flat, minlength=nx * ny).reshape(ny, nx)
    ssum = np.bincount(flat, weights=inc[m], minlength=nx * ny).reshape(ny, nx)
    with np.errstate(invalid="ignore"):
        mean = np.where(cnt > 0, ssum / np.maximum(cnt, 1), np.nan)
    return mean, cnt, nx, ny


# Region grid 1 km over full ZGB extent (from kreis polygons, small pad)
xb0, yb0, xb1, yb1 = kr.total_bounds
pad = 2000
rx0, ry0 = np.floor((xb0 - pad) / 1000) * 1000, np.floor((yb0 - pad) / 1000) * 1000
rx1, ry1 = np.ceil((xb1 + pad) / 1000) * 1000, np.ceil((yb1 + pad) / 1000) * 1000
mean_r, cnt_r, nxr, nyr = grid_stats(x, y, inc, 1000, rx0, ry0, rx1, ry1)
valid_r = cnt_r >= 25
vals = mean_r[valid_r]
print(f"region 1km cells valid(>=25 HH): {valid_r.sum()} / nonzero {int((cnt_r>0).sum())}")
print("region cell mean p2/p50/p98:", np.percentile(vals, [2, 50, 98]))

# City zoom: Kreis 03101 bounds + pad, 400 m cells
bs = kr[kr["ars5"] == "03101"] if "ars5" in kr.columns else kr.iloc[[0]]
cb = bs.total_bounds
cpad = 1200
cx0, cy0 = np.floor((cb[0] - cpad) / 400) * 400, np.floor((cb[1] - cpad) / 400) * 400
cx1, cy1 = np.ceil((cb[2] + cpad) / 400) * 400, np.ceil((cb[3] + cpad) / 400) * 400
mean_c, cnt_c, nxc, nyc = grid_stats(x, y, inc, 400, cx0, cy0, cx1, cy1)
valid_c = cnt_c >= 15
vc = mean_c[valid_c]
print(f"city 400m cells valid(>=15 HH): {valid_c.sum()} / nonzero {int((cnt_c>0).sum())}")
print("city cell mean p2/p50/p98:", np.percentile(vc, [2, 50, 98]))

np.savez_compressed(
    FD + "income_prep.npz",
    mean_r=mean_r, cnt_r=cnt_r, region_extent=np.array([rx0, rx1, ry0, ry1]),
    mean_c=mean_c, cnt_c=cnt_c, city_extent=np.array([cx0, cx1, cy0, cy1]),
)
per_kreis.to_csv(FD + "per_kreis_realized.csv")
print("saved.")
