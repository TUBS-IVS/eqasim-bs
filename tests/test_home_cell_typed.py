import math
import numpy as np, pandas as pd, geopandas as gpd
from shapely.geometry import Point, box
from braunschweig.synthesis.locations import home_cell
from braunschweig.popsim.cells import parse_inspire_id


def _geo(rows):  # rows: (building_id, area, north3035, east3035)
    pts = [Point(e + 50, n + 50) for (_, _, n, e) in rows]
    g = gpd.GeoDataFrame(
        {"building_id": [r[0] for r in rows], "weight": [r[1] for r in rows],
         "area_m2": [r[1] for r in rows], "commune_id": ["031010000000"] * len(rows)},
        geometry=pts, crs="EPSG:3035").to_crs("EPSG:25832")
    return g


def test_typed_places_efh_hh_in_efh_building():
    # cell A: one small (EFH, id 0) + one large (MFH, id 1) building
    buildings = _geo([(0, 80.0, 2689100, 4337000), (1, 1000.0, 2689100, 4337000)])
    cells = pd.DataFrame([{
        "ZENSUS100m": "CRS3035RES100mN2689100E4337000",
        "FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
        "MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
        "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
        "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
        "BewohntWhg_Leerstand_100m_Gitter": 2.0,
        "90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter": 2.0,
    }])
    households = pd.DataFrame({
        "household_id": ["h_efh", "h_mfh"], "commune_id": ["031010000000"] * 2,
        "ZENSUS100m": ["CRS3035RES100mN2689100E4337000"] * 2,
        "building_type_3class": ["ein_zweifamilienhaus", "mehrfamilienhaus"],
        "household_size": [4, 1],
    })
    out, rep = home_cell.assign_homes_typed(households, buildings, cells, random_seed=1)
    by = out.set_index("household_id")["home_location_id"]
    assert by["h_efh"] == 0 and by["h_mfh"] == 1
    assert rep.in_cell_rate == 1.0
    assert list(out.columns) == ["household_id", "commune_id", "home_location_id", "geometry"]
    assert out.crs.to_epsg() == 25832


def test_typed_intersection_join_rescues_boundary_cell():
    """A footprint straddling the A/B cell boundary (centroid in A) must serve
    cell B too when the buildings frame carries a ``footprint`` polygon column.
    Without the fix, B would be a zero-building orphan cell.

    Setup
    -----
    - Cell A: CRS3035RES100mN2689100E4337000  (SW corner N=2689100, E=4337000)
    - Cell B: CRS3035RES100mN2689100E4337100  (SW corner N=2689100, E=4337100)
      (same northing strip, adjacent eastward — share the vertical E=4337100 edge)
    - One building: a 120 m wide footprint box, centroid at E=4337060 (inside A),
      but the east side reaches E=4337120 — 20 m into cell B.
    - Households: one in A, one in B.
    - Expected: both placed (B not orphaned), home in B lies inside B's square.
    """
    # --- cell ids ---
    _CELL_A = "CRS3035RES100mN2689100E4337000"
    _CELL_B = "CRS3035RES100mN2689100E4337100"

    # --- one straddling building footprint (EPSG:3035) ---
    # Box: E 4337000 .. 4337120, N 2689110 .. 2689160
    # centroid E=4337060 → inside cell A (E4337000..4337100)
    # east edge at E=4337120 → 20 m inside cell B (E4337100..4337200)
    footprint_3035 = box(4337000, 2689110, 4337120, 2689160)  # (minx,miny,maxx,maxy)
    centroid_3035 = footprint_3035.centroid  # x=4337060, y=2689135 → cell A

    # Reproject centroid to 25832 to build the building GeoDataFrame
    cent_gs = gpd.GeoSeries([centroid_3035], crs="EPSG:3035").to_crs("EPSG:25832")
    cent_25832 = cent_gs.iloc[0]

    # Reproject footprint polygon to 25832
    fp_gs = gpd.GeoSeries([footprint_3035], crs="EPSG:3035").to_crs("EPSG:25832")
    fp_25832 = fp_gs.iloc[0]

    buildings = gpd.GeoDataFrame(
        {
            "building_id": [0],
            "weight": [200.0],
            "area_m2": [200.0],
            "commune_id": ["031010000000"],
            "footprint": [fp_25832],   # <-- the new column enabling intersection join
        },
        geometry=[cent_25832],
        crs="EPSG:25832",
    )

    # --- minimal cells signals for both cells ---
    cells = pd.DataFrame([
        {
            "ZENSUS100m": _CELL_A,
            "FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
            "MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter": 0.0,
            "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
            "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 0.0,
            "BewohntWhg_Leerstand_100m_Gitter": 1.0,
            "90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter": 1.0,
        },
        {
            "ZENSUS100m": _CELL_B,
            "FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
            "MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter": 0.0,
            "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
            "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 0.0,
            "BewohntWhg_Leerstand_100m_Gitter": 1.0,
            "90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter": 1.0,
        },
    ])

    # --- one household in each cell ---
    households = pd.DataFrame({
        "household_id": ["hh_A", "hh_B"],
        "commune_id": ["031010000000", "031010000000"],
        "ZENSUS100m": [_CELL_A, _CELL_B],
        "building_type_3class": ["ein_zweifamilienhaus", "ein_zweifamilienhaus"],
        "household_size": [2, 2],
    })

    out, rep = home_cell.assign_homes_typed(households, buildings, cells, random_seed=7)

    # --- assert B is not orphaned ---
    # rep.n_zero_building_cells should be 0 (both cells served by the footprint)
    assert rep.n_zero_building_cells == 0, (
        f"Cell B was a zero-building orphan (n_zero={rep.n_zero_building_cells}). "
        "The intersection join must include B because the footprint straddles the boundary."
    )

    by_hh = out.set_index("household_id")

    # hh_B must have a real building_id (not NA)
    bid_B = by_hh.loc["hh_B", "home_location_id"]
    assert not pd.isna(bid_B), (
        "hh_B got no building_id (NA) — it was treated as a zero-building cell. "
        "The footprint intersection join must serve cell B."
    )

    # hh_B's home geometry must lie inside cell B's 100 m square (EPSG:3035)
    _, n_B, e_B = parse_inspire_id(_CELL_B)
    home_B_3035 = out[out["household_id"] == "hh_B"].to_crs("EPSG:3035").geometry.iloc[0]
    assert e_B <= home_B_3035.x <= e_B + 100, (
        f"hh_B home x={home_B_3035.x:.2f} outside cell B east range [{e_B}, {e_B+100}]"
    )
    assert n_B <= home_B_3035.y <= n_B + 100, (
        f"hh_B home y={home_B_3035.y:.2f} outside cell B north range [{n_B}, {n_B+100}]"
    )

    # hh_A's home must lie inside cell A
    _, n_A, e_A = parse_inspire_id(_CELL_A)
    home_A_3035 = out[out["household_id"] == "hh_A"].to_crs("EPSG:3035").geometry.iloc[0]
    assert e_A <= home_A_3035.x <= e_A + 100, (
        f"hh_A home x={home_A_3035.x:.2f} outside cell A east range [{e_A}, {e_A+100}]"
    )
    assert n_A <= home_A_3035.y <= n_A + 100, (
        f"hh_A home y={home_A_3035.y:.2f} outside cell A north range [{n_A}, {n_A+100}]"
    )
