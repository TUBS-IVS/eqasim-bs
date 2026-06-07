"""CLI entry point: PopulationSim-style control validation + quality assessment
+ geo-exploration export for one synthetic population (run output or synpp cache).

Usage (PowerShell, conda env eqasim):
    $env:PYTHONUTF8=1; python -m braunschweig.analysis.population_validation.run_population_validation `
        --run-output-dir eqasim-data/output_bs_25pct --label 25pct

Note: controls whose attribute column is absent from the chosen source are skipped
(logged as WARNING), not silently ignored. The eqasim run-output CSVs carry a
narrower person/household schema than the synpp cache; in particular `license_type`,
`economic_status` and `housing_tenure` may be absent from a run-output dir, so those
controls validate only when present (e.g. when run against `--sim-cache` or a richer
output). This is expected and logged.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

from braunschweig.analysis import spatial
from braunschweig.analysis.population_validation import (
    control_validation as CV,
    controls as C,
    geo_export as GE,
    population_source as PS,
    quality_assessment as QA,
    validation_chart as VC,
)

LOGGER = logging.getLogger("braunschweig.analysis.population_validation")
REPO_ROOT = spatial.REPO_ROOT
DATA_PATH = str(REPO_ROOT / "eqasim-data" / "data")


def _attach_home_geometry_to_vehicles(vehicles, persons, home_geom):
    """Attach home geometry + ars5 + commune_id to each vehicle.

    The eqasim vehicles frame is keyed on ``owner_id`` (a person id), not
    ``household_id`` (only the household-fleet car rows carry household_id;
    passenger and legacy-fleet rows do not). We therefore route
    owner_id -> person_id -> household_id -> home geometry. Returns None and
    logs a WARNING if the vehicles frame has neither ``owner_id`` nor
    ``household_id`` (cannot be geolocated -> no silent fallback).
    """
    if "household_id" in vehicles.columns:
        return vehicles.merge(home_geom, on="household_id", how="left")
    if "owner_id" in vehicles.columns:
        person_hh = persons[["person_id", "household_id"]].drop_duplicates("person_id")
        linked = vehicles.merge(person_hh, left_on="owner_id", right_on="person_id", how="left")
        n_unlinked = int(linked["household_id"].isna().sum())
        if n_unlinked:
            LOGGER.warning(
                "vehicles: %d/%d vehicle(s) could not be linked to a household via "
                "owner_id->person_id; their geometry will be missing",
                n_unlinked, len(linked),
            )
        return linked.merge(home_geom, on="household_id", how="left")
    LOGGER.warning(
        "vehicles frame has neither 'household_id' nor 'owner_id'; the vehicles "
        "layer cannot be geolocated and is skipped")
    return None


def _parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-output-dir", default=None)
    ap.add_argument("--sim-cache", default=None)
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--analysis-out", default=None)
    ap.add_argument("--label", default=None)
    ap.add_argument("--whisker", choices=["stdev", "rmse", "both"], default="both")
    ap.add_argument("--geo", dest="geo", action="store_true", default=True)
    ap.add_argument("--no-geo", dest="geo", action="store_false")
    ns = ap.parse_args(argv)
    if (ns.run_output_dir is None) == (ns.sim_cache is None):
        ap.error("pass exactly ONE of --run-output-dir / --sim-cache")
    return ns


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=str(REPO_ROOT)).decode().strip()
    except Exception:
        return "unknown"


def _deviation_wide(long: pd.DataFrame, geography: str, id_name: str) -> pd.DataFrame:
    sub = long[long["geography"] == geography]
    if sub.empty:
        return pd.DataFrame(columns=[id_name])
    sub = sub.copy()
    sub["key"] = sub["control"] + "__" + sub["category"].astype(str) + "_delta_pp"
    wide = sub.pivot_table(index="geo_id", columns="key", values="delta_pp",
                           aggfunc="first").reset_index()
    return wide.rename(columns={"geo_id": id_name})


def _interpretation_markdown(quality: pd.DataFrame) -> str:
    if quality.empty:
        return "_No controls with a target were evaluated._"
    good = quality[quality["grade"].isin(["very good", "good"])].head(10)
    bad = quality[quality["grade"].isin(["acceptable", "needs improvement"])] \
        .sort_values("mean_abs_delta_pp", ascending=False)
    lines = ["### Where are we good", ""]
    for r in good.itertuples():
        lines.append(f"- **{r.control}** ({r.grade}, mean |delta| "
                     f"{r.mean_abs_delta_pp:.2f} pp, SRMSE {r.srmse:.3f})")
    lines += ["", "### What can be improved", ""]
    for r in bad.itertuples():
        lines.append(f"- **{r.control}** ({r.grade}, mean |delta| "
                     f"{r.mean_abs_delta_pp:.2f} pp, SRMSE {r.srmse:.3f})")
    lines += ["", "### Possible causes", ""]
    for r in bad.itertuples():
        if getattr(r, "cause_hint", ""):
            lines.append(f"- **{r.control}**: {r.cause_hint}")
    return "\n".join(lines)


def run(ns) -> dict:
    frames = PS.load_population(run_output_dir=ns.run_output_dir,
                               sim_cache=ns.sim_cache, prefix=ns.prefix)
    source_path = Path(frames.source_path)
    out = Path(ns.analysis_out) if ns.analysis_out else source_path / "analysis" / "population_validation"
    out.mkdir(parents=True, exist_ok=True)
    label = ns.label or frames.prefix.rstrip("_")

    kreise = spatial.load_kreise(frames.homes.crs)
    homes_geo = spatial.assign_geographies(frames.homes, kreise=kreise)
    geo = homes_geo[["household_id", "ars5", "commune_id"]].drop_duplicates("household_id")

    registry = C.build_registry(DATA_PATH)
    long = CV.evaluate_all(registry, frames, geo, DATA_PATH)
    summary = CV.summarize(long)
    quality = QA.assess(long)
    fam = QA.family_scores(quality)

    long.to_csv(out / "controls_long.csv", index=False)
    summary.to_csv(out / "controls_summary.csv", index=False)
    quality.to_csv(out / "quality_summary.csv", index=False)
    fam.to_csv(out / "quality_by_family.csv", index=False)

    if ns.whisker in ("stdev", "both"):
        VC.dot_and_whisker(summary, out / "validation_chart_stdev.png", whisker="stdev")
    if ns.whisker in ("rmse", "both"):
        VC.dot_and_whisker(summary, out / "validation_chart_rmse.png", whisker="rmse")
    VC.quality_plot(quality, out / "quality_by_control.png")

    geo_paths: dict = {}
    if ns.geo:
        home_geom = homes_geo[["household_id", "ars5", "commune_id", "geometry"]]
        persons_geo = frames.persons.merge(home_geom, on="household_id", how="left")
        persons_gdf = gpd.GeoDataFrame(persons_geo, geometry="geometry", crs=frames.homes.crs)
        households_geo = frames.households.merge(home_geom, on="household_id", how="left")
        households_gdf = gpd.GeoDataFrame(households_geo, geometry="geometry", crs=frames.homes.crs)
        vehicles_gdf = None
        if frames.vehicles is not None:
            veh = _attach_home_geometry_to_vehicles(frames.vehicles, frames.persons, home_geom)
            if veh is not None:
                vehicles_gdf = gpd.GeoDataFrame(veh, geometry="geometry", crs=frames.homes.crs)
        kreis_poly = kreise[["ars5", "geometry"]]
        gem_poly = spatial.load_gemeinden(frames.homes.crs)[["commune_id", "geometry"]]
        geo_paths = GE.write_geo_package(
            out_dir=out, persons=persons_gdf, households=households_gdf,
            vehicles=vehicles_gdf, gemeinde_poly=gem_poly, kreis_poly=kreis_poly,
            deviation_kreis=_deviation_wide(long, "kreis", "ars5"),
            deviation_gemeinde=_deviation_wide(long, "gemeinde", "commune_id"))

    report = {
        "label": label, "source_kind": frames.source_kind,
        "source_path": frames.source_path, "prefix": frames.prefix,
        "git_commit": _git_commit(),
        "n_persons": int(len(frames.persons)),
        "n_households": int(len(frames.households)),
        "n_vehicles": int(len(frames.vehicles)) if frames.vehicles is not None else 0,
        "family_scores": fam.to_dict(orient="records"),
        "quality": quality.to_dict(orient="records"),
        "geo_outputs": {k: str(v) for k, v in geo_paths.items()},
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                     encoding="utf-8")

    md = [f"# Population validation - {label}", "",
          f"- Source: `{frames.source_path}` ({frames.source_kind})",
          f"- Git commit: `{report['git_commit']}`",
          f"- Persons: {report['n_persons']:,} | Households: {report['n_households']:,} "
          f"| Vehicles: {report['n_vehicles']:,}", "",
          "## Headline quality by family", "",
          fam.to_string(index=False) if not fam.empty else "_no targets_", "",
          "## Interpretation", "", _interpretation_markdown(quality), ""]
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    LOGGER.info("Wrote population validation to %s", out)
    return report


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
    run(_parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
