"""Plot palette and Matplotlib styling helpers."""
from __future__ import annotations

import matplotlib as mpl
import matplotlib.pyplot as plt

# --- Brand palette ---------------------------------------------------------
PALETTE = {
    "synth": "#1f4e79",       # BS-Blau
    "synth_light": "#5b87b8",
    "ref": "#c00000",         # MiD-Rot
    "ref_light": "#e07c7c",
    "ok": "#2e7d32",
    "warn": "#ed8936",
    "bad": "#c00000",
    "muted": "#6c757d",
    "bg": "#fbfbfd",
}

MODE_COLORS = {
    "miv": "#1f4e79",
    "oev": "#28a745",
    "rad": "#ed8936",
    "fuss": "#6c757d",
    "other": "#999999",
}

PURPOSE_COLORS = {
    "home":      "#1f4e79",
    "work":      "#c00000",
    "education": "#7b3294",
    "shop":      "#ed8936",
    "leisure":   "#28a745",
    "other":     "#6c757d",
    "escort":    "#5b87b8",
}


def apply_mpl_style() -> None:
    """Set a consistent, professional matplotlib style for all plots."""
    mpl.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": "#333333",
        "axes.labelcolor": "#333333",
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.labelsize": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": "#e8e8e8",
        "grid.linewidth": 0.6,
        "xtick.color": "#333333",
        "ytick.color": "#333333",
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.frameon": False,
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "savefig.dpi": 150,
        "savefig.bbox": "tight",
    })


def deviation_color(value_pct: float, thresholds: tuple[float, float]) -> str:
    """Return a traffic-light hex colour given an absolute deviation."""
    a = abs(value_pct)
    green, amber = thresholds
    if a <= green:
        return PALETTE["ok"]
    if a <= amber:
        return PALETTE["warn"]
    return PALETTE["bad"]


def deviation_class(value_pct: float, thresholds: tuple[float, float]) -> str:
    """Return CSS class name corresponding to deviation."""
    a = abs(value_pct)
    green, amber = thresholds
    if a <= green:
        return "ok"
    if a <= amber:
        return "warn"
    return "bad"
