"""synpp data stage: RVB VISUM traffic-analysis zones (Verkehrszellen / TAZ).

Loads the parquet produced by ``scripts/import_rvb_verkehrszellen.py`` and
returns one row per TAZ shaped like the IRIS zone stage
(``eqasim_common.data.spatial.iris``): ``[taz_id, commune_id, kreis,
regiostar7, geometry]`` in EPSG:25832. This is the eqasim IRIS-analog sub-commune
zone source; when the ``taz_work_location_choice`` flag is ON, the gravity
distance matrix + OD are built from these zones instead of the
Gemeinde-resolution IRIS placeholder (implemented behind the flag in
``braunschweig.locations.work`` and ``braunschweig.gravity.model``).

The underlying VISUM data is LOCAL-ONLY / proprietary: the parquet lives in the
gitignored ``eqasim-data`` tree and is never committed. This stage fails fast if
the parquet is absent or malformed (CLAUDE.md: no silent fallbacks).

Stage name: ``braunschweig.data.spatial.taz``.
"""
from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd

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
    bad_ags = gdf[gdf["commune_id"].astype(str).str.len() != 8]
    if len(bad_ags):
        raise ValueError(
            "%d rows with a commune_id that is not 8 digits; re-run "
            "scripts/import_rvb_verkehrszellen.py" % len(bad_ags)
        )
    n_dup = int(gdf["taz_id"].duplicated().sum())
    if n_dup:
        raise ValueError(
            "%d duplicate taz_id values; re-run "
            "scripts/import_rvb_verkehrszellen.py" % n_dup
        )
    rs7_numeric = pd.to_numeric(gdf["regiostar7"], errors="coerce")
    bad_rs7 = gdf[rs7_numeric.isna() | ~rs7_numeric.isin(VALID_RS7)]
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


def filter_to_scope(gdf: gpd.GeoDataFrame, kreise) -> gpd.GeoDataFrame:
    """Keep only zones whose 5-digit ``kreis`` is in ``kreise`` (the ZGB political
    scope, ``braunschweig.political_prefix``).

    The imported gpkg spans the wider RVB VISUM Einflussraum (~2118 zones across
    several Bundeslaender); the WORK location choice only needs the ZGB zones
    (~880 across the 8 member Kreise). ``kreise`` None/empty -> no filter (the
    whole imported set is returned, e.g. when the stage is resolved standalone).
    Raises if the filter would keep zero zones (a wrong scope, no silent empty).
    """
    if not kreise:
        return gdf
    scope = {str(k) for k in kreise}
    kept = gdf[gdf["kreis"].astype(str).isin(scope)].copy()
    if len(kept) == 0:
        raise ValueError(
            "TAZ scope filter kept 0 of %d zones for political_prefix %s; "
            "check the Kreis codes" % (len(gdf), sorted(scope))
        )
    return kept


def configure(context):
    context.config("data_path")
    context.config(
        "taz_zones_path",
        "braunschweig/taz/rvb_verkehrszellen_epsg25832.parquet",
    )
    # ZGB scope filter (see filter_to_scope): the imported gpkg spans the wider
    # VISUM Einflussraum; keep only the ZGB Kreise from the political scope.
    # Default None -> no filter (stage resolvable standalone / in Phase-1 tests);
    # the popsim configs set braunschweig.political_prefix to the 8 ZGB Kreise.
    context.config("braunschweig.political_prefix", None)


def execute(context) -> gpd.GeoDataFrame:
    path = os.path.join(context.config("data_path"),
                        context.config("taz_zones_path"))
    gdf = load_taz_zones(path)
    # Cast to str so downstream consumers always see a consistent str dtype
    # regardless of how the parquet was written (integer taz_id is common when
    # the zone IDs happen to be numeric).
    gdf["taz_id"] = gdf["taz_id"].astype(str)
    gdf["kreis"] = gdf["kreis"].astype(str)
    n_total = len(gdf)
    gdf = filter_to_scope(gdf, context.config("braunschweig.political_prefix"))
    per_kreis = gdf["kreis"].value_counts().sort_index()
    print(
        "[braunschweig.data.spatial.taz] %d/%d TAZ in ZGB scope across %d Kreise (%s)"
        % (len(gdf), n_total, len(per_kreis),
           ", ".join("%s=%d" % (k, int(c)) for k, c in per_kreis.items()))
    )
    return gdf[["taz_id", "commune_id", "kreis", "regiostar7", "geometry"]]


def validate(context):
    path = os.path.join(context.config("data_path"),
                        context.config("taz_zones_path"))
    if not os.path.exists(path):
        raise RuntimeError(
            "rvb_verkehrszellen_epsg25832.parquet missing: run "
            "scripts/import_rvb_verkehrszellen.py (local-only proprietary data)"
        )
    return os.path.getsize(path)
