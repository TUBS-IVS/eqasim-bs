"""PopulationSim-style validation chart + quality plot. Layout is tuned so axes
never overlap regardless of the number of control rows."""
from __future__ import annotations

from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

FAMILY_COLORS = {
    "census": "tab:blue", "mid_person": "tab:green",
    "mid_household": "tab:orange", "distribution": "tab:purple",
}
GRADE_COLORS = {
    "very good": "tab:green", "good": "yellowgreen",
    "acceptable": "gold", "needs improvement": "tab:red",
}


def _fig_height(n_rows: int) -> float:
    return max(3.0, 0.28 * n_rows + 1.5)


def dot_and_whisker(summary: pd.DataFrame, out_path: Path, whisker: str = "stdev") -> Path:
    """Dot = mean_pct_diff; whisker = +/-2*STDEV (whisker='stdev') or +/-RMSE
    (whisker='rmse'), centred on 0. Rows grouped + coloured by family."""
    out_path = Path(out_path)
    if summary.empty:
        fig, ax = plt.subplots(figsize=(6, 2), constrained_layout=True)
        ax.text(0.5, 0.5, "no controls with a target", ha="center", va="center")
        ax.set_axis_off()
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        return out_path

    df = summary.sort_values(["family", "control", "category"]).reset_index(drop=True)
    labels = [f"{r.control} = {r.category}" for r in df.itertuples()]
    y = np.arange(len(df))[::-1]
    if whisker == "rmse":
        half = df["rmse_pct_diff"].to_numpy()
        wlabel = "+/- RMSE of % diff"
    else:
        half = 2.0 * df["stdev_pct_diff"].to_numpy()
        wlabel = "+/- 2*STDEV of % diff"
    colors = [FAMILY_COLORS.get(f, "gray") for f in df["family"]]

    fig, ax = plt.subplots(figsize=(9, _fig_height(len(df))), constrained_layout=True)
    ax.axvline(0.0, color="black", linewidth=0.8, zorder=0)
    # Draw one errorbar per row so each can receive its own colour (older
    # matplotlib versions reject a list passed to ecolor on a single call).
    means = df["mean_pct_diff"].to_numpy()
    for i, (yi, xi, hi, ci) in enumerate(zip(y, means, half, colors)):
        ax.errorbar(xi, yi, xerr=hi, fmt="none",
                    ecolor=ci, elinewidth=2, capsize=3, zorder=1)
    ax.scatter(means, y, c=colors, s=28, zorder=2)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel(f"mean % difference (synthetic vs target), whisker = {wlabel}")
    ax.set_title("Control validation - synthetic vs synthesis targets")
    handles = [plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=c,
                          markersize=8, label=fam) for fam, c in FAMILY_COLORS.items()]
    ax.legend(handles=handles, loc="lower right", fontsize=8, title="control family")
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def quality_plot(quality: pd.DataFrame, out_path: Path) -> Path:
    """Horizontal bar of mean |delta_pp| per control, coloured by grade."""
    out_path = Path(out_path)
    if quality.empty:
        fig, ax = plt.subplots(figsize=(6, 2), constrained_layout=True)
        ax.text(0.5, 0.5, "no controls assessed", ha="center", va="center")
        ax.set_axis_off()
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        return out_path
    df = quality.sort_values("mean_abs_delta_pp")
    y = np.arange(len(df))
    colors = [GRADE_COLORS.get(g, "gray") for g in df["grade"]]
    fig, ax = plt.subplots(figsize=(9, _fig_height(len(df))), constrained_layout=True)
    ax.barh(y, df["mean_abs_delta_pp"], color=colors)
    ax.set_yticks(y)
    ax.set_yticklabels(df["control"], fontsize=8)
    ax.set_xlabel("mean |delta| (percentage points)")
    ax.set_title("Control quality (lower is better)")
    handles = [plt.Line2D([0], [0], marker="s", color="w", markerfacecolor=c,
                          markersize=9, label=g) for g, c in GRADE_COLORS.items()]
    ax.legend(handles=handles, loc="lower right", fontsize=8, title="grade")
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
