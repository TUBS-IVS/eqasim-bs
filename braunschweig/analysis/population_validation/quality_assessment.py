"""Standard population-synthesis fit measures + grading + interpretation hints.

Measures (per control, over its geo cells x categories):
  SRMSE   standardized RMSE of (synthetic - target) counts, = RMSE / mean(target)
  TAE     total absolute error in counts
  RAE     relative absolute error = TAE / sum(target)
  coverage_5pp / coverage_10pp  share of cells with |delta_pp| <= 5 / 10
  r2      coefficient of determination of synthetic vs target counts
Grade thresholds are configurable (defaults below)."""
from __future__ import annotations

import numpy as np
import pandas as pd

# Default grade thresholds on |mean_delta_pp| (percentage points). Documented and
# overridable by the caller; no hard-coded magic at call sites.
DEFAULT_GRADE_BOUNDS_PP = (1.0, 3.0, 5.0)
GRADE_LABELS = ("very good", "good", "acceptable", "needs improvement")

CAUSE_HINTS = {
    "driving_license_type": (
        "MiD P17.1 base is age 14+ incl. ~19% BF17 holders, while the synthesis "
        "floor is 18 (BF17 ignored) -> a structural ~1pp shortfall is expected."),
    "pt_ticket_type": (
        "MiD P24.1 margins are rounded to integer percent and raked across "
        "Kreis/sex/age -> a ~5pp least-squares compromise per cell is expected."),
    "economic_status": (
        "Descriptive only: economic_status has no hard Kreis target (modelled "
        "from hhtype x region Bayes), so deviations reflect the model, not a fit."),
}
_GENERIC_SMALL_CELL = ("Deviation correlates with small cell counts -> likely "
                       "sampling noise; expected to shrink at a higher sampling rate.")
_GENERIC_BIAS = ("Same-sign deviation across cells -> a structural offset rather "
                 "than noise; check the realised attribute vs the control definition.")


def grade_for(mean_abs_delta_pp: float, bounds=DEFAULT_GRADE_BOUNDS_PP) -> str:
    for label, bound in zip(GRADE_LABELS, bounds):
        if mean_abs_delta_pp < bound:
            return label
    return GRADE_LABELS[-1]


def cause_hint(control_name: str, same_sign_bias: bool, small_cell_correlation: bool) -> str:
    parts = []
    if control_name in CAUSE_HINTS:
        parts.append(CAUSE_HINTS[control_name])
    if small_cell_correlation:
        parts.append(_GENERIC_SMALL_CELL)
    if same_sign_bias:
        parts.append(_GENERIC_BIAS)
    return " ".join(parts) if parts else ""


def _r2(synth: np.ndarray, target: np.ndarray) -> float:
    ss_res = float(np.sum((synth - target) ** 2))
    ss_tot = float(np.sum((target - np.mean(target)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan


def assess(long: pd.DataFrame, grade_bounds=DEFAULT_GRADE_BOUNDS_PP) -> pd.DataFrame:
    """Per-control fit measures + grade + cause hint. Empty in -> empty out."""
    if long.empty:
        return pd.DataFrame()
    rows = []
    for (control, family), sub in long.groupby(["control", "family"]):
        synth = sub["synthetic_count"].to_numpy(dtype=float)
        target = sub["target_count"].to_numpy(dtype=float)
        dp = sub["delta_pp"].to_numpy(dtype=float)
        rmse = float(np.sqrt(np.mean((synth - target) ** 2)))
        mean_target = float(np.mean(target))
        tae = float(np.sum(np.abs(synth - target)))
        if len(sub) >= 3 and np.std(target) > 0:
            with np.errstate(divide="ignore"):
                inv = 1.0 / np.sqrt(np.where(target > 0, target, np.nan))
            mask = ~np.isnan(inv)
            corr = (float(np.corrcoef(np.abs(dp)[mask], inv[mask])[0, 1])
                    if mask.sum() >= 3 else np.nan)
        else:
            corr = np.nan
        mean_abs_dp = float(np.mean(np.abs(dp)))
        same_sign = bool(np.all(dp >= 0) or np.all(dp <= 0)) and len(sub) > 1
        rows.append({
            "control": control, "family": family, "n_cells": int(len(sub)),
            "srmse": rmse / mean_target if mean_target > 0 else np.nan,
            "tae": tae,
            "rae": tae / float(np.sum(target)) if np.sum(target) > 0 else np.nan,
            "coverage_5pp": float(np.mean(np.abs(dp) <= 5.0)),
            "coverage_10pp": float(np.mean(np.abs(dp) <= 10.0)),
            "r2": _r2(synth, target),
            "mean_abs_delta_pp": mean_abs_dp,
            "grade": grade_for(mean_abs_dp, grade_bounds),
            "cause_hint": cause_hint(control, same_sign, bool(corr and corr > 0.5)),
        })
    return pd.DataFrame(rows).sort_values("mean_abs_delta_pp").reset_index(drop=True)


def family_scores(quality: pd.DataFrame) -> pd.DataFrame:
    """Mean SRMSE + mean |delta_pp| per family (the headline roll-up)."""
    if quality.empty:
        return pd.DataFrame()
    return (quality.groupby("family")
            .agg(mean_srmse=("srmse", "mean"),
                 mean_abs_delta_pp=("mean_abs_delta_pp", "mean"),
                 n_controls=("control", "count"))
            .reset_index())
