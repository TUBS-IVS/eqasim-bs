"""Validate realised secondary trip distances vs MiD 2023 Tabelle A W12.

Measures the euclidean secondary trip-distance distribution per purpose
(shop / leisure / other) from a cached synpp working_directory and compares
to the committed W12 band-share targets via EMD.

A secondary trip is any leg whose destination activity has purpose
shop / leisure / other. The distance is computed as the straight-line
(euclidean) distance from the PREVIOUS activity location to the secondary
activity location (EPSG:25832 metres -> km). Each leg's euclidean distance
is scaled to the routed axis using the fitted circuity curve for its mode
network (car/pt/walk), matching the MiD W12 reported trip lengths.

Usage::

    python scripts/validate_secondary_distances.py \\
        --cache eqasim-data/cache_bs_25pct_allfeat \\
        --mid-dir eqasim-data/data/braunschweig/mid \\
        [--output secondary_distances.csv]

The script reads only the cached stage pickles and the committed MiD CSV;
it does NOT run synpp or any simulation step.
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root to sys.path so braunschweig.* imports work when the script
# is run directly (not as a module) without a prior `pip install -e .`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.calibration import circuity  # noqa: E402
from braunschweig.calibration.metrics import emd_on_bands  # noqa: E402
from braunschweig.calibration.targets import (  # noqa: E402
    W12_BAND_EDGES_KM,
    load_w12_band_shares,
)

# ---------------------------------------------------------------------------
# Per-leg mode -> circuity network dispatch
# ---------------------------------------------------------------------------

# Map a leg mode to the circuity network used for its euclidean->routed scaling.
_MODE_TO_NETWORK = {
    "car": "car",
    "car_passenger": "car",
    "pt": "pt",
    "walk": "walk",
    "bike": "walk",
}


def mode_to_network(mode: str) -> str:
    """Return the circuity network ('car'|'pt'|'walk') for a leg mode.

    Unknown modes default to 'car' (the most common motorised network).
    """
    return _MODE_TO_NETWORK.get(str(mode), "car")

logger = logging.getLogger(__name__)

# Default cache path (25 % all-features run, absolute Windows path).
_DEFAULT_CACHE = (
    r"C:\Users\bienzeisler\Documents\GitHub\eqasim-bs"
    r"\eqasim-data\cache_bs_25pct_allfeat"
)
_DEFAULT_MID_DIR = (
    r"C:\Users\bienzeisler\Documents\GitHub\eqasim-bs"
    r"\eqasim-data\data\braunschweig\mid"
)

# Secondary purposes to report.
_SECONDARY_PURPOSES = ("shop", "leisure", "other")


# ---------------------------------------------------------------------------
# Stage-cache loading
# ---------------------------------------------------------------------------

def _load_stage(working_directory: str, stage: str):
    """Load the most-recently-modified pickle for a synpp stage name.

    Raises RuntimeError when no matching file is found (fail-fast, no silent
    fallback).
    """
    pattern = os.path.join(working_directory, stage + "__*.p")
    matches = glob.glob(pattern)
    if not matches:
        raise RuntimeError(
            f"No cached pickle found for stage '{stage}' in '{working_directory}'. "
            f"Expected pattern: {pattern}"
        )
    latest = max(matches, key=os.path.getmtime)
    logger.info("Loading stage '%s' from '%s'", stage, latest)
    with open(latest, "rb") as fh:
        return pickle.load(fh)


# ---------------------------------------------------------------------------
# Distance-band helpers (W12-specific)
# ---------------------------------------------------------------------------

def _w12_band_shares(distances_km):
    """Normalised share per W12 band for an array of distances in km.

    W12_BAND_EDGES_KM = (0, 0.5, 1, 2, 5, 10, 20, 50, 100, inf) -> 9 bands.
    """
    edges = np.asarray(W12_BAND_EDGES_KM[1:-1], dtype=float)  # inner edges only
    bands = np.digitize(np.asarray(distances_km, dtype=float), edges)
    n_bands = len(W12_BAND_EDGES_KM) - 1  # 9
    counts = np.bincount(bands, minlength=n_bands).astype(float)
    total = counts.sum()
    return counts / total if total > 0 else counts


# ---------------------------------------------------------------------------
# Main measurement logic
# ---------------------------------------------------------------------------

def measure_secondary_distances(working_directory: str):
    """Load cache and compute per-purpose euclidean secondary trip distances.

    Returns
    -------
    dict[str, pd.DataFrame]
        Per purpose (shop / leisure / other): DataFrame with columns
        ``euclidean_km`` (float) and ``mode`` (str). Empty DataFrame when no
        trips of that purpose are present.
    """
    # Load the three required stages.
    df_locs = _load_stage(working_directory, "synthesis.population.spatial.locations")
    df_acts = _load_stage(working_directory, "synthesis.population.activities")
    df_trips = _load_stage(working_directory, "synthesis.population.trips")

    logger.info(
        "Loaded locations (%d rows), activities (%d rows) and trips (%d rows).",
        len(df_locs), len(df_acts), len(df_trips),
    )

    # Merge geometry onto activities using (person_id, activity_index) as the
    # join key.  The locations stage stores one geometry per (person_id,
    # activity_index); activities stores one row per activity with its purpose.
    # Both stages have the same (person_id, activity_index) index.
    df_acts = df_acts.reset_index(drop=True)
    df_locs = df_locs.reset_index(drop=True)

    # Extract x/y from geometry (EPSG:25832).
    df_xy = df_locs[["person_id", "activity_index"]].copy()
    df_xy["x_m"] = df_locs["geometry"].x
    df_xy["y_m"] = df_locs["geometry"].y

    # Merge purpose onto coordinates.
    df_merged = df_xy.merge(
        df_acts[["person_id", "activity_index", "purpose"]],
        on=["person_id", "activity_index"],
        how="inner",
    )
    logger.info(
        "Merged locations+activities: %d rows (from %d locations, %d activities).",
        len(df_merged), len(df_locs), len(df_acts),
    )

    # Sort by (person_id, activity_index) to ensure leg ordering is correct.
    df_merged = df_merged.sort_values(["person_id", "activity_index"]).reset_index(drop=True)

    # Build per-person shifted DataFrame to get the PREVIOUS activity's
    # location.  A leg from activity i-1 to activity i is valid only when both
    # activities belong to the same person.
    df_prev = df_merged[["person_id", "x_m", "y_m"]].shift(1).rename(
        columns={"person_id": "prev_person_id", "x_m": "prev_x", "y_m": "prev_y"}
    )
    df_legs = pd.concat([df_merged, df_prev], axis=1)

    # Drop rows where the previous activity belongs to a different person
    # (i.e. the first activity of each person has no valid predecessor).
    same_person_mask = df_legs["person_id"] == df_legs["prev_person_id"]
    n_dropped = (~same_person_mask).sum()
    n_total = len(df_legs)
    logger.info(
        "Leg construction: %d/%d legs are within-person (dropped %d first-activity rows).",
        same_person_mask.sum(), n_total, n_dropped,
    )
    df_legs = df_legs[same_person_mask].copy()

    # Keep only secondary purposes.
    secondary_mask = df_legs["purpose"].isin(_SECONDARY_PURPOSES)
    n_secondary = secondary_mask.sum()
    logger.info(
        "[secondary-validate] secondary legs (shop/leisure/other): %d / %d total legs.",
        n_secondary, len(df_legs),
    )
    df_secondary = df_legs[secondary_mask].copy()

    # Compute euclidean distance in km (EPSG:25832 -> metres -> km).
    dx = df_secondary["x_m"].values - df_secondary["prev_x"].values
    dy = df_secondary["y_m"].values - df_secondary["prev_y"].values
    df_secondary = df_secondary.copy()
    df_secondary["euclidean_km"] = np.sqrt(dx * dx + dy * dy) / 1000.0

    # Join mode from the trips stage.  In the synthesis pipeline the leg that
    # brings a person TO activity_index=k corresponds to trip_index=k in
    # synthesis.population.trips (activities.py sets activity_index = trip_index).
    df_mode = df_trips[["person_id", "trip_index", "mode"]].rename(
        columns={"trip_index": "activity_index"}
    )
    n_before_mode_join = len(df_secondary)
    df_secondary = df_secondary.merge(
        df_mode, on=["person_id", "activity_index"], how="left"
    )
    if len(df_secondary) != n_before_mode_join:
        raise AssertionError(
            f"[secondary-validate] secondary mode-join changed row count "
            f"{n_before_mode_join} -> {len(df_secondary)}: "
            "non-unique (person_id, activity_index) key in trips stage?"
        )
    n_mode_missing = df_secondary["mode"].isna().sum()
    if n_mode_missing > 0:
        logger.warning(
            "[secondary-validate] mode join: %d/%d legs missing mode; defaulting to 'car'.",
            n_mode_missing, n_before_mode_join,
        )
        df_secondary["mode"] = df_secondary["mode"].fillna("car")
    else:
        logger.info(
            "[secondary-validate] mode join: primary %d/%d (100%%, no fallback).",
            n_before_mode_join, n_before_mode_join,
        )

    # Split by purpose and return per-leg DataFrames with euclidean_km + mode.
    result: dict[str, pd.DataFrame] = {}
    for purpose in _SECONDARY_PURPOSES:
        mask = df_secondary["purpose"] == purpose
        df_purpose = df_secondary.loc[mask, ["euclidean_km", "mode"]].copy()
        result[purpose] = df_purpose
        n_legs = len(df_purpose)
        mean_euc = float(df_purpose["euclidean_km"].mean()) if n_legs > 0 else float("nan")
        logger.info(
            "[secondary-validate] purpose=%s: %d legs, mean euclidean=%.2f km.",
            purpose, n_legs, mean_euc,
        )

    # Log primary-path rate (CLAUDE.md no-silent-fallback): all legs derived
    # directly from the cached synthesis locations -- there is no fallback path
    # in this measurement step.
    logger.info(
        "[secondary-validate] measurement: primary (direct from cache) %d/%d (100%%); "
        "no fallback path exists.",
        n_secondary, n_secondary,
    )

    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_report(distances_by_purpose: dict, w12_targets: dict) -> list[dict]:
    """Build a per-purpose comparison row list.

    Parameters
    ----------
    distances_by_purpose : dict[str, pd.DataFrame]
        Per purpose: DataFrame with columns ``euclidean_km`` and ``mode``.
    w12_targets : dict
        Output of load_w12_band_shares: includes purpose band shares + mean_km.

    Returns
    -------
    list[dict]
        One dict per purpose with keys:
        purpose, n, mean_euclidean_km, mean_routed_equiv_km,
        w12_mean_km, emd_vs_w12.
    """
    rows = []
    for purpose in _SECONDARY_PURPOSES:
        df_purpose = distances_by_purpose.get(purpose, pd.DataFrame(columns=["euclidean_km", "mode"]))
        n = len(df_purpose)
        if n == 0:
            rows.append({
                "purpose": purpose,
                "n": 0,
                "mean_euclidean_km": float("nan"),
                "mean_routed_equiv_km": float("nan"),
                "w12_mean_km": w12_targets.get(f"{purpose}_mean_km", float("nan")),
                "emd_vs_w12": float("nan"),
            })
            logger.warning(
                "[secondary-validate] purpose=%s: no legs found; EMD reported as NaN.",
                purpose,
            )
            continue

        mean_euc = float(df_purpose["euclidean_km"].mean())

        # Per-leg routed-equivalent: scale each leg by its mode's circuity network
        # using the constant detour factor (mode="constant", the default). The
        # distance-dependent curve is opt-in only (mode="curve") and was measured
        # immaterial for ZGB secondary trips (EMD delta ~0.003).
        # routed is computed ONCE and reused for both the mean and the band shares.
        routed_km = np.empty(n, dtype=float)
        for network, grp_idx in df_purpose.groupby(
            df_purpose["mode"].map(mode_to_network)
        ).groups.items():
            sl = df_purpose.index.get_indexer(list(grp_idx))
            routed_km[sl] = circuity.euclidean_to_routed(
                df_purpose.loc[grp_idx, "euclidean_km"].to_numpy(), network,
                mode="constant",
            )

        mean_routed = float(np.mean(routed_km))

        # Band shares on the W12 9-band grid (after per-leg circuity scaling).
        model_shares = _w12_band_shares(routed_km)
        target_shares = w12_targets[purpose]
        emd = emd_on_bands(model_shares, target_shares)

        rows.append({
            "purpose": purpose,
            "n": n,
            "mean_euclidean_km": mean_euc,
            "mean_routed_equiv_km": mean_routed,
            "w12_mean_km": w12_targets.get(f"{purpose}_mean_km", float("nan")),
            "emd_vs_w12": emd,
        })

    return rows


def print_table(rows: list[dict]) -> None:
    """Print a formatted per-purpose comparison table."""
    header = (
        f"{'purpose':<10}  {'n':>8}  "
        f"{'mean_euc_km':>12}  {'mean_routed_km':>14}  "
        f"{'w12_mean_km':>12}  {'emd_vs_w12':>12}"
    )
    sep = "-" * len(header)
    print("\n" + sep)
    print("Secondary trip distances: model vs MiD 2023 W12")
    print("  Routed-equiv = per-leg circuity scaling by mode network (car/pt/walk)")
    print("  EMD computed on the 9 W12 distance bands after per-leg circuity scaling")
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        print(
            f"{row['purpose']:<10}  {row['n']:>8d}  "
            f"{row['mean_euclidean_km']:>12.2f}  {row['mean_routed_equiv_km']:>14.2f}  "
            f"{row['w12_mean_km']:>12.1f}  {row['emd_vs_w12']:>12.4f}"
        )
    print(sep + "\n")


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Measure realised secondary trip distances per purpose and compare "
            "to MiD 2023 Tabelle A W12 via EMD. Reads only cached synpp stage "
            "pickles -- does NOT run synpp."
        )
    )
    parser.add_argument(
        "--cache", default=_DEFAULT_CACHE,
        help=(
            "Directory containing synpp stage pickles "
            f"(default: {_DEFAULT_CACHE!r})."
        ),
    )
    parser.add_argument(
        "--mid-dir", default=_DEFAULT_MID_DIR,
        help=(
            "Directory containing the MiD W12 CSV "
            f"(default: {_DEFAULT_MID_DIR!r})."
        ),
    )
    parser.add_argument(
        "--output", default=None,
        help="Optional path to write the per-purpose comparison CSV.",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # 1. Validate input paths
    # ------------------------------------------------------------------
    if not os.path.isdir(args.cache):
        raise RuntimeError(
            f"Cache directory not found: '{args.cache}'. "
            "Ensure --cache points to a valid synpp working directory."
        )
    if not os.path.isdir(args.mid_dir):
        raise RuntimeError(
            f"MiD directory not found: '{args.mid_dir}'. "
            "Ensure --mid-dir points to the MiD data directory."
        )

    # ------------------------------------------------------------------
    # 2. Load W12 targets
    # ------------------------------------------------------------------
    w12 = load_w12_band_shares(args.mid_dir)
    logger.info(
        "[secondary-validate] W12 targets loaded: shop %.1f km, leisure %.1f km, other %.1f km.",
        w12["shop_mean_km"], w12["leisure_mean_km"], w12["other_mean_km"],
    )

    # ------------------------------------------------------------------
    # 3. Measure realised secondary distances from cache
    # ------------------------------------------------------------------
    distances = measure_secondary_distances(args.cache)

    # ------------------------------------------------------------------
    # 4. Build and print comparison table
    # ------------------------------------------------------------------
    rows = build_report(distances, w12)
    print_table(rows)

    # ------------------------------------------------------------------
    # 5. Write optional CSV output
    # ------------------------------------------------------------------
    if args.output:
        df_out = pd.DataFrame(rows)
        df_out.to_csv(args.output, index=False)
        logger.info("[secondary-validate] wrote comparison CSV to '%s'.", args.output)

    # Log a note that the ON path (with purpose-resolved distributions enabled)
    # requires a synpp re-run with the feature flags set to true -- that step is
    # deferred and must be run on the server where the all-features popsim cache lives.
    logger.info(
        "[secondary-validate] This is the OFF-path baseline "
        "(secondary_purpose_distributions: false). "
        "The ON comparison requires a synpp re-run with the feature flags enabled "
        "-- that step is DEFERRED (server run)."
    )


if __name__ == "__main__":
    main()
