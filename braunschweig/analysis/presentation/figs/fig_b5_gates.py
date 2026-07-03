# -*- coding: utf-8 -*-
"""Figure B5: 'Einpendler-Tore am Regionsrand' — where external commuter traffic
enters the ZGB region.

Real data only:
- road gates:  eqasim-data/output_bs_cordon_gatecheck/road_gates.gpkg
               (gate_id, road_class, capacity, inbound = BA SvB in-commuters
               gravity-assigned to the gate, EPSG:25832)
- rail entry:  eqasim-data/output_bs_cordon_gatecheck/pt_stations.gpkg
               (external rail boarding stations, reach = direct / one transfer)
- region body: 100% all-features PopulationSim homes.gpkg (subsampled point cloud)
- Kreis boundaries: simwrapper/kreis_socio.geojson (EPSG:4326 -> 25832)
"""
import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patheffects as pe
import os

BG = "#0a0e14"
INK = "#eef3fb"
SUB = "#8b95a7"
CREDIT = "#5a6577"
GRIDC = "#33465f"
HOMES_C = "#4a5a72"
AMBER = "#fbbf24"
AMBER_GLOW = "#f59e0b"
CYAN = "#22d3ee"

BASE = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs"
GATES_P = f"{BASE}/eqasim-data/output_bs_cordon_gatecheck/road_gates.gpkg"
PT_P = f"{BASE}/eqasim-data/output_bs_cordon_gatecheck/pt_stations.gpkg"
HOMES_P = ("C:/Users/bienzeisler/Downloads/popsim_100pct_results/"
           "output_bs_100pct_allfeat_popsim/braunschweig_100pct_allfeat_popsim_homes.gpkg")
KREIS_P = ("C:/Users/bienzeisler/Downloads/popsim_100pct_results/"
           "output_bs_100pct_allfeat_popsim/simwrapper/kreis_socio.geojson")
OUT = ("C:/Users/BIENZE~1/AppData/Local/Temp/claude/"
       "c--Users-bienzeisler-Documents-GitHub-eqasim-bs/"
       "b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/fig_b5_gates.png")

# ---------------------------------------------------------------- fonts
FONT_DIR = f"{BASE}/braunschweig/analysis/poster/fonts"
for f in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
    p = os.path.join(FONT_DIR, f)
    if os.path.exists(p):
        fm.fontManager.addfont(p)
plt.rcParams["font.family"] = "Space Mono" if any(
    "Space Mono" in f.name for f in fm.fontManager.ttflist) else "DejaVu Sans Mono"

# ---------------------------------------------------------------- data
gates = gpd.read_file(GATES_P)                     # EPSG:25832
pt = gpd.read_file(PT_P)                           # EPSG:25832
homes = gpd.read_file(HOMES_P, columns=[])         # geometry only, EPSG:25832
kreis = gpd.read_file(KREIS_P).to_crs("EPSG:25832")

# Deduplicate rail stations: rows repeat per link direction / source Kreis.
pt["xr"] = pt.geometry.x.round(0)
pt["yr"] = pt.geometry.y.round(0)
st = (pt.groupby(["xr", "yr"])
        .agg(reach=("reach", lambda s: "direct" if (s == "direct").any() else "transfer"))
        .reset_index())
st_dir = st[st.reach == "direct"]
st_tra = st[st.reach == "transfer"]

# Homes subsample (fixed seed for reproducibility)
rng = np.random.default_rng(42)
n_sub = 320_000
idx = rng.choice(len(homes), size=min(n_sub, len(homes)), replace=False)
hx = homes.geometry.x.to_numpy()[idx]
hy = homes.geometry.y.to_numpy()[idx]

# ---------------------------------------------------------------- gate clusters
# Twin gates (two carriageways of one interchange) sit metres apart; merge gates
# within 1.5 km for the ranking so one corridor is counted once.
gx = gates.geometry.x.to_numpy()
gy = gates.geometry.y.to_numpy()
n = len(gates)
parent = list(range(n))
def find(a):
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a
for i in range(n):
    for j in range(i + 1, n):
        if (gx[i]-gx[j])**2 + (gy[i]-gy[j])**2 < 1500.0**2:
            parent[find(i)] = find(j)
gates["cluster"] = [find(i) for i in range(n)]
clu = (gates.assign(x=gx, y=gy)
       .groupby("cluster")
       .agg(inbound=("inbound", "sum"),
            x=("x", "mean"), y=("y", "mean"),
            n_gates=("gate_id", "size"),
            motorway=("road_class", lambda s: (s == "motorway").any()))
       .sort_values("inbound", ascending=False)
       .reset_index(drop=True))

# 8-way compass direction from the region's home centroid (data-derived).
cx, cy = hx.mean(), hy.mean()
DIRS = ["O", "NO", "N", "NW", "W", "SW", "S", "SO"]
def compass(x, y):
    ang = np.degrees(np.arctan2(y - cy, x - cx)) % 360.0
    return DIRS[int(((ang + 22.5) % 360) // 45)]
clu["dir"] = [compass(r.x, r.y) for r in clu.itertuples()]
clu["cls_de"] = np.where(clu.motorway, "Autobahn", "Bundesstraße")
TOPN = 7
top = clu.head(TOPN).copy()

def de_num(v):
    return f"{int(round(v)):,}".replace(",", ".")

# ---------------------------------------------------------------- figure
fig = plt.figure(figsize=(16, 9), dpi=170)
fig.patch.set_facecolor(BG)

ax = fig.add_axes([0.005, 0.02, 0.58, 0.90])
ax.set_facecolor(BG)
ax.set_axis_off()
ax.set_aspect("equal")

# region body: very dim home point cloud
ax.scatter(hx, hy, s=0.5, c=HOMES_C, alpha=0.065, linewidths=0, rasterized=True)

# Kreis boundaries
kreis.boundary.plot(ax=ax, color=GRIDC, linewidth=0.7, alpha=0.9, zorder=2)

# Kreis name labels for orientation (standard ARS-5 codes of the 8 ZGB Kreise).
ARS_NAMES = {"03101": "Braunschweig", "03102": "Salzgitter", "03103": "Wolfsburg",
             "03151": "Gifhorn", "03153": "Goslar", "03154": "Helmstedt",
             "03157": "Peine", "03158": "Wolfenbüttel"}
halo = [pe.withStroke(linewidth=2.2, foreground=BG)]
for r in kreis.itertuples():
    name = ARS_NAMES.get(str(r.ars5))
    if name:
        pt_lab = r.geometry.representative_point()
        ax.text(pt_lab.x, pt_lab.y, name, fontsize=7.5, color="#65758f",
                ha="center", va="center", zorder=2.5, alpha=0.95,
                path_effects=halo)

# External orientation labels at the two largest boarding-station areas
# (source Kreis with most station rows; ARS 03241 = Region Hannover, 15003 = Magdeburg).
EXT_NAMES = {"03241": "Raum Hannover", "15003": "Magdeburg"}
ext = pt.groupby("source_ars5").agg(n=("stop_id", "size"), x=("xr", "mean"),
                                    y=("yr", "mean"))
for ars, name in EXT_NAMES.items():
    if ars in ext.index:
        ax.text(ext.loc[ars, "x"], ext.loc[ars, "y"] - 10500, name, fontsize=8,
                color="#4f8494", ha="center", va="top", zorder=4.5,
                path_effects=halo)

# rail entry stations, cyan glow circles (direct brighter than one-transfer)
for sdf, s_glow, s_dot, a_glow, a_dot in (
        (st_tra, 70, 8, 0.05, 0.45),
        (st_dir, 130, 15, 0.09, 0.85)):
    ax.scatter(sdf.xr, sdf.yr, s=s_glow, c=CYAN, alpha=a_glow, linewidths=0, zorder=3)
    ax.scatter(sdf.xr, sdf.yr, s=s_dot, c=CYAN, alpha=a_dot, linewidths=0, zorder=4)

# road gates: amber diamonds, area ~ assigned in-commuters, two-pass glow
smax, smin = 320.0, 26.0
gs = smin + (smax - smin) * (gates["inbound"] - gates["inbound"].min()) / \
     (gates["inbound"].max() - gates["inbound"].min())
ax.scatter(gx, gy, s=gs * 4.2, marker="D", c=AMBER_GLOW, alpha=0.10, linewidths=0, zorder=5)
ax.scatter(gx, gy, s=gs * 1.8, marker="D", c=AMBER_GLOW, alpha=0.22, linewidths=0, zorder=5)
ax.scatter(gx, gy, s=gs, marker="D", c=AMBER, alpha=0.95, linewidths=0.5,
           edgecolors="#fff7e0", zorder=6)

# rank markers "1".."7" next to the top corridors; alternate the offset when a
# higher-ranked cluster sits nearby so the badges do not overlap.
ALT_OFFSETS = [(10, 10), (-24, -20), (12, -22), (-26, 12)]
placed = []
for i, r in enumerate(top.itertuples(), start=1):
    n_close = sum(1 for (px_, py_) in placed
                  if (px_ - r.x) ** 2 + (py_ - r.y) ** 2 < 4000.0 ** 2)
    off = ALT_OFFSETS[min(n_close, len(ALT_OFFSETS) - 1)]
    placed.append((r.x, r.y))
    ax.annotate(str(i), xy=(r.x, r.y), xytext=off, textcoords="offset points",
                fontsize=10.5, fontweight="bold", color="#ffe9b0", zorder=7,
                bbox=dict(boxstyle="circle,pad=0.22", fc="#1a1408", ec=AMBER_GLOW,
                          lw=0.7, alpha=0.85))

# map extent: region + gates + surrounding boarding stations
pad = 6000
x0 = min(gx.min(), st.xr.min()) - pad
x1 = max(gx.max(), st.xr.max()) + pad
y0 = min(gy.min(), st.yr.min()) - pad
y1 = max(gy.max(), st.yr.max()) + pad
ax.set_xlim(x0, x1)
ax.set_ylim(y0, y1)

# ---------------------------------------------------------------- text panel
fig.text(0.615, 0.925, "Einpendler-Tore am Regionsrand", fontsize=16.5,
         color=INK, fontweight="bold", va="top")
fig.text(0.615, 0.876,
         "Wo externer Pendlerverkehr in die Region Braunschweig (ZGB)\n"
         "eintritt: Straßen-Tore am Kordon und Bahn-Einstiegsstationen im Umland",
         fontsize=10, color=SUB, va="top", linespacing=1.5)

px = 0.615
py = 0.790
lax = fig.add_axes([px, 0.10, 0.375, py - 0.10])
lax.set_facecolor(BG)
lax.set_axis_off()
lax.set_xlim(0, 1)
lax.set_ylim(0, 1)

# legend box
lax.add_patch(plt.Rectangle((0.0, 0.72), 1.0, 0.28, fc="#0f141d", ec="#1d2633",
                            lw=1.0, zorder=1))
ly = 0.945
lax.scatter([0.05], [ly], s=340, marker="D", c=AMBER_GLOW, alpha=0.18, linewidths=0)
lax.scatter([0.05], [ly], s=120, marker="D", c=AMBER, linewidths=0.5, edgecolors="#fff7e0")
lax.text(0.11, ly, "Straßen-Tor (Autobahn / Bundesstraße)",
         fontsize=9.5, color=INK, va="center")
lax.text(0.11, ly - 0.062, "Größe ~ zugeordnete SvB-Einpendler",
         fontsize=8, color=SUB, va="center")
ly2 = 0.815
lax.scatter([0.05], [ly2], s=170, c=CYAN, alpha=0.14, linewidths=0)
lax.scatter([0.05], [ly2], s=42, c=CYAN, alpha=0.9, linewidths=0)
lax.text(0.11, ly2, "Bahn-Tor (Station, direkt erreichbar)",
         fontsize=9.5, color=INK, va="center")
ly3 = 0.752
lax.scatter([0.05], [ly3], s=90, c=CYAN, alpha=0.08, linewidths=0)
lax.scatter([0.05], [ly3], s=20, c=CYAN, alpha=0.45, linewidths=0)
lax.text(0.11, ly3, "Bahn-Tor (mit einem Umstieg)",
         fontsize=9.5, color=SUB, va="center")

# key numbers
ky = 0.635
lax.text(0.0, ky, f"{len(gates)}", fontsize=17, color=AMBER, fontweight="bold", va="center")
lax.text(0.0, ky - 0.058, "Straßen-Tore", fontsize=8.5, color=SUB, va="center")
lax.text(0.34, ky, de_num(gates["inbound"].sum()), fontsize=17, color=AMBER,
         fontweight="bold", va="center")
lax.text(0.34, ky - 0.058, "SvB-Einpendler (zugeordnet)", fontsize=8.5, color=SUB, va="center")
lax.text(0.0, ky - 0.155, f"{len(st)}", fontsize=17, color=CYAN, fontweight="bold", va="center")
lax.text(0.0, ky - 0.213, "Bahn-Stationen", fontsize=8.5, color=SUB, va="center")
lax.text(0.34, ky - 0.155, f"{len(st_dir)} / {len(st_tra)}", fontsize=17, color=CYAN,
         fontweight="bold", va="center")
lax.text(0.34, ky - 0.213, "direkt / mit Umstieg", fontsize=8.5, color=SUB, va="center")

# top corridors ranking
lax.text(0.0, 0.335, "Stärkste Korridore (Straße)", fontsize=10.5,
         color=INK, fontweight="bold", va="center")
lax.plot([0.0, 1.0], [0.305, 0.305], color="#1d2633", lw=1.0)
row_y = 0.262
for i, r in enumerate(top.itertuples(), start=1):
    yy = row_y - (i - 1) * 0.046
    lax.text(0.0, yy, f"{i}", fontsize=9, color="#ffe9b0", fontweight="bold", va="center")
    lax.text(0.07, yy, f"{r.cls_de} ({r.dir})", fontsize=9, color=INK, va="center")
    lax.text(1.0, yy, de_num(r.inbound), fontsize=9, color=AMBER, va="center", ha="right")

# credit
fig.text(0.012, 0.018,
         "eqasim-bs | Kordon-Tore: output_bs_cordon_gatecheck (road_gates / pt_stations), "
         "Einpendler: BA-Pendlerstatistik (Gravitationszuordnung) | Wohnorte: 100-%-Lauf "
         "all-features (PopulationSim), Juni 2026",
         fontsize=7.5, color=CREDIT)

fig.savefig(OUT, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("saved", OUT)
print(top[["cls_de", "dir", "inbound", "n_gates"]])
