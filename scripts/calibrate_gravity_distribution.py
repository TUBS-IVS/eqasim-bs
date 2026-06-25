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
from braunschweig.calibration.metrics import apply_detour, band_shares, emd_on_bands  # noqa: E402
from braunschweig.calibration.targets import load_p13_band_shares  # noqa: E402
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
    jobs_by_gemeinde: dict[str, float] = {}
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
        help="Stop when ZGB-aggregate EMD falls at or below this value (default: 0.08).",
    )
    parser.add_argument(
        "--per-rs7", action="store_true",
        help=(
            "EXPERIMENTAL / LIMITED: calibrate per-RS7 factors rather than a single global "
            "factor set. Limitation: no per-RS7 P13 distance-distribution target exists in "
            "the committed MiD data (P13 is per-Kreis only); all RS7 codes chase the SAME "
            "ZGB aggregate P13 target. The global (default) path is the primary, fully-supported "
            "mode. Use --per-rs7 only for exploratory analysis, not for committed calibration."
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

    # Filtered population (commune_id, weight) -- one row per synthetic person.
    # CACHE KEY NOTE: resolves to 'braunschweig.ipf.attributed' (legacy) or
    # 'braunschweig.popsim.stage' (popsim configs). The stage 'data.census.filtered'
    # is a redirect alias; the actual pickle may be stored under the resolved name.
    # Try 'data.census.filtered' first; if absent, use the enriched population.
    try:
        df_population_full = _load_stage(wd, "data.census.filtered")
    except RuntimeError:
        logger.warning(
            "Stage 'data.census.filtered' not cached; falling back to "
            "'braunschweig.popsim.stage'. This is NOT a silent fallback: "
            "the upstream resolver directs data.census.filtered to one of "
            "these two stages; the fallback is a deliberate secondary lookup."
        )
        df_population_full = _load_stage(wd, "braunschweig.popsim.stage")

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

    # Home zones (person_id -> commune_id for working persons).
    # CACHE KEY NOTE: 'synthesis.population.spatial.home.zones' maps person_id
    # to the home Gemeinde commune_id. Verify column names on the server.
    df_home_zones = _load_stage(wd, "synthesis.population.spatial.home.zones")

    # Enriched persons (to identify workers and get person->household mapping).
    # CACHE KEY NOTE: 'braunschweig.synthesis.population.enriched' is the BS-specific
    # enrichment stage (or 'synthesis.population.enriched' in the base eqasim).
    # Try the BS-specific name first, then the base name as a secondary lookup.
    try:
        df_enriched = _load_stage(wd, "braunschweig.synthesis.population.enriched")
    except RuntimeError:
        logger.warning(
            "Stage 'braunschweig.synthesis.population.enriched' not cached; "
            "falling back to 'synthesis.population.enriched' (secondary lookup, "
            "non-silent: the enrichment stage may be stored under the base name)."
        )
        df_enriched = _load_stage(wd, "synthesis.population.enriched")

    # Work candidate locations (commune_id, geometry, optional employees weight).
    # CACHE KEY NOTE: in BS the work stage may be 'braunschweig.synthesis.locations.work'
    # (building-potential replacement) or the base 'synthesis.locations.work'.
    # Try the BS override first; fall back to base (non-silent).
    try:
        df_work_locations = _load_stage(wd, "braunschweig.synthesis.locations.work")
        logger.info("[commute-calib] work locations: loaded from BS override stage")
    except RuntimeError:
        logger.warning(
            "[commute-calib] work locations: BS override stage not cached; "
            "falling back to base 'synthesis.locations.work' (non-silent fallback: "
            "check that work_building_potentials flag matches the cached run)."
        )
        df_work_locations = _load_stage(wd, "synthesis.locations.work")

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
    # Identify workers: persons who have a work trip.
    if "has_work_trip" in df_enriched.columns:
        df_workers = df_enriched[df_enriched["has_work_trip"]].copy()
    else:
        logger.warning(
            "[commute-calib] column 'has_work_trip' not in enriched persons; "
            "using all persons as workers. Verify the enriched stage schema on the server."
        )
        df_workers = df_enriched.copy()

    # Merge worker person -> home commune_id.
    # home_zones has columns: person_id, commune_id (or similar).
    # CACHE KEY NOTE: column name may be 'commune_id' or 'origin_id'; rename if needed.
    if "commune_id" not in df_home_zones.columns and "origin_id" in df_home_zones.columns:
        df_home_zones = df_home_zones.rename(columns={"origin_id": "commune_id"})

    worker_communes = df_workers[["person_id"]].merge(
        df_home_zones[["person_id", "commune_id"]], on="person_id", how="inner"
    )
    logger.info("[commute-calib] workers with home commune: %d", len(worker_communes))

    # Merge home location geometry (from household locations via household_id).
    # The home locations stage is a GeoDataFrame keyed by household; join via household_id.
    # The all-features enriched stage always carries household_id; if absent, fail early.
    if "household_id" not in df_workers.columns:
        raise RuntimeError(
            "[commute-calib] Column 'household_id' not found in enriched persons stage. "
            "The all-features enriched stage is expected to carry household_id for every "
            "worker. Check that the correct enriched stage was loaded and the cache is "
            "from an all-features run."
        )
    worker_hh = df_workers[["person_id", "household_id"]].copy()
    df_home_geom = df_home_locations[["household_id", "geometry"]].copy()
    df_home_geom = df_home_geom.drop_duplicates("household_id")
    worker_homes_gdf = worker_communes.merge(
        worker_hh, on="person_id", how="left"
    ).merge(df_home_geom, on="household_id", how="left")

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
    factors_dict = {b: float(factors_b[b]) for b in range(N_BANDS)}

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
        km_by_kreis, jobs_by_gemeinde = assign_and_measure(
            od_matrix, municipalities, worker_homes_gdf,
            df_work_locations, df_population_full, seed + it,
        )

        # ZGB aggregate band shares (all workers pooled).
        all_km = np.concatenate(list(km_by_kreis.values())) if km_by_kreis else np.array([])
        if len(all_km) == 0:
            logger.error("[commute-calib] iter %d: no realised distances; aborting", it)
            break
        model_shares_zgb = band_shares(apply_detour(all_km, factor=args.detour_factor))

        # ZGB aggregate EMD.
        if "03ZGB" in targets:
            emd_zgb = emd_on_bands(model_shares_zgb, targets["03ZGB"])
        else:
            # Fall back to the population-weighted mean across Kreis targets.
            emd_zgb_vals = []
            for kreis, km_arr in km_by_kreis.items():
                if kreis in targets:
                    shares_k = band_shares(apply_detour(km_arr, factor=args.detour_factor))
                    emd_zgb_vals.append(emd_on_bands(shares_k, targets[kreis]))
            emd_zgb = float(np.mean(emd_zgb_vals)) if emd_zgb_vals else float("nan")

        # Per-Kreis EMD for diagnostics.
        per_kreis_emd = {}
        for kreis, km_arr in km_by_kreis.items():
            if kreis in targets:
                shares_k = band_shares(apply_detour(km_arr, factor=args.detour_factor))
                per_kreis_emd[kreis] = emd_on_bands(shares_k, targets[kreis])

        worst_kreis_emd = max(per_kreis_emd.values()) if per_kreis_emd else float("nan")
        worst_kreis = max(per_kreis_emd, key=per_kreis_emd.get) if per_kreis_emd else "n/a"

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
        })

        if emd_zgb <= args.emd_threshold:
            logger.info(
                "[commute-calib] converged at iter %d (EMD=%.4f <= threshold=%.4f)",
                it, emd_zgb, args.emd_threshold,
            )
            converged = True
            break

        # Furness update.
        if args.per_rs7 and factors_by_rs7 is not None:
            for rs7 in rs7_codes:
                # Per-RS7 target: use the pooled ZGB target for all RS7 to avoid fabricating
                # per-RS7 P13 targets that are not committed in the repository.
                # ASSUMPTION: per-RS7 calibration uses the ZGB P13 aggregate as the target;
                # no separate per-RS7 P13 distribution is available in the committed MiD CSVs.
                target_for_rs7 = targets.get("03ZGB", None)
                if target_for_rs7 is None:
                    continue
                rs7_mask = worker_homes_gdf["rs7"] == rs7
                rs7_workers = worker_homes_gdf[rs7_mask]
                rs7_kreis_set = set(rs7_workers["commune_id"].str[:5].unique())
                rs7_km_parts = [km_by_kreis[k] for k in km_by_kreis if k in rs7_kreis_set]
                km_rs7 = np.concatenate(rs7_km_parts) if rs7_km_parts else np.array([])

                if len(km_rs7) < args.min_count:
                    logger.debug(
                        "[commute-calib] RS7=%d: only %d workers; skipping factor update",
                        rs7, len(km_rs7),
                    )
                    continue
                shares_rs7 = band_shares(apply_detour(km_rs7, factor=args.detour_factor))
                factors_by_rs7[rs7] = furness_update(
                    factors_by_rs7[rs7], target_for_rs7, shares_rs7
                )
        else:
            factors_b = furness_update(factors_b, targets.get("03ZGB", model_shares_zgb),
                                       model_shares_zgb)
            factors_dict = {b: float(factors_b[b]) for b in range(N_BANDS)}

    if not converged:
        logger.warning(
            "[commute-calib] did not converge within %d iterations; "
            "final ZGB EMD=%.4f (threshold=%.4f). "
            "The Furness loop is limited by the achievable within-pair portion; "
            "residual EMD reflects the BA inter-Kreis constraint.",
            args.max_iterations,
            iter_log[-1]["emd_zgb"] if iter_log else float("nan"),
            args.emd_threshold,
        )

    # ------------------------------------------------------------------
    # 8. Per-RS7 shrinkage (if --per-rs7)
    # ------------------------------------------------------------------
    if args.per_rs7 and factors_by_rs7 is not None:
        # EXPERIMENTAL / LIMITED: per-RS7 factors all chase the SAME ZGB P13 aggregate
        # target (no per-RS7 P13 distribution is available in the committed MiD data).
        # Per CLAUDE.md (no invented references), per-RS7 targets are NOT fabricated here.
        # Use the global (default) path for committed calibration results.
        logger.warning(
            "[commute-calib] --per-rs7 is EXPERIMENTAL: no per-RS7 P13 target exists "
            "in the committed MiD data. All RS7 codes chase the same ZGB aggregate target. "
            "Do not treat per-RS7 factors as validated calibration results."
        )

        # Count workers per (RS7, band) for shrinkage.
        # Build the per-band histogram from the realised km_by_kreis so that cells
        # with very few observed trips in a given band are correctly identified as sparse
        # (instead of passing the total RS7 worker count for every band, which overstates
        # the per-band sample size).
        counts_by_rs7 = {}
        for rs7 in rs7_codes:
            rs7_mask = worker_homes_gdf["rs7"] == rs7
            rs7_workers_sub = worker_homes_gdf[rs7_mask]
            rs7_kreis_set = set(rs7_workers_sub["commune_id"].str[:5].unique())
            rs7_km_parts = [km_by_kreis[k] for k in km_by_kreis if k in rs7_kreis_set]
            if rs7_km_parts:
                km_all_rs7 = np.concatenate(rs7_km_parts)
                km_detoured = apply_detour(km_all_rs7, factor=args.detour_factor)
                # Per-band count = number of workers whose detoured commute fell in each band.
                band_hist = np.zeros(N_BANDS, dtype=int)
                for b in range(N_BANDS):
                    lo = BAND_EDGES_KM[b]
                    hi = BAND_EDGES_KM[b + 1]
                    band_hist[b] = int(np.sum((km_detoured >= lo) & (km_detoured < hi)))
                counts_by_rs7[rs7] = band_hist
            else:
                counts_by_rs7[rs7] = np.zeros(N_BANDS, dtype=int)

        factors_by_rs7, shrinkage_rate = shrink_sparse_factors(
            factors_by_rs7, counts_by_rs7, factors_b, args.min_count
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
        )
        logger.info("[commute-calib] wrote outputs to %s", args.output_dir)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _write_outputs(final_factors, km_by_kreis, jobs_by_gemeinde, targets,
                   df_employees, iter_log, args, converged):
    """Write gravity_calibration_results.csv and gravity_calibration_report.json."""
    # Per-Kreis band shares + EMD.
    rows = []
    for kreis, km_arr in km_by_kreis.items():
        shares = band_shares(apply_detour(km_arr, factor=args.detour_factor))
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
