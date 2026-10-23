"""Derive the parking-lot release ``parking_lots_osm_2026`` from the pinned OSM extract (issue #465, ADR-0165).

The large-site access step of ``matsim.simulation.prepare`` (eqasim-java-bs ``RunAdaptLargeFacilityAccess``) spreads
the car access of large work and university sites over the parking lots around them, weighted by their spaces. This
curation script writes those lots, region-wide, from the same pinned Geofabrik extract the parking module uses
(``niedersachsen-260929.osm.pbf``, data record ``parking_zones_2026``), checked against its Geofabrik MD5 and
its pinned SHA-256:

* every ``amenity=parking`` area (GDAL layer ``multipolygons``) and node (layer ``points``) inside ``--bbox``;
  ``amenity=parking`` lines are not read (an unclosed parking way has no area and is street parking in practice);
* off-street lots of ANY ``access`` value are kept: OSM does not say whom a lot serves, and the employee lots of the
  large plants carry ``access=no``, ``private`` or no access tag; street-side parking
  (``supply_share.is_street_side_object``) is dropped;
* spaces by the parking module's capacity rule B2 (``supply_share.lot_capacity``): the ``capacity`` tag, else the
  lot area / 25 m2 (ASSUMPTION B-d) times the tagged levels of a multi-storey car park; rounded to whole spaces, lots
  with fewer than one space (nodes without a capacity tag) are dropped;
* coordinates: the centroid of the area, the node itself, in EPSG:25832.

Output in ``--out-dir``: ``parking_lots_osm_2026.csv`` (semicolon separated, sorted by ``lot_id``; columns ``lot_id``
(``way/<id>``, ``relation/<id>``, ``node/<id>``), ``x``, ``y``, ``spaces``, ``capacity_source`` (``tag``, ``area``,
``area_levels``), ``access``, ``parking``, ``name``) and ``parking_lots_osm_2026.json`` (extract digests, OSM snapshot,
box, layer filters, counts and the share of spaces resting on the area heuristic). OpenStreetMap data, ODbL 1.0.

Usage:
    python scripts/build_parking_lots_from_osm.py \
        --extract eqasim-data/data/braunschweig/parking/raw_osm/niedersachsen-260929.osm.pbf \
        --bbox 51.52,9.819,52.927,11.34 --out-dir eqasim-data/data/braunschweig/parking

(the box of the committed release: the node extent of the prepared #465 1 % network grown by 2 km, data record
parking_lots_osm_2026).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from braunschweig.parking import supply_share  # noqa: E402

RELEASE_STEM = "parking_lots_osm_2026"
#: SHA-256 of the pinned Geofabrik extract niedersachsen-260929.osm.pbf (data record parking_zones_2026).
PINNED_EXTRACT_SHA256 = "c2b33b8414584686f9320971300de17f66ba66f92b45b23b24ad0a51b0218a94"
METRIC_CRS = "EPSG:25832"
COLUMNS = ("lot_id", "x", "y", "spaces", "capacity_source", "access", "parking", "name")
#: GDAL OSM driver layers and attribute filters: ``amenity`` is a column of the multipolygons layer and sits in
#: ``other_tags`` for points (default osmconf.ini).
LAYER_FILTERS = {
    "points": "other_tags LIKE '%\"amenity\"=>\"parking\"%'",
    "multipolygons": "amenity = 'parking'",
}

log = logging.getLogger("parking-lots")


def lots_from_objects(objects: gpd.GeoDataFrame) -> pd.DataFrame:
    """The release rows of ``objects`` (EPSG:25832; ``osm_type``, ``osm_id``, ``tags`` as dicts, geometry).

    Keeps ``amenity=parking`` objects that are no street-side parking and carry at least one space after rounding;
    logs how many were dropped for each reason and the share of the kept spaces that rests on the area heuristic.
    """
    if objects.crs is None or objects.crs.to_epsg() != 25832:
        raise ValueError(f"parking objects must be in {METRIC_CRS}, got {objects.crs}")
    rows = []
    dropped = {"not_parking": 0, "street_side": 0, "no_space": 0}
    for osm_type, osm_id, tags, geometry in zip(objects["osm_type"], objects["osm_id"], objects["tags"],
                                                objects.geometry):
        tags = dict(tags or {})
        if tags.get("amenity") != "parking":
            dropped["not_parking"] += 1
            continue
        if supply_share.is_street_side_object(tags):
            dropped["street_side"] += 1
            continue
        spaces, source = supply_share.lot_capacity(geometry, tags)
        spaces = int(round(spaces))
        if spaces < 1:
            dropped["no_space"] += 1
            continue
        centre = geometry.centroid
        rows.append({"lot_id": f"{osm_type}/{int(osm_id)}", "x": round(float(centre.x), 2),
                     "y": round(float(centre.y), 2), "spaces": spaces, "capacity_source": source,
                     "access": str(tags.get("access", "")), "parking": str(tags.get("parking", "")),
                     "name": str(tags.get("name", ""))})
    lots = pd.DataFrame(rows, columns=list(COLUMNS)).sort_values("lot_id", kind="stable").reset_index(drop=True)
    lots["spaces"] = lots["spaces"].astype("int64")
    total = int(lots["spaces"].sum())
    heuristic = int(lots.loc[lots["capacity_source"] != "tag", "spaces"].sum())
    log.info("[parking-lots] %d objects: kept %d lots with %d spaces; dropped not parking %d, street-side %d, "
             "without a space %d; spaces on the area heuristic (ASSUMPTION B-d) %d (%.1f %%)", len(objects), len(lots),
             total, dropped["not_parking"], dropped["street_side"], dropped["no_space"], heuristic,
             100.0 * heuristic / total if total else 0.0)
    if total and heuristic / total > supply_share.HEURISTIC_CAPACITY_WARNING_SHARE:
        log.warning("[parking-lots] %.1f %% of the spaces rest on the area heuristic (ASSUMPTION B-d, %.0f m2 per "
                    "space), above the parking module's reporting threshold of %.0f %%: most OSM lots carry no "
                    "capacity tag", 100.0 * heuristic / total, supply_share.LOT_M2_PER_SPACE,
                    100.0 * supply_share.HEURISTIC_CAPACITY_WARNING_SHARE)
    return lots


def check_extract_sha256(digests: dict, expected: str) -> None:
    """SystemExit when the extract's SHA-256 (``digests`` of the MD5 check) is not the pinned one: another extract
    would give another release under the same name."""
    if digests["sha256"] != expected:
        raise SystemExit(f"{digests['file']}: SHA-256 {digests['sha256']} is not the pinned {expected}; "
                         "use the pinned extract or pass --expected-sha256 for a deliberate new release")


def _zones_script():
    """The parking-zone curation script, whose extract checks and OSM readers this script reuses."""
    path = Path(__file__).resolve().parent / "build_parking_zones_from_osm.py"
    spec = importlib.util.spec_from_file_location("build_parking_zones_from_osm", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_parking_objects(extract: Path, bbox, zones) -> tuple:
    """(EPSG:25832 frame of the ``amenity=parking`` points and areas of ``bbox``, {layer: read seconds, count})."""
    import tempfile

    import numpy as np
    import pyogrio

    south, west, north, east = bbox
    frames, report = [], {}
    with tempfile.TemporaryDirectory(prefix="gdal_osm_") as scratch:
        pyogrio.set_gdal_config_options({"CPL_TMPDIR": scratch,
                                         "OSM_MAX_TMPFILE_SIZE": str(int(zones.DEFAULT_OSM_MAX_TMPFILE_MB))})
        try:
            for layer, where in LAYER_FILTERS.items():
                started = time.perf_counter()
                frame = pyogrio.read_dataframe(str(extract), layer=layer, bbox=(west, south, east, north), where=where)
                if layer == "multipolygons":
                    relation = frame["osm_id"].notna().to_numpy()
                    osm_type = np.where(relation, "relation", "way")
                    osm_id = np.where(relation, frame["osm_id"], frame["osm_way_id"])
                else:
                    osm_type, osm_id = "node", frame["osm_id"]
                frames.append(gpd.GeoDataFrame({"osm_type": osm_type,
                                                "osm_id": pd.Series(osm_id).astype("int64").to_numpy(),
                                                "tags": zones._layer_tags(frame)},
                                               geometry=frame.geometry.values, crs=frame.crs or "EPSG:4326"))
                report[layer] = {"features": len(frame), "seconds": round(time.perf_counter() - started, 1)}
                log.info("[parking-lots] read layer %s of %s: %d features in %.1f s", layer, extract.name,
                         len(frame), report[layer]["seconds"])
        finally:
            pyogrio.set_gdal_config_options({"CPL_TMPDIR": None, "OSM_MAX_TMPFILE_SIZE": None})
    objects = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=frames[0].crs)
    return objects.to_crs(METRIC_CRS), report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--extract", required=True, type=Path, help="the pinned Geofabrik extract (.osm.pbf)")
    parser.add_argument("--md5", type=Path, default=None, help="its Geofabrik .md5 file (default <extract>.md5)")
    parser.add_argument("--bbox", required=True, help="south,west,north,east in WGS84 degrees")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--expected-sha256", default=PINNED_EXTRACT_SHA256,
                        help="SHA-256 the extract must have (default: the pinned niedersachsen-260929)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    zones = _zones_script()
    bbox = zones.parse_bbox(args.bbox)
    csv_path = args.out_dir / f"{RELEASE_STEM}.csv"
    meta_path = args.out_dir / f"{RELEASE_STEM}.json"
    for path in (csv_path, meta_path):
        if path.exists() and not args.overwrite:
            raise SystemExit(f"{path} exists; pass --overwrite to replace it")
    digests = zones.check_extract_md5(args.extract, args.md5)
    check_extract_sha256(digests, args.expected_sha256)
    header = zones.pbf_header(args.extract)

    objects, layers = read_parking_objects(args.extract, bbox, zones)
    lots = lots_from_objects(objects)
    if lots.empty:
        raise SystemExit(f"no parking lot found in box {args.bbox} of {args.extract}; wrong box or extract")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    lots.to_csv(csv_path, sep=";", index=False, lineterminator="\n", float_format="%.2f")
    by_source = lots.groupby("capacity_source")["spaces"].sum().astype(int).to_dict()
    meta = {
        "dataset": RELEASE_STEM,
        "script": "scripts/build_parking_lots_from_osm.py",
        "extract": digests,
        "osm_timestamp": header.get("osmosis_replication_timestamp"),
        "bbox_south_west_north_east": list(bbox),
        "layer_filters": LAYER_FILTERS,
        "layers": layers,
        "objects": int(len(objects)),
        "lots": int(len(lots)),
        "spaces": int(lots["spaces"].sum()),
        "spaces_by_capacity_source": {key: int(value) for key, value in sorted(by_source.items())},
        "crs": METRIC_CRS,
        "licence": "OpenStreetMap contributors, ODbL 1.0",
    }
    meta_path.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    log.info("[parking-lots] wrote %s (%d lots, %d spaces) and %s", csv_path, len(lots), meta["spaces"], meta_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
