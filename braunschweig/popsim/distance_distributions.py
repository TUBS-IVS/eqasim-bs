"""synpp stage: secondary distance distributions from MiD 2023 Wege survey (bug D3).

Aliased to synthesis.population.spatial.secondary.distance_distributions in the
popsim_mid workflow. Builds the IDENTICAL output structure as the default ENTD-based
stage (synthesis/population/spatial/secondary/distance_distributions.py) but uses
the German MiD 2023 Wege survey instead of the French ENTD, so secondary activities
in popsim_mid are placed at German empirical distances rather than French ones.

Output structure (consumed by synthesis/population/spatial/secondary/components.py
CustomDistanceSampler, indexed as distributions[mode]["bounds"] and
distributions[mode]["distributions"][bound_index]["cdf"/"values"/"weights"]):

    {
        mode: {
            "bounds": np.ndarray,        # quantile travel_time bin upper-bounds;
                                         # last element is np.inf
            "distributions": [           # one per travel_time bin
                {
                    "cdf":     np.ndarray,  # cumulative weight, ending at 1.0
                    "values":  np.ndarray,  # euclidean_distance in metres, sorted
                    "weights": np.ndarray,  # W_GEW trip weight per observation
                },
                ...
            ],
        },
        ...
    }

The calculate_bounds helper is imported directly from the default stage so that the
quantile-binning logic is identical (not re-implemented).

MiD distance derivation:
    euclidean_distance_m = wegkm_imp [km] * 1000 / DETOUR_FACTOR

where DETOUR_FACTOR = 1.3 (the same ENTD detour factor used throughout this project:
braunschweig/popsim/trips_stage.py, synthesis/population/trips.py, and the MiD
school-distance calibration). This converts the MiD imputed routed trip length to a
straight-line distance in metres, consistent with the units expected by the RDA solver.

Travel time derivation:
    travel_time_s = arrival_time_s - departure_time_s

where arrival_time and departure_time are seconds since midnight built from the MiD
time columns W_AZS/W_AZM and W_SZS/W_SZM via braunschweig.popsim.trips.mid_time_seconds.

Trip weight:
    W_GEW  -- MiD Wege-Gewicht (Fallzahl-normalised expansion weight; mean ~1.0).
    Source: MiD 2023 Handbuch, Kap. 6.1-6.2 (Tab. weight reference table).
    W_GEW = person weight x Hebefaktor (covers mobile non-reporters + excess trips).
    This is the direct analog of person_weight in the ENTD stage.

Trip selection filter (identical to the default stage):
    Trips where BOTH preceding_purpose AND following_purpose are primary activities
    (home, work, education) are excluded. Trips between a primary and a secondary
    location (or two different secondary locations) are included. This matches the
    filter in synthesis/population/spatial/secondary/distance_distributions.py
    lines 43-47 exactly.

Activity purposes:
    purpose (following_purpose) is mapped from MiD W_ZWECK via
    braunschweig.popsim.trips.map_purpose. preceding_purpose is derived by
    the diary-starts-at-home convention (first trip departs from home; each
    subsequent trip's preceding = previous following), matching the same
    convention in braunschweig.popsim.trips.build_trip_table.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

# Import the helper directly from the default stage so that the binning logic
# is identical. This is the only function we need from it (no CDF helpers are
# separately defined there; the CDF is computed inline via cumsum).
from synthesis.population.spatial.secondary.distance_distributions import (
    calculate_bounds,
)

from braunschweig.popsim.trips import map_mode, map_purpose, mid_time_seconds

logger = logging.getLogger(__name__)

# Straight-line detour factor: routed_km / straight-line_km.
# Source: eqasim ENTD processing; used consistently throughout the project.
DETOUR_FACTOR = 1.3

# Primary activity types — trips where BOTH ends are primary are excluded.
# Matches the default stage exactly (synthesis/population/spatial/secondary/
# distance_distributions.py, line 43).
PRIMARY_ACTIVITIES = frozenset(["home", "work", "education"])

# Bin size: number of travel-time observations per quantile bin.
# Must match the default stage (line 51: bin_size = 200).
BIN_SIZE = 200

# MiD columns required to build the distributions.
REQUIRED_COLUMNS = ("H_ID", "P_ID", "W_ID", "W_ZWECK", "hvm_imp",
                    "wegkm_imp", "W_SZS", "W_SZM", "W_AZS", "W_AZM", "W_GEW")


def _build_preceding_purpose(wege: pd.DataFrame) -> pd.Series:
    """Derive preceding_purpose from following_purpose per (H_ID, P_ID) chain.

    Mirrors build_trip_table's diary-starts-at-home convention: the first trip
    of each person departs from home; each subsequent trip departs from the
    destination of the previous trip.

    Parameters
    ----------
    wege:
        DataFrame with ``H_ID``, ``P_ID``, ``W_ID`` (trip sequence), and
        ``following_purpose`` (eqasim activity at the trip destination).

    Returns
    -------
    pd.Series
        ``preceding_purpose`` aligned to the wege index.
    """
    # Sort within (H_ID, P_ID) by W_ID to get the diary order.
    sorted_idx = wege.sort_values(["H_ID", "P_ID", "W_ID"]).index
    sorted_wege = wege.loc[sorted_idx]

    preceding = (
        sorted_wege
        .groupby(["H_ID", "P_ID"])["following_purpose"]
        .shift(1)
    )
    # First trip of each person departs from home.
    is_first = sorted_wege.groupby(["H_ID", "P_ID"]).cumcount() == 0
    preceding.loc[is_first] = "home"

    # Re-align to the original index order.
    return preceding.reindex(wege.index)


def run(mid_wege: pd.DataFrame) -> dict:
    """Build secondary distance distributions from the MiD 2023 Wege survey.

    This is the pure computational core, factored out of execute() so that
    tests can call it directly without a synpp context.

    Parameters
    ----------
    mid_wege:
        MiD 2023 Wege DataFrame with at least the columns in REQUIRED_COLUMNS.
        All rows are used; caller is responsible for any pre-filtering (e.g.
        restricting to a specific Bundesland). The weight column W_GEW is the
        MiD Wege-Gewicht (Fallzahl-normalised expansion weight).

    Returns
    -------
    dict
        Per-mode distance distributions in the EXACT structure consumed by
        CustomDistanceSampler (see module docstring).
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in mid_wege.columns]
    if missing:
        raise ValueError(
            f"[popsim.distance_distributions] MiD Wege frame is missing "
            f"required columns: {missing}. "
            f"Available columns: {list(mid_wege.columns[:20])} ..."
        )

    df = mid_wege.copy()

    # --- Step 1: map mode and purpose from MiD codes. ----------------------
    df = map_mode(map_purpose(df))
    # following_purpose = destination activity.
    df["following_purpose"] = df["purpose"]

    # --- Step 2: derive preceding_purpose (diary-starts-at-home). ----------
    df["preceding_purpose"] = _build_preceding_purpose(df)

    # --- Step 3: compute travel_time in seconds. ----------------------------
    df["departure_time"] = mid_time_seconds(df, "W_SZS", "W_SZM")
    df["arrival_time"] = mid_time_seconds(df, "W_AZS", "W_AZM")
    df["travel_time"] = df["arrival_time"] - df["departure_time"]

    # Repair negative travel_time from midnight crossing: add 24 h.
    midnight_cross = df["travel_time"] < 0
    df.loc[midnight_cross, "travel_time"] += 24 * 3600

    # Clamp to positive (zero-duration trips are kept; negative after repair
    # cannot occur but guard against data errors).
    df = df[df["travel_time"] >= 0].copy()

    # --- Step 4: compute euclidean_distance in metres from wegkm_imp. ------
    # wegkm_imp is the MiD imputed routed trip length in kilometres.
    # Dividing by DETOUR_FACTOR gives the straight-line distance in km; x1000 -> m.
    df["distance"] = df["wegkm_imp"].astype(float) * 1000.0 / DETOUR_FACTOR

    # --- Step 5: select columns needed and filter primary-only trips. ------
    df = df[["mode", "travel_time", "distance", "W_GEW",
             "preceding_purpose", "following_purpose"]].rename(
        columns={"W_GEW": "weight"}
    )

    # Replicate the default stage filter exactly (lines 43-47):
    # exclude trips where BOTH ends are primary activities.
    is_primary_both = (
        df["preceding_purpose"].isin(PRIMARY_ACTIVITIES) &
        df["following_purpose"].isin(PRIMARY_ACTIVITIES)
    )
    n_before = len(df)
    df = df[~is_primary_both]
    n_after = len(df)
    n_excluded = n_before - n_after

    logger.info(
        "[popsim.distance_distributions] total trips: %d; "
        "primary-only excluded: %d (%.1f%%); secondary included: %d",
        n_before, n_excluded,
        100.0 * n_excluded / n_before if n_before > 0 else 0.0,
        n_after,
    )

    # --- Step 6: build per-mode quantile travel_time bins + CDFs. ----------
    # Mirrors the default stage execute() logic exactly (lines 54-78).
    modes = df["mode"].unique()
    distributions = {}

    n_modes_built = 0
    for mode in modes:
        f_mode = df["mode"] == mode
        mode_df = df[f_mode]

        # Quantile bin bounds from travel_time values (same as default).
        bounds = calculate_bounds(mode_df["travel_time"].values, BIN_SIZE)
        distributions[mode] = dict(bounds=np.array(bounds), distributions=[])

        # Per-bin weighted CDF of euclidean_distance (same logic as default,
        # but using W_GEW weights instead of person_weight).
        for lower_bound, upper_bound in zip([-np.inf] + bounds[:-1], bounds):
            f_bound = (
                (mode_df["travel_time"] > lower_bound) &
                (mode_df["travel_time"] <= upper_bound)
            )
            bin_df = mode_df[f_bound]

            values = bin_df["distance"].values
            weights = bin_df["weight"].values

            sorter = np.argsort(values)
            values = values[sorter]
            weights = weights[sorter]

            cdf = np.cumsum(weights)
            cdf /= cdf[-1]

            distributions[mode]["distributions"].append(
                dict(cdf=cdf, values=values, weights=weights)
            )

        n_modes_built += 1

    logger.info(
        "[popsim.distance_distributions] built distributions for %d modes: %s",
        n_modes_built,
        sorted(distributions.keys()),
    )

    return distributions


def configure(context):
    """Declare stage dependencies: MiD Wege path + random_seed from config."""
    context.config("braunschweig.population.popsim.mid_dir")
    # random_seed is not consumed here (the default stage also does not use one)
    # but we declare it for consistent config validation across popsim stages.
    context.config("random_seed")


def execute(context):
    """Load MiD Wege and build secondary distance distributions.

    Returns the same structure as the default
    synthesis.population.spatial.secondary.distance_distributions stage so that
    synthesis.population.spatial.secondary.locations (and CustomDistanceSampler)
    can consume it without modification.
    """
    from braunschweig.popsim import mid as mid_module

    mid_dir = context.config("braunschweig.population.popsim.mid_dir")
    logger.info(
        "[popsim.distance_distributions] loading MiD Wege from %s", mid_dir
    )
    mid_wege = mid_module.load_mid_wege(mid_dir)
    logger.info(
        "[popsim.distance_distributions] loaded %d MiD trips", len(mid_wege)
    )
    return run(mid_wege)
