# -*- coding: utf-8 -*-
"""
Presentation figure: mobility indicators of the synthetic travel demand.

REWORKED LAYOUT (leads with what fits):
LEFT  (2x2 KPI tiles, green accents) "Was gut sitzt":
  - purpose share Einkauf     (synthesis vs MiD 2023 W1, renormalised)
  - purpose share Ausbildung  (synthesis vs MiD 2023 W1, renormalised)
  - trips per person/day 2.58 with national MiD report 2.9 as context tick
  - commute distance distribution EMD 0.054 (threshold gauge, see next slide)
RIGHT (compact secondary panel, rose/amber) "Bekannter Donor-Effekt":
  - paired bars Arbeit / Freizeit / Mobilitaetsquote + 2-line explanation.

Inputs (all real, no invented numbers):
  - .../analysis/population_validation/trip_coherence_*.csv       (run output)
  - eqasim-data/data/braunschweig/mid/mid2023_P36_1.csv           (committed)
  - eqasim-data/data/braunschweig/mid/mid2023_W1.csv              (committed)
  - scratchpad figs/commute_bands.json                            (computed on this run)
  - MiD 2023 Ergebnisbericht: 2.9 Wege/Person/Tag (Tab. 3, S. 34, bundesweit)
    -- context tick only, page-cited.

Output: fig_mob_trips.png (dark deck style, 16:9, dpi 170)
"""

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager
from matplotlib.patches import FancyBboxPatch
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- paths ----
VAL_DIR = ("C:/Users/bienzeisler/Downloads/popsim_100pct_results/"
           "output_bs_100pct_allfeat_popsim/analysis/population_validation/")
MID_DIR = ("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/eqasim-data/"
           "data/braunschweig/mid/")
FONT_DIR = ("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/"
            "analysis/poster/fonts/")
FIG_DIR = ("C:/Users/BIENZE~1/AppData/Local/Temp/claude/"
           "c--Users-bienzeisler-Documents-GitHub-eqasim-bs/"
           "b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/")
OUT_PNG = FIG_DIR + "fig_mob_trips.png"

# ---------------------------------------------------------------- style ----
BG = "#0a0e14"
PANEL = "#0f1520"
GRID = "#1d2633"
TXT = "#eef3fb"
SUB = "#8b95a7"
CREDIT = "#5a6577"
VIOLET = "#6366f1"
REFGREY = "#4b5568"
ROSE = "#fb7185"
AMBER = "#fbbf24"
GREEN = "#34d399"

for fname in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
    fpath = os.path.join(FONT_DIR, fname)
    if os.path.exists(fpath):
        font_manager.fontManager.addfont(fpath)

plt.rcParams.update({
    "font.family": "Space Mono" if any(
        f.name == "Space Mono" for f in font_manager.fontManager.ttflist)
        else "DejaVu Sans Mono",
    "figure.facecolor": BG,
    "axes.facecolor": BG,
    "text.color": TXT,
    "axes.edgecolor": GRID,
    "xtick.color": SUB,
    "ytick.color": TXT,
})


def de(v, digits=1):
    """German decimal-comma number format."""
    return f"{v:.{digits}f}".replace(".", ",")


# ----------------------------------------------------------------- data ----
tpp = pd.read_csv(VAL_DIR + "trip_coherence_trips_per_person.csv")
mob = pd.read_csv(VAL_DIR + "trip_coherence_mobility_by_segment.csv")
pur = pd.read_csv(VAL_DIR + "trip_coherence_purpose.csv")
p36 = pd.read_csv(MID_DIR + "mid2023_P36_1.csv", dtype={"ars5": str})
w1 = pd.read_csv(MID_DIR + "mid2023_W1.csv", dtype={"ars5": str})
with open(FIG_DIR + "commute_bands.json", encoding="utf-8") as f:
    commute = json.load(f)

# --- overall values (employed 0/1 partitions the full population) ----------
emp_t = tpp[tpp.segment == "employed"]
trips_overall = emp_t.n_trips.sum() / emp_t.n_persons.sum()

emp_m = mob[mob.segment == "employed"]
mob_synth = 100.0 * emp_m.n_mobile.sum() / emp_m.n_persons.sum()
mob_mid = float(p36.loc[p36.ars5 == "03ZGB", "mobil"].iloc[0])

# --- purposes: cross-check run target against committed W1 ------------------
w1g = w1[w1.ars5 == "03ZGB"].iloc[0]
w1_four = {p: float(w1g[p]) for p in ("arbeit", "ausbildung", "einkauf", "freizeit")}
w1_sum = sum(w1_four.values())
purx = pur.set_index("purpose")
for p, v in w1_four.items():
    ref = v / w1_sum
    run = float(purx.loc[p, "target_share"])
    assert abs(ref - run) < 1e-9, f"W1 target mismatch for {p}: {ref} vs {run}"

share = {p: (100.0 * float(purx.loc[p, "realized_share"]),
             100.0 * float(purx.loc[p, "target_share"]))
         for p in ("arbeit", "ausbildung", "einkauf", "freizeit")}

emd = float(commute["emd"])
EMD_THRESHOLD = 0.08          # acceptance threshold used on the commute slide
TRIPS_MID_NATIONAL = 2.9      # MiD 2023 Ergebnisbericht, Tab. 3, S. 34 (bundesweit)

print("verified:",
      f"trips_overall={trips_overall:.4f}",
      f"mob_synth={mob_synth:.2f} mob_mid={mob_mid:.1f}",
      f"einkauf={share['einkauf'][0]:.1f}/{share['einkauf'][1]:.1f}",
      f"ausbildung={share['ausbildung'][0]:.1f}/{share['ausbildung'][1]:.1f}",
      f"arbeit={share['arbeit'][0]:.1f}/{share['arbeit'][1]:.1f}",
      f"freizeit={share['freizeit'][0]:.1f}/{share['freizeit'][1]:.1f}",
      f"emd={emd:.4f}")

# ---------------------------------------------------------------- figure ---
fig = plt.figure(figsize=(16, 9), dpi=170)


def tile_axes(x, y, w, h, edge=GRID):
    """A rounded panel + a content axes (0..1 data coords) inside it."""
    box = FancyBboxPatch((x, y), w, h, transform=fig.transFigure,
                         boxstyle="round,pad=0.006,rounding_size=0.010",
                         facecolor=PANEL, edgecolor=edge, linewidth=1.1)
    box.set_zorder(0.5)          # keep the panel below the content axes
    fig.add_artist(box)
    ax = fig.add_axes([x, y, w, h])
    ax.set_zorder(2)
    ax.patch.set_visible(False)  # panel patch provides the background
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return ax


def glow_hbar(ax, y, x0, x1, color, h=0.09, alpha=0.95):
    """Horizontal glow bar in tile coordinates."""
    ax.fill_between([x0, x1], y - h * 0.95, y + h * 0.95,
                    color=color, alpha=0.16, zorder=2)
    ax.fill_between([x0, x1], y - h / 2, y + h / 2,
                    color=color, alpha=alpha, zorder=3)


def delta_badge(ax, x, y, text, color):
    ax.text(x, y, text, ha="right", va="center", fontsize=11.5, color=BG,
            fontweight="bold", zorder=5,
            bbox=dict(boxstyle="round,pad=0.32", facecolor=color,
                      edgecolor="none"))


# ---------------- layout geometry ----------------
TOP = 0.775
L_X, L_W = 0.048, 0.560           # left tile block
R_X, R_W = 0.652, 0.310           # right bias panel
TILE_GAP_X, TILE_GAP_Y = 0.022, 0.048
tw = (L_W - TILE_GAP_X) / 2
th = 0.298
row1_y = TOP - th
row2_y = row1_y - TILE_GAP_Y - th

# ---------------- section headers ----------------
fig.text(L_X + 0.004, TOP + 0.028, "WAS GUT SITZT", fontsize=13,
         color=GREEN, fontweight="bold", ha="left")
fig.text(R_X + 0.004, TOP + 0.028, "BEKANNTER DONOR-EFFEKT", fontsize=13,
         color=ROSE, fontweight="bold", ha="left")

# ================= TILES 1+2: purpose shares that fit ======================
BAR_X0, BAR_X1 = 0.30, 0.93        # bar track inside tile
BAR_SCALE = 30.0                   # % value mapped onto the track


def purpose_tile(x, y, name_de, key):
    real, targ = share[key]
    delta = real - targ
    sign = "+" if delta >= 0 else "−"
    ax = tile_axes(x, y, tw, th)
    ax.text(0.055, 0.865, f"Wegezweck {name_de}", fontsize=11.5, color=TXT,
            fontweight="bold", va="center")
    delta_badge(ax, 0.945, 0.865, f"{sign}{de(abs(delta))} pp", GREEN)

    def track(yy, val, col, lab, val_col, bold):
        w = (val / BAR_SCALE) * (BAR_X1 - BAR_X0)
        ax.plot([BAR_X0, BAR_X1], [yy, yy], color=GRID, lw=5,
                solid_capstyle="round", zorder=1)
        glow_hbar(ax, yy, BAR_X0, BAR_X0 + w, col)
        ax.text(BAR_X0 - 0.03, yy, lab, ha="right", va="center",
                fontsize=10, color=SUB)
        ax.text(BAR_X0 + w + 0.025, yy, f"{de(val)} %", ha="left",
                va="center", fontsize=11.5, color=val_col,
                fontweight="bold" if bold else "normal")

    track(0.565, real, GREEN, "Synthese", TXT, True)
    track(0.275, targ, VIOLET, "MiD W1", SUB, False)
    ax.text(0.055, 0.075, "MiD 2023 W1 (ZGB), auf 4 Kernzwecke renormiert",
            fontsize=8.2, color=CREDIT, va="center")


purpose_tile(L_X, row1_y, "Einkauf", "einkauf")
purpose_tile(L_X + tw + TILE_GAP_X, row1_y, "Ausbildung", "ausbildung")

# ================= TILE 3: trips per person/day ============================
ax = tile_axes(L_X, row2_y, tw, th)
ax.text(0.055, 0.865, "Wege je Person und Tag", fontsize=11.5, color=TXT,
        fontweight="bold", va="center")
ax.text(0.055, 0.565, de(trips_overall, 2), fontsize=27, color=GREEN,
        fontweight="bold", va="center")
ax.text(0.325, 0.545, "Synthese, alle Personen\n(inkl. nicht-mobiler)",
        fontsize=9, color=SUB, va="center")

# scale 0 .. 3.5 trips mapped onto the track
T0, T1, TMAX = 0.075, 0.93, 3.5
ty = 0.275
ax.plot([T0, T1], [ty, ty], color=GRID, lw=5, solid_capstyle="round", zorder=1)
tx_val = T0 + (trips_overall / TMAX) * (T1 - T0)
glow_hbar(ax, ty, T0, tx_val, GREEN)
tx_ref = T0 + (TRIPS_MID_NATIONAL / TMAX) * (T1 - T0)
ax.plot([tx_ref, tx_ref], [ty - 0.085, ty + 0.085], color=AMBER, lw=2.2,
        ls=(0, (2, 1.4)), zorder=4)
ax.text(tx_ref, ty + 0.155, "2,9", fontsize=10.5, color=AMBER, ha="center",
        va="center", fontweight="bold")
ax.text(0.055, 0.075,
        "Kontext-Marke: MiD-Bericht bundesweit 2,9 (Tab. 3, S. 34)",
        fontsize=8.2, color=CREDIT, va="center")

# ================= TILE 4: commute distance EMD ============================
ax = tile_axes(L_X + tw + TILE_GAP_X, row2_y, tw, th)
ax.text(0.055, 0.865, "Pendeldistanz-Verteilung", fontsize=11.5, color=TXT,
        fontweight="bold", va="center")
delta_badge(ax, 0.945, 0.865, "erfüllt", GREEN)
ax.text(0.055, 0.565, f"EMD {de(emd, 3)}", fontsize=27, color=GREEN,
        fontweight="bold", va="center")
ax.text(0.635, 0.545, "vs. MiD P13\n(Distanzklassen)", fontsize=9, color=SUB,
        va="center")

# gauge 0 .. 0.10 with threshold marker at 0.08
G0, G1, GMAX = 0.075, 0.93, 0.10
gy = 0.275
ax.plot([G0, G1], [gy, gy], color=GRID, lw=5, solid_capstyle="round", zorder=1)
gx_val = G0 + (emd / GMAX) * (G1 - G0)
glow_hbar(ax, gy, G0, gx_val, GREEN)
gx_thr = G0 + (EMD_THRESHOLD / GMAX) * (G1 - G0)
ax.plot([gx_thr, gx_thr], [gy - 0.085, gy + 0.085], color=SUB, lw=2.2,
        ls=(0, (2, 1.4)), zorder=4)
ax.text(gx_thr, gy + 0.155, "Schwelle 0,08", fontsize=9.5, color=SUB,
        ha="center", va="center")
ax.text(0.055, 0.075, "Details und Distanzklassen: siehe Folgefolie",
        fontsize=8.2, color=CREDIT, va="center")

# ================= RIGHT: compact donor-bias panel =========================
panel_h = row1_y + th - row2_y            # spans both tile rows
axb = tile_axes(R_X, row2_y, R_W, panel_h, edge="#3a2530")

bias_rows = [  # (label, synthesis value, reference value, ref label, accent)
    ("Arbeit",            share["arbeit"][0],   share["arbeit"][1],   "MiD W1",   ROSE),
    ("Freizeit",          share["freizeit"][0], share["freizeit"][1], "MiD W1",   AMBER),
    ("Mobilitätsquote", mob_synth,         mob_mid,              "MiD P36.1", AMBER),
]

BX0, BX1 = 0.335, 0.955
BSCALE = 90.0
row_tops = [0.895, 0.665, 0.435]
for (lab, sv, rv, rlab, accent), y0 in zip(bias_rows, row_tops):
    delta = sv - rv
    sign = "+" if delta >= 0 else "−"
    ax = axb
    ax.text(0.045, y0, lab, fontsize=11, color=TXT, fontweight="bold",
            va="center")
    ax.text(0.955, y0, f"{sign}{de(abs(delta))} pp", fontsize=10.5,
            color=accent, fontweight="bold", va="center", ha="right")
    for yy, val, col, vlab, vcol in (
            (y0 - 0.072, sv, accent, "Syn", TXT),
            (y0 - 0.136, rv, REFGREY, rlab, SUB)):
        w = (val / BSCALE) * (BX1 - BX0)
        ax.plot([BX0, BX1], [yy, yy], color=GRID, lw=3.6,
                solid_capstyle="round", zorder=1)
        glow_hbar(ax, yy, BX0, BX0 + w, col, h=0.040)
        ax.text(BX0 - 0.025, yy, vlab, ha="right", va="center", fontsize=8.2,
                color=SUB)
        ax.text(BX0 + w + 0.02, yy, de(val), ha="left", va="center",
                fontsize=9, color=vcol)

axb.plot([0.045, 0.955], [0.255, 0.255], color=GRID, lw=1.0)
axb.text(0.045, 0.215,
         "Erwerbstätige erhalten beim Matching pendel-lastige\n"
         "Tagebücher: Arbeit über-, Freizeit unterrepräsentiert.\n"
         "Bekannter, dokumentierter Effekt des Befragungs-\n"
         "Donors — kein Zufallsfehler. Anteile in %.",
         fontsize=9.2, color=SUB, va="top", linespacing=1.45)

# ================= titles / credit ==========================================
fig.text(L_X, 0.945,
         "Mobilitätskenngrößen: vieles sitzt — "
         "ein bekannter Donor-Effekt bleibt",
         fontsize=18.5, color=TXT, fontweight="bold", ha="left")
fig.text(L_X, 0.900,
         "Synthetische Bevölkerung ZGB Braunschweig, 100 % (popsim_mid) "
         "— Kenngrößen der Tagespläne vor der Verkehrssimulation",
         fontsize=11.5, color=SUB, ha="left")
fig.text(L_X, 0.868,
         "Tagespläne aus dem Befragungs-Donor (vor Verkehrsmittelwahl) "
         "— Verkehrsmittelanteile sind noch nicht kalibriert und werden "
         "hier bewusst nicht gezeigt.",
         fontsize=11.5, color=AMBER, ha="left")
fig.text(L_X, 0.048,
         "Quellen: Synthese-Lauf braunschweig_100pct_allfeat_popsim (Commit e1164cc), "
         "trip_coherence_*.csv · MiD 2023 Regionalauswertung ZGB: W1 (Wegezwecke),\n"
         "P36.1 (Mobilitätsquote), P13 (Pendeldistanz) · MiD 2023 Ergebnisbericht "
         "Tab. 3, S. 34 · Stand: 2026-07-03",
         fontsize=8, color=CREDIT, ha="left", va="top", linespacing=1.5)

fig.savefig(OUT_PNG, facecolor=BG, dpi=170)
print("written:", OUT_PNG)
