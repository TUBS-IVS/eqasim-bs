"""CLI entry point: PopulationSim-style control validation + quality assessment
+ geo-exploration export for one synthetic population (run output or synpp cache).

Usage (PowerShell, conda env eqasim):
    $env:PYTHONUTF8=1; python -m braunschweig.analysis.population_validation.run_population_validation `
        --run-output-dir eqasim-data/output_bs_25pct --label 25pct

Note: controls whose attribute column is absent from the chosen source are skipped
(logged as WARNING), not silently ignored. The eqasim run-output CSVs carry a
narrower person/household schema than the synpp cache; in particular `license_type`
may be absent from a run-output dir (the licence control then falls back to the
boolean `has_driving_license`), so that control validates only when one of the two
columns is present (e.g. when run against `--sim-cache` or a richer output). This is
expected and logged. The attributes `economic_status`, `housing_tenure` and income
are NOT validated as controls (no hard Kreis/Gemeinde target exists for them); they
are exported spatially via `geo_export` instead.
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
    trip_coherence as TC,
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

    # Optimization step (2): trip-coherence check against MiD W1 (purpose) and
    # P36_1 (mobility), segmented by the matching anchors. Only runs when the
    # source carries donor activity chains (<prefix>trips.csv). A failure here is
    # logged loudly but does not abort the control validation above.
    trip_json = None
    if frames.trips is not None:
        try:
            # Merge household_size onto persons so the trip-coherence breakdown can
            # segment by it (it lives on the households frame, not on persons).
            persons_for_tc = frames.persons
            if ("household_size" not in persons_for_tc.columns
                    and "household_size" in frames.households.columns):
                persons_for_tc = persons_for_tc.merge(
                    frames.households[["household_id", "household_size"]],
                    on="household_id", how="left")
            tc = TC.build_trip_coherence_report(persons_for_tc, frames.trips, DATA_PATH)
            tc["mobility_by_segment"].to_csv(
                out / "trip_coherence_mobility_by_segment.csv", index=False)
            pur = tc["purpose"]
            pd.DataFrame({
                "purpose": list(pur["target"]),
                "realized_share": [pur["realized"].get(p) for p in pur["target"]],
                "target_share": [pur["target"][p] for p in pur["target"]],
                "abs_delta_pp": [pur["abs_delta_pp"][p] for p in pur["target"]],
            }).to_csv(out / "trip_coherence_purpose.csv", index=False)
            tc["work_participation_by_segment"].to_csv(
                out / "trip_coherence_work_participation.csv", index=False)
            tc["trips_per_person_by_segment"].to_csv(
                out / "trip_coherence_trips_per_person.csv", index=False)
            # W12 mean trip length per purpose: synthetic mean routed km
            # (detour-inflated straight-line) vs MiD W12 mittel_km, four scored
            # purposes. None when the trips frame carries no distance column.
            length = tc.get("length")
            if length is not None:
                pd.DataFrame(length).to_csv(
                    out / "trip_coherence_length.csv", index=False)
            trip_json = {
                "n_trips": tc["n_trips"],
                "mobility": tc["mobility"],
                "purpose": {k: v for k, v in pur.items()},
                "length": length,
                "differentiation": tc["differentiation"],
                "mobility_by_segment": tc["mobility_by_segment"].to_dict(orient="records"),
                "work_participation_by_segment":
                    tc["work_participation_by_segment"].to_dict(orient="records"),
                "trips_per_person_by_segment":
                    tc["trips_per_person_by_segment"].to_dict(orient="records"),
            }
            LOGGER.info(
                "Trip coherence: mobility %.1f%% (P36_1 %.1f%%, |d| %.1f pp); "
                "purpose SRMSE vs W1 %.3f",
                100 * tc["mobility"]["overall_rate"],
                100 * tc["mobility"]["target_rate"],
                100 * tc["mobility"]["abs_delta"], tc["purpose"]["srmse"])
            if length is not None:
                LOGGER.info(
                    "[trip-coherence] W12 mean trip length per purpose "
                    "(synthetic routed km vs MiD): %s",
                    ", ".join(f"{r['purpose']} {r['realised_km']:.1f}/"
                              f"{r['target_km']:.1f} (d {r['delta_km']:+.1f}km)"
                              for r in length))
        except Exception:
            LOGGER.exception(
                "Trip-coherence check failed; continuing without it.")
            trip_json = None
    else:
        LOGGER.info(
            "No %strips.csv at the source; trip-coherence check skipped "
            "(it needs donor activity chains).", frames.prefix)

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

        # Per-Kreis trip coherence (realised vs MiD W1/P36 + signed deltas), merged
        # onto the kreis_aggregat GPKG layer so the purpose/mobility gaps are
        # mappable next to the demographic deviations. Needs the home ars5 (on
        # persons_geo) and the donor trips; skipped loudly if either is absent.
        trip_coherence_kreis = None
        if frames.trips is not None:
            try:
                trip_coherence_kreis = TC.trip_coherence_by_kreis(
                    persons_geo[["person_id", "ars5"]], frames.trips, DATA_PATH)
                trip_coherence_kreis.to_csv(
                    out / "trip_coherence_by_kreis.csv", index=False)
            except Exception:
                LOGGER.exception(
                    "Per-Kreis trip coherence failed; GPKG kreis layer without it.")
                trip_coherence_kreis = None

        geo_paths = GE.write_geo_package(
            out_dir=out, persons=persons_gdf, households=households_gdf,
            vehicles=vehicles_gdf, gemeinde_poly=gem_poly, kreis_poly=kreis_poly,
            deviation_kreis=_deviation_wide(long, "kreis", "ars5"),
            deviation_gemeinde=_deviation_wide(long, "gemeinde", "commune_id"),
            trip_coherence_kreis=trip_coherence_kreis)

    report = {
        "label": label, "source_kind": frames.source_kind,
        "source_path": frames.source_path, "prefix": frames.prefix,
        "git_commit": _git_commit(),
        "n_persons": int(len(frames.persons)),
        "n_households": int(len(frames.households)),
        "n_vehicles": int(len(frames.vehicles)) if frames.vehicles is not None else 0,
        "family_scores": fam.to_dict(orient="records"),
        "quality": quality.to_dict(orient="records"),
        "trip_coherence": trip_json,
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
    if trip_json is not None:
        m = trip_json["mobility"]
        md += ["## Trip coherence (donor activity chains vs MiD)", "",
               f"- Mobility rate: {100 * m['overall_rate']:.1f}% "
               f"(MiD P36_1 {100 * m['target_rate']:.1f}%, "
               f"|delta| {100 * m['abs_delta']:.1f} pp)",
               f"- Purpose distribution SRMSE vs MiD W1 (4 scored purposes): "
               f"{trip_json['purpose']['srmse']:.3f}",
               f"- [KPI] work-trip participation gap employed - not-employed: "
               f"{trip_json['differentiation']['work_share_employed_gap_pp']:.1f} pp "
               f"(higher = matching gives employed persons commute diaries)", "",
               "Scored purpose shares (realised vs W1, re-normalised over the four "
               "unambiguous purposes):", ""]
        pur = trip_json["purpose"]
        for p in pur["target"]:
            md.append(f"  - {p}: {100 * pur['realized'].get(p, float('nan')):.1f}% vs "
                      f"{100 * pur['target'][p]:.1f}% "
                      f"(|delta| {pur['abs_delta_pp'][p]:.1f} pp)")
        length = trip_json.get("length")
        if length is not None:
            md += ["", "Mean trip length per purpose (synthetic routed km vs MiD "
                   "W12 mittel_km; synthetic straight-line x 1.3 detour factor, "
                   "four scored purposes work/education/shop/leisure):", ""]
            for r in length:
                md.append(f"  - {r['purpose']}: {r['realised_km']:.1f} km vs "
                          f"{r['target_km']:.1f} km "
                          f"(delta {r['delta_km']:+.1f} km, {100 * r['rel_delta']:+.1f}%)")
        md += ["", "_Modal split is not shown: the synthesis trips.csv carries no "
               "transport mode (written only by the MATSim mode-choice run), and "
               "donor-inherited modes would be French-biased - see step 3 (MiD "
               "donor) in the matching-optimization concept._", ""]
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
