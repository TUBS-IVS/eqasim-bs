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


def test_derive_buildings_btype_uses_height_in_no_census_signal_cell():
    """No census building-type counts + a tall LoD2 footprint -> the derived ground-truth
    types it mfh, MIRRORING the matcher (which also height-types no-signal cells).
    Regression: derive_buildings_btype must carry height_m through to assign_building_types;
    if it strips it, the cell is forced all-EFH and the metric unfairly penalises the
    matcher's height-based MFH placements."""
    cell_id = "CRS3035RES100mN2689100E4337000"
    pts_3035 = [Point(4337050, 2689150), Point(4337060, 2689130)]
    gdf = gpd.GeoDataFrame(
        {"building_id": [1, 2], "area_m2": [100.0, 100.0], "height_m": [3.0, 30.0]},
        geometry=pts_3035, crs="EPSG:3035",
    ).to_crs("EPSG:25832")
    # no-signal cell: zero building-type counts (suppression)
    cells = _make_cells(cell_id, n_efh_geb=0, n_mfh_geb=0)
    result = v.derive_buildings_btype(gdf, cells, random_seed=42)
    btype = result.set_index("building_id")["btype"]
    assert btype[2] == "mfh"      # 30 m (~10 floors) footprint -> MFH via height
    assert btype[1] == "efh_zfh"  # 3 m (~1 floor) -> EFH


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

def test_size_assortativity_non_nan_with_varying_sizes():
    """size_assortativity must be finite (not nan) and positive when larger
    households are in larger dwellings, even when the cells frame has no
    size-bin histogram columns (size_hist is empty → degenerate slot sizes).

    This exercises the area_m2 fallback in derive_buildings_btype so that
    cross-building size variation is always present.
    """
    cell_id = "CRS3035RES100mN2689100E4337000"

    # Four buildings: two small EFH (area 80 m², 100 m²) + two large MFH (300 m², 600 m²)
    # Centroids in EPSG:3035 inside the cell (N=2689100..2689200, E=4337000..4337100)
    pts_3035 = [
        Point(4337020, 2689150),
        Point(4337040, 2689130),
        Point(4337060, 2689170),
        Point(4337080, 2689140),
    ]
    bids   = [1, 2, 3, 4]
    areas  = [80.0, 100.0, 300.0, 600.0]

    gdf = gpd.GeoDataFrame(
        {"building_id": bids, "area_m2": areas},
        geometry=pts_3035, crs="EPSG:3035",
    ).to_crs("EPSG:25832")

    # Cells WITHOUT size-bin histogram columns → size_hist will be empty → slot sizes = 0
    cells = pd.DataFrame([{
        "ZENSUS100m": cell_id,
        "FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter": 2.0,
        "MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter": 2.0,
        "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 2.0,
        "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 4.0,
        "BewohntWhg_Leerstand_100m_Gitter": 6.0,
        # deliberately NO size-bin columns (e.g. "90bis99_Flaeche_der_Wohnung_...")
    }])

    bld_btype = v.derive_buildings_btype(gdf, cells, random_seed=0)
    assert bld_btype["size"].nunique() > 1, (
        "buildings must have varying sizes (area_m2 fallback); got constant size — "
        f"unique values: {bld_btype['size'].unique()}"
    )

    # Six households: larger ones paired with EFH (bigger area) → positive assortativity
    # EFH buildings (bids 1, 2 — small area) and MFH (bids 3, 4 — large area)
    # Match: big HH (size 4,5) in MFH (bigger area), small HH (size 1,2) in EFH (smaller area)
    placed = pd.DataFrame({
        "household_id": [10, 11, 12, 13, 14, 15],
        "building_type_3class": [
            "mehrfamilienhaus", "mehrfamilienhaus", "mehrfamilienhaus",
            "ein_zweifamilienhaus", "ein_zweifamilienhaus", "ein_zweifamilienhaus",
        ],
        "household_size": [4, 5, 3, 1, 2, 1],
        # Large-area buildings (3,4) → big HH; small-area buildings (1,2) → small HH
        "home_location_id": [3, 4, 3, 1, 2, 1],
    })

    metrics = v.home_match_metrics(placed, bld_btype)
    assert math.isfinite(metrics["size_assortativity"]), (
        f"size_assortativity must be finite (not nan/inf), got {metrics['size_assortativity']}"
    )
    assert metrics["size_assortativity"] > 0, (
        f"size_assortativity should be positive (bigger HH in bigger buildings), "
        f"got {metrics['size_assortativity']}"
    )


def test_size_assortativity_ignores_nan_sizes():
    """size_assortativity must be finite and > 0 when some buildings have size=NaN
    but the finite rows have a monotone household_size <-> size relationship.
    This is the regression test for the bug where spearmanr over NaN-containing
    arrays returned nan even with real variation in the finite rows."""
    n = 15
    n_nan = 3
    n_finite = n - n_nan

    # Finite rows: strictly monotone (hh_size 1..12 paired with size 100..1200)
    hh_sizes = list(range(1, n_finite + 1)) + [1] * n_nan
    sizes = [float(s * 100) for s in range(1, n_finite + 1)] + [np.nan] * n_nan
    building_ids = list(range(n))

    placed = pd.DataFrame({
        "household_id": list(range(n)),
        "home_location_id": building_ids,
        "building_type_3class": ["ein_zweifamilienhaus"] * n,
        "household_size": hh_sizes,
    })
    buildings_btype = pd.DataFrame({
        "building_id": building_ids,
        "btype": ["efh_zfh"] * n,
        "size": sizes,
    })

    m = v.home_match_metrics(placed, buildings_btype)

    assert math.isfinite(m["size_assortativity"]), (
        f"Expected finite size_assortativity when NaN rows present but finite rows vary; "
        f"got {m['size_assortativity']}"
    )
    assert m["size_assortativity"] > 0, (
        f"Expected positive correlation (monotone finite data), got {m['size_assortativity']}"
    )


def test_size_assortativity_nan_when_all_sizes_nan():
    """size_assortativity must be nan when ALL buildings have size=NaN (genuinely degenerate)."""
    placed = pd.DataFrame({
        "household_id": [0, 1, 2],
        "home_location_id": [0, 1, 2],
        "building_type_3class": ["ein_zweifamilienhaus"] * 3,
        "household_size": [1, 2, 3],
    })
    buildings_btype = pd.DataFrame({
        "building_id": [0, 1, 2],
        "btype": ["efh_zfh"] * 3,
        "size": [np.nan, np.nan, np.nan],
    })

    m = v.home_match_metrics(placed, buildings_btype)
    assert not math.isfinite(m["size_assortativity"]), (
        f"Expected nan when all sizes are NaN, got {m['size_assortativity']}"
    )


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


def test_home_match_metrics_raises_on_zero_overlap_id_space():
    """Zero overlap between home_location_id and building_id must raise.

    The legacy home draw emits home_location_id as a positional 0-based index
    (home_cell._legacy_capped_buildings renumbers building_id to arange), NOT a
    real building_id. Joining such a frame against a real-building_id btype table
    matches nothing; the previous code silently returned a nan type_match_share.
    That silent-garbage metric is now a loud failure (audit FRAGILE item).
    """
    bld = pd.DataFrame({
        "building_id": [10, 11, 20, 21],
        "btype": ["efh_zfh", "efh_zfh", "mfh", "mfh"],
        "size": [150.0, 130.0, 50.0, 60.0],
    })
    placed_legacy = pd.DataFrame({
        "household_id": [1, 2, 3, 4],
        "building_type_3class": ["ein_zweifamilienhaus", "ein_zweifamilienhaus",
                                 "mehrfamilienhaus", "mehrfamilienhaus"],
        "household_size": [5, 4, 1, 2],
        "home_location_id": [0, 1, 2, 3],   # positional legacy ids: no overlap
    })
    with pytest.raises(RuntimeError, match="ZERO matches"):
        v.home_match_metrics(placed_legacy, bld)
