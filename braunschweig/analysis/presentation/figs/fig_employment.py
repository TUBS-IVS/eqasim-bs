# -*- coding: utf-8 -*-
# fig_employment.png -- "Erwerbstaetigkeit: an der amtlichen Statistik verankert"
# Two panels, bases never mixed within a panel:
#   LEFT : synthetic employment rate (persons 20+) vs committed Zensus 2022 anchor
#          (zensus2022_employment_by_age_ref.csv), exact for the 3 kreisfreie Staedte.
#   RIGHT: MiD P9 cross-check (persons 14+), values from the run's controls_long.csv,
#          P9 shown with uncertainty styling (hollow markers + spread band + n).
# Inputs (prepared by prep_employment_data.py / prep_ageband.py in this directory):
#   employment_panel_data.csv, employment_ageband_data.csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- style setup
BG = "#0a0e14"
INK = "#eef3fb"
INK_SUB = "#8b95a7"
INK_FAINT = "#5a6577"
GRID = "#1d2633"
CONN = "#33465f"
CYAN = "#22d3ee"     # synthetic model (entity color, both panels)
AMBER = "#fbbf24"    # official anchor (Zensus/GENESIS)
ROSE = "#fb7185"     # noisy MiD P9 cross-check
GREEN = "#34d399"    # grade: sehr gut

FONT_DIR = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts"
for f in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
    p = os.path.join(FONT_DIR, f)
    if os.path.exists(p):
        fm.fontManager.addfont(p)
family = "Space Mono" if any("Space Mono" in f.name for f in fm.fontManager.ttflist) else "DejaVu Sans Mono"
plt.rcParams.update({
    "font.family": family,
    "figure.facecolor": BG,
    "axes.facecolor": BG,
    "text.color": INK,
    "axes.edgecolor": GRID,
    "xtick.color": INK_SUB,
    "ytick.color": INK,
})

def de(v, nd=1):
    return f"{v:.{nd}f}".replace(".", ",")

NAME = {
    "03101": "Braunschweig", "03102": "Salzgitter", "03103": "Wolfsburg",
    "03151": "LK Gifhorn", "03153": "LK Goslar", "03154": "LK Helmstedt",
    "03157": "LK Peine", "03158": "LK Wolfenbüttel",
}

# ------------------------------------------------------------------ load data
panel = pd.read_csv(os.path.join(HERE, "employment_panel_data.csv"), dtype={"ars5": str})
band = pd.read_csv(os.path.join(HERE, "employment_ageband_data.csv"), dtype={"ars5": str})

# LEFT: 20+ base (bands 20-29 ... 80+), synthetic vs committed Zensus reference
adult = band[~band["age_band"].isin(["0-9", "10-19"])]
left = adult.groupby("ars5").apply(
    lambda g: pd.Series({
        "syn": 100.0 * g["emp"].sum() / g["n"].sum(),
        "ref": 100.0 * g["erwerbstaetige"].sum() / g["total"].sum(),
    }), include_groups=False).reset_index()
left["delta"] = left["syn"] - left["ref"]
left = left.sort_values("syn", ascending=False).reset_index(drop=True)
print(left)

# RIGHT: controls_long values (14+ base) + P9 respondent counts
right = panel[["ars5", "n_unweighted", "synthetic_pct", "target_pct"]].copy()
right = right.sort_values("target_pct", ascending=True).reset_index(drop=True)
p9_lo, p9_hi = right["target_pct"].min(), right["target_pct"].max()
print(right, p9_lo, p9_hi)

# -------------------------------------------------------------------- figure
fig = plt.figure(figsize=(16, 9))

# title block
fig.text(0.045, 0.955, "Erwerbstätigkeit: an der amtlichen Statistik verankert",
         fontsize=18.5, fontweight="bold", color=INK, ha="left", va="top")
fig.text(0.045, 0.905, "Gleiches Modell, zwei Referenzen — der Anker sitzt, "
         "der Stichproben-Quervergleich streut.",
         fontsize=11.5, color=INK_SUB, ha="left", va="top")

ax1 = fig.add_axes([0.075, 0.30, 0.36, 0.46])   # left panel
ax2 = fig.add_axes([0.55, 0.175, 0.40, 0.585])  # right panel

def style_axis(ax, xlim, xticks):
    ax.set_xlim(*xlim)
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{t:d} %" for t in xticks], fontsize=10.5, color=INK_SUB)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.xaxis.grid(True, color=GRID, lw=0.8, zorder=0)
    ax.tick_params(length=0)

def legend_marker(ax, fx, fy, color, marker="o", hollow=False):
    """Draw a legend marker at figure coordinates (Space Mono lacks ●/◆/○ glyphs)."""
    kw = dict(transform=fig.transFigure, clip_on=False, zorder=10, s=95, marker=marker)
    if hollow:
        ax.scatter([fx], [fy], facecolors=BG, edgecolors=color, linewidths=2.0, **kw)
    else:
        ax.scatter([fx], [fy], color=color, lw=0, **kw)

def glow_dot(ax, x, y, color, s=170, marker="o", hollow=False):
    if hollow:
        ax.scatter([x], [y], s=s * 3.0, marker=marker, facecolors="none",
                   edgecolors=color, linewidths=5, alpha=0.18, zorder=3)
        ax.scatter([x], [y], s=s, marker=marker, facecolors=BG,
                   edgecolors=color, linewidths=2.2, zorder=4)
    else:
        ax.scatter([x], [y], s=s * 3.0, marker=marker, color=color,
                   alpha=0.18, lw=0, zorder=3)
        ax.scatter([x], [y], s=s, marker=marker, color=color, lw=0, zorder=4)

# ------------------------------------------------------------- LEFT: anchor
fig.text(0.045, 0.850, "Der Anker · GENESIS/Zensus 2022", fontsize=13.5,
         fontweight="bold", color=AMBER, ha="left", va="top")
fig.text(0.045, 0.817, "Erwerbstätigenquote · Basis: Personen ab 20 Jahren\n"
         "kreisfreie Städte (kreisgenaue Referenz)",
         fontsize=10.5, color=INK_SUB, ha="left", va="top", linespacing=1.45)

style_axis(ax1, (44, 74), [45, 50, 55, 60, 65, 70])
ys = list(range(len(left)))[::-1]
ax1.set_ylim(-0.75, len(left) - 0.25)
ax1.set_yticks(ys)
ax1.set_yticklabels([NAME[a] for a in left["ars5"]], fontsize=12.5, color=INK)

for y, (_, r) in zip(ys, left.iterrows()):
    ax1.plot([r["ref"], r["syn"]], [y, y], color=CONN, lw=2.4,
             solid_capstyle="round", zorder=2)
    glow_dot(ax1, r["ref"], y, AMBER, s=200, marker="D")
    glow_dot(ax1, r["syn"], y, CYAN, s=190, marker="o")
    ax1.text(r["ref"], y - 0.34, de(r["ref"]) + " %", fontsize=11,
             color=AMBER, ha="center", va="top")
    ax1.text(r["syn"], y + 0.32, de(r["syn"]) + " %", fontsize=11,
             color=CYAN, ha="center", va="bottom")
    ax1.text(70.4, y, "+" + de(r["delta"]) + " pp", fontsize=11,
             color=GREEN, ha="left", va="center")

ax1.text(70.4, len(left) - 0.34, "Abweichung", fontsize=9.5, color=INK_FAINT,
         ha="left", va="bottom")

# legend (left)
lx = 0.075
legend_marker(ax1, lx, 0.247, CYAN, marker="o")
fig.text(lx + 0.014, 0.247, "Synthese (100-%-Lauf)", fontsize=10.5, color=INK, ha="left", va="center")
legend_marker(ax1, lx + 0.185, 0.247, AMBER, marker="D")
fig.text(lx + 0.199, 0.247, "Zensus 2022 (amtlicher Anker)", fontsize=10.5, color=INK,
         ha="left", va="center")

fig.text(lx, 0.20,
         "Die Synthese verankert die Erwerbstätigkeit je 100-m-Zelle nach Alter\n"
         "und Geschlecht an den amtlichen Zensus-Niveaus —\n"
         "Abweichung unter 2 Prozentpunkten (pp).",
         fontsize=10.5, color=INK, ha="left", va="top", linespacing=1.5)
fig.text(lx, 0.115,
         "Landkreise: Anker = Bundes-Altersform x Zensus-Kreisniveau (Referenzdatei kreisgenau\n"
         "nur für die kreisfreien Städte). Basis 20+, da das Output-Flag „employed“ für\n"
         "unter 20-Jährige (Schüler/Ausbildung) von der Zensus-Abgrenzung abweicht.",
         fontsize=9.5, color=INK_SUB, ha="left", va="top", linespacing=1.5)

# ------------------------------------------------------ RIGHT: P9 cross-check
fig.text(0.55, 0.850, "Der Quervergleich · MiD 2023 P9", fontsize=13.5,
         fontweight="bold", color=ROSE, ha="left", va="top")
fig.text(0.55, 0.817, "Erwerbstätigenanteil · Basis: Personen ab 14 Jahren\n"
         "alle 8 Kreise der Region",
         fontsize=10.5, color=INK_SUB, ha="left", va="top", linespacing=1.45)

style_axis(ax2, (38, 73), [40, 45, 50, 55, 60, 65])
ys2 = list(range(len(right)))[::-1]
ax2.set_ylim(-0.75, len(right) - 0.25)
ax2.set_yticks(ys2)
labels2 = []
for a in right["ars5"]:
    labels2.append(NAME[a])
ax2.set_yticklabels(labels2, fontsize=12, color=INK)
# highlight Wolfsburg row label
for tick, a in zip(ax2.get_yticklabels(), right["ars5"]):
    if a == "03103":
        tick.set_color(ROSE)
        tick.set_fontweight("bold")

# P9 spread band (label placed below the last row to keep the top free)
ax2.axvspan(p9_lo, p9_hi, color=ROSE, alpha=0.07, zorder=0)
ax2.text((p9_lo + p9_hi) / 2, -0.58,
         "P9-Spannweite " + de(p9_lo, 0) + "–" + de(p9_hi, 0) + " %",
         fontsize=10.5, color=ROSE, ha="center", va="center", alpha=0.9)

for y, (_, r) in zip(ys2, right.iterrows()):
    ax2.plot([r["target_pct"], r["synthetic_pct"]], [y, y], color=CONN, lw=2.0,
             solid_capstyle="round", zorder=2)
    glow_dot(ax2, r["target_pct"], y, ROSE, s=170, marker="o", hollow=True)
    glow_dot(ax2, r["synthetic_pct"], y, CYAN, s=170, marker="o")
    ax2.text(r["target_pct"] - 1.1, y, de(r["target_pct"], 0) + " %", fontsize=10.5,
             color=ROSE, ha="right", va="center")
    ax2.text(r["synthetic_pct"] + 1.1, y, de(r["synthetic_pct"], 1) + " %", fontsize=10.5,
             color=CYAN, ha="left", va="center")
    ax2.text(72.8, y, "n=" + f"{int(r['n_unweighted']):,}".replace(",", "."),
             fontsize=10, color=INK_FAINT, ha="right", va="center")

# n column header
ax2.text(72.8, len(right) - 0.32, "MiD-Fälle\n(ungew.)", fontsize=9,
         color=INK_FAINT, ha="right", va="bottom", linespacing=1.3)

# Wolfsburg annotation -- placed in the free strip ABOVE the Wolfsburg row.
y_wob = ys2[list(right["ars5"]).index("03103")]
ax2.text(38.6, y_wob + 0.80,
         "trotz VW-Werk die niedrigste P9-Quote\n"
         "→ Stichproben-Artefakt (n=874)",
         fontsize=10.5, color=ROSE, ha="left", va="top", linespacing=1.35)

# legend (right)
rx = 0.55
legend_marker(ax2, rx, 0.137, CYAN, marker="o")
fig.text(rx + 0.014, 0.137, "Synthese (100-%-Lauf)", fontsize=10.5, color=INK, ha="left", va="center")
legend_marker(ax2, rx + 0.185, 0.137, ROSE, marker="o", hollow=True)
fig.text(rx + 0.199, 0.137, "MiD 2023 P9 (gewichtet)", fontsize=10.5, color=INK,
         ha="left", va="center")

fig.text(rx, 0.098,
         "Bewusste Entscheidung: Die Synthese wird nicht auf P9 gerakt — Raken auf nur\n"
         "874–1.902 Befragte je Kreis hieße, Stichprobenrauschen nachzubilden (Überanpassung).",
         fontsize=10.5, color=AMBER, ha="left", va="top", linespacing=1.5)

# ------------------------------------------------------------------ credit
fig.text(0.045, 0.028,
         "Quellen: Zensus 2022 (zensus2022_employment_by_age_ref.csv, eqasim-data) · MiD 2023 Tab. P9 (mid2023_P9.csv, n ungewichtet) · "
         "Lauf output_bs_100pct_allfeat_popsim (controls_long.csv; persons + homes, Kreiszuordnung punktgenau in EPSG:25832) · Stand 02.07.2026",
         fontsize=8, color=INK_FAINT, ha="left", va="bottom")

out = os.path.join(HERE, "fig_employment.png")
fig.savefig(out, dpi=170, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("saved", out)
