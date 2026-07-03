"""Employment figure, unified base: ONE synthetic 20+ rate per Kreis shown in both panels.

Left  = anchor: synthetic 20+ vs Zensus 2022 20+ (3 kreisfreie Staedte, clean Kreis reference).
Right = cross-check: the SAME synthetic 20+ rate vs MiD 2023 P9 (base 14+), all 8 Kreise.
All values recomputed from employment_ageband_data.csv + employment_panel_data.csv.
"""
from __future__ import annotations
import pathlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patheffects as pe
import pandas as pd
import numpy as np

FD = pathlib.Path(__file__).resolve().parent
REPO = pathlib.Path("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs")
FONT_DIR = REPO / "braunschweig" / "analysis" / "poster" / "fonts"
MONO = "DejaVu Sans Mono"
for ttf in sorted(FONT_DIR.glob("SpaceMono-*.ttf")):
    fm.fontManager.addfont(str(ttf))
    MONO = fm.FontProperties(fname=str(ttf)).get_name()
plt.rcParams["font.family"] = MONO

BG = "#0a0e14"; TXT = "#eef3fb"; DIM = "#8b95a7"; CRED = "#5a6577"
CYAN = "#22d3ee"; GOLD = "#fbbf24"; ROSE = "#fb7185"; GREEN = "#34d399"; GRID = "#1d2633"

ab = pd.read_csv(FD / "employment_ageband_data.csv")
panel = pd.read_csv(FD / "employment_panel_data.csv")

# 20+ bands (clean cut; ageband labels are decadal 0-9,10-19,20-29,...)
BANDS_20 = ["20-29", "30-39", "40-49", "50-59", "60-69", "70-79", "80+"]
ab20 = ab[ab["age_band"].isin(BANDS_20)].copy()
# Zensus 20+ anchor only exists cleanly for the 3 kreisfreie Staedte (ageband CSV scope).
zen20 = (ab20.groupby("ars5")["erwerbstaetige"].sum() / ab20.groupby("ars5")["total"].sum() * 100).rename("zen20")

# Synthetic 20+ rate for ALL 8 Kreise from the PIP-join (syn20_by_kreis.csv, ars5 zero-padded).
s20 = pd.read_csv(FD / "syn20_by_kreis.csv")
s20["ars5"] = s20["ars5"].astype(str).str.zfill(5).str.lstrip("0").astype(int)
s20 = s20.set_index("ars5")["syn20"]

panel = panel.set_index("ars5")
panel["syn20"] = s20
panel["zen20"] = zen20
NAMES = {3101: "Braunschweig", 3103: "Wolfsburg", 3102: "Salzgitter", 3151: "LK Gifhorn",
         3157: "LK Peine", 3154: "LK Helmstedt", 3158: "LK Wolfenbuettel", 3153: "LK Goslar"}
panel["name"] = [NAMES[i] for i in panel.index]

CITIES = [3101, 3103, 3102]  # kreisfreie Staedte with clean Zensus Kreis reference

fig = plt.figure(figsize=(18, 8.6), dpi=170)
fig.patch.set_facecolor(BG)
gs = fig.add_gridspec(1, 2, left=0.005, right=0.995, top=0.76, bottom=0.10, wspace=0.30)

fig.text(0.055, 0.955, "Erwerbstaetigkeit: an der amtlichen Statistik verankert",
         color=TXT, fontsize=19, fontweight="bold")
fig.text(0.055, 0.908, "Dieselbe Synthese-Quote je Kreis (cyan, Basis 20+) — zweimal verglichen: "
         "links mit dem amtlichen Kalibrierziel, rechts mit der MiD-Stichprobe.",
         color=DIM, fontsize=11.5)

# ---------------- LEFT: anchor ----------------
axL = fig.add_subplot(gs[0, 0]); axL.set_facecolor(BG)
axL.text(0.0, 1.15, "1 · Gegen das Kalibrierziel: Zensus/GENESIS 2022", transform=axL.transAxes,
         color=GOLD, fontsize=14, fontweight="bold")
axL.text(0.0, 1.08, "kreisfreie Staedte (kreisgenaue amtliche Referenz) — passt auf 0,8–1,9 pp",
         transform=axL.transAxes, color=DIM, fontsize=10.5)

cdf = panel.loc[CITIES].sort_values("syn20")
yl = np.arange(len(cdf))
for y, (_, r) in zip(yl, cdf.iterrows()):
    axL.plot([r.zen20, r.syn20], [y, y], color="#3a4a63", lw=2.2, zorder=1)
    axL.scatter(r.zen20, y, marker="D", s=340, color=GOLD, edgecolor=BG, lw=1.5, zorder=3)
    axL.scatter(r.syn20, y, s=300, color=CYAN, edgecolor=BG, lw=1.5, zorder=3)
    axL.text(r.syn20, y + 0.26, f"{r.syn20:.1f} %".replace(".", ","), color=CYAN,
             fontsize=11.5, ha="center", va="bottom", fontweight="bold")
    axL.text(r.zen20, y - 0.28, f"{r.zen20:.1f} %".replace(".", ","), color=GOLD,
             fontsize=11.5, ha="center", va="top")
    d = r.syn20 - r.zen20
    axL.text(70.5, y, f"+{d:.1f} pp".replace(".", ","), color=GREEN, fontsize=12,
             ha="right", va="center", fontweight="bold")
axL.text(70.5, len(cdf) - 0.5 + 0.55, "Abweichung", color=DIM, fontsize=9.5, ha="right", va="bottom")
axL.set_yticks(yl); axL.set_yticklabels(cdf["name"], color=TXT, fontsize=13)
axL.set_xlim(44, 71); axL.set_ylim(-0.7, len(cdf) - 0.3)
axL.set_xticks(range(45, 71, 5)); axL.set_xticklabels([f"{v} %" for v in range(45, 71, 5)], color=DIM, fontsize=10.5)
axL.grid(axis="x", color=GRID, lw=0.8); axL.set_axisbelow(True)
for s in axL.spines.values(): s.set_visible(False)
axL.tick_params(length=0)
axL.scatter([], [], s=140, color=CYAN, edgecolor=BG, label="Synthese (100-%-Lauf, 20+)")
axL.scatter([], [], marker="D", s=160, color=GOLD, edgecolor=BG, label="Zensus 2022 (amtlicher Anker)")
axL.legend(loc="lower right", frameon=False, labelcolor=TXT, fontsize=10.5, bbox_to_anchor=(1.0, -0.02))
axL.text(0.0, -0.14, "Die Synthese verankert die Erwerbstaetigkeit je 100-m-Zelle nach Alter und\n"
         "Geschlecht an den amtlichen Zensus-Niveaus — Abweichung 0,8–1,9 Prozentpunkte.\n"
         "Kreisgenaue Zensus-Referenz nur fuer die drei kreisfreien Staedte verfuegbar.",
         transform=axL.transAxes, color=CRED, fontsize=9, va="top")

# ---------------- RIGHT: P9 cross-check ----------------
axR = fig.add_subplot(gs[0, 1]); axR.set_facecolor(BG)
axR.text(0.0, 1.15, "2 · Gegen die MiD-Stichprobe: P9 je Kreis", transform=axR.transAxes,
         color=ROSE, fontsize=14, fontweight="bold")
axR.text(0.0, 1.08, "alle 8 Kreise — die P9-Werte streuen stark (kleine Stichprobe, Basis 14+)",
         transform=axR.transAxes, color=DIM, fontsize=10.5)

rdf = panel.sort_values("p9_employed_pct")
yr = np.arange(len(rdf))
# P9 spread band
axR.axvspan(rdf["p9_employed_pct"].min(), rdf["p9_employed_pct"].max(), color=ROSE, alpha=0.06, zorder=0)
for y, (idx, r) in zip(yr, rdf.iterrows()):
    axR.plot([r.p9_employed_pct, r.syn20], [y, y], color="#3a4a63", lw=1.8, zorder=1)
    axR.scatter(r.p9_employed_pct, y, s=230, facecolor="none", edgecolor=ROSE, lw=2.2, zorder=3)
    axR.scatter(r.syn20, y, s=230, color=CYAN, edgecolor=BG, lw=1.2, zorder=3)
    axR.text(r.p9_employed_pct - 1.0, y, f"{r.p9_employed_pct:.0f} %", color=ROSE, fontsize=10.5,
             ha="right", va="center")
    axR.text(r.syn20 + 1.0, y, f"{r.syn20:.1f} %".replace(".", ","), color=CYAN, fontsize=10.5,
             ha="left", va="center", fontweight="bold")
    axR.text(69.5, y, f"n={int(r.n_unweighted):,}".replace(",", "."), color=CRED, fontsize=9.5,
             ha="left", va="center")
axR.text(69.5, len(rdf) - 1 + 0.05, "MiD-Faelle\n(ungew.)", color=DIM, fontsize=8.5, ha="left", va="bottom")
# Wolfsburg note: sits directly on the (highlighted) WOB row - no arrow across the panel.
wy = list(rdf.index).index(3103)
axR.text(61.6, wy, "trotz VW-Werk die\nniedrigste P9-Quote →\nStichproben-Artefakt",
         color=ROSE, fontsize=8.6, ha="left", va="center", linespacing=1.35)
axR.set_yticks(yr)
axR.set_yticklabels([("Wolfsburg" if n == "Wolfsburg" else n) for n in rdf["name"]],
                    color=TXT, fontsize=12)
for t, n in zip(axR.get_yticklabels(), rdf["name"]):
    if n == "Wolfsburg":
        t.set_color(ROSE); t.set_fontweight("bold")
axR.set_xlim(40, 74); axR.set_ylim(-0.7, len(rdf) - 0.3)
axR.set_xticks(range(40, 71, 5)); axR.set_xticklabels([f"{v} %" for v in range(40, 71, 5)], color=DIM, fontsize=10.5)
axR.grid(axis="x", color=GRID, lw=0.8); axR.set_axisbelow(True)
for s in axR.spines.values(): s.set_visible(False)
axR.tick_params(length=0)
axR.scatter([], [], s=150, color=CYAN, edgecolor=BG, label="Synthese (100-%-Lauf, 20+)")
axR.scatter([], [], s=150, facecolor="none", edgecolor=ROSE, lw=2.0, label="MiD 2023 P9 (14+, gewichtet)")
axR.legend(loc="lower right", frameon=False, labelcolor=TXT, fontsize=10.5, bbox_to_anchor=(1.0, -0.02))
axR.text(0.0, -0.14,
         "MiD P9 bezieht sich auf Personen ab 14 Jahren; die Synthese-Quote ab 20 liegt konstruktions-\n"
         "bedingt einige pp hoeher — der Punkt ist nicht das Niveau, sondern die starke Streuung der\n"
         "P9-Kreiswerte (43–59 %) bei kleiner Stichprobe. Bewusst wird NICHT auf P9 gerakt (Ueberanpassung).",
         transform=axR.transAxes, color=CRED, fontsize=9, va="top")

fig.text(0.055, 0.02,
         "Quellen: Zensus 2022 (zensus2022_employment_by_age_ref.csv) · MiD 2023 Tab. P9 (n ungewichtet) · "
         "100-%-Lauf output_bs_100pct_allfeat_popsim (persons.csv, punktgenaue Kreiszuordnung EPSG:25832) · Stand 07/2026",
         color=CRED, fontsize=8)

out = FD / "fig_employment.png"
fig.savefig(out, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("wrote", out)
print("syn20 per Kreis:")
for i, r in panel.iterrows():
    print(f"  {r['name']:20s} syn20={r.syn20:.1f}  p9={r.p9_employed_pct:.0f}  n={int(r.n_unweighted)}")
print("anchor deltas (cities):")
for i in CITIES:
    r = panel.loc[i]
    print(f"  {r['name']:14s} syn20={r.syn20:.1f} zen20={r.zen20:.1f} d=+{r.syn20-r.zen20:.1f}")
