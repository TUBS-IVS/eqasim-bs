"""Fallback-transparency tests for ``braunschweig.data.buildings``.

The building registry has two silent fallbacks (CLAUDE.md "Fallback
transparency"):

(a) Commune imputation -- a building whose centroid misses every Gemeinde
    polygon in the VG250 spatial join is back-filled from its own ``AGS``
    attribute. PRIMARY = direct polygon match, FALLBACK = AGS back-fill.

(b) Zero-building communes -- a Gemeinde that received no real building
    footprint is filled with a single zone-centroid placeholder.
    PRIMARY = communes with real buildings, FALLBACK = centroid placeholder.

These tests exercise the pure accounting / logging helpers with small
synthetic GeoDataFrames (no real shapefile IO) and assert that:

  * the PRIMARY / FALLBACK counts are correct for the three required cases
    (all-inside -> 0 fallback, one-outside-with-AGS -> 1 fallback, a
    zero-building Gemeinde -> 1 placeholder),
  * the imputation is OUTPUT-PRESERVING -- a building that matched a polygon
    keeps its commune, and only NaN rows with an AGS are changed,
  * the explicit rate is logged, with a ``WARNING: `` prefix above threshold.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
from shapely.geometry import Point

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from braunschweig.data import buildings  # noqa: E402


# --------------------------------------------------------------------------- #
# (a) Commune imputation: PRIMARY (polygon sjoin) vs FALLBACK (AGS attribute)
# --------------------------------------------------------------------------- #

def _post_sjoin_frame(commune_ids, ags_values):
    """Build a synthetic post-sjoin frame.

    ``commune_ids`` is the result of the polygon join (``None`` = the centroid
    landed outside every Gemeinde polygon); ``ags_values`` is the building's
    own AGS attribute (``None`` = no AGS available).
    """
    n = len(commune_ids)
    return pd.DataFrame(
        {
            "building_id": np.arange(n),
            "commune_id": pd.array(commune_ids, dtype="object"),
            "iris_id": pd.array(commune_ids, dtype="object"),
            "AGS": pd.array(ags_values, dtype="object"),
        }
    )


def test_all_buildings_inside_polygons_zero_ags_fallback():
    # Every building matched a Gemeinde polygon directly -> no AGS back-fill.
    df = _post_sjoin_frame(
        commune_ids=["03101000", "03102000", "03101000"],
        ags_values=["03101000", "03102000", "03101000"],
    )
    out, primary, fallback = buildings.impute_commune_with_ags_fallback(df)

    assert primary == 3
    assert fallback == 0
    # Output-preserving: no commune changed by the (unused) fallback.
    assert list(out["commune_id"]) == ["03101000", "03102000", "03101000"]


# Production-path zone lookup: AGS8 -> (commune_id ARS-12, iris_id ARS-12+"0000").
_AGS_TO_ZONE = {
    "03101000": ("031010000000", "0310100000000000"),
    "03154999": ("031549990000", "0315499900000000"),
}


def test_building_outside_polygons_with_ags_maps_to_zone_vocabulary():
    # The middle building missed the join but carries an AGS -> 1 fallback,
    # mapped to the ARS-12 commune_id / iris_id vocabulary (NOT the raw AGS).
    df = _post_sjoin_frame(
        commune_ids=["031010000000", None, "031010000000"],
        ags_values=["03101000", "03154999", "03101000"],
    )
    out, primary, fallback = buildings.impute_commune_with_ags_fallback(
        df, ags_to_zone=_AGS_TO_ZONE
    )

    assert primary == 2
    assert fallback == 1
    # The NaN row was back-filled in the ZONE vocabulary, primary rows untouched.
    assert list(out["commune_id"]) == ["031010000000", "031549990000", "031010000000"]
    assert list(out["iris_id"]) == ["031010000000", "0315499900000000", "031010000000"]


def test_building_ags_not_in_zone_lookup_is_dropped():
    # An AGS with no matching zone (e.g. an out-of-scope / stale code) must
    # stay NaN so the caller's notna() filter drops it -- never a dead-weight
    # building in the wrong vocabulary.
    df = _post_sjoin_frame(
        commune_ids=["031010000000", None],
        ags_values=["03101000", "09999999"],  # 09999999 not in the lookup
    )
    out, primary, fallback = buildings.impute_commune_with_ags_fallback(
        df, ags_to_zone=_AGS_TO_ZONE
    )

    assert primary == 1
    assert fallback == 0
    assert pd.isna(out["commune_id"].iloc[1])


def test_building_outside_polygons_without_ags_stays_nan():
    # Missed the join AND no AGS -> stays NaN (dropped by the caller), NOT the
    # literal string "None" (the fixed legacy bug).
    df = _post_sjoin_frame(
        commune_ids=["031010000000", None],
        ags_values=["03101000", None],
    )
    out, primary, fallback = buildings.impute_commune_with_ags_fallback(
        df, ags_to_zone=_AGS_TO_ZONE
    )

    assert primary == 1
    assert fallback == 0
    assert pd.isna(out["commune_id"].iloc[1])


def test_categorical_commune_column_is_handled():
    # The real sjoin yields categorical commune/iris columns; the helper must
    # cast them to object before injecting the mapped zone value.
    df = _post_sjoin_frame(
        commune_ids=["031010000000", None],
        ags_values=["03101000", "03154999"],
    )
    df["commune_id"] = df["commune_id"].astype("category")
    df["iris_id"] = df["iris_id"].astype("category")

    out, primary, fallback = buildings.impute_commune_with_ags_fallback(
        df, ags_to_zone=_AGS_TO_ZONE
    )

    assert primary == 1
    assert fallback == 1
    assert list(out["commune_id"]) == ["031010000000", "031549990000"]


def test_commune_fallback_rate_logged_without_warning(capsys):
    # Below-threshold fallback share -> plain info line, no WARNING prefix.
    buildings._log_commune_fallback_rate(primary_count=1000, fallback_count=2)
    out = capsys.readouterr().out
    assert "commune imputation" in out
    assert "1,000 primary" in out
    assert "2 fallback" in out
    assert "WARNING: " not in out


def test_commune_fallback_rate_warns_above_threshold(capsys):
    # 20 % fallback share is well above the 1 % threshold -> WARNING prefix.
    buildings._log_commune_fallback_rate(primary_count=80, fallback_count=20)
    out = capsys.readouterr().out
    assert out.startswith("WARNING: ")
    assert "20.00% fallback" in out


# --------------------------------------------------------------------------- #
# (b) Zero-building communes: PRIMARY (real buildings) vs FALLBACK (centroid)
# --------------------------------------------------------------------------- #

def test_zero_building_commune_counts_match_required_zones():
    # Three required Gemeinden; one has no building -> 1 centroid placeholder.
    df_buildings = pd.DataFrame({"commune_id": ["03101000", "03101000", "03102000"]})
    df_zones = pd.DataFrame({"commune_id": ["03101000", "03102000", "03103000"]})

    required = set(df_zones["commune_id"].unique())
    available = set(df_buildings["commune_id"].unique())
    missing = required - available

    real = len(required & available)
    placeholder = len(missing)

    assert real == 2
    assert placeholder == 1
    assert missing == {"03103000"}


def test_zero_building_commune_rate_logged_without_warning(capsys):
    buildings._log_zero_building_commune_rate(
        real_commune_count=200, placeholder_commune_count=1
    )
    out = capsys.readouterr().out
    assert "commune coverage" in out
    assert "200 with real buildings" in out
    assert "1 zero-building centroid placeholders" in out
    assert "WARNING: " not in out


def test_zero_building_commune_rate_warns_above_threshold(capsys):
    # 10 % placeholder share is above the 1 % threshold -> WARNING prefix.
    buildings._log_zero_building_commune_rate(
        real_commune_count=90, placeholder_commune_count=10
    )
    out = capsys.readouterr().out
    assert out.startswith("WARNING: ")
    assert "10.00% placeholder" in out


def test_zero_building_rate_no_division_by_zero(capsys):
    # Degenerate empty-input case must not raise.
    buildings._log_zero_building_commune_rate(0, 0)
    out = capsys.readouterr().out
    assert "0.00% placeholder" in out
    assert "WARNING: " not in out


# --------------------------------------------------------------------------- #
# End-to-end sanity on the synthetic placeholder geometry (no real IO)
# --------------------------------------------------------------------------- #

def test_centroid_placeholder_geometry_is_a_point():
    # Mirrors step 5 of execute(): a zero-building Gemeinde gets its zone
    # centroid as the placeholder geometry. Pure geometry check, no real data.
    import geopandas as gpd
    from shapely.geometry import Polygon

    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    df_missing = gpd.GeoDataFrame(
        {"commune_id": ["03103000"], "iris_id": ["03103000"]},
        geometry=[poly],
        crs="EPSG:25832",
    )
    df_missing["geometry"] = df_missing["geometry"].centroid

    assert isinstance(df_missing.geometry.iloc[0], Point)
    assert df_missing.geometry.iloc[0].equals(Point(5, 5))
