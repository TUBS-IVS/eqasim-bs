"""Bootstrap confidence intervals for KPIs (TASK-006).

Per-Kreis household resampling with replacement, n_replicates = 200 by default.
Output: 2.5 / 50 / 97.5 percentiles for each scalar KPI and each mode/purpose
share. Resampling is stratified by Kreis (ars5) so within-Kreis HH structure
is preserved.

Vectorised: pre-aggregate per-HH count vectors, then bootstrap resamples
indices and sums vectors — no DataFrame concat in the loop.
"""
from __future__ import annotations

import logging
from typing import Dict, List

import numpy as np
import pandas as pd

from . import io

log = logging.getLogger(__name__)

DEFAULT_N_REPLICATES = 200
DEFAULT_SEED = 20260426
PERCENTILES = (2.5, 50.0, 97.5)

MODES = ("miv", "oev", "rad", "fuss")
PURPOSES = ("home", "work", "leisure", "shop", "education", "other")


def _build_hh_aggregates() -> pd.DataFrame:
    """Return per-HH aggregate frame: household_id, ars5, n_persons, counts/sums."""
    persons = io.persons_with_kreis()[["person_id", "household_id", "ars5"]].copy()
    persons["ars5"] = persons["ars5"].fillna("UNKNOWN").astype(str)
    n_persons_per_hh = persons.groupby("household_id").size().rename("n_persons")
    hh_kreis = (
        persons[["household_id", "ars5"]]
        .drop_duplicates("household_id")
        .set_index("household_id")
    )
    hh_meta = hh_kreis.join(n_persons_per_hh).reset_index()

    trips = io.trips_full().copy()
    trips["mid_mode"] = trips["mode"].map(io.map_mode)
    # Raw ENTD purpose semantics: trip purpose = activity at destination.
    trips["mid_purpose"] = trips["following_purpose"].astype(str)

    trips = trips.merge(
        persons[["person_id", "household_id"]], on="person_id", how="left"
    )

    # Per-HH base aggregates.
    base = trips.groupby("household_id").agg(
        n_trips=("distance_km", "size"),
        sum_distance_km=("distance_km", "sum"),
    )

    # Per-HH per-person daily distance summed → average per-HH numerator.
    daily = (
        trips.groupby(["household_id", "person_id"])["distance_km"].sum()
        .groupby("household_id").sum()
        .rename("daily_dist_sum")
    )
    base = base.join(daily, how="left")

    # Per-mode and per-purpose trip counts via crosstab.
    mode_ct = pd.crosstab(trips["household_id"], trips["mid_mode"]).reindex(
        columns=list(MODES), fill_value=0
    )
    mode_ct.columns = [f"trips_{m}" for m in MODES]
    purp_ct = pd.crosstab(trips["household_id"], trips["mid_purpose"]).reindex(
        columns=list(PURPOSES), fill_value=0
    )
    purp_ct.columns = [f"trips_purpose_{p}" for p in PURPOSES]

    base = base.join(mode_ct, how="left").join(purp_ct, how="left").fillna(0)
    merged = hh_meta.merge(base, on="household_id", how="left").fillna(0)
    return merged


def run(
    n_replicates: int = DEFAULT_N_REPLICATES,
    seed: int = DEFAULT_SEED,
) -> List[dict]:
    """Run per-Kreis HH bootstrap and return percentile records."""
    rng = np.random.RandomState(seed)
    merged = _build_hh_aggregates()

    feature_cols = (
        ["n_persons", "n_trips", "sum_distance_km", "daily_dist_sum"]
        + [f"trips_{m}" for m in MODES]
        + [f"trips_purpose_{p}" for p in PURPOSES]
    )
    matrix = merged[feature_cols].to_numpy(dtype=np.float64)
    idx = {c: i for i, c in enumerate(feature_cols)}

    kreis_groups: Dict[str, np.ndarray] = {
        k: g.index.to_numpy() for k, g in merged.groupby("ars5")
    }

    log.info(
        "Bootstrap: %d replicates over %d Kreise, %d HHs total",
        n_replicates, len(kreis_groups), len(merged),
    )

    samples: List[Dict[str, float]] = []
    for rep in range(n_replicates):
        chunks = [
            rng.choice(idxs, size=len(idxs), replace=True)
            for idxs in kreis_groups.values()
        ]
        sampled_idx = np.concatenate(chunks)
        s = matrix[sampled_idx].sum(axis=0)
        n_pers = s[idx["n_persons"]]
        n_trips = s[idx["n_trips"]]
        kpis: Dict[str, float] = {
            "trips_per_person": float(n_trips / n_pers) if n_pers > 0 else 0.0,
            "mean_distance_km": (
                float(s[idx["sum_distance_km"]] / n_trips) if n_trips > 0 else 0.0
            ),
            "daily_distance_km": (
                float(s[idx["daily_dist_sum"]] / n_pers) if n_pers > 0 else 0.0
            ),
        }
        for m in MODES:
            kpis[f"mode_share[{m}]"] = (
                float(s[idx[f"trips_{m}"]] / n_trips) if n_trips > 0 else 0.0
            )
        for p in PURPOSES:
            kpis[f"purpose_share[{p}]"] = (
                float(s[idx[f"trips_purpose_{p}"]] / n_trips) if n_trips > 0 else 0.0
            )
        samples.append(kpis)
        if (rep + 1) % 50 == 0:
            log.info("  bootstrap replicate %d/%d", rep + 1, n_replicates)

    df = pd.DataFrame(samples)
    records: List[dict] = []
    for col in df.columns:
        vals = df[col].to_numpy()
        p_lo, p_med, p_hi = np.percentile(vals, PERCENTILES)
        records.append({
            "kpi": col,
            "p2.5": float(p_lo),
            "p50": float(p_med),
            "p97.5": float(p_hi),
            "mean": float(np.mean(vals)),
            "std": float(np.std(vals, ddof=1)),
        })
    return records
