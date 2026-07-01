"""Calibrate per-distance-band gravity friction factors to MiD P13 commute distribution.

Extracts the gravity + assignment inputs once from a cached synpp working_directory,
then iterates per-band friction factors (Furness loop) against the realised
home->work straight-line distance distribution, and prints the pinned
``gravity_friction_factors`` YAML block + writes an eval CSV + report JSON.

The BA Pendleratlas calibration (``_calibrate`` in braunschweig.gravity.model) is
always applied INSIDE the loop: the BA stays the authoritative inter-Kreis control
and the Furness factors only reshape the within-pair distribution.

Run (after a 1 % or 25 % synthesis exists in the working directory):
  python scripts/calibrate_gravity_distribution.py \\
    --working-directory eqasim-data/cache_bs_1pct_allfeat_full \\
    --config config_server_braunschweig_1pct_allfeat_popsim.yml \\
    --output-dir eqasim-data/data/braunschweig/calibration/commute

The actual run-on-cache step is a DEFERRED SERVER STEP: the calibration caches
(cache_bs_*) are local-only (not committed) and the pipeline depends on synpp;
the script cannot be invoked locally. Verify syntax with py_compile, then run on
the server where the caches live.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.gravity.friction import (  # noqa: E402
    BAND_EDGES_KM,
    build_friction_matrix,
)
from braunschweig.gravity.model import (  # noqa: E402
    _build_origin_slope_vector,
    _calibrate,
    evaluate_gravity,
    _synthesise_intra_kreis,
)
from braunschweig.calibration.metrics import band_shares, emd_on_bands  # noqa: E402
from braunschweig.calibration.targets import load_p13_band_shares, load_p13_band_shares_by_rs7  # noqa: E402
from braunschweig.calibration.commute import (  # noqa: E402
    build_validation_report,
    furness_update,
    shrink_sparse_factors,
)
from braunschweig.data.bbsr.regiostar import ars_to_ags8  # noqa: E402

logger = logging.getLogger(__name__)

# Number of friction bands (aligned to BAND_EDGES_KM).
N_BANDS = len(BAND_EDGES_KM) - 1  # 7

# Mid-points of each distance band used to seed the initial factor vector from
# the gravity slope (f_b ~ exp(slope * mid_b)). The last band is open-ended;
# we use 150 km as a representative long-haul mid-point.
_BAND_MIDPOINTS_KM = (2.5, 7.5, 15.0, 25.0, 40.0, 75.0, 150.0)
assert len(_BAND_MIDPOINTS_KM) == N_BANDS, "Band midpoints must match N_BANDS"


# ---------------------------------------------------------------------------
# Stage-cache loading (mirrors calibrate_education_slopes.py)
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


# ---------------------------------------------------------------------------
# Config loading (YAML via PyYAML)
# ---------------------------------------------------------------------------

def _load_config(config_path: str) -> dict:
    """Load a YAML run config into a flat dict (top-level only)."""
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required to load the run config. "
            "Install with: conda install pyyaml"
        ) from exc
    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    # The run config is typically {run: [...], config: {key: value}}.
    if isinstance(raw, dict) and "config" in raw:
        return raw["config"]
    return raw or {}


# ---------------------------------------------------------------------------
# assign_and_measure: simulate the work-location assignment for one OD matrix
# ---------------------------------------------------------------------------

def assign_and_measure(od_matrix, municipalities, df_homes, df_work_locations,
                       df_population, random_seed):
    """Assign work locations from the OD matrix and measure realised commute km.

    Parameters
    ----------
    od_matrix : np.ndarray, shape (N, N)
        Row-normalised doubly-constrained gravity flow matrix (output of
        evaluate_gravity + _calibrate converted to a numpy array).
        Rows = origins (indexed by ``municipalities``), columns = destinations.
    municipalities : list[str]
        Commune IDs aligned with the rows/cols of ``od_matrix``.
    df_homes : pd.DataFrame
        Home locations with columns ``person_id``, ``commune_id``, ``x_m``, ``y_m``
        (home coordinates in EPSG:25832 metres). One row per working person.
    df_work_locations : gpd.GeoDataFrame
        Work location candidates with ``commune_id``, ``geometry`` (metric CRS).
    df_population : pd.DataFrame
        Used for the Kreis key (columns ``commune_id``, ``weight``); needed
        to map worker origin Gemeinde -> Kreis.
    random_seed : int

    Returns
    -------
    km_by_kreis : dict[str, np.ndarray]
        Realised straight-line commute distances (km) per home-Kreis (5-digit ars5).
    jobs_by_gemeinde : dict[str, float]
        Realised assigned-job counts per destination Gemeinde.
    km_by_rs7 : dict[int, np.ndarray]
        Realised straight-line commute distances (km) per home-RS7 code. Only
        populated when ``df_homes`` contains an ``rs7`` column; otherwise an
        empty dict. The per-RS7 measurement is exact (per-worker, not a Kreis
        proxy) and is used by the per-RS7 Furness update path.

    Notes
    -----
    This is a simplified in-process version of
    ``synthesis.population.spatial.primary.candidates`` /
    ``synthesis.population.spatial.primary.locations``: for each worker, sample a
    destination Gemeinde from the OD row for their home Gemeinde (multinomial),
    then sample a work location uniformly from that Gemeinde's work locations
    (using ``employees`` weight if present). Straight-line distance = L2 norm of
    home - work in metric coordinates.

    CACHE KEY UNCERTAINTY: ``df_homes`` is loaded from stage
    ``synthesis.population.spatial.home.locations`` -- the schema must have columns
    ``person_id``, ``commune_id``, and geometry (GeoDataFrame). The caller
    pre-extracts x/y from the geometry column. If the stage schema differs on the
    server, the x/y extraction step must be adjusted.
    """
    rng = np.random.RandomState(random_seed)
    muni_index = {c: i for i, c in enumerate(municipalities)}

    # Pre-build work location arrays per destination commune.
    # ``df_work_locations`` schema: commune_id, geometry (with employees weight).
    work_xy: dict[str, np.ndarray] = {}
    work_weights: dict[str, np.ndarray] = {}
    for commune_id, grp in df_work_locations.groupby("commune_id"):
        xy = np.column_stack([grp.geometry.x.values, grp.geometry.y.values])
        work_xy[commune_id] = xy
        if "employees" in grp.columns and grp["employees"].sum() > 0:
            w = grp["employees"].values.astype(float)
            work_weights[commune_id] = w / w.sum()
        else:
            work_weights[commune_id] = np.ones(len(grp)) / len(grp)

    km_by_kreis: dict[str, list] = {}
    km_by_rs7: dict[int, list] = {}
    jobs_by_gemeinde: dict[str, float] = {}
    has_rs7_col = "rs7" in df_homes.columns
    # F2: use a local variable instead of a function attribute.
    # F6: called "skipped" because no alternative assignment is made (it is a drop,
    #     not a fallback to a different method).
    n_skipped = 0

    for _, row in df_homes.iterrows():
        origin_commune = str(row["commune_id"])
        home_x = float(row["x_m"])
        home_y = float(row["y_m"])

        # Identify home Kreis (first 5 characters of the 8-digit AGS commune_id).
        origin_ars5 = origin_commune[:5]
        # Home RS7 code (present when the caller tagged worker_homes_gdf["rs7"]).
        home_rs7 = int(row["rs7"]) if has_rs7_col else -1

        if origin_commune not in muni_index:
            # Origin not in the gravity matrix; skip (very rare edge case).
            logger.debug("Origin commune %s not in OD matrix; skipping person", origin_commune)
            continue

        od_row = od_matrix[muni_index[origin_commune], :]
        od_sum = od_row.sum()
        if od_sum <= 0:
            continue
        prob = od_row / od_sum

        # Draw destination Gemeinde
        dest_idx = int(rng.choice(len(municipalities), p=prob))
        dest_commune = municipalities[dest_idx]

        # Sample a work location in the destination commune.
        if dest_commune not in work_xy or len(work_xy[dest_commune]) == 0:
            # No work locations in the drawn destination commune -- worker is dropped
            # (no alternative assignment is attempted). Counted as skipped below.
            n_skipped += 1
            continue
        xy = work_xy[dest_commune]
        w = work_weights[dest_commune]
        loc_idx = int(rng.choice(len(xy), p=w))
        work_x, work_y = xy[loc_idx]

        # Straight-line distance in km.
        dx = work_x - home_x
        dy = work_y - home_y
        d_km = np.sqrt(dx * dx + dy * dy) / 1000.0

        km_by_kreis.setdefault(origin_ars5, []).append(d_km)
        if home_rs7 > 0:
            km_by_rs7.setdefault(home_rs7, []).append(d_km)
        jobs_by_gemeinde[dest_commune] = jobs_by_gemeinde.get(dest_commune, 0.0) + 1.0

    # Report skip rate (CLAUDE.md no-silent-fallback).
    # Workers are skipped (dropped, not reassigned) when the drawn destination
    # Gemeinde has no work locations in the cached work_locations stage.
    n_workers = len(df_homes)
    n_assigned = n_workers - n_skipped
    assigned_pct = 100.0 * n_assigned / n_workers if n_workers else 0.0
    skipped_pct = 100.0 * n_skipped / n_workers if n_workers else 0.0
    if n_skipped > 0.05 * n_workers:
        logger.warning(
            "[commute-calib] work-location assignment: assigned %d/%d (%.1f%%), "
            "skipped (no work locations in drawn destination) %d/%d (%.1f%%) -- "
            "high skip rate may indicate a commune mismatch; check work_locations stage.",
            n_assigned, n_workers, assigned_pct, n_skipped, n_workers, skipped_pct,
        )
    else:
        logger.info(
            "[commute-calib] work-location assignment: assigned %d/%d (%.1f%%), "
            "skipped %d/%d (%.1f%%)",
            n_assigned, n_workers, assigned_pct, n_skipped, n_workers, skipped_pct,
        )

    return (
        {k: np.array(v) for k, v in km_by_kreis.items()},
        jobs_by_gemeinde,
        {k: np.array(v) for k, v in km_by_rs7.items()},
    )


# ---------------------------------------------------------------------------
# YAML output helpers
# ---------------------------------------------------------------------------

def _yaml_factors_block(factors, label="global"):
    """Format a factors dict (band -> float) or (rs7 -> {band -> float}) as YAML."""
    if not factors:
        return "gravity_friction_factors: {}"
    lines = ["gravity_friction_factors:"]
    if isinstance(list(factors.values())[0], dict):
        # Per-RS7 factors
        for rs7 in sorted(factors.keys()):
            rf = factors[rs7]
            pairs = ", ".join(f"{b}: {rf[b]:.6f}" for b in sorted(rf.keys()))
            lines.append(f"  {rs7}: {{{pairs}}}")
    else:
        # Global per-band factors
        pairs = ", ".join(f"{b}: {factors[b]:.6f}" for b in sorted(factors.keys()))
        lines.append(f"  {{{pairs}}}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# OD row-normalisation helper (used in two places inside the Furness loop)
# ---------------------------------------------------------------------------

def _row_normalise_od(df_od: "pd.DataFrame") -> "pd.DataFrame":
    """Row-normalise an OD DataFrame with columns origin_id, destination_id, weight.

    Rows whose origin total is zero get weight=1 on the self-loop and total=1
    (the self-loop sentinel used throughout the Furness loop).
    Returns df with columns [origin_id, destination_id, weight].
    """
    df_total = (
        df_od[["origin_id", "weight"]].groupby("origin_id").sum()
        .reset_index().rename(columns={"weight": "total"})
    )
    df_od = df_od.merge(df_total, on="origin_id")
    f_missing = df_od["total"] == 0.0
    df_od.loc[f_missing & (df_od["origin_id"] == df_od["destination_id"]), "weight"] = 1.0
    df_od.loc[f_missing, "total"] = 1.0
    df_od["weight"] = df_od["weight"] / df_od["total"]
    return df_od[["origin_id", "destination_id", "weight"]]


# ---------------------------------------------------------------------------
# TAZ calibration branch (_run_taz_calibration)
# ---------------------------------------------------------------------------

def _run_taz_calibration(args, cfg, slope, constant, diagonal, slope_overrides, seed):
    """TAZ variant of the calibration: re-fit per-RS7 friction on the TAZ work OD.

    Loads population-level stages from the cache (no popsim rebuild), builds the TAZ
    layer via build_taz_calibration_inputs, then runs the Furness loop through
    compute_work_od. Emits work_gravity_friction_factors (work-pass scoped; Task 4
    wires model.py to read this key).

    The BA Pendleratlas Kreis anchor is preserved: _calibrate is called after each
    compute_work_od invocation, mirroring execute()'s TAZ ON path. compute_work_od
    returns an UNcalibrated row-normalised OD (it does not call _calibrate internally);
    without this explicit call the inter-Kreis flows would be shaped only by the
    gravity friction and not anchored to observed BA Pendleratlas Kreis-pair flows.
    """
    import geopandas as gpd

    from braunschweig.data.building_potentials import assign_commune, load_potentials
    from braunschweig.data.spatial.taz import filter_to_scope, load_taz_zones
    from braunschweig.gravity.model import (
        _calibrate,
        _synthesise_intra_kreis,
        compute_work_od,
    )
    from braunschweig.calibration.commute_taz import (
        assign_and_measure_taz,
        build_taz_calibration_inputs,
        build_work_by_taz,
    )
    from braunschweig.gravity.taz_margins import assign_taz
    from braunschweig.calibration.metrics import band_shares, emd_on_bands
    from braunschweig.calibration.targets import (
        load_p13_band_shares,
        load_p13_band_shares_by_rs7,
    )
    from braunschweig.calibration.commute import furness_update, shrink_sparse_factors

    wd = args.working_directory

    # -----------------------------------------------------------------------
    # 1. Load cached inputs (direct pickle, no synpp rebuild)
    # Resolved cache stage names (verified against cache_bs_100pct_allfeat_popsim):
    # config aliases resolve to concrete cached stages; _load_stage globs the
    # concrete name (NOT the alias).
    # -----------------------------------------------------------------------
    df_homes = _load_stage(wd, "braunschweig.synthesis.locations.home_cell")
    df_population = _load_stage(wd, "braunschweig.popsim.stage")
    df_employees = _load_stage(wd, "braunschweig.data.census.employees")
    df_municipalities = _load_stage(wd, "data.spatial.municipalities")
    df_regiostar = _load_stage(wd, "braunschweig.data.bbsr.regiostar")
    df_work = _load_stage(wd, "braunschweig.locations.work")
    df_pendler = _load_stage(wd, "braunschweig.data.census.pendler")
    df_employment = _load_stage(wd, "braunschweig.data.census.employment")

    # Building potentials: the building_potentials STAGE is not cached (only
    # braunschweig.data.buildings, which lacks potential_work), so read the
    # committed parquet directly and replicate the stage execute() prep:
    # load_potentials() validates + ensures EPSG:25832; assign_commune() attaches
    # commune_id via representative_point sjoin (primary) + nearest-zone fallback.
    # This matches exactly what build_dest_attraction_per_taz consumes in model.py.
    _bp_path = os.path.join(
        cfg.get("data_path", "eqasim-data/data"),
        cfg.get("building_potentials_path",
                "braunschweig/buildings/building_activity_potentials.parquet"),
    )
    df_buildings_raw = load_potentials(_bp_path)
    df_buildings, bp_primary, bp_fallback = assign_commune(df_buildings_raw, df_municipalities)
    bp_total = bp_primary + bp_fallback
    logger.info(
        "[commute-taz] building_potentials: %d buildings loaded; commune join primary %d (%.1f%%), "
        "fallback %d (%.1f%%)",
        len(df_buildings), bp_primary, 100.0 * bp_primary / bp_total if bp_total else 0.0,
        bp_fallback, 100.0 * bp_fallback / bp_total if bp_total else 0.0,
    )

    # -----------------------------------------------------------------------
    # 2. Build TAZ layer
    # -----------------------------------------------------------------------
    prefixes = [p.strip() for p in args.political_prefix.split(",") if p.strip()]
    df_taz = filter_to_scope(load_taz_zones(args.taz_parquet), prefixes)
    inp = build_taz_calibration_inputs(
        df_taz, df_homes, df_population, df_employees, df_buildings, df_municipalities)
    zones = inp["zones"]

    # Tag work locations with TAZ; pre-build the per-TAZ work-location sampler.
    # assign_taz returns (gdf[id_column, taz_id, commune_id], primary, fallback).
    work = df_work.copy().reset_index(drop=True)
    work["work_row_id"] = work.index.astype(str)
    work["_kreis"] = work["commune_id"].astype(str).str[:5]
    work_map, work_primary, work_fallback = assign_taz(
        work, df_taz, id_column="work_row_id", kreis_column="_kreis")
    logger.info(
        "[commute-taz] work-location->TAZ primary %d / fallback %d",
        work_primary, work_fallback,
    )
    work = work.merge(work_map[["work_row_id", "taz_id"]], on="work_row_id", how="left")
    n_drop = int(work["taz_id"].isna().sum())
    if n_drop:
        logger.warning(
            "[commute-taz] %d work locations had no taz_id after assign_taz -- dropped",
            n_drop,
        )
    work_by_taz = build_work_by_taz(work.dropna(subset=["taz_id"]))

    # -----------------------------------------------------------------------
    # 3. MiD P13 targets (committed references only)
    # -----------------------------------------------------------------------
    targets = load_p13_band_shares(args.mid_dir)
    rs7_targets = load_p13_band_shares_by_rs7(args.mid_dir)
    logger.info(
        "[commute-taz] loaded P13 targets: ZGB=%s, per-RS7 codes=%s",
        "03ZGB" in targets, sorted(rs7_targets.keys()),
    )

    # -----------------------------------------------------------------------
    # 4. BA Pendleratlas Kreis anchor (same construction as the Gemeinde path)
    # -----------------------------------------------------------------------
    scope = prefixes
    mask = df_pendler["orig_ars"].isin(scope) | df_pendler["dest_ars"].isin(scope)
    df_pendler_scope = df_pendler[mask].copy()
    df_pendler_scope = _synthesise_intra_kreis(df_pendler_scope, df_employment, scope)

    # Population frame for _calibrate (TAZ ON path): population_key="taz_id",
    # population_value="population". inp["df_pop_taz"] has column "origin_id"
    # (renamed from "taz_id" by build_taz_calibration_inputs); we rename it back
    # so _calibrate's groupby(population_key) works correctly.
    df_pop_for_calibrate = inp["df_pop_taz"].rename(
        columns={"origin_id": "taz_id"})[["taz_id", "population"]].copy()

    # -----------------------------------------------------------------------
    # 5. Furness loop (per-RS7) via compute_work_od
    # -----------------------------------------------------------------------
    factors_by_rs7 = {rs7: np.ones(N_BANDS) for rs7 in sorted(rs7_targets)}
    iter_log, converged = [], False
    km_by_rs7 = {}
    for it in range(args.max_iterations):
        friction_factors = {int(rs7): {int(b): float(factors_by_rs7[rs7][b])
                                       for b in range(N_BANDS)}
                            for rs7 in factors_by_rs7}
        work_od = compute_work_od(
            df_population=inp["df_pop_taz"],
            df_employees=inp["df_emp_taz"],
            df_distances=inp["df_dist_taz"],
            df_regiostar=df_regiostar,
            rs7_by_zone=inp["rs7_by_zone"],
            slope=slope,
            constant=constant,
            diagonal=diagonal,
            slope_overrides=slope_overrides,
            friction_factors=friction_factors,
            max_iterations=int(cfg.get("gravity_max_iterations", 50)),
        )
        # BA Pendleratlas Kreis anchor.
        # VERIFIED: compute_work_od returns an UNcalibrated row-normalised OD (it
        # calls build_friction_matrix + evaluate_gravity + row-normalise only;
        # _calibrate is applied separately by execute()). So we must call _calibrate
        # here, mirroring execute()'s TAZ ON path, else the inter-Kreis BA control
        # is lost. population_key="taz_id"/population_value="population" matches
        # the ON path in execute() (lines 1187-1192 in model.py).
        work_od = _calibrate(
            work_od, df_pop_for_calibrate, df_pendler_scope,
            zone_to_kreis=inp["zone_to_kreis"],
            population_key="taz_id",
            population_value="population",
        )
        # _calibrate returns a 'flow' column (not 'weight'); re-normalise to weights.
        if "flow" in work_od.columns and "weight" not in work_od.columns:
            work_od = work_od.rename(columns={"flow": "weight"})
        work_od = _row_normalise_od(work_od)

        od_pivot = (work_od.pivot(index="origin_id", columns="destination_id", values="weight")
                    .reindex(index=zones, columns=zones).fillna(0.0))
        od_matrix = od_pivot.values

        _, km_by_rs7, skip_rate = assign_and_measure_taz(
            od_matrix, zones, inp["home_taz"], work_by_taz,
            inp["rs7_by_zone"], random_seed=seed + it,
        )

        emds = {}
        for rs7 in list(factors_by_rs7):
            km = km_by_rs7.get(rs7, np.array([]))
            if len(km) < args.min_count:
                continue
            shares = band_shares(np.asarray(km, float) * args.detour_factor)
            tgt = (rs7_targets.get(rs7)
                   if rs7_targets.get(rs7) is not None
                   else targets.get("03ZGB"))
            if tgt is None:
                continue
            emds[rs7] = emd_on_bands(shares, tgt)
            factors_by_rs7[rs7] = furness_update(factors_by_rs7[rs7], tgt, shares)

        max_emd = max(emds.values()) if emds else float("nan")
        iter_log.append({"iter": it, "max_emd": max_emd, "emds": emds,
                          "skip_rate": skip_rate})
        logger.info(
            "[commute-taz] iter %d max_emd=%.4f skip_rate=%.3f (per-RS7 %s)",
            it, max_emd, skip_rate,
            {k: round(v, 3) for k, v in emds.items()},
        )
        if np.isnan(max_emd):
            raise RuntimeError(
                "[commute-taz] EMD is NaN -- no RS7 cell had >= min_count workers "
                "(%d). Check that home_taz rs7_by_zone covers the TAZ zones and "
                "that min_count (%d) is not too high for the sample size."
                % (args.min_count, args.min_count)
            )
        if max_emd <= args.emd_threshold:
            converged = True
            break

    if not converged:
        logger.warning(
            "[commute-taz] did not converge within %d iterations; "
            "final max_emd=%.4f (threshold=%.4f). "
            "The Furness loop is limited by the achievable within-pair portion.",
            args.max_iterations, max_emd, args.emd_threshold,
        )

    # -----------------------------------------------------------------------
    # 6. Shrink sparse RS7 cells + emit
    # -----------------------------------------------------------------------
    # Build per-(RS7, band) histograms for shrinkage. A scalar total-per-RS7 would
    # make sparse-band detection all-or-nothing; the band histogram lets shrinkage
    # act independently on each distance band (matching the Gemeinde path, ~L1187).
    counts_by_rs7 = {}
    for rs7 in factors_by_rs7:
        km_rs7 = km_by_rs7.get(rs7, [])
        if len(km_rs7) > 0:
            km_detoured = np.asarray(km_rs7, dtype=float) * args.detour_factor
            band_hist = np.zeros(N_BANDS, dtype=int)
            for b in range(N_BANDS):
                lo = BAND_EDGES_KM[b]
                hi = BAND_EDGES_KM[b + 1]
                band_hist[b] = int(np.sum((km_detoured >= lo) & (km_detoured < hi)))
            counts_by_rs7[rs7] = band_hist
        else:
            counts_by_rs7[rs7] = np.zeros(N_BANDS, dtype=int)
    pooled = np.mean([factors_by_rs7[r] for r in factors_by_rs7], axis=0)
    factors_by_rs7, shrink_rate = shrink_sparse_factors(
        factors_by_rs7, counts_by_rs7, pooled, args.min_count)
    logger.info("[commute-taz] shrinkage rate %.3f; converged=%s", shrink_rate, converged)

    final = {
        int(rs7): {int(b): float(factors_by_rs7[rs7][b]) for b in range(N_BANDS)}
        for rs7 in factors_by_rs7
    }
    # Output under work_gravity_friction_factors (work-pass scoped, not education).
    yaml_block = _yaml_factors_block(final).replace(
        "gravity_friction_factors:", "work_gravity_friction_factors:")
    print("\n# Paste under 'work_gravity_friction_factors' in config_*braunschweig*.yml")
    print(yaml_block)

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        report_path = os.path.join(args.output_dir, "taz_friction_report.json")
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump({
                "converged": converged,
                "iter_log": iter_log,
                "work_gravity_friction_factors": final,
                "shrinkage_rate": shrink_rate,
            }, fh, indent=2)
        logger.info("[commute-taz] wrote report to %s", report_path)

    return 0


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
            "Calibrate per-distance-band gravity friction factors to MiD P13 "
            "commute distribution. Reads cached synpp stage pickles."
        )
    )
    parser.add_argument(
        "--working-directory", required=True,
        help="Directory containing synpp stage pickles (e.g. eqasim-data/cache_bs_1pct_allfeat_full).",
    )
    parser.add_argument(
        "--mid-dir", default="eqasim-data/data/braunschweig/mid",
        help="Directory with MiD P13 CSV (default: eqasim-data/data/braunschweig/mid).",
    )
    parser.add_argument(
        "--config", default=None,
        help="Run config YAML for gravity_slope/constant/diagonal/RS7 overrides.",
    )
    parser.add_argument(
        "--detour-factor", type=float, default=1.3,
        help="Euclidean->routed detour factor applied to model output before band-share "
             "comparison with the MiD P13 routed targets (default: 1.3).",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=30,
        help="Maximum number of Furness iterations (default: 30).",
    )
    parser.add_argument(
        "--emd-threshold", type=float, default=0.08,
        help=(
            "Stop when EMD falls at or below this value (default: 0.08). "
            "Global mode: ZGB-aggregate EMD. "
            "Per-RS7 mode: max EMD across all RS7 codes."
        ),
    )
    parser.add_argument(
        "--per-rs7", action="store_true",
        help=(
            "Calibrate per-RS7 friction factors rather than a single global factor set. "
            "Each RS7 code (72-77) is calibrated to its own commute-distance distribution "
            "from MiD 2023 Tabelle P13 Raumtyp block "
            "(mid2023_P13_commute_distance_by_rs7.csv). RS7 71 (Metropole) is absent "
            "from the ZGB sample. An RS7 present among origins but absent from the P13 "
            "Raumtyp table falls back to the ZGB aggregate target (logged explicitly)."
        ),
    )
    parser.add_argument(
        "--min-count", type=int, default=50,
        help="Minimum worker count per (RS7, band) cell for shrinkage (default: 50).",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for work-location sampling (default: random_seed from config, or 0).",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Write gravity_calibration_results.csv and gravity_calibration_report.json here.",
    )
    parser.add_argument(
        "--taz", action="store_true",
        help="Calibrate on the TAZ work-gravity path (sub-Gemeinde zones). Builds the "
             "TAZ layer in-script from the RVB parquet + cached population stages; writes "
             "work_gravity_friction_factors (scoped to the work pass, not education).",
    )
    parser.add_argument(
        "--taz-parquet",
        default="eqasim-data/data/braunschweig/taz/rvb_verkehrszellen_epsg25832.parquet",
        help="RVB Verkehrszellen parquet (used only with --taz).",
    )
    parser.add_argument(
        "--political-prefix",
        default="03101,03102,03103,03151,03153,03154,03157,03158",
        help="Comma-separated 5-digit Kreis ARS to scope the TAZ zones to (used with --taz).",
    )
    args = parser.parse_args()

    wd = args.working_directory

    # ------------------------------------------------------------------
    # 1. Load run config values
    # ------------------------------------------------------------------
    slope = -0.065        # project default from config_*braunschweig*.yml
    constant = -2.4
    diagonal = 1.0
    slope_overrides = None
    cfg = {}

    if args.config:
        cfg = _load_config(args.config)
        slope = float(cfg.get("gravity_slope", slope))
        constant = float(cfg.get("gravity_constant", constant))
        diagonal = float(cfg.get("gravity_diagonal", diagonal))
        slope_overrides = cfg.get("gravity_slope_by_regiostar7", None) or None
        logger.info(
            "Config loaded: slope=%.4f, constant=%.4f, diagonal=%.4f, "
            "slope_by_rs7=%s",
            slope, constant, diagonal,
            "set" if slope_overrides else "none",
        )
    else:
        logger.info(
            "No --config supplied; using project defaults: "
            "slope=%.4f, constant=%.4f, diagonal=%.4f", slope, constant, diagonal
        )

    # F9: resolve random seed: explicit --seed > config random_seed > 0.
    seed = args.seed if args.seed is not None else int(cfg.get("random_seed", 0))
    logger.info("[commute-calib] random seed: %d", seed)

    # Dispatch to TAZ calibration branch when --taz is given; returns here.
    if args.taz:
        return _run_taz_calibration(args, cfg, slope, constant, diagonal, slope_overrides, seed)

    # ------------------------------------------------------------------
    # 2. Load required stage pickles
    # ------------------------------------------------------------------
    logger.info("Loading synthesis stage caches from: %s", wd)

    # Distance matrix between Gemeinden (commune_id pairs + distance_km).
    # CACHE KEY NOTE: the upstream eqasim-common stage is named
    # 'eqasim_common.gravity.distance_matrix'. Verify on the server that a
    # pickle with this prefix exists; if not, check whether the stage is
    # cached as 'gravity.distance_matrix' (without the eqasim_common prefix).
    df_distances = _load_stage(wd, "eqasim_common.gravity.distance_matrix")

    # Census population per Gemeinde (commune_id, sex, age_class, weight).
    # Schema confirmed on the 25pct cache: type=DataFrame, cols=[commune_id, sex, age_class, weight].
    # Used to build the population margin vector for the gravity model and for
    # the BA Pendleratlas calibration (_calibrate expects commune_id + weight).
    df_population_full = _load_stage(wd, "braunschweig.data.census.population")

    # Employees-at-workplace per Gemeinde (commune_id, weight -> employees).
    df_employees_raw = _load_stage(wd, "braunschweig.data.census.employees")

    # RegioStaR-7 lookup (commune_id, regiostar7).
    df_regiostar = _load_stage(wd, "braunschweig.data.bbsr.regiostar")

    # BA Pendleratlas (orig_ars, dest_ars, flow) -- Kreis-pair OD for calibration.
    df_pendler = _load_stage(wd, "braunschweig.data.census.pendler")

    # Census employment (departement_id, weight) -- needed by _synthesise_intra_kreis.
    df_employment = _load_stage(wd, "braunschweig.data.census.employment")

    # Home locations of working persons (person_id, commune_id, geometry).
    # CACHE KEY NOTE: 'synthesis.population.spatial.home.locations' is the
    # standard eqasim stage that stores a GeoDataFrame keyed by household_id.
    # Verify the schema on the server; it must carry commune_id + geometry.
    df_home_locations = _load_stage(wd, "synthesis.population.spatial.home.locations")
    if "household_id" not in df_home_locations.columns:
        df_home_locations = df_home_locations.reset_index()

    # Home zones (household_id -> commune_id).
    # Schema confirmed on the 25pct cache: type=DataFrame,
    # cols=[household_id, departement_id, commune_id, iris_id].
    # Workers are joined to their home commune via household_id (not person_id).
    df_home_zones = _load_stage(wd, "braunschweig.synthesis.spatial.home_zones")

    # Enriched persons (to identify workers and get person->household mapping).
    # Schema confirmed on the 25pct cache: type=GeoDataFrame,
    # cols include person_id, household_id, employed (bool), age, sex, ...
    # Workers are identified by employed==True.
    df_enriched = _load_stage(wd, "braunschweig.synthesis.population.enriched")

    # Work candidate locations (commune_id, geometry, employees weight).
    # Schema confirmed on the 25pct cache: type=GeoDataFrame,
    # cols=[employees, fake, commune_id, iris_id, geometry, location_id], CRS=EPSG:25832.
    # This is the BS replacement stage (building-potential work locations).
    df_work_locations = _load_stage(wd, "braunschweig.locations.work")
    logger.info("[commute-calib] work locations: loaded %d candidates", len(df_work_locations))

    # ------------------------------------------------------------------
    # 3. Build OD matrix inputs (mirrors _execute_gravity_base in gravity/model.py)
    # ------------------------------------------------------------------
    df_population = df_population_full.rename(columns={
        "commune_id": "origin_id",
        "weight": "population",
    })[["origin_id", "population"]]
    df_population = df_population.groupby("origin_id")["population"].sum().reset_index()

    df_employees = df_employees_raw.rename(columns={
        "commune_id": "destination_id",
        "weight": "employees",
    })[["destination_id", "employees"]]

    municipalities = set(df_population["origin_id"])
    municipalities |= set(df_employees["destination_id"])
    municipalities |= set(df_distances["origin_id"])
    municipalities |= set(df_distances["destination_id"])
    municipalities = sorted(list(municipalities))
    logger.info("[commute-calib] %d municipalities in gravity model", len(municipalities))

    df_pop_idx = df_population.set_index("origin_id").reindex(municipalities).fillna(0.0)
    df_emp_idx = df_employees.set_index("destination_id").reindex(municipalities).fillna(0.0)
    df_dist_idx = df_distances.set_index(["origin_id", "destination_id"]).reindex(
        pd.MultiIndex.from_product([municipalities, municipalities])
    )
    distances_matrix = df_dist_idx["distance_km"].values.reshape(
        (len(municipalities), len(municipalities))
    )
    population_vec = df_pop_idx["population"].values
    employees_vec = df_emp_idx["employees"].values

    observations = min(np.sum(population_vec), np.sum(employees_vec))
    population_vec = population_vec * (observations / np.sum(population_vec))
    employees_vec = employees_vec * (observations / np.sum(employees_vec))

    # Per-origin slope vector (per-RS7 overrides or scalar).
    slope_vec = _build_origin_slope_vector(
        municipalities, slope, slope_overrides, df_regiostar,
    )

    # Scope for BA calibration: the ZGB political prefixes (5-digit Kreis ARS).
    # CACHE KEY NOTE: the scope (braunschweig.political_prefix) is a config value.
    # We derive it from the Pendler data (origins that appear in df_pendler) as a
    # robust fallback when the config is not parsed fully.
    # Reuse the already-loaded `cfg` dict (F8: no second _load_config call).
    scope = [str(p) for p in cfg.get("braunschweig.political_prefix",
                                     cfg.get("political_prefix", []))]
    if not scope:
        logger.warning(
            "[commute-calib] 'braunschweig.political_prefix' not found in config; "
            "deriving scope from Pendler orig_ars. Check config loading if wrong."
        )
        scope = sorted(df_pendler["orig_ars"].unique().tolist())
    logger.info("[commute-calib] ZGB scope: %s", scope)

    mask_pendler = (
        df_pendler["orig_ars"].isin(scope) | df_pendler["dest_ars"].isin(scope)
    )
    df_pendler_scope = df_pendler[mask_pendler].copy()
    df_pendler_scope = _synthesise_intra_kreis(df_pendler_scope, df_employment, scope)

    # ------------------------------------------------------------------
    # 4. Build the working-persons home DataFrame
    # ------------------------------------------------------------------
    # Identify workers: persons with employed==True.
    # Schema confirmed: enriched stage has 'employed' (bool), 'person_id', 'household_id'.
    if "employed" in df_enriched.columns:
        df_workers = df_enriched[df_enriched["employed"]].copy()
    elif "has_work_trip" in df_enriched.columns:
        logger.warning(
            "[commute-calib] 'employed' not in enriched stage; using 'has_work_trip' "
            "(non-silent fallback: verify enriched stage schema)."
        )
        df_workers = df_enriched[df_enriched["has_work_trip"]].copy()
    else:
        logger.warning(
            "[commute-calib] neither 'employed' nor 'has_work_trip' in enriched stage; "
            "using all persons as workers (non-silent: verify enriched stage schema)."
        )
        df_workers = df_enriched.copy()
    logger.info("[commute-calib] workers identified: %d (employed==True)", len(df_workers))

    # Merge worker person -> home commune_id via household_id.
    # home_zones schema: [household_id, departement_id, commune_id, iris_id].
    # Workers carry household_id; join household_id -> commune_id, then bring in
    # home geometry from home_locations (also keyed by household_id).
    if "household_id" not in df_workers.columns:
        raise RuntimeError(
            "[commute-calib] Column 'household_id' not found in enriched persons stage. "
            "The all-features enriched stage is expected to carry household_id for every "
            "worker. Check that the correct enriched stage was loaded and the cache is "
            "from an all-features run."
        )

    # Deduplicate home_zones to one row per household (it is already one row per household,
    # but guard defensively). Then join: worker -> household -> commune_id.
    df_home_zones_dedup = df_home_zones[["household_id", "commune_id"]].drop_duplicates("household_id")
    worker_communes = df_workers[["person_id", "household_id"]].merge(
        df_home_zones_dedup, on="household_id", how="inner"
    )
    logger.info("[commute-calib] workers with home commune: %d", len(worker_communes))

    # Merge home location geometry via household_id.
    df_home_geom = df_home_locations[["household_id", "geometry"]].copy()
    df_home_geom = df_home_geom.drop_duplicates("household_id")
    worker_homes_gdf = worker_communes.merge(df_home_geom, on="household_id", how="left")

    # Extract metric x/y coordinates (stage is in EPSG:25832).
    worker_homes_gdf = worker_homes_gdf.dropna(subset=["geometry"])
    worker_homes_gdf["x_m"] = worker_homes_gdf["geometry"].apply(lambda g: g.x)
    worker_homes_gdf["y_m"] = worker_homes_gdf["geometry"].apply(lambda g: g.y)
    logger.info(
        "[commute-calib] workers with resolved home geometry: %d", len(worker_homes_gdf)
    )

    # ------------------------------------------------------------------
    # 5. Load MiD P13 target band shares
    # ------------------------------------------------------------------
    p13_path = os.path.join(args.mid_dir, "mid2023_P13.csv")
    if not os.path.isfile(p13_path):
        raise RuntimeError(
            f"MiD P13 CSV not found at '{p13_path}'. "
            "Ensure --mid-dir points to the correct directory."
        )
    targets = load_p13_band_shares(args.mid_dir)
    if "03ZGB" not in targets:
        logger.warning(
            "[commute-calib] '03ZGB' not found in P13 targets; the ZGB aggregate "
            "convergence criterion will not be available. "
            "Ensure mid2023_P13.csv contains an '03ZGB' row."
        )
    logger.info("[commute-calib] loaded P13 targets for %d keys", len(targets))

    # Load per-RS7 P13 targets when running in per-RS7 mode.
    rs7_targets: dict = {}
    if args.per_rs7:
        rs7_p13_path = os.path.join(args.mid_dir, "mid2023_P13_commute_distance_by_rs7.csv")
        if not os.path.isfile(rs7_p13_path):
            raise RuntimeError(
                f"Per-RS7 P13 CSV not found at '{rs7_p13_path}'. "
                "Ensure --mid-dir points to the correct directory and "
                "mid2023_P13_commute_distance_by_rs7.csv is committed."
            )
        rs7_targets = load_p13_band_shares_by_rs7(args.mid_dir)
        logger.info(
            "[commute-calib] loaded per-RS7 P13 targets for RS7 codes: %s",
            sorted(rs7_targets.keys()),
        )

    # Per-RS7 worker counts for the shrinkage step.
    rs7_lookup = (
        df_regiostar.set_index("commune_id")["regiostar7"].astype("Int64").to_dict()
    )

    def _worker_rs7(commune_id: str) -> int:
        ags8 = ars_to_ags8(commune_id)
        rs7 = rs7_lookup.get(ags8)
        return int(rs7) if rs7 is not None and not pd.isna(rs7) else -1

    worker_homes_gdf["rs7"] = worker_homes_gdf["commune_id"].map(_worker_rs7)

    # ------------------------------------------------------------------
    # 6. Initialise friction factors from the current gravity slope
    # ------------------------------------------------------------------
    # f_b ~ exp(slope * mid_b): same distribution as the legacy exponential, so the
    # first iteration is comparable to the pre-calibration model.
    initial_factors = np.array([
        float(np.exp(slope * mid)) for mid in _BAND_MIDPOINTS_KM
    ], dtype=float)
    initial_factors = initial_factors / initial_factors.mean()  # normalise mean to 1
    factors_b = initial_factors.copy()

    # Per-RS7 factors: start from the same global initial factors for every RS7.
    rs7_codes = sorted(
        int(c) for c in worker_homes_gdf["rs7"].unique() if int(c) > 0
    )
    if args.per_rs7:
        factors_by_rs7 = {rs7: initial_factors.copy() for rs7 in rs7_codes}
        logger.info(
            "[commute-calib] per-RS7 mode: calibrating %d RS7 codes: %s",
            len(rs7_codes), rs7_codes,
        )
    else:
        factors_by_rs7 = None

    # F4: precompute rs7_vec once (municipalities are constant across iterations).
    # _worker_rs7 already calls ars_to_ags8 internally; no double application here.
    rs7_vec_global = np.array([_worker_rs7(c) for c in municipalities])

    # ------------------------------------------------------------------
    # 7. Furness iteration loop
    # ------------------------------------------------------------------
    iter_log = []
    converged = False

    for it in range(args.max_iterations):
        # Build friction matrix for this iteration.
        if args.per_rs7 and factors_by_rs7 is not None:
            # Per-RS7 friction: use the precomputed rs7_vec (F4).
            rs7_vec = rs7_vec_global
            friction_factors_arg = {
                rs7: {b: float(factors_by_rs7[rs7][b]) for b in range(N_BANDS)}
                for rs7 in factors_by_rs7
            }
        else:
            rs7_vec = None
            friction_factors_arg = {b: float(factors_b[b]) for b in range(N_BANDS)}

        friction = build_friction_matrix(
            distances_matrix, slope_vec, constant, diagonal,
            factors=friction_factors_arg, rs7_vec=rs7_vec,
        )

        # Doubly-constrained gravity balancing.
        flow = evaluate_gravity(population_vec, employees_vec, friction)

        # Convert flow to an OD DataFrame for BA calibration.
        df_od = pd.DataFrame(
            {"weight": flow.reshape(-1)},
            index=pd.MultiIndex.from_product(
                [municipalities, municipalities],
                names=["origin_id", "destination_id"],
            ),
        ).reset_index()

        # Normalise to row-sum weights for _calibrate.
        df_od = _row_normalise_od(df_od)

        # BA Pendleratlas calibration (authoritative inter-Kreis constraint).
        df_od_calibrated = _calibrate(df_od, df_population_full, df_pendler_scope)
        # _calibrate returns a 'flow' column; re-normalise to weights.
        if "flow" in df_od_calibrated.columns and "weight" not in df_od_calibrated.columns:
            df_od_calibrated = df_od_calibrated.rename(columns={"flow": "weight"})
        if "weight" in df_od_calibrated.columns:
            df_od_calibrated = _row_normalise_od(df_od_calibrated)

        # Rebuild od_matrix numpy array from the calibrated OD.
        od_pivot = df_od_calibrated.pivot(
            index="origin_id", columns="destination_id", values="weight"
        ).reindex(index=municipalities, columns=municipalities).fillna(0.0)
        od_matrix = od_pivot.values

        # Assign work locations and measure realised distances.
        # C1: pass seed + it so each iteration draws an independent-but-reproducible
        # sample; passing the same seed every iteration would freeze the stochastic
        # draw and could falsely trigger early convergence.
        km_by_kreis, jobs_by_gemeinde, km_by_rs7_realised = assign_and_measure(
            od_matrix, municipalities, worker_homes_gdf,
            df_work_locations, df_population_full, seed + it,
        )

        # ZGB aggregate band shares (all workers pooled).
        all_km = np.concatenate(list(km_by_kreis.values())) if km_by_kreis else np.array([])
        if len(all_km) == 0:
            logger.error("[commute-calib] iter %d: no realised distances; aborting", it)
            break
        model_shares_zgb = band_shares(np.asarray(all_km, dtype=float) * args.detour_factor)

        # ZGB aggregate EMD.
        if "03ZGB" in targets:
            emd_zgb = emd_on_bands(model_shares_zgb, targets["03ZGB"])
        else:
            # Fall back to the population-weighted mean across Kreis targets.
            emd_zgb_vals = []
            for kreis, km_arr in km_by_kreis.items():
                if kreis in targets:
                    shares_k = band_shares(np.asarray(km_arr, dtype=float) * args.detour_factor)
                    emd_zgb_vals.append(emd_on_bands(shares_k, targets[kreis]))
            emd_zgb = float(np.mean(emd_zgb_vals)) if emd_zgb_vals else float("nan")

        # Per-Kreis EMD for diagnostics.
        per_kreis_emd = {}
        for kreis, km_arr in km_by_kreis.items():
            if kreis in targets:
                shares_k = band_shares(np.asarray(km_arr, dtype=float) * args.detour_factor)
                per_kreis_emd[kreis] = emd_on_bands(shares_k, targets[kreis])

        worst_kreis_emd = max(per_kreis_emd.values()) if per_kreis_emd else float("nan")
        worst_kreis = max(per_kreis_emd, key=per_kreis_emd.get) if per_kreis_emd else "n/a"

        # Per-RS7 EMD diagnostics (only in per-RS7 mode).
        per_rs7_emd: dict[int, float] = {}
        if args.per_rs7 and rs7_targets:
            for rs7_code, km_arr_rs7 in km_by_rs7_realised.items():
                if rs7_code in rs7_targets:
                    shares_rs7_diag = band_shares(np.asarray(km_arr_rs7, dtype=float) * args.detour_factor)
                    per_rs7_emd[rs7_code] = emd_on_bands(shares_rs7_diag, rs7_targets[rs7_code])
            if per_rs7_emd:
                max_rs7_emd = max(per_rs7_emd.values())
                worst_rs7 = max(per_rs7_emd, key=per_rs7_emd.get)
                logger.info(
                    "[commute-calib] iter %02d: ZGB aggregate EMD=%.4f, "
                    "max RS7 EMD=%.4f (RS7=%d), per-RS7 EMDs: %s",
                    it, emd_zgb, max_rs7_emd, worst_rs7,
                    {r: round(v, 4) for r, v in sorted(per_rs7_emd.items())},
                )
            else:
                max_rs7_emd = float("nan")
                logger.info(
                    "[commute-calib] iter %02d: ZGB aggregate EMD=%.4f, "
                    "worst Kreis EMD=%.4f (%s); no per-RS7 EMD available",
                    it, emd_zgb, worst_kreis_emd, worst_kreis,
                )
        else:
            max_rs7_emd = float("nan")
            logger.info(
                "[commute-calib] iter %02d: ZGB aggregate EMD=%.4f, "
                "worst Kreis EMD=%.4f (%s)",
                it, emd_zgb, worst_kreis_emd, worst_kreis,
            )

        iter_log.append({
            "iteration": it,
            "emd_zgb": emd_zgb,
            "worst_kreis_emd": worst_kreis_emd,
            "worst_kreis": worst_kreis,
            "max_rs7_emd": max_rs7_emd,
            "per_rs7_emd": {str(r): v for r, v in per_rs7_emd.items()},
        })

        # Convergence criterion:
        #   - per-RS7 mode: stop when the max per-RS7 EMD <= threshold
        #   - global mode: stop when the ZGB aggregate EMD <= threshold
        if args.per_rs7:
            convergence_emd = max_rs7_emd if not np.isnan(max_rs7_emd) else emd_zgb
        else:
            convergence_emd = emd_zgb

        # Fix 3: fail fast when both convergence metrics are NaN — means no RS7 target
        # matched any realised home-RS7 AND no ZGB/Kreis target is present.  Running to
        # max_iterations would produce a meaningless calibration (CLAUDE.md fail-early).
        if np.isnan(convergence_emd):
            msg = (
                f"[commute-calib] iter {it:02d}: convergence_emd is NaN — "
                "no per-RS7 P13 target matched any realised home-RS7 home commune "
                "and no ZGB/Kreis aggregate target available. "
                "Check that mid2023_P13_commute_distance_by_rs7.csv is present in "
                "--mid-dir and that the home-RS7 join in assign_and_measure produced "
                "non-empty per-RS7 km arrays."
            )
            logger.error(msg)
            raise RuntimeError(msg)

        if convergence_emd <= args.emd_threshold:
            logger.info(
                "[commute-calib] converged at iter %d "
                "(%s EMD=%.4f <= threshold=%.4f)",
                it,
                "max RS7" if args.per_rs7 else "ZGB aggregate",
                convergence_emd, args.emd_threshold,
            )
            converged = True
            break

        # Furness update.
        if args.per_rs7 and factors_by_rs7 is not None:
            # For each RS7 present in the origin set, update toward the real per-RS7
            # P13 Raumtyp target. An RS7 absent from rs7_targets falls back to the ZGB
            # aggregate -- this is logged explicitly (CLAUDE.md no-silent-fallback).
            n_rs7_primary = 0
            n_rs7_fallback = 0
            for rs7 in rs7_codes:
                if rs7 in rs7_targets:
                    target_for_rs7 = rs7_targets[rs7]
                    n_rs7_primary += 1
                else:
                    # No committed per-RS7 P13 target for this code; fall back to ZGB
                    # aggregate. This should not occur for the ZGB RS7 codes 72-77 --
                    # log it prominently so it is never silent.
                    target_for_rs7 = targets.get("03ZGB", None)
                    n_rs7_fallback += 1
                    logger.warning(
                        "[commute-calib] iter %02d RS7=%d: no per-RS7 P13 target found "
                        "in mid2023_P13_commute_distance_by_rs7.csv; falling back to ZGB "
                        "aggregate target (CLAUDE.md no-silent-fallback). "
                        "Expected for RS7 codes 72-77; unexpected otherwise.",
                        it, rs7,
                    )
                if target_for_rs7 is None:
                    continue

                # Use directly measured per-RS7 km (exact, not Kreis-proxy).
                km_rs7 = km_by_rs7_realised.get(rs7, np.array([]))

                if len(km_rs7) < args.min_count:
                    logger.debug(
                        "[commute-calib] iter %02d RS7=%d: only %d workers; "
                        "skipping factor update (below min_count=%d)",
                        it, rs7, len(km_rs7), args.min_count,
                    )
                    continue
                shares_rs7 = band_shares(np.asarray(km_rs7, dtype=float) * args.detour_factor)
                factors_by_rs7[rs7] = furness_update(
                    factors_by_rs7[rs7], target_for_rs7, shares_rs7
                )

            # Aggregate fallback-rate log: emit once (first iteration only) so it
            # is never silent (CLAUDE.md no-silent-fallback) but does not repeat
            # up to max_iterations times.  RS7 target coverage is static — the set
            # of RS7 codes in rs7_targets does not change between iterations.
            if n_rs7_fallback > 0 and it == 0:
                total_rs7 = n_rs7_primary + n_rs7_fallback
                logger.warning(
                    "[commute-calib] per-RS7 target coverage: "
                    "primary (real P13 Raumtyp) %d/%d RS7 codes, "
                    "fallback (ZGB aggregate) %d/%d RS7 codes (%.1f%%). "
                    "(Logged once; coverage is static across iterations.)",
                    n_rs7_primary, total_rs7, n_rs7_fallback, total_rs7,
                    100.0 * n_rs7_fallback / total_rs7,
                )
        else:
            factors_b = furness_update(factors_b, targets.get("03ZGB", model_shares_zgb),
                                       model_shares_zgb)

    if not converged:
        final_emd = float("nan")
        if iter_log:
            if args.per_rs7 and not np.isnan(iter_log[-1]["max_rs7_emd"]):
                final_emd = iter_log[-1]["max_rs7_emd"]
            else:
                final_emd = iter_log[-1]["emd_zgb"]
        logger.warning(
            "[commute-calib] did not converge within %d iterations; "
            "final %s EMD=%.4f (threshold=%.4f). "
            "The Furness loop is limited by the achievable within-pair portion; "
            "residual EMD reflects the BA inter-Kreis constraint.",
            args.max_iterations,
            "max RS7" if args.per_rs7 else "ZGB aggregate",
            final_emd,
            args.emd_threshold,
        )

    # ------------------------------------------------------------------
    # 8. Per-RS7 shrinkage (if --per-rs7)
    # ------------------------------------------------------------------
    if args.per_rs7 and factors_by_rs7 is not None:
        # Per-RS7 friction factors are calibrated to the real MiD 2023 P13 Raumtyp
        # per-RS7 commute-distance distribution (mid2023_P13_commute_distance_by_rs7.csv,
        # RS7 codes 72-77). Shrinkage blends low-count cells toward the pooled factor
        # to reduce noise in sparse rural RS7 codes.

        # Count workers per (RS7, band) for shrinkage using directly measured per-RS7 km
        # (exact, not the Kreis-proxy previously used). This correctly identifies sparse
        # per-band cells rather than overestimating density by using the total RS7 count.
        counts_by_rs7 = {}
        for rs7 in rs7_codes:
            km_all_rs7 = km_by_rs7_realised.get(rs7, np.array([]))
            if len(km_all_rs7) > 0:
                km_detoured = np.asarray(km_all_rs7, dtype=float) * args.detour_factor
                # Per-band count = number of workers whose detoured commute fell in each band.
                band_hist = np.zeros(N_BANDS, dtype=int)
                for b in range(N_BANDS):
                    lo = BAND_EDGES_KM[b]
                    hi = BAND_EDGES_KM[b + 1]
                    band_hist[b] = int(np.sum((km_detoured >= lo) & (km_detoured < hi)))
                counts_by_rs7[rs7] = band_hist
            else:
                counts_by_rs7[rs7] = np.zeros(N_BANDS, dtype=int)

        # Compute a genuine pooled-calibrated factor: the worker-count-weighted mean
        # of the converged per-RS7 factors.  This is the correct blend target for
        # shrinkage: sparse rural RS7 cells (RS7 75/76/77) will be pulled toward the
        # realised ZGB-wide calibrated distribution, NOT toward the uncalibrated seed
        # (which is what passing ``factors_b`` would do in per-RS7 mode, since
        # ``factors_b`` is only updated in the global ``else`` branch and stays at the
        # initial exp(slope * mid_b) seed when --per-rs7 is active).
        rs7_worker_counts = {
            rs7: int(np.sum(counts_by_rs7.get(rs7, np.zeros(N_BANDS))))
            for rs7 in factors_by_rs7
        }
        total_workers_for_pool = sum(rs7_worker_counts.values())
        if total_workers_for_pool > 0:
            pooled_calibrated = np.zeros(N_BANDS, dtype=float)
            for rs7, f_arr in factors_by_rs7.items():
                w = rs7_worker_counts[rs7] / total_workers_for_pool
                pooled_calibrated += w * np.asarray(f_arr, dtype=float)
        else:
            # No workers at all — fall back to the global initial seed (safe no-op).
            pooled_calibrated = factors_b.copy()
            logger.warning(
                "[commute-calib] shrinkage: no RS7 workers found for pooled-calibrated "
                "derivation; using initial seed as pooled target (should not occur on a "
                "real synthesis)."
            )

        factors_by_rs7, shrinkage_rate = shrink_sparse_factors(
            factors_by_rs7, counts_by_rs7, pooled_calibrated, args.min_count
        )

        # MANDATORY: log shrinkage rate explicitly (CLAUDE.md no-silent-fallback).
        n_total_cells = sum(len(f) for f in factors_by_rs7.values())
        n_shrunk_cells = int(round(shrinkage_rate * n_total_cells))
        if shrinkage_rate > 0.5:
            logger.warning(
                "[commute-calib] per-RS7 shrinkage: %d/%d cells (%.1f%%) shrunk "
                "toward pooled factor (count < %d). "
                "High shrinkage rate indicates most RS7 cells are sparse -- "
                "results may not reflect genuine RS7 differentiation.",
                n_shrunk_cells, n_total_cells, 100.0 * shrinkage_rate, args.min_count,
            )
        else:
            logger.info(
                "[commute-calib] per-RS7 shrinkage: %d/%d cells (%.1f%%) shrunk "
                "toward pooled factor (count < %d).",
                n_shrunk_cells, n_total_cells, 100.0 * shrinkage_rate, args.min_count,
            )

        # Convert per-RS7 factors to the final output dict.
        final_factors = {
            rs7: {b: float(factors_by_rs7[rs7][b]) for b in range(N_BANDS)}
            for rs7 in sorted(factors_by_rs7.keys())
        }
    else:
        final_factors = {b: float(factors_b[b]) for b in range(N_BANDS)}

    # ------------------------------------------------------------------
    # 9. Print paste-ready YAML block
    # ------------------------------------------------------------------
    print("\n# Paste under 'gravity_friction_factors' in config_*braunschweig*.yml")
    print(_yaml_factors_block(final_factors))

    print("\n# Per-iteration convergence log:")
    for entry in iter_log:
        if args.per_rs7:
            # In per-RS7 mode the convergence criterion is max_rs7_emd; show it
            # prominently along with a compact per-RS7 breakdown.
            rs7_breakdown = " ".join(
                f"{r}:{v:.4f}"
                for r, v in sorted(
                    (int(k), v) for k, v in entry.get("per_rs7_emd", {}).items()
                )
            )
            print(
                f"  iter {entry['iteration']:02d}: max RS7 EMD={entry['max_rs7_emd']:.4f}, "
                f"ZGB EMD={entry['emd_zgb']:.4f} | per-RS7: {rs7_breakdown}"
            )
        else:
            print(
                f"  iter {entry['iteration']:02d}: ZGB EMD={entry['emd_zgb']:.4f}, "
                f"worst Kreis EMD={entry['worst_kreis_emd']:.4f} ({entry['worst_kreis']})"
            )

    # ------------------------------------------------------------------
    # 10. Write outputs (if --output-dir)
    # ------------------------------------------------------------------
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        _write_outputs(
            final_factors, km_by_kreis, jobs_by_gemeinde, targets,
            df_employees_raw, iter_log, args, converged,
            km_by_rs7_realised=km_by_rs7_realised,
            rs7_targets=rs7_targets,
        )
        logger.info("[commute-calib] wrote outputs to %s", args.output_dir)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _write_outputs(final_factors, km_by_kreis, jobs_by_gemeinde, targets,
                   df_employees, iter_log, args, converged,
                   km_by_rs7_realised=None, rs7_targets=None):
    """Write gravity_calibration_results.csv and gravity_calibration_report.json.

    In per-RS7 mode (args.per_rs7 is True), ``final_factors`` is a nested dict
    ``{rs7: {band: float}}`` instead of ``{band: float}``.  In that mode an
    additional file ``gravity_calibration_results_per_rs7.csv`` is written with
    one row per (rs7, band) showing the realised detoured band share, the MiD P13
    Raumtyp reference share, their difference, and the per-RS7 EMD.  The existing
    per-Kreis results CSV is always written in both modes (it remains useful context
    for diagnosing inter-Kreis variation).

    Parameters
    ----------
    km_by_rs7_realised : dict[int, np.ndarray] | None
        Per-RS7 realised straight-line commute distances from the last Furness
        iteration.  Required when args.per_rs7 is True.
    rs7_targets : dict[int, np.ndarray] | None
        Per-RS7 MiD P13 Raumtyp band-share targets (RS7 codes 72-77).
        Required when args.per_rs7 is True.
    """
    # Per-Kreis band shares + EMD.
    rows = []
    for kreis, km_arr in km_by_kreis.items():
        shares = band_shares(np.asarray(km_arr, dtype=float) * args.detour_factor)
        target = targets.get(kreis, targets.get("03ZGB", np.full(N_BANDS, 1.0 / N_BANDS)))
        emd = emd_on_bands(shares, target)
        n_workers = len(km_arr)
        for b in range(N_BANDS):
            lo = BAND_EDGES_KM[b]
            hi = BAND_EDGES_KM[b + 1]
            rows.append({
                "kreis": kreis,
                "band": b,
                "band_lo_km": lo,
                "band_hi_km": hi if np.isfinite(hi) else 999.0,
                "model_share": float(shares[b]),
                "target_share": float(target[b]),
                "diff": float(shares[b] - target[b]),
                "emd_kreis": emd,
                "n_workers": n_workers,
            })
    df_results = pd.DataFrame(rows)
    csv_path = os.path.join(args.output_dir, "gravity_calibration_results.csv")
    df_results.to_csv(csv_path, index=False)
    logger.info("[commute-calib] wrote %s", csv_path)

    # Per-RS7 results CSV (only in per-RS7 mode).
    # One row per (rs7, band): realised detoured share, MiD P13 Raumtyp reference share,
    # difference, and per-RS7 EMD.  Uses band_shares/emd_on_bands directly
    # (DRY — same helpers as the Furness loop) so the numbers are fully traceable.
    if args.per_rs7 and km_by_rs7_realised is not None and rs7_targets is not None:
        rs7_rows = []
        for rs7_code in sorted(km_by_rs7_realised.keys()):
            km_arr_rs7 = km_by_rs7_realised[rs7_code]
            if len(km_arr_rs7) == 0:
                continue
            shares_rs7 = band_shares(np.asarray(km_arr_rs7, dtype=float) * args.detour_factor)
            target_rs7 = rs7_targets.get(rs7_code)
            emd_rs7 = emd_on_bands(shares_rs7, target_rs7) if target_rs7 is not None else float("nan")
            n_workers_rs7 = len(km_arr_rs7)
            for b in range(N_BANDS):
                lo = BAND_EDGES_KM[b]
                hi = BAND_EDGES_KM[b + 1]
                rs7_rows.append({
                    "rs7": rs7_code,
                    "band": b,
                    "band_lo_km": lo,
                    "band_hi_km": hi if np.isfinite(hi) else 999.0,
                    "model_share": float(shares_rs7[b]),
                    "target_share": float(target_rs7[b]) if target_rs7 is not None else float("nan"),
                    "diff": float(shares_rs7[b] - target_rs7[b]) if target_rs7 is not None else float("nan"),
                    "emd_rs7": emd_rs7,
                    "n_workers": n_workers_rs7,
                })
        if rs7_rows:
            df_rs7 = pd.DataFrame(rs7_rows)
            csv_rs7_path = os.path.join(args.output_dir, "gravity_calibration_results_per_rs7.csv")
            df_rs7.to_csv(csv_rs7_path, index=False)
            logger.info("[commute-calib] wrote %s", csv_rs7_path)

    # SvB target per Gemeinde for attraction fill.
    svb_target = (
        df_employees.groupby("commune_id")["weight"].sum().to_dict()
        if "commune_id" in df_employees.columns and "weight" in df_employees.columns
        else {}
    )

    # Validation report via build_validation_report.
    report = build_validation_report(km_by_kreis, targets, jobs_by_gemeinde, svb_target)

    # Potential-respect block: mean assigned potential weight vs uniform baseline.
    # This measures how much the work-location sampling used the employee-weight
    # signal. A ratio > 1 means larger workplaces attracted more workers than
    # a uniform draw would predict.
    # NOTE: to compute this precisely we would need to compare mean(employees at assigned
    # location) vs mean(employees at random location); the information is deferred to
    # the server run where the work_locations stage has the full geometry + employees.
    # Here we record only a placeholder for the potential-respect block.
    report["potential_respect"] = {
        "note": (
            "Potential-respect block (mean chosen attraction vs uniform baseline) "
            "requires per-person assigned location metadata. Compute on server run "
            "by comparing mean(employees_at_assigned) vs mean(employees_at_random)."
        )
    }

    report["convergence"] = {
        "converged": converged,
        "iterations": len(iter_log),
        "emd_threshold": args.emd_threshold,
        "final_emd_zgb": iter_log[-1]["emd_zgb"] if iter_log else None,
        "per_iteration": iter_log,
    }
    report["factors"] = final_factors

    json_path = os.path.join(args.output_dir, "gravity_calibration_report.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    logger.info("[commute-calib] wrote %s", json_path)


if __name__ == "__main__":
    main()
