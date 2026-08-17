"""Deck figure: spatial structure of modelled household income (eqasim-bs 100% run).

Three panels: 1-km region grid, 400-m city zoom (Braunschweig), per-Kreis bars.
Dark poster style (Space Mono, bg #0a0e14). All values are REALIZED model output.
"""
import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patheffects as pe
from matplotlib.patches import Rectangle, ConnectionPatch
from matplotlib.colors import Normalize, LinearSegmentedColormap
from matplotlib import cm

FD = "C:/Users/BIENZE~1/AppData/Local/Temp/claude/c--Users-bienzeisler-Documents-GitHub-eqasim-bs/b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/"
FONTS = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts/"

for f in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
    fm.fontManager.addfont(FONTS + f)
plt.rcParams.update({
    "font.family": "Space Mono",
    "figure.facecolor": "#0a0e14",
    "axes.facecolor": "#0a0e14",
    "text.color": "#c8d3e0",
    "axes.edgecolor": "#26313f",
})

BG = "#0a0e14"
INK = "#c8d3e0"
INK_DIM = "#7c8aa0"
INK_FAINT = "#55637a"
KREIS_LINE = "#33465f"
ACCENT = "#e8b45a"

d = np.load(FD + "income_prep.npz")
mean_r, cnt_r = d["mean_r"], d["cnt_r"]
mean_c, cnt_c = d["mean_c"], d["cnt_c"]
rx0, rx1, ry0, ry1 = d["region_extent"]
cx0, cx1, cy0, cy1 = d["city_extent"]

mr = np.ma.masked_where(cnt_r < 25, mean_r)
mc = np.ma.masked_where(cnt_c < 15, mean_c)

kr = gpd.read_file(FD + "kreis_25832.gpkg")
per_kreis = pd.read_csv(FD + "per_kreis_realized.csv", dtype={"ars5": str}).set_index("ars5")

KREIS_NAMES = {
    "03101": "Braunschweig", "03102": "Salzgitter", "03103": "Wolfsburg",
    "03151": "Gifhorn", "03153": "Goslar", "03154": "Helmstedt",
    "03157": "Peine", "03158": "Wolfenbüttel",
}

# Shared color scale: 2nd-98th percentile of the valid REGION cells.
# Truncate magma's near-black lower end so low-income cells stay visible on the dark bg.
vals = mean_r[cnt_r >= 25]
vmin, vmax = np.percentile(vals, [2, 98])
norm = Normalize(vmin=vmin, vmax=vmax)
cmap = LinearSegmentedColormap.from_list(
    "magma_deck", plt.get_cmap("magma")(np.linspace(0.16, 1.0, 256)))

fig = plt.figure(figsize=(16, 9), dpi=170)
gs = fig.add_gridspec(1, 3, width_ratios=[1.45, 1.02, 0.85],
                      left=0.035, right=0.975, top=0.815, bottom=0.135, wspace=0.13)
ax_r = fig.add_subplot(gs[0, 0])
ax_c = fig.add_subplot(gs[0, 1])
ax_b = fig.add_subplot(gs[0, 2])

glow = [pe.withStroke(linewidth=3.2, foreground="#1c3350", alpha=0.9)]

# ---------------------------------------------------------------- region map
ax_r.imshow(mr, extent=[rx0, rx1, ry0, ry1], origin="lower", cmap=cmap,
            norm=norm, interpolation="nearest", zorder=2)
kr.boundary.plot(ax=ax_r, color=KREIS_LINE, linewidth=0.9, zorder=3, path_effects=glow)
ax_r.set_xlim(rx0, rx1)
ax_r.set_ylim(ry0, ry1)
ax_r.set_aspect("equal")
ax_r.axis("off")

LABEL_OFFSETS = {  # meters, to keep labels off dense clusters
    "03101": (-14000, 2500), "03102": (0, -9500), "03103": (0, 8000),
    "03151": (0, 12000), "03153": (0, -8500), "03154": (2500, -8000),
    "03157": (-1000, 8000), "03158": (7000, -6500),
}
for _, row in kr.iterrows():
    ars = row["ars5"]
    p = row.geometry.representative_point()
    dx, dy = LABEL_OFFSETS.get(ars, (0, 0))
    ax_r.annotate(KREIS_NAMES.get(ars, ars), (p.x + dx, p.y + dy),
                  ha="center", va="center", fontsize=8.2, color=INK_DIM, zorder=5,
                  path_effects=[pe.withStroke(linewidth=2.4, foreground=BG)])

ax_r.set_title("Region ZGB · 1-km-Raster", fontsize=11.5, color=INK, pad=8, loc="left")

# zoom indicator rectangle (city extent)
rect = Rectangle((cx0, cy0), cx1 - cx0, cy1 - cy0, fill=False,
                 edgecolor=ACCENT, linewidth=1.1, linestyle=(0, (4, 3)), zorder=6)
ax_r.add_patch(rect)

# scale bar 10 km
sb_x, sb_y = rx0 + 4000, ry0 + 4500
ax_r.plot([sb_x, sb_x + 10000], [sb_y, sb_y], color=INK_FAINT, lw=1.4, zorder=6)
ax_r.text(sb_x + 5000, sb_y + 1400, "10 km", ha="center", fontsize=7.5, color=INK_FAINT)

# ------------------------------------------------------------------ city map
bs = kr[kr["ars5"] == "03101"]
ax_c.imshow(mc, extent=[cx0, cx1, cy0, cy1], origin="lower", cmap=cmap,
            norm=norm, interpolation="nearest", zorder=2)
bs.boundary.plot(ax=ax_c, color=KREIS_LINE, linewidth=1.1, zorder=3, path_effects=glow)
ax_c.set_xlim(cx0, cx1)
ax_c.set_ylim(cy0, cy1)
ax_c.set_aspect("equal")
ax_c.axis("off")
ax_c.set_title("Zoom Stadt Braunschweig · 400-m-Raster", fontsize=11.5, color=INK,
               pad=8, loc="left")
frame = Rectangle((cx0, cy0), cx1 - cx0, cy1 - cy0, fill=False,
                  edgecolor=ACCENT, linewidth=1.0, linestyle=(0, (4, 3)), zorder=6)
ax_c.add_patch(frame)

# connect indicator rectangle -> zoom panel (subtle)
for corner_r, corner_c in (((cx1, cy1), (cx0, cy1)), ((cx1, cy0), (cx0, cy0))):
    con = ConnectionPatch(xyA=corner_r, coordsA=ax_r.transData,
                          xyB=corner_c, coordsB=ax_c.transData,
                          color=ACCENT, linewidth=0.6, alpha=0.35, zorder=1)
    fig.add_artist(con)

# scale bar 5 km, below the zoom frame on plain background
ax_c.set_ylim(cy0 - 2800, cy1)
sbx, sby = cx1 - 5000, cy0 - 1700
ax_c.plot([sbx, sbx + 5000], [sby, sby], color=INK_FAINT, lw=1.4, zorder=6)
ax_c.text(sbx - 900, sby, "5 km", ha="right", va="center", fontsize=7.5, color=INK_FAINT)

# shared horizontal colorbar under the two maps
cax = fig.add_axes([0.075, 0.068, 0.30, 0.016])
cb = fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax,
                  orientation="horizontal")
cb.ax.tick_params(labelsize=8, colors=INK_DIM, length=3)
cb.outline.set_edgecolor("#26313f")
fig.text(0.385, 0.082, "mittleres Haushaltseinkommen [€/Monat]",
         fontsize=9, color=INK, ha="left", va="center")
fig.text(0.385, 0.058,
         f"Skala: 2.–98. Perzentil der 1-km-Zellen ({vmin:,.0f}–{vmax:,.0f} €)".replace(",", "."),
         fontsize=7.2, color=INK_FAINT, ha="left", va="center")

# --------------------------------------------------------------------- bars
pk = per_kreis.copy()
pk["name"] = [KREIS_NAMES[a] for a in pk.index]
pk = pk.sort_values("mean")
ypos = np.arange(len(pk))
colors = [cmap(norm(v)) for v in pk["mean"]]
ax_b.barh(ypos, pk["mean"], height=0.52, color=colors, zorder=3)
for spine in ax_b.spines.values():
    spine.set_visible(False)
ax_b.set_yticks([])
ax_b.set_xticks([])
xmax = pk["mean"].max()
ax_b.set_xlim(0, xmax * 1.28)
ax_b.set_ylim(-0.75, len(pk) - 0.25 + 0.55)
for yi, v, name in zip(ypos, pk["mean"], pk["name"]):
    ax_b.text(0, yi + 0.48, name, va="bottom", ha="left", fontsize=8.6, color=INK_DIM)
    ax_b.text(v + xmax * 0.025, yi, f"{v:,.0f} €".replace(",", "."),
              va="center", fontsize=8.6, color=INK)
ax_b.set_title("Kreisniveaus · realisierte Mittelwerte", fontsize=11.5, color=INK,
               pad=8, loc="left")

lo, hi = pk["mean"].min(), pk["mean"].max()
span = hi - lo
ax_b.text(0.0, -0.085,
          f"Spannweite {span:,.0f} € (+{span/lo*100:.0f} % ggü. Minimum)".replace(",", "."),
          transform=ax_b.transAxes, fontsize=8.6, color=INK_DIM, ha="left")

# --------------------------------------------------------------- title block
fig.text(0.035, 0.955, "Einkommen im Raum", fontsize=19, color="#f2f6fb",
         fontweight="bold", ha="left", va="top")
fig.text(0.035, 0.903,
         "modelliertes monatliches Haushaltseinkommen · Kreisniveau an INKAR geführt, "
         "innerorts über den Nettokaltmieten-Index verteilt (kreis-mittelwerttreu)",
         fontsize=9.5, color=INK_DIM, ha="left", va="top")
fig.text(0.035, 0.018,
         "eqasim-bs · 100%-Lauf popsim_mid (all features) · 558.281 Haushalte · dargestellt: realisierte Modellwerte, keine Zielwerte · "
         "Region: Zellen mit ≥25 HH · Zoom: ≥15 HH · EPSG:25832",
         fontsize=8, color=INK_FAINT, ha="left")

fig.savefig(FD + "fig_income_space.png", facecolor=BG, dpi=170)
print("written", FD + "fig_income_space.png")
