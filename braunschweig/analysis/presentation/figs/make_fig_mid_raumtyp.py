# Deck figure: MiD raumtyp reference vs official report (Abb. 22) + Kreis-level noise.
# Indicator: share of households without a car (kein Auto).
# REAL data only:
#  - eqasim-data/data/braunschweig/mid/mid2023_cars_by_raumtyp.csv (committed table)
#  - MiD 2023 Ergebnisbericht Abb. 22, p. 82 (values re-read from the PDF text layer)
#  - eqasim-data/data/braunschweig/mid/mid2023_H7_cars_by_kreis.csv (Kreis targets)
#  - popsim 100pct controls_long.csv (synthetic realised)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import pandas as pd
import numpy as np

FD = r"C:/Users/BIENZE~1/AppData/Local/Temp/claude/c--Users-bienzeisler-Documents-GitHub-eqasim-bs/b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs"
REPO = r"c:/Users/bienzeisler/Documents/GitHub/eqasim-bs"

for f in ["SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"]:
    fm.fontManager.addfont(rf"{REPO}/braunschweig/analysis/poster/fonts/{f}")
# DejaVu Sans as fallback for glyphs Space Mono lacks (Delta, geometric markers)
plt.rcParams["font.family"] = ["Space Mono", "DejaVu Sans"]

BG = "#0a0e14"
CYAN = "#22d3ee"
AMBER = "#fbbf24"
ROSE = "#fb7185"
GREEN = "#34d399"
GREY = "#8b98ab"
DIM = "#4a5670"
TXT = "#dbe4f0"
GRID = "#1a2332"

# ---------------- data: our committed raumtyp table ----------------
rt = pd.read_csv(rf"{REPO}/eqasim-data/data/braunschweig/mid/mid2023_cars_by_raumtyp.csv", comment="#")
piv = rt.pivot(index="region", columns="num_cars", values="base_weighted")
ours = (piv[0] / piv.sum(axis=1) * 100.0)

# report order (Abb. 22): Stadtregion first, then laendliche Region
order = [
    ("stadtregion_metropole",            "Metropole",                     40),
    ("stadtregion_regiopole_grossstadt", "Regiopole und Großstadt",  27),
    ("stadtregion_mittelstadt",          "Mittelstadt, städt. Raum", 13),
    ("stadtregion_kleinstaedtisch",      "kleinstädt., dörfl. Raum", 8),
    ("laendlich_zentrale_stadt",         "zentrale Stadt",                21),
    ("laendlich_mittelstadt",            "Mittelstadt, städt. Raum", 13),
    ("laendlich_kleinstaedtisch",        "kleinstädt., dörfl. Raum", 9),
]
labels = [o[1] for o in order]
ours_v = np.array([ours[o[0]] for o in order])
rep_v = np.array([float(o[2]) for o in order])
delta = ours_v - rep_v

# ---------------- data: Kreis targets + synthetic realised ----------------
h7 = pd.read_csv(rf"{REPO}/eqasim-data/data/braunschweig/mid/mid2023_H7_cars_by_kreis.csv", comment="#", dtype={"ars5": str})
kt = h7.set_index("ars5")["0"] * 100.0
cl = pd.read_csv(r"C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim/analysis/population_validation/controls_long.csv")
syn = cl[(cl["control"] == "cars_per_hh") & (cl["geography"] == "kreis") & (cl["category"].astype(str) == "0")].set_index("geo_id")["synthetic_pct"]

cities = [("Braunschweig", "03101", 3101), ("Salzgitter", "03102", 3102), ("Wolfsburg", "03103", 3103)]

# ---------------- figure ----------------
fig = plt.figure(figsize=(1920 / 170, 1080 / 170), dpi=170)
fig.patch.set_facecolor(BG)

fig.text(0.045, 0.955, "MiD als Referenz: Raumtyp sitzt, Kreis rauscht",
         fontsize=18, fontweight="bold", color="white", ha="left", va="top")
fig.text(0.045, 0.895, "Indikator: Anteil Haushalte ohne Pkw  ·  unsere MiD-Referenztabellen vs. offizieller MiD-2023-Ergebnisbericht",
         fontsize=9.5, color=GREY, ha="left", va="top")

axL = fig.add_axes([0.185, 0.15, 0.295, 0.60])
axR = fig.add_axes([0.635, 0.15, 0.32, 0.60])
for ax in (axL, axR):
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_visible(False)

# ============== LEFT: raumtyp paired bars ==============
# manual y positions with a gap + section headers between Stadtregion / laendlich
ys = np.array([7.5, 6.5, 5.5, 4.5, 2.7, 1.7, 0.7])
bh = 0.30

for gw, ga in [(bh * 2.3, 0.05), (bh * 1.75, 0.09), (bh * 1.3, 0.14)]:
    axL.barh(ys + 0.19, ours_v, height=gw, color=CYAN, alpha=ga, zorder=2)
axL.barh(ys + 0.19, ours_v, height=bh, color=CYAN, alpha=0.95, zorder=3)
axL.barh(ys - 0.19, rep_v, height=bh, facecolor="none", edgecolor=GREY, linewidth=1.3, zorder=3)

for yy, ov, rv, dl in zip(ys, ours_v, rep_v, delta):
    axL.text(ov + 0.8, yy + 0.19, f"{ov:.1f}".replace(".", ","), va="center", ha="left", fontsize=8.5, color=CYAN)
    axL.text(rv + 0.8, yy - 0.19, f"{rv:.0f}", va="center", ha="left", fontsize=8.5, color=GREY)
    # delta column in the gap between the two panels (clip off)
    axL.text(47.5, yy, "Δ " + f"{dl:+.1f}".replace(".", ",") + " pp", va="center", ha="left",
             fontsize=8, color=GREEN, clip_on=False)

axL.set_yticks(ys)
axL.set_yticklabels(labels, fontsize=8.6, color=TXT)
axL.tick_params(axis="y", colors=TXT, length=0, pad=8)
# section headers
axL.text(0, 8.35, "STADTREGION", fontsize=7.5, color=DIM, ha="left", va="center")
axL.text(0, 3.55, "LÄNDLICHE REGION", fontsize=7.5, color=DIM, ha="left", va="center")

axL.set_xlim(0, 47)
axL.set_ylim(-0.1, 8.75)
axL.set_xticks([0, 10, 20, 30, 40])
axL.tick_params(axis="x", colors=GREY, labelsize=8, length=0)
axL.grid(axis="x", color=GRID, lw=0.7, zorder=0)
axL.set_title("Raumtyp-Ebene: Tabelle = offizieller Bericht",
              fontsize=10.5, color="white", fontweight="bold", loc="left", pad=26)
axL.text(0, 1.035, "alle Abweichungen ≤ 0,5 pp — im Rundungsbereich des Berichts",
         transform=axL.transAxes, fontsize=8, color=GREEN, ha="left", va="bottom")

# legend left (below panel)
fig.text(0.188, 0.078, "■", color=CYAN, fontsize=10, ha="left")
fig.text(0.203, 0.078, "unsere MiD-Tabelle", color=TXT, fontsize=8, ha="left")
fig.text(0.330, 0.078, "□", color=GREY, fontsize=10, ha="left")
fig.text(0.345, 0.078, "Ergebnisbericht Abb. 22", color=GREY, fontsize=8, ha="left")

# ============== RIGHT: Kreis outlier dot plot ==============
yc = np.array([2.45, 1.45, 0.45])
axR.set_xlim(0, 45)
axR.set_ylim(-1.9, 3.45)

# reference lines (stop above the annotation block at the bottom)
ref_ymin = (-0.15 - (-1.9)) / (3.45 - (-1.9))
for gw, ga in [(5, 0.05), (3, 0.09)]:
    axR.axvline(27, color=AMBER, lw=gw, alpha=ga, zorder=1, ymin=ref_ymin)
axR.axvline(27, color=AMBER, lw=1.4, alpha=0.9, zorder=2, ymin=ref_ymin)
axR.text(26.0, 3.40, "Raumtyp 72 — Bericht: 27 %", color=AMBER, fontsize=7.5, ha="right", va="top")
axR.axvline(8.5, color=DIM, lw=1.1, ls=(0, (4, 3)), zorder=1, ymin=ref_ymin)
axR.text(9.5, 3.06, "ländlichste Klassen: 8–9 %", color=GREY, fontsize=7.5, ha="left", va="top", alpha=0.9)

axR.set_yticks(yc)
axR.set_yticklabels([c[0] for c in cities], fontsize=9.5, color=TXT, fontweight="bold")
axR.tick_params(axis="y", length=0, pad=8)

for (name, ars, gid), yy in zip(cities, yc):
    t = float(kt[ars])
    s = float(syn[gid])
    axR.plot([t, s], [yy, yy], color="#26334a", lw=1.6, zorder=2)
    for gs, ga in [(430, 0.06), (250, 0.10)]:
        axR.scatter([t], [yy], s=gs, color=ROSE, alpha=ga, zorder=3)
    axR.scatter([t], [yy], s=110, facecolor=BG, edgecolor=ROSE, linewidth=1.8, zorder=4)
    for gs, ga in [(430, 0.07), (250, 0.12)]:
        axR.scatter([s], [yy], s=gs, color=CYAN, alpha=ga, zorder=3)
    axR.scatter([s], [yy], s=95, color=CYAN, zorder=4)
    axR.text(t, yy - 0.30, f"{t:.0f} %", color=ROSE, fontsize=8.5, ha="center", va="top")
    axR.text(s, yy - 0.30, f"{s:.1f} %".replace(".", ","), color=CYAN, fontsize=8.5, ha="center", va="top")

# Salzgitter annotation (bottom free area, arrow up to the SZ target dot)
axR.annotate(
    "Salzgitter: Kreis-Target 10 % — 17 pp unter dem eigenen\n"
    "Raumtyp, praktisch auf dem Niveau des ländlichsten\n"
    "Deutschlands (8–9 %) — für eine Großstadt kaum plausibel;\n"
    "die Synthese (30,9 %) liegt näher am Berichtswert",
    xy=(float(kt["03102"]) - 1.0, yc[1] - 0.10), xytext=(1.5, -0.85),
    fontsize=7.7, color=ROSE, linespacing=1.55, va="top",
    arrowprops=dict(arrowstyle="-", color=ROSE, lw=0.9, alpha=0.7,
                    connectionstyle="arc3,rad=-0.22", relpos=(0.05, 1.0)),
)

axR.set_xticks([0, 10, 20, 30, 40])
axR.tick_params(axis="x", colors=GREY, labelsize=8, length=0)
axR.grid(axis="x", color=GRID, lw=0.7, zorder=0)
axR.set_title("Kreis-Ebene: der Ausreißer (alle Raumtyp 72)",
              fontsize=10.5, color="white", fontweight="bold", loc="left", pad=26)
axR.text(0, 1.035, "MiD-Kreiswerte: kleine Teilstichproben → hohe Unsicherheit",
         transform=axR.transAxes, fontsize=8, color=GREY, ha="left", va="bottom")

# legend right (below panel)
fig.text(0.638, 0.078, "○", color=ROSE, fontsize=10, ha="left")
fig.text(0.653, 0.078, "MiD-Kreis-Target (H7)", color=TXT, fontsize=8, ha="left")
fig.text(0.790, 0.078, "●", color=CYAN, fontsize=10, ha="left")
fig.text(0.805, 0.078, "Synthese realisiert (100 %)", color=TXT, fontsize=8, ha="left")

# credit line
fig.text(0.045, 0.032,
         "Quellen: MiD 2023 Ergebnisbericht, Abb. 22, S. 82 (BMDV/infas)  ·  eqasim-data/mid: mid2023_cars_by_raumtyp.csv, "
         "mid2023_H7_cars_by_kreis.csv  ·  Synthese: controls_long.csv (100 %)",
         fontsize=6.8, color=DIM, ha="left")

out = rf"{FD}/fig_mid_raumtyp.png"
fig.savefig(out, dpi=170, facecolor=BG)
print("saved", out)
print("ours:", dict(zip([o[0] for o in order], ours_v.round(2))))
print("deltas:", delta.round(2))
print("kreis targets:", {a: round(float(kt[a]), 1) for _, a, _ in cities})
print("synthetic:", {g: round(float(syn[g]), 1) for _, _, g in cities})
