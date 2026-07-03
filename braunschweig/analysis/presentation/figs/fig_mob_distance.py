# Deck figure: commute distance-band distribution, model (synthesis) vs MiD 2023 P13.
# Data: commute_bands.json produced by compute_commute_bands.py (real data only).
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np

FIG_DIR = "C:/Users/BIENZE~1/AppData/Local/Temp/claude/c--Users-bienzeisler-Documents-GitHub-eqasim-bs/b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs"
FONT_DIR = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts"

# --- fonts ---
for f in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
    p = os.path.join(FONT_DIR, f)
    if os.path.exists(p):
        fm.fontManager.addfont(p)
FAMILY = "Space Mono" if any("Space Mono" in f.name for f in fm.fontManager.ttflist) else "DejaVu Sans Mono"
plt.rcParams["font.family"] = FAMILY

# --- palette / style ---
BG = "#0a0e14"
FG = "#eef3fb"
SUB = "#8b95a7"
DIM = "#5a6577"
GRID = "#1d2633"
CYAN = "#22d3ee"
MID_GREY = "#aab4c5"
GREEN = "#34d399"

with open(os.path.join(FIG_DIR, "commute_bands.json")) as fh:
    D = json.load(fh)

bands_de = ["unter 5 km", "5 bis 10 km", "10 bis 20 km", "20 bis 30 km",
            "30 bis 50 km", "50 bis 100 km", "über 100 km"]
sim = np.array(D["sim_shares_pct"])
mid = np.array(D["mid_shares_pct"])
delta = sim - mid


def de(v, nd=1):
    return f"{v:.{nd}f}".replace(".", ",")


fig, ax = plt.subplots(figsize=(16, 9), dpi=170)
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)

n = len(bands_de)
y = np.arange(n)[::-1] * 1.0  # top band first
bh = 0.32
off = 0.185

# value grid (thin, behind bars)
ax.set_axisbelow(True)
ax.xaxis.grid(True, color=GRID, linewidth=0.9)
ax.yaxis.grid(False)

# MiD bars (dim grey, reference)
ax.barh(y - off, mid, height=bh, color=MID_GREY, alpha=0.42, zorder=3)
ax.barh(y - off, mid, height=bh, color="none", edgecolor=MID_GREY, linewidth=0.8, alpha=0.75, zorder=4)

# Synthesis bars (neon cyan with glow: wide low-alpha layers behind the sharp bar)
ax.barh(y + off, sim, height=bh + 0.16, color=CYAN, alpha=0.12, zorder=2)
ax.barh(y + off, sim, height=bh + 0.07, color=CYAN, alpha=0.22, zorder=3)
ax.barh(y + off, sim, height=bh, color=CYAN, alpha=0.95, zorder=5)

# value labels at bar ends
for yi, s, m in zip(y, sim, mid):
    ax.text(s + 0.35, yi + off, de(s) + " %", va="center", ha="left",
            fontsize=11.5, color=CYAN, fontweight="bold")
    ax.text(m + 0.35, yi - off, de(m) + " %", va="center", ha="left",
            fontsize=11, color=SUB)

# small delta column on the right
x_delta = 33.6
ax.text(x_delta, y[0] + 0.78, "Modell − MiD", ha="center", va="bottom",
        fontsize=10.5, color=DIM)
for yi, d in zip(y, delta):
    sign = "+" if d >= 0 else "−"
    ax.text(x_delta, yi, f"{sign}{de(abs(d))} pp", ha="center", va="center",
            fontsize=10.5, color=SUB)

# axes cosmetics
ax.set_yticks(y)
ax.set_yticklabels(bands_de, fontsize=13, color=FG)
ax.set_xlim(0, 36.5)
ax.set_ylim(-0.75, n - 0.25 + 0.72)
xt = np.arange(0, 31, 5)
ax.set_xticks(xt)
ax.set_xticklabels([f"{t} %" for t in xt], fontsize=11, color=SUB)
for spine in ax.spines.values():
    spine.set_visible(False)
ax.tick_params(length=0)

# legend (custom rectangles, inside the empty upper-right plot area)
from matplotlib.patches import Rectangle

# single horizontal legend row in the free strip above the first band
leg_y = y[0] + 0.92
sw, sh = 0.9, 0.30
ax.add_patch(Rectangle((10.5, leg_y - sh / 2), sw, sh, facecolor=CYAN, alpha=0.95,
                       edgecolor="none", zorder=6))
ax.text(10.5 + sw + 0.45, leg_y, "Synthese (Modell, 100 %)", va="center", ha="left",
        fontsize=12.5, color=FG, zorder=6)
ax.add_patch(Rectangle((21.5, leg_y - sh / 2), sw, sh, facecolor=MID_GREY, alpha=0.45,
                       edgecolor=MID_GREY, linewidth=0.8, zorder=6))
ax.text(21.5 + sw + 0.45, leg_y, "MiD 2023 (Befragung)", va="center", ha="left",
        fontsize=12.5, color=FG, zorder=6)

# titles
fig.suptitle("Pendeldistanzen: nah an der MiD-Verteilung — Feinkalibrierung folgt",
             x=0.055, y=0.985, ha="left", fontsize=19, color=FG, fontweight="bold")
n_fmt = f"{D['n_commutes']:,}".replace(",", ".")
fig.text(0.055, 0.925,
         f"Anteil der Pendlerinnen und Pendler je Distanzklasse — {n_fmt} synthetische "
         f"Wohn-Arbeits-Beziehungen vs. MiD 2023 P13 (Großraum Braunschweig)",
         fontsize=12, color=SUB, ha="left")
fig.text(0.055, 0.893,
         "Earth Mover's Distance auf den Bandanteilen: "
         f"{de(D['emd'], 3)}  (Qualitätsschwelle ≤ 0,08 — erfüllt) · einzelne Bänder weichen bis ±9 pp ab; "
         "Feinkalibrierung der Gravitationsmodelle = Roadmap-Schritt 02",
         fontsize=12, color=GREEN, ha="left")
fig.text(0.055, 0.862,
         "Definitionen: Synthese = Luftlinie Wohnung–Arbeitsplatz × Umwegfaktor 1,3 (Annahme); "
         "MiD = Selbstangabe der einfachen Weglänge (n = 1.583 Befragte)",
         fontsize=10.5, color=DIM, ha="left")

# credit line
fig.text(0.055, 0.022,
         "Quellen: braunschweig_100pct_allfeat_popsim_commutes.gpkg (Synthese-Lauf 100 %, EPSG:25832) · "
         "mid2023_P13.csv (MiD 2023 Großraum Braunschweig, infas) · Stand 07/2026",
         fontsize=8, color=DIM, ha="left")

fig.subplots_adjust(left=0.155, right=0.975, top=0.815, bottom=0.075)
out = os.path.join(FIG_DIR, "fig_mob_distance.png")
fig.savefig(out, facecolor=BG, dpi=170, bbox_inches="tight", pad_inches=0.15)
print("saved", out)
