"""Tests for braunschweig.data.external_workplaces.

Covers:
- build_external_workplaces: per-Gemeinde split, column schema, iris_id derivation.
- Kreis below min_flow produces no rows.
- gem_ags derivation from 12-digit ARS (pure logic, no VG250 file required).
"""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from braunschweig.data.external_workplaces import build_external_workplaces


def _g(ars5, ags, ewz, cx):
    return {
        "ars5": ars5,
        "gem_ags": ags,
        "ewz": ewz,
        "geometry": Polygon([(cx, 0), (cx + 1, 0), (cx + 1, 1), (cx, 1)]),
    }


# ---------------------------------------------------------------------------
# build_external_workplaces
# ---------------------------------------------------------------------------

def test_build_external_workplaces_per_gemeinde():
    """Two Gemeinden in one Kreis -> two rows; employees sum to Kreis flow."""
    gem = gpd.GeoDataFrame(
        [_g("03241", "03241001", 60000, 0), _g("03241", "03241002", 40000, 9)],
        crs="EPSG:25832",
    )
    outbound = pd.DataFrame({"ars5": ["03241"], "employees": [1000]})
    df = build_external_workplaces(outbound, gem, min_flow=50)
    assert len(df) == 2
    assert df["employees"].sum() == 1000
    assert df["commune_id"].is_unique
    assert (df["commune_id"].str[3:8] == "03241").all()
    assert (df["iris_id"] == df["commune_id"] + "0000").all()
    assert {"employees", "commune_id", "iris_id", "geometry"} <= set(df.columns)


def test_build_external_workplaces_required_downstream_columns():
    """Columns consumed by locations.work must all be present."""
    gem = gpd.GeoDataFrame(
        [_g("03241", "03241001", 100000, 0)],
        crs="EPSG:25832",
    )
    outbound = pd.DataFrame({"ars5": ["03241"], "employees": [500]})
    df = build_external_workplaces(outbound, gem, min_flow=50)
    required = {"employees", "commune_id", "iris_id", "geometry"}
    assert required <= set(df.columns), f"Missing columns: {required - set(df.columns)}"


def test_build_external_workplaces_kreis_below_min_flow_dropped():
    """A Kreis with outbound < min_flow must produce zero rows."""
    gem = gpd.GeoDataFrame(
        [_g("03241", "03241001", 50000, 0)],
        crs="EPSG:25832",
    )
    outbound = pd.DataFrame({"ars5": ["03241"], "employees": [10]})
    df = build_external_workplaces(outbound, gem, min_flow=50)
    assert len(df) == 0


def test_build_external_workplaces_exactly_at_min_flow_kept():
    """Kreise with outbound == min_flow (boundary, inclusive) are kept."""
    gem = gpd.GeoDataFrame(
        [_g("03241", "03241001", 50000, 0)],
        crs="EPSG:25832",
    )
    outbound = pd.DataFrame({"ars5": ["03241"], "employees": [50]})
    df = build_external_workplaces(outbound, gem, min_flow=50)
    assert len(df) == 1
    assert df["employees"].iloc[0] == 50


def test_build_external_workplaces_two_kreise():
    """Two Kreise above threshold -> rows from both; employees per Kreis preserved."""
    gem = gpd.GeoDataFrame(
        [
            _g("03241", "03241001", 80000, 0),
            _g("03241", "03241002", 20000, 2),
            _g("03153", "03153001", 30000, 50),
        ],
        crs="EPSG:25832",
    )
    outbound = pd.DataFrame(
        {"ars5": ["03241", "03153"], "employees": [1000, 200]}
    )
    df = build_external_workplaces(outbound, gem, min_flow=50)
    assert set(df["ars5"]) == {"03241", "03153"}
    assert df["employees"].sum() == 1200
    # ars5 from commune_id[3:8]
    assert (df["commune_id"].str[3:8] == df["ars5"]).all()


def test_build_external_workplaces_population_split():
    """60/40 population split -> 600/400 employees (largest-remainder exact)."""
    gem = gpd.GeoDataFrame(
        [_g("03241", "03241001", 60000, 0), _g("03241", "03241002", 40000, 9)],
        crs="EPSG:25832",
    )
    outbound = pd.DataFrame({"ars5": ["03241"], "employees": [1000]})
    df = build_external_workplaces(outbound, gem, min_flow=50)
    by_ags = dict(zip(df["gem_ags"], df["employees"]))
    assert by_ags["03241001"] == 600
    assert by_ags["03241002"] == 400


def test_build_external_workplaces_output_geodataframe():
    """Result must be a GeoDataFrame with the Gemeinde CRS."""
    gem = gpd.GeoDataFrame(
        [_g("03241", "03241001", 100000, 0)],
        crs="EPSG:25832",
    )
    outbound = pd.DataFrame({"ars5": ["03241"], "employees": [300]})
    df = build_external_workplaces(outbound, gem, min_flow=50)
    assert isinstance(df, gpd.GeoDataFrame)
    assert df.crs is not None
    assert df.crs == gem.crs


# ---------------------------------------------------------------------------
# gem_ags derivation rule (pure logic; does not require the VG250 file)
# ---------------------------------------------------------------------------

def test_gem_ags_derivation_from_ars12():
    """AGS-8 = ARS[:5] + ARS[9:12] must be 8 chars and Kreis-prefix consistent."""
    # Representative 12-digit ARS examples (Hannover city 03241001):
    #   ARS "032410000001" -> AGS "03241001"
    # Wolfenbuettel:
    #   ARS "031580000001" -> AGS "03158001"
    cases = [
        ("032410000001", "03241001", "03241"),
        ("031580000001", "03158001", "03158"),
        ("031010000001", "03101001", "03101"),
    ]
    for ars12, expected_ags, expected_ars5 in cases:
        ags = ars12[:5] + ars12[9:12]
        assert len(ags) == 8, f"AGS not 8 chars: {ags!r}"
        assert ags[:5] == expected_ars5, f"Kreis prefix mismatch: {ags[:5]!r} != {expected_ars5!r}"
        assert ags == expected_ags, f"Wrong AGS: {ags!r} != {expected_ags!r}"
