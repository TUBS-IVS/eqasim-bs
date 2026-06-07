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


def _robust_xlim(means: np.ndarray, half: np.ndarray) -> float:
    """Compute a robust symmetric x-axis cap so explosive outlier whiskers
    do not crush the scale for all other rows.

    Strategy: take the 75th percentile of all finite endpoint magnitudes,
    apply a sensible floor (5 %) and 10 % headroom.  The 75th percentile
    is used so that even a dataset with as few as four rows can have one
    outlier without it dominating the axis: one explosive row contributes
    two large endpoint values out of 2*N total, which at N=4 is 25 % of
    the sorted list -- p75 therefore still captures the bulk of non-outlier
    rows.  A single row whose whisker spans +/-400 % will not set the axis;
    it receives an overflow marker instead (drawn by the caller).

    Parameters
    ----------
    means:
        Per-row dot positions (mean_pct_diff).
    half:
        Per-row half-widths (2*stdev or rmse).

    Returns
    -------
    cap:
        Positive symmetric limit; set ax.set_xlim(-cap, cap).
    """
    # Suppress invalid-value warnings that arise when means or half contain
    # NaN (e.g. the all-NaN test path); the result is still correct because
    # the NaN endpoints are filtered out by isfinite() immediately after.
    with np.errstate(invalid="ignore"):
        lo = means - half
        hi = means + half
    ends = np.abs(np.concatenate([lo, hi]))
    finite = ends[np.isfinite(ends)]
    if finite.size == 0:
        return 10.0
    cap = float(np.nanpercentile(finite, 75))
    cap = max(cap, 5.0) * 1.10
    return cap


def dot_and_whisker(summary: pd.DataFrame, out_path: Path, whisker: str = "stdev") -> Path:
    """Dot = mean_pct_diff; whisker = +/-2*STDEV (whisker='stdev') or +/-RMSE
    (whisker='rmse'), centred on 0. Rows grouped + coloured by family.

    The x-axis is bounded by a robust 75th-percentile cap so a single noisy
    control cannot compress all other rows into the centre.  Rows whose
    whisker (or dot) extends beyond the cap receive a small outward-pointing
    triangle marker at the clipped edge, and a note is appended to the x-label
    so the reader knows the axis is bounded.  Full numbers are available in
    controls_summary.csv.
    """
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
    means = df["mean_pct_diff"].to_numpy()

    # Robust x-limit: bulk of data fills the axis; outlier rows get markers.
    cap = _robust_xlim(means, half)
    lo = means - half
    hi = means + half

    fig, ax = plt.subplots(figsize=(9, _fig_height(len(df))), constrained_layout=True)
    ax.set_xlim(-cap, cap)
    ax.axvline(0.0, color="black", linewidth=0.8, zorder=0)

    # Draw one errorbar per row so each can receive its own colour (older
    # matplotlib versions reject a list passed to ecolor on a single call).
    # Matplotlib clips errorbars to the axes by default; overflow rows are
    # additionally flagged with explicit triangle markers below.
    n_overflow = 0
    for yi, xi, hi_i, li_i, ci, half_i in zip(y, means, hi, lo, colors, half):
        ax.errorbar(xi, yi, xerr=half_i, fmt="none",
                    ecolor=ci, elinewidth=2, capsize=3, zorder=1)
        # Overflow markers: triangle pointing toward the clipped region.
        row_overflows = False
        if hi_i > cap:
            ax.plot(cap, yi, marker=">", color=ci, markersize=6, clip_on=False, zorder=3)
            row_overflows = True
        if li_i < -cap:
            ax.plot(-cap, yi, marker="<", color=ci, markersize=6, clip_on=False, zorder=3)
            row_overflows = True
        if row_overflows:
            n_overflow += 1

    ax.scatter(means, y, c=colors, s=28, zorder=2)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)

    xlabel = f"mean % difference (synthetic vs target), whisker = {wlabel}"
    if n_overflow > 0:
        xlabel += f" ({n_overflow} rows clipped at +/-{cap:.0f}%; see controls_summary.csv)"
    ax.set_xlabel(xlabel)
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
