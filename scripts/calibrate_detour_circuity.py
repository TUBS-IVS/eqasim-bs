"""Fit the distance-dependent detour/circuity curve c(d) = c_inf + a * exp(-d/tau)
separately for the car and walk networks.

The script reads a cached synpp working_directory, extracts OD pairs (home->work +
car/walk secondary legs + long/short education trips), builds routing graphs from the
cached MATSim network (car) and an OSM PBF (walk), and runs a convergence-driven
stratified-sampling loop to fit the curve robustly.

Output:
  eqasim-data/data/braunschweig/calibration/detour_circuity_params.csv
    car/walk rows with fitted c_inf/a/tau_km/n_samples/fit_r2; pt row carried over
    from the existing seed file unchanged.
  <output_dir>/circuity_convergence_<net>.csv, circuity_convergence_<net>.png
    Per-round convergence history and plot.
  <output_dir>/circuity_by_rs7.csv
    Per-RS7 fitted curve parameters + promote/keep_global verdict.
  <output_dir>/band_shift_impact.csv
    Commute EMD vs P13 and secondary EMD vs W12 under constant 1.3 vs fitted curve.
  <output_dir>/circuity_fit_<net>.png
    Scatter of sample ratio vs euclidean km + fitted curve + constant-1.3 line.
  <output_dir>/summary.md

Run (server-side only; no cache here):
  python scripts/calibrate_detour_circuity.py \\
    --working-directory eqasim-data/cache_bs_25pct_allfeat \\
    --osm-pbf eqasim-data/osm/niedersachsen.osm.pbf \\
    --config config_server_braunschweig_25pct_allfeat_popsim.yml \\
    --output-dir eqasim-data/data/braunschweig/calibration/detour_circuity
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.calibration.detour_fit import (  # noqa: E402
    accumulate_accepted_indices,
    ConvergenceTracker,
    build_graph_from_edges,
    fit_circuity_curve,
    read_matsim_network,
    read_walk_network_pyrosm,
    route_lengths_km,
    stratified_sample,
    STRATA_EDGES_KM,
)
from braunschweig.calibration.circuity import (  # noqa: E402
    LEGACY_DETOUR_FACTOR,
    load_circuity_params,
    euclidean_to_routed,
    DEFAULT_PARAMS_PATH,
)
from braunschweig.calibration.metrics import (  # noqa: E402
    band_shares,
    emd_on_bands,
)
from braunschweig.calibration.targets import (  # noqa: E402
    load_p13_band_shares,
    load_w12_band_shares,
    W12_BAND_EDGES_KM,
)
from braunschweig.data.bbsr.regiostar import (  # noqa: E402
    ars_to_ags8,
    urban_class_by_commune,
)
from braunschweig.gravity.friction import BAND_EDGES_KM  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage-cache loading (mirrors calibrate_gravity_distribution.py)
# ---------------------------------------------------------------------------

def _load_stage(working_directory: str, stage: str):
    """Load the most-recently-modified pickle for a synpp stage name.

    Raises RuntimeError if no matching file is found.
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


def _load_config(config_path: str) -> dict:
    """Load a YAML run config into a flat dict (top-level 'config' key or raw)."""
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required to load the run config. "
            "Install with: conda install pyyaml"
        ) from exc
    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if isinstance(raw, dict) and "config" in raw:
        return raw["config"]
    return raw or {}


# ---------------------------------------------------------------------------
# OD pool extraction from cached stages
# ---------------------------------------------------------------------------

def _extract_od_pools(wd: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray,
                                         np.ndarray, np.ndarray]:
    """Extract car and walk OD pools from the cached synthesis stages.

    Car OD pool:
      - home->work pairs for employed persons (commute legs)
      - car secondary legs (mode == 'car' in secondary locations stage)
      - long education legs (oberstufe, bbs, hochschule = age >= 16) routed by car

    Walk OD pool:
      - walk or bike secondary legs (mode in {'walk', 'bike'})
      - short education legs (grundschule, sekundar_1 = age 6-15) typically walked

    Returns:
      car_origins_xy, car_dests_xy   (N_car, 2) EPSG:25832 metres
      walk_origins_xy, walk_dests_xy (N_walk, 2) EPSG:25832 metres
      od_commune_ids_car, od_commune_ids_walk  (N,) home commune_id strings
        (used downstream for RS7 tagging)

    ASSUMED STAGE SCHEMAS (mirroring calibrate_gravity_distribution.py):
      synthesis.population.spatial.home.locations : GeoDataFrame keyed by household_id
        cols: [household_id, geometry] CRS=EPSG:25832
      braunschweig.synthesis.spatial.home_zones : DataFrame
        cols: [household_id, commune_id, ...]
      braunschweig.synthesis.population.enriched : DataFrame/GeoDataFrame
        cols: [person_id, household_id, employed(bool), age, ...]
      braunschweig.locations.work : GeoDataFrame
        cols: [commune_id, geometry, ...] CRS=EPSG:25832
      synthesis.population.spatial.primary.candidates : dict
        key 'persons': DataFrame with [person_id, household_id, has_education_trip]
      braunschweig.synthesis.locations.secondary : GeoDataFrame (or DataFrame)
        cols: [person_id, mode, geometry] CRS=EPSG:25832
        ASSUMPTION: secondary locations stage carries a 'mode' column with values
        'car'/'walk'/'bike'/... (standard eqasim secondary schema). If the stage
        schema differs (e.g. mode is not present), the car/walk split will fall
        back to all pairs being treated as car. Logged explicitly below.
    """
    logger.info("[circuity] extracting OD pools from cache: %s", wd)

    # --- Core stages ---
    df_home_locations = _load_stage(wd, "synthesis.population.spatial.home.locations")
    if "household_id" not in df_home_locations.columns:
        df_home_locations = df_home_locations.reset_index()

    df_home_zones = _load_stage(wd, "braunschweig.synthesis.spatial.home_zones")
    df_enriched = _load_stage(wd, "braunschweig.synthesis.population.enriched")

    # Home geometry lookup: household_id -> (x_m, y_m) + commune_id
    hh_geom = df_home_locations[["household_id", "geometry"]].drop_duplicates("household_id")
    hh_zones = df_home_zones[["household_id", "commune_id"]].drop_duplicates("household_id")
    hh_lookup = hh_geom.merge(hh_zones, on="household_id", how="left")
    hh_lookup = hh_lookup.dropna(subset=["geometry"])
    hh_lookup["hx"] = hh_lookup["geometry"].apply(lambda g: g.x)
    hh_lookup["hy"] = hh_lookup["geometry"].apply(lambda g: g.y)
    hh_xy = hh_lookup.set_index("household_id")[["hx", "hy", "commune_id"]]
    logger.info("[circuity] households with geometry: %d", len(hh_xy))

    # --- CAR: home->work commute legs ---
    car_ox, car_oy, car_dx, car_dy = [], [], [], []
    car_commune_ids: list[str] = []
    walk_ox, walk_oy, walk_dx, walk_dy = [], [], [], []
    walk_commune_ids: list[str] = []

    # Workers: employed==True
    if "employed" in df_enriched.columns:
        workers = df_enriched[df_enriched["employed"]].copy()
    elif "has_work_trip" in df_enriched.columns:
        logger.warning(
            "[circuity] 'employed' not in enriched stage; using 'has_work_trip' as fallback "
            "(CLAUDE.md no-silent-fallback: verify enriched stage schema)."
        )
        workers = df_enriched[df_enriched["has_work_trip"]].copy()
    else:
        logger.warning(
            "[circuity] neither 'employed' nor 'has_work_trip' in enriched stage; "
            "using all persons as workers (CLAUDE.md no-silent-fallback: check schema)."
        )
        workers = df_enriched.copy()
    logger.info("[circuity] workers identified: %d", len(workers))

    # Work location stage (car destinations for commutes)
    df_work = _load_stage(wd, "braunschweig.locations.work")
    logger.info("[circuity] work location candidates: %d", len(df_work))

    # Merge worker -> household -> home coords + commune_id
    if "household_id" not in workers.columns:
        raise RuntimeError(
            "[circuity] 'household_id' not in enriched stage. "
            "Check that an all-features cache was loaded."
        )
    worker_hh = workers[["person_id", "household_id"]].merge(
        hh_xy.reset_index(), on="household_id", how="inner"
    )
    logger.info("[circuity] workers with resolved home geometry: %d", len(worker_hh))

    # Precompute work location arrays per commune
    work_xy_by_commune: dict[str, np.ndarray] = {}
    for commune_id, grp in df_work.groupby("commune_id"):
        work_xy_by_commune[str(commune_id)] = np.column_stack(
            [grp.geometry.x.values, grp.geometry.y.values]
        )

    # Paired: for each worker draw a work location from the same commune
    # (approximate: use all same-commune work locations as destinations, not the
    # gravity-selected destination — this is for circuity MEASUREMENT not gravity
    # calibration; we just need real home/work pairs spread across strata).
    # ASSUMPTION: we use cross-commune pairs by assigning each worker's work location
    # from the work locations in a randomly drawn destination commune, reusing the
    # approach from assign_and_measure. Since we only need euclidean vs routed
    # distances for the curve, we take one random work location per worker from the
    # combined pool.
    rng_pool = np.random.RandomState(42)
    all_work_xy = np.concatenate(list(work_xy_by_commune.values()), axis=0) if work_xy_by_commune else np.zeros((0, 2))
    if len(all_work_xy) > 0 and len(worker_hh) > 0:
        n_workers = len(worker_hh)
        dest_idx = rng_pool.randint(0, len(all_work_xy), size=n_workers)
        dest_xy = all_work_xy[dest_idx]
        car_ox.extend(worker_hh["hx"].tolist())
        car_oy.extend(worker_hh["hy"].tolist())
        car_dx.extend(dest_xy[:, 0].tolist())
        car_dy.extend(dest_xy[:, 1].tolist())
        car_commune_ids.extend(worker_hh["commune_id"].fillna("").tolist())
        logger.info("[circuity] commute OD pairs added: %d", n_workers)
    else:
        logger.warning("[circuity] no commute OD pairs available (no workers or no work locations).")

    # --- SECONDARY legs (car + walk) ---
    # Build secondary legs via the same pattern as validate_secondary_distances.py:
    # load synthesis.population.spatial.locations (geometry per activity),
    # synthesis.population.activities (purpose per activity),
    # synthesis.population.trips (mode per leg).
    # Each leg is defined as (previous_activity_location -> secondary_activity_location).
    # Mode is joined from the trips stage via activity_index == trip_index.
    # Car/car_passenger -> car pool; walk/bike -> walk pool; pt excluded.
    try:
        df_all_locs = _load_stage(wd, "synthesis.population.spatial.locations")
        df_acts = _load_stage(wd, "synthesis.population.activities")
        df_trips = _load_stage(wd, "synthesis.population.trips")
        logger.info(
            "[circuity] secondary: loaded locations (%d), activities (%d), trips (%d)",
            len(df_all_locs), len(df_acts), len(df_trips),
        )

        # Extract x/y from activity geometry (EPSG:25832).
        df_all_locs = df_all_locs.reset_index(drop=True)
        df_acts = df_acts.reset_index(drop=True)
        df_xy = df_all_locs[["person_id", "activity_index"]].copy()
        df_xy["x_m"] = df_all_locs["geometry"].x
        df_xy["y_m"] = df_all_locs["geometry"].y

        # Attach commune_id for RS7 tagging (from spatial.locations if available,
        # else fall back to person -> household -> home commune).
        if "commune_id" in df_all_locs.columns:
            df_xy["commune_id"] = df_all_locs["commune_id"].values
        else:
            # Fallback: join via household; commune_id will be home commune.
            p_to_hh = df_enriched[["person_id", "household_id"]].drop_duplicates("person_id")
            df_xy = df_xy.merge(p_to_hh, on="person_id", how="left")
            df_xy = df_xy.merge(
                hh_xy.reset_index()[["household_id", "commune_id"]],
                on="household_id", how="left",
            )

        # Merge purpose onto coordinates.
        df_merged = df_xy.merge(
            df_acts[["person_id", "activity_index", "purpose"]],
            on=["person_id", "activity_index"],
            how="inner",
        )
        df_merged = df_merged.sort_values(["person_id", "activity_index"]).reset_index(drop=True)

        # Build legs: shift by one to obtain the previous activity's coordinates.
        df_prev = df_merged[["person_id", "x_m", "y_m"]].shift(1).rename(
            columns={"person_id": "prev_person_id", "x_m": "prev_x", "y_m": "prev_y"}
        )
        df_legs_all = pd.concat([df_merged, df_prev], axis=1)
        same_person_mask = df_legs_all["person_id"] == df_legs_all["prev_person_id"]
        df_legs_all = df_legs_all[same_person_mask].copy()

        # Keep only secondary purposes.
        _SEC_PURPOSES = ("shop", "leisure", "other")
        sec_mask = df_legs_all["purpose"].isin(_SEC_PURPOSES)
        df_sec_legs = df_legs_all[sec_mask].copy()
        logger.info("[circuity] secondary legs before mode join: %d", len(df_sec_legs))

        # Join mode via trip_index == activity_index.
        df_mode = df_trips[["person_id", "trip_index", "mode"]].rename(
            columns={"trip_index": "activity_index"}
        )
        n_before_mode = len(df_sec_legs)
        df_sec_legs = df_sec_legs.merge(
            df_mode, on=["person_id", "activity_index"], how="left"
        )
        if len(df_sec_legs) != n_before_mode:
            logger.warning(
                "[circuity] secondary mode-join changed row count %d -> %d; "
                "possible non-unique (person_id, activity_index) key in trips stage.",
                n_before_mode, len(df_sec_legs),
            )
        n_mode_missing = df_sec_legs["mode"].isna().sum()
        if n_mode_missing > 0:
            logger.warning(
                "[circuity] secondary mode join: %d/%d legs missing mode (no fallback; "
                "these legs are excluded from both pools).",
                n_mode_missing, n_before_mode,
            )
        df_sec_legs = df_sec_legs.dropna(subset=["mode", "prev_x", "prev_y"])

        sec_mode_arr = df_sec_legs["mode"].values
        car_sec_mask = np.isin(sec_mode_arr, ["car", "car_passenger"])
        walk_sec_mask = np.isin(sec_mode_arr, ["walk", "bike"])
        other_sec_mask = ~(car_sec_mask | walk_sec_mask)
        n_car_sec = int(car_sec_mask.sum())
        n_walk_sec = int(walk_sec_mask.sum())
        n_other_sec = int(other_sec_mask.sum())
        logger.info(
            "[circuity] secondary legs: %d car, %d walk/bike, %d other modes (pt excluded)",
            n_car_sec, n_walk_sec, n_other_sec,
        )

        sec_ox = df_sec_legs["prev_x"].values
        sec_oy = df_sec_legs["prev_y"].values
        sec_dx_arr = df_sec_legs["x_m"].values
        sec_dy_arr = df_sec_legs["y_m"].values
        sec_commune_vals = df_sec_legs["commune_id"].fillna("").values if "commune_id" in df_sec_legs.columns else np.full(len(df_sec_legs), "", dtype=object)

        if car_sec_mask.any():
            car_ox.extend(sec_ox[car_sec_mask].tolist())
            car_oy.extend(sec_oy[car_sec_mask].tolist())
            car_dx.extend(sec_dx_arr[car_sec_mask].tolist())
            car_dy.extend(sec_dy_arr[car_sec_mask].tolist())
            car_commune_ids.extend(sec_commune_vals[car_sec_mask].tolist())
        if walk_sec_mask.any():
            walk_ox.extend(sec_ox[walk_sec_mask].tolist())
            walk_oy.extend(sec_oy[walk_sec_mask].tolist())
            walk_dx.extend(sec_dx_arr[walk_sec_mask].tolist())
            walk_dy.extend(sec_dy_arr[walk_sec_mask].tolist())
            walk_commune_ids.extend(sec_commune_vals[walk_sec_mask].tolist())

    except RuntimeError as exc:
        logger.warning(
            "[circuity] secondary locations stages not found (tried "
            "synthesis.population.spatial.locations / synthesis.population.activities / "
            "synthesis.population.trips): %s; "
            "secondary OD pairs will be absent from the pool.", exc
        )

    # --- EDUCATION legs (long = car pool, short = walk pool) ---
    # Real stage: braunschweig.synthesis.locations.education_gravity
    # GeoDataFrame columns: [person_id, commune_id, location_id, geometry]
    # Origin = student's home location from synthesis.population.spatial.home.locations.
    # Age split (from enriched): age 6-15 -> walk pool (grundschule/sekundar_1);
    #                            age >= 16 -> car pool (oberstufe/bbs/hochschule);
    #                            age 0-5 excluded (kindergarten, trip dominated by
    #                            walk secondary legs).
    try:
        edu_locs = _load_stage(wd, "braunschweig.synthesis.locations.education_gravity")
        logger.info(
            "[circuity] education locations loaded from "
            "'braunschweig.synthesis.locations.education_gravity': %d rows",
            len(edu_locs),
        )

        # Education destination coordinates.
        edu_locs_df = edu_locs[["person_id", "geometry"]].copy()
        edu_locs_df = edu_locs_df.dropna(subset=["geometry"])
        edu_locs_df["ex"] = edu_locs_df["geometry"].x
        edu_locs_df["ey"] = edu_locs_df["geometry"].y

        # Join age from enriched.
        age_cols = df_enriched[["person_id", "age"]].drop_duplicates("person_id")
        edu_locs_df = edu_locs_df.merge(age_cols, on="person_id", how="left")

        # Join household_id from enriched, then home coords from hh_xy.
        hh_id_col = df_enriched[["person_id", "household_id"]].drop_duplicates("person_id")
        edu_locs_df = edu_locs_df.merge(hh_id_col, on="person_id", how="left")
        edu_locs_df = edu_locs_df.merge(
            hh_xy.reset_index()[["household_id", "hx", "hy", "commune_id"]],
            on="household_id", how="left",
        )
        edu_locs_df = edu_locs_df.dropna(subset=["ex", "ey", "hx", "hy", "age"])

        long_mask = edu_locs_df["age"] >= 16        # oberstufe / bbs / hochschule
        short_mask = (edu_locs_df["age"] >= 6) & (edu_locs_df["age"] <= 15)  # grundschule / sekundar_1
        # age 0-5 (kindergarten) excluded intentionally

        def _add_edu(mask, pool_ox, pool_oy, pool_dx, pool_dy, pool_commune, label):
            sel = edu_locs_df[mask]
            if len(sel) == 0:
                return
            pool_ox.extend(sel["hx"].tolist())
            pool_oy.extend(sel["hy"].tolist())
            pool_dx.extend(sel["ex"].tolist())
            pool_dy.extend(sel["ey"].tolist())
            pool_commune.extend(sel["commune_id"].fillna("").tolist())
            logger.info("[circuity] education OD pairs added to %s pool: %d", label, len(sel))

        _add_edu(long_mask, car_ox, car_oy, car_dx, car_dy, car_commune_ids, "car")
        _add_edu(short_mask, walk_ox, walk_oy, walk_dx, walk_dy, walk_commune_ids, "walk")

    except RuntimeError as exc:
        logger.warning(
            "[circuity] education gravity stage not found "
            "(tried 'braunschweig.synthesis.locations.education_gravity'): %s; "
            "education OD pairs will be absent from the pool.", exc
        )

    # --- Assemble arrays ---
    car_origins = np.column_stack([car_ox, car_oy]) if car_ox else np.zeros((0, 2))
    car_dests = np.column_stack([car_dx, car_dy]) if car_dx else np.zeros((0, 2))
    walk_origins = np.column_stack([walk_ox, walk_oy]) if walk_ox else np.zeros((0, 2))
    walk_dests = np.column_stack([walk_dx, walk_dy]) if walk_dx else np.zeros((0, 2))
    car_commune_arr = np.asarray(car_commune_ids, dtype=object)
    walk_commune_arr = np.asarray(walk_commune_ids, dtype=object)

    logger.info(
        "[circuity] OD pool sizes: car=%d, walk=%d",
        len(car_origins), len(walk_origins),
    )
    return car_origins, car_dests, walk_origins, walk_dests, car_commune_arr, walk_commune_arr


# ---------------------------------------------------------------------------
# Convergence loop (shared for car and walk)
# ---------------------------------------------------------------------------

def _run_convergence_loop(
    network_label: str,
    csr, node_xy,
    origins_xy: np.ndarray,
    dests_xy: np.ndarray,
    rng: np.random.RandomState,
    min_samples: int,
    max_samples: int,
    convergence_step: int,
    convergence_tol: float,
    convergence_patience: int,
) -> tuple[dict, list[dict], np.ndarray, np.ndarray, np.ndarray]:
    """Run the convergence-driven stratified-sampling loop for one network.

    Each round draws `convergence_step` more OD pairs via `stratified_sample`,
    routes them on the graph, drops snap/route failures (with explicit failure-rate
    logging), fits `fit_circuity_curve` on the cumulative routed sample, and checks
    convergence via ConvergenceTracker.

    Returns:
      fit_params: final fitted param dict
      history: list of per-round tracker history dicts
      cum_euclidean_km: cumulative accepted euclidean distances (km), length M
      cum_routed_km: cumulative accepted routed distances (km), length M
      cum_pool_indices: pool indices of each accepted pair (length M); satisfies
        origins_xy[cum_pool_indices[i]] == origin of accepted pair i, so the caller
        can recover commune_ids[cum_pool_indices] for RS7 tagging.
    """
    tracker = ConvergenceTracker(min_samples=min_samples, tol=convergence_tol,
                                 patience=convergence_patience)
    cum_euclidean: list[float] = []
    cum_routed: list[float] = []
    cum_pool_idx: list[int] = []
    n_routed_total = 0
    n_fail_total = 0
    converged = False
    fit_params: dict = {"c_inf": 1.3, "a": 0.0, "tau": 1.0, "r2": 0.0, "n": 0}

    for _round in range(max_samples // convergence_step + 2):
        n_done = len(cum_euclidean)
        if n_done >= max_samples:
            break

        # Draw a fresh batch of stratified OD pairs
        batch_idx = stratified_sample(
            origins_xy, dests_xy, n_target=convergence_step, rng=rng,
            edges_km=STRATA_EDGES_KM,
        )
        if len(batch_idx) == 0:
            logger.warning(
                "[circuity][%s] stratified_sample returned empty batch; "
                "OD pool may be too small. Stopping.", network_label,
            )
            break

        o_batch = origins_xy[batch_idx]
        d_batch = dests_xy[batch_idx]
        eucl_km = np.linalg.norm(d_batch - o_batch, axis=1) / 1000.0

        routed_km, fail = route_lengths_km(csr, node_xy, o_batch, d_batch)

        n_fail = int(fail.sum())
        n_routed = len(fail)
        n_fail_total += n_fail
        n_routed_total += n_routed

        if n_routed > 0:
            fail_rate = n_fail / n_routed
            if fail_rate > 0.05:
                logger.warning(
                    "[circuity][%s] snap/route failure rate %.1f%% (%d/%d) in round; "
                    "check that the network covers the OD pool area (CLAUDE.md no-silent-fallback).",
                    network_label, 100.0 * fail_rate, n_fail, n_routed,
                )
            else:
                logger.debug(
                    "[circuity][%s] round snap/route failures: %d/%d (%.1f%%)",
                    network_label, n_fail, n_routed, 100.0 * fail_rate,
                )

        # Accept only non-failed pairs with positive euclidean distance
        keep = ~fail & (eucl_km > 1e-6)
        if not keep.any():
            logger.warning(
                "[circuity][%s] all pairs in this batch failed or have zero distance; "
                "skipping round.", network_label,
            )
            continue

        cum_euclidean.extend(eucl_km[keep].tolist())
        cum_routed.extend(routed_km[keep].tolist())
        # Track accepted pool indices so _rs7_diagnostic can recover origin commune_id.
        accumulate_accepted_indices(cum_pool_idx, batch_idx, keep)

        n_cum = len(cum_euclidean)
        try:
            fit_params = fit_circuity_curve(
                np.array(cum_euclidean), np.array(cum_routed)
            )
        except RuntimeError as exc:
            logger.warning(
                "[circuity][%s] fit_circuity_curve failed (n=%d): %s; continuing.",
                network_label, n_cum, exc,
            )
            continue

        converged = tracker.update(n_cum, fit_params)
        logger.info(
            "[circuity][%s] n=%d  c_inf=%.4f  a=%.4f  tau=%.4f  R2=%.4f  converged=%s",
            network_label, n_cum,
            fit_params["c_inf"], fit_params["a"], fit_params["tau"], fit_params["r2"],
            converged,
        )

        if converged:
            logger.info(
                "[circuity][%s] converged after %d routed pairs.",
                network_label, n_cum,
            )
            break

    if not converged:
        logger.warning(
            "[circuity][%s] did not converge within max_samples=%d; "
            "using the current fit (c_inf=%.4f a=%.4f tau=%.4f R2=%.4f). "
            "Consider increasing --max-samples or checking the OD pool / network quality.",
            network_label, max_samples,
            fit_params.get("c_inf", float("nan")),
            fit_params.get("a", float("nan")),
            fit_params.get("tau", float("nan")),
            fit_params.get("r2", float("nan")),
        )

    # Cumulative failure rate log (CLAUDE.md traceability)
    if n_routed_total > 0:
        overall_fail_rate = n_fail_total / n_routed_total
        msg = (
            "[circuity][%s] overall snap/route failure rate: %.1f%% (%d/%d) "
            "across all rounds."
        )
        if overall_fail_rate > 0.05:
            logger.warning(msg, network_label, 100.0 * overall_fail_rate,
                           n_fail_total, n_routed_total)
        else:
            logger.info(msg, network_label, 100.0 * overall_fail_rate,
                        n_fail_total, n_routed_total)

    return (
        fit_params,
        tracker.history,
        np.array(cum_euclidean),
        np.array(cum_routed),
        np.array(cum_pool_idx, dtype=int),
    )


# ---------------------------------------------------------------------------
# Per-RS7 diagnostic
# ---------------------------------------------------------------------------

def _rs7_diagnostic(
    origins_xy: np.ndarray,
    dests_xy: np.ndarray,
    commune_ids: np.ndarray,
    cum_euclidean: np.ndarray,
    cum_routed: np.ndarray,
    sample_indices_used: np.ndarray,
    rs7_by_ags8: dict,
    global_fit: dict,
    p13_targets: dict,
    w12_targets: dict,
    network_label: str,
    min_samples: int,
) -> pd.DataFrame:
    """Fit circuity per RS7 on the already-routed cumulative sample.

    Tags each routed OD pair by origin RS7 (via commune_ids). Fits the curve
    per RS7 cell with at least min_samples pairs; cells below floor are flagged
    'under_sampled'. Returns a DataFrame with per-RS7 fit params + verdict.

    The promote/keep_global verdict: promote if max per-RS7 EMD delta vs global
    curve exceeds the global-vs-constant-1.3 EMD delta (i.e. RS7 matters more
    than the curve itself).
    """
    if len(cum_euclidean) == 0 or len(commune_ids) == 0:
        logger.warning("[circuity][%s] no data for per-RS7 diagnostic.", network_label)
        return pd.DataFrame()

    # Tag each cumulative sample pair by origin RS7.
    # sample_indices_used: 1-D int array of pool indices for the accepted pairs,
    # returned by _run_convergence_loop. Must satisfy:
    #   len(sample_indices_used) == len(cum_euclidean)
    # so commune_ids[sample_indices_used] gives each accepted pair's origin commune.
    if sample_indices_used is None:
        raise ValueError(
            f"[circuity][{network_label}] _rs7_diagnostic called with "
            "sample_indices_used=None; pass the cum_pool_idx from _run_convergence_loop "
            "so each accepted pair is correctly mapped to its origin commune_id."
        )
    if len(sample_indices_used) != len(cum_euclidean):
        raise ValueError(
            f"[circuity][{network_label}] _rs7_diagnostic: sample_indices_used length "
            f"({len(sample_indices_used)}) != cum_euclidean length ({len(cum_euclidean)}). "
            "The index array and the cumulative distance arrays must be in 1-to-1 correspondence."
        )
    raw_communes = commune_ids[sample_indices_used]

    rs7_tags = np.array([
        rs7_by_ags8.get(ars_to_ags8(c), -1) if c else -1
        for c in raw_communes
    ], dtype=int)

    rs7_codes = sorted(set(int(c) for c in rs7_tags if c > 0))
    logger.info("[circuity][%s] per-RS7 diagnostic: RS7 codes present: %s", network_label, rs7_codes)

    # Global EMD: global curve vs constant 1.3 (on P13 commute bands, all pairs)
    p13_zgb = p13_targets.get("03ZGB", None)
    if p13_zgb is not None:
        global_routed_by_curve = euclidean_to_routed(cum_euclidean, network="car",
                                                     params=None, mode="constant")
        shares_const = band_shares(global_routed_by_curve, edges=BAND_EDGES_KM)
        # Under fitted curve
        # Only the "car" key is used; drop unused walk/pt keys to avoid
        # magic-number placeholders that never influence the car-network evaluation.
        fitted_params = {
            "car": {"c_inf": global_fit["c_inf"], "a": global_fit["a"],
                    "tau": global_fit["tau"]},
        }
        from braunschweig.calibration.circuity import circuity_factor as _cf
        routed_by_fitted = cum_euclidean * _cf(
            cum_euclidean, network="car", params=fitted_params, mode="curve")
        shares_fitted = band_shares(routed_by_fitted, edges=BAND_EDGES_KM)
        emd_constant = emd_on_bands(shares_const, p13_zgb)
        emd_fitted = emd_on_bands(shares_fitted, p13_zgb)
        global_delta_emd = abs(emd_constant - emd_fitted)
        logger.info(
            "[circuity][%s] global EMD vs P13: constant=%.4f, fitted=%.4f, delta=%.4f",
            network_label, emd_constant, emd_fitted, global_delta_emd,
        )
    else:
        global_delta_emd = 0.0
        logger.warning(
            "[circuity][%s] '03ZGB' P13 target absent; global EMD delta = 0 "
            "(all RS7 cells will default to 'keep_global').", network_label,
        )

    rows = []
    for rs7 in rs7_codes:
        mask = rs7_tags == rs7
        n_rs7 = int(mask.sum())
        under_sampled = n_rs7 < min_samples
        row: dict = {
            "rs7": rs7,
            "n_samples": n_rs7,
            "under_sampled": under_sampled,
            "c_inf": float("nan"),
            "a": float("nan"),
            "tau_km": float("nan"),
            "r2": float("nan"),
            "emd_vs_p13_constant": float("nan"),
            "emd_vs_p13_fitted": float("nan"),
            "rs7_delta_emd": float("nan"),
            "verdict": "under_sampled" if under_sampled else "keep_global",
        }
        if not under_sampled:
            try:
                rs7_fit = fit_circuity_curve(cum_euclidean[mask], cum_routed[mask])
                row["c_inf"] = rs7_fit["c_inf"]
                row["a"] = rs7_fit["a"]
                row["tau_km"] = rs7_fit["tau"]
                row["r2"] = rs7_fit["r2"]

                # Per-RS7 EMD vs P13 ZGB target
                if p13_zgb is not None:
                    # Only the "car" key is evaluated; unused walk/pt keys are omitted
                    # to avoid hardcoded placeholder values that never affect the result.
                    rs7_params_dict = {
                        "car": {"c_inf": rs7_fit["c_inf"], "a": rs7_fit["a"],
                                "tau": rs7_fit["tau"]},
                    }
                    from braunschweig.calibration.circuity import circuity_factor as _cf
                    routed_rs7_fitted = cum_euclidean[mask] * _cf(
                        cum_euclidean[mask], network="car",
                        params=rs7_params_dict, mode="curve")
                    shares_rs7_const = band_shares(
                        cum_euclidean[mask] * LEGACY_DETOUR_FACTOR,
                        edges=BAND_EDGES_KM)
                    shares_rs7_fitted = band_shares(routed_rs7_fitted, edges=BAND_EDGES_KM)
                    emd_rs7_const = emd_on_bands(shares_rs7_const, p13_zgb)
                    emd_rs7_fitted = emd_on_bands(shares_rs7_fitted, p13_zgb)
                    rs7_delta = abs(emd_rs7_const - emd_rs7_fitted)
                    row["emd_vs_p13_constant"] = emd_rs7_const
                    row["emd_vs_p13_fitted"] = emd_rs7_fitted
                    row["rs7_delta_emd"] = rs7_delta
                    # Promote if this RS7's curve reduces EMD by more than the global curve does
                    verdict = "promote" if rs7_delta > global_delta_emd else "keep_global"
                    row["verdict"] = verdict
                    logger.info(
                        "[circuity][%s] RS7=%d n=%d  c_inf=%.4f a=%.4f tau=%.4f "
                        "R2=%.4f  emd_const=%.4f emd_fit=%.4f delta=%.4f -> %s",
                        network_label, rs7, n_rs7,
                        rs7_fit["c_inf"], rs7_fit["a"], rs7_fit["tau"], rs7_fit["r2"],
                        emd_rs7_const, emd_rs7_fitted, rs7_delta, verdict,
                    )
            except RuntimeError as exc:
                logger.warning(
                    "[circuity][%s] RS7=%d fit failed: %s", network_label, rs7, exc
                )
        rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Band-shift impact analysis
# ---------------------------------------------------------------------------

def _band_shift_impact(
    car_euclidean: np.ndarray,
    walk_euclidean: np.ndarray,
    car_fit: dict,
    walk_fit: dict,
    p13_targets: dict,
    w12_targets: dict,
    mid_dir: str,
) -> pd.DataFrame:
    """Compute commute and secondary EMD under constant 1.3 vs fitted curve.

    Returns a DataFrame with columns:
      trip_type, metric, emd_constant_1_3, emd_fitted_curve, delta
    """
    rows = []
    p13_zgb = p13_targets.get("03ZGB", None)

    # Named fit_params (not car_params) because the dict covers car+walk+pt;
    # the walk lookup below uses network="walk" on this same dict.
    fit_params = {
        "car": {"c_inf": car_fit["c_inf"], "a": car_fit["a"], "tau": car_fit["tau"]},
        "walk": {"c_inf": walk_fit["c_inf"], "a": walk_fit["a"], "tau": walk_fit["tau"]},
        "pt": {"uplift": 1.3, "base": "car"},
    }

    from braunschweig.calibration.circuity import circuity_factor as _cf

    # Commute (car, P13)
    if p13_zgb is not None and len(car_euclidean) > 0:
        const_routed = car_euclidean * LEGACY_DETOUR_FACTOR
        fit_routed = car_euclidean * _cf(car_euclidean, network="car",
                                         params=fit_params, mode="curve")
        shares_const = band_shares(const_routed, edges=BAND_EDGES_KM)
        shares_fit = band_shares(fit_routed, edges=BAND_EDGES_KM)
        emd_c = emd_on_bands(shares_const, p13_zgb)
        emd_f = emd_on_bands(shares_fit, p13_zgb)
        rows.append({
            "trip_type": "commute",
            "metric": "emd_vs_p13_zgb",
            "emd_constant_1_3": emd_c,
            "emd_fitted_curve": emd_f,
            "delta": emd_f - emd_c,
            "note": "negative delta = fitted curve reduces EMD (closer to MiD P13 target)",
        })
        logger.info(
            "[circuity] band-shift impact commute P13: const=%.4f fitted=%.4f delta=%.4f",
            emd_c, emd_f, emd_f - emd_c,
        )
    else:
        logger.warning("[circuity] band-shift commute: P13 ZGB target or car pool absent; skipped.")

    # Secondary (walk, W12) -- one row per purpose
    if walk_fit.get("n", 0) == 0:
        logger.warning(
            "[circuity] band-shift walk impact: walk_fit n=0 (placeholder, no real walk fit). "
            "Walk EMD deltas below are NOT from a real fit and must not be used for "
            "calibration decisions. Re-run with --osm-pbf to obtain a real walk curve."
        )
    if w12_targets and len(walk_euclidean) > 0:
        # Emit a SINGLE pooled row: walk_euclidean covers all secondary purposes
        # combined (the pool passed here has no purpose column), so separate
        # per-purpose EMDs would be identical and misleading.  The authoritative
        # per-purpose secondary EMD is produced by scripts/validate_secondary_distances.py.
        const_walk = walk_euclidean * LEGACY_DETOUR_FACTOR
        fit_walk = walk_euclidean * _cf(walk_euclidean, network="walk",
                                        params=fit_params, mode="curve")
        n_w12 = len(W12_BAND_EDGES_KM) - 1
        shares_const_w12 = band_shares(const_walk, edges=W12_BAND_EDGES_KM)
        shares_fit_w12 = band_shares(fit_walk, edges=W12_BAND_EDGES_KM)
        # Use the pooled W12 target (average of available purpose shares, or first available).
        pooled_w12 = next(
            (w12_targets[p] for p in ("shop", "leisure", "other") if p in w12_targets),
            None,
        )
        if pooled_w12 is not None:
            emd_c_w12 = emd_on_bands(shares_const_w12[:n_w12], pooled_w12[:n_w12])
            emd_f_w12 = emd_on_bands(shares_fit_w12[:n_w12], pooled_w12[:n_w12])
            rows.append({
                "trip_type": "secondary_walk_pooled",
                "metric": "emd_vs_w12",
                "emd_constant_1_3": emd_c_w12,
                "emd_fitted_curve": emd_f_w12,
                "delta": emd_f_w12 - emd_c_w12,
                "note": (
                    "pooled secondary walk (no purpose split here; "
                    "per-purpose EMD: scripts/validate_secondary_distances.py). "
                    "negative delta = fitted curve reduces EMD"
                ),
            })
            logger.info(
                "[circuity] band-shift impact secondary walk (pooled) W12: "
                "const=%.4f fitted=%.4f delta=%.4f",
                emd_c_w12, emd_f_w12, emd_f_w12 - emd_c_w12,
            )
    else:
        logger.warning("[circuity] band-shift secondary: W12 targets or walk pool absent; skipped.")

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Figure helpers (matplotlib; deferred import so py_compile stays light)
# ---------------------------------------------------------------------------

def _plot_convergence(history: list[dict], network_label: str, output_dir: str):
    """Write circuity_convergence_<net>.png: c_inf, a, tau vs sample size."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not history:
        return
    df_h = pd.DataFrame(history)
    fig, axes = plt.subplots(1, 3, figsize=(14, 4), sharex=True)
    for ax, param in zip(axes, ("c_inf", "a", "tau")):
        ax.plot(df_h["n"], df_h[param], marker="o", markersize=4)
        ax.set_xlabel("cumulative routed pairs")
        ax.set_ylabel(param)
        ax.set_title(f"{network_label}: {param} convergence")
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, f"circuity_convergence_{network_label}.png"), dpi=120)
    plt.close(fig)


def _plot_fit(euclidean_km: np.ndarray, routed_km: np.ndarray,
              fit_params: dict, network_label: str, output_dir: str):
    """Write circuity_fit_<net>.png: scatter of ratio + fitted curve + constant 1.3."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if len(euclidean_km) == 0:
        return
    # Subsample scatter for readability (max 5000 points)
    rng_plot = np.random.RandomState(0)
    idx_plot = rng_plot.choice(len(euclidean_km),
                                size=min(5000, len(euclidean_km)), replace=False)
    d_plot = euclidean_km[idx_plot]
    ratio_plot = routed_km[idx_plot] / np.maximum(d_plot, 1e-9)

    d_line = np.linspace(0.0, float(np.percentile(euclidean_km, 99)), 300)
    c_inf = fit_params["c_inf"]
    a = fit_params["a"]
    tau = fit_params["tau"]
    curve_line = c_inf + a * np.exp(-d_line / tau)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.scatter(d_plot, ratio_plot, alpha=0.08, s=6, color="#4C72B0", label="sample pairs")
    ax.plot(d_line, curve_line, color="#DD8452", linewidth=2,
            label=f"fitted: {c_inf:.3f}+{a:.3f}·exp(-d/{tau:.2f}), R²={fit_params['r2']:.3f}")
    ax.axhline(LEGACY_DETOUR_FACTOR, color="#55A868", linewidth=1.5,
               linestyle="--", label=f"constant {LEGACY_DETOUR_FACTOR}")
    ax.set_xlabel("euclidean distance (km)")
    ax.set_ylabel("circuity ratio routed/euclidean")
    ax.set_title(f"{network_label} circuity curve fit")
    ax.legend(fontsize=9)
    ax.set_ylim(bottom=0.9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, f"circuity_fit_{network_label}.png"), dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Summary markdown
# ---------------------------------------------------------------------------

def _write_summary(
    car_fit: dict, walk_fit: dict,
    df_rs7_car: pd.DataFrame, df_rs7_walk: pd.DataFrame,
    df_impact: pd.DataFrame,
    seed: int,
    output_dir: str,
):
    """Write summary.md with fit params, per-RS7 verdict, and band-shift impact statement."""
    lines = [
        "# Detour circuity fit summary",
        "",
        f"Random seed: {seed}",
        "",
        "## Fitted curve parameters",
        "",
        "Model: c(d) = c_inf + a * exp(-d / tau_km)",
        "",
        "| network | c_inf | a | tau_km | R2 | n_samples |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for label, fit in (("car", car_fit), ("walk", walk_fit)):
        lines.append(
            f"| {label} | {fit.get('c_inf', 'nan'):.4f} | {fit.get('a', 'nan'):.4f} | "
            f"{fit.get('tau', 'nan'):.4f} | {fit.get('r2', 'nan'):.4f} | {fit.get('n', 0)} |"
        )
    lines += [""]

    # Per-RS7 verdicts (car)
    if not df_rs7_car.empty:
        lines += ["## Per-RS7 diagnostic (car)", ""]
        n_promote = int((df_rs7_car["verdict"] == "promote").sum()) if "verdict" in df_rs7_car.columns else 0
        n_keep = int((df_rs7_car["verdict"] == "keep_global").sum()) if "verdict" in df_rs7_car.columns else 0
        n_under = int((df_rs7_car["verdict"] == "under_sampled").sum()) if "verdict" in df_rs7_car.columns else 0
        lines += [
            f"RS7 cells: {n_promote} promote, {n_keep} keep_global, {n_under} under_sampled.",
            "",
            "```",
            df_rs7_car[["rs7", "n_samples", "c_inf", "a", "tau_km", "r2",
                         "rs7_delta_emd", "verdict"]].to_string(index=False)
            if len(df_rs7_car.columns) > 0 else "(empty)",
            "```",
            "",
        ]
        any_promoted = n_promote > 0
    else:
        any_promoted = False

    # Is the curve material vs constant 1.3?
    if not df_impact.empty and "delta" in df_impact.columns:
        max_abs_delta = float(df_impact["delta"].abs().max())
        is_material = max_abs_delta > 0.01
        lines += [
            "## Band-shift impact vs constant 1.3",
            "",
            f"Maximum absolute EMD delta (fitted vs constant 1.3): {max_abs_delta:.4f}.",
            f"**Assessment: the distance-dependent circuity curve is {'MATERIAL' if is_material else 'NOT MATERIAL'} "
            f"vs the legacy constant 1.3** (threshold: EMD delta > 0.01).",
            "",
            "```",
            df_impact.to_string(index=False),
            "```",
            "",
        ]
    else:
        lines += [
            "## Band-shift impact vs constant 1.3",
            "",
            "Band-shift impact data absent (no P13/W12 targets or OD pool).",
            "",
        ]

    lines += [
        "## Per-RS7 verdict summary",
        "",
        ("RS7-specific curves are RECOMMENDED (at least one RS7 cell promoted)."
         if any_promoted else
         "RS7-specific curves are NOT necessary; global curve is sufficient."),
        "",
        "## Notes",
        "",
        "- car graph: MATSim processed network (cache supply.processed network.xml.gz)",
        "- walk graph: OSM PBF via pyrosm, EPSG:25832",
        "- OD pool: home->work (employed persons) + secondary (car/walk) + education legs",
        "- pt row: NOT fitted here; carried over from existing seed file (UNVERIFIED placeholder)",
        (
            "- walk fit: PLACEHOLDER (n=0, no real walk curve fitted; re-run with --osm-pbf). "
            "Walk impact figures above are NOT scientifically valid."
            if walk_fit.get("n", 0) == 0 else
            "- walk fit: real curve fitted from OSM walk network."
        ),
        "- REGENERATE: re-run scripts/calibrate_detour_circuity.py on the server after each synthesis update",
    ]

    summary_path = os.path.join(output_dir, "summary.md")
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    logger.info("[circuity] wrote summary to %s", summary_path)


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
            "Fit the distance-dependent detour/circuity curve c(d)=c_inf+a*exp(-d/tau) "
            "for the car and walk networks from cached synpp synthesis output."
        )
    )
    parser.add_argument(
        "--working-directory",
        default="eqasim-data/cache_bs_25pct_allfeat",
        help="Directory containing synpp stage pickles (default: eqasim-data/cache_bs_25pct_allfeat).",
    )
    parser.add_argument(
        "--osm-pbf", default=None,
        help="Path to the OSM PBF file for the walk network (required for walk graph).",
    )
    parser.add_argument(
        "--config", default=None,
        help="Run config YAML (used for random_seed; optional).",
    )
    parser.add_argument(
        "--mid-dir",
        default="eqasim-data/data/braunschweig/mid",
        help="Directory with MiD P13 and W12 CSVs (default: eqasim-data/data/braunschweig/mid).",
    )
    parser.add_argument(
        "--min-samples", type=int, default=8000,
        help="Minimum cumulative routed pairs before convergence can be declared (default: 8000).",
    )
    parser.add_argument(
        "--max-samples", type=int, default=50000,
        help="Maximum total routed pairs per network before giving up (default: 50000).",
    )
    parser.add_argument(
        "--convergence-tol", type=float, default=0.01,
        help="Relative parameter-change tolerance for convergence (default: 0.01).",
    )
    parser.add_argument(
        "--convergence-step", type=int, default=2000,
        help="OD pairs to add per convergence round (default: 2000).",
    )
    parser.add_argument(
        "--convergence-patience", type=int, default=2,
        help="Consecutive stable rounds required to declare convergence (default: 2).",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed (default: random_seed from config, or 0).",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Write diagnostic CSVs, figures, and summary.md here.",
    )
    parser.add_argument(
        "--params-output",
        default=DEFAULT_PARAMS_PATH,
        help=(
            f"Path to write the fitted params CSV "
            f"(default: {DEFAULT_PARAMS_PATH})."
        ),
    )
    args = parser.parse_args()

    wd = args.working_directory
    cfg: dict = {}

    # --- Load config for seed ---
    if args.config:
        cfg = _load_config(args.config)
        logger.info("[circuity] config loaded from '%s'", args.config)
    seed = args.seed if args.seed is not None else int(cfg.get("random_seed", 0))
    logger.info("[circuity] random seed: %d", seed)
    logger.info("[circuity] working directory: %s", wd)
    logger.info("[circuity] output dir: %s", args.output_dir)
    logger.info("[circuity] params output: %s", args.params_output)

    # --- Output directory ---
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    # --- Extract OD pools ---
    (car_origins, car_dests,
     walk_origins, walk_dests,
     car_commune_ids, walk_commune_ids) = _extract_od_pools(wd)

    if len(car_origins) == 0:
        raise RuntimeError(
            "[circuity] car OD pool is empty after extraction. "
            "Check that the working directory contains the expected stage pickles "
            "and that employed persons and work locations are present."
        )
    if len(walk_origins) == 0:
        logger.warning(
            "[circuity] walk OD pool is empty; walk curve fit will be skipped. "
            "Check secondary (walk/bike mode) and education (age 6-15) stage pickles."
        )

    # --- Load MiD targets for impact analysis ---
    p13_targets: dict = {}
    w12_targets: dict = {}
    try:
        p13_targets = load_p13_band_shares(args.mid_dir)
        logger.info("[circuity] loaded P13 targets for %d keys", len(p13_targets))
    except Exception as exc:
        logger.warning("[circuity] P13 targets not loaded: %s", exc)
    try:
        w12_targets = load_w12_band_shares(args.mid_dir)
        logger.info("[circuity] loaded W12 targets for purposes: %s",
                    [k for k in w12_targets if not k.endswith("_mean_km")])
    except Exception as exc:
        logger.warning("[circuity] W12 targets not loaded: %s", exc)

    # --- Load RegioStaR lookup for per-RS7 diagnostic ---
    rs7_by_ags8: dict = {}
    try:
        df_regiostar = _load_stage(wd, "braunschweig.data.bbsr.regiostar")
        # Drop rows with missing regiostar7 before building the dict so values are
        # plain Python ints (no pd.NA). The -1 sentinel is the only missing-marker
        # downstream (via .get(ags8, -1)).
        rs7_series = df_regiostar.dropna(subset=["regiostar7"]).set_index(
            "commune_id"
        )["regiostar7"]
        rs7_by_ags8 = {k: int(v) for k, v in rs7_series.items()}
        logger.info("[circuity] RS7 lookup loaded: %d Gemeinden", len(rs7_by_ags8))
    except Exception as exc:
        logger.warning("[circuity] RS7 lookup not available: %s; per-RS7 diagnostic skipped.", exc)

    # --- Build car graph ---
    # The real artifact is a .cache DIRECTORY named
    # matsim.scenario.supply.processed__<hash>.cache/ containing road_network.xml.gz
    # (the car-only network without PT links).  The parallel .p pickle returns a dict
    # with keys 'network_path' and 'schedule_path' holding bare filenames relative to
    # that .cache directory -- NOT absolute paths.  We glob for the .cache directory
    # and look for road_network.xml.gz inside it; fall back to network.xml.gz only
    # when road_network.xml.gz is absent.
    logger.info("[circuity] building car graph from MATSim processed network...")
    car_csr = None
    car_node_xy = None
    try:
        cache_dirs = glob.glob(
            os.path.join(wd, "matsim.scenario.supply.processed__*.cache")
        )
        if not cache_dirs:
            raise RuntimeError(
                "No matsim.scenario.supply.processed__*.cache directory found in "
                f"'{wd}'. Ensure the full synthesis pipeline including the MATSim "
                "supply stage has been run and cached."
            )
        # Use the most-recently-modified cache directory.
        supply_cache_dir = max(cache_dirs, key=os.path.getmtime)
        logger.info("[circuity] supply.processed cache dir: %s", supply_cache_dir)

        road_net = os.path.join(supply_cache_dir, "road_network.xml.gz")
        full_net = os.path.join(supply_cache_dir, "network.xml.gz")
        if os.path.isfile(road_net):
            net_path = road_net
            logger.info("[circuity] using road_network.xml.gz (car-only, no PT links)")
        elif os.path.isfile(full_net):
            net_path = full_net
            logger.warning(
                "[circuity] road_network.xml.gz not found; falling back to "
                "network.xml.gz (includes PT links — car routing may be less clean)."
            )
        else:
            raise RuntimeError(
                f"Neither road_network.xml.gz nor network.xml.gz found inside "
                f"'{supply_cache_dir}'."
            )

        logger.info("[circuity] reading MATSim network from '%s'", net_path)
        node_xy_arr, edges, _ = read_matsim_network(net_path)
        car_csr, car_node_xy = build_graph_from_edges(node_xy_arr, edges)
        logger.info("[circuity] car graph: %d nodes, %d edges", car_node_xy.shape[0], len(edges))
    except Exception as exc:
        raise RuntimeError(
            f"[circuity] failed to build car graph: {exc}. "
            "Check that matsim.scenario.supply.processed__*.cache exists in the cache "
            "directory and contains road_network.xml.gz."
        ) from exc

    # --- Build walk graph ---
    walk_csr = None
    walk_node_xy = None
    if args.osm_pbf:
        if not os.path.isfile(args.osm_pbf):
            raise FileNotFoundError(
                f"[circuity] OSM PBF not found: '{args.osm_pbf}'. "
                "Provide the correct path via --osm-pbf."
            )
        logger.info("[circuity] building walk graph from OSM PBF: %s", args.osm_pbf)
        try:
            walk_node_xy_arr, walk_edges, _ = read_walk_network_pyrosm(
                args.osm_pbf, bbox=None
            )
            walk_csr, walk_node_xy = build_graph_from_edges(walk_node_xy_arr, walk_edges)
            logger.info(
                "[circuity] walk graph: %d nodes, %d edges",
                walk_node_xy.shape[0], len(walk_edges),
            )
        except Exception as exc:
            logger.warning(
                "[circuity] walk graph build failed: %s; walk fit will be skipped.", exc
            )
    else:
        logger.warning(
            "[circuity] --osm-pbf not provided; walk graph will not be built. "
            "Walk circuity curve will carry the seed placeholder."
        )

    # --- Convergence loop: car ---
    rng_car = np.random.RandomState(seed)
    logger.info("[circuity] --- CAR convergence loop ---")
    car_fit, car_history, car_eucl, car_rout, car_pool_idx = _run_convergence_loop(
        "car", car_csr, car_node_xy,
        car_origins, car_dests,
        rng=rng_car,
        min_samples=args.min_samples,
        max_samples=args.max_samples,
        convergence_step=args.convergence_step,
        convergence_tol=args.convergence_tol,
        convergence_patience=args.convergence_patience,
    )
    logger.info(
        "[circuity] CAR fit: c_inf=%.4f a=%.4f tau=%.4f R2=%.4f n=%d",
        car_fit["c_inf"], car_fit["a"], car_fit["tau"], car_fit["r2"], car_fit["n"],
    )

    # --- Convergence loop: walk ---
    walk_fit: dict = {"c_inf": 1.20, "a": 0.40, "tau": 1.0, "r2": 0.0, "n": 0}
    walk_history: list[dict] = []
    walk_eucl: np.ndarray = np.zeros(0)
    walk_rout: np.ndarray = np.zeros(0)
    walk_pool_idx: np.ndarray = np.zeros(0, dtype=int)

    if walk_csr is not None and len(walk_origins) > 0:
        rng_walk = np.random.RandomState(seed + 1)
        logger.info("[circuity] --- WALK convergence loop ---")
        walk_fit, walk_history, walk_eucl, walk_rout, walk_pool_idx = _run_convergence_loop(
            "walk", walk_csr, walk_node_xy,
            walk_origins, walk_dests,
            rng=rng_walk,
            min_samples=args.min_samples,
            max_samples=args.max_samples,
            convergence_step=args.convergence_step,
            convergence_tol=args.convergence_tol,
            convergence_patience=args.convergence_patience,
        )
        logger.info(
            "[circuity] WALK fit: c_inf=%.4f a=%.4f tau=%.4f R2=%.4f n=%d",
            walk_fit["c_inf"], walk_fit["a"], walk_fit["tau"], walk_fit["r2"], walk_fit["n"],
        )
    else:
        logger.warning(
            "[circuity] walk convergence loop skipped (no walk graph or empty walk pool); "
            "walk params retain seed placeholder values."
        )

    # --- Per-RS7 diagnostic ---
    df_rs7_car = pd.DataFrame()
    df_rs7_walk = pd.DataFrame()

    if rs7_by_ags8 and len(car_eucl) > 0:
        logger.info("[circuity] --- per-RS7 diagnostic: car ---")
        df_rs7_car = _rs7_diagnostic(
            car_origins, car_dests,
            car_commune_ids,
            car_eucl, car_rout,
            sample_indices_used=car_pool_idx,
            rs7_by_ags8=rs7_by_ags8,
            global_fit=car_fit,
            p13_targets=p13_targets,
            w12_targets=w12_targets,
            network_label="car",
            min_samples=args.min_samples,
        )

    if rs7_by_ags8 and len(walk_eucl) > 0:
        logger.info("[circuity] --- per-RS7 diagnostic: walk ---")
        df_rs7_walk = _rs7_diagnostic(
            walk_origins, walk_dests,
            walk_commune_ids,
            walk_eucl, walk_rout,
            sample_indices_used=walk_pool_idx,
            rs7_by_ags8=rs7_by_ags8,
            global_fit=walk_fit,
            p13_targets=p13_targets,
            w12_targets=w12_targets,
            network_label="walk",
            min_samples=args.min_samples,
        )

    # Merge for the output CSV (car only for now; walk is a separate diagnostic)
    df_rs7_combined = pd.concat(
        [df_rs7_car.assign(network="car"), df_rs7_walk.assign(network="walk")],
        ignore_index=True,
    ) if not df_rs7_car.empty or not df_rs7_walk.empty else pd.DataFrame()

    # --- Band-shift impact ---
    df_impact = _band_shift_impact(
        car_eucl, walk_eucl,
        car_fit, walk_fit,
        p13_targets, w12_targets,
        mid_dir=args.mid_dir,
    )

    # --- Write params CSV (car/walk fitted; pt row preserved) ---
    # Read the existing seed file to carry over the pt row unchanged.
    params_path = os.path.abspath(args.params_output)
    os.makedirs(os.path.dirname(params_path), exist_ok=True)

    existing_pt_row: dict | None = None
    if os.path.isfile(params_path):
        try:
            df_existing = pd.read_csv(params_path, comment="#")
            pt_rows = df_existing[df_existing["network"] == "pt"]
            if not pt_rows.empty:
                existing_pt_row = pt_rows.iloc[0].to_dict()
                logger.info("[circuity] carried over existing pt row from '%s'", params_path)
        except Exception as exc:
            logger.warning("[circuity] could not read existing params CSV: %s", exc)

    if existing_pt_row is None:
        # Default placeholder (UNVERIFIED; matches the seed file provenance)
        existing_pt_row = {
            "network": "pt",
            "c_inf": "",
            "a": "",
            "tau_km": "",
            "uplift": 1.30,
            "base": "car",
            "n_samples": 0,
            "fit_r2": 0.0,
            "rs7": "",
            "provenance": "UNVERIFIED_Huang_Levinson_2015_circuity_in_urban_transit_networks",
        }
        logger.warning(
            "[circuity] pt row not found in existing params CSV; "
            "using hardcoded UNVERIFIED placeholder. "
            "Verify uplift=1.30 against Huang & Levinson (2015) before pinning."
        )

    rows_out = []
    for label, fit in (("car", car_fit), ("walk", walk_fit)):
        rows_out.append({
            "network": label,
            "c_inf": round(fit["c_inf"], 6),
            "a": round(fit["a"], 6),
            "tau_km": round(fit["tau"], 6),
            "uplift": "",
            "base": "",
            "n_samples": fit["n"],
            "fit_r2": round(fit["r2"], 6),
            "rs7": "",
            "provenance": f"fitted by scripts/calibrate_detour_circuity.py seed={seed}",
        })
    rows_out.append(existing_pt_row)

    df_params = pd.DataFrame(rows_out, columns=[
        "network", "c_inf", "a", "tau_km", "uplift", "base",
        "n_samples", "fit_r2", "rs7", "provenance",
    ])

    header_comment = (
        "# Distance-dependent detour/circuity parameters c(d)=c_inf+a*exp(-d/tau).\n"
        f"# car, walk: fitted by scripts/calibrate_detour_circuity.py (seed={seed})"
        " on the ZGB networks.\n"
        "# pt: NOT fitted -> c_pt(d)=c_car(d)*uplift. uplift provenance MUST be verified"
        " against Huang & Levinson (2015) before pinning (currently UNVERIFIED placeholder).\n"
    )
    with open(params_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(header_comment)
        df_params.to_csv(fh, index=False)
    logger.info("[circuity] wrote params CSV to '%s'", params_path)
    logger.info(
        "[circuity] TRACEABILITY: seed=%d  car: c_inf=%.4f a=%.4f tau=%.4f R2=%.4f n=%d | "
        "walk: c_inf=%.4f a=%.4f tau=%.4f R2=%.4f n=%d",
        seed,
        car_fit["c_inf"], car_fit["a"], car_fit["tau"], car_fit["r2"], car_fit["n"],
        walk_fit["c_inf"], walk_fit["a"], walk_fit["tau"], walk_fit["r2"], walk_fit["n"],
    )

    # --- Write diagnostics (if --output-dir) ---
    if args.output_dir:
        od = args.output_dir

        # Convergence CSVs
        for label, history in (("car", car_history), ("walk", walk_history)):
            if history:
                pd.DataFrame(history).to_csv(
                    os.path.join(od, f"circuity_convergence_{label}.csv"), index=False
                )
                logger.info("[circuity] wrote convergence CSV for %s", label)

        # Per-RS7 CSV
        if not df_rs7_combined.empty:
            df_rs7_combined.to_csv(os.path.join(od, "circuity_by_rs7.csv"), index=False)
            logger.info("[circuity] wrote per-RS7 diagnostic to circuity_by_rs7.csv")

        # Band-shift impact CSV
        if not df_impact.empty:
            df_impact.to_csv(os.path.join(od, "band_shift_impact.csv"), index=False)
            logger.info("[circuity] wrote band-shift impact to band_shift_impact.csv")

        # Figures
        _plot_convergence(car_history, "car", od)
        _plot_convergence(walk_history, "walk", od)
        _plot_fit(car_eucl, car_rout, car_fit, "car", od)
        if len(walk_eucl) > 0:
            _plot_fit(walk_eucl, walk_rout, walk_fit, "walk", od)

        # Summary markdown
        _write_summary(
            car_fit, walk_fit,
            df_rs7_car, df_rs7_walk,
            df_impact,
            seed=seed,
            output_dir=od,
        )

    logger.info("[circuity] calibrate_detour_circuity.py complete.")


if __name__ == "__main__":
    main()
