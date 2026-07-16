"""Building-potential fit report for the WORK activity (CLI).

Analogous to ``braunschweig.analysis.run_education_validation`` (which reports
school enrollment vs capacity), but for the building activity potentials that
govern the WITHIN-zone placement of WORK locations.

It answers: does the realised within-commune distribution of work activities
over buildings follow the building ``potential_work`` weights, and in which
communes is the fit good vs poor?

Method (see ``braunschweig.calibration.building_fit`` for the pure core):
  - The zone is the commune. The upstream gravity model fixes the per-commune
    work total (GENESIS SvB); ``potential_work`` only governs the within-commune
    building split. The fit is therefore evaluated per commune, in within-commune
    SHARES, which are sampling-rate-invariant (the potentials are a 100% reference
    while a run is sampled, e.g. 25%).
  - Realised work locations come from the cached primary-locations stage
    (``braunschweig.locations.synthesis.replacement_education_gravity`` -> df_work).
  - The potential support is the cached work-candidate stage
    (``braunschweig.locations.work``): each non-fake candidate is one gpkg building
    carrying ``employees == potential_work``. The synthetic-centroid (``fake``)
    candidates -- emitted for communes with no gpkg building -- are EXCLUDED from
    the support, so a worker placed there is counted as a fallback (CLAUDE.md
    no-silent-fallback).

Outputs (into ``--output-dir``):
  - building_potential_fit_by_commune.csv   -- per-commune fit metrics
  - building_potential_fit_per_building.csv -- per-building realised vs potential
  - building_potential_fit_by_commune.gpkg  -- commune polygons + tv_distance (choropleth)
  - building_potential_fit_per_building.gpkg -- building points + share_residual (over/under-fill map)
  - building_potential_fit_summary.md       -- coverage + headline fit

Note: a few non-fake candidates are external-Kreis workplaces (one synthetic
point per outbound Kreis); their commune is a single-building zone, so the
within-zone fit there is undefined (NaN) and they do not affect the ZGB fit.
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

LOGGER = logging.getLogger("run_building_fit")

# Cached synpp stages consumed by the report.
STAGE_REALISED_WORK = "braunschweig.locations.synthesis.replacement_education_gravity"
STAGE_WORK_CANDIDATES = "braunschweig.locations.work"
STAGE_MUNICIPALITIES = "data.spatial.municipalities"


def _load_stage(working_directory, stage_name):
    """Load the most-recently-written synpp pickle for ``stage_name``.

    Pickle files are named ``<stage_name>__<hash>.p``. Raises RuntimeError when
    no pickle for the stage is present (fail-fast; the pipeline must have run).
    """
    pattern = os.path.join(working_directory, f"{stage_name}__*.p")
    hits = glob.glob(pattern)
    if not hits:
        direct = os.path.join(working_directory, f"{stage_name}.p")
        if os.path.exists(direct):
            hits = [direct]
    if not hits:
        raise RuntimeError(
            f"[run_building_fit] no cached pickle for stage '{stage_name}' in "
            f"'{working_directory}'. Run the synpp pipeline first (stage must be complete)."
        )
    if len(hits) > 1:
        # Multiple config-hash generations of one stage: the mtime pick can mix
        # pickles from DIFFERENT runs across stages (work location_id is
        # positional, so a cross-run mix silently misattributes workers to the
        # wrong buildings while the id join still looks ~100% matched).
        LOGGER.warning(
            "_load_stage: %d cached pickles found for stage '%s'; picking the "
            "newest by mtime. Verify ALL loaded stages come from the SAME run "
            "(candidates: %s).", len(hits), stage_name,
            [os.path.basename(h) for h in sorted(hits)],
        )
    path = max(hits, key=os.path.getmtime)
    LOGGER.info("Loading stage '%s' from %s", stage_name, path)
    with open(path, "rb") as fh:
        return pickle.load(fh)


def _prepare_frames(df_work_realised, candidates):
    """Build the (realised, potential_support) frames the pure core expects.

    ``df_work_realised`` : realised work locations (one row per worker) with a
        ``location_id`` column.
    ``candidates`` : the work-candidate GeoDataFrame [employees, fake, commune_id,
        location_id, geometry]. Non-fake rows are the gpkg-building support.

    Returns (realised, potential, support_gdf):
        realised  : DataFrame[building_id]            -- one row per worker
        potential : DataFrame[building_id, zone, potential]
        support_gdf : GeoDataFrame (potential + geometry) for the per-building map
    """
    realised = pd.DataFrame(
        {"building_id": df_work_realised["location_id"].astype(str).values}
    )

    support = candidates[~candidates["fake"]].copy()
    support["building_id"] = support["location_id"].astype(str)
    support["zone"] = support["commune_id"].astype(str)
    support["potential"] = support["employees"].astype(float)

    potential = pd.DataFrame(support[["building_id", "zone", "potential"]])
    support_gdf = gpd.GeoDataFrame(
        support[["building_id", "zone", "potential", "geometry"]],
        geometry="geometry",
        crs=candidates.crs,
    )
    return realised, potential, support_gdf


def _build(working_directory, sampling_rate, output_dir):
    working_directory = str(working_directory)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    realised_stage = _load_stage(working_directory, STAGE_REALISED_WORK)
    # The primary-locations stage returns (df_work, df_education).
    df_work_realised = realised_stage[0] if isinstance(realised_stage, tuple) else realised_stage
    candidates = _load_stage(working_directory, STAGE_WORK_CANDIDATES)

    realised, potential, support_gdf = _prepare_frames(df_work_realised, candidates)

    report = build_fit_report(realised, potential, sampling_rate=sampling_rate)
    per_zone = report["per_zone"]
    coverage = report["coverage"]

    # --- Per-commune CSV. ---
    by_commune_path = output_dir / "building_potential_fit_by_commune.csv"
    per_zone.to_csv(by_commune_path, index=False)
    LOGGER.info("Wrote %s (%d communes)", by_commune_path, len(per_zone))

    # --- Per-building residuals (CSV + GPKG). ---
    residuals = per_building_residuals(realised, potential)
    residuals["realised_count_scaled_100pct"] = residuals["realised_count"] / sampling_rate
    per_building_csv = output_dir / "building_potential_fit_per_building.csv"
    residuals.to_csv(per_building_csv, index=False)
    LOGGER.info("Wrote %s (%d buildings)", per_building_csv, len(residuals))

    res_gdf = support_gdf.merge(
        residuals.drop(columns=["zone", "potential"]), on="building_id", how="left")
    per_building_gpkg = output_dir / "building_potential_fit_per_building.gpkg"
    res_gdf.to_file(per_building_gpkg, driver="GPKG")
    LOGGER.info("Wrote %s", per_building_gpkg)

    # --- Per-commune choropleth GPKG (tv_distance / pearson on commune polygons). ---
    try:
        municipalities = _load_stage(working_directory, STAGE_MUNICIPALITIES)
        muni = municipalities[["commune_id", "geometry"]].copy()
        muni["commune_id"] = muni["commune_id"].astype(str)
        choro = muni.merge(per_zone, left_on="commune_id", right_on="zone", how="inner")
        choro_gdf = gpd.GeoDataFrame(choro, geometry="geometry", crs=municipalities.crs)
        choro_path = output_dir / "building_potential_fit_by_commune.gpkg"
        choro_gdf.to_file(choro_path, driver="GPKG")
        LOGGER.info("Wrote %s (%d communes with geometry)", choro_path, len(choro_gdf))
    except RuntimeError as exc:
        # Explicit skip, never silent (CLAUDE.md no-silent-fallback).
        LOGGER.warning("Skipping commune choropleth GPKG: %s", exc)

    # --- Summary markdown. ---
    _write_summary(output_dir, per_zone, coverage, sampling_rate)
    return report


def _md_table(df, columns):
    """Render a small DataFrame as a GitHub-flavoured markdown table.

    Avoids the optional ``tabulate`` dependency (CLAUDE.md: no new dependencies
    when standard code suffices).
    """
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []
    for _, r in df[columns].iterrows():
        cells = []
        for c in columns:
            v = r[c]
            cells.append(f"{v:.4f}" if isinstance(v, float) else str(v))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *rows])


def _write_summary(output_dir, per_zone, coverage, sampling_rate):
    # A commune is scorable only with >= 2 buildings AND a defined correlation.
    # Single-building zones (e.g. external-Kreis workplaces) give a trivial
    # tv_distance of 0 and an undefined Pearson, so they are excluded.
    valid = per_zone[(per_zone["n_buildings"] >= 2)
                     & per_zone["pearson"].notna()].copy()
    w = valid["realised_activities"].to_numpy(dtype=float)
    tv = valid["tv_distance"].to_numpy(dtype=float)
    pe = valid["pearson"].to_numpy(dtype=float)
    weighted_tv = float(np.average(tv, weights=w)) if w.sum() > 0 else float("nan")
    weighted_pearson = float(np.average(pe, weights=w)) if w.sum() > 0 else float("nan")
    ex = valid["excess_tv"].to_numpy(dtype=float)
    weighted_excess = float(np.average(ex, weights=w)) if w.sum() > 0 else float("nan")
    median_pearson = float(valid["pearson"].median())
    # Activity density: how many sampled workers per candidate building. When this
    # is < 1, more buildings exist than sampled workers, so many potential
    # buildings necessarily receive 0 activities and tv_distance is inflated by
    # discreteness, NOT by a placement bias -- it shrinks as the sampling rate
    # rises. Pearson (scale-insensitive) is the robust fit signal at < 1 density.
    density = (valid["realised_activities"].sum()
               / valid["n_buildings"].sum()) if valid["n_buildings"].sum() else float("nan")

    lines = [
        "# Building-potential fit report (WORK)",
        "",
        f"Sampling rate: {sampling_rate:g} (within-commune SHARE fit is "
        "sampling-rate-invariant; counts are also written scaled to 100%).",
        "",
        "## Coverage (no-silent-fallback)",
        "",
        f"- realised work activities: {coverage['realised_total']:,}",
        f"- on a gpkg potential building (PRIMARY): {coverage['on_potential_building']:,} "
        f"({coverage['primary_rate']*100:.1f}%)",
        f"- on a synthetic-centroid / external workplace (FALLBACK): "
        f"{coverage['off_potential_building']:,} ({coverage['fallback_rate']*100:.1f}%)",
        "",
        "## Within-commune fit (realised share vs potential_work share)",
        "",
        f"- communes scored: {len(valid)} (of {len(per_zone)}; single-building / "
        "external-workplace zones are excluded)",
        f"- sampled work activities per candidate building: {density:.3f} "
        "(< 1 means more buildings than sampled workers -> tv_distance is inflated "
        "by discreteness, not bias; it shrinks toward 0 as the sampling rate rises)",
        f"- activity-weighted mean Pearson r (realised count vs potential_work): "
        f"{weighted_pearson:.3f}  [PRIMARY fit signal -- scale-insensitive]",
        f"- median per-commune Pearson r: {median_pearson:.3f}",
        f"- activity-weighted mean total-variation distance: {weighted_tv:.4f} "
        "(0 = exact; high values here are dominated by the sub-1 activity density)",
        f"- activity-weighted mean EXCESS TV (observed - multinomial noise floor): "
        f"{weighted_excess:.4f}  [sampling-rate-fair misfit; ~0 = as good as the "
        "sample size allows, the 0-effects netted out]",
        "",
        "Largest communes by work volume (the most reliable cells):",
        "",
        _md_table(valid.nlargest(10, "realised_activities"),
                  ["zone", "n_buildings", "realised_activities", "pearson", "tv_distance"]),
        "",
        "Poorest correlation among well-sampled communes (>= 50 activities):",
        "",
        _md_table(valid[valid["realised_activities"] >= 50].nsmallest(5, "pearson"),
                  ["zone", "n_buildings", "realised_activities", "pearson", "tv_distance"]),
        "",
    ]
    path = output_dir / "building_potential_fit_summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    LOGGER.info("Wrote %s", path)


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Building-potential fit report (WORK).")
    p.add_argument("--working-directory", required=True,
                   help="synpp cache directory with the completed stage pickles.")
    p.add_argument("--sampling-rate", type=float, required=True,
                   help="Run sampling rate (e.g. 0.25 for the 25%% cache).")
    p.add_argument("--output-dir", required=True,
                   help="Destination directory for the CSV / GPKG / summary outputs.")
    return p.parse_args(argv)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)
    _build(args.working_directory, args.sampling_rate, args.output_dir)


if __name__ == "__main__":
    main()
