"""Import the building-level activity potentials gpkg into the eqasim-bs data tree.

Source (local-only): the redistributed building potentials produced by
``https://github.com/TUBS-IVS/Activities-and-Potentials-Calculation-Pipeline``
from OSM + ALKIS. One row per building with a per-activity-type potential.

This script renames the source columns to the project snake_case convention,
validates the schema and value ranges, and writes a parquet plus a provenance
README under ``eqasim-data/data/braunschweig/buildings/``. Re-running this script
is the ONLY supported way to (re)generate the parquet; hand-editing values is
prohibited (CLAUDE.md data provenance).
"""
from __future__ import annotations

import argparse
import os
import sys

import geopandas as gpd

CRS_METRIC = "EPSG:25832"

# Source gpkg column -> standardised snake_case column.
COLUMN_RENAME = {
    "building_index": "building_id",
    "assigned_Workers": "potential_work",
    "assigned_School": "potential_school",
    "assigned_University": "potential_university",
    "assigned_Kindergarten": "potential_kindergarten",
    "assigned_Leisure": "potential_leisure",
    "assigned_Retail_Daily": "potential_retail_daily",
    "assigned_Retail_Non-Daily": "potential_retail_non_daily",
    "potentials": "potential_generic",
}

# The standardised per-activity potential columns (all float >= 0).
POTENTIAL_COLUMNS = [
    "potential_work", "potential_school", "potential_university",
    "potential_kindergarten", "potential_leisure",
    "potential_retail_daily", "potential_retail_non_daily",
    "potential_generic",
]

# Provenance metadata columns kept for traceability.
KEEP_METADATA = ["gml_id", "bosserhof_class_clean", "volume_m3", "target_taz"]

DEFAULT_SOURCE = r"T:\bienzeisler\VISUM-quality-analysis\building_level_redistributed_values_with_fallback.gpkg"
SOURCE_REPO = "https://github.com/TUBS-IVS/Activities-and-Potentials-Calculation-Pipeline"


def rename_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy with source columns renamed to the snake_case convention.

    ``building_id`` is reset to a contiguous int. Missing source columns raise
    (a changed source schema must fail loudly, not silently drop a potential).
    """
    missing = [c for c in COLUMN_RENAME if c not in gdf.columns]
    if missing:
        raise ValueError(
            "source gpkg is missing expected columns %s; the upstream schema "
            "changed -- update COLUMN_RENAME instead of dropping silently" % missing
        )
    out = gdf.rename(columns=COLUMN_RENAME).copy()
    out["building_id"] = range(len(out))
    if out.crs is None:
        out = out.set_crs(CRS_METRIC)
    elif out.crs.to_epsg() != 25832:
        out = out.to_crs(CRS_METRIC)
    keep = ["building_id"] + POTENTIAL_COLUMNS + \
        [c for c in KEEP_METADATA if c in out.columns] + ["geometry"]
    return out[keep]


def validate(gdf: gpd.GeoDataFrame) -> None:
    """Fail loudly on negative potentials or empty geometry (no silent repair)."""
    for col in POTENTIAL_COLUMNS:
        neg = int((gdf[col] < 0).sum())
        if neg:
            raise ValueError("%d negative values in %s" % (neg, col))
    if gdf.geometry.is_empty.any() or gdf.geometry.isna().any():
        raise ValueError("empty/missing geometry in source")


def write_readme(path: str, source: str, n_rows: int) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "# Building-level activity potentials\n\n"
            "One row per building with a redistributed potential per activity "
            "type, computed from OSM + ALKIS.\n\n"
            "## Provenance\n\n"
            "- Producer pipeline: %s\n"
            "- Source file (local-only): `%s`\n"
            "- Rows: %d. CRS: EPSG:25832.\n\n"
            "## Regenerate\n\n"
            "```powershell\n"
            "python scripts/import_building_activity_potentials.py --source <gpkg>\n"
            "```\n\n"
            "## Columns\n\n"
            "`building_id` (int key); per-activity potentials "
            "`potential_work|school|university|kindergarten|leisure|"
            "retail_daily|retail_non_daily|generic` (float >= 0); provenance "
            "`gml_id, bosserhof_class_clean, volume_m3, target_taz`; `geometry` "
            "(footprint polygon, EPSG:25832).\n"
            % (SOURCE_REPO, source, n_rows)
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument(
        "--out-dir",
        default=os.path.join("eqasim-data", "data", "braunschweig", "buildings"),
    )
    args = parser.parse_args(argv)

    if not os.path.exists(args.source):
        print("ERROR: source gpkg not found: %s" % args.source, file=sys.stderr)
        return 2

    gdf = gpd.read_file(args.source)
    out = rename_columns(gdf)
    validate(out)

    os.makedirs(args.out_dir, exist_ok=True)
    parquet_path = os.path.join(args.out_dir, "building_activity_potentials.parquet")
    out.to_parquet(parquet_path)
    write_readme(os.path.join(args.out_dir, "README.md"), args.source, len(out))
    print(
        "[import_building_activity_potentials] wrote %d buildings -> %s"
        % (len(out), parquet_path)
    )
    for col in POTENTIAL_COLUMNS:
        print("  %-26s sum=%14.1f nonzero=%d"
              % (col, float(out[col].sum()), int((out[col] > 0).sum())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
