"""Wolfsburg area zones on the finished zone release (parking cost zones v3, spec B2, issue #436).

Not part of the pipeline and never run by synpp. ``assemble_parking_zones.py --municipal-dir`` carves the areas with a
recorded maximum stay out of the Wolfsburg tariff zones in the full zone chain (``wolfsburg_area_zones.carve_area_zones``).
The OSM-derived car network and the municipality polygons that the full chain needs are not on every machine (the station
step of spec Amendment G1 met the same limit), so this step applies the SAME derivation to the FINISHED release, like
``station_lots.py``:

1. the three tariff zones are re-derived from the pinned package WITHOUT the carving, through the same precedence and
   opening as the chain (``assemble_parking_zones.wolfsburg_zone_records``), and must equal the committed polygons up to the
   rounding of the zone file; else the step stops (the release was not built from this package or this code);
2. the areas are carved out (``wolfsburg_zone_records`` with the carving), precedence and opening run on the Wolfsburg records
   alone; that is the chain's result only if no committed zone of another municipality interacts with them, which is checked
   (a committed zone within the cut clearance of a Wolfsburg zone stops the step: re-run the full chain);
3. the three tariff zone features are replaced and the area zones inserted before them (the order of the chain); every other
   feature is carried over unchanged (read back and compared). The licence and attribution members name the new source.

``update_qa_table`` rewrites the twelve ``wob_tarifzone_*`` rows of the municipal QA table (their release polygons changed) and
inserts the two rows per area zone after the last row of the municipal step, which is where a full chain run writes them.
CRS: EPSG:25832 throughout, areas in m2. Command (the zone file must be named parking_zones_2026.geojson, like the
station step: the GeoJSON 'name' member follows the file name)::

    python scripts/curation/parking_zones_2026/wolfsburg_area_release.py \\
        --package-dir <main checkout>/eqasim-data/data/braunschweig/parking/raw_sources/municipal_2026-10-01 \\
        --raw-overpass <main checkout>/eqasim-data/data/braunschweig/parking/raw_overpass \\
        --zones <release before this step> \\
        --zones-out <data directory>/braunschweig/parking/parking_zones_2026.geojson \\
        --tariffs eqasim-data/data/braunschweig/parking/parking_tariffs_2026.csv \\
        --municipal-qa eqasim-data/data/braunschweig/parking/parking_zones_2026_municipal_qa.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.ops import unary_union

import assemble_parking_zones as ap
import curation_common as cc
import municipal_zones as mz
import regional_zones as rz
import station_lots as sl
import wolfsburg_area_zones as waz
from braunschweig.parking import municipal_zone_qa as mq
from braunschweig.parking import zones as pz

ZONES_FILE_NAME = "parking_zones_2026.geojson"
#: Largest symmetric difference, m2, between a committed tariff zone and its re-derivation after the rounding of the file
#: (WGS84 with 7 decimals, about 1 cm): float noise of the transformation only.
REPRODUCTION_TOLERANCE_M2 = 0.01
#: The last row of the municipal step in the QA table; the area rows follow it in a full chain run (before the regional rows).
LAST_MUNICIPAL_QA_ROW = f"{mz.GOSLAR_ZONE}_vs_{mz.FACILITY_FEE_STATUSES[-1]}_facility_areas"


def rounded_like_the_zone_file(geometry):
    """``geometry`` (EPSG:25832) as the zone file stores it: WGS84 with 7 decimals, read back to EPSG:25832."""
    wgs84 = gpd.GeoSeries([geometry], crs=cc.METRIC_CRS).to_crs("EPSG:4326").iloc[0]
    rounded = shapely.transform(wgs84, lambda coordinates: np.round(coordinates, 7))
    return gpd.GeoSeries([rounded], crs="EPSG:4326").to_crs(cc.METRIC_CRS).iloc[0]


def _finished_wolfsburg_records(inputs: dict, *, carve: bool) -> dict:
    """The Wolfsburg records through the chain's precedence and opening; {"records", "built", "trims"}."""
    built = ap.wolfsburg_zone_records(inputs, carve=carve)
    records, trims = ap.apply_precedence(built["area_zones"] + built["tariff_zones"])
    ap.finish_wolfsburg_zones(records, built["wolfsburg"], built["areas"])
    return {"records": records, "built": built, "trims": trims}


def check_committed_tariff_zones(inputs: dict, committed: dict) -> None:
    """The committed polygons of the Wolfsburg tariff zones equal their re-derivation WITHOUT the carving (module docstring,
    step 1), up to ``REPRODUCTION_TOLERANCE_M2`` after the rounding of the zone file; else ``SystemExit`` naming the zones."""
    derived = _finished_wolfsburg_records(inputs, carve=False)
    problems = []
    for record in derived["records"]:
        zone_id = record["zone_id"]
        if zone_id not in committed:
            problems.append(f"{zone_id} is missing in the release")
            continue
        difference = float(rounded_like_the_zone_file(record["geometry"]).symmetric_difference(committed[zone_id]).area)
        if difference > REPRODUCTION_TOLERANCE_M2:
            problems.append(f"{zone_id} differs by {difference:.3f} m2 from the tariff zone the package gives")
    if problems:
        raise SystemExit("the release does not hold what the pinned Wolfsburg package and this code give for the tariff "
                         "zones (" + "; ".join(problems) + "): this step needs the release the chain wrote before spec v3 B2")
    print("committed Wolfsburg tariff zones re-derived from the package without the carving: "
          + ", ".join(record["zone_id"] for record in derived["records"]) + f" (symmetric difference <= "
          f"{REPRODUCTION_TOLERANCE_M2:g} m2 after the rounding of the zone file)")


def carve_release(inputs: dict, zones_path, out_path, *, regional: bool = True, station: bool = True) -> dict:
    """Carve the Wolfsburg area zones out of the finished release at ``zones_path`` and write ``out_path`` (the layout and
    licence members of ``assemble_parking_zones.write_zone_file`` with ``regional``/``station`` as the release was built).

    ``inputs`` are the verified packages (``municipal_zones.load_packages``). Returns {"records" (the new Wolfsburg records,
    area zones first), "context" (``wolfsburg``, ``wolfsburg_areas``, ``simplify_m``, ``clearance_m``, ``inputs``, for
    ``update_qa_table``), "release" (the written file loaded, EPSG:25832)}. Raises ``SystemExit`` when the release has area
    zones already, does not hold the package's tariff zones, or has a zone within the cut clearance of a Wolfsburg zone.
    """
    release = sl.read_release(zones_path)
    ids = [str(zone_id) for zone_id in release["zone_id"]]
    present = [zone_id for zone_id in ids if zone_id.startswith(mz.WOLFSBURG_AREA_ZONE_ID_PREFIX)]
    if present:
        raise SystemExit(f"the release has the area zones {present} already; the step needs the release before spec v3 B2 "
                         "(it replaces the tariff zones and never re-applies itself)")
    committed = dict(zip(ids, release.geometry))
    check_committed_tariff_zones(inputs, committed)
    derived = _finished_wolfsburg_records(inputs, carve=True)
    records, built = derived["records"], derived["built"]
    tariff_ids = [record["zone_id"] for record in built["tariff_zones"]]
    outside = [zone_id for zone_id in ids if zone_id not in tariff_ids]
    wolfsburg_union = unary_union([record["geometry"] for record in records])
    touched = [zone_id for zone_id in outside
               if committed[zone_id].distance(wolfsburg_union) <= ap.EROSION_CUT_CLEARANCE_M]
    if touched:
        raise SystemExit(f"the committed zone(s) {touched} lie within {ap.EROSION_CUT_CLEARANCE_M:g} m of a Wolfsburg zone: the "
                         "precedence of the full chain would cut them against each other, which this step does not do; "
                         "re-run the full chain (assemble_parking_zones.py) instead")
    first = ids.index(tariff_ids[0])
    new_frame = ap.zone_frame(records).reindex(columns=list(release.columns))
    kept_before, kept_after = release.iloc[:first], release[~release["zone_id"].isin(tariff_ids)].iloc[first:]
    combined = gpd.GeoDataFrame(pd.concat([kept_before, new_frame, kept_after], ignore_index=True), geometry="geometry",
                                crs=cc.METRIC_CRS)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    ap.write_zone_file(combined, out_path, municipal=True, regional=regional, station=station)
    written = pz.load_zone_polygons(out_path, max_repairs=0)
    carried = [zone_id for zone_id in ids if zone_id not in tariff_ids]
    after = dict(zip(written["zone_id"].astype(str), written.geometry))
    changed = [zone_id for zone_id in carried
               if after[zone_id].symmetric_difference(committed[zone_id]).area > REPRODUCTION_TOLERANCE_M2]
    if changed:
        raise SystemExit(f"zones outside Wolfsburg changed in the written file: {changed}")
    areas = built["areas"]["areas"]
    print(f"Wolfsburg area zones carved out of the finished release: {len(areas)} zones, {len(carried)} other zones carried over "
          f"unchanged, committed Wolfsburg tariff zones replaced: {', '.join(tariff_ids)}; precedence trims among the "
          f"Wolfsburg zones (m2): {derived['trims']}; written {out_path} with {len(written)} zones")
    context = {"wolfsburg": built["wolfsburg"], "wolfsburg_areas": built["areas"], "simplify_m": ap.SIMPLIFY_M,
               "clearance_m": ap.EROSION_CUT_CLEARANCE_M, "inputs": inputs}
    return {"records": records, "context": context, "release": written}


def update_qa_table(qa_path, context: dict, release: gpd.GeoDataFrame, v1_wolfsburg, intro: str = mz.QA_INTRO) -> list:
    """Rewrite the municipal QA table at ``qa_path`` after ``carve_release``: the ``wob_tarifzone_*`` rows are recomputed
    against the written ``release`` (EPSG:25832) and ``v1_wolfsburg`` (the v1 polygon of wob_innenstadt,
    ``assemble_parking_zones.wolfsburg_v1_geometry``) and replace the old rows in place; the two rows per area zone
    (``wolfsburg_area_zones.qa_rows``) follow the last row of the municipal step. ``intro`` is the header text. Returns the
    new rows; ``SystemExit`` when the table has area rows already or lacks a Wolfsburg row."""
    table = mq.load_municipal_qa(qa_path)
    polygons = dict(zip(release["zone_id"].astype(str), release.geometry))
    complete = dict(context, v1={mz.WOLFSBURG_V1_ZONE: v1_wolfsburg})
    replaced = {row["row_id"]: row for row in mz.wolfsburg_qa_rows(complete, polygons)}
    area_rows = waz.qa_rows(complete, release)
    existing = table.to_dict("records")
    clash = sorted({row["row_id"] for row in area_rows} & {row["row_id"] for row in existing})
    if clash:
        raise SystemExit(f"{qa_path}: the QA rows {clash} exist already; the step needs the table before spec v3 B2")
    missing = sorted(set(replaced) - {row["row_id"] for row in existing})
    if missing:
        raise SystemExit(f"{qa_path}: the Wolfsburg QA rows {missing} are missing")
    rows = [dict(replaced.get(row["row_id"], row)) for row in existing]
    anchor = [row["row_id"] for row in rows].index(LAST_MUNICIPAL_QA_ROW) if LAST_MUNICIPAL_QA_ROW in {
        row["row_id"] for row in rows} else None
    if anchor is None:
        raise SystemExit(f"{qa_path}: the last row of the municipal step, {LAST_MUNICIPAL_QA_ROW}, is missing")
    rows = rows[:anchor + 1] + area_rows + rows[anchor + 1:]
    mz.write_qa_table(qa_path, rows, intro)
    return area_rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--package-dir", required=True, help="raw_sources/municipal_2026-10-01 (SHA-256 checked)")
    parser.add_argument("--raw-overpass", required=True, help="raw_overpass with the Wolfsburg response (the v1 polygon of "
                                                              "the QA rows)")
    parser.add_argument("--zones", required=True, help="the finished zone release before spec v3 B2 (read)")
    parser.add_argument("--zones-out", required=True, help="the zone release with the area zones to write")
    parser.add_argument("--tariffs", required=True, help="parking_tariffs_2026.csv: the area rows are written into it")
    parser.add_argument("--municipal-qa", required=True, help="parking_zones_2026_municipal_qa.csv: rows are rewritten")
    args = parser.parse_args(argv)
    if Path(args.zones_out).name != ZONES_FILE_NAME:
        raise SystemExit(f"--zones-out must be named {ZONES_FILE_NAME}: the 'name' member of the GeoJSON follows the file "
                         f"name (got {Path(args.zones_out).name})")
    inputs = mz.load_packages(args.package_dir)
    built = carve_release(inputs, args.zones, args.zones_out)
    tariffs = pd.read_csv(args.tariffs, comment="#", dtype=str, keep_default_na=False)
    areas = built["context"]["wolfsburg_areas"]["areas"]
    rows = waz.area_tariff_rows(tariffs[~tariffs["zone_id"].str.startswith(mz.WOLFSBURG_AREA_ZONE_ID_PREFIX)], areas,
                                package_sha256=inputs["files"]["Wolfsburg_Parkdaten"]["sha256"])
    waz.write_area_rows(args.tariffs, rows)
    waz.check_area_tariff_rows(areas, pz.load_tariffs(args.tariffs))
    v1, _ = ap.wolfsburg_v1_geometry(cc.overpass_response(args.raw_overpass, "03103000"))
    intro = sl.qa_intro(rz.qa_intro(mz.QA_INTRO))
    update_qa_table(args.municipal_qa, built["context"], built["release"], v1, intro)
    print("Wolfsburg area zones done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
