# Figure B2: "Mobilitaetsmerkmale im Raum" - two-panel 1 km rate map.
# LEFT : mean number_of_cars per household (households.csv joined to homes.gpkg)
# RIGHT: share of persons with has_pt_subscription == True (person-weighted)
# Real data only: 100% all-features PopulationSim run (exported 2026-06-30, EPSG:25832).
import os

import matplotlib

matplotlib.use("Agg")
import geopandas as gpd
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import Normalize

BASE = "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim"
HOMES_GPKG = f"{BASE}/braunschweig_100pct_allfeat_popsim_homes.gpkg"
HOUSEHOLDS_CSV = f"{BASE}/braunschweig_100pct_allfeat_popsim_households.csv"
PERSONS_CSV = f"{BASE}/braunschweig_100pct_allfeat_popsim_persons.csv"
KREIS_GEOJSON = f"{BASE}/simwrapper/kreis_socio.geojson"
FONT_DIR = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts"
OUT_PNG = ("C:/Users/BIENZE~1/AppData/Local/Temp/claude/"
           "c--Users-bienzeisler-Documents-GitHub-eqasim-bs/"
           "b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/fig_b2_attributes.png")

BG = "#0a0e14"
TITLE_COLOR = "#eef3fb"
SUB_COLOR = "#8b95a7"
CREDIT_COLOR = "#5a6577"
KREIS_EDGE = "#33465f"
BIN_METERS = 1000.0
MIN_WEIGHT = 25.0  # minimum weighted observations per 1 km bin

# ---------------------------------------------------------------- fonts
for ttf in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
    path = os.path.join(FONT_DIR, ttf)
    if os.path.exists(path):
        fm.fontManager.addfont(path)
if any("Space Mono" in f.name for f in fm.fontManager.ttflist):
    plt.rcParams["font.family"] = "Space Mono"
else:
    plt.rcParams["font.family"] = "DejaVu Sans Mono"

# ---------------------------------------------------------------- data
print("reading homes gpkg ...")
homes = gpd.read_file(HOMES_GPKG)  # household_id + POINT, EPSG:25832
homes["x"] = homes.geometry.x
homes["y"] = homes.geometry.y
homes_df = pd.DataFrame(homes[["household_id", "x", "y"]])
del homes

print("reading households csv ...")
hh = pd.read_csv(HOUSEHOLDS_CSV, sep=";", usecols=["household_id", "number_of_cars"])
hh["number_of_cars"] = pd.to_numeric(hh["number_of_cars"], errors="coerce")
n_hh_total = len(hh)
hh = hh.dropna(subset=["number_of_cars"])
print(f"households: {n_hh_total} total, {len(hh)} with valid number_of_cars")

hh = hh.merge(homes_df, on="household_id", how="inner")
print(f"households joined to home coords: {len(hh)}")

print("reading persons csv ...")
pers = pd.read_csv(PERSONS_CSV, sep=";", usecols=["household_id", "has_pt_subscription"])
n_p_total = len(pers)
# careful boolean parsing: values may arrive as bool or as "True"/"False" strings
raw = pers["has_pt_subscription"]
if raw.dtype == bool:
    pers["pt_sub"] = raw.astype(float)
else:
    s = raw.astype(str).str.strip().str.lower()
    valid = s.isin(["true", "false"])
    pers = pers[valid].copy()
    pers["pt_sub"] = (s[valid] == "true").astype(float)
print(f"persons: {n_p_total} total, {len(pers)} with valid has_pt_subscription, "
      f"overall subscription share {pers['pt_sub'].mean() * 100:.2f}%")

pers = pers.merge(homes_df, on="household_id", how="inner")
print(f"persons joined to home coords: {len(pers)}")

print("reading kreis boundaries ...")
kreise = gpd.read_file(KREIS_GEOJSON).to_crs("EPSG:25832")

# ---------------------------------------------------------------- 1 km grid
kxmin, kymin, kxmax, kymax = kreise.total_bounds
pad = 2000.0
xmin = np.floor((kxmin - pad) / BIN_METERS) * BIN_METERS
ymin = np.floor((kymin - pad) / BIN_METERS) * BIN_METERS
xmax = np.ceil((kxmax + pad) / BIN_METERS) * BIN_METERS
ymax = np.ceil((kymax + pad) / BIN_METERS) * BIN_METERS
xedges = np.arange(xmin, xmax + BIN_METERS, BIN_METERS)
yedges = np.arange(ymin, ymax + BIN_METERS, BIN_METERS)


def rate_grid(x, y, values):
    """Weighted mean of `values` per 1 km bin; bins with < MIN_WEIGHT observations -> NaN."""
    count, _, _ = np.histogram2d(x, y, bins=[xedges, yedges])
    vsum, _, _ = np.histogram2d(x, y, bins=[xedges, yedges], weights=values)
    with np.errstate(invalid="ignore", divide="ignore"):
        rate = vsum / count
    rate[count < MIN_WEIGHT] = np.nan
    return rate.T, count.T  # transpose: histogram2d returns (x, y)


cars_rate, cars_count = rate_grid(hh["x"].to_numpy(), hh["y"].to_numpy(),
                                  hh["number_of_cars"].to_numpy())
pt_rate, pt_count = rate_grid(pers["x"].to_numpy(), pers["y"].to_numpy(),
                              pers["pt_sub"].to_numpy())
pt_rate = pt_rate * 100.0  # -> percent

v = cars_rate[np.isfinite(cars_rate)]
print(f"cars grid: {v.size} valid bins, p2={np.percentile(v, 2):.2f} p98={np.percentile(v, 98):.2f}")
w = pt_rate[np.isfinite(pt_rate)]
print(f"pt grid:   {w.size} valid bins, p2={np.percentile(w, 2):.1f} p98={np.percentile(w, 98):.1f}")

# ---------------------------------------------------------------- layout (manual, in inches)
# The ZGB region is strongly portrait (w/h ~ 0.56), so two side-by-side maps alone
# cannot fill a 16:9 slide. Layout: text column (title / subtitle / summary stats /
# credit) on the left, two tall map panels with adjacent thin colorbars to the right.
extent = [xedges[0], xedges[-1], yedges[0], yedges[-1]]
map_ratio = (xedges[-1] - xedges[0]) / (yedges[-1] - yedges[0])  # width / height

FIG_H = 8.5
MAP_H = 6.9                       # map height in inches
MAP_BOTTOM = 0.50
MAP_W = MAP_H * map_ratio
CB_GAP, CB_W, CB_LABEL_W = 0.14, 0.15, 0.75
COL_W = MAP_W + CB_GAP + CB_W + CB_LABEL_W
TEXT_X = 0.55                     # left text column anchor
MAP1_X = 4.35                     # first map column start
MID_GAP = 0.55
MAP2_X = MAP1_X + COL_W + MID_GAP
FIG_W = MAP2_X + COL_W + 0.25

fig = plt.figure(figsize=(FIG_W, FIG_H), facecolor=BG)

# real summary statistics derived from the plotted data (region-wide means)
mean_cars_region = hh["number_of_cars"].mean()
pt_share_region = pers["pt_sub"].mean() * 100.0


def de(fmtd):
    return fmtd.replace(".", ",")


panels = [
    dict(x0_in=MAP1_X, grid=cars_rate,
         title="Pkw je Haushalt",
         sub="Mittlere Pkw-Anzahl pro Haushalt",
         norm=Normalize(vmin=np.percentile(v, 2), vmax=np.percentile(v, 98)),
         cb_label="Pkw je Haushalt"),
    dict(x0_in=MAP2_X, grid=pt_rate,
         title="ÖPNV-Abo-Quote",
         sub="Anteil Personen mit ÖPNV-Abo (%)",
         norm=Normalize(vmin=np.percentile(w, 2), vmax=np.percentile(w, 98)),
         cb_label="Abo-Quote [%]"),
]

y0 = MAP_BOTTOM / FIG_H
map_h_frac = MAP_H / FIG_H
map_top = y0 + map_h_frac

for p in panels:
    x0 = p["x0_in"] / FIG_W
    ax = fig.add_axes([x0, y0, MAP_W / FIG_W, map_h_frac])
    ax.set_facecolor(BG)
    ax.set_axis_off()
    cmap = matplotlib.colormaps["viridis"].copy()
    cmap.set_bad(BG)
    im = ax.imshow(p["grid"], origin="lower", extent=extent, cmap=cmap,
                   norm=p["norm"], interpolation="nearest", aspect="auto", zorder=2)
    kreise.boundary.plot(ax=ax, edgecolor=KREIS_EDGE, linewidth=0.7, zorder=3)
    ax.set_xlim(xedges[0], xedges[-1])
    ax.set_ylim(yedges[0], yedges[-1])
    # panel title + subtitle, anchored to the map's left edge
    fig.text(x0, (MAP_BOTTOM + MAP_H + 0.48) / FIG_H, p["title"], fontsize=15,
             color=TITLE_COLOR, fontweight="bold", va="bottom")
    fig.text(x0, (MAP_BOTTOM + MAP_H + 0.16) / FIG_H, p["sub"], fontsize=9.5,
             color=SUB_COLOR, va="bottom")
    # thin colorbar directly right of the map
    cax = fig.add_axes([(p["x0_in"] + MAP_W + CB_GAP) / FIG_W, y0 + 0.03,
                        CB_W / FIG_W, map_h_frac - 0.06])
    cb = fig.colorbar(im, cax=cax)
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=8, colors=SUB_COLOR, length=2.5)
    cb.set_label(p["cb_label"], fontsize=8.5, color=SUB_COLOR)

# ------------------------------- left text column
tx = TEXT_X / FIG_W


def ty(y_in):
    return y_in / FIG_H


fig.text(tx, ty(8.12), "Mobilitätsmerkmale\nim Raum", fontsize=18,
         color=TITLE_COLOR, fontweight="bold", va="top", linespacing=1.35)
fig.text(tx, ty(6.95),
         "Synthetische Bevölkerung\nRegion Braunschweig (ZGB)\n\n"
         "1-km-Raster am Wohnort;\nZellen mit < 25 gewichteten\n"
         "Beobachtungen ausgeblendet",
         fontsize=9.5, color=SUB_COLOR, va="top", linespacing=1.5)
# summary stats (computed from the same data as the maps)
fig.text(tx, ty(4.55), "Ø " + de(f"{mean_cars_region:.2f}"), fontsize=27,
         color="#22d3ee", fontweight="bold", va="top")
fig.text(tx, ty(4.02), "Pkw je Haushalt\n(Mittel über alle Haushalte)",
         fontsize=9, color=SUB_COLOR, va="top", linespacing=1.4)
fig.text(tx, ty(3.05), de(f"{pt_share_region:.1f}") + " %", fontsize=27,
         color="#fb7185", fontweight="bold", va="top")
fig.text(tx, ty(2.52), "Personen mit ÖPNV-Abo\n(Anteil an allen Personen)",
         fontsize=9, color=SUB_COLOR, va="top", linespacing=1.4)
fig.text(tx, ty(0.50),
         "eqasim-bs | 100-%-Lauf all-features\n(PopulationSim), Juni 2026\n"
         "households.csv / persons.csv /\nhomes.gpkg, EPSG:25832",
         fontsize=7.5, color=CREDIT_COLOR, va="bottom", linespacing=1.45)

fig.savefig(OUT_PNG, dpi=170, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("saved", OUT_PNG)
