# Figure B6: "Gueterverkehr auf dem Netz" - truck legs from raw MATSim output (100% run)
# Data: output_legs.csv.gz (mode == "truck"), pre-extracted to truck_legs.csv (44,542 legs).
# Desire lines start->end, colored by euclidean length, glow style on dark background.
import os
import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.cm import ScalarMappable

BG = "#0a0e14"
FIGDIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- fonts
FONT_DIR = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts"
family = "DejaVu Sans Mono"
try:
    for f in ["SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"]:
        fp = os.path.join(FONT_DIR, f)
        if os.path.exists(fp):
            fm.fontManager.addfont(fp)
    family = "Space Mono"
except Exception:
    pass
plt.rcParams["font.family"] = family

# ---------------------------------------------------------------- data
legs = pd.read_csv(os.path.join(FIGDIR, "truck_legs.csv"), sep=";")
n_legs = len(legs)
seg_counts = legs["person"].str.replace(r"_[0-9]+$", "", regex=True).value_counts()

x0 = legs["start_x"].to_numpy()
y0 = legs["start_y"].to_numpy()
x1 = legs["end_x"].to_numpy()
y1 = legs["end_y"].to_numpy()
length_km = np.hypot(x1 - x0, y1 - y0) / 1000.0

# Kreis boundaries (WGS84 geojson -> EPSG:25832)
kreise = gpd.read_file(
    "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim/simwrapper/kreis_socio.geojson"
).to_crs("EPSG:25832")
region = kreise.union_all()  # outer ZGB ring

# Homes (dim context cloud), subsample
homes = gpd.read_file(
    "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim/braunschweig_100pct_allfeat_popsim_homes.gpkg",
    columns=[],
)
rng = np.random.default_rng(42)
idx = rng.choice(len(homes), size=min(220_000, len(homes)), replace=False)
hx = homes.geometry.x.to_numpy()[idx]
hy = homes.geometry.y.to_numpy()[idx]

# ---------------------------------------------------------------- extent (16:9-ish view around region)
rb = kreise.total_bounds  # minx, miny, maxx, maxy
cx, cy = (rb[0] + rb[2]) / 2, (rb[1] + rb[3]) / 2
w, h = rb[2] - rb[0], rb[3] - rb[1]
w *= 1.10  # tight margin: region dominates, long-haul corridors run off-canvas
h *= 1.04
target_aspect = 16.4 / 9.0
if w / h < target_aspect:
    w = h * target_aspect
else:
    h = w / target_aspect
cx += 0.045 * w  # shift view east -> map content sits slightly left, balancing stats panel on the right
xmin, xmax = cx - w / 2, cx + w / 2
ymin, ymax = cy - h / 2, cy + h / 2

# ---------------------------------------------------------------- figure
fig = plt.figure(figsize=(16, 9), dpi=170)
fig.patch.set_facecolor(BG)
ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
ax.set_facecolor(BG)
ax.set_axis_off()
ax.set_xlim(xmin, xmax)
ax.set_ylim(ymin, ymax)
ax.set_aspect("equal")

# dim homes cloud
ax.scatter(hx, hy, s=0.3, c="#24354d", alpha=0.45, linewidths=0, rasterized=True, zorder=1)

# Kreis boundaries + region ring (drawn ABOVE the line cloud so the region stays readable)
kreise.boundary.plot(ax=ax, edgecolor="#33465f", linewidth=0.7, alpha=0.8, zorder=6)
gpd.GeoSeries([region.boundary]).plot(ax=ax, color="#6b8cb8", linewidth=2.2, alpha=0.25, zorder=6)  # soft glow
gpd.GeoSeries([region.boundary]).plot(ax=ax, color="#6b8cb8", linewidth=0.9, alpha=0.95, zorder=6)

# ---------------------------------------------------------------- truck desire lines
cmap = LinearSegmentedColormap.from_list("freight", ["#fbbf24", "#f97316", "#ef4444"])
norm = Normalize(vmin=0.0, vmax=np.percentile(length_km, 98))
order = np.argsort(length_km)  # draw long-haul on top
segs = np.stack([np.column_stack([x0, y0]), np.column_stack([x1, y1])], axis=1)[order]
cols = cmap(norm(length_km[order]))

lc_glow = LineCollection(segs, colors=cols, linewidths=2.2, alpha=0.010, zorder=4)
lc_glow.set_capstyle("round")
ax.add_collection(lc_glow)
lc_sharp = LineCollection(segs, colors=cols, linewidths=0.45, alpha=0.055, zorder=5)
lc_sharp.set_capstyle("round")
ax.add_collection(lc_sharp)

# ---------------------------------------------------------------- colorbar (thin, right)
cax = fig.add_axes([0.945, 0.16, 0.008, 0.34])
cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), cax=cax)
cb.outline.set_visible(False)
cb.ax.tick_params(color="#5a6577", labelcolor="#8b95a7", labelsize=8, length=2, width=0.6)
cb.set_label("Distanz Start–Ziel (Luftlinie, km)", color="#8b95a7", fontsize=8.5, labelpad=6)

# ---------------------------------------------------------------- text
def de(n):
    return f"{n:,}".replace(",", ".")

fig.text(0.028, 0.945, "Lkw-Fernverkehr im Simulationstag", fontsize=17,
         color="#eef3fb", fontweight="bold", ha="left", va="top", zorder=10,
         bbox=dict(boxstyle="round,pad=0.35", facecolor=BG, edgecolor="none", alpha=0.75))
fig.text(0.028, 0.893,
         f"Güterverkehr auf dem Netz — {de(n_legs)} Lkw-Fahrten (Modus „truck“) im rohen MATSim-Output,\n"
         "dargestellt als Luftlinien Start → Ziel · helle Bündel = Fernverkehr durch die Region",
         fontsize=10, color="#8b95a7", ha="left", va="top", linespacing=1.5, zorder=10,
         bbox=dict(boxstyle="round,pad=0.35", facecolor=BG, edgecolor="none", alpha=0.75))

# boundary legend note (below the stats box, right side)
fig.text(0.972, 0.72, "— Kreisgrenzen · äußerer Ring = ZGB",
         fontsize=8, color="#6b8cb8", ha="right", va="top", zorder=10,
         bbox=dict(boxstyle="round,pad=0.35", facecolor=BG, edgecolor="none", alpha=0.75))

# segment stats block (top right)
stats = [
    ("Durchgangsverkehr", seg_counts.get("freight_transit", 0)),
    ("Quellverkehr (ab ZGB)", seg_counts.get("freight_outgoing", 0)),
    ("Zielverkehr (in ZGB)", seg_counts.get("freight_incoming", 0)),
    ("Binnenverkehr", seg_counts.get("freight_internal", 0)),
]
lines = [f"{name:<22}{de(v):>7}" for name, v in stats]
fig.text(0.972, 0.945, "Lkw-Fahrten nach Segment\n" + "\n".join(lines),
         fontsize=9, color="#aeb8c9", ha="right", va="top", linespacing=1.65,
         bbox=dict(boxstyle="round,pad=0.55", facecolor="#0e141d", edgecolor="#1d2633", linewidth=0.8))

fig.text(0.028, 0.032,
         "eqasim-bs | 100-%-Lauf all-features (PopulationSim), Juni 2026 | Quelle: simulation_output/output_legs.csv.gz "
         "(roher MATSim-Output, Modus „truck“) · Lkw-Injektion: german-wide-freight v3",
         fontsize=7.5, color="#5a6577", ha="left", va="bottom", zorder=10,
         bbox=dict(boxstyle="round,pad=0.3", facecolor=BG, edgecolor="none", alpha=0.75))

out = os.path.join(FIGDIR, "fig_b6_freight.png")
fig.savefig(out, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("saved", out)
print("legs:", n_legs, "| length km p50/p90/max:",
      np.percentile(length_km, 50).round(1), np.percentile(length_km, 90).round(1), length_km.max().round(1))
print(seg_counts.to_dict())
