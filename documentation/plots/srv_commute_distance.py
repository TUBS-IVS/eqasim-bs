"""Plots for the SrV primary-distance baseline (mirrors eqasim documentation/plots/commute_distance.py).

Reads the CSVs written by ``braunschweig.analysis.synthesis.commute_distance_by_kreis``
and draws grouped band-share bars, model vs SrV, one panel per Kreis (plus ZGB).
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from braunschweig.calibration import srv_distance_targets as T  # noqa: E402


def _grouped_bars(cells, labels, title, out_png, group_col, group_values):
    n = len(group_values)
    fig, axes = plt.subplots(1, n, figsize=(2.6 * n, 2.8), sharey=True, dpi=150)
    if n == 1:
        axes = [axes]
    x = range(len(labels))
    for ax, value in zip(axes, group_values):
        row = cells[cells[group_col] == value]
        if row.empty:
            ax.set_title(f"{value}\n(no data)")
            continue
        row = row.iloc[0]
        model = [row[f"model_share_{lbl}"] for lbl in labels]
        target = [row[f"target_share_{lbl}"] for lbl in labels]
        ax.bar([i - 0.2 for i in x], model, width=0.4, label="model")
        ax.bar([i + 0.2 for i in x], target, width=0.4, label="SrV 2023")
        emd_str = "n/a" if pd.isna(row['emd']) else f"{row['emd']:.3f}"
        ax.set_title(f"{value}\nEMD {emd_str} ({row['classification']})", fontsize=8)
        ax.set_xticks(list(x))
        ax.set_xticklabels([lbl.replace("_", "-") for lbl in labels], rotation=90, fontsize=7)
    axes[0].set_ylabel("share")
    axes[0].legend(fontsize=7)
    fig.suptitle(title, fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)
    return str(out_png)


def plot_work_bands(commute_by_kreis_csv, out_png, scope="all"):
    cells = pd.read_csv(commute_by_kreis_csv, dtype={"code": str})
    cells = cells[cells["scope"] == scope]
    codes = [c for c in list(T.ZGB_KREISE) + ["zgb"] if c in set(cells["code"])]
    return _grouped_bars(cells, T.WORK_BAND_LABELS, f"Home->work distance bands (routed km), scope={scope}",
                         out_png, "code", codes)


def plot_education_bands(education_csv, out_png):
    cells = pd.read_csv(education_csv, dtype={"code": str})
    zgb = cells[cells["code"] == "zgb"].copy()
    levels = [lvl for lvl in T.COMPARABLE_LEVELS if lvl in set(zgb["education_level"])]
    return _grouped_bars(zgb, T.EDUCATION_BAND_LABELS, "Home->education distance bands (routed km), ZGB by level",
                         out_png, "education_level", levels)
