# Presentation figure: "Der Tag im Modell" - trips per hour of day stacked by purpose.
# Data: hourly departure counts computed from braunschweig_100pct_allfeat_popsim_trips.csv
# (departure_time seconds, following_purpose), departures >= 24 h wrapped mod 24 to clock time.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
import pandas as pd

FIGS = "C:/Users/BIENZE~1/AppData/Local/Temp/claude/c--Users-bienzeisler-Documents-GitHub-eqasim-bs/b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/"
FONT_DIR = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts/"

BG = "#0a0e14"
FG = "#eef3fb"
SUB = "#8b95a7"
CREDIT = "#5a6577"
GRID = "#1d2633"

# Register Space Mono
try:
    fm.fontManager.addfont(FONT_DIR + "SpaceMono-Regular.ttf")
    fm.fontManager.addfont(FONT_DIR + "SpaceMono-Bold.ttf")
    plt.rcParams["font.family"] = "Space Mono"
except Exception:
    plt.rcParams["font.family"] = "DejaVu Sans Mono"

tab = pd.read_csv(FIGS + "daycycle_hour_purpose_wrapped.csv", index_col="hour")
N_TOTAL = int(tab.values.sum())  # 2,840,277

# Stack order bottom -> top; (column, German label, accent color)
LAYERS = [
    ("work",      "Arbeit",                 "#fbbf24"),
    ("education", "Bildung",                "#22d3ee"),
    ("shop",      "Einkauf",                "#d946ef"),
    ("leisure",   "Freizeit",               "#6366f1"),
    ("other",     "Sonstiges & Erledigung", "#fb7185"),
    ("home",      "Heimweg",                "#7d8ba6"),
]

def hex2rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)])

def blend(c, b, f):
    # f = share of accent color kept against background b
    return tuple(hex2rgb(c) * f + hex2rgb(b) * (1 - f))

# x positions: bin midpoints + cyclic edge points at 0 and 24
mid = np.arange(24) + 0.5
x = np.concatenate([[0.0], mid, [24.0]])

def series(col):
    v = tab[col].to_numpy() / 1000.0  # thousands
    edge = 0.5 * (v[0] + v[-1])       # cyclic interpolation at midnight
    return np.concatenate([[edge], v, [edge]])

vals = {c: series(c) for c, _, _ in LAYERS}
cum = np.zeros_like(x)

fig = plt.figure(figsize=(16, 9), dpi=170)
fig.patch.set_facecolor(BG)
ax = fig.add_axes([0.065, 0.105, 0.905, 0.70])
ax.set_facecolor(BG)

for col, label, color in LAYERS:
    top = cum + vals[col]
    ax.fill_between(x, cum, top, color=blend(color, BG, 0.40), lw=0, zorder=2)
    # glow edge: wide low-alpha behind sharp line
    ax.plot(x, top, color=color, lw=6.5, alpha=0.18, zorder=3,
            solid_capstyle="round")
    ax.plot(x, top, color=color, lw=1.9, alpha=0.95, zorder=4,
            solid_capstyle="round")
    cum = top

total = cum  # top edge = hourly total

# --- peak annotations (values from the data) ---
tot_h = tab.sum(axis=1) / 1000.0
h_am = int(tot_h.loc[4:11].idxmax())
h_pm = int(tot_h.loc[12:21].idxmax())

for h_peak, name, dx, dy, ha in [(h_am, "Morgenspitze", 1.6, 28, "left"),
                                 (h_pm, "Abendspitze", 0.8, 34, "center")]:
    y_peak = tot_h.loc[h_peak]
    xm = h_peak + 0.5
    ax.plot([xm], [y_peak], marker="o", ms=7, mfc=FG, mec=BG, mew=1.2, zorder=6)
    ax.plot([xm], [y_peak], marker="o", ms=15, mfc="none", mec=FG, mew=0.8,
            alpha=0.35, zorder=6)
    ax.annotate(
        f"{name}\n{h_peak}–{h_peak + 1} Uhr · {y_peak:.0f} Tsd. Wege",
        xy=(xm, y_peak), xytext=(xm + dx, y_peak + dy), ha=ha,
        color=FG, fontsize=11.5, fontweight="bold", linespacing=1.45,
        arrowprops=dict(arrowstyle="-", color=SUB, lw=1.0,
                        shrinkA=4, shrinkB=6), zorder=7)

# --- axes styling ---
ax.set_xlim(0, 24)
ax.set_ylim(0, 305)
ax.set_xticks(range(0, 25, 3))
ax.set_xticklabels([f"{h}" for h in range(0, 25, 3)])
ax.set_yticks(range(0, 301, 50))
ax.tick_params(colors=SUB, labelsize=11.5, length=0, pad=7)
ax.set_xlabel("Uhrzeit (Stunde des Tages)", color=SUB, fontsize=12, labelpad=9)
ax.set_ylabel("Wege pro Stunde (Tausend)", color=SUB, fontsize=12, labelpad=10)
ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
ax.grid(axis="x", visible=False)
for s in ax.spines.values():
    s.set_visible(False)

# --- legend (top of stack first), with purpose shares ---
shares = tab.sum() / N_TOTAL * 100
handles, labels = [], []
for col, label, color in reversed(LAYERS):
    handles.append(plt.Rectangle((0, 0), 1, 1, fc=blend(color, BG, 0.55),
                                 ec=color, lw=1.6))
    labels.append(f"{label}  ({shares[col]:.0f} %)")
leg = ax.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.012, 0.985),
                frameon=False, fontsize=11.5, labelcolor=FG,
                handlelength=1.25, handleheight=1.05, labelspacing=0.55,
                borderaxespad=0.0)

# --- title block ---
fig.text(0.065, 0.945, "Der Tag im Modell: Wege im Tagesverlauf",
         color=FG, fontsize=18.5, fontweight="bold", ha="left", va="top")
fig.text(0.065, 0.893,
         "Lauf braunschweig_100pct_allfeat_popsim (100 % Population) · "
         f"{N_TOTAL:,.0f} Wege gesamt".replace(",", "."),
         color=SUB, fontsize=11.5, ha="left", va="top")
fig.text(0.065, 0.862,
         "Stündliche Abfahrten nach Wegezweck aus der eqasim-Ausgabe (trips.csv); "
         "Abfahrten nach 24 Uhr (3,6 %) der Uhrzeit zugeordnet",
         color=SUB, fontsize=11.5, ha="left", va="top")

# --- credit line ---
fig.text(0.065, 0.022,
         "Quelle: output_bs_100pct_allfeat_popsim/braunschweig_100pct_allfeat_popsim_trips.csv "
         "· Lauf vom 30.06.2026, Commit e1164cc · Abbildung erstellt am 02.07.2026",
         color=CREDIT, fontsize=8, ha="left", va="bottom")

out = FIGS + "fig_mob_daycycle.png"
fig.savefig(out, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("saved", out)
