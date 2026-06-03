"""Calibrate the education gravity decay per (RegioStaR-7, level) to MiD Tab. 43.

For each (home RS7 class, school level) the slope is chosen so the modelled mean
straight-line school-trip distance matches the MiD target (routed / detour). Uses
ONLY the capacity-gravity Furness + a 1-D bisection search (no numpy.linalg
decompositions), so it runs in the eqasim conda env.

Run (after a 25 % synthesis exists in the working directory):
  python scripts/calibrate_education_slopes.py `
    --working-directory eqasim-data/cache_bs_25pct --detour-factor 1.3
It prints the YAML block to paste under `education_gravity_slope_by_level_rs7`.
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
from braunschweig.synthesis.locations.education_gravity_model import (  # noqa: E402
    assign_by_capacity_gravity,
)
from braunschweig.data.mid.school_distance import build_target_table  # noqa: E402

_MAX_RADIUS_KM = {"grundschule": 15.0, "sekundar_1": 30.0, "sekundar_2": 60.0}
_AGE_BANDS = {"grundschule": (6, 9), "sekundar_1": (10, 15), "sekundar_2": (16, 19)}
_MIN_PUPILS_PER_CELL = 20

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure calibration helpers (unit-tested)
# ---------------------------------------------------------------------------

def mean_distance_for_slope(slope, pupil_xy, school_xy, capacity,
                            max_radius_km, rng):
    """Mean straight-line km between pupils and their assigned school for a
    single scalar slope (one stochastic draw)."""
    choice, _ = assign_by_capacity_gravity(
        pupil_xy, school_xy, capacity, slope=slope, max_radius_km=max_radius_km,
        max_iterations=200, tolerance=1e-6, rng=rng)
    delta = pupil_xy - school_xy[choice]
    d_km = np.sqrt((delta ** 2).sum(axis=1)) / 1000.0
    return float(np.mean(d_km))


def secant_calibrate_slope(target_km, pupil_xy, school_xy, capacity,
                           max_radius_km, seed, lo, hi, max_iter, tol):
    """Find slope in [lo, hi] (both negative) so the mean distance ~ target_km.
    Mean distance increases monotonically with slope (less negative -> longer
    trips), so a bisection on the bracket is used, re-seeding each evaluation
    for stability."""
    def f(s):
        return mean_distance_for_slope(
            s, pupil_xy, school_xy, capacity, max_radius_km,
            np.random.RandomState(seed)) - target_km

    a, b = lo, hi
    fa = f(a)
    for _ in range(max_iter):
        m = 0.5 * (a + b)
        fm = f(m)
        if abs(fm) < tol:
            return m
        if (fm < 0) == (fa < 0):
            a, fa = m, fm
        else:
            b = m
    return 0.5 * (a + b)


# ---------------------------------------------------------------------------
# Helpers for main() — not unit-tested
# ---------------------------------------------------------------------------

def age_to_level_school(age):
    """Return the school level for a given age, or None if outside 6-19."""
    for level, (lo, hi) in _AGE_BANDS.items():
        if lo <= age <= hi:
            return level
    return None


def _load_stage(working_directory, stage):
    """Load the most-recently-modified pickle for a given synpp stage name.

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
# main()
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Calibrate per-(RS7, level) education gravity slopes to MiD Tab. 43."
    )
    parser.add_argument("--working-directory", required=True,
                        help="Directory containing synpp stage pickles (e.g. eqasim-data/cache_bs_25pct).")
    parser.add_argument("--detour-factor", type=float, default=1.3,
                        help="Routed/straight-line detour factor applied to MiD target distances (default 1.3).")
    parser.add_argument("--seed", type=int, default=0,
                        help="Random seed for stochastic assignment (default 0).")
    args = parser.parse_args()

    wd = args.working_directory

    # ------------------------------------------------------------------
    # 1. Load required stage pickles
    # ------------------------------------------------------------------
    logger.info("Loading synthesis stage caches from: %s", wd)

    candidates = _load_stage(wd, "synthesis.population.spatial.primary.candidates")
    persons_raw = candidates["persons"]
    # Keep only persons who travel to school
    has_education = persons_raw["has_education_trip"] if "has_education_trip" in persons_raw.columns else persons_raw.get("has_education_trip")
    if has_education is None:
        raise RuntimeError("Column 'has_education_trip' not found in persons frame from 'synthesis.population.spatial.primary.candidates'.")
    persons_edu = persons_raw[persons_raw["has_education_trip"]].copy()
    logger.info("Persons with education trip: %d", len(persons_edu))

    enriched = _load_stage(wd, "braunschweig.synthesis.population.enriched")
    # Bring in age from the enriched frame
    age_cols = enriched[["person_id", "age"]].drop_duplicates("person_id")
    persons_edu = persons_edu.merge(age_cols, on="person_id", how="left")

    homes = _load_stage(wd, "synthesis.population.spatial.home.locations")
    # homes is a GeoDataFrame keyed by household_id; reset to get it as a column
    if "household_id" not in homes.columns:
        homes = homes.reset_index()
    homes_geom = homes[["household_id", "geometry"]].rename(columns={"geometry": "home_geom"})
    persons_edu = persons_edu.merge(homes_geom, on="household_id", how="left")

    import geopandas as gpd
    persons_gdf = gpd.GeoDataFrame(persons_edu, geometry="home_geom",
                                   crs=homes.crs)

    schools_gdf = _load_stage(wd, "braunschweig.data.schools.facilities")
    municipalities = _load_stage(wd, "data.spatial.municipalities")
    regiostar = _load_stage(wd, "braunschweig.data.bbsr.regiostar")

    # ------------------------------------------------------------------
    # 2. Assign school level per pupil via age bands
    # ------------------------------------------------------------------
    persons_gdf["level"] = persons_gdf["age"].apply(age_to_level_school)
    persons_gdf = persons_gdf[persons_gdf["level"].notna()].copy()
    logger.info("Persons in age 6-19 with school level assigned: %d", len(persons_gdf))

    # ------------------------------------------------------------------
    # 3. Spatial join home points -> municipalities -> regiostar7
    # ------------------------------------------------------------------
    muni = municipalities.to_crs(persons_gdf.crs)
    persons_gdf = gpd.sjoin(persons_gdf, muni[["commune_id", "geometry"]],
                            how="left", predicate="within")
    if "commune_id_left" in persons_gdf.columns:
        persons_gdf = persons_gdf.rename(columns={"commune_id_left": "commune_id"})
    elif "commune_id_right" in persons_gdf.columns:
        persons_gdf = persons_gdf.rename(columns={"commune_id_right": "commune_id"})

    # Merge regiostar7
    if "commune_id" not in regiostar.columns and "Gemeinde_id" in regiostar.columns:
        regiostar = regiostar.rename(columns={"Gemeinde_id": "commune_id"})
    persons_gdf = persons_gdf.merge(regiostar[["commune_id", "regiostar7"]],
                                    on="commune_id", how="left")
    persons_gdf["regiostar7"] = persons_gdf["regiostar7"].astype("Int64")
    logger.info("Persons with RS7 assigned: %d / %d",
                persons_gdf["regiostar7"].notna().sum(), len(persons_gdf))

    # ------------------------------------------------------------------
    # 4. Load MiD Tab. 43 target table
    # ------------------------------------------------------------------
    repo_root = str(Path(__file__).resolve().parents[1])
    t43_path = os.path.join(repo_root,
                            "eqasim-data/data/braunschweig/mid/mid2023_T43_school_distance_by_rs7.csv")
    if not os.path.isfile(t43_path):
        raise RuntimeError(
            f"MiD Tab. 43 CSV not found at '{t43_path}'. "
            "Run scripts/seed_mid_t43_school_distance.py first."
        )
    raw_t43 = pd.read_csv(t43_path)
    target_table = build_target_table(raw_t43, args.detour_factor)
    logger.info("Target table rows: %d", len(target_table))

    # ------------------------------------------------------------------
    # 5. Calibrate per (level, RS7) using bisection
    # ------------------------------------------------------------------
    results = []

    for level in ("grundschule", "sekundar_1", "sekundar_2"):
        level_mask = persons_gdf["level"] == level
        level_pupils = persons_gdf[level_mask].copy()

        # Schools for this level
        if "level" in schools_gdf.columns:
            level_schools = schools_gdf[schools_gdf["level"] == level].copy()
        else:
            level_schools = schools_gdf.copy()
        level_schools = level_schools.to_crs(persons_gdf.crs)

        if len(level_schools) == 0:
            logger.warning("No schools found for level '%s'; skipping.", level)
            continue

        school_xy = np.column_stack([
            level_schools.geometry.x.values,
            level_schools.geometry.y.values,
        ])
        cap_col = "capacity" if "capacity" in level_schools.columns else level_schools.columns[0]
        if "capacity" in level_schools.columns:
            capacity = level_schools["capacity"].values.astype(float)
        else:
            capacity = np.ones(len(level_schools), dtype=float)

        target_for_level = target_table[target_table["level"] == level].set_index("regiostar7")

        rs7_values = sorted(level_pupils["regiostar7"].dropna().unique())

        for rs7 in rs7_values:
            rs7_int = int(rs7)
            if rs7_int not in target_for_level.index:
                logger.debug("RS7=%d not in target table for level=%s; skipping.", rs7_int, level)
                continue

            cell_pupils = level_pupils[level_pupils["regiostar7"] == rs7]
            n_pupils = len(cell_pupils)
            if n_pupils < _MIN_PUPILS_PER_CELL:
                logger.debug("RS7=%d level=%s: only %d pupils (< %d); skipping.",
                             rs7_int, level, n_pupils, _MIN_PUPILS_PER_CELL)
                continue

            target_km = float(target_for_level.loc[rs7_int, "target_km"])
            pupil_xy = np.column_stack([
                cell_pupils.geometry.x.values,
                cell_pupils.geometry.y.values,
            ])

            logger.info("Calibrating level=%s RS7=%d n=%d target=%.2f km ...",
                        level, rs7_int, n_pupils, target_km)
            slope = secant_calibrate_slope(
                target_km, pupil_xy, school_xy, capacity,
                max_radius_km=_MAX_RADIUS_KM[level],
                seed=args.seed,
                lo=-3.0, hi=-0.001,
                max_iter=40, tol=0.1,
            )
            achieved = mean_distance_for_slope(
                slope, pupil_xy, school_xy, capacity,
                max_radius_km=_MAX_RADIUS_KM[level],
                rng=np.random.RandomState(args.seed),
            )
            logger.info("  -> slope=%.4f  achieved=%.3f km (target=%.2f km)",
                        slope, achieved, target_km)
            results.append({
                "level": level,
                "regiostar7": rs7_int,
                "n_pupils": n_pupils,
                "target_km": target_km,
                "slope": round(slope, 4),
                "achieved_mean_km": round(achieved, 3),
            })

    # ------------------------------------------------------------------
    # 6. Print paste-ready YAML block
    # ------------------------------------------------------------------
    if not results:
        logger.warning("No calibration results produced. Check input data and pupil counts.")
        return

    df_results = pd.DataFrame(results)
    print("\n# Paste under 'education_gravity_slope_by_level_rs7' in config_*braunschweig*.yml")
    print("education_gravity_slope_by_level_rs7:")
    for level in ("grundschule", "sekundar_1", "sekundar_2"):
        sub = df_results[df_results["level"] == level].sort_values("regiostar7")
        if sub.empty:
            continue
        pairs = ", ".join(f"{int(row.regiostar7)}: {row.slope:.4f}"
                          for _, row in sub.iterrows())
        print(f"  {level}: {{{pairs}}}")

    print("\n# Full calibration log:")
    print(df_results.to_string(index=False))


if __name__ == "__main__":
    main()
