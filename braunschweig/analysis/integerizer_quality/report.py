"""Assemble the integerizer-quality outputs: per-control error split by zone status,
per-cell error summary, feasibility aggregation, and the writers (CSV / GPKG / md)."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.analysis.integerizer_quality import cell_geometry

logger = logging.getLogger(__name__)


def build_outputs(error_long: pd.DataFrame, zones: pd.DataFrame, *, regiostar=None) -> dict:
    """Build the analysis output dict from the long error frame.

    Parameters
    ----------
    error_long:
        Long frame from ``cell_error.cell_error_table`` with columns
        ``[zensus100m, control, realised, target, abs_error, batch]`` and
        optionally ``KREIS``.
    zones:
        Zone feasibility frame from ``zone_feasibility.classify_zones``.

    Returns
    -------
    dict with keys:
        ``error_by_control``, ``cell_summary``, ``feasibility_by_batch``,
        ``error_by_kreis`` (real per-Kreis aggregation when KREIS available).
    """
    status = zones[["zensus100m", "status"]].drop_duplicates("zensus100m")
    err = error_long.merge(status, on="zensus100m", how="left")
    err["status"] = err["status"].fillna("unknown")

    def _stats(group):
        a = group["abs_error"].to_numpy(dtype=float)
        return pd.Series({
            "n_cells": int(group["zensus100m"].nunique()),
            "mean_abs_error": float(np.mean(a)) if a.size else np.nan,
            "max_abs_error": float(np.max(a)) if a.size else np.nan,
            "p90_abs_error": float(np.percentile(a, 90)) if a.size else np.nan,
        })

    error_by_control = (err.groupby(["control", "status"]).apply(_stats)
                        .reset_index())

    cell_summary = (err.groupby("zensus100m")
                    .agg(total_abs_error=("abs_error", "sum"),
                         n_controls=("control", "nunique"))
                    .reset_index())
    smart = set(status.loc[status["status"] == "smart_rounded", "zensus100m"])
    cell_summary["is_smart_rounded"] = cell_summary["zensus100m"].isin(smart)

    # Attach KREIS to cell_summary when available in the error frame (carried from
    # synthetic_households by cell_error_table).  Do NOT mutate the caller's frame.
    kreis_col = "KREIS" if "KREIS" in error_long.columns else None
    if kreis_col is not None:
        cell_kreis = (error_long[["zensus100m", kreis_col]]
                      .drop_duplicates("zensus100m")
                      .rename(columns={kreis_col: "kreis"}))
        cell_summary = cell_summary.merge(cell_kreis, on="zensus100m", how="left")
    else:
        cell_summary = cell_summary.copy()
        cell_summary["kreis"] = None

    feasibility_by_batch = (zones.groupby("batch")["status"]
                            .value_counts().unstack(fill_value=0).reset_index())

    # Real per-Kreis error aggregation from the cell_summary kreis column.
    if cell_summary["kreis"].notna().any():
        error_by_kreis = (cell_summary.dropna(subset=["kreis"])
                          .groupby("kreis")
                          .agg(
                              mean_total_abs_error=("total_abs_error", "mean"),
                              sum_total_abs_error=("total_abs_error", "sum"),
                              n_cells=("zensus100m", "nunique"),
                          )
                          .reset_index())
    else:
        logger.info(
            "[integerizer_quality] no KREIS column in error frame; "
            "error_by_kreis will be empty."
        )
        error_by_kreis = pd.DataFrame(
            columns=["kreis", "mean_total_abs_error", "sum_total_abs_error", "n_cells"])

    return {
        "error_by_control": error_by_control,
        "cell_summary": cell_summary,
        "feasibility_by_batch": feasibility_by_batch,
        "error_by_kreis": error_by_kreis,
    }


def write_report(outputs: dict, output_dir, *, regiostar=None) -> None:
    out = Path(output_dir) / "integerizer_quality"
    out.mkdir(parents=True, exist_ok=True)
    outputs["error_by_control"].to_csv(out / "error_by_control.csv", index=False)
    outputs["feasibility_by_batch"].to_csv(out / "feasibility_by_stratum.csv", index=False)

    # Write the real per-Kreis breakdown (may be empty when KREIS absent).
    outputs["error_by_kreis"].to_csv(out / "error_by_kreis.csv", index=False)

    # GPKG: per-cell error map (operate on a copy to avoid mutating the caller's dict).
    cell_summary = outputs["cell_summary"].copy()
    gdf = cell_geometry.cells_geodataframe(cell_summary["zensus100m"].tolist(), target_epsg=25832)
    gdf = gdf.merge(cell_summary, on="zensus100m", how="left")
    gdf.to_file(out / "cell_error.gpkg", driver="GPKG")

    _write_summary_md(outputs, out)


def _write_summary_md(outputs: dict, out: Path) -> None:
    ebc = outputs["error_by_control"]
    lines = ["# Integerizer / smart-rounding error analysis", ""]
    opt = ebc[ebc["status"] == "optimal"]["mean_abs_error"].mean()
    smr = ebc[ebc["status"] == "smart_rounded"]["mean_abs_error"].mean()
    lines += [
        f"- mean abs error, OPTIMAL zones: {opt:.3f}",
        f"- mean abs error, smart-rounded zones: {smr:.3f}",
        f"- smart-rounding-attributable gap: {smr - opt:.3f}",
        "",
        "Error sources (only (a) is smart-rounding):",
        "- (a) integerizer smart-rounding = the OPTIMAL vs smart_rounded gap above.",
        "- (b) irreducible MiD-margin inconsistency (margins rounded to integer percent).",
        "- (c) IPU non-convergence (zones logged 'converged False iter 1000').",
        "",
        "Fit metric vs the run's OWN control inputs (not an external ground truth).",
    ]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
