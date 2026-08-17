"""Calibrate the secondary chainsolvers scorer scale-alignment against MiD W12.

Coordinate-descent over {attr_transform, pot_weight, dist_dev_weight} on a cached
synpp working directory: per trial it re-solves secondary locations with the trial
Scorer, measures per-purpose (shop/leisure/other) realised band shares
(detour-adjusted), and computes the per-purpose W12 EMD (+ optional concentration
penalty).

Prints the winning YAML block to paste into the real configs. Does NOT modify any
config -- pinning is a manual, reviewed step after inspecting the report.

This script is intended to be run on the server where cache_bs_25pct_allfeat lives.
It is measure-first infrastructure: a winner is pinned ONLY if it beats the
byte-identical baseline (linear/1.0/1.0) on the summed per-purpose W12 EMD without
worsening any single purpose beyond its OFF baseline (shop 0.053, leisure 0.064,
other 0.018). If none beats the baseline, record the negative result and keep
current defaults.

Usage::

    python scripts/calibrate_secondary_scorer.py \\
        --working-directory eqasim-data/cache_bs_25pct_allfeat \\
        --config eqasim-data/cache_bs_25pct_allfeat_popsim/.merged_config.yml \\
        --conc-weight 1.0 \\
        --output-dir eqasim-data/data/braunschweig/calibration/secondary
    # (the composed run writes its exact resolved config there; see configs/base_bs.yml)

Step 7 of feature/smart-other-potential (server-side only; no pinning here).
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np

# Add project root so braunschweig.* imports work when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.calibration import circuity  # noqa: E402
from braunschweig.calibration.secondary import build_secondary_loss, coordinate_descent  # noqa: E402
from braunschweig.calibration.secondary_measurement import (  # noqa: E402
    mode_to_network,
    w12_band_shares as _w12_band_shares,
)
from braunschweig.calibration.targets import W12_BAND_EDGES_KM, load_w12_band_shares  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Grid definition (per brief)
# ---------------------------------------------------------------------------

_GRID = {
    "attr_transform":   ["linear", "log1p"],
    "pot_weight":       [0.5, 1.0, 2.0, 4.0],
    "dist_dev_weight":  [1.0, 1.0 / 100, 1.0 / 300, 1.0 / 1000],
}

_INIT = {
    "attr_transform":  "linear",
    "pot_weight":      1.0,
    "dist_dev_weight": 1.0,
}

# Secondary purposes reported.
_SECONDARY_PURPOSES = ("shop", "leisure", "other")

# OFF-path W12 EMD baselines (per-purpose; from OFF run on 25 % cache).
# These are the COMMITTED reference values to compare against.
# Source: CLAUDE.md section "Purpose-resolved secondary activity distances".
_OFF_BASELINE_EMD = {
    "shop":    0.053,
    "leisure": 0.064,
    "other":   0.018,
}

# ---------------------------------------------------------------------------
# Stage-cache loading
# ---------------------------------------------------------------------------

def _load_stage(working_directory: str, stage: str, aliases=()):
    """Load the most-recently-modified pickle for a synpp stage (or any alias).

    Tries ``stage`` then each alias in order; returns the first match.
    Raises RuntimeError when no name matches (fail-fast, no silent fallback).
    """
    for name in (stage, *aliases):
        matches = glob.glob(os.path.join(working_directory, name + "__*.p"))
        if matches:
            latest = max(matches, key=os.path.getmtime)
            logger.info("Loading stage '%s' from '%s'", name, latest)
            with open(latest, "rb") as fh:
                return pickle.load(fh)
    tried = ", ".join((stage, *aliases))
    raise RuntimeError(
        f"No cached pickle found for stage '{stage}' (names tried: {tried}) "
        f"in '{working_directory}'."
    )


# ---------------------------------------------------------------------------
# Realised band shares from a solve result
# ---------------------------------------------------------------------------

def compute_realised_band_shares(df_locs, df_acts, df_trips):
    """Compute per-purpose (shop/leisure/other) detour-adjusted band shares.

    Mirrors the measurement logic in ``scripts/validate_secondary_distances.py``
    (``measure_secondary_distances`` + the per-leg circuity scaling in
    ``build_report``). Returns {purpose: length-9 ndarray}.

    Parameters
    ----------
    df_locs : GeoDataFrame
        Output of the secondary chainsolvers solve: columns person_id,
        activity_index, location_id, geometry (EPSG:25832 points).
    df_acts : DataFrame
        synthesis.population.activities: person_id, activity_index, purpose.
    df_trips : DataFrame
        synthesis.population.trips: person_id, trip_index, mode.
    """
    import pandas as pd
    import geopandas as gpd

    df_acts = df_acts.reset_index(drop=True)
    df_locs = df_locs.reset_index(drop=True)

    # Extract x/y from geometry.
    df_xy = df_locs[["person_id", "activity_index"]].copy()
    df_xy["x_m"] = df_locs["geometry"].x
    df_xy["y_m"] = df_locs["geometry"].y

    df_merged = df_xy.merge(
        df_acts[["person_id", "activity_index", "purpose"]],
        on=["person_id", "activity_index"],
        how="inner",
    )
    df_merged = df_merged.sort_values(["person_id", "activity_index"]).reset_index(drop=True)

    df_prev = df_merged[["person_id", "x_m", "y_m"]].shift(1).rename(
        columns={"person_id": "prev_person_id", "x_m": "prev_x", "y_m": "prev_y"}
    )
    df_legs = pd.concat([df_merged, df_prev], axis=1)
    same_person_mask = df_legs["person_id"] == df_legs["prev_person_id"]
    df_legs = df_legs[same_person_mask].copy()

    secondary_mask = df_legs["purpose"].isin(_SECONDARY_PURPOSES)
    df_secondary = df_legs[secondary_mask].copy()

    dx = df_secondary["x_m"].values - df_secondary["prev_x"].values
    dy = df_secondary["y_m"].values - df_secondary["prev_y"].values
    df_secondary = df_secondary.copy()
    df_secondary["euclidean_km"] = np.sqrt(dx * dx + dy * dy) / 1000.0

    df_mode = df_trips[["person_id", "trip_index", "mode"]].rename(
        columns={"trip_index": "activity_index"}
    )
    df_secondary = df_secondary.merge(
        df_mode, on=["person_id", "activity_index"], how="left"
    )
    n_missing_mode = df_secondary["mode"].isna().sum()
    if n_missing_mode > 0:
        logger.warning(
            "[calibrate-scorer] mode join: %d/%d legs missing mode; defaulting to 'car'.",
            n_missing_mode, len(df_secondary),
        )
        df_secondary["mode"] = df_secondary["mode"].fillna("car")

    result = {}
    for purpose in _SECONDARY_PURPOSES:
        mask = df_secondary["purpose"] == purpose
        df_p = df_secondary.loc[mask].copy()
        n = len(df_p)
        if n == 0:
            logger.warning("[calibrate-scorer] no %s legs; returning uniform shares.", purpose)
            result[purpose] = np.ones(len(W12_BAND_EDGES_KM) - 1) / (len(W12_BAND_EDGES_KM) - 1)
            continue

        # Per-leg routed-equivalent via mode circuity network (constant mode).
        routed_km = np.empty(n, dtype=float)
        for network, grp_idx in df_p.groupby(df_p["mode"].map(mode_to_network)).groups.items():
            sl = df_p.index.get_indexer(list(grp_idx))
            routed_km[sl] = circuity.euclidean_to_routed(
                df_p.loc[grp_idx, "euclidean_km"].to_numpy(),
                network, mode="constant",
            )

        result[purpose] = _w12_band_shares(routed_km)
        logger.info(
            "[calibrate-scorer] %s: %d legs, mean routed %.2f km.",
            purpose, n, float(np.mean(routed_km)),
        )
    return result


# ---------------------------------------------------------------------------
# Concentration helper (excess top-1 share as a proxy for over-concentration)
# ---------------------------------------------------------------------------

def compute_concentration(df_locs) -> float:
    """Return the top-1 building share (max fraction to a single location_id).

    A high top-1 share means the solver is over-concentrating trips at a few
    buildings. This is the main sign that pot_weight is too large.
    Returns 0.0 on empty input.
    """
    if df_locs is None or len(df_locs) == 0:
        return 0.0
    counts = df_locs["location_id"].value_counts(normalize=True)
    return float(counts.iloc[0]) if len(counts) > 0 else 0.0


# ---------------------------------------------------------------------------
# Solve wrapper: re-run carla with trial scorer weights
# ---------------------------------------------------------------------------

def solve_with_weights(
    weights: dict,
    plans_df,
    problem_meta,
    unbounded_idx,
    problems,
    df_secondary,
    distributions,
    leisure_corr: float,
    random_seed: int,
    crs,
    fallback_strategy: str = "rda",
):
    """Re-solve secondary locations with trial scorer weights.

    Rebuilds the scorer from ``weights["attr_transform"]``,
    ``weights["pot_weight"]``, ``weights["dist_dev_weight"]`` and passes it to
    the carla solver, using the pre-built ``plans_df`` (distances already sampled
    from ``distributions``; only the candidate scoring changes). The plans_df is
    used AS-IS so the distance samples are identical across trials; only the
    scorer changes.

    Returns a GeoDataFrame with columns [person_id, activity_index, location_id,
    geometry] -- the same schema as the chainsolvers stage output.

    Raises RuntimeError when chainsolvers is unavailable.
    """
    import chainsolvers as cs
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        build_scorer,
        _build_locations_df,
        _rda_fallback_place,
        _fallback_place,
        _build_rda_candidate_index,
        SECONDARY_PURPOSES,
    )

    scorer = build_scorer(
        enabled=True,
        mode="combined",
        pot_weight=float(weights["pot_weight"]),
        dist_dev_weight=float(weights["dist_dev_weight"]),
        attr_transform=str(weights["attr_transform"]),
    )

    locations_df = _build_locations_df(df_secondary, with_potentials=True)

    # Run carla with the trial scorer.
    t0 = time.time()
    result_df = cs.solve(
        plans_df,
        locations_df,
        scorer=scorer,
        n_jobs=1,
    )
    logger.info(
        "[calibrate-scorer] carla solve (attr_transform=%s, pot_weight=%.2f, "
        "dist_dev_weight=%.4f): %d result rows in %.1fs.",
        weights["attr_transform"], weights["pot_weight"], weights["dist_dev_weight"],
        len(result_df), time.time() - t0,
    )

    # Extract locations from result_df.
    import geopandas as gpd
    import pandas as pd
    import shapely.geometry as geo

    rows = []
    for _, row in result_df.iterrows():
        try:
            pid, prob_idx_s, leg_idx_s = str(row["unique_leg_id"]).rsplit("#", 2)
            prob_idx = int(prob_idx_s)
            leg_idx = int(leg_idx_s)
        except (ValueError, AttributeError):
            continue
        if str(row.get("to_act_type", "")) not in SECONDARY_PURPOSES:
            continue
        try:
            tx = float(row["to_x"])
            ty = float(row["to_y"])
        except (ValueError, TypeError):
            continue
        if np.isnan(tx) or np.isnan(ty):
            continue
        meta = next(
            (m for m in problem_meta if m["problem_idx"] == prob_idx), None
        )
        if meta is None:
            continue
        act_idx = meta["activity_index"] + leg_idx
        rows.append((pid, act_idx, str(row.get("to_act_identifier", "")),
                     geo.Point(tx, ty)))

    if rows:
        df_out = gpd.GeoDataFrame(
            pd.DataFrame(rows, columns=["person_id", "activity_index",
                                         "location_id", "geometry"]),
            crs=crs,
        )
    else:
        df_out = gpd.GeoDataFrame(
            pd.DataFrame(columns=["person_id", "activity_index",
                                   "location_id", "geometry"]),
            crs=crs,
        )

    # Fallback for unbounded problems (same random seed as the production run;
    # the fallback result does not depend on scorer weights so it is identical
    # across trials -- run it once if needed).
    if unbounded_idx:
        random = np.random.RandomState(random_seed + 99999)
        if fallback_strategy == "rda":
            rda_index = _build_rda_candidate_index(df_secondary)
            fb_rows, _ = _rda_fallback_place(
                problems, unbounded_idx, rda_index,
                distributions, leisure_corr, random, crs,
            )
        else:
            fb_rows, _ = _fallback_place(
                problems, unbounded_idx, df_secondary, random, crs,
            )
        if fb_rows:
            import shapely.geometry as _geo
            fb_df = gpd.GeoDataFrame(
                pd.DataFrame(
                    [(pid, ai, lid, pt) for pid, ai, lid, pt in fb_rows],
                    columns=["person_id", "activity_index", "location_id", "geometry"],
                ),
                crs=crs,
            )
            df_out = gpd.GeoDataFrame(
                pd.concat([df_out, fb_df], ignore_index=True), crs=crs
            )

    return df_out


# ---------------------------------------------------------------------------
# Main calibration loop
# ---------------------------------------------------------------------------

def calibrate(
    working_directory: str,
    mid_dir: str,
    conc_weight: float,
    output_dir: str,
    random_seed: int = 0,
    leisure_corr: float = 2.0,
):
    """Run coordinate-descent calibration of the secondary scorer.

    Loads the cached synpp stages, builds the plans_df once (distances are
    fixed across trials; only the scorer changes), then runs coordinate_descent
    over the grid. Returns the best weights dict and a report dict.

    NOTE: This function requires a live synpp working directory with cached
    stage pickles for the secondary solve. It is intended to be called on the
    server, not locally.
    """
    import pandas as pd
    import geopandas as gpd
    from braunschweig.calibration.metrics import emd_on_bands
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        _build_plans_df,
        _resample_distributions,
    )
    from synthesis.population.spatial.secondary.problems import find_assignment_problems

    logger.info("[calibrate-scorer] loading stage pickles from '%s'.", working_directory)

    df_trips_raw = _load_stage(working_directory, "synthesis.population.trips",
                               aliases=("braunschweig.popsim.trips_stage",))
    df_trips = df_trips_raw.sort_values(by=["person_id", "trip_index"]).copy()
    df_trips["travel_time"] = df_trips["arrival_time"] - df_trips["departure_time"]

    df_acts = _load_stage(working_directory, "synthesis.population.activities")

    df_home = _load_stage(working_directory, "synthesis.population.spatial.home.locations")
    df_work_raw, df_education_raw = _load_stage(
        working_directory, "synthesis.population.spatial.primary.locations"
    )
    df_sampled = _load_stage(working_directory, "synthesis.population.sampled")

    # Build the df_primary frame (mirrors _prepare_primary).
    crs = df_home.crs
    df_locations = df_sampled[["person_id", "household_id"]].copy()
    df_locations = df_locations.merge(
        df_home.rename(columns={"geometry": "home"})[["household_id", "home"]],
        on="household_id", how="left",
    )
    df_locations = df_locations.merge(
        df_work_raw.rename(columns={"geometry": "work"})[["person_id", "work"]],
        on="person_id", how="left",
    )
    df_locations = df_locations.merge(
        df_education_raw.rename(columns={"geometry": "education"})[["person_id", "education"]],
        on="person_id", how="left",
    )
    df_primary = df_locations[["person_id", "home", "work", "education"]].sort_values("person_id")

    distance_distributions = _load_stage(
        working_directory,
        "synthesis.population.spatial.secondary.distance_distributions",
    )
    df_secondary = _load_stage(working_directory, "synthesis.locations.secondary")

    # Resample distributions (matches production execute).
    distance_distributions = _resample_distributions(distance_distributions, dict(
        car=0.0, car_passenger=0.1, pt=0.5, bicycle=0.0, walk=-0.5,
    ))

    # leisure_corr passed in from main() (config key leisure_correction_factor, default 2.0).

    logger.info("[calibrate-scorer] enumerating assignment problems...")
    t0 = time.time()
    problems = list(find_assignment_problems(df_trips, df_primary))
    logger.info("[calibrate-scorer] %d problems in %.1fs.", len(problems), time.time() - t0)

    random = np.random.RandomState(random_seed)
    plans_df, problem_meta, unbounded_idx, _, _ = _build_plans_df(
        problems, distance_distributions, leisure_corr, random,
    )
    logger.info(
        "[calibrate-scorer] plans_df: %d bounded problems, %d unbounded.",
        len(problem_meta), len(unbounded_idx),
    )

    # Load W12 targets.
    w12 = load_w12_band_shares(mid_dir)
    w12_targets = {p: w12[p] for p in _SECONDARY_PURPOSES}
    logger.info(
        "[calibrate-scorer] W12 targets loaded: shop %.1f km, leisure %.1f km, other %.1f km.",
        w12["shop_mean_km"], w12["leisure_mean_km"], w12["other_mean_km"],
    )

    # Cache for per-trial concentration values (top-1 share).
    _concentration_cache: dict = {}

    def per_purpose_realised(weights):
        """Re-solve with trial weights and return per-purpose band shares."""
        key = (
            str(weights.get("attr_transform")),
            float(weights.get("pot_weight", 1.0)),
            float(weights.get("dist_dev_weight", 1.0)),
        )
        if key in _concentration_cache:
            return _concentration_cache[key]["band_shares"]

        df_locs = solve_with_weights(
            weights=weights,
            plans_df=plans_df,
            problem_meta=problem_meta,
            unbounded_idx=unbounded_idx,
            problems=problems,
            df_secondary=df_secondary,
            distributions=distance_distributions,
            leisure_corr=leisure_corr,
            random_seed=random_seed,
            crs=crs,
        )
        shares = compute_realised_band_shares(df_locs, df_acts, df_trips_raw)
        conc = compute_concentration(df_locs)
        _concentration_cache[key] = {"band_shares": shares, "concentration": conc}
        return shares

    def concentration_fn(weights):
        key = (
            str(weights.get("attr_transform")),
            float(weights.get("pot_weight", 1.0)),
            float(weights.get("dist_dev_weight", 1.0)),
        )
        if key in _concentration_cache:
            return _concentration_cache[key]["concentration"]
        per_purpose_realised(weights)  # populates cache
        return _concentration_cache[key]["concentration"]

    loss_fn = build_secondary_loss(
        per_purpose_realised, w12_targets,
        concentration_fn=(concentration_fn if conc_weight > 0.0 else None),
        conc_weight=conc_weight,
    )

    logger.info("[calibrate-scorer] starting coordinate descent (grid: %s).", _GRID)
    result = coordinate_descent(loss_fn, init=_INIT, grid=_GRID, max_rounds=10)
    best_weights = result["weights"]
    best_loss = result["loss"]

    logger.info(
        "[calibrate-scorer] best weights: %s (loss=%.6f).",
        best_weights, best_loss,
    )

    # Evaluate the baseline (linear/1.0/1.0) explicitly for comparison.
    baseline_shares = per_purpose_realised(_INIT)
    baseline_per_purpose_emd = {
        p: float(emd_on_bands(baseline_shares[p], w12_targets[p]))
        for p in _SECONDARY_PURPOSES
    }
    baseline_total_emd = sum(baseline_per_purpose_emd.values())

    best_shares = per_purpose_realised(best_weights)
    best_per_purpose_emd = {
        p: float(emd_on_bands(best_shares[p], w12_targets[p]))
        for p in _SECONDARY_PURPOSES
    }
    best_total_emd = sum(best_per_purpose_emd.values())

    # Verdict: accept only if total EMD improves AND no purpose regresses past OFF baseline.
    improves_total = best_total_emd < baseline_total_emd - 1e-6
    no_purpose_regression = all(
        best_per_purpose_emd[p] <= _OFF_BASELINE_EMD[p] + 1e-4
        for p in _SECONDARY_PURPOSES
    )
    verdict = "ACCEPT" if (improves_total and no_purpose_regression) else "REJECT"

    report = {
        "working_directory": working_directory,
        "mid_dir": mid_dir,
        "conc_weight": conc_weight,
        "random_seed": random_seed,
        "grid": _GRID,
        "init": _INIT,
        "baseline_init": _INIT,
        "baseline_total_emd": baseline_total_emd,
        "baseline_per_purpose_emd": baseline_per_purpose_emd,
        "off_baseline_emd": _OFF_BASELINE_EMD,
        "best_weights": best_weights,
        "best_loss": best_loss,
        "best_total_emd": best_total_emd,
        "best_per_purpose_emd": best_per_purpose_emd,
        "verdict": verdict,
        "history": result["history"],
    }

    if verdict == "ACCEPT":
        logger.info(
            "[calibrate-scorer] ACCEPT: best total EMD %.4f < baseline %.4f; "
            "no purpose regression past OFF baseline.",
            best_total_emd, baseline_total_emd,
        )
    else:
        logger.warning(
            "[calibrate-scorer] REJECT: best total EMD %.4f (baseline %.4f); "
            "improves_total=%s, no_purpose_regression=%s. "
            "Keep current defaults; record negative result.",
            best_total_emd, baseline_total_emd,
            improves_total, no_purpose_regression,
        )

    return best_weights, report


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
            "Calibrate the secondary chainsolvers scorer scale-alignment against "
            "MiD W12 via coordinate descent. Measure-first: prints the winning "
            "YAML block but does NOT pin anything. Run on the server."
        )
    )
    parser.add_argument(
        "--working-directory", required=True,
        help="synpp working directory containing cached stage pickles.",
    )
    parser.add_argument(
        "--config", required=True,
        help=(
            "Run config YAML (used to derive mid_dir from "
            "braunschweig.population.popsim.mid_dir; also logged for traceability)."
        ),
    )
    parser.add_argument(
        "--conc-weight", type=float, default=0.0,
        help=(
            "Weight on the concentration penalty (top-1 building share). "
            "Default 0.0 (pure W12 EMD, no penalty). Use 1.0 for a mild penalty."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="eqasim-data/data/braunschweig/calibration/secondary",
        help="Directory for the JSON report and any CSVs.",
    )
    args = parser.parse_args()

    # Derive mid_dir from the config YAML (key braunschweig.population.popsim.mid_dir).
    import yaml  # safe-load; pyyaml is a transitive eqasim dep
    with open(args.config, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    mid_dir = None
    for section in cfg.values() if isinstance(cfg, dict) else []:
        if isinstance(section, dict):
            mid_dir = section.get("braunschweig.population.popsim.mid_dir")
            if mid_dir:
                break
    if not mid_dir:
        # Fallback: try top-level or a common default.
        mid_dir = cfg.get("braunschweig.population.popsim.mid_dir")
    if not mid_dir:
        raise RuntimeError(
            "Could not find 'braunschweig.population.popsim.mid_dir' in config "
            f"'{args.config}'. Provide a config that sets this key."
        )
    logger.info("[calibrate-scorer] mid_dir resolved to '%s'.", mid_dir)

    # Derive random_seed from config.
    random_seed = 0
    if isinstance(cfg, dict):
        rs = cfg.get("random_seed")
        if rs is None:
            for section in cfg.values():
                if isinstance(section, dict) and "random_seed" in section:
                    rs = section["random_seed"]
                    break
        if rs is not None:
            random_seed = int(rs)
    logger.info("[calibrate-scorer] random_seed=%d.", random_seed)

    # Derive leisure_correction_factor from config (mirrors configure() default 2.0).
    leisure_corr = 2.0
    if isinstance(cfg, dict):
        lc = cfg.get("leisure_correction_factor")
        if lc is None:
            for section in cfg.values():
                if isinstance(section, dict) and "leisure_correction_factor" in section:
                    lc = section["leisure_correction_factor"]
                    break
        if lc is not None:
            leisure_corr = float(lc)
    logger.info("[calibrate-scorer] leisure_correction_factor=%.2f.", leisure_corr)

    # Validate paths.
    if not os.path.isdir(args.working_directory):
        raise RuntimeError(
            f"Working directory not found: '{args.working_directory}'. "
            "Ensure --working-directory points to a valid synpp cache."
        )
    if not os.path.isdir(mid_dir):
        raise RuntimeError(
            f"MiD directory not found: '{mid_dir}'. "
            "Ensure the config's mid_dir points to the committed MiD data."
        )
    os.makedirs(args.output_dir, exist_ok=True)

    # Run calibration.
    best_weights, report = calibrate(
        working_directory=args.working_directory,
        mid_dir=mid_dir,
        conc_weight=args.conc_weight,
        output_dir=args.output_dir,
        random_seed=random_seed,
        leisure_corr=leisure_corr,
    )

    # Write report JSON.
    report_path = os.path.join(args.output_dir, "scorer_calibration_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    logger.info("[calibrate-scorer] report written to '%s'.", report_path)

    # Print the winning YAML block for manual pinning.
    verdict = report["verdict"]
    print("\n" + "=" * 72)
    print("SECONDARY SCORER CALIBRATION RESULT")
    print(f"Verdict: {verdict}")
    print(f"  Baseline total EMD (linear/1.0/1.0): {report['baseline_total_emd']:.4f}")
    print(f"  Best total EMD:                       {report['best_total_emd']:.4f}")
    print("  Per-purpose EMD:")
    for p in _SECONDARY_PURPOSES:
        print(
            f"    {p:<10}: OFF={_OFF_BASELINE_EMD[p]:.3f}  "
            f"baseline={report['baseline_per_purpose_emd'][p]:.4f}  "
            f"best={report['best_per_purpose_emd'][p]:.4f}"
        )
    print("=" * 72)
    if verdict == "ACCEPT":
        print("\nPinned YAML (paste into real configs if accepted after review):\n")
        print(f"  secondary_scorer_attr_transform: {best_weights['attr_transform']!r}")
        print(f"  secondary_scorer_pot_weight: {best_weights['pot_weight']}")
        print(f"  secondary_scorer_dist_dev_weight: {best_weights['dist_dev_weight']}")
    else:
        print(
            "\nNo improvement over baseline: keep current defaults "
            "(secondary_scorer_attr_transform: linear, secondary_scorer_pot_weight: 1.0, "
            "secondary_scorer_dist_dev_weight: 1.0). Negative result recorded in report."
        )
    print()


if __name__ == "__main__":
    main()
