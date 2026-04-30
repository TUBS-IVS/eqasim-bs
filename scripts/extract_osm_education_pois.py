"""
Cross-check helper for TASK-004 of
``plan/feature-education-gravity-bs-1.md``.

The main pipeline already extracts education POIs as part of
``scripts/preprocess_osm_pois.py`` (location_type == "education"
covering OSM tags ``amenity in {school, university, kindergarten}``
and ``building in {school, university, kindergarten}``). This helper
re-emits that subset as a standalone GeoPackage so it can be opened
directly in QGIS for visual cross-checks against LSN/DESTATIS
capacity counts.

It is *not* used by the synpp DAG and produces no model input.

Usage::

    python scripts/extract_osm_education_pois.py \
        --pois eqasim-data/data/braunschweig/preprocessed/osm_pois.parquet \
        --out  eqasim-data/data/braunschweig/osm/osm_education_pois.gpkg
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pois", required=True, type=Path,
                        help="Path to osm_pois.parquet emitted by "
                             "scripts/preprocess_osm_pois.py")
    parser.add_argument("--out", required=True, type=Path,
                        help="Output GeoPackage path")
    args = parser.parse_args()

    if not args.pois.exists():
        print(f"[osm-education] input parquet missing: {args.pois}",
              file=sys.stderr)
        return 2

    gdf = gpd.read_parquet(args.pois)
    education = gdf[gdf["location_type"] == "education"].copy()

    if education.empty:
        print("[osm-education] no education POIs found in input",
              file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    education.to_file(args.out, driver="GPKG")
    print(f"[osm-education] wrote {len(education)} POIs to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
