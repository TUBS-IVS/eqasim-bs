# Figure: "Emissionsbereit: die Flotte im Detail" (3 panels)
# (a) normalized stacked euro-class shares per powertrain (petrol, diesel,
#     hybrid, PHEV) + BEV as a single "no emission standard" bar.
# (b) normalized stacked age-band shares per powertrain, with per-row median-age
#     annotation + amber model-update note (age model rework, Issue #92).
# (c) annotated heatmap: mean vehicle age by household economic_status x segment
#     (selected segments + all-segments row); old = bright/warm.
# Data: attributed fleet (mode=="car" & brand notna) from the 100% all-features
#   PopulationSim export (2026-06-30).
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle, FancyBboxPatch
import pandas as pd
import numpy as np
import os

# ---------------------------------------------------------------- fonts
FONT_DIR = r"c:\Users\bienzeisler\Documents\GitHub\eqasim-bs\braunschweig\analysis\poster\fonts"
for f in ["SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"]:
    p = os.path.join(FONT_DIR, f)
    if os.path.exists(p):
        fm.fontManager.addfont(p)
if any("Space Mono" in f.name for f in fm.fontManager.ttflist):
    plt.rcParams["font.family"] = "Space Mono"
else:
    plt.rcParams["font.family"] = "DejaVu Sans Mono"

BG = "#0a0e14"
FG = "#eef3fb"
SUB = "#8b95a7"
DIM = "#5a6577"
GRID = "#1d2633"
AMBER = "#fbbf24"
ROSE = "#fb7185"

# ---------------------------------------------------------------- data
CSV = ("C:/Users/bienzeisler/Downloads/popsim_100pct_results/"
       "output_bs_100pct_allfeat_popsim/braunschweig_100pct_allfeat_popsim_vehicles.csv")
df = pd.read_csv(CSV, sep=";", low_memory=False)
fleet = df[(df["mode"] == "car") & df["brand"].notna()].copy()
n_fleet = len(fleet)
hbefa_share = fleet["hbefa_emission"].notna().mean() * 100.0
n_hbefa = int(fleet["hbefa_emission"].notna().sum())


def de(n):
    """German thousands separator."""
    return f"{n:,}".replace(",", ".")


def de_pct(x, digits=1):
    return f"{x:.{digits}f}".replace(".", ",") + " %"


def de_years(x, digits=1):
    return f"{x:.{digits}f}".replace(".", ",") + " J."


# ----- panel (a): euro-class shares --------------------------------------
# Verified against hbefa_emission: 'other' for petrol/diesel == 'PC ... Euro-0'.
EURO_ORDER = ["other", "euro1", "euro2", "euro3", "euro4", "euro5", "euro6"]
EURO_LABELS = ["Euro 0", "Euro 1", "Euro 2", "Euro 3", "Euro 4", "Euro 5", "Euro 6"]
EURO_COLORS = ["#6b2140", "#a03a5e", "#e25c7f", "#f97316", "#fbbf24", "#a3e635", "#34d399"]

PT_ROWS = [("petrol", "Benzin"), ("diesel", "Diesel"),
           ("hybrid", "Hybrid"), ("phev", "Plug-in-Hybrid")]

euro_shares = {}
euro_ns = {}
n_dropped_no_euro = 0
for pt, _ in PT_ROWS:
    sub = fleet[fleet["powertrain"] == pt]
    if pt in ("hybrid", "phev"):
        # 'other' here is NOT Euro-0 (hbefa = 'PC P-Hybrid'/'PC PHEV petrol'); drop it.
        n_dropped_no_euro += int((sub["euro_class"] == "other").sum())
        sub = sub[sub["euro_class"] != "other"]
    counts = sub["euro_class"].value_counts()
    total = counts.sum()
    euro_shares[pt] = [100.0 * counts.get(k, 0) / total for k in EURO_ORDER]
    euro_ns[pt] = int(total)

n_bev = int((fleet["powertrain"] == "bev").sum())
n_gas = int((fleet["powertrain"] == "gas").sum())

# ----- panel (b): age-band shares ----------------------------------------
BAND_ORDER = ["under_5", "5_to_9", "10_to_14", "15_to_19", "20_to_24", "25_to_29", "30_plus"]
BAND_LABELS = ["unter 5 J.", "5-9", "10-14", "15-19", "20-24", "25-29", "30+"]
AGE_COLORS = ["#34d399", "#22d3ee", "#4f7ff0", "#8b7cf6", "#d946ef", "#fb7185", "#6b4550"]

PT_AGE_ROWS = PT_ROWS + [("bev", "Elektro (BEV)")]
band_shares = {}
age_ns = {}
medians = {}
for pt, _ in PT_AGE_ROWS:
    sub = fleet[fleet["powertrain"] == pt]
    counts = sub["age_band"].value_counts()
    total = counts.sum()
    band_shares[pt] = np.array([100.0 * counts.get(b, 0) / total for b in BAND_ORDER])
    age_ns[pt] = int(total)
    # 'age' is quantized to band midpoints (2, 7, 12, ...), so medians are ~-values.
    medians[pt] = float(sub["age"].median())

# ----- panel (c): mean age by economic status x segment -------------------
STATUS_ORDER = ["very_low", "low", "medium", "high", "very_high"]
STATUS_LABELS = ["sehr\nniedrig", "niedrig", "mittel", "hoch", "sehr\nhoch"]
SEG_ROWS = [("sportwagen", "Sportwagen"), ("oberklasse", "Oberklasse"),
            ("suv", "SUV"), ("mittelklasse", "Mittelklasse"),
            ("kompaktklasse", "Kompaktklasse"), ("kleinwagen", "Kleinwagen"),
            ("ALL", "alle Segmente")]
MIN_CELL_N = 100

heat_vals = {}   # (seg, status) -> mean age
heat_ns = {}     # (seg, status) -> n
seg_row_ns = {}  # seg -> total n
for seg, _ in SEG_ROWS:
    s = fleet if seg == "ALL" else fleet[fleet["segment"] == seg]
    seg_row_ns[seg] = int(len(s))
    for st in STATUS_ORDER:
        cell = s[s["economic_status"] == st]["age"]
        heat_vals[(seg, st)] = float(cell.mean())
        heat_ns[(seg, st)] = int(len(cell))

# ---------------------------------------------------------------- figure
fig = plt.figure(figsize=(20, 9.2), dpi=170)
fig.patch.set_facecolor(BG)

# ----- title block -------------------------------------------------------
fig.text(0.045, 0.965, "Emissionsbereit: die Flotte im Detail",
         ha="left", va="top", fontsize=17, color=FG, fontweight="bold")
fig.text(0.045, 0.917,
         "Jedes Fahrzeug trägt Euro-Klasse und HBEFA-Emissionstyp — Grundlage für künftige "
         "Emissionsanalysen. Rechts: das Flottenalter folgt dem ökonomischen Status des Haushalts.",
         ha="left", va="top", fontsize=10, color=SUB)

# ----- stat block top-right (computed) -----------------------------------
fig.text(0.958, 0.968, de_pct(hbefa_share), ha="right", va="top",
         fontsize=17, color="#34d399", fontweight="bold")
fig.text(0.958, 0.918,
         f"der Pkw mit HBEFA-Emissionstyp\n({de(n_hbefa)} von {de(n_fleet)})",
         ha="right", va="top", fontsize=8, color=SUB)

# Shared panel geometry
PANEL_Y0, PANEL_H = 0.245, 0.555

y_rows = {"petrol": 4.7, "diesel": 3.7, "hybrid": 2.7, "phev": 1.7, "bev": 0.45}
BAR_H, GLOW_H = 0.58, 0.76


def seg_text_color(hexcol):
    r, g, b = (int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#0a0e14" if lum > 0.45 else "#eef3fb"


# ========================= PANEL A (euro classes) =========================
axL = fig.add_axes([0.075, PANEL_Y0, 0.225, PANEL_H])
axL.set_facecolor(BG)
for s in axL.spines.values():
    s.set_visible(False)
axL.set_title("Euro-Klassen je Antrieb", loc="left", fontsize=12.5, color=FG, pad=14)

for pt, _ in PT_ROWS:
    y = y_rows[pt]
    left = 0.0
    for share, col in zip(euro_shares[pt], EURO_COLORS):
        if share <= 0:
            continue
        axL.barh(y, share, left=left, height=GLOW_H, color=col, alpha=0.22,
                 zorder=2, edgecolor="none")
        axL.barh(y, share, left=left, height=BAR_H, color=col,
                 zorder=3, edgecolor="none")
        if share >= 7.0:
            # Avoid rounding a <100% block up to "100 %" (e.g. PHEV Euro 6 = 99.7%).
            if share < 100.0 and round(share) >= 100:
                lab = f"{share:.1f} %".replace(".", ",")
            else:
                lab = f"{share:.0f} %"
            axL.text(left + share / 2, y, lab,
                     ha="center", va="center", fontsize=7.5,
                     color=seg_text_color(col), zorder=4)
        left += share

# BEV row: no emission standard (all euro_class=='other' == 'PC BEV')
y = y_rows["bev"]
axL.barh(y, 100, height=GLOW_H, color="#34d399", alpha=0.13, zorder=2)
axL.barh(y, 100, height=BAR_H, color="#34d399", alpha=0.42, zorder=3,
         edgecolor="#34d399", lw=1.0)
axL.text(50, y, "100 % ohne Abgasnorm (elektrisch)", ha="center", va="center",
         fontsize=7.5, color="#d9fbee", zorder=4)

# row labels + n
names = dict(PT_ROWS + [("bev", "Elektro (BEV)")])
row_ns = dict(euro_ns)
row_ns["bev"] = n_bev
axL.set_yticks([])
for pt, y in y_rows.items():
    axL.text(-2.5, y + 0.10, names[pt], ha="right", va="center",
             fontsize=9, color="#c7d0de", clip_on=False)
    axL.text(-2.5, y - 0.26, f"n = {de(row_ns[pt])}", ha="right", va="center",
             fontsize=7, color=DIM, clip_on=False)

axL.set_xlim(0, 100)
axL.set_ylim(-0.25, 5.35)
axL.set_xticks([0, 25, 50, 75, 100])
axL.set_xticklabels(["0", "25", "50", "75", "100 %"], fontsize=8, color=SUB)
axL.tick_params(length=0)
for x in [25, 50, 75, 100]:
    axL.axvline(x, color=GRID, lw=0.8, zorder=1)

legend_handles = [Patch(facecolor=c, edgecolor="none", label=l)
                  for c, l in zip(EURO_COLORS, EURO_LABELS)]
leg = axL.legend(handles=legend_handles, loc="upper center",
                 bbox_to_anchor=(0.5, -0.065), ncol=4, frameon=False,
                 fontsize=7.5, handlelength=1.0, handleheight=1.0,
                 columnspacing=1.0, handletextpad=0.5)
for t in leg.get_texts():
    t.set_color(SUB)

axL.text(0, -0.155,
         f"Euro 0 = HBEFA „Euro-0“ (vor Euro 1) · {n_dropped_no_euro} Hybrid/PHEV ohne\n"
         f"Euro-Klasse sowie Gas (n = {de(n_gas)}) nicht dargestellt",
         transform=axL.transAxes, ha="left", va="top", fontsize=7, color=DIM)

# ========================= PANEL B (age bands) ============================
axR = fig.add_axes([0.385, PANEL_Y0, 0.20, PANEL_H])
axR.set_facecolor(BG)
for s in axR.spines.values():
    s.set_visible(False)
axR.set_title("Flottenalter je Antrieb", loc="left", fontsize=12.5, color=FG, pad=14)

for pt, _ in PT_AGE_ROWS:
    y = y_rows[pt]
    left = 0.0
    for share, col in zip(band_shares[pt], AGE_COLORS):
        if share <= 0:
            continue
        axR.barh(y, share, left=left, height=GLOW_H, color=col, alpha=0.22,
                 zorder=2, edgecolor="none")
        axR.barh(y, share, left=left, height=BAR_H, color=col,
                 zorder=3, edgecolor="none")
        if share >= 9.0:
            # Avoid rounding a <100% block up to "100 %".
            if share < 100.0 and round(share) >= 100:
                lab = f"{share:.1f} %".replace(".", ",")
            else:
                lab = f"{share:.0f} %"
            axR.text(left + share / 2, y, lab,
                     ha="center", va="center", fontsize=7.5,
                     color=seg_text_color(col), zorder=4)
        left += share
    # Median age right of the bar; 'age' is band-midpoint quantized -> "~".
    axR.text(103.0, y, f"Median ~{medians[pt]:.0f} J.", ha="left", va="center",
             fontsize=8, color=SUB, clip_on=False)

# row labels + n (same grammar as panel a; n here = full powertrain fleet)
for pt, name in PT_AGE_ROWS:
    y = y_rows[pt]
    axR.text(-2.5, y + 0.10, name, ha="right", va="center",
             fontsize=9, color="#c7d0de", clip_on=False)
    axR.text(-2.5, y - 0.26, f"n = {de(age_ns[pt])}", ha="right", va="center",
             fontsize=7, color=DIM, clip_on=False)

axR.set_yticks([])
axR.set_xlim(0, 100)
axR.set_ylim(-0.25, 5.35)
axR.set_xticks([0, 25, 50, 75, 100])
axR.set_xticklabels(["0", "25", "50", "75", "100 %"], fontsize=8, color=SUB)
axR.tick_params(length=0)
for x in [25, 50, 75, 100]:
    axR.axvline(x, color=GRID, lw=0.8, zorder=1)

legend_handles_age = [Patch(facecolor=c, edgecolor="none", label=l)
                      for c, l in zip(AGE_COLORS, BAND_LABELS)]
leg2 = axR.legend(handles=legend_handles_age, loc="upper center",
                  bbox_to_anchor=(0.5, -0.065), ncol=4, frameon=False,
                  fontsize=7.5, handlelength=1.0, handleheight=1.0,
                  columnspacing=1.0, handletextpad=0.5)
for t in leg2.get_texts():
    t.set_color(SUB)

axR.text(0, -0.155,
         "Alter aus KBA-Altersbändern (5-Jahres-Klassen, Klassenmitten) —\n"
         f"Mediane daher quantisierte Näherung · Gas (n = {de(n_gas)}) nicht dargestellt",
         transform=axR.transAxes, ha="left", va="top", fontsize=7, color=DIM)

# ----- amber model-update note (below panel b) ---------------------------
NOTE = ("Alters-Modell gerade überarbeitet: (Alter, Euro) werden künftig gemeinsam\n"
        "per IPF gezogen, damit das Flottenalter die KBA-Verteilung trifft (Issue #92)\n"
        "— nächster Lauf aktualisiert dieses Bild.")
fig.text(0.385, 0.050, NOTE, ha="left", va="bottom", fontsize=7.1, color=AMBER,
         linespacing=1.45,
         bbox=dict(boxstyle="round,pad=0.5", facecolor="#171208",
                   edgecolor=AMBER, lw=0.8, alpha=0.9))

# ========================= PANEL C (age vs. income heatmap) ===============
axH = fig.add_axes([0.715, PANEL_Y0, 0.245, PANEL_H])
axH.set_facecolor(BG)
for s in axH.spines.values():
    s.set_visible(False)
axH.set_title("Alter folgt dem Einkommen", loc="left", fontsize=12.5, color=FG, pad=14)

# Mean-age colour scale: warm/bright = OLD (magma high end), dark = young.
cmap = plt.get_cmap("magma")
vmin, vmax = 4.0, 10.5


def cell_color(v):
    return cmap(0.15 + 0.80 * (v - vmin) / (vmax - vmin))


n_cols, n_rows = len(STATUS_ORDER), len(SEG_ROWS)
axH.set_xlim(0, n_cols)
# Extra 0.45 gap above the bottom "alle Segmente" row; 1.55 rows of headroom
# above the grid for the column headers + axis caption (below the panel title).
ALL_GAP = 0.45
HEADROOM = 1.55
axH.set_ylim(-(n_rows + ALL_GAP), HEADROOM)
axH.set_xticks([])
axH.set_yticks([])

low_n_cells = []
for i, (seg, seg_label) in enumerate(SEG_ROWS):
    y_top = -i if seg != "ALL" else -(i + ALL_GAP)
    yc = y_top - 0.5
    for j, st in enumerate(STATUS_ORDER):
        v = heat_vals[(seg, st)]
        n = heat_ns[(seg, st)]
        col = cell_color(v)
        suppress = n < MIN_CELL_N
        rect = Rectangle((j + 0.03, y_top - 0.97), 0.94, 0.94,
                         facecolor=col, edgecolor="none",
                         alpha=0.35 if suppress else 1.0, zorder=2)
        axH.add_patch(rect)
        # convert cmap RGBA to hex for luminance check
        hexcol = "#%02x%02x%02x" % tuple(int(255 * c) for c in col[:3])
        txt = de_years(v)
        if suppress:
            low_n_cells.append((seg_label, st, n))
            axH.text(j + 0.5, yc + 0.10, txt + "*", ha="center", va="center",
                     fontsize=8, color=DIM, zorder=4)
            axH.text(j + 0.5, yc - 0.22, f"n = {n}", ha="center", va="center",
                     fontsize=6.5, color=DIM, zorder=4)
        else:
            axH.text(j + 0.5, yc, txt, ha="center", va="center",
                     fontsize=8.5, color=seg_text_color(hexcol), zorder=4)
    # row label + n
    bold = seg == "ALL"
    axH.text(-0.12, yc + 0.13, seg_label, ha="right", va="center", fontsize=8.5,
             color=FG if bold else "#c7d0de",
             fontweight="bold" if bold else "normal", clip_on=False)
    axH.text(-0.12, yc - 0.24, f"n = {de(seg_row_ns[seg])}", ha="right", va="center",
             fontsize=6.5, color=DIM, clip_on=False)

# column headers (economic status, low -> high), inside the axes headroom
for j, lab in enumerate(STATUS_LABELS):
    axH.text(j + 0.5, 0.14, lab, ha="center", va="bottom", fontsize=7.5,
             color=SUB, linespacing=1.2)
axH.text(n_cols / 2, 1.12, "ökonomischer Status des Haushalts  (niedrig → hoch)",
         ha="center", va="bottom", fontsize=7, color=DIM)

# rose frame + annotation: Sportwagen row (row 0)
axH.add_patch(Rectangle((0.0, -0.995), n_cols, 0.985, facecolor="none",
                        edgecolor=ROSE, lw=1.6, zorder=5))
sp_vlow = heat_vals[("sportwagen", "very_low")]
sp_n = heat_ns[("sportwagen", "very_low")]
axH.text(0, -0.075,
         f"einkommensschwache Haushalte fahren die ältesten Sportwagen "
         f"(Ø {de_years(sp_vlow)} bei n = {sp_n})",
         transform=axH.transAxes, ha="left", va="top", fontsize=7.3, color=ROSE)

# footnote (includes colour hint + low-n marking)
min_shown_n = min(n for (s, st), n in heat_ns.items() if n >= MIN_CELL_N)
foot_low = " · ".join(f"* {sl}/{dict(zip(STATUS_ORDER, ['sehr niedrig','niedrig','mittel','hoch','sehr hoch']))[st]}: "
                      f"n = {n} < {MIN_CELL_N} (geringe Fallzahl, abgedimmt)"
                      for sl, st, n in low_n_cells)
axH.text(0, -0.155,
         "Ø-Alter in Jahren aus KBA-Altersbändern (Klassenmitten) · heller/wärmer = älter ·\n"
         f"{foot_low} ·\n"
         f"alle übrigen Zellen n ≥ {de(min_shown_n)} · „alle Segmente“ = gesamte attribuierte Flotte",
         transform=axH.transAxes, ha="left", va="top", fontsize=7, color=DIM,
         linespacing=1.5)

# ----- credit line -------------------------------------------------------
fig.text(0.045, 0.018,
         "Daten: braunschweig_100pct_allfeat_popsim_vehicles.csv (100%-PopulationSim-Lauf, "
         f"Export 2026-06-30, Stand vor Alters-Modell-Update) · attributierte Flotte: {de(n_fleet)} Pkw "
         "(mode=car mit Marke) · Spalten: powertrain, euro_class, age_band, age, segment, "
         "economic_status, hbefa_emission",
         ha="left", va="bottom", fontsize=7.5, color=DIM)

OUT = ("C:/Users/BIENZE~1/AppData/Local/Temp/claude/"
       "c--Users-bienzeisler-Documents-GitHub-eqasim-bs/"
       "b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/fig_fleet_emissions.png")
fig.savefig(OUT, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("saved", OUT)
print("hbefa share %.2f%%" % hbefa_share)
print("medians", medians)
print("heatmap:")
for seg, lab in SEG_ROWS:
    print(" ", lab, [round(heat_vals[(seg, st)], 2) for st in STATUS_ORDER],
          "n=", [heat_ns[(seg, st)] for st in STATUS_ORDER])
print("low-n cells:", low_n_cells)
