"""Import the RVB VISUM traffic-analysis zones (Verkehrszellen / TAZ) into the
eqasim-bs data tree.

Source (LOCAL-ONLY, PROPRIETARY -- must NOT be published): the RVB
(Regionalverband Grossraum Braunschweig) VISUM transport-model zoning,
``Verkehrszellen`` layer. One row per traffic-analysis zone (TAZ).

This script reprojects the source from EPSG:32632 (WGS84 / UTM 32N) to the
project CRS EPSG:25832 (ETRS89 / UTM 32N) -- the two UTM32 datums differ ~0.5 m,
so we reproject properly and never assume identity. It renames the source
columns to the project snake_case convention, derives ``commune_id`` (8-digit
AGS) and ``kreis`` (5-digit) from the official municipality key, validates the
schema and value ranges, and writes a parquet plus a provenance README under
``eqasim-data/data/braunschweig/taz/``. Re-running this script is the ONLY
supported way to (re)generate the parquet; hand-editing is prohibited
(CLAUDE.md data provenance).
"""
from __future__ import annotations

import argparse
import os
import sys

import geopandas as gpd

CRS_METRIC = "EPSG:25832"

# Source layer name inside the gpkg.
SOURCE_LAYER = "Verkehrszellen"

# The official municipality key column in the source (7-digit AGS, e.g.
# ``3101000``). commune_id = "0" + this value (8-digit, e.g. ``03101000``).
AGS_COLUMN = "Amtlicher_Gemeindeschluessel"

# Source column -> standardised snake_case column.
COLUMN_RENAME = {
    "Verkehrszelle_Nummer": "taz_id",
    "Verkehrszelle_Name": "taz_name",
    "RegioStaR7_Regionstyp": "regiostar7",
}

# Valid RegioStaR-7 region-type codes (71..77 inclusive).
VALID_RS7 = set(range(71, 78))

DEFAULT_SOURCE = r"T:\bienzeisler\Verkehrsmodell_RVB_Gebietsgliederung.gpkg"


def rename_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy with source columns renamed to the snake_case convention,
    ``commune_id`` / ``kreis`` derived from the official AGS, and geometry
    reprojected to EPSG:25832.

    Missing source columns or an absent source CRS raise (a changed source
    schema or an unprojectable layer must fail loudly, not be silently assumed).
    """
    missing = [c for c in list(COLUMN_RENAME) + [AGS_COLUMN] if c not in gdf.columns]
    if missing:
        raise ValueError(
            "source layer is missing expected columns %s; the upstream schema "
            "changed -- update COLUMN_RENAME/AGS_COLUMN instead of dropping "
            "silently" % missing
        )
    if gdf.crs is None:
        raise ValueError(
            "source layer has no CRS; cannot reproject to %s without assuming "
            "the datum (CLAUDE.md: do not invent data assumptions)" % CRS_METRIC
        )

    out = gdf.rename(columns=COLUMN_RENAME).copy()
    # taz_id and the official key are categorical/string identifiers.
    out["taz_id"] = out["taz_id"].astype(str)
    out["regiostar7"] = out["regiostar7"].astype(int)
    # commune_id = "0" + 7-digit AGS -> 8-digit; kreis = commune_id[:5].
    out["commune_id"] = "0" + gdf[AGS_COLUMN].astype(str).str.strip()
    out["kreis"] = out["commune_id"].str[:5]

    if out.crs.to_epsg() != 25832:
        out = out.to_crs(CRS_METRIC)

    keep = ["taz_id", "taz_name", "commune_id", "kreis", "regiostar7", "geometry"]
    return out[keep]


def validate(gdf: gpd.GeoDataFrame) -> None:
    """Fail loudly on duplicate ids, malformed AGS, out-of-range RS7, or empty
    geometry (no silent repair; CLAUDE.md fallback transparency)."""
    n_dup = int(gdf["taz_id"].duplicated().sum())
    if n_dup:
        raise ValueError("%d duplicate taz_id values in source" % n_dup)
    bad_ags = gdf[gdf["commune_id"].str.len() != 8]
    if len(bad_ags):
        raise ValueError(
            "%d rows with a commune_id that is not 8 digits "
            "(check Amtlicher_Gemeindeschluessel)" % len(bad_ags)
        )
    bad_rs7 = gdf[~gdf["regiostar7"].isin(VALID_RS7)]
    if len(bad_rs7):
        raise ValueError(
            "%d rows with regiostar7 outside 71..77: %s"
            % (len(bad_rs7), sorted(bad_rs7["regiostar7"].unique().tolist()))
        )
    if gdf.geometry.is_empty.any() or gdf.geometry.isna().any():
        raise ValueError("empty/missing geometry in source")
