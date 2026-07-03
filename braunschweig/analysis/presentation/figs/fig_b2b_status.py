# -*- coding: utf-8 -*-
"""Method figure: economic status assignment P(status | Haushaltstyp, Raumtyp).

Uses the PRODUCTION code path (braunschweig.data.mid.status_by_hhtype) on the
committed MiD 2023 tables:
  eqasim-data/data/braunschweig/mid/mid2023_status_by_hhtype_bundesland.csv
  eqasim-data/data/braunschweig/mid/mid2023_status_by_hhtype_raumtyp.csv
via region_status_probabilities(NDS base + RegioStaR-7 raumtyp tilt), i.e. the
exact Bayes update the pipeline applies. Right strip: realised household-level
economic_status distribution from the 100% all-features PopulationSim run
(persons.csv, deduplicated by household_id -- status is a household attribute).
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

REPO = Path("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs")
sys.path.insert(0, str(REPO))

from braunschweig.data.mid.status_by_hhtype import (  # noqa: E402
    BUNDESLAND_NIEDERSACHSEN,
    STATUS_CATEGORIES,
    load_status_by_hhtype_bundesland,
    load_status_by_hhtype_raumtyp,
    region_status_probabilities,
)

DATA_PATH = REPO / "eqasim-data" / "data"
PERSONS_CSV = Path(
    "C:/Users/bienzeisler/Downloads/popsim_100pct_results/"
    "output_bs_100pct_allfeat_popsim/braunschweig_100pct_allfeat_popsim_persons.csv"
)
OUT_PNG = Path(__file__).with_suffix(".png")

BG = "#0a0e14"
FG_TITLE = "#eef3fb"
FG_SUB = "#8b95a7"
FG_CREDIT = "#5a6577"
GRID = "#1d2633"

# Register Space Mono.
FONT_DIR = REPO / "braunschweig" / "analysis" / "poster" / "fonts"
FONT = "DejaVu Sans Mono"
for ttf in sorted(FONT_DIR.glob("SpaceMono-*.ttf")):
    fm.fontManager.addfont(str(ttf))
    FONT = fm.FontProperties(fname=str(ttf)).get_name()
plt.rcParams["font.family"] = FONT

# Display vocab (German), row order = MiD Haushaltstyp partition (substantive
# keys only; 'not_classifiable' is a pure MiD reference residual, households
# are never mapped to it).
HHTYPE_DE = {
    "single_18_29": "Alleinlebend, 18–29 J.",
    "single_30_59": "Alleinlebend, 30–59 J.",
    "single_60_plus": "Alleinlebend, 60+ J.",
    "couple_youngest_18_29": "2-Pers.-HH, jüngste 18–29 J.",
    "couple_youngest_30_59": "2-Pers.-HH, jüngste 30–59 J.",
    "couple_youngest_60_plus": "2-Pers.-HH, jüngste 60+ J.",
    "three_plus_adults": "3+ Erwachsene",
    "child_under_6": "Kinder, jüngstes < 6 J.",
    "child_under_14": "Kinder, jüngstes 6–13 J.",
    "child_under_18": "Kinder, jüngstes 14–17 J.",
    "single_parent": "Alleinerziehende",
}
ROW_ORDER = list(HHTYPE_DE.keys())
STATUS_DE = ["sehr\nniedrig", "niedrig", "mittel", "hoch", "sehr\nhoch"]
STATUS_DE_FLAT = ["sehr niedrig", "niedrig", "mittel", "hoch", "sehr hoch"]

# The two ZGB-relevant RegioStaR-7 poles: Braunschweig city (RS7 72) vs the
# rural small-town/village communes of the region (RS7 77).
PANELS = [
    ("stadtregion_regiopole_grossstadt",
     "Städtisch — z. B. Stadt Braunschweig\n(RegioStaR 72: Regiopole/Großstadt)"),
    ("laendlich_kleinstaedtisch",
     "Ländlich — Umland\n(RegioStaR 77: kleinstädtisch/dörflich)"),
]

NEON = ["#22d3ee", "#6366f1", "#d946ef", "#fb7185"]
neon_cmap = LinearSegmentedColormap.from_list("neon", NEON)


def build_matrix(raumtyp_key: str) -> np.ndarray:
    """P(status | hhtype, raumtyp) in percent, rows = ROW_ORDER."""
    df_bl = load_status_by_hhtype_bundesland(str(DATA_PATH))
    df_rt = load_status_by_hhtype_raumtyp(str(DATA_PATH))
    probs = region_status_probabilities(
        df_bl, df_rt, BUNDESLAND_NIEDERSACHSEN, raumtyp_key
    )
    mat = np.vstack([probs[k] for k in ROW_ORDER]) * 100.0
    return mat


def realised_household_distribution() -> tuple[np.ndarray, int]:
    """Household-level economic_status shares (%) from the 100% run."""
    df = pd.read_csv(
        PERSONS_CSV, sep=";", usecols=["household_id", "economic_status"]
    )
    hh = df.drop_duplicates("household_id")
    n = len(hh)
    shares = (
        hh["economic_status"].value_counts(normalize=True)
        .reindex(list(STATUS_CATEGORIES)).fillna(0.0).to_numpy() * 100.0
    )
    return shares, n


def main() -> None:
    matrices = {key: build_matrix(key) for key, _ in PANELS}
    shares, n_hh = realised_household_distribution()
    for key, m in matrices.items():
        rs = m.sum(axis=1)
        assert np.allclose(rs, 100.0, atol=1e-6), (key, rs)
    print("row sums OK; realised shares:", np.round(shares, 1), "n_hh:", n_hh)

    vmax = max(m.max() for m in matrices.values())

    fig = plt.figure(figsize=(18, 8.5), dpi=170)
    fig.patch.set_facecolor(BG)
    gs = fig.add_gridspec(
        1, 5, width_ratios=[1.0, 1.0, 0.035, 0.16, 0.50],
        left=0.155, right=0.965, top=0.78, bottom=0.10, wspace=0.16,
    )

    axes = [fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])]
    cax = fig.add_subplot(gs[0, 2])
    axb = fig.add_subplot(gs[0, 4])

    im = None
    for i, (ax, (key, panel_title)) in enumerate(zip(axes, PANELS)):
        mat = matrices[key]
        ax.set_facecolor(BG)
        im = ax.imshow(
            mat, cmap="magma", vmin=0.0, vmax=vmax, aspect="auto",
            interpolation="nearest",
        )
        # Cell annotations (integer percent).
        for r in range(mat.shape[0]):
            for c in range(mat.shape[1]):
                v = mat[r, c]
                frac = v / vmax
                color = "#0a0e14" if frac > 0.62 else "#f3f5f9"
                ax.text(
                    c, r, f"{v:.0f}", ha="center", va="center",
                    fontsize=10.5, color=color,
                    fontweight="bold" if frac > 0.42 else "normal",
                )
        ax.set_xticks(range(5))
        ax.set_xticklabels(STATUS_DE, fontsize=9.5, color=FG_SUB)
        ax.set_yticks(range(len(ROW_ORDER)))
        if i == 0:
            ax.set_yticklabels(
                [HHTYPE_DE[k] for k in ROW_ORDER], fontsize=10, color="#c7d0dd"
            )
        else:
            ax.set_yticklabels([])
        ax.tick_params(colors=FG_SUB, length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        # subtle white cell grid
        ax.set_xticks(np.arange(-0.5, 5, 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(ROW_ORDER), 1), minor=True)
        ax.grid(which="minor", color=BG, linewidth=1.6)
        ax.tick_params(which="minor", length=0)
        ax.set_title(panel_title, fontsize=10.5, color="#c7d0dd", pad=10)

    cb = fig.colorbar(im, cax=cax)
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=FG_SUB, labelsize=8.5, length=0)
    cb.set_label(
        "P(Status | Haushaltstyp) in %", fontsize=9, color=FG_SUB, labelpad=8
    )

    # --- Right strip: realised distribution in the synthetic population ----
    axb.set_facecolor(BG)
    ypos = np.arange(5)[::-1]  # sehr niedrig on top
    colors = [neon_cmap(t) for t in np.linspace(0.0, 1.0, 5)]
    # glow pass + sharp pass
    axb.barh(ypos, shares, height=0.86, color=colors, alpha=0.22, zorder=2)
    axb.barh(ypos, shares, height=0.55, color=colors, alpha=1.0, zorder=3)
    for y, v, c in zip(ypos, shares, colors):
        axb.text(
            v + 0.9, y, f"{v:.1f} %", va="center", ha="left",
            fontsize=10, color="#e6ebf4",
        )
    axb.set_yticks(ypos)
    axb.set_yticklabels(STATUS_DE_FLAT, fontsize=9.5, color="#c7d0dd")
    axb.set_xlim(0, max(shares) * 1.30)
    axb.set_xticks([0, 10, 20, 30])
    axb.set_xticklabels(["0", "10", "20", "30 %"], fontsize=8.5, color=FG_SUB)
    axb.tick_params(colors=FG_SUB, length=0)
    axb.grid(axis="x", color=GRID, linewidth=0.7, zorder=1)
    axb.set_axisbelow(True)
    for spine in axb.spines.values():
        spine.set_visible(False)
    axb.set_title(
        "Ergebnis: synthet. Bevölkerung\n"
        f"Anteil der Haushalte (n = {n_hh:,.0f})".replace(",", "."),
        fontsize=10, color="#c7d0dd", pad=12,
    )

    # --- Titles + credit ---------------------------------------------------
    fig.text(
        0.03, 0.955,
        "Ökonomischer Status: bedingte Zuweisung nach Haushaltstyp (MiD 2023)",
        fontsize=16.5, color=FG_TITLE, ha="left", va="top",
    )
    fig.text(
        0.03, 0.905,
        "Bayes-Update  P(Status | Haushaltstyp, Raumtyp)  —  Basis: Niedersachsen (MiD 2023),"
        " moduliert mit RegioStaR-7-Raumtyp  —  statt pauschaler Zuweisung nur nach Haushaltsgröße",
        fontsize=10, color=FG_SUB, ha="left", va="top",
    )
    fig.text(
        0.03, 0.022,
        "eqasim-bs | MiD 2023: mid2023_status_by_hhtype_{bundesland,raumtyp}.csv (Bayes via braunschweig.data.mid.status_by_hhtype)"
        " | rechts: 100-%-Lauf all-features (PopulationSim), Juni 2026",
        fontsize=7.5, color=FG_CREDIT, ha="left", va="bottom",
    )

    fig.savefig(
        OUT_PNG, facecolor=BG, bbox_inches="tight", pad_inches=0.15
    )
    print("saved", OUT_PNG)


if __name__ == "__main__":
    main()
