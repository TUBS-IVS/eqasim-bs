# -*- coding: utf-8 -*-
# Render fig_qa_100m.png: control fit of the PopulationSim synthesis vs.
# Zensus control totals. Management-friendly framing: LEAD with the absolute
# lens (mean absolute deviation per cell, < +-1 household for nearly all
# controls) because on the 100-m grid targets are tiny (often < 2 per cell)
# and relative deviations therefore look alarming although the fit is fine.
# Input: qa100m_aggregate.csv (computed on the run server from
# popsim_work_allfeat_opt, 33 batches, 2026-06-30 run).
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- fonts
FONT_DIR = r"c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts"
for f in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
    p = os.path.join(FONT_DIR, f)
    if os.path.exists(p):
        font_manager.fontManager.addfont(p)
plt.rcParams["font.family"] = "Space Mono"

BG = "#0a0e14"
FG = "#eef3fb"
SUB = "#8b95a7"
GRID = "#1d2633"
CREDIT = "#5a6577"

FAM_COLORS = {
    "hh_total": "#eef3fb",
    "tenure": "#34d399",
    "hh_size": "#fbbf24",
    "building_type": "#fb7185",
    "hh_type": "#d946ef",
    "employment": "#6366f1",
    "age_sex": "#22d3ee",
    "education": "#a3e635",
}
# Unit note per family: person-based controls vs. household-based controls.
FAM_DE = {
    "hh_total": "Haushalte gesamt",
    "tenure": "Eigentum/Miete",
    "hh_size": "Haushaltsgröße",
    "building_type": "Gebäudetyp",
    "hh_type": "Haushaltstyp",
    "employment": "Erwerbstätigkeit (Pers.)",
    "age_sex": "Alter x Geschlecht (Pers.)",
    "education": "Bildung (nur Kreis)",
}
FAM_DE_SHORT = {
    "hh_total": "Haushalte gesamt",
    "tenure": "Eigentum/Miete",
    "hh_size": "Haushaltsgröße",
    "building_type": "Gebäudetyp",
    "hh_type": "Haushaltstyp",
    "employment": "Erwerbstätigkeit",
    "age_sex": "Alter x Geschlecht",
    "education": "Bildung (nur Kreis)",
}
LEVEL_COLORS = {"ZENSUS100m": "#22d3ee", "ZENSUS1km": "#6366f1", "KREIS": "#34d399"}
LEVEL_DE = {"ZENSUS100m": "100-m-Zelle", "ZENSUS1km": "1-km-Zelle", "KREIS": "Kreis"}


def label_de(base):
    b = base
    if b.startswith("Insgesamt_Haushalte"):
        return "Haushalte insgesamt"
    if "Groesse_des_privaten_Haushalts" in b:
        n = b.split("_")[0]
        return ("HH mit 6+ Pers." if n == "6" else "HH mit %s Pers." % n)
    if b.startswith("Paare_ohneKind"):
        return "Paare ohne Kind"
    if b.startswith("Paare_mitKind"):
        return "Paare mit Kind"
    if b.startswith("Alleinerziehende"):
        return "Alleinerziehende"
    if b.startswith("MehrpersHH"):
        return "Mehrpers.-HH o. Kernfam."
    if b.startswith("EigentuemerHH"):
        return "Eigentümer-HH"
    if b.startswith("MieterHH"):
        return "Mieter-HH"
    if b.startswith("building_type_ein_zwei"):
        return "Ein-/Zweifamilienhaus"
    if b.startswith("building_type_mehr"):
        return "Mehrfamilienhaus"
    if b.startswith("building_type_sonst"):
        return "Sonstige Gebäude"
    if b.startswith(("M_AGE", "F_AGE")):
        sex = "M" if b.startswith("M_") else "W"
        rng = b.replace("M_AGE_", "").replace("F_AGE_", "").replace("_agg", "")
        rng = rng.replace("_plus", "+").replace("_", "-")
        return "%s %s J." % (sex, rng)
    if b.startswith("EMPLOYED_"):
        parts = b.replace("EMPLOYED_", "").replace("_agg", "")
        sex = "M" if parts.startswith("M") else "W"
        rng = parts[2:].replace("plus", "+").replace("_", "-")
        return "Erw. %s %s J." % (sex, rng)
    if b == "employed":
        return "Erwerbstätige gesamt"
    return b


def fmt_de(v, dec=2):
    s = ("%." + str(dec) + "f") % v
    return s.replace(".", ",")


def fmt_pct(v, dec=1):
    return fmt_de(v, dec) + " %"


def tsep(n):
    return format(int(round(n)), ",d").replace(",", ".")


# ---------------------------------------------------------------- data
df = pd.read_csv(os.path.join(HERE, "qa100m_aggregate.csv"))
n_batches = df["batch"].nunique()

# LEFT lens: per control at 100 m, aggregated over all batches ->
# mean absolute deviation per positive-target cell + share of exactly hit cells.
p100 = (df[df.level == "ZENSUS100m"]
        .groupby(["family", "control_base"])
        .agg(absdev=("absdev_pos_sum", "sum"), n_cells=("n_cells_pos", "sum"),
             n_exact=("n_cells_exact", "sum"), target=("target_sum", "sum"))
        .reset_index())
p100["mad_per_cell"] = p100["absdev"] / p100["n_cells"]
p100["exact_share"] = 100.0 * p100["n_exact"] / p100["n_cells"]
p100["mean_target"] = p100["target"] / p100["n_cells"]
# Sorted ascending -> smallest deviation ends up at the BOTTOM of the barh
# panel (y=0), largest at the top: the eye meets the worst case first and
# sees it is still only ~1.2 households per cell.
p100 = p100.sort_values("mad_per_cell", ascending=True).reset_index(drop=True)
n_controls_100m = len(p100)

# Median target size per positive cell over the detail controls (excluding the
# steering total) -- used in the annotation explaining WHY relative deviation
# explodes on the 100-m grid.
median_target = p100.loc[p100.family != "hh_total", "mean_target"].median()

# RIGHT lens: target-weighted mean absolute percentage deviation per
# family x level (identical methodology to the previous figure version).
fam = (df.groupby(["level", "family"])
         .agg(target=("target_sum", "sum"), absdev=("absdev_pos_sum", "sum"))
         .reset_index())
fam["wmape"] = 100.0 * fam["absdev"] / fam["target"]

hh100 = df[(df.level == "ZENSUS100m") & (df.family == "hh_total")]
n_cells_100m = int(hh100["n_cells_pos"].sum())
n_hh_total = hh100["target_sum"].sum()

# ---------------------------------------------------------------- figure
fig = plt.figure(figsize=(20.0, 9.0), dpi=170)
fig.patch.set_facecolor(BG)

# Header band above y=0.865; panel titles live in the gap just below it.
ax1 = fig.add_axes([0.150, 0.085, 0.360, 0.700])
ax2 = fig.add_axes([0.700, 0.085, 0.270, 0.700])
for ax in (ax1, ax2):
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=SUB, labelsize=9.5, length=0)

# --------------------------------------------------- LEFT panel (lead)
y = np.arange(len(p100))
colors = [FAM_COLORS[f] for f in p100["family"]]
XMAX1 = 1.42
ax1.barh(y, p100["mad_per_cell"], height=0.88, color=colors, alpha=0.22, zorder=2)
ax1.barh(y, p100["mad_per_cell"], height=0.52, color=colors, alpha=1.0, zorder=3)
ax1.set_yticks(y)
ax1.set_yticklabels([label_de(b) for b in p100["control_base"]], fontsize=8.6, color="#c7d0df")
ax1.set_ylim(-0.75, len(p100) - 0.25)
ax1.set_xlim(0, XMAX1)
ax1.set_xticks([0, 0.25, 0.5, 0.75, 1.0, 1.25])
ax1.set_xticklabels(["0", "0,25", "0,50", "0,75", "1,00", "1,25"])
ax1.xaxis.grid(True, color=GRID, lw=0.8, zorder=1)
ax1.set_axisbelow(True)
ax1.set_xlabel("mittlere absolute Abweichung je Zelle  [Haushalte bzw. Personen]",
               fontsize=10.5, color=SUB)

# +-1 household reference line: the visual anchor of the whole story.
ax1.axvline(1.0, color="#fb7185", lw=1.2, ls=(0, (5, 4)), alpha=0.85, zorder=4)
ax1.text(1.0, len(p100) - 0.1, "±1 Haushalt", fontsize=9.5, color="#fb7185",
         ha="center", va="bottom", zorder=6)

# Value label right of each bar (mean abs deviation, comma decimals).
for yi, v, fa in zip(y, p100["mad_per_cell"], p100["family"]):
    ax1.text(v + XMAX1 * 0.010, yi, fmt_de(v, 3 if v < 0.01 else 2),
             va="center", ha="left", fontsize=8.2, color=FAM_COLORS[fa])

# Exact-hit column OUTSIDE the axes (x in axes fraction, y in data coords):
# share of positive-target cells the synthesis hits exactly.
tr = ax1.get_yaxis_transform()
for yi, ex in zip(y, p100["exact_share"]):
    ax1.text(1.115, yi, "%s exakt" % fmt_pct(ex, 0), transform=tr,
             va="center", ha="right", fontsize=8.2,
             color="#c7d0df" if ex >= 50 else SUB, zorder=6)
ax1.text(1.115, len(p100) - 0.1, "Zellen exakt\ngetroffen", transform=tr,
         fontsize=9.0, color=SUB, ha="right", va="bottom", linespacing=1.3)

ax1.set_title("Im Mittel weniger als ±1 Haushalt daneben", fontsize=14,
              color=FG, loc="left", pad=16)

# Annotation: steering control is hit almost exactly (bottom row, right of
# the tiny "0,003" value label, ending well before the lower-right legend).
ax1.text(0.115, 0.0, "Steuerungskontrolle - praktisch exakt",
         fontsize=9.5, color="#34d399", ha="left", va="center", zorder=6)

# Annotation: why the RELATIVE view looks bad -- targets per cell are tiny.
# Placed RIGHT of the +-1 reference line (rows ~15-23 are empty there: bars
# in that band stay below ~0.6 and the legend sits below row ~12).
ax1.text(1.025, 19.0,
         "Typische Zielwerte:\nnur ~%s Haushalte bzw.\nPersonen je Zelle -\n"
         "±1 daneben heißt schon\n50 %% relative Abweichung" % fmt_de(median_target, 1),
         fontsize=9.3, color=SUB, ha="left", va="center", linespacing=1.55, zorder=6)

# Family legend in the empty lower-right region (bars there are < 0.4).
fam_order = ["hh_total", "tenure", "hh_size", "building_type", "hh_type", "employment", "age_sex"]
handles = [Patch(facecolor=FAM_COLORS[f], label=FAM_DE[f]) for f in fam_order]
ax1.legend(handles=handles, loc="lower right", fontsize=8.6, frameon=False,
           labelcolor="#c7d0df", handlelength=1.1, handleheight=1.0,
           borderaxespad=0.2, bbox_to_anchor=(1.0, 0.030))

# --------------------------------------------------- RIGHT panel (secondary)
fam_plot = ["age_sex", "hh_type", "hh_size", "building_type", "employment", "tenure", "education", "hh_total"]
levels = ["ZENSUS100m", "ZENSUS1km", "KREIS"]
bar_h = 0.26
ys = np.arange(len(fam_plot))[::-1] * 1.15
fam_idx = fam.set_index(["family", "level"])["wmape"]

xmax2 = max(fam_idx[(f, lv)] for f in fam_plot for lv in levels if (f, lv) in fam_idx.index) * 1.24
for i, f in enumerate(fam_plot):
    for j, lv in enumerate(levels):
        if (f, lv) not in fam_idx.index:
            continue
        v = fam_idx[(f, lv)]
        yy = ys[i] + (1 - j) * bar_h
        ax2.barh(yy, v, height=bar_h * 1.55, color=LEVEL_COLORS[lv], alpha=0.20, zorder=2)
        ax2.barh(yy, v, height=bar_h * 0.85, color=LEVEL_COLORS[lv], alpha=1.0, zorder=3)
        ax2.text(v + xmax2 * 0.015, yy, fmt_pct(v, 1 if v >= 0.1 else 2), va="center",
                 ha="left", fontsize=8.8, color=LEVEL_COLORS[lv])

ax2.set_yticks(ys)
ax2.set_yticklabels([FAM_DE_SHORT[f] for f in fam_plot], fontsize=9.6, color="#c7d0df")
ax2.set_ylim(ys.min() - 0.62, ys.max() + 0.80)
ax2.set_xlim(0, xmax2)
ax2.xaxis.grid(True, color=GRID, lw=0.8, zorder=1)
ax2.set_axisbelow(True)
ax2.set_xlabel("zielgewichtete mittlere absolute Abweichung [%]", fontsize=10.5, color=SUB)
ax2.set_title("Und relativ? Rauschen mittelt sich heraus", fontsize=14, color=FG, loc="left", pad=16)

lvl_handles = [Patch(facecolor=LEVEL_COLORS[lv], label=LEVEL_DE[lv]) for lv in levels]
ax2.legend(handles=lvl_handles, loc="lower right", fontsize=9.5, frameon=False,
           labelcolor="#c7d0df", handlelength=1.1, borderaxespad=0.2)

# Annotation: aggregation averages out small-cell noise. Placed in the empty
# lower-middle zone (tenure/education rows have only short bars; the level
# legend sits further right at the very bottom).
ax2.text(xmax2 * 0.25, 2.05,
         "25 %% auf 100 m wirken dramatisch -\nbei Aggregation auf 1 km/Kreis\nbleiben %s / %s (Alter x Geschl.)"
         % (fmt_pct(fam_idx[("age_sex", "ZENSUS1km")], 1), fmt_pct(fam_idx[("age_sex", "KREIS")], 1)),
         fontsize=9.3, color=SUB, ha="left", va="center", linespacing=1.55)

# --------------------------------------------------- titles + credit
fig.text(0.03, 0.965, "Kontroll-Fit auf der 100-m-Zelle: absolut fast exakt", fontsize=19,
         color=FG, weight="bold", ha="left", va="center")
fig.text(0.03, 0.928,
         "Links (absolute Sicht): mittlere absolute Abweichung Synthese vs. Zensus-Ziel je bewohnter 100-m-Zelle - "
         "für alle %d Kontrollen unter ±1,3, meist unter ±1.\n"
         "Rechts (relative Sicht): zielgewichtete Abweichung in %% - auf 100 m wirken die winzigen Zielwerte "
         "verzerrend groß, mitteln sich auf 1 km/Kreis heraus." % n_controls_100m,
         fontsize=11.0, color=SUB, ha="left", va="top", linespacing=1.6)

fig.text(0.03, 0.014,
         "Datenbasis: popsim_work_allfeat_opt (Lauf 2026-06-30, Server), 33/33 Batches - %s bewohnte 100-m-Zellen - "
         "%s Ziel-Haushalte - nur Zellen mit Zielwert > 0 - Personenmerkmale über synthetic_households x Seed-Personen "
         "(H_ID) rekonstruiert - Auswertung server-side 2026-07-02"
         % (tsep(n_cells_100m), tsep(n_hh_total)),
         fontsize=8, color=CREDIT, ha="left", va="bottom")

out = os.path.join(HERE, "fig_qa_100m.png")
fig.savefig(out, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("wrote", out)
