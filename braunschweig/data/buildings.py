"""
Braunschweig building registry — drop-in replacement for
``braunschweig.data.buildings``.

Emits the same schema expected by ``bavaria.locations.home``:
    building_id   int
    weight        float    (proxy for dwelling capacity, = footprint area)
    commune_id    str      (AGS, 8-digit)
    iris_id       str      (AGS, 8-digit; ZGB has no IRIS-level split)
    geometry      Point    (building centroid, EPSG:25832)

The stage reads the ALKIS parquet produced by
``scripts/preprocess_alkis_landuse.py`` and retains only polygons whose
AAA Gebäudefunktion maps to ``residential`` (plus ``unknown`` codes that
pass the area filter, as a safety net for settlements where our GFK
mapping is incomplete).  Garages, sheds and other ancillary buildings
(``activity == 'ancillary'``) are explicitly excluded.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd


# Residential GFK codes collapse to these activity labels in the
# preprocessing stage.  'unknown' is a fallback for codes not yet in our
# mapping table — we still include them but only if they pass the area
# filter so pure garages/sheds get excluded even without an explicit code.
RESIDENTIAL_ACTIVITIES = {"residential", "unknown"}

# Minimum / maximum footprint area in m² — same heuristic as the Bavarian
# Hausumringe pipeline.  40 m² eliminates garages/sheds, 400 m² eliminates
# apartment complexes that would otherwise dominate sampling.
AREA_MIN = 40.0
AREA_MAX = 400.0


# Fallback-rate thresholds (CLAUDE.md "Fallback transparency"). Both building
# imputation fallbacks are silent in the legacy code; above these shares a
# ``WARNING`` is emitted so a degraded spatial join / data gap is surfaced
# rather than passing unnoticed.
#
# (a) Commune imputation: the PRIMARY path is the direct VG250 polygon sjoin
#     (``predicate="within"``); the FALLBACK is the building's own ``AGS``
#     attribute, used only when the centroid landed outside every Gemeinde
#     polygon. A high fallback share means the polygon join is broken (CRS
#     mismatch, stale zones, invalid geometries), not just a few topology
#     artefacts on tile borders. 1 % matches the "tiny topology artefacts"
#     intent in the original comment.
COMMUNE_AGS_FALLBACK_WARN_THRESHOLD = 0.01

# (b) Zero-building communes: the PRIMARY path is a Gemeinde that received at
#     least one real building footprint; the FALLBACK is a single zone-centroid
#     placeholder for a Gemeinde with no buildings at all. A high share means
#     whole communes are missing real residential footprints (an ALKIS coverage
#     gap), not just a couple of tiny exclaves. 1 % of required communes.
ZERO_BUILDING_COMMUNE_WARN_THRESHOLD = 0.01


def impute_commune_with_ags_fallback(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Back-fill ``commune_id`` / ``iris_id`` from the ``AGS`` attribute for

    buildings whose centroid missed every Gemeinde polygon in the spatial
    join, and return the PRIMARY / FALLBACK accounting (CLAUDE.md "Fallback
    transparency").

    ``df`` is the post-sjoin frame: ``commune_id`` / ``iris_id`` are NaN for
    buildings that landed outside all polygons, and an ``AGS`` column (if
    present) carries the building's own commune code from the source data.

    Returns ``(df, primary_count, fallback_count)`` where ``primary_count`` is
    the number of buildings that matched a polygon directly and
    ``fallback_count`` is the number back-filled from a non-null ``AGS``.
    Behaviour is byte-identical to the legacy inline block: the only buildings
    whose commune changes are those that were already NaN and receive an AGS
    value; no primary assignment is touched, and rows that miss the join with
    no AGS stay NaN (dropped by the caller's ``notna`` filter).
    """
    df = df.copy()
    for col in ("commune_id", "iris_id"):
        if isinstance(df[col].dtype, pd.CategoricalDtype):
            df[col] = df[col].astype(object)

    missing_before = df["commune_id"].isna()
    primary_count = int((~missing_before).sum())
    fallback_count = 0
    if "AGS" in df.columns:
        missing = df["commune_id"].isna()
        if missing.any():
            fallback_count = int((missing & df["AGS"].notna()).sum())
            df.loc[missing, "commune_id"] = df.loc[missing, "AGS"].astype(str)
            df.loc[missing, "iris_id"] = df.loc[missing, "AGS"].astype(str)

    return df, primary_count, fallback_count


def _log_commune_fallback_rate(primary_count: int, fallback_count: int) -> None:
    """Log the PRIMARY (polygon sjoin) vs FALLBACK (AGS attribute) commune-

    imputation split as an explicit rate (CLAUDE.md "Fallback transparency").

    ``primary_count`` is the number of buildings whose centroid matched a
    Gemeinde polygon directly; ``fallback_count`` is the number back-filled
    from the building's own ``AGS`` attribute because the centroid landed
    outside every polygon. A ``WARNING``-prefixed line is printed when the
    fallback share of all imputed buildings exceeds
    ``COMMUNE_AGS_FALLBACK_WARN_THRESHOLD``.

    Pure logging; this function never changes which commune is assigned.
    """
    total = primary_count + fallback_count
    share = (fallback_count / total) if total else 0.0
    message = (
        "[braunschweig.data.buildings] commune imputation: "
        "{:,} primary (polygon sjoin) / {:,} fallback (AGS attribute) "
        "= {:.2%} fallback".format(primary_count, fallback_count, share)
    )
    if share > COMMUNE_AGS_FALLBACK_WARN_THRESHOLD:
        print(
            "WARNING: " + message
            + " (exceeds {:.0%} threshold; the VG250 polygon join may be "
            "broken rather than dropping a few topology artefacts)".format(
                COMMUNE_AGS_FALLBACK_WARN_THRESHOLD
            )
        )
    else:
        print(message)


def _log_zero_building_commune_rate(real_commune_count: int, placeholder_commune_count: int) -> None:
    """Log the PRIMARY (real buildings) vs FALLBACK (zone-centroid placeholder)

    commune-coverage split as an explicit rate (CLAUDE.md "Fallback
    transparency").

    ``real_commune_count`` is the number of required Gemeinden that received at
    least one real building footprint; ``placeholder_commune_count`` is the
    number filled with a single zone-centroid placeholder because they had no
    buildings at all. A ``WARNING``-prefixed line is printed when the
    placeholder share of all required communes exceeds
    ``ZERO_BUILDING_COMMUNE_WARN_THRESHOLD``.

    Pure logging; this function never changes any geometry or value.
    """
    total = real_commune_count + placeholder_commune_count
    share = (placeholder_commune_count / total) if total else 0.0
    message = (
        "[braunschweig.data.buildings] commune coverage: "
        "{:,} with real buildings / {:,} zero-building centroid placeholders "
        "= {:.2%} placeholder".format(
            real_commune_count, placeholder_commune_count, share
        )
    )
    if share > ZERO_BUILDING_COMMUNE_WARN_THRESHOLD:
        print(
            "WARNING: " + message
            + " (exceeds {:.0%} threshold; whole communes lack real "
            "residential footprints, an ALKIS coverage gap rather than a "
            "few tiny exclaves)".format(ZERO_BUILDING_COMMUNE_WARN_THRESHOLD)
        )
    else:
        print(message)


def configure(context):
    context.stage("braunschweig.data.alkis")
    context.stage("eqasim_common.data.spatial.iris")


def execute(context) -> gpd.GeoDataFrame:
    df_alkis = context.stage("braunschweig.data.alkis")
    df_zones = context.stage("eqasim_common.data.spatial.iris")

    # Ensure aligned CRS (both should be UTM32N/EPSG:25832).
    if df_zones.crs != df_alkis.crs:
        df_zones = df_zones.to_crs(df_alkis.crs)

    df = df_alkis.copy()
    df["activity"] = df["activity"].astype(str)

    # 1) Keep residential & unknown activities, drop garages & other
    #    ancillary footprints outright.
    df = df[df["activity"].isin(RESIDENTIAL_ACTIVITIES)].copy()

    # 2) Area filter (40–400 m²).  ``area_m2`` already exists from the
    #    preprocessing step; recompute if missing as a defensive fallback.
    if "area_m2" not in df.columns:
        df["area_m2"] = df.geometry.area.astype("float32")
    df = df[(df["area_m2"] >= AREA_MIN) & (df["area_m2"] < AREA_MAX)].copy()

    print(
        "[braunschweig.data.buildings] {:,} candidate dwellings after GFK+area filter".format(len(df))
    )

    # 3) Weight by area, take centroid.
    df["weight"] = df["area_m2"].astype(float)
    df["geometry"] = df.geometry.centroid
    df["building_id"] = np.arange(len(df))

    # 4) Impute commune_id / iris_id via spatial join to VG250 Gemeinden.
    df = gpd.sjoin(
        df,
        df_zones[["geometry", "commune_id", "iris_id"]],
        how="left",
        predicate="within",
    ).reset_index(drop=True).drop(columns=["index_right"])

    # Fallback: for buildings whose centroid landed outside any Gemeinde
    # polygon (tiny topology artefacts) use the AGS attribute directly.
    # PRIMARY = direct polygon sjoin match; FALLBACK = AGS-attribute back-fill.
    df, primary_count, fallback_count = impute_commune_with_ags_fallback(df)
    _log_commune_fallback_rate(primary_count, fallback_count)

    df = df[df["commune_id"].notna()].copy()

    df_combined = df[["building_id", "weight", "commune_id", "iris_id", "geometry"]]
    df_combined = gpd.GeoDataFrame(df_combined, crs=df.crs)

    # 5) Fill Gemeinden that ended up with zero buildings (should be rare
    #    but happens for tiny exclaves) via the zone centroid.
    required_zones = set(df_zones["commune_id"].unique())
    available_zones = set(df_combined["commune_id"].unique())
    missing_zones = required_zones - available_zones

    # PRIMARY = communes with at least one real building footprint;
    # FALLBACK = communes filled with a single zone-centroid placeholder.
    _log_zero_building_commune_rate(
        real_commune_count=len(required_zones & available_zones),
        placeholder_commune_count=len(missing_zones),
    )

    if missing_zones:
        print(
            "[braunschweig.data.buildings] filling {} commune(s) with "
            "zone centroids".format(len(missing_zones))
        )
        df_missing = df_zones[df_zones["commune_id"].isin(missing_zones)][
            ["commune_id", "iris_id", "geometry"]
        ].copy()
        df_missing["geometry"] = df_missing["geometry"].centroid
        df_missing["building_id"] = np.arange(len(df_missing)) + len(df_combined)
        df_missing["weight"] = 1.0
        df_combined = gpd.GeoDataFrame(
            pd.concat([df_combined, df_missing], ignore_index=True),
            crs=df_combined.crs,
        )

    print(
        "[braunschweig.data.buildings] returning {:,} buildings across {} communes".format(
            len(df_combined), df_combined["commune_id"].nunique()
        )
    )
    return df_combined


def validate(context):
    return 0
