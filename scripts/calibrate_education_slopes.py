"""Calibrate the education gravity decay per (RegioStaR-7, level) to MiD Tab. 43.

For each (home RS7 class, school level) the slope is chosen so the modelled mean
straight-line school-trip distance matches the MiD target (routed / detour). Uses
ONLY the capacity-gravity Furness + a 1-D bisection search (no numpy.linalg
decompositions), so it runs in the eqasim conda env.

Levels calibrated:
  grundschule  -- per-RS7 MiD Tab. 43 target (ages 6-9)
  sekundar_1   -- per-RS7 MiD Tab. 43 target (ages 10-15)
  oberstufe    -- per-RS7 MiD Tab. 43 target (ages 16-19, academic track)
  bbs          -- national Destatis MZ 2024 BBS straight-line mean, same
                  target for every RS7 (vocational, long regional catchment)

Ages 16-19 pupils are first labelled "upper_secondary", then split into
oberstufe / bbs by a Bernoulli draw with probability --bbs-share (default
0.681, derived from NDS enrollment statistics).

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
    assign_by_decay,
)
from braunschweig.data.mid.school_distance import build_target_table  # noqa: E402
from braunschweig.data.mikrozensus.school_distance import (  # noqa: E402
    bbs_target_km,
    hochschule_target_km,
)
from braunschweig.data.bbsr.regiostar import ars_to_ags8  # noqa: E402
from braunschweig.data.schools.university_facilities import (  # noqa: E402
    build_university_facilities,
)

# Maximum search radius per level (km). oberstufe and bbs share the 16-19 age
# band; bbs gets the wider radius to reflect its regional catchment.
_MAX_RADIUS_KM = {
    "grundschule": 15.0,
    "sekundar_1": 30.0,
    "oberstufe": 60.0,
    "bbs": 100.0,
}
# Age bands used to assign pupils to levels. Ages 16-19 are first labelled
# "upper_secondary" and then split into oberstufe / bbs by enrollment share.
_AGE_BANDS = {
    "grundschule": (6, 9),
    "sekundar_1": (10, 15),
    "upper_secondary": (16, 19),
}
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


def mean_distance_decay(slope, pupil_xy, uni_xy, weight, max_radius_km, rng):
    """Mean straight-line km for the singly-constrained university model
    (assign_by_decay) at a single scalar slope."""
    choice = assign_by_decay(pupil_xy, uni_xy, weight, slope=slope,
                             max_radius_km=max_radius_km, rng=rng)
    delta = pupil_xy - uni_xy[choice]
    return float(np.mean(np.sqrt((delta ** 2).sum(axis=1)) / 1000.0))


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


def calibrate_level_per_rs7(pupil_xy, pupil_rs7, school_xy, capacity, targets,
                            max_radius_km, seed, rounds=40, lo=-3.0, hi=-0.02,
                            tol=0.25):
    """Calibrate one school level's per-RegioStaR-7 slopes on the FULL pupil set.

    Calibrating a single (RS7, level) cell in isolation is wrong: the capacity
    constraint, scaled to a pupil subset, would force that subset to also fill
    schools outside its catchment, inflating distances. Instead the WHOLE level
    (all RS7 together) is assigned each round with a per-pupil slope vector
    (looked up by ``pupil_rs7``); each RS7's mean distance is measured and its
    slope updated toward ``targets[rs7]`` by a per-RS7 **bisection** on the
    monotone slope -> mean relationship (mean increases as the slope flattens
    toward 0). Bisection is used rather than a secant because small rural cells
    (tens of pupils at 25%) have noisy means that make a secant overshoot; a
    bracketed bisection lands stably inside the noise band. The coupling (one
    full-level assignment per round) lets each class's slope mainly move its own
    pupils, so the coordinate updates converge in a few rounds.

    pupil_xy: (R, 2) metric; pupil_rs7: (R,) int RS7 per pupil; school_xy: (C, 2);
    capacity: (C,); targets: {rs7: target straight-line km}. Returns {rs7: slope}.
    RS7 codes absent from ``targets`` keep the mid-bracket slope (and are not
    returned).
    """
    pupil_rs7 = np.asarray(pupil_rs7)
    classes = sorted(int(c) for c in targets)
    # per-RS7 bracket: steep end ``lo`` (short trips) .. flat end ``hi`` (long).
    steep = {c: lo for c in classes}
    flat = {c: hi for c in classes}
    slope = {c: 0.5 * (lo + hi) for c in classes}

    for _ in range(int(rounds)):
        slope_vec = np.array([slope.get(int(c), 0.5 * (lo + hi))
                              for c in pupil_rs7], dtype=float)
        choice, _ = assign_by_capacity_gravity(
            pupil_xy, school_xy, capacity, slope=slope_vec,
            max_radius_km=max_radius_km, max_iterations=200, tolerance=1e-6,
            rng=np.random.RandomState(seed))
        d_km = np.sqrt(((pupil_xy - school_xy[choice]) ** 2).sum(axis=1)) / 1000.0

        converged = True
        for c in classes:
            mask = pupil_rs7 == c
            if not mask.any():
                continue
            mean_c = float(d_km[mask].mean())
            if abs(mean_c - targets[c]) > tol:
                converged = False
            # mean too long -> need a steeper slope -> bring the flat bound in;
            # mean too short -> need a flatter slope -> bring the steep bound in.
            if mean_c > targets[c]:
                flat[c] = slope[c]
            else:
                steep[c] = slope[c]
            slope[c] = 0.5 * (steep[c] + flat[c])
        if converged:
            break
    return slope


# ---------------------------------------------------------------------------
# Helpers for main() — not unit-tested
# ---------------------------------------------------------------------------

def age_to_level_school(age):
    """Return the school level for a given age, or None if outside 6-19.

    Ages 16-19 return the intermediate label 'upper_secondary'; the caller
    in main() splits this into 'oberstufe' / 'bbs' by enrollment share.
    """
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
    parser.add_argument("--output-dir", default=None,
                        help="If set, write calibration_results.csv, two figures "
                             "and calibration_summary.md to this directory.")
    parser.add_argument("--bbs-share", type=float, default=0.681,
                        help="Fraction of ages-16-19 pupils assigned to BBS "
                             "(vocational), remainder goes to oberstufe (academic). "
                             "Default 0.681 from NDS enrollment statistics.")
    parser.add_argument("--university-max-radius", type=float, default=150.0,
                        help="Maximum search radius in km for university assignment "
                             "(singly-constrained decay model). Default 150.0 km.")
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

    # Split ages-16-19 "upper_secondary" pupils into oberstufe (academic) and
    # bbs (vocational) using an enrollment-share Bernoulli draw. The share is
    # configurable via --bbs-share (default 0.681). Each draw is reproducible
    # given a fixed --seed.
    us = persons_gdf["level"] == "upper_secondary"
    if us.any():
        logger.info(
            "Splitting %d upper_secondary pupils: bbs_share=%.3f -> "
            "bbs ~%d, oberstufe ~%d",
            int(us.sum()), args.bbs_share,
            int(round(us.sum() * args.bbs_share)),
            int(round(us.sum() * (1.0 - args.bbs_share))),
        )
        rng_split = np.random.RandomState(args.seed)
        draw = rng_split.random_sample(size=int(us.sum())) < args.bbs_share
        persons_gdf.loc[us, "level"] = np.where(draw, "bbs", "oberstufe")

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

    # Merge regiostar7. municipalities carry the 12-digit ARS; regiostar keys on
    # the 8-digit AGS -> convert before the merge (else every pupil is unmatched).
    if "commune_id" not in regiostar.columns and "Gemeinde_id" in regiostar.columns:
        regiostar = regiostar.rename(columns={"Gemeinde_id": "commune_id"})
    persons_gdf["commune_id"] = persons_gdf["commune_id"].map(ars_to_ags8)
    persons_gdf = persons_gdf.merge(regiostar[["commune_id", "regiostar7"]],
                                    on="commune_id", how="left")
    persons_gdf["regiostar7"] = persons_gdf["regiostar7"].astype("Int64")
    logger.info("Persons with RS7 assigned: %d / %d",
                persons_gdf["regiostar7"].notna().sum(), len(persons_gdf))

    # ------------------------------------------------------------------
    # 4. Load MiD Tab. 43 target table (grundschule / sekundar_1 / oberstufe)
    #    and the Destatis MZ 2024 BBS national mean (bbs)
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

    mz_path = os.path.join(
        repo_root,
        "eqasim-data/data/braunschweig/mikrozensus/"
        "mikrozensus2024_school_distance_by_type.csv",
    )
    if not os.path.isfile(mz_path):
        raise RuntimeError(
            f"Destatis MZ 2024 school-distance CSV not found at '{mz_path}'. "
            "Run scripts/extract_mikrozensus_bundesland_margins.py or the "
            "relevant seed script first."
        )
    raw_mz = pd.read_csv(mz_path)
    bbs_t = bbs_target_km(raw_mz, args.detour_factor)
    logger.info("BBS national straight-line target: %.3f km "
                "(detour factor %.2f)", bbs_t, args.detour_factor)

    # ------------------------------------------------------------------
    # 5. Calibrate per level on the FULL pupil set (coupled per-RS7 secant)
    # ------------------------------------------------------------------
    results = []

    for level in ("grundschule", "sekundar_1", "oberstufe", "bbs"):
        level_pupils = persons_gdf[persons_gdf["level"] == level].copy()

        if "level" in schools_gdf.columns:
            level_schools = schools_gdf[schools_gdf["level"] == level].copy()
        else:
            level_schools = schools_gdf.copy()
        level_schools = level_schools.to_crs(persons_gdf.crs)
        if len(level_schools) == 0 or level_pupils.empty:
            logger.warning("No schools/pupils for level '%s'; skipping.", level)
            continue

        school_xy = np.column_stack([level_schools.geometry.x.values,
                                     level_schools.geometry.y.values])
        if "capacity" in level_schools.columns:
            capacity = level_schools["capacity"].values.astype(float)
        else:
            capacity = np.ones(len(level_schools), dtype=float)

        pupil_xy = np.column_stack([level_pupils.geometry.x.values,
                                    level_pupils.geometry.y.values])
        pupil_rs7 = level_pupils["regiostar7"].fillna(-1).astype(int).to_numpy()
        counts = pd.Series(pupil_rs7).value_counts()

        if level == "bbs":
            # BBS uses a single national Destatis MZ 2024 target for every RS7;
            # rural cells legitimately exceed it due to school sparsity.
            targets = {
                int(rs7): bbs_t
                for rs7 in counts.index
                if int(rs7) > 0 and counts[int(rs7)] >= _MIN_PUPILS_PER_CELL
            }
            logger.info(
                "BBS: applying national target %.3f km to RS7 codes %s",
                bbs_t, sorted(targets.keys()),
            )
        else:
            # grundschule / sekundar_1 / oberstufe: per-RS7 MiD Tab. 43 target
            target_for_level = (target_table[target_table["level"] == level]
                                .set_index("regiostar7"))
            targets = {
                int(rs7): float(target_for_level.loc[rs7, "target_km"])
                for rs7 in target_for_level.index
                if int(rs7) in counts.index and counts[int(rs7)] >= _MIN_PUPILS_PER_CELL
            }

        if not targets:
            logger.warning("No RS7 cells with >= %d pupils for level '%s'; skipping.",
                           _MIN_PUPILS_PER_CELL, level)
            continue

        logger.info("Calibrating level=%s on %d pupils, %d schools, RS7 targets=%s",
                    level, len(level_pupils), len(level_schools),
                    {k: round(v, 2) for k, v in targets.items()})
        slopes = calibrate_level_per_rs7(
            pupil_xy, pupil_rs7, school_xy, capacity, targets,
            max_radius_km=_MAX_RADIUS_KM[level], seed=args.seed,
            rounds=40, lo=-3.0, hi=-0.02, tol=0.25)

        def _achieved(slope_dict):
            sv = np.array([slope_dict.get(int(c), -0.3) for c in pupil_rs7],
                          dtype=float)
            ch, _ = assign_by_capacity_gravity(
                pupil_xy, school_xy, capacity, slope=sv,
                max_radius_km=_MAX_RADIUS_KM[level], max_iterations=200,
                tolerance=1e-6, rng=np.random.RandomState(args.seed))
            return np.sqrt(((pupil_xy - school_xy[ch]) ** 2).sum(axis=1)) / 1000.0

        d_km = _achieved(slopes)
        counts = {rs7: int((pupil_rs7 == rs7).sum()) for rs7 in targets}
        errs = {rs7: abs(float(d_km[pupil_rs7 == rs7].mean()) - targets[rs7])
                for rs7 in targets}

        # Shrinkage regularisation: tiny/sparse RS7 cells (tens of pupils at 25%)
        # have a noisy, coupling-dominated mean that neither secant nor bisection
        # can pin reliably. Any such cell off by > 1.5 km is replaced by the
        # pupil-weighted mean slope of the converged cells (<= 1.0 km error) of
        # the same level. Cells sitting at the steep bound (slope ~ -3.0) are NOT
        # regularised: there the target is simply below the nearest-school floor
        # (e.g. rural BBS, nearest school already farther than the national mean),
        # which is a legitimate structural distance, not calibration noise.
        floor_slope = -2.95
        reliable = [rs7 for rs7 in targets if errs[rs7] <= 1.0]
        unreliable = [rs7 for rs7 in targets
                      if errs[rs7] > 1.5 and slopes[rs7] > floor_slope]
        if reliable and unreliable:
            wsum = sum(counts[rs7] for rs7 in reliable)
            reg = sum(slopes[rs7] * counts[rs7] for rs7 in reliable) / wsum
            for rs7 in unreliable:
                logger.info("  regularize %s RS7=%d (n=%d, err=%.1f km) "
                            "slope %.4f -> %.4f (reliable-cell mean)",
                            level, rs7, counts[rs7], errs[rs7], slopes[rs7], reg)
                slopes[rs7] = float(reg)
            d_km = _achieved(slopes)

        for rs7 in sorted(targets):
            mask = pupil_rs7 == rs7
            achieved = float(d_km[mask].mean())
            logger.info("  level=%s RS7=%d n=%d target=%.2f -> slope=%.4f achieved=%.2f km",
                        level, rs7, int(mask.sum()), targets[rs7],
                        slopes[rs7], achieved)
            results.append({
                "level": level,
                "regiostar7": rs7,
                "n_pupils": int(mask.sum()),
                "target_km": round(targets[rs7], 3),
                "slope": round(slopes[rs7], 4),
                "achieved_mean_km": round(achieved, 3),
            })

    # ------------------------------------------------------------------
    # 6. University calibration (singly-constrained, single national slope)
    #
    # University commuters are age-20+ persons with an education trip. The
    # model is assign_by_decay (no capacity balancing): a single national slope
    # is bisected so the mean straight-line home-to-university distance matches
    # the Destatis MZ 2024 Hochschule target (routed / detour_factor). The
    # 20+ pupil subset is built by the same merge path as the school pupils
    # (candidates[has_education_trip] + enriched age + home locations).
    # ------------------------------------------------------------------

    # Build the age-20+ pupil GeoDataFrame using the same stage data that was
    # already loaded for the school pupil frame.
    persons_raw_all = _load_stage(wd, "synthesis.population.spatial.primary.candidates")["persons"]
    persons_20plus = persons_raw_all[persons_raw_all["has_education_trip"]].copy()
    persons_20plus = persons_20plus.merge(age_cols, on="person_id", how="left")
    persons_20plus = persons_20plus[persons_20plus["age"] >= 20].copy()
    persons_20plus = persons_20plus.merge(homes_geom, on="household_id", how="left")
    import geopandas as gpd  # already imported above; re-import is a no-op
    persons_uni = gpd.GeoDataFrame(persons_20plus, geometry="home_geom",
                                   crs=homes.crs)
    logger.info("Age-20+ persons with education trip: %d", len(persons_uni))

    if len(persons_uni) == 0:
        logger.warning("No age-20+ education-trip persons found; skipping university calibration.")
    else:
        # Build university facilities from the raw CSV + cached OSM stage
        nds_h_path = os.path.join(
            repo_root, "eqasim-data/data/braunschweig/schools/nds_hochschulen.csv")
        if not os.path.isfile(nds_h_path):
            raise RuntimeError(
                f"nds_hochschulen.csv not found at '{nds_h_path}'. "
                "Run scripts/seed_nds_hochschulen.py first."
            )
        df_h = pd.read_csv(nds_h_path, dtype={"ars5": str})
        osm_edu = _load_stage(wd, "eqasim_common.locations.education")
        osm_uni = osm_edu[osm_edu["education_type"] == "university"].copy()
        uni = build_university_facilities(df_h, osm_uni)
        uni = uni.to_crs(persons_uni.crs)

        pupil_xy_uni = np.column_stack([persons_uni.geometry.x.values,
                                        persons_uni.geometry.y.values])
        uni_xy = np.column_stack([uni.geometry.x.values, uni.geometry.y.values])
        uni_weight = uni["capacity"].values.astype(float)

        hochschule_t = hochschule_target_km(raw_mz, args.detour_factor)
        logger.info("Hochschule straight-line target: %.3f km (detour factor %.2f)",
                    hochschule_t, args.detour_factor)

        # Bisection: mean distance decreases monotonically as slope becomes more
        # negative. Bracket [lo, hi] = [-1.0, -0.001]; tolerance 0.3 km; 30 iters.
        uni_lo, uni_hi = -1.0, -0.001
        uni_tol = 0.3
        uni_seed = args.seed

        def _uni_f(s):
            return mean_distance_decay(
                s, pupil_xy_uni, uni_xy, uni_weight,
                args.university_max_radius,
                np.random.RandomState(uni_seed)) - hochschule_t

        fa_uni = _uni_f(uni_lo)
        for _ in range(30):
            m_uni = 0.5 * (uni_lo + uni_hi)
            fm_uni = _uni_f(m_uni)
            if abs(fm_uni) < uni_tol:
                break
            if (fm_uni < 0) == (fa_uni < 0):
                uni_lo, fa_uni = m_uni, fm_uni
            else:
                uni_hi = m_uni
        uni_slope = 0.5 * (uni_lo + uni_hi)

        uni_achieved = mean_distance_decay(
            uni_slope, pupil_xy_uni, uni_xy, uni_weight,
            args.university_max_radius, np.random.RandomState(uni_seed))
        n_uni = len(persons_uni)
        logger.info(
            "university: n=%d, target=%.3f km, slope=%.4f, achieved=%.3f km",
            n_uni, hochschule_t, uni_slope, uni_achieved)
        results.append({
            "level": "university",
            "regiostar7": -1,
            "n_pupils": n_uni,
            "target_km": round(hochschule_t, 3),
            "slope": round(uni_slope, 4),
            "achieved_mean_km": round(uni_achieved, 3),
        })

    # ------------------------------------------------------------------
    # 7. Print paste-ready YAML block
    # ------------------------------------------------------------------
    if not results:
        logger.warning("No calibration results produced. Check input data and pupil counts.")
        return

    df_results = pd.DataFrame(results)
    print("\n# Paste under 'education_gravity_slope_by_level_rs7' in config_*braunschweig*.yml")
    print(_yaml_block(df_results))

    # University slope is a single national value; emit on its own line so it
    # can be pasted directly under 'education_university_slope' in the config.
    uni_row = df_results[df_results["level"] == "university"]
    if not uni_row.empty:
        print("\neducation_university_slope: %.4f" % float(uni_row.iloc[0]["slope"]))

    print("\n# Full calibration log:")
    print(df_results.to_string(index=False))

    if args.output_dir:
        _write_outputs(df_results, args.detour_factor, args.output_dir)
        logger.info("Wrote calibration evaluation to %s", args.output_dir)


_RS7_LABELS = {
    71: "Metropole", 72: "Regiopole/GS", 73: "Mittelstadt",
    74: "kleinst./doerfl.", 75: "l. zentrale Stadt",
    76: "l. Mittelstadt", 77: "l. kleinst./doerfl.",
}


def _yaml_block(df_results):
    lines = ["education_gravity_slope_by_level_rs7:"]
    for level in ("grundschule", "sekundar_1", "oberstufe", "bbs"):
        sub = df_results[df_results["level"] == level].sort_values("regiostar7")
        if sub.empty:
            continue
        pairs = ", ".join(f"{int(r.regiostar7)}: {r.slope:.4f}"
                          for _, r in sub.iterrows())
        lines.append(f"  {level}: {{{pairs}}}")
    return "\n".join(lines)


def _write_outputs(df_results, detour_factor, output_dir):
    """Write the calibration results CSV, two figures, and a markdown summary."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(output_dir, exist_ok=True)
    df_results = df_results.copy()
    df_results["abs_error_km"] = (df_results["achieved_mean_km"]
                                  - df_results["target_km"]).abs()
    df_results.to_csv(os.path.join(output_dir, "calibration_results.csv"),
                      index=False)

    levels = ["grundschule", "sekundar_1", "oberstufe", "bbs"]

    # Figure 1: target vs achieved straight-line mean per level x RS7
    fig, axes = plt.subplots(1, 4, figsize=(20, 5), sharey=True)
    for ax, level in zip(axes, levels):
        sub = df_results[df_results["level"] == level].sort_values("regiostar7")
        if sub.empty:
            ax.set_title(level + " (no data)")
            continue
        idx = np.arange(len(sub))
        label = "MZ 2024 national" if level == "bbs" else "MiD target"
        ax.bar(idx - 0.2, sub["target_km"], width=0.4, label=label,
               color="#4C72B0")
        ax.bar(idx + 0.2, sub["achieved_mean_km"], width=0.4, label="model",
               color="#DD8452")
        ax.set_xticks(idx)
        ax.set_xticklabels([f"RS7 {int(r)}" for r in sub["regiostar7"]],
                           rotation=45, ha="right", fontsize=8)
        ax.set_title(level)
        ax.set_ylabel("mean school-trip km (straight-line)")
        ax.legend(fontsize=8)
    fig.suptitle("Education slope calibration vs MiD Tab. 43 / MZ 2024 BBS "
                 f"(detour factor {detour_factor})")
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "calibration_target_vs_achieved.png"),
                dpi=120)
    plt.close(fig)

    # Figure 2: calibrated slope per RS7 x level
    fig, ax = plt.subplots(figsize=(12, 5))
    width = 0.2
    for i, level in enumerate(levels):
        sub = df_results[df_results["level"] == level].sort_values("regiostar7")
        if sub.empty:
            continue
        idx = np.arange(len(sub))
        ax.bar(idx + (i - 1.5) * width, sub["slope"], width=width, label=level)
        ax.set_xticks(np.arange(len(sub)))
        ax.set_xticklabels([f"RS7 {int(r)}" for r in sub["regiostar7"]])
    ax.set_ylabel("calibrated slope (1/km)")
    ax.set_title("Calibrated education decay slope by RegioStaR-7 and level")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "calibration_slopes_by_rs7.png"), dpi=120)
    plt.close(fig)

    # Markdown summary
    floor = df_results[(df_results["slope"] <= -2.999)]
    miss = df_results[df_results["abs_error_km"] > 0.5]
    lines = [
        "# Education slope calibration vs MiD 2023 Tabelle 43 / MZ 2024 BBS",
        "",
        f"Detour factor (routed -> straight-line): {detour_factor}. Calibrated on "
        "the 25% synthesis (cache_bs_25pct); per-pupil slope by home RegioStaR-7, "
        "coupled full-level secant.",
        "",
        "Levels: grundschule / sekundar_1 / oberstufe use per-RS7 MiD Tab. 43 targets. "
        "bbs uses the national Destatis MZ 2024 BBS straight-line mean as a uniform "
        "target across all RS7 (rural cells may legitimately exceed it due to school sparsity).",
        "",
        "## Results (straight-line mean school-trip km)",
        "",
        "```",
        df_results[["level", "regiostar7", "n_pupils", "target_km",
                    "achieved_mean_km", "abs_error_km", "slope"]]
        .to_string(index=False),
        "```",
        "",
        "## Assessment",
        "",
        f"- Cells within 0.5 km of target: {len(df_results) - len(miss)} / "
        f"{len(df_results)}.",
        f"- Cells at the steepest bracket bound (slope = -3.0, school-sparsity "
        f"floor): {len(floor)} "
        f"({', '.join(f'{r.level}/RS7 {int(r.regiostar7)}' for _, r in floor.iterrows()) or 'none'}).",
        "- grundschule and sekundar_1 calibrate well. oberstufe rural cells "
        "(RS7 74/77) may not reach the MiD target even at the steepest slope: rural "
        "upper-secondary schools (Gymnasium/Oberstufe) are sparse. bbs rural cells "
        "legitimately exceed the national MZ 2024 mean due to regional school sparsity "
        "-- this is expected and consistent with the longer BBS catchment assumption.",
    ]
    with open(os.path.join(output_dir, "calibration_summary.md"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    main()
