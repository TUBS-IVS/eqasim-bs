# -*- coding: utf-8 -*-
"""Figure QA-3: synthetic vs reference household-size distribution on a COMMON
PERSON basis (share of persons living in households of size n).

Basis note:
  - Since #97 (person-basis fix, PR #103), controls_long.csv
    control=="household_size" reports synthetic_count PERSON-weighted, matching
    its Zensus 2022 1000A-2081 target (a PERSON share: persons in private
    households by size class).
  - This figure computes the synthetic person shares directly and exactly from
    households.csv (household_size column; 6+ uses the actual person sum, not
    6*count), independent of the control's own aggregation.
"""
import matplotlib
matplotlib.use("Agg")

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Patch

BG = "#0a0e14"
CYAN = "#22d3ee"
GREY = "#8b95a7"
ROSE = "#fb7185"
GRID = "#1d2633"
TITLE_C = "#eef3fb"
SUB_C = "#8b95a7"
CREDIT_C = "#5a6577"
TICK_C = "#c7d0dd"

BASE = Path("C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim")
OUT = Path(__file__).with_suffix(".png")

# ---------------------------------------------------------------- fonts
font_dir = Path("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts")
family = ["DejaVu Sans Mono"]
if font_dir.exists():
    for f in font_dir.glob("*.ttf"):
        font_manager.fontManager.addfont(str(f))
    family = ["Space Mono", "DejaVu Sans Mono"]  # glyph-level fallback
plt.rcParams["font.family"] = family

# ---------------------------------------------------------------- data
ORDER = ["1", "2", "3", "4", "5", "6+"]

# Synthetic person shares: exact person sums per size class from households.csv.
hh = pd.read_csv(BASE / "braunschweig_100pct_allfeat_popsim_households.csv",
                 sep=";", usecols=["household_size"])
cls = np.where(hh["household_size"] >= 6, "6+", hh["household_size"].astype(str))
persons_per_class = hh.groupby(cls)["household_size"].sum().reindex(ORDER)
hh_per_class = hh.groupby(cls)["household_size"].size().reindex(ORDER)
total_persons = float(hh["household_size"].sum())
total_households = int(len(hh))
syn = (persons_per_class / total_persons * 100.0).to_numpy(dtype=float)

# Reference person shares: aggregate target_count of the household_size control
# (per-Gemeinde Zensus person shares scaled by the tool) over all Gemeinden.
cl = pd.read_csv(BASE / "analysis/population_validation/controls_long.csv")
hs = cl[cl["control"] == "household_size"]
tgt = hs.groupby("category")["target_count"].sum().reindex(ORDER)
ref = (tgt / tgt.sum() * 100.0).to_numpy(dtype=float)

delta = syn - ref
ratio_6plus = syn[-1] / ref[-1] * 100.0


def de(x: float, dec: int = 1) -> str:
    return f"{x:.{dec}f}".replace(".", ",")


def de_int(x: float) -> str:
    return f"{int(round(x)):,}".replace(",", ".")


# ---------------------------------------------------------------- figure
fig, ax = plt.subplots(figsize=(16, 9), dpi=170)
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)
fig.subplots_adjust(top=0.815, bottom=0.105, left=0.115, right=0.965)

y = np.arange(len(ORDER))[::-1].astype(float)  # class "1" on top
h = 0.30
off = 0.185

# x grid (thin, behind bars)
XMAX = 43.5
for gx in range(0, 36, 5):
    ax.axvline(gx, color=GRID, lw=0.8, zorder=0)

# glow pass (wide, low alpha) + sharp pass
ax.barh(y + off, syn, height=h * 2.05, color=CYAN, alpha=0.14, zorder=2)
ax.barh(y + off, syn, height=h, color=CYAN, alpha=0.95, zorder=3)
ax.barh(y - off, ref, height=h * 2.05, color=GREY, alpha=0.10, zorder=2)
ax.barh(y - off, ref, height=h, color=GREY, alpha=0.38,
        edgecolor="#aeb8c9", lw=1.1, zorder=3)

# value labels
for yi, v in zip(y + off, syn):
    ax.text(v + 0.55, yi, de(v) + " %", va="center", ha="left",
            color="#bfeffa", fontsize=9.5, zorder=4)
for yi, v in zip(y - off, ref):
    ax.text(v + 0.55, yi, de(v) + " %", va="center", ha="left",
            color=GREY, fontsize=9.5, zorder=4)

# delta column (aligned right)
X_DELTA = 40.2
ax.text(X_DELTA, y[0] + 0.92, "Δ in pp", ha="center", va="center",
        color=SUB_C, fontsize=9)
for yi, d, c in zip(y, delta, ORDER):
    col = ROSE if c in ("5", "6+") else "#7f8b9e"
    sign = "+" if d >= 0 else "-"
    weight = "bold" if c in ("5", "6+") else "normal"
    ax.text(X_DELTA, yi, sign + de(abs(d)), ha="center", va="center",
            color=col, fontsize=11, fontweight=weight)

# rose accent frame around the 5 / 6+ groups
frame = FancyBboxPatch(
    (-0.35, -0.62), 11.6, 2.24,
    boxstyle="round,pad=0.02,rounding_size=0.18",
    facecolor="none", edgecolor=ROSE, lw=1.4, alpha=0.9, zorder=5)
ax.add_patch(frame)

ax.annotate(
    "Bekannter Engpass: große Haushalte sind im\n"
    "Befragungs-Donor selten → 6+ erreicht nur "
    + de(ratio_6plus, 0) + " %\n"
    "des Referenzanteils. → Roadmap-Schritt 01:\n"
    "Donor-Anpassung",
    xy=(11.35, 0.5), xytext=(14.6, 0.5),
    va="center", ha="left", color=ROSE, fontsize=10, linespacing=1.55,
    arrowprops=dict(arrowstyle="-", color=ROSE, lw=1.1, alpha=0.8))

# axes cosmetics
ax.set_xlim(0, XMAX)
ax.set_ylim(-0.85, 5.95)
ax.set_yticks(y)
ax.set_yticklabels(["1 Person", "2 Personen", "3 Personen",
                    "4 Personen", "5 Personen", "6+ Personen"],
                   fontsize=11.5, color=TICK_C)
ax.set_xticks(range(0, 36, 5))
ax.set_xticklabels([str(v) + " %" for v in range(0, 36, 5)],
                   fontsize=9, color=SUB_C)
ax.tick_params(length=0)
for s in ax.spines.values():
    s.set_visible(False)
ax.set_xlabel("Anteil der Personen", fontsize=10, color=SUB_C, labelpad=8)

# legend (top right, in the title band)
handles = [
    Patch(facecolor=CYAN, alpha=0.95, label="Synthese (100-%-Lauf, PopulationSim)"),
    Patch(facecolor=GREY, alpha=0.38, edgecolor="#aeb8c9",
          label="Referenz Zensus 2022 (Gemeinde-Kontrollen)"),
]
leg = fig.legend(handles=handles, loc="upper right", bbox_to_anchor=(0.962, 0.975),
                 frameon=False, fontsize=10, labelcolor=TICK_C,
                 handlelength=1.4, handleheight=1.0, borderaxespad=0.0)

# title / subtitle / credit
fig.text(0.045, 0.965,
         "Haushaltsgrößen: nah dran — außer bei den großen Haushalten",
         ha="left", va="top", fontsize=16.5, color=TITLE_C, fontweight="bold")
fig.text(0.045, 0.905,
         "Anteil der Personen nach Größe ihres Haushalts (gemeinsame Basis) · "
         "Referenz: Gemeinde-Kontrollwerte der Validierung (Zensus 2022, 1000A-2081)",
         ha="left", va="top", fontsize=10, color=SUB_C)
fig.text(0.045, 0.872,
         "Region Braunschweig (ZGB) · " + de_int(total_persons) + " Personen in "
         + de_int(total_households) + " Haushalten",
         ha="left", va="top", fontsize=10, color=SUB_C)
fig.text(0.045, 0.022,
         "Daten: braunschweig_100pct_allfeat_popsim_households.csv (household_size) · "
         "analysis/population_validation/controls_long.csv (control=household_size) · "
         "100-%-PopulationSim-Lauf, Export 2026-06-30",
         ha="left", va="bottom", fontsize=7.5, color=CREDIT_C)

fig.savefig(OUT, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("written:", OUT)
print("syn:", np.round(syn, 2))
print("ref:", np.round(ref, 2))
print("delta:", np.round(delta, 2))
print("6+ ratio:", round(ratio_6plus, 1))
