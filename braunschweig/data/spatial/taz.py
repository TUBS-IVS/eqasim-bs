"""synpp data stage: RVB VISUM traffic-analysis zones (Verkehrszellen / TAZ).

Loads the parquet produced by ``scripts/import_rvb_verkehrszellen.py`` and
returns one row per TAZ shaped like the IRIS zone stage
(``eqasim_common.data.spatial.iris``): ``[taz_id, commune_id, kreis,
regiostar7, geometry]`` in EPSG:25832. This is the eqasim IRIS-analog sub-commune
zone source; when the work-location TAZ feature is ON, the gravity distance
matrix + OD are built from these zones instead of the Gemeinde-resolution IRIS
placeholder (later phase).

The underlying VISUM data is LOCAL-ONLY / proprietary: the parquet lives in the
gitignored ``eqasim-data`` tree and is never committed. This stage fails fast if
the parquet is absent or malformed (CLAUDE.md: no silent fallbacks).

Stage name: ``braunschweig.data.spatial.taz``.
"""
from __future__ import annotations

import os

import geopandas as gpd

CRS_METRIC = "EPSG:25832"

REQUIRED_COLUMNS = ["taz_id", "commune_id", "kreis", "regiostar7"]
VALID_RS7 = set(range(71, 78))


def load_taz_zones(path: str) -> gpd.GeoDataFrame:
    """Load + re-validate the imported TAZ parquet. Fails loudly on malformed
    input (the authoritative loader must not propagate bad zones downstream)."""
    gdf = gpd.read_parquet(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_METRIC)
    elif gdf.crs.to_epsg() != 25832:
        gdf = gdf.to_crs(CRS_METRIC)

    missing = [c for c in REQUIRED_COLUMNS if c not in gdf.columns]
    if missing:
        raise ValueError(
            "rvb_verkehrszellen parquet missing columns %s; re-run "
            "scripts/import_rvb_verkehrszellen.py" % missing
        )
    n_dup = int(gdf["taz_id"].duplicated().sum())
    if n_dup:
        raise ValueError(
            "%d duplicate taz_id values; re-run "
            "scripts/import_rvb_verkehrszellen.py" % n_dup
        )
    bad_rs7 = gdf[~gdf["regiostar7"].astype(int).isin(VALID_RS7)]
    if len(bad_rs7):
        raise ValueError(
            "%d rows with regiostar7 outside 71..77: %s; re-run "
            "scripts/import_rvb_verkehrszellen.py"
            % (len(bad_rs7), sorted(bad_rs7["regiostar7"].unique().tolist()))
        )
    if gdf.geometry.is_empty.any() or gdf.geometry.isna().any():
        raise ValueError(
            "empty/missing geometry in rvb_verkehrszellen parquet; re-run "
            "scripts/import_rvb_verkehrszellen.py"
        )
    return gdf
