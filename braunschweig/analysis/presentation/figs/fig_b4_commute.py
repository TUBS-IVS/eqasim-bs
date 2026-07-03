# -*- coding: utf-8 -*-
"""Figure B4: commute desire lines + workplace density (two-panel dark map).

Data: braunschweig_100pct_allfeat_popsim_commutes.gpkg (100% all-features
PopulationSim run, exported 2026-06-30). LineStrings home -> work, EPSG:25832
(CRS tag missing in file, set explicitly as documented for this export).
Left: glowing desire-line web (35k sample, destinations in/near the region).
Right: 2D histogram (200 m bins) of commute destination endpoints (workplaces),
inferno + LogNorm. Same extent both panels.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import shapely
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap, LogNorm, Normalize

BG = "#0a0e14"
TITLE_COL = "#eef3fb"
SUB_COL = "#8b95a7"
CREDIT_COL = "#5a6577"
KREIS_EDGE = "#33465f"
CRS_METRIC = "EPSG:25832"
RNG = np.random.default_rng(42)

COMMUTES = Path("C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim/braunschweig_100pct_allfeat_popsim_commutes.gpkg")
KREIS = Path("C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim/simwrapper/kreis_socio.geojson")
OUT = Path(__file__).resolve().parent / "fig_b4_commute.png"

# ---- fonts -----------------------------------------------------------------
FONT_DIR = Path("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts")
FONT = "DejaVu Sans Mono"
for ttf in sorted(FONT_DIR.glob("SpaceMono-*.ttf")):
    fm.fontManager.addfont(str(ttf))
    FONT = fm.FontProperties(fname=str(ttf)).get_name()
plt.rcParams["font.family"] = FONT

# ---- data ------------------------------------------------------------------
print("loading commutes ...", flush=True)
gdf = gpd.read_file(COMMUTES)
if gdf.crs is None:
    gdf = gdf.set_crs(CRS_METRIC)
geom = gdf.geometry.values
x0 = shapely.get_x(shapely.get_point(geom, 0))
y0 = shapely.get_y(shapely.get_point(geom, 0))
x1 = shapely.get_x(shapely.get_point(geom, -1))
y1 = shapely.get_y(shapely.get_point(geom, -1))
n_total = len(x0)
print(f"  commutes: {n_total:,}", flush=True)

kreis = gpd.read_file(KREIS).to_crs(CRS_METRIC)
kxmin, kymin, kxmax, kymax = kreis.total_bounds
pad = 0.025 * max(kxmax - kxmin, kymax - kymin)
xmin, xmax = kxmin - pad, kxmax + pad
ymin, ymax = kymin - pad, kymax + pad

dist_km = np.hypot(x1 - x0, y1 - y0) / 1000.0
inside_dest = (x1 >= xmin) & (x1 <= xmax) & (y1 >= ymin) & (y1 <= ymax)
print(f"  destinations inside region extent: {inside_dest.sum():,} "
      f"({100.0 * inside_dest.mean():.1f}%)", flush=True)

# ---- left panel data: 35k line sample --------------------------------------
# Keep commutes whose destination lies within the extent + 30 km buffer, so
# near-region out-commuting (e.g. Hannover) still shows as edge rays while
# rare long-haul streaks (Berlin, Hamburg ...) do not dominate the web.
BUF_M = 30000.0
near = ((x1 >= xmin - BUF_M) & (x1 <= xmax + BUF_M) &
        (y1 >= ymin - BUF_M) & (y1 <= ymax + BUF_M))
print(f"  destinations in/near region (+30 km): {near.sum():,} "
      f"({100.0 * near.mean():.1f}%)", flush=True)
near_idx = np.flatnonzero(near)
N_LINES = 35000
idx = RNG.choice(near_idx, min(N_LINES, len(near_idx)), replace=False)
segs = np.stack(
    [np.column_stack([x0[idx], y0[idx]]), np.column_stack([x1[idx], y1[idx]])],
    axis=1,
)
seg_km = dist_km[idx]
cmap_lines = LinearSegmentedColormap.from_list(
    "commute", ["#22d3ee", "#6366f1", "#d946ef", "#fb7185"]
)
vmax_km = float(np.percentile(seg_km, 95))
norm_lines = Normalize(vmin=0.0, vmax=vmax_km)
line_colors = cmap_lines(norm_lines(np.clip(seg_km, 0, vmax_km)))
print(f"  line sample: {len(segs):,}, colour vmax = {vmax_km:.1f} km", flush=True)

# ---- right panel data: destination 2D histogram, 200 m bins ----------------
BIN_M = 200.0
bx = np.arange(xmin, xmax + BIN_M, BIN_M)
by = np.arange(ymin, ymax + BIN_M, BIN_M)
H, _, _ = np.histogram2d(x1[inside_dest], y1[inside_dest], bins=[bx, by])
Hm = np.ma.masked_less(H.T, 1.0)
cmap_dens = plt.get_cmap("inferno").copy()
cmap_dens.set_bad(BG)
cmap_dens.set_under(BG)
print(f"  max workplaces per 200m cell: {H.max():.0f}", flush=True)

# ---- figure ----------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(13.0, 8.6), dpi=170)
fig.patch.set_facecolor(BG)
fig.subplots_adjust(left=0.02, right=0.95, top=0.775, bottom=0.095, wspace=0.04)

for ax in axes:
    ax.set_facecolor(BG)
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.set_axis_off()

# LEFT: desire lines ----------------------------------------------------------
axL = axes[0]
kreis.plot(ax=axL, facecolor="none", edgecolor=KREIS_EDGE, linewidth=0.7, zorder=1)
axL.add_collection(LineCollection(segs, colors=line_colors, linewidths=2.0,
                                  alpha=0.012, zorder=2, capstyle="round"))
axL.add_collection(LineCollection(segs, colors=line_colors, linewidths=0.5,
                                  alpha=0.15, zorder=3, capstyle="round"))
axL.set_title("Pendlerbeziehungen Wohnen – Arbeiten", fontsize=12.5,
              color=TITLE_COL, pad=9, loc="center")
axL.text(0.0, -0.045,
         "Stichprobe: 35.000 Beziehungen (Ziele bis 30 km um die Region) | Farbe: Distanz",
         transform=axL.transAxes, fontsize=7.5, color=SUB_COL, ha="left", va="top")

# small inline gradient legend for line length (bottom-left inside the map box)
axLeg = axL.inset_axes([0.06, 0.035, 0.24, 0.020])
axLeg.imshow(np.linspace(0, 1, 256).reshape(1, -1), aspect="auto", cmap=cmap_lines)
axLeg.set_axis_off()
axL.text(0.05, 0.045, "0", transform=axL.transAxes, fontsize=7.5,
         color=SUB_COL, ha="right", va="center")
axL.text(0.31, 0.045, f"≥{vmax_km:.0f} km", transform=axL.transAxes,
         fontsize=7.5, color=SUB_COL, ha="left", va="center")

# RIGHT: workplace density ------------------------------------------------------
axR = axes[1]
im = axR.imshow(Hm, extent=(bx[0], bx[-1], by[0], by[-1]), origin="lower",
                cmap=cmap_dens, norm=LogNorm(vmin=1, vmax=Hm.max()),
                interpolation="nearest", zorder=2)
kreis.plot(ax=axR, facecolor="none", edgecolor=KREIS_EDGE, linewidth=0.7, zorder=3)
axR.set_title("Arbeitsplatz-Schwerpunkte (Zielorte)", fontsize=12.5,
              color=TITLE_COL, pad=9, loc="center")
axR.text(0.0, -0.045,
         "262.210 Pendelziele im Kartenausschnitt (89 %), aufgelöst auf Gebäudeebene",
         transform=axR.transAxes, fontsize=7.5, color=SUB_COL, ha="left", va="top")

cax = axR.inset_axes([1.05, 0.14, 0.035, 0.72])
cb = fig.colorbar(im, cax=cax)
cb.outline.set_edgecolor("#1d2633")
cb.ax.tick_params(colors=SUB_COL, labelsize=8, length=2)
cb.set_label("Pendelziele je 200-m-Zelle (log. Skala)", color=SUB_COL, fontsize=8.5)

# ---- headings / credit --------------------------------------------------------
fig.text(0.02, 0.965, "Pendlerbeziehungen und Arbeitsplatz-Schwerpunkte",
         fontsize=16, color=TITLE_COL, ha="left", va="top", fontweight="bold")
fig.text(0.02, 0.90,
         "Synthetische Erwerbstätige der Region Braunschweig (ZGB) – "
         "295.037 Heim-Arbeits-Beziehungen aus dem 100-%-Modell",
         fontsize=10, color=SUB_COL, ha="left", va="top")
fig.text(0.02, 0.006,
         "eqasim-bs | 100-%-Lauf all-features (PopulationSim), Juni 2026 | "
         "braunschweig_100pct_allfeat_popsim_commutes.gpkg (EPSG:25832)",
         fontsize=7.5, color=CREDIT_COL, ha="left", va="bottom")

fig.savefig(OUT, dpi=170, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print(f"saved {OUT}", flush=True)
