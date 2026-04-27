"""Calibration diagnostics — OD fit, per-Kreis HH-size χ², regression guard.

Purpose: provide quantitative before/after measurements for the calibration
refactor cycle (R-A gravity, R-C household size, R-D purpose-remap). Results
are surfaced in the validation HTML report (section 7) and the JSON summary.

All references go through `metrics.commute_od_kreis()`, `io.load_households()`
and `references.load_zensus_households()`. No new data files needed.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from . import io, metrics, references
from .config import SAMPLING_RATE, ZGB8


# ---------------------------------------------------------------------------
# OD fit — synth vs BA Pendleratlas (Kreis-pair)
# ---------------------------------------------------------------------------
def od_fit_stats(top_n: int = 200) -> tuple[pd.DataFrame, dict]:
    """Return (top_n flow table, fit-statistics dict).

    The flow table is restricted to Kreis-pairs where the BA observation
    is non-zero. Statistics are computed on the **expanded** synth flow
    (sample / SAMPLING_RATE) vs the BA flow.
    """
    od = metrics.commute_od_kreis().copy()
    od = od[od["ba_flow"] > 0].copy()
    od = od.sort_values("ba_flow", ascending=False).head(top_n)

    obs = od["ba_flow"].to_numpy(dtype=float)
    pred = od["synth_flow_expanded"].to_numpy(dtype=float)

    # Pearson R² (squared correlation), RMSE, MAPE.
    if len(obs) >= 2 and obs.std() > 0 and pred.std() > 0:
        r = float(np.corrcoef(obs, pred)[0, 1])
        r2 = r * r
    else:
        r2 = float("nan")
    rmse = float(np.sqrt(np.mean((pred - obs) ** 2)))
    with np.errstate(divide="ignore", invalid="ignore"):
        mape = float(np.mean(np.abs(pred - obs) / np.where(obs > 0, obs, np.nan))) * 100

    # Bias = mean(pred - obs) / mean(obs).
    bias_pct = float((pred.mean() - obs.mean()) / obs.mean() * 100) if obs.mean() > 0 else float("nan")

    stats = {
        "n_pairs": int(len(od)),
        "r2": r2,
        "rmse": rmse,
        "mape_pct": mape,
        "bias_pct": bias_pct,
        "ba_total": float(obs.sum()),
        "synth_total": float(pred.sum()),
    }
    return od, stats


def od_top_outbound(top_n: int = 20) -> pd.DataFrame:
    """Top outbound flows ZGB → external Kreis (Synth vs BA)."""
    od = metrics.commute_od_kreis().copy()
    out = od[od["orig_ars"].isin(ZGB8) & ~od["dest_ars"].isin(ZGB8)].copy()
    out = out[out["ba_flow"] > 0]
    return out.sort_values("ba_flow", ascending=False).head(top_n)


def od_top_inbound(top_n: int = 20) -> pd.DataFrame:
    """Top inbound flows external → ZGB (Synth vs BA)."""
    od = metrics.commute_od_kreis().copy()
    inb = od[~od["orig_ars"].isin(ZGB8) & od["dest_ars"].isin(ZGB8)].copy()
    inb = inb[inb["ba_flow"] > 0]
    return inb.sort_values("ba_flow", ascending=False).head(top_n)


# ---------------------------------------------------------------------------
# HH-size per-Kreis fit — synth vs Zensus
# ---------------------------------------------------------------------------
def _chi_square(observed: np.ndarray, expected: np.ndarray) -> tuple[float, int]:
    """Pearson χ² statistic and degrees of freedom.

    NaN-safe: cells with expected == 0 are dropped (degenerate). Returns
    (statistic, dof). p-value is not computed (no scipy dependency); the
    statistic itself is the comparison metric used here.
    """
    mask = expected > 0
    o = observed[mask].astype(float)
    e = expected[mask].astype(float)
    if len(o) == 0:
        return float("nan"), 0
    chi = float(np.sum((o - e) ** 2 / e))
    dof = max(int(mask.sum()) - 1, 1)
    return chi, dof


def hh_size_fit_per_kreis() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (per-Kreis χ² table, long-format synth vs Zensus shares)."""
    hh = metrics.household_size_per_kreis().copy()
    # Per-Kreis pivot of synth vs Zensus shares.
    pivot = hh.pivot_table(
        index="ars5", columns="size_bin",
        values=["synth_share", "zensus_share"],
        aggfunc="first", fill_value=0.0,
    )
    rows = []
    # Total synth households per Kreis to scale the χ² counts properly.
    synth_counts = hh.groupby("ars5")["synth_count"].sum().rename("n").to_dict()

    for ars5 in pivot.index:
        n = synth_counts.get(ars5, 0)
        if n <= 0 or ars5 not in ZGB8:
            continue
        synth_shares = pivot.loc[ars5, "synth_share"].to_numpy(dtype=float)
        zensus_shares = pivot.loc[ars5, "zensus_share"].to_numpy(dtype=float)
        observed = synth_shares * n
        expected = zensus_shares * n
        chi, dof = _chi_square(observed, expected)
        # Total absolute deviation in percentage points.
        tvd = float(0.5 * np.sum(np.abs(synth_shares - zensus_shares)) * 100)
        rows.append({
            "ars5": ars5,
            "kreis_name": ZGB8.get(ars5, ars5),
            "n_synth_hh": int(n),
            "chi2": chi,
            "dof": dof,
            "tvd_pp": tvd,
        })
    summary = pd.DataFrame(rows).sort_values("tvd_pp", ascending=False).reset_index(drop=True)
    return summary, hh


# ---------------------------------------------------------------------------
# Purpose remap (H1 fix preview)
# ---------------------------------------------------------------------------
def purpose_mix_remapped() -> pd.DataFrame:
    """Deprecated alias of :func:`metrics.purpose_mix` (raw ENTD purposes).

    The H1/R-D ``home -> preceding_purpose`` remap was removed: the model
    operates on ENTD chain semantics where each return-home leg is a real
    trip ending at ``home``. This function is kept for backward
    compatibility (JSON key ``purpose_mix_remapped`` and existing notebook
    cells) and now returns the same frame as ``metrics.purpose_mix``.
    """
    return metrics.purpose_mix()


# ---------------------------------------------------------------------------
# Regression guard — JSON-driven KPI tolerance check
# ---------------------------------------------------------------------------
KPI_TOLERANCES: dict[str, tuple[str, float]] = {
    # key: (description, abs tolerance)
    "population_total_pct":     ("ZGB-8 population vs Zensus 2022 (% deviation)", 2.0),
    "trips_per_person_pct":     ("Trips/person vs MiD (% deviation)", 10.0),
    "mean_trip_distance_km":    ("Mean trip distance |Δ km|", 5.0),
    "mode_share_miv_pp":        ("MIV share |Δ pp|", 5.0),
    "mode_share_oev_pp":        ("ÖV share |Δ pp|", 5.0),
    # bicycle/walk are known residuals (R-E deferred) — wide tolerance.
    "mode_share_rad_pp":        ("Bike share |Δ pp| (R-E deferred)", 12.0),
    "mode_share_fuss_pp":       ("Walk share |Δ pp| (R-E deferred)", 12.0),
    "od_top200_r2":             ("OD top-200 R² (lower bound)", 0.85),
}


def regression_guard_status(report_json: dict, od_stats: dict) -> pd.DataFrame:
    """Compare current run against the configured tolerances.

    Returns a DataFrame with columns: kpi, value, tolerance, status (ok/fail).
    """
    from .config import MID_BASELINE
    pop_total = next(r for r in report_json["population"] if r["ars5"] == "TOTAL")
    summary = report_json["trip_summary"]

    mode = {row["mode"]: row for row in report_json["mode_share"]}

    rows = [
        {"kpi": "population_total_pct",
         "value": abs(pop_total["deviation_pct"]),
         "tolerance": KPI_TOLERANCES["population_total_pct"][1]},
        {"kpi": "trips_per_person_pct",
         "value": abs(summary["trips_per_person"] / MID_BASELINE["trips_per_person"] - 1) * 100,
         "tolerance": KPI_TOLERANCES["trips_per_person_pct"][1]},
        {"kpi": "mean_trip_distance_km",
         "value": abs(summary["mean_distance_km"] - MID_BASELINE["mean_trip_distance_km"]),
         "tolerance": KPI_TOLERANCES["mean_trip_distance_km"][1]},
        {"kpi": "mode_share_miv_pp",
         "value": abs(mode.get("miv", {"deviation_pp": 0})["deviation_pp"]),
         "tolerance": KPI_TOLERANCES["mode_share_miv_pp"][1]},
        {"kpi": "mode_share_oev_pp",
         "value": abs(mode.get("oev", {"deviation_pp": 0})["deviation_pp"]),
         "tolerance": KPI_TOLERANCES["mode_share_oev_pp"][1]},
        {"kpi": "mode_share_rad_pp",
         "value": abs(mode.get("rad", {"deviation_pp": 0})["deviation_pp"]),
         "tolerance": KPI_TOLERANCES["mode_share_rad_pp"][1]},
        {"kpi": "mode_share_fuss_pp",
         "value": abs(mode.get("fuss", {"deviation_pp": 0})["deviation_pp"]),
         "tolerance": KPI_TOLERANCES["mode_share_fuss_pp"][1]},
        {"kpi": "od_top200_r2",
         "value": od_stats.get("r2", float("nan")),
         "tolerance": KPI_TOLERANCES["od_top200_r2"][1]},
    ]
    df = pd.DataFrame(rows)
    df["description"] = df["kpi"].map(lambda k: KPI_TOLERANCES[k][0])
    # R² is "higher is better"; everything else is "lower is better".
    df["status"] = np.where(
        df["kpi"] == "od_top200_r2",
        np.where(df["value"] >= df["tolerance"], "ok", "fail"),
        np.where(df["value"] <= df["tolerance"], "ok", "fail"),
    )
    return df[["kpi", "description", "value", "tolerance", "status"]]
