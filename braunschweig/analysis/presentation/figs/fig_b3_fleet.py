# Figure B3: "Simulierte Fahrzeugflotte - Marken und Antriebe"
# Two-panel horizontal bar chart (dark deck style) from the 100% all-features
# PopulationSim run vehicles table.
#
# Data: braunschweig_100pct_allfeat_popsim_vehicles.csv (semicolon-separated)
# Fleet definition: rows with mode == "car" AND non-null brand/powertrain
# (= household fleet cars with KBA attribution; vehicle_id "N:car:0").
# Excluded: 552,274 per-person default routing vehicles (vehicle_id "N:car",
# no household link, no attributes) and 1,100,608 car_passenger pseudo-vehicles.

import matplotlib
matplotlib.use("Agg")

import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.colors import LinearSegmentedColormap, to_rgba

BG = "#0a0e14"
TEXT_MAIN = "#eef3fb"
TEXT_SUB = "#8b95a7"
TEXT_CREDIT = "#5a6577"
GRID = "#1d2633"

VEHICLES_CSV = ("C:/Users/bienzeisler/Downloads/popsim_100pct_results/"
                "output_bs_100pct_allfeat_popsim/braunschweig_100pct_allfeat_popsim_vehicles.csv")
OUT_PNG = ("C:/Users/BIENZE~1/AppData/Local/Temp/claude/"
           "c--Users-bienzeisler-Documents-GitHub-eqasim-bs/"
           "b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/fig_b3_fleet.png")

FONT_DIR = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts"


def register_fonts():
    family = "DejaVu Sans Mono"
    try:
        reg = os.path.join(FONT_DIR, "SpaceMono-Regular.ttf")
        bold = os.path.join(FONT_DIR, "SpaceMono-Bold.ttf")
        if os.path.exists(reg):
            fm.fontManager.addfont(reg)
            family = fm.FontProperties(fname=reg).get_name()
        if os.path.exists(bold):
            fm.fontManager.addfont(bold)
    except Exception:
        pass
    plt.rcParams["font.family"] = family


def de_int(n):
    return f"{n:,}".replace(",", ".")


def de_pct(x, digits=1):
    return f"{x:.{digits}f}".replace(".", ",") + " %"


def main():
    register_fonts()

    df = pd.read_csv(VEHICLES_CSV, sep=";",
                     usecols=["household_id", "mode", "brand", "powertrain"],
                     dtype=str)
    n_rows_total = len(df)
    car = df[df["mode"] == "car"]
    fleet = car[car["brand"].notna()].copy()
    n_fleet = len(fleet)
    n_default = len(car) - n_fleet
    n_households = fleet["household_id"].nunique()

    # empty-string brands (none expected, but be honest if present)
    fleet["brand"] = fleet["brand"].str.strip()
    n_empty_brand = (fleet["brand"] == "").sum()
    named = fleet[fleet["brand"] != ""]

    brand_counts = named["brand"].value_counts()
    top15 = brand_counts.head(15)
    top15_share = top15.sum() / n_fleet * 100.0

    pt_counts = fleet["powertrain"].value_counts()

    PT_LABELS = {
        "petrol": "Benzin",
        "diesel": "Diesel",
        "hybrid": "Hybrid",
        "phev": "Plug-in-Hybrid",
        "bev": "Elektro (BEV)",
        "gas": "Gas (CNG/LPG)",
    }
    PT_COLORS = {
        "petrol": "#fbbf24",   # amber
        "diesel": "#a78bfa",   # violet
        "hybrid": "#22d3ee",   # cyan
        "phev": "#d946ef",     # fuchsia
        "bev": "#34d399",      # green
        "gas": "#fb7185",      # rose
    }
    pt_order = [k for k in ["petrol", "diesel", "bev", "hybrid", "phev", "gas"]
                if k in pt_counts.index]
    # any unexpected powertrain values -> append
    for k in pt_counts.index:
        if k not in pt_order:
            pt_order.append(k)

    # ---------------- figure ----------------
    fig = plt.figure(figsize=(18, 8.5), dpi=170)
    fig.patch.set_facecolor(BG)

    gs = fig.add_gridspec(1, 2, left=0.075, right=0.955, top=0.765, bottom=0.10,
                          wspace=0.42, width_ratios=[1.15, 1.0])
    ax_l = fig.add_subplot(gs[0, 0])
    ax_r = fig.add_subplot(gs[0, 1])
    for ax in (ax_l, ax_r):
        ax.set_facecolor(BG)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(colors=TEXT_SUB, length=0)

    # ----- LEFT: top-15 brands -----
    brands = top15.index.tolist()
    shares = (top15.values / n_fleet * 100.0)
    y = np.arange(len(brands))[::-1]  # top brand at top

    grad = LinearSegmentedColormap.from_list(
        "brandneon", ["#22d3ee", "#6366f1", "#46508c"])
    colors = [grad(i / max(len(brands) - 1, 1)) for i in range(len(brands))]

    for yi, sh, c in zip(y, shares, colors):
        # glow pass (wider, low alpha), then sharp bar
        ax_l.barh(yi, sh, height=0.86, color=to_rgba(c, 0.22), zorder=2)
        ax_l.barh(yi, sh, height=0.56, color=c, zorder=3)
        ax_l.text(sh + 0.35, yi, de_pct(sh), va="center", ha="left",
                  fontsize=10, color=TEXT_MAIN, zorder=4)

    ax_l.set_yticks(y)
    ax_l.set_yticklabels(brands, fontsize=10.5, color=TEXT_MAIN)
    ax_l.set_xlim(0, shares.max() * 1.17)
    ax_l.set_ylim(-0.7, len(brands) - 0.3)
    ax_l.xaxis.grid(True, color=GRID, lw=0.8, zorder=1)
    ax_l.set_axisbelow(True)
    ax_l.set_xticks(np.arange(0, shares.max() * 1.1, 5))
    ax_l.set_xticklabels([f"{int(t)}" for t in np.arange(0, shares.max() * 1.1, 5)],
                         fontsize=9, color=TEXT_SUB)
    ax_l.text(0, 1.115, "Top-15 Marken", transform=ax_l.transAxes,
              fontsize=12.5, color=TEXT_MAIN, fontweight="bold", va="bottom")
    ax_l.text(0, 1.045, f"Anteil an allen Pkw in % · Top 15 = {de_pct(top15_share)} der Flotte",
              transform=ax_l.transAxes, fontsize=9, color=TEXT_SUB, va="bottom")

    # ----- RIGHT: powertrain mix -----
    pt_shares = np.array([pt_counts[k] / n_fleet * 100.0 for k in pt_order])
    pt_labels = [PT_LABELS.get(k, k) for k in pt_order]
    pt_cols = [PT_COLORS.get(k, "#94a3b8") for k in pt_order]
    yr = np.arange(len(pt_order))[::-1]

    for yi, sh, c, k in zip(yr, pt_shares, pt_cols, pt_order):
        ax_r.barh(yi, sh, height=0.80, color=to_rgba(c, 0.22), zorder=2)
        ax_r.barh(yi, sh, height=0.50, color=c, zorder=3)
        lbl = f"{de_pct(sh)}  ({de_int(int(pt_counts[k]))})"
        ax_r.text(sh + 1.0, yi, lbl, va="center", ha="left",
                  fontsize=10, color=TEXT_MAIN, zorder=4)

    ax_r.set_yticks(yr)
    ax_r.set_yticklabels(pt_labels, fontsize=10.5, color=TEXT_MAIN)
    ax_r.set_xlim(0, pt_shares.max() * 1.32)
    ax_r.set_ylim(-0.7, len(pt_order) - 0.3)
    ax_r.xaxis.grid(True, color=GRID, lw=0.8, zorder=1)
    ax_r.set_axisbelow(True)
    xt = np.arange(0, pt_shares.max() * 1.25, 10)
    ax_r.set_xticks(xt)
    ax_r.set_xticklabels([f"{int(t)}" for t in xt], fontsize=9, color=TEXT_SUB)
    ax_r.text(0, 1.115, "Antriebsarten", transform=ax_r.transAxes,
              fontsize=12.5, color=TEXT_MAIN, fontweight="bold", va="bottom")
    ax_r.text(0, 1.045, "Anteil an allen Pkw in % (Anzahl Fahrzeuge)",
              transform=ax_r.transAxes, fontsize=9, color=TEXT_SUB, va="bottom")

    # ----- titles / credit -----
    fig.text(0.075, 0.945, "Simulierte Fahrzeugflotte – Marken und Antriebe",
             fontsize=16.5, color=TEXT_MAIN, ha="left", va="top", fontweight="bold")
    sub = (f"{de_int(n_fleet)} synthetische Pkw in {de_int(n_households)} Haushalten "
           "der Region Braunschweig (ZGB) · Marken- und Antriebszuordnung aus KBA-Bestandsdaten")
    fig.text(0.075, 0.895, sub, fontsize=10, color=TEXT_SUB, ha="left", va="top")

    credit = ("eqasim-bs | 100-%-Lauf all-features (PopulationSim), Juni 2026 | "
              "braunschweig_100pct_allfeat_popsim_vehicles.csv")
    fig.text(0.075, 0.028, credit, fontsize=7.5, color=TEXT_CREDIT, ha="left", va="bottom")

    fig.savefig(OUT_PNG, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)

    # console provenance
    print("rows total:", n_rows_total)
    print("fleet cars (mode=car, brand attributed):", n_fleet)
    print("default per-person car vehicles excluded:", n_default)
    print("households with fleet cars:", n_households)
    print("empty-string brands:", n_empty_brand)
    print("top15 coverage %:", round(top15_share, 2))
    print("powertrain shares %:",
          {k: round(pt_counts[k] / n_fleet * 100, 2) for k in pt_order})
    print("saved:", OUT_PNG)


if __name__ == "__main__":
    main()
