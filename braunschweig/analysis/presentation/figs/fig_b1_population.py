# -*- coding: utf-8 -*-
"""
Figure B1 -- "Die synthetische Bevoelkerung" (two-panel census-style poster map).

LEFT : person-weighted home-location density (150 m bins, magma, LogNorm).
        homes.gpkg has one point per household (column household_id); weights
        come from households.csv household_size, so the raster shows PERSONS.
RIGHT: mean household size per 1 km bin (viridis), bins with < 25 households
        rendered transparent.

Data (real, no synthesis):
  homes:      braunschweig_100pct_allfeat_popsim_homes.gpkg  (EPSG:25832)
  households: braunschweig_100pct_allfeat_popsim_households.csv (sep=';')
  boundaries: simwrapper/kreis_socio.geojson (EPSG:4326 -> 25832)
"""
import os

import matplotlib
matplotlib.use("Agg")

import geopandas as gpd
import matplotlib.font_manager as fm
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm

BG = "#0a0e14"
FG_TITLE = "#eef3fb"
FG_SUB = "#8b95a7"
FG_CREDIT = "#5a6577"
KREIS_EDGE = "#33465f"
NEON = ["#22d3ee", "#d946ef", "#fb7185"]

BASE = "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim"
HOMES_GPKG = f"{BASE}/braunschweig_100pct_allfeat_popsim_homes.gpkg"
HOUSEHOLDS_CSV = f"{BASE}/braunschweig_100pct_allfeat_popsim_households.csv"
KREIS_GEOJSON = f"{BASE}/simwrapper/kreis_socio.geojson"
FONT_DIR = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts"
OUT_PNG = ("C:/Users/BIENZE~1/AppData/Local/Temp/claude/"
           "c--Users-bienzeisler-Documents-GitHub-eqasim-bs/"
           "b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/fig_b1_population.png")


def de(n):
    """German thousands separator."""
    return f"{n:,}".replace(",", ".")


# ---------------------------------------------------------------- fonts
mono = "DejaVu Sans Mono"
if os.path.isdir(FONT_DIR):
    for f in os.listdir(FONT_DIR):
        if f.lower().endswith(".ttf"):
            fm.fontManager.addfont(os.path.join(FONT_DIR, f))
    if any("Space Mono" in f.name for f in fm.fontManager.ttflist):
        mono = "Space Mono"
plt.rcParams["font.family"] = mono

# ---------------------------------------------------------------- data
homes = gpd.read_file(HOMES_GPKG)  # 558k points, EPSG:25832
assert homes.crs is not None and homes.crs.to_epsg() == 25832, "unexpected CRS in homes.gpkg"
hh = pd.read_csv(HOUSEHOLDS_CSV, sep=";", usecols=["household_id", "household_size"])

df = pd.DataFrame({
    "household_id": homes["household_id"].values,
    "x": homes.geometry.x.values,
    "y": homes.geometry.y.values,
}).merge(hh, on="household_id", how="left", validate="one_to_one")

n_missing = int(df["household_size"].isna().sum())
if n_missing > 0:
    raise RuntimeError(f"{n_missing} homes without household_size after join -- join is broken")

n_households = len(df)
n_persons = int(df["household_size"].sum())
mean_size = n_persons / n_households
print(f"households: {n_households:,}   persons (sum household_size): {n_persons:,}   mean size: {mean_size:.2f}")

kreis = gpd.read_file(KREIS_GEOJSON).to_crs(25832)

# ---------------------------------------------------------------- extent (shared)
pad = 2_000.0
x0, x1 = df["x"].min() - pad, df["x"].max() + pad
y0, y1 = df["y"].min() - pad, df["y"].max() + pad
extent = (x0, x1, y0, y1)
map_aspect = (x1 - x0) / (y1 - y0)  # width / height in data units (~0.58, tall region)
print(f"extent: {(x1 - x0) / 1000:.1f} km x {(y1 - y0) / 1000:.1f} km  (w/h = {map_aspect:.3f})")

# ---------------------------------------------------------------- binning
# LEFT: 150 m bins, person-weighted counts
bs_l = 150.0
xe_l = np.arange(x0, x1 + bs_l, bs_l)
ye_l = np.arange(y0, y1 + bs_l, bs_l)
H_pers, _, _ = np.histogram2d(df["x"], df["y"], bins=[xe_l, ye_l], weights=df["household_size"])
H_pers = np.ma.masked_where(H_pers < 1.0, H_pers)
vmax_l = np.percentile(H_pers.compressed(), 99.5)  # clip hotspots so the city cores glow
print(f"density cells: {H_pers.count():,}, max {H_pers.max():.0f}, vmax(99.5%) {vmax_l:.0f}")

# RIGHT: 1 km bins, mean household size = sum(size)/count, mask thin bins
bs_r = 1_000.0
xe_r = np.arange(x0, x1 + bs_r, bs_r)
ye_r = np.arange(y0, y1 + bs_r, bs_r)
H_size_sum, _, _ = np.histogram2d(df["x"], df["y"], bins=[xe_r, ye_r], weights=df["household_size"])
H_count, _, _ = np.histogram2d(df["x"], df["y"], bins=[xe_r, ye_r])
MIN_HH = 25
with np.errstate(invalid="ignore", divide="ignore"):
    H_mean = H_size_sum / H_count
H_mean = np.ma.masked_where(H_count < MIN_HH, H_mean)
v_lo, v_hi = np.percentile(H_mean.compressed(), [2, 98])
print(f"mean-hh-size bins kept: {H_mean.count():,} (>= {MIN_HH} HH), range shown {v_lo:.2f}-{v_hi:.2f}")

# ---------------------------------------------------------------- figure layout
FIG_W, FIG_H = 16.0, 8.5
fig = plt.figure(figsize=(FIG_W, FIG_H), facecolor=BG)

# map axes sized so the box aspect equals the data aspect (maps fill their boxes)
AX_H = 0.84                                   # fig fraction
AX_W = AX_H * FIG_H * map_aspect / FIG_W      # fig fraction
axL = fig.add_axes([0.300, 0.055, AX_W, AX_H])
axR = fig.add_axes([0.660, 0.055, AX_W, AX_H])
caxL = fig.add_axes([0.300 + AX_W + 0.008, 0.26, 0.0065, 0.42])
caxR = fig.add_axes([0.660 + AX_W + 0.008, 0.26, 0.0065, 0.42])

cm_l = plt.get_cmap("magma").copy()
cm_l.set_bad(BG)
cm_r = plt.get_cmap("viridis").copy()
cm_r.set_bad(BG)

for ax in (axL, axR):
    ax.set_facecolor(BG)
    ax.set_axis_off()
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")

# ---- left: person density
imL = axL.imshow(H_pers.T, origin="lower", extent=extent, cmap=cm_l,
                 norm=LogNorm(vmin=1, vmax=vmax_l), interpolation="nearest", zorder=1)
kreis.boundary.plot(ax=axL, color=KREIS_EDGE, linewidth=0.7, zorder=3)

# ---- right: mean household size
imR = axR.imshow(H_mean.T, origin="lower", extent=extent, cmap=cm_r,
                 vmin=v_lo, vmax=v_hi, interpolation="nearest", zorder=1)
kreis.boundary.plot(ax=axR, color=KREIS_EDGE, linewidth=0.7, zorder=3)

# ---- Kreis labels (official ARS5 codes of the ZGB Kreise)
KREIS_NAMES = {
    "03101": "Braunschweig", "03102": "Salzgitter", "03103": "Wolfsburg",
    "03151": "Gifhorn", "03153": "Goslar", "03154": "Helmstedt",
    "03157": "Peine", "03158": "Wolfenbüttel",
}
CITY = {"03101", "03102", "03103"}  # kreisfreie Staedte -> brighter label
for ax in (axL, axR):
    for _, row in kreis.iterrows():
        name = KREIS_NAMES.get(str(row["ars5"]))
        if name is None:
            continue
        pt = row.geometry.representative_point()
        big = str(row["ars5"]) in CITY
        ax.annotate(name, (pt.x, pt.y), ha="center", va="center",
                    fontsize=8.5 if big else 7.5,
                    color="#d5deeb" if big else "#7d8aa0",
                    zorder=4,
                    path_effects=[pe.withStroke(linewidth=2.4, foreground=BG, alpha=0.8)])

# ---- colorbars (thin)
cbL = fig.colorbar(imL, cax=caxL)
cbL.set_label("Personen je 150-m-Zelle (log. Skala)", color=FG_SUB, fontsize=8.5)
cbR = fig.colorbar(imR, cax=caxR)
cbR.set_label("Personen je Haushalt", color=FG_SUB, fontsize=8.5)
for cb in (cbL, cbR):
    cb.outline.set_visible(False)
    cb.ax.tick_params(color=FG_SUB, labelcolor=FG_SUB, labelsize=8, length=2.5)

# ---- panel titles + tiny method captions
axL.set_title("Wohnstandorte — Personendichte", color=FG_TITLE, fontsize=12.5, loc="left", pad=10)
axR.set_title("Mittlere Haushaltsgröße", color=FG_TITLE, fontsize=12.5, loc="left", pad=10)
fig.text(0.300, 0.030, "150-m-Raster, logarithmische Farbskala",
         color=FG_CREDIT, fontsize=7.5, ha="left", va="bottom")
fig.text(0.660, 0.030, f"1-km-Raster, haushaltsgewichtet; Zellen mit < {MIN_HH} Haushalten ausgeblendet",
         color=FG_CREDIT, fontsize=7.5, ha="left", va="bottom")

# ---- left text column: title, subtitle, key figures (all computed from the data)
tx = 0.030
fig.text(tx, 0.945, "Die synthetische Bevölkerung", color=FG_TITLE, fontsize=17,
         fontweight="bold", ha="left", va="top")
fig.text(tx, 0.888, "Region Braunschweig (ZGB)\n100-%-Synthese, ein Wohnstandort\nje Haushalt (PopulationSim)",
         color=FG_SUB, fontsize=10, ha="left", va="top", linespacing=1.5)

stats = [
    (de(n_persons), "synthetische Personen", NEON[0]),
    (de(n_households), "Haushalte", NEON[1]),
    ("Ø " + f"{mean_size:.2f}".replace(".", ","), "Personen je Haushalt", NEON[2]),
]
y_stat = 0.68
for value, label, color in stats:
    fig.text(tx, y_stat, value, color=color, fontsize=21, fontweight="bold", ha="left", va="top")
    fig.text(tx, y_stat - 0.048, label, color=FG_SUB, fontsize=9.5, ha="left", va="top")
    y_stat -= 0.125

fig.text(tx, 0.030,
         "eqasim-bs | 100-%-Lauf all-features (PopulationSim),\n"
         "Juni 2026 | homes.gpkg + households.csv | EPSG:25832",
         color=FG_CREDIT, fontsize=7.5, ha="left", va="bottom", linespacing=1.6)

fig.savefig(OUT_PNG, dpi=170, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("saved:", OUT_PNG)
