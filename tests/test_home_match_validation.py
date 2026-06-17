import math

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.analysis import home_match_validation as v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cells(cell_id, *, n_efh_geb=1, n_mfh_geb=1, n_efh_whg=1, n_mfh_whg=4,
                occupied=5.0, vacant=1.0):
    """Return a minimal cells DataFrame for *one* cell."""
    return pd.DataFrame([{
        "ZENSUS100m": cell_id,
        "FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter": float(n_efh_geb),
        "MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter": float(n_mfh_geb),
        "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": float(n_efh_whg),
        "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": float(n_mfh_whg),
        "BewohntWhg_Leerstand_100m_Gitter": occupied,
        "LeerstehendWhg_Leerstand_100m_Gitter": vacant,
        "90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter": float(occupied),
    }])


# ---------------------------------------------------------------------------
# Existing test – must keep passing
# ---------------------------------------------------------------------------

def test_type_match_share_and_assortativity():
    placed = pd.DataFrame({
        "household_id": [1, 2, 3, 4],
        "building_type_3class": ["ein_zweifamilienhaus", "ein_zweifamilienhaus",
                                 "mehrfamilienhaus", "mehrfamilienhaus"],
        "household_size": [5, 4, 1, 2],
        "home_location_id": [10, 11, 20, 21]})
    bld = pd.DataFrame({"building_id": [10, 11, 20, 21],
                        "btype": ["efh_zfh", "efh_zfh", "mfh", "mfh"],
                        "size": [150.0, 130.0, 50.0, 60.0]})
    m = v.home_match_metrics(placed, bld)
    assert m["type_match_share"] == 1.0      # all in matching type
    assert m["size_assortativity"] > 0.0     # bigger HH in bigger dwelling
    assert m["n_households"] == 4


# ---------------------------------------------------------------------------
# test_derive_buildings_btype_reproduces_types
# ---------------------------------------------------------------------------

def test_derive_buildings_btype_reproduces_types():
    """Small EFH + large MFH in one cell: large footprint should be typed mfh."""
    # Cell centroid in EPSG:3035 coordinates.  We place buildings at known 3035
    # points and back out the expected cell id.
    # N=2689100 E=4337000 → cell CRS3035RES100mN2689100E4337000
    cell_id = "CRS3035RES100mN2689100E4337000"

    # Build a GeoDataFrame: one small building (EFH) and one large building (MFH).
    # Centroids in EPSG:3035 (both inside the same 100 m cell).
    pts_3035 = [Point(4337050, 2689150), Point(4337060, 2689130)]
    areas = [80.0, 1000.0]   # small -> EFH, large -> MFH
    bids = [1, 2]

    gdf = gpd.GeoDataFrame(
        {"building_id": bids, "area_m2": areas},
        geometry=pts_3035, crs="EPSG:3035",
    ).to_crs("EPSG:25832")

    cells = _make_cells(cell_id, n_efh_geb=1, n_mfh_geb=1)

    result = v.derive_buildings_btype(gdf, cells, random_seed=42)

    assert len(result) == 2, "should return one row per building"
    assert set(result["building_id"]) == {1, 2}
    assert result["btype"].notna().all(), "all btypes must be non-null"
    assert result["size"].apply(lambda x: isinstance(x, float)).all()

    # The large footprint (building_id=2) must be typed mfh.
    large_btype = result.loc[result["building_id"] == 2, "btype"].iloc[0]
    assert large_btype == "mfh", f"large building should be mfh, got {large_btype!r}"


# ---------------------------------------------------------------------------
# test_home_match_report_metrics
# ---------------------------------------------------------------------------

def test_home_match_report_metrics():
    """Vacancy, overflow_rate, orphan_cells are computed and base metrics present."""
    cell_id = "CRS3035RES100mN2689100E4337000"

    # 4 households, all matched to correct types
    placed = pd.DataFrame({
        "household_id": [1, 2, 3, 4],
        "building_type_3class": ["ein_zweifamilienhaus", "ein_zweifamilienhaus",
                                 "mehrfamilienhaus", "mehrfamilienhaus"],
        "household_size": [5, 4, 1, 2],
        "home_location_id": [10, 11, 20, 21],
    })
    bld = pd.DataFrame({
        "building_id": [10, 11, 20, 21],
        "btype": ["efh_zfh", "efh_zfh", "mfh", "mfh"],
        "size": [150.0, 130.0, 50.0, 60.0],
    })
    # cells: 8 occupied, 2 vacant → vacancy = 2/10 = 0.2
    cells = _make_cells(cell_id, occupied=8.0, vacant=2.0)

    report = v.home_match_report(
        placed, bld, cells,
        n_overcapacity=1,
        n_zero_building_cells=3,
    )

    # Base keys present
    assert "type_match_share" in report
    assert "size_assortativity" in report
    assert "n_households" in report

    # Vacancy
    assert abs(report["realized_vacancy"] - 0.2) < 1e-9, (
        f"expected vacancy 0.2, got {report['realized_vacancy']}"
    )

    # Overflow rate: 1 overcapacity / 4 households = 0.25
    assert abs(report["overflow_rate"] - 0.25) < 1e-9, (
        f"expected overflow 0.25, got {report['overflow_rate']}"
    )

    # Orphan cells passed through
    assert report["orphan_cells"] == 3


def test_home_match_report_missing_vacant_col():
    """When LeerstehendWhg column absent (treated as 0), vacancy = 0.0 (not NaN)."""
    cell_id = "CRS3035RES100mN2689100E4337000"

    placed = pd.DataFrame({
        "household_id": [1],
        "building_type_3class": ["ein_zweifamilienhaus"],
        "household_size": [3],
        "home_location_id": [10],
    })
    bld = pd.DataFrame({"building_id": [10], "btype": ["efh_zfh"], "size": [120.0]})

    # Cells without the vacant column — vacant treated as 0 → vacancy = 0/5 = 0.0
    cells = pd.DataFrame([{
        "ZENSUS100m": cell_id,
        "BewohntWhg_Leerstand_100m_Gitter": 5.0,
    }])

    report = v.home_match_report(placed, bld, cells)
    assert report["realized_vacancy"] == 0.0, (
        f"when vacant column absent, vacancy = 0/occ = 0.0, got {report['realized_vacancy']}"
    )

    # NaN only when BOTH occupied and vacant are 0 (degenerate cell)
    cells_zero = pd.DataFrame([{"ZENSUS100m": cell_id}])
    report_zero = v.home_match_report(placed, bld, cells_zero)
    assert math.isnan(report_zero["realized_vacancy"]), (
        "expected NaN when both occupied and vacant are absent/zero"
    )


# ---------------------------------------------------------------------------
# test_compare_typed_vs_legacy
# ---------------------------------------------------------------------------

def test_compare_typed_vs_legacy():
    """Typed (all matching) should score higher than legacy (half mismatched)."""
    bld = pd.DataFrame({
        "building_id": [10, 11, 20, 21],
        "btype": ["efh_zfh", "efh_zfh", "mfh", "mfh"],
        "size": [150.0, 130.0, 50.0, 60.0],
    })

    # Typed: all 4 households in the correct building type
    placed_typed = pd.DataFrame({
        "household_id": [1, 2, 3, 4],
        "building_type_3class": ["ein_zweifamilienhaus", "ein_zweifamilienhaus",
                                 "mehrfamilienhaus", "mehrfamilienhaus"],
        "household_size": [5, 4, 1, 2],
        "home_location_id": [10, 11, 20, 21],
    })

    # Legacy: first two HH are EFH type but placed in MFH buildings (mismatched),
    # last two are MFH type placed in MFH buildings (matched).
    placed_legacy = pd.DataFrame({
        "household_id": [1, 2, 3, 4],
        "building_type_3class": ["ein_zweifamilienhaus", "ein_zweifamilienhaus",
                                 "mehrfamilienhaus", "mehrfamilienhaus"],
        "household_size": [5, 4, 1, 2],
        "home_location_id": [20, 21, 20, 21],   # EFH HH in MFH buildings
    })

    result = v.compare_typed_vs_legacy(placed_typed, placed_legacy, bld)

    assert "type_match_typed" in result
    assert "type_match_legacy" in result
    assert "delta" in result

    assert result["type_match_typed"] > result["type_match_legacy"], (
        f"typed ({result['type_match_typed']}) should beat legacy "
        f"({result['type_match_legacy']})"
    )
    assert result["delta"] > 0, f"delta should be positive, got {result['delta']}"
    assert abs(result["delta"] - (result["type_match_typed"] - result["type_match_legacy"])) < 1e-12
