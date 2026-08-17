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

import os

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
INCLUDE_LARGE_BUILDINGS = True   # the matcher uses capacity, so big MFH blocks are kept


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


def filter_residential_buildings(df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep only residential (and unknown) activity buildings above AREA_MIN.

    Extracted from ``execute()`` so it can be unit-tested in isolation.
    The upper 400 m² cap has been removed — large MFH footprints are kept
    because the matcher uses per-building capacity, not flat sampling.
    """
    df = df.copy()
    df["activity"] = df["activity"].astype(str)
    df = df[df["activity"].isin(RESIDENTIAL_ACTIVITIES)].copy()
    if "area_m2" not in df.columns:
        df["area_m2"] = df.geometry.area.astype("float32")
    df = df[df["area_m2"] >= AREA_MIN].copy()   # lower cap only; large blocks kept
    return df


def impute_commune_with_ags_fallback(
    df: pd.DataFrame,
    ags_to_zone: dict[str, tuple[str, str]] | None = None,
) -> tuple[pd.DataFrame, int, int]:
    """Back-fill ``commune_id`` / ``iris_id`` from the ``AGS`` attribute for

    buildings whose centroid missed every Gemeinde polygon in the spatial
    join, and return the PRIMARY / FALLBACK accounting (CLAUDE.md "Fallback
    transparency").

    ``df`` is the post-sjoin frame: ``commune_id`` / ``iris_id`` are NaN for
    buildings that landed outside all polygons, and an ``AGS`` column (if
    present) carries the building's own 8-digit commune code from the source
    data.

    ``ags_to_zone`` maps the building's 8-digit ``AGS`` to the zone system's
    ``(commune_id, iris_id)`` (12-digit ARS and ARS+"0000"). It MUST be supplied
    on the production path: the building AGS is in a different vocabulary than
    the ARS-keyed zone system, so writing the raw AGS would make every rescued
    building unmatchable downstream. When omitted (legacy callers/tests that
    already pass AGS in the commune vocabulary) the raw AGS is written, but a
    NaN AGS is left NaN (dropped) rather than stringified to "None".

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
            ags = df.loc[missing, "AGS"].astype(str)
            if ags_to_zone is not None:
                # Correct-vocabulary fallback: the building AGS is the 8-digit
                # AGS, but the zone system keys on the 12-digit ARS commune_id
                # (and ARS+"0000" iris_id). Writing the raw AGS here would make
                # every rescued building unmatchable downstream (dead weight in
                # the commune fallback pool). Map AGS -> (commune_id, iris_id)
                # through the zone lookup; an AGS (or NaN) not in the lookup
                # stays NaN and is dropped by the caller's notna() filter.
                mapped_commune = ags.map(lambda a: ags_to_zone.get(a, (None, None))[0])
                mapped_iris = ags.map(lambda a: ags_to_zone.get(a, (None, None))[1])
                fallback_count = int(mapped_commune.notna().sum())
                n_unmappable = int(mapped_commune.isna().sum())
                if n_unmappable:
                    print(
                        f"[braunschweig.data.buildings] {n_unmappable} building(s) "
                        f"missed the polygon join and have an AGS with no matching "
                        f"zone (e.g. {sorted(ags[mapped_commune.isna()].unique())[:5]}) "
                        f"-> dropped."
                    )
                df.loc[missing, "commune_id"] = mapped_commune
                df.loc[missing, "iris_id"] = mapped_iris
            else:
                # Legacy path (no lookup supplied): retained only for callers
                # that pass an AGS already in the commune_id vocabulary. A NaN
                # AGS stays NaN (not the literal string "None") so it is dropped
                # by the caller's notna() filter, as the docstring promises.
                valid = df.loc[missing, "AGS"].notna()
                fallback_count = int(valid.sum())
                fill_idx = df.loc[missing].index[valid.to_numpy()]
                df.loc[fill_idx, "commune_id"] = df.loc[fill_idx, "AGS"].astype(str)
                df.loc[fill_idx, "iris_id"] = df.loc[fill_idx, "AGS"].astype(str)

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


def join_lod2_heights(buildings, heights):
    """LEFT-join LoD2 height onto footprints by OI. Non-destructive: every footprint
    and every existing column is kept; only `height_m` is added (NaN where no LoD2 match)."""
    if "OI" not in buildings.columns:
        out = buildings.copy(); out["height_m"] = float("nan"); return out
    h = heights[["OI", "height_m"]].drop_duplicates("OI")
    out = buildings.merge(h, on="OI", how="left")
    cov = out["height_m"].notna().mean() if len(out) else 0.0
    print(f"[braunschweig.data.buildings] LoD2 height coverage: {cov:.1%} "
          f"({int(out['height_m'].notna().sum())}/{len(out)} footprints)")
    return out


def configure(context):
    context.stage("braunschweig.data.alkis")
    context.stage("eqasim_common.data.spatial.iris")
    # synpp scopes config per stage: execute() may only read options THIS stage's
    # configure() declared. execute() reads data_path (LoD2 heights path), so declare
    # it here (issue #229 class: "Config option data_path is not requested"; latent
    # while this stage was cache-hit, surfaced on a fresh matsim.output run).
    context.config("data_path")


def execute(context) -> gpd.GeoDataFrame:
    df_alkis = context.stage("braunschweig.data.alkis")
    df_zones = context.stage("eqasim_common.data.spatial.iris")

    # Ensure aligned CRS (both should be UTM32N/EPSG:25832).
    if df_zones.crs != df_alkis.crs:
        df_zones = df_zones.to_crs(df_alkis.crs)

    # 1+2) Keep residential/unknown activities, lower area cap only (no upper cap).
    df = filter_residential_buildings(df_alkis)

    print(
        "[braunschweig.data.buildings] {:,} candidate dwellings after GFK+area filter".format(len(df))
    )

    # 3) Weight by area; store the original footprint polygon BEFORE replacing
    #    the active geometry with the centroid. This allows downstream stages
    #    (e.g. assign_homes_typed) to use intersection-based cell membership so
    #    that footprints straddling a 100 m cell boundary are not false orphans.
    df["weight"] = df["area_m2"].astype(float)
    df["footprint"] = df.geometry  # original polygon in EPSG:25832
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
    # polygon (tiny topology artefacts) map the 8-digit AGS attribute back to
    # the zone system's 12-digit ARS commune_id / iris_id. The building AGS and
    # the zone commune_id are DIFFERENT vocabularies, so the mapping is built
    # from df_zones (AGS8 = ARS[:5] + ARS[9:12], the codes.py rule) rather than
    # writing the raw AGS (which would make rescued buildings unmatchable).
    # PRIMARY = direct polygon sjoin match; FALLBACK = AGS-attribute back-fill.
    zone_commune = df_zones["commune_id"].astype(str)
    zone_iris = df_zones["iris_id"].astype(str)
    ags_to_zone = {
        cid[:5] + cid[9:12]: (cid, iid)
        for cid, iid in zip(zone_commune, zone_iris)
    }
    df, primary_count, fallback_count = impute_commune_with_ags_fallback(
        df, ags_to_zone=ags_to_zone
    )
    _log_commune_fallback_rate(primary_count, fallback_count)

    df = df[df["commune_id"].notna()].copy()

    keep_cols = ["building_id", "weight", "area_m2", "commune_id", "iris_id", "geometry", "footprint"]
    if "OI" in df.columns:
        keep_cols = ["building_id", "weight", "area_m2", "OI", "commune_id", "iris_id", "geometry", "footprint"]
    df_combined = df[keep_cols]
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
        df_missing["area_m2"] = 1.0  # placeholder; no real footprint (zone-centroid fallback)
        # For zone-centroid placeholders, footprint = the point itself (no polygon
        # available). This ensures the column has no None holes in df_combined.
        df_missing["footprint"] = df_missing["geometry"]
        df_combined = gpd.GeoDataFrame(
            pd.concat([df_combined, df_missing], ignore_index=True),
            crs=df_combined.crs,
        )

    print(
        "[braunschweig.data.buildings] returning {:,} buildings across {} communes".format(
            len(df_combined), df_combined["commune_id"].nunique()
        )
    )

    heights_path = os.path.join(context.config("data_path"),
                                "braunschweig/preprocessed/lod2_heights.parquet")
    if os.path.exists(heights_path):
        df_combined = join_lod2_heights(df_combined, pd.read_parquet(heights_path))
    else:
        df_combined["height_m"] = float("nan")

    return df_combined


def validate(context):
    return 0
