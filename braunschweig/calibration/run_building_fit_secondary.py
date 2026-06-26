"""Building-potential fit report for SECONDARY activities (shop / leisure / other).

The secondary analogue of ``run_building_fit`` (work). It measures whether the
realised within-zone distribution of secondary activities over buildings follows
the per-purpose building potentials.

Plumbing differs from work and is handled here:
  - Realised secondary locations
    (``braunschweig.synthesis.locations.secondary_chainsolvers`` -> df0) carry
    [person_id, activity_index, location_id, geometry] but NO purpose; the purpose
    is joined from ``synthesis.population.activities`` on (person_id, activity_index).
  - The per-building potential value is NOT exposed by any cached candidate stage
    (the chainsolver attaches it internally), so we go to the ground-truth
    ``building_activity_potentials.parquet`` directly and spatially join each
    realised secondary POINT into the building footprint to recover its building
    and zone (``target_taz``). Realised points that fall in no potential building
    are reported as a fallback (CLAUDE.md no-silent-fallback) -- expected to be
    non-trivial for ``other`` (broad OSM catalog, not gpkg-potential-replaced).

Per-purpose potential column (building_activity_potentials.parquet):
  - shop    = potential_retail_daily + potential_retail_non_daily
  - leisure = potential_leisure
  - other   = potential_generic

Outputs per purpose into ``--output-dir``:
  building_potential_fit_secondary_<purpose>_by_taz.csv / .gpkg,
  ..._per_building.csv, and a combined building_potential_fit_secondary_summary.md.
The zone is the parquet TAZ (``target_taz``); the within-zone SHARE fit and the
multinomial excess_tv are sampling-rate-invariant (see building_fit docstring).
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd

from braunschweig.calibration.building_fit import build_fit_report, per_building_residuals

LOGGER = logging.getLogger("run_building_fit_secondary")

STAGE_REALISED_SECONDARY = "braunschweig.synthesis.locations.secondary_chainsolvers"
STAGE_ACTIVITIES = "synthesis.population.activities"

# eqasim secondary purpose -> building_activity_potentials columns that quantify it.
PURPOSE_POTENTIAL_COLUMNS = {
    "shop": ["potential_retail_daily", "potential_retail_non_daily"],
    "leisure": ["potential_leisure"],
    "other": ["potential_generic"],
}


def _load_stage(working_directory, stage_name):
    pattern = os.path.join(working_directory, f"{stage_name}__*.p")
    hits = glob.glob(pattern)
    if not hits:
        direct = os.path.join(working_directory, f"{stage_name}.p")
        if os.path.exists(direct):
            hits = [direct]
    if not hits:
        raise RuntimeError(
            f"[run_building_fit_secondary] no cached pickle for stage '{stage_name}' "
            f"in '{working_directory}'. Run the synpp pipeline first."
        )
    path = max(hits, key=os.path.getmtime)
    LOGGER.info("Loading stage '%s' from %s", stage_name, path)
    with open(path, "rb") as fh:
        return pickle.load(fh)


def secondary_potential_support(parquet, purpose, *, id_col="building_id",
                                zone_col="target_taz"):
    """Per-building potential support for one secondary purpose.

    Returns DataFrame[building_id, zone, potential] for the buildings that carry a
    strictly positive potential for ``purpose`` (the within-zone share denominator).
    """
    cols = PURPOSE_POTENTIAL_COLUMNS[purpose]
    value = parquet[cols].sum(axis=1)
    out = pd.DataFrame({
        "building_id": parquet[id_col].values,
        "zone": parquet[zone_col].astype(str).values,
        "potential": value.values,
    })
    return out[out["potential"] > 0].reset_index(drop=True)


def _match_realised_to_buildings(realised_points, parquet_gdf):
    """Spatial-join realised secondary points into building footprints.

    Returns the realised GeoDataFrame with a ``building_id`` column (NaN where the
    point falls in no potential building -> fallback).
    """
    if realised_points.crs != parquet_gdf.crs:
        realised_points = realised_points.to_crs(parquet_gdf.crs)
    joined = gpd.sjoin(
        realised_points[["row_id", "purpose", "geometry"]],
        parquet_gdf[["building_id", "geometry"]],
        predicate="within", how="left",
    )
    # A point inside two overlapping footprints would duplicate; keep the first.
    joined = joined.drop_duplicates(subset="row_id", keep="first")
    return joined


def _build(working_directory, sampling_rate, output_dir, parquet_path):
    working_directory = str(working_directory)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sec_stage = _load_stage(working_directory, STAGE_REALISED_SECONDARY)
    realised = sec_stage[0] if isinstance(sec_stage, tuple) else sec_stage
    activities = _load_stage(working_directory, STAGE_ACTIVITIES)

    # Attach purpose to each realised secondary location.
    realised = realised.merge(
        activities[["person_id", "activity_index", "purpose"]],
        on=["person_id", "activity_index"], how="left",
    )
    realised = gpd.GeoDataFrame(realised, geometry="geometry", crs=sec_stage[0].crs
                                if isinstance(sec_stage, tuple) else realised.crs)
    realised = realised[realised["purpose"].isin(PURPOSE_POTENTIAL_COLUMNS)].copy()
    realised["row_id"] = np.arange(len(realised))

    LOGGER.info("Loading building potentials parquet: %s", parquet_path)
    parquet = gpd.read_parquet(parquet_path)
    LOGGER.info("Spatial-joining %d realised secondary points into %d building footprints",
                len(realised), len(parquet))
    matched = _match_realised_to_buildings(realised, parquet)
    realised = realised.merge(matched[["row_id", "building_id"]], on="row_id", how="left")

    summary_blocks = []
    for purpose in ("shop", "leisure", "other"):
        sub = realised[realised["purpose"] == purpose]
        support = secondary_potential_support(parquet, purpose)
        # Realised activities of this purpose, keyed by the matched building_id
        # (NaN -> fell in no building; build_fit_report counts those as fallback).
        realised_ids = pd.DataFrame({"building_id": sub["building_id"].values})
        report = build_fit_report(realised_ids, support, sampling_rate=sampling_rate)
        per_zone = report["per_zone"]
        cov = report["coverage"]

        per_zone.to_csv(output_dir / f"building_potential_fit_secondary_{purpose}_by_taz.csv",
                        index=False)
        residuals = per_building_residuals(realised_ids, support)
        residuals.to_csv(
            output_dir / f"building_potential_fit_secondary_{purpose}_per_building.csv",
            index=False)
        summary_blocks.append(_summary_block(purpose, per_zone, cov))
        LOGGER.info("[%s] primary %.1f%%, communes/TAZ scored %d",
                    purpose, cov["primary_rate"] * 100,
                    int((per_zone["n_buildings"] >= 2).sum()))

    (output_dir / "building_potential_fit_secondary_summary.md").write_text(
        "# Building-potential fit report (SECONDARY)\n\n"
        f"Sampling rate: {sampling_rate:g}. Zone = building TAZ (target_taz). "
        "excess_tv = observed TV minus the multinomial sampling-noise floor "
        "(sampling-rate-fair misfit).\n\n"
        "> **METHOD LIMITATION (read before interpreting).** Unlike the work report\n"
        "> (which joins the realised location_id to the candidate stage exactly), this\n"
        "> report spatially joins realised secondary POINTS into building footprints,\n"
        "> because the chainsolver's per-candidate potential weight is not exposed by\n"
        "> any cached stage. On the 25% cache only ~66% of realised secondary points\n"
        "> fall inside ANY building footprint (the rest are catalog/OSM candidate\n"
        "> points up to ~1 km from the nearest gpkg building), and the join cannot\n"
        "> distinguish 'not a retail/leisure building' from 'not in the parquet at\n"
        "> all'. The resulting high fallback rate therefore reflects a MEASUREMENT\n"
        "> limitation, not necessarily a placement-fit problem (CLAUDE.md\n"
        "> no-silent-fallback: a high fallback rate signals the primary method needs\n"
        "> fixing). A clean secondary fit requires exposing the chainsolver's\n"
        "> per-candidate potential and joining by location_id, as the work report does.\n\n"
        + "\n".join(summary_blocks),
        encoding="utf-8")
    LOGGER.info("Wrote secondary summary to %s", output_dir)


def _summary_block(purpose, per_zone, coverage):
    valid = per_zone[(per_zone["n_buildings"] >= 2) & per_zone["pearson"].notna()]
    w = valid["realised_activities"].to_numpy(dtype=float)
    if w.sum() > 0:
        wp = float(np.average(valid["pearson"], weights=w))
        wex = float(np.average(valid["excess_tv"], weights=w))
        wtv = float(np.average(valid["tv_distance"], weights=w))
    else:
        wp = wex = wtv = float("nan")
    density = (valid["realised_activities"].sum() / valid["n_buildings"].sum()
               if valid["n_buildings"].sum() else float("nan"))
    return "\n".join([
        f"## {purpose}",
        "",
        f"- realised activities: {coverage['realised_total']:,}; "
        f"on a potential building (PRIMARY): {coverage['primary_rate']*100:.1f}%, "
        f"fallback (no potential building): {coverage['fallback_rate']*100:.1f}%",
        f"- TAZ zones scored: {len(valid)}; activity density per building: {density:.3f}",
        f"- activity-weighted Pearson r: {wp:.3f}",
        f"- activity-weighted TV distance: {wtv:.4f} "
        f"(EXCESS over noise floor: {wex:.4f})",
        "",
    ])


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Building-potential fit report (SECONDARY).")
    p.add_argument("--working-directory", required=True)
    p.add_argument("--sampling-rate", type=float, required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--building-potentials-path", required=True,
                   help="Path to building_activity_potentials.parquet (ground-truth potentials).")
    return p.parse_args(argv)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)
    _build(args.working_directory, args.sampling_rate, args.output_dir,
           args.building_potentials_path)


if __name__ == "__main__":
    main()
