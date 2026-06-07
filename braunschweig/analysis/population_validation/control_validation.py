"""Compare each control's realised synthetic shares against its target, per geo
cell, and summarise the deviations (PopulationSim-style)."""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

LOGGER = logging.getLogger("braunschweig.analysis.control_validation")


def evaluate_control(control, frames, geo, data_path: str) -> pd.DataFrame:
    """Return the long deviation table for one control, or an empty frame
    (logged) when the control has no target or no realised data."""
    realized = control.realized(frames, geo)
    if realized.empty:
        LOGGER.warning("control %s: no realised data; skipped", control.name)
        return pd.DataFrame()
    if control.target is None:
        LOGGER.info("control %s: descriptive only (no target); deviation not computed",
                    control.name)
        return pd.DataFrame()

    target = control.target(data_path)

    realized = realized.copy()
    realized["category"] = realized["category"].astype(str)
    target = target.copy()
    target["category"] = target["category"].astype(str)

    # Per-geo synthetic totals from the FULL realised frame (including any
    # categories not present in the target), so shares use a stable denominator
    # and zero-count target categories are not lost.
    totals = realized.groupby("geo_id")["synthetic_count"].sum()

    # No silent fallback: log synthetic (geo, category) cells that have no target
    # category (out-of-vocabulary). They stay in the `totals` denominator but
    # cannot be compared to a target, so they are excluded from the deviation.
    realized_cats = set(zip(realized["geo_id"], realized["category"]))
    target_cats = set(zip(target["geo_id"], target["category"]))
    oov = realized_cats - target_cats
    if oov:
        LOGGER.warning(
            "control %s: %d synthetic (geo, category) cell(s) have no matching "
            "target category and are excluded from the deviation; examples: %s",
            control.name, len(oov), sorted(oov)[:3],
        )

    # Restrict the target to geo cells that actually carry synthetic data, then
    # RIGHT-join so every target category is evaluated -- filling synthetic_count
    # = 0 where the synthetic population produced none (the key validation case
    # the previous inner join silently dropped).
    target = target[target["geo_id"].isin(totals.index)]
    merged = realized.merge(target, on=["geo_id", "category"], how="right")
    merged["synthetic_count"] = merged["synthetic_count"].fillna(0).astype(int)

    merged["geo_total"] = merged["geo_id"].map(totals)
    merged["synthetic_pct"] = 100.0 * merged["synthetic_count"] / merged["geo_total"]
    merged["target_pct"] = 100.0 * merged["target_share"]
    merged["target_count"] = merged["target_share"] * merged["geo_total"]
    merged["delta_pp"] = merged["synthetic_pct"] - merged["target_pct"]
    with np.errstate(divide="ignore", invalid="ignore"):
        merged["pct_diff"] = np.where(
            merged["target_count"] > 0,
            100.0 * (merged["synthetic_count"] - merged["target_count"]) / merged["target_count"],
            np.nan,
        )
    merged["control"] = control.name
    merged["family"] = control.family
    merged["geography"] = control.geography
    return merged[[
        "control", "family", "geography", "geo_id", "category",
        "synthetic_count", "synthetic_pct", "target_pct", "target_count",
        "delta_pp", "pct_diff",
    ]]


def evaluate_all(registry, frames, geo, data_path: str) -> pd.DataFrame:
    parts = [evaluate_control(c, frames, geo, data_path) for c in registry]
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def summarize(long: pd.DataFrame) -> pd.DataFrame:
    """One row per (control, family, category): n_cells + deviation statistics.
    STDEV is the population standard deviation (ddof=0) so a single-cell control
    is well defined (0)."""
    if long.empty:
        return pd.DataFrame()
    rows = []
    for (control, family, category), sub in long.groupby(["control", "family", "category"]):
        pd_ = sub["pct_diff"].to_numpy(dtype=float)
        pd_valid = pd_[~np.isnan(pd_)]
        dp = sub["delta_pp"].to_numpy(dtype=float)
        rows.append({
            "control": control, "family": family, "category": category,
            "n_cells": int(len(sub)),
            "mean_pct_diff": float(np.mean(pd_valid)) if pd_valid.size else np.nan,
            "stdev_pct_diff": float(np.std(pd_valid, ddof=0)) if pd_valid.size else np.nan,
            "rmse_pct_diff": float(np.sqrt(np.mean(pd_valid ** 2))) if pd_valid.size else np.nan,
            "mean_delta_pp": float(np.mean(dp)),
            "max_abs_delta_pp": float(np.max(np.abs(dp))),
        })
    return pd.DataFrame(rows)
