# -*- coding: utf-8 -*-
"""
Two-panel management-slide figure "Einkommen und Fahrzeuge im Raum".

LEFT : household income density per economic-status class (5 smoothed curves).
RIGHT: map of mean engine power (kW) of the attributed car fleet per 1-km cell.

Data (100% all-features PopulationSim run, exported 2026-06-30):
  - braunschweig_100pct_allfeat_popsim_persons.csv    (household_id, economic_status)
  - braunschweig_100pct_allfeat_popsim_households.csv (household_id, household_income_eur)
  - braunschweig_100pct_allfeat_popsim_vehicles.csv   (household_id, mode, brand, engine_power_kw)
  - braunschweig_100pct_allfeat_popsim_homes.gpkg     (household_id, Point EPSG:25832)
  - simwrapper/kreis_socio.geojson                    (Kreis boundaries, EPSG:4326 -> 25832)
All real data; no synthetic values.
"""

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import matplotlib.patheffects as pe
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
import os

# ----------------------------------------------------------------------------- paths
DATA = "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim/"
OUT_PNG = ("C:/Users/BIENZE~1/AppData/Local/Temp/claude/"
           "c--Users-bienzeisler-Documents-GitHub-eqasim-bs/"
           "b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/fig_inc_income_cars.png")
FONT_DIR = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts/"

# ----------------------------------------------------------------------------- style
BG = "#0a0e14"
FG_TITLE = "#eef3fb"
FG_SUB = "#8b95a7"
FG_CREDIT = "#5a6577"
GRID = "#1d2633"
KREIS_LINE = "#33465f"
NODATA = "#11161f"

for f in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
    p = os.path.join(FONT_DIR, f)
    if os.path.exists(p):
        font_manager.fontManager.addfont(p)
try:
    font_manager.findfont("Space Mono", fallback_to_default=False)
    plt.rcParams["font.family"] = "Space Mono"
except Exception:
    plt.rcParams["font.family"] = "DejaVu Sans Mono"
plt.rcParams["axes.edgecolor"] = GRID
plt.rcParams["text.color"] = FG_TITLE


def de_num(x, dec=0):
    """German number format: dot thousands separator, comma decimals."""
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


# Economic-status classes, cool -> hot
STATUS_ORDER = ["very_low", "low", "medium", "high", "very_high"]
STATUS_DE = {"very_low": "sehr niedrig", "low": "niedrig", "medium": "mittel",
             "high": "hoch", "very_high": "sehr hoch"}
STATUS_COLOR = {"very_low": "#22d3ee", "low": "#4f7ff0", "medium": "#d946ef",
                "high": "#fb7185", "very_high": "#fbbf24"}

# ----------------------------------------------------------------------------- data: income by status
persons = pd.read_csv(DATA + "braunschweig_100pct_allfeat_popsim_persons.csv", sep=";",
                      usecols=["household_id", "economic_status"])
# economic_status is a household attribute broadcast to persons -> one row per household
hh_status = persons.drop_duplicates("household_id")
households = pd.read_csv(DATA + "braunschweig_100pct_allfeat_popsim_households.csv", sep=";",
                         usecols=["household_id", "household_income_eur"])
inc = hh_status.merge(households, on="household_id", how="inner")
n_hh = len(inc)

X_MAX = 8000.0
share_above = (inc["household_income_eur"] > X_MAX).mean() * 100.0
share_above_vh = (inc.loc[inc["economic_status"] == "very_high",
                          "household_income_eur"] > X_MAX).mean() * 100.0

# Fine histogram + Gaussian smoothing (deterministic, dependency-free KDE substitute).
# Density is normalised over the FULL income range; the axis is capped at 8000 EUR,
# so curves are shown truncated (honest) rather than piling clipped mass at the edge.
edges = np.arange(0.0, 22000.0 + 100.0, 100.0)
centers = 0.5 * (edges[:-1] + edges[1:])
sigma_bins = 2.5  # 250 EUR smoothing bandwidth
kx = np.arange(-10, 11)
kernel = np.exp(-0.5 * (kx / sigma_bins) ** 2)
kernel /= kernel.sum()

dens = {}
medians = {}
for s in STATUS_ORDER:
    v = inc.loc[inc["economic_status"] == s, "household_income_eur"].to_numpy()
    h, _ = np.histogram(v, bins=edges, density=True)
    dens[s] = np.convolve(h, kernel, mode="same")
    medians[s] = float(np.median(v))

# ----------------------------------------------------------------------------- data: fleet map
veh = pd.read_csv(DATA + "braunschweig_100pct_allfeat_popsim_vehicles.csv", sep=";",
                  usecols=["household_id", "mode", "brand", "engine_power_kw"],
                  dtype={"household_id": "float64", "mode": "string",
                         "brand": "string", "engine_power_kw": "float64"})
cars = veh[(veh["mode"] == "car") & veh["brand"].notna() & veh["engine_power_kw"].notna()].copy()
cars["household_id"] = cars["household_id"].astype("int64")
n_cars = len(cars)
mean_kw_region = cars["engine_power_kw"].mean()
share_150 = (cars["engine_power_kw"] >= 150.0).mean() * 100.0

homes = gpd.read_file(DATA + "braunschweig_100pct_allfeat_popsim_homes.gpkg")  # EPSG:25832
homes_xy = pd.DataFrame({"household_id": homes["household_id"].astype("int64"),
                         "x": homes.geometry.x, "y": homes.geometry.y})
carpts = cars.merge(homes_xy, on="household_id", how="inner")
n_joined = len(carpts)

CELL = 1000.0  # 1-km bins, EPSG:25832 metric
x0 = np.floor(carpts["x"].min() / CELL) * CELL
y0 = np.floor(carpts["y"].min() / CELL) * CELL
x1 = np.ceil(carpts["x"].max() / CELL) * CELL
y1 = np.ceil(carpts["y"].max() / CELL) * CELL
xedges = np.arange(x0, x1 + CELL, CELL)
yedges = np.arange(y0, y1 + CELL, CELL)
cnt, _, _ = np.histogram2d(carpts["x"], carpts["y"], bins=[xedges, yedges])
kwsum, _, _ = np.histogram2d(carpts["x"], carpts["y"], bins=[xedges, yedges],
                             weights=carpts["engine_power_kw"])
with np.errstate(invalid="ignore", divide="ignore"):
    mean_kw = kwsum / cnt
MIN_VEH = 25
mean_kw_masked = np.ma.masked_where(cnt < MIN_VEH, mean_kw)
valid_vals = mean_kw[cnt >= MIN_VEH]
vmin, vmax = np.percentile(valid_vals, [2, 98])

kreise = gpd.read_file(DATA + "simwrapper/kreis_socio.geojson").to_crs("EPSG:25832")
# Official AGS/ARS district codes (established domain knowledge, not model output)
KREIS_NAME = {"03101": "Braunschweig", "03102": "Salzgitter", "03103": "Wolfsburg",
              "03151": "Gifhorn", "03153": "Goslar", "03154": "Helmstedt",
              "03157": "Peine", "03158": "Wolfenbüttel"}
kb = kreise.total_bounds

# ----------------------------------------------------------------------------- figure
fig = plt.figure(figsize=(18, 8.5), dpi=170)
fig.patch.set_facecolor(BG)

gs = fig.add_gridspec(1, 2, left=0.055, right=0.955, top=0.795, bottom=0.115,
                      wspace=0.07, width_ratios=[1.25, 1.0])
axL = fig.add_subplot(gs[0, 0])
axR = fig.add_subplot(gs[0, 1])

# ------------------------------------------------- title block
fig.text(0.055, 0.955, "Einkommen prägt die Flotte",
         fontsize=17, fontweight="bold", color=FG_TITLE, ha="left", va="top")
fig.text(0.055, 0.905,
         "Synthetische Bevölkerung Region Braunschweig (100 %) — links: Verteilung des Haushaltseinkommens "
         "nach ökonomischem Status des Haushalts,\nrechts: mittlere Motorleistung der attribuierten Pkw-Flotte "
         "je 1-km-Rasterzelle am Wohnort",
         fontsize=10, color=FG_SUB, ha="left", va="top", linespacing=1.45)

# ------------------------------------------------- LEFT: income densities
axL.set_facecolor(BG)
mask_x = centers <= X_MAX
xs = centers[mask_x]
ymax = 0.0
for s in STATUS_ORDER:
    d = dens[s][mask_x] * 1000.0  # density per 1000 EUR for readable axis
    c = STATUS_COLOR[s]
    axL.fill_between(xs, 0.0, d, color=c, alpha=0.22, lw=0, zorder=2)
    axL.plot(xs, d, color=c, lw=5.2, alpha=0.20, zorder=3,
             solid_capstyle="round")  # glow
    axL.plot(xs, d, color=c, lw=1.9, alpha=1.0, zorder=4,
             label=f"{STATUS_DE[s]} · Median {de_num(medians[s])} €",
             solid_capstyle="round")
    ymax = max(ymax, d.max())

# median ticks on the x axis, in class colour
for s in STATUS_ORDER:
    axL.plot([medians[s]], [0], marker="|", markersize=14, markeredgewidth=2.4,
             color=STATUS_COLOR[s], clip_on=False, zorder=6)

axL.set_xlim(0, X_MAX)
axL.set_ylim(0, ymax * 1.12)
axL.set_title("Haushaltseinkommen nach ökonomischem Status", loc="left",
              fontsize=12.5, color=FG_TITLE, pad=12)
axL.set_xlabel("Haushaltseinkommen [€/Monat]", fontsize=9.5, color=FG_SUB, labelpad=8)
axL.set_ylabel("Dichte (normiert je Klasse)", fontsize=9.5, color=FG_SUB, labelpad=8)
axL.set_xticks(np.arange(0, X_MAX + 1, 1000))
axL.set_xticklabels([de_num(t) for t in np.arange(0, X_MAX + 1, 1000)])
axL.tick_params(colors=FG_SUB, labelsize=8.5, length=3)
axL.set_yticks([])
for spine in ("top", "right", "left"):
    axL.spines[spine].set_visible(False)
axL.spines["bottom"].set_color(GRID)
axL.grid(axis="x", color=GRID, lw=0.7, alpha=0.8, zorder=1)

leg = axL.legend(loc="upper right", frameon=False, fontsize=9,
                 title="Ökonomischer Status", title_fontsize=9.5,
                 labelcolor="#c7d1e0", handlelength=1.5, borderaxespad=0.4)
leg.get_title().set_color(FG_SUB)

# ------------------------------------------------- RIGHT: mean kW map
axR.set_facecolor(BG)
axR.set_axis_off()

# no-data ground: Kreis polygons filled dark
kreise.plot(ax=axR, color=NODATA, edgecolor="none", zorder=1)

cmap = plt.get_cmap("inferno").copy()
cmap.set_bad(alpha=0.0)
im = axR.imshow(mean_kw_masked.T, origin="lower",
                extent=(x0, x1, y0, y1), cmap=cmap,
                vmin=vmin, vmax=vmax, interpolation="nearest", zorder=2)

kreise.boundary.plot(ax=axR, color=KREIS_LINE, lw=0.9, zorder=4)
halo = [pe.withStroke(linewidth=2.4, foreground=BG, alpha=0.85)]
for _, row in kreise.iterrows():
    pt = row.geometry.representative_point()
    axR.annotate(KREIS_NAME.get(row["ars5"], row["ars5"]),
                 (pt.x, pt.y), color=FG_SUB, fontsize=8, ha="center", va="center",
                 zorder=5, path_effects=halo)

axR.set_xlim(kb[0] - 2000, kb[2] + 2000)
axR.set_ylim(kb[1] - 2000, kb[3] + 2000)
axR.set_aspect("equal")
axR.set_anchor("W")
axR.set_title("Wo die stärksten Autos stehen", loc="left",
              fontsize=12.5, color=FG_TITLE, pad=12)

cax = axR.inset_axes([1.03, 0.12, 0.035, 0.76])
cb = fig.colorbar(ScalarMappable(norm=Normalize(vmin, vmax), cmap="inferno"), cax=cax)
cb.outline.set_edgecolor(GRID)
cb.ax.tick_params(colors=FG_SUB, labelsize=8)
cb.ax.yaxis.set_major_locator(matplotlib.ticker.MultipleLocator(5))
cb.ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.0f"))
cb.set_label("mittlere Motorleistung [kW]", color=FG_SUB, fontsize=8.5)

# side stats in the empty north-west corner of the map extent (data coordinates)
stats = (f"Regionsmittel: {mean_kw_region:.0f} kW\n"
         f"Anteil Pkw ≥ 150 kW: {de_num(share_150, 1)} %")
axR.text(kb[0] - 1000, kb[3] + 1000, stats,
         fontsize=9.2, color="#c7d1e0", ha="left", va="top",
         linespacing=1.7, zorder=7, path_effects=halo)
# map methodology note moved to the figure footnote row (avoids polygon overlap)
fig.text(0.585, 0.052,
         f"1-km-Raster · Zellen mit < {MIN_VEH} Pkw ausgeblendet · Farbskala 2.–98. Perzentil",
         fontsize=7.5, color=FG_CREDIT, ha="left", va="bottom")

# ------------------------------------------------- footnote + credit
fig.text(0.055, 0.052,
         f"Striche auf der Achse: Median je Klasse · Achse bei 8.000 € gekappt "
         f"({de_num(share_above, 1)} % aller Haushalte darüber, in Klasse "
         f"„sehr hoch“ {share_above_vh:.0f} %)",
         fontsize=7.5, color=FG_CREDIT, ha="left", va="bottom")
fig.text(0.055, 0.022,
         "Daten: braunschweig_100pct_allfeat_popsim_{persons,households,vehicles}.csv + homes.gpkg + "
         "kreis_socio.geojson (100%-Lauf, Export 2026-06-30, EPSG:25832) · "
         f"{de_num(n_hh)} Haushalte · {de_num(n_cars)} attribuierte Pkw",
         fontsize=7.5, color=FG_CREDIT, ha="left", va="bottom")

fig.savefig(OUT_PNG, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("saved", OUT_PNG)
print(f"n_hh={n_hh}  n_cars={n_cars}  joined={n_joined}")
print(f"mean_kw_region={mean_kw_region:.2f}  share_150={share_150:.2f}%")
print(f"share_above_8000_all={share_above:.2f}%  very_high={share_above_vh:.2f}%")
print("medians:", {k: round(v) for k, v in medians.items()})
print(f"vmin={vmin:.1f} vmax={vmax:.1f}  valid_cells={int((cnt>=MIN_VEH).sum())}")
