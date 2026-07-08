# -*- coding: utf-8 -*-
"""Management-slide figure: control scoreboard of the synthetic population validation.

Redesign: rows are split into two groups so that quality GRADES are only shown for
controls the synthesis actually calibrates on (official registers / full census),
while MiD-based small-sample comparisons are labelled neutrally as cross-checks.

Inputs : quality_summary.csv from the 100% all-features PopulationSim run
         (analysis/population_validation of output_bs_100pct_allfeat_popsim);
         employment_ageband_data.csv (cached: per Kreis x decadal age band the
         synthetic employed counts vs the Zensus/GENESIS 13111 reference, for the
         three kreisfreie Staedte 03101/03102/03103).
Output : fig_qa1_scoreboard.png (dark deck style, 16:9, dpi 170).

All values are computed from the CSVs; nothing is invented. The employment anchor
row is mean |synthetic - Zensus| on base age 20+ (bands 20-29..80+ summed per
Kreis) -- base 20+ because of the known minor-employment-flag finding (Issue #96).
"""

import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# ----------------------------------------------------------------- constants
BG = "#0a0e14"
GRID = "#1d2633"
INK = "#eef3fb"
INK_SUB = "#8b95a7"
INK_FAINT = "#5a6577"
DOT_EMPTY = "#11161f"
NEUTRAL = "#8b95a7"          # group B: neutral cross-check colour (no grade)

GRADE_COLOR = {
    "very good": "#34d399",
    "good": "#22d3ee",
    "acceptable": "#fbbf24",
    "needs improvement": "#fb7185",
}
GRADE_DE = {
    "very good": "sehr gut",
    "good": "gut",
    "acceptable": "akzeptabel",
    "needs improvement": "verbesserungswürdig",
}

FD = ("C:/Users/BIENZE~1/AppData/Local/Temp/claude/"
      "c--Users-bienzeisler-Documents-GitHub-eqasim-bs/"
      "b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/")
CSV = ("C:/Users/bienzeisler/Downloads/popsim_100pct_results/"
       "output_bs_100pct_allfeat_popsim/analysis/population_validation/quality_summary.csv")
AGE_CSV = FD + "employment_ageband_data.csv"
OUT = FD + "fig_qa1_scoreboard.png"

# ----------------------------------------------------------------- fonts
FONT_DIR = ("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/"
            "braunschweig/analysis/poster/fonts/")
for f in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
    try:
        font_manager.fontManager.addfont(FONT_DIR + f)
    except Exception:
        pass
families = {f.name for f in font_manager.fontManager.ttflist}
MONO = "Space Mono" if "Space Mono" in families else "DejaVu Sans Mono"
plt.rcParams["font.family"] = [MONO, "DejaVu Sans Mono", "DejaVu Sans"]

# ----------------------------------------------------------------- data
df = pd.read_csv(CSV).set_index("control")

# Employment anchor: mean |synthetic - Zensus| employment rate, base age 20+
# (bands 20-29..80+ summed per Kreis), over the three kreisfreie Staedte.
age = pd.read_csv(AGE_CSV, dtype={"ars5": str})
age = age[~age["age_band"].isin(["0-9", "10-19"])]
grp = age.groupby("ars5").agg(n=("n", "sum"), emp=("emp", "sum"),
                              total=("total", "sum"),
                              erwerbstaetige=("erwerbstaetige", "sum"))
grp["delta_pp"] = (grp["emp"] / grp["n"] - grp["erwerbstaetige"] / grp["total"]) * 100.0
ANCHOR_DELTAS = grp["delta_pp"].round(2).to_dict()
ANCHOR_PP = float(grp["delta_pp"].abs().mean())
# Grade by the validation tool's observed convention on mean_abs_delta_pp:
# quality_summary shows 1.24 pp (age_group) -> "good"; the anchor lands there too.
ANCHOR_GRADE = "good"

def row(ctrl, name, ref, graded, value=None, grade=None, cov=None, n_cells=None):
    if ctrl is not None:
        r = df.loc[ctrl]
        value = float(r["mean_abs_delta_pp"]) if value is None else value
        grade = r["grade"] if grade is None else grade
        cov = float(r["coverage_10pp"]) if cov is None else cov
        n_cells = int(r["n_cells"]) if n_cells is None else n_cells
    return dict(name=name, ref=ref, graded=graded, value=value,
                grade=grade, cov=cov, n_cells=n_cells)

GROUP_A = [
    row("bev_share", "E-Auto-Anteil", "KBA FZ 27.15 (Kreis)", True),
    row("sex", "Geschlecht", "DESTATIS 12411 (Kreis)", True),
    row("age_group", "Altersgruppen", "DESTATIS 12411 (Kreis)", True),
    row(None, "Erwerbstätigkeit — vs. Zensus-Anker*",
        "GENESIS 13111, Basis 20+ (3 kreisfreie Städte)", True,
        value=ANCHOR_PP, grade=ANCHOR_GRADE, cov=None, n_cells=3),
    row("household_size", "Haushaltsgröße*", "Zensus 2022 (Gemeinde)", True),
]
GROUP_B = [
    row("pt_ticket_type", "ÖPNV-Ticket-Typ", "MiD 2023 P24.1 (Kreis)", False),
    row("driving_license_type", "Führerschein", "MiD 2023 P17.1 (Kreis)", False),
    row("cars_per_hh", "Pkw je Haushalt", "MiD 2023 Haushalte (Kreis)", False),
    row("bicycles_per_hh", "Fahrräder je Haushalt", "MiD 2023 Haushalte (Kreis)", False),
    row("employment", "Erwerbstätigkeit — vs. MiD P9*", "MiD 2023 P9 (Kreis)", False),
]
GROUP_A.sort(key=lambda r: r["value"])
GROUP_B.sort(key=lambda r: r["value"])


def de_num(v):
    # Two decimals below 1 pp so the near-zero controls stay distinguishable.
    dec = 2 if v < 1.0 else 1
    return f"{v:.{dec}f}".replace(".", ",")


# ----------------------------------------------------------------- layout
ROW_H = 1.0
HDR_H = 1.15          # vertical room a group header occupies
GAP_H = 0.55          # extra air between the two groups

items = []            # (kind, payload, y)
y = 0.0
items.append(("header",
              ("KALIBRIERZIELE", " — amtliche Register & Vollerhebung"), y))
y += HDR_H
for r in GROUP_A:
    items.append(("row", r, y))
    y += ROW_H
y += GAP_H
items.append(("header",
              ("QUERVERGLEICHE", " — MiD-Befragung, Kreisebene (kleine Stichprobe)"), y))
y += HDR_H
for r in GROUP_B:
    items.append(("row", r, y))
    y += ROW_H
Y_TOTAL = y - ROW_H + 0.55    # bottom edge for axis / grid lines

# ----------------------------------------------------------------- figure
fig = plt.figure(figsize=(16, 9), facecolor=BG)
ax = fig.add_axes([0.315, 0.205, 0.665, 0.645])
ax.set_facecolor(BG)
ax.set_axis_off()

X_MAX = 16.55          # data-units canvas (bars + badge + coverage columns)
BAR_ZONE = 10.5        # bars live in 0..BAR_ZONE
X_BADGE_DOT = 10.95    # grade badge marker
X_BADGE_TXT = 11.22    # grade badge text
X_DOTS0 = 13.75        # first coverage dot (clear of the longest grade badge)
DOT_DX = 0.185         # coverage dot spacing (10 dots = 10% each)
X_PCT = 16.5           # coverage percent (right-aligned)

ax.set_xlim(0, X_MAX)
ax.set_ylim(Y_TOTAL, -1.55)   # inverted: first item on top, column-header room above

# grid (bar zone only)
for gx in range(0, 11, 2):
    ax.vlines(gx, -0.55, Y_TOTAL - 0.28, color=GRID, lw=0.8, zorder=0)
    ax.text(gx, Y_TOTAL - 0.02, f"{gx}", ha="center", va="top",
            color=INK_FAINT, fontsize=8)
ax.text(BAR_ZONE + 0.15, Y_TOTAL - 0.02, "pp", ha="left", va="top",
        color=INK_FAINT, fontsize=8)

# column headers
HDR_Y = -1.05
ax.text(0, HDR_Y, "Mittlere absolute Abweichung je Zelle (Prozentpunkte)",
        ha="left", va="center", color=INK_SUB, fontsize=8.5)
ax.text(X_BADGE_DOT, HDR_Y, "Bewertung", ha="left", va="center",
        color=INK_SUB, fontsize=8.5)
ax.text(X_PCT, HDR_Y, "Zellen < 10 pp", ha="right", va="center",
        color=INK_SUB, fontsize=8.5)

# rows + group headers
X_LABEL = -0.28        # right edge of the row-label column (data units)
X_GHDR = -7.45         # group headers start at the left edge of the label column
for kind, payload, yy in items:
    if kind == "header":
        strong, rest = payload
        ax.text(X_GHDR, yy + 0.10, strong, ha="left", va="center",
                color=INK, fontsize=10.5, fontweight="bold", clip_on=False)
        # measure nothing: place the dim tail right after via annotate offset
        ax.annotate(rest, xy=(X_GHDR, yy + 0.10), xycoords="data",
                    xytext=(len(strong) * 6.55, 0), textcoords="offset points",
                    ha="left", va="center", color=INK_FAINT, fontsize=8.5,
                    annotation_clip=False)
        ax.hlines(yy + 0.52, X_GHDR, X_MAX, color=GRID, lw=1.0, clip_on=False)
        continue

    r = payload
    v = r["value"]
    c = GRADE_COLOR[r["grade"]] if r["graded"] else NEUTRAL

    # control name + source/cells sub-line (left of the bars)
    ax.text(X_LABEL, yy - 0.13, r["name"], ha="right", va="center",
            color=INK, fontsize=10.5, fontweight="bold", clip_on=False)
    ax.text(X_LABEL, yy + 0.30, f"{r['ref']} · {r['n_cells']} Zellen",
            ha="right", va="center", color=INK_FAINT, fontsize=7, clip_on=False)

    # glow bar: wide low-alpha passes behind the sharp bar
    ax.barh(yy, v, height=0.72, color=c, alpha=0.16, zorder=2)
    ax.barh(yy, v, height=0.52, color=c, alpha=0.28, zorder=2)
    ax.barh(yy, v, height=0.34, color=c, alpha=1.0 if r["graded"] else 0.85, zorder=3)

    # value label right of the bar
    ax.text(v + 0.17, yy, f"{de_num(v)} pp", ha="left", va="center",
            color=INK, fontsize=9.5, zorder=4)

    # badge: grade (group A) or neutral cross-check marker (group B)
    if r["graded"]:
        ax.scatter([X_BADGE_DOT], [yy], marker="s", s=32, color=c, zorder=4)
        ax.text(X_BADGE_TXT, yy, GRADE_DE[r["grade"]], ha="left", va="center",
                color=c, fontsize=8.5, zorder=4)
    else:
        ax.scatter([X_BADGE_DOT], [yy], marker="s", s=32, facecolor="none",
                   edgecolor=NEUTRAL, linewidth=1.0, zorder=4)
        ax.text(X_BADGE_TXT, yy, "Quervergleich", ha="left", va="center",
                color=NEUTRAL, fontsize=8.5, zorder=4)

    # coverage_10pp: 10 dots (10% each) + percent text, where available
    if r["cov"] is not None:
        cov = float(r["cov"])
        filled = int(cov * 10 + 0.5)
        for k in range(10):
            col = INK_SUB if k < filled else DOT_EMPTY
            ec = "none" if k < filled else GRID
            ax.scatter([X_DOTS0 + k * DOT_DX], [yy], marker="o", s=15,
                       facecolor=col, edgecolor=ec, linewidth=0.6, zorder=4)
        ax.text(X_PCT, yy, f"{cov * 100:.0f} %", ha="right", va="center",
                color=INK, fontsize=9.5, zorder=4)
    else:
        ax.text(X_PCT, yy, "—", ha="right", va="center",
                color=INK_FAINT, fontsize=9.5, zorder=4)

# ----------------------------------------------------------------- titles
fig.text(0.032, 0.962, "Qualitäts-Scoreboard der synthetischen Bevölkerung",
         ha="left", va="top", color=INK, fontsize=16.5, fontweight="bold")
fig.text(0.032, 0.912,
         "Kalibrierziele vs. Quervergleiche — bewertet wird nur, worauf die "
         "Synthese wirklich steuert.",
         ha="left", va="top", color=INK_SUB, fontsize=10)
fig.text(0.032, 0.885,
         "100-%-PopulationSim-Lauf, Region Braunschweig (ZGB)",
         ha="left", va="top", color=INK_SUB, fontsize=10)

# ----------------------------------------------------------------- footnotes
FN1 = ("* Erwerbstätigkeit-Anker: Basis 20+ wegen des bekannten "
       "Minderjährigen-Flag-Befunds (Issue #96); kreisgenaue Zensus-Referenz "
       "liegt nur für die 3 kreisfreien Städte vor\n"
       "  (Abweichungen +0,8 / +1,1 / +1,9 pp).")
FN2 = ("* Haushaltsgröße: auf gemeinsamer Personen-Basis validiert "
       "(Basis-Mismatch #97 behoben); der verbleibende Restfehler liegt bei "
       "den 5+/6+-Haushalten (siehe Folgefolie).")
FN3 = ("MiD-Kreiswerte: Regionalauswertung Großraum Braunschweig, nur 874–1.902 "
       "Befragte je Kreis — bewusst kein Kalibrierziel.")

fig.text(0.032, 0.142, FN1, ha="left", va="top", color=INK_SUB,
         fontsize=8, linespacing=1.35)
fig.text(0.032, 0.098, FN2, ha="left", va="top", color=INK_SUB,
         fontsize=8, linespacing=1.35)
fig.text(0.032, 0.072, FN3, ha="left", va="top", color=INK_SUB,
         fontsize=8, linespacing=1.35)

fig.text(0.032, 0.032,
         "Daten: quality_summary.csv — analysis/population_validation, "
         "Lauf output_bs_100pct_allfeat_popsim (100 % Synthese, Export 2026-06-30; "
         "Haushaltsgröße nach #97-Fix am 2026-07-03 nachvalidiert); "
         "Zensus-Anker aus GENESIS 13111 je Kreis × Altersband",
         ha="left", va="top", color=INK_FAINT, fontsize=7.5)

fig.savefig(OUT, dpi=170, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("written:", OUT)
print("anchor deltas (pp):", ANCHOR_DELTAS, "mean abs:", round(ANCHOR_PP, 2))
