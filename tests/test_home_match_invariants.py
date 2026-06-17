# tests/test_home_match_invariants.py
import pandas as pd, geopandas as gpd
from shapely.geometry import Point
from braunschweig.synthesis.locations import home_cell
from braunschweig.popsim.cells import parse_inspire_id

# Cell A: N2689100 E4337000  — has buildings  (matched path)
# Cell B: N2689200 E4337000  — NO buildings   (random_point_in_cell fallback)
_CELL_A = "CRS3035RES100mN2689100E4337000"
_CELL_B = "CRS3035RES100mN2689200E4337000"


def _setup():
    # Two buildings, both inside cell A square (EPSG:3035 centre pts → reproject to 25832)
    rows = [(0, 80.0, 2689100, 4337000), (1, 1000.0, 2689100, 4337000)]
    pts = [Point(e + 50, n + 50) for (_, _, n, e) in rows]
    b = gpd.GeoDataFrame(
        {"building_id": [r[0] for r in rows], "weight": [r[1] for r in rows],
         "area_m2": [r[1] for r in rows], "commune_id": ["031010000000"] * 2},
        geometry=pts, crs="EPSG:3035").to_crs("EPSG:25832")

    # Cells frame: cell A (with buildings) and cell B (zero buildings but occupied)
    cells = pd.DataFrame([
        {
            "ZENSUS100m": _CELL_A,
            "FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
            "MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
            "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
            "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
            "BewohntWhg_Leerstand_100m_Gitter": 2.0,
            "90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter": 2.0,
        },
        {
            "ZENSUS100m": _CELL_B,
            # Census signals indicate households live here even though there are no
            # ALKIS footprints in cell B → triggers random_point_in_cell fallback
            "FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
            "MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter": 0.0,
            "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
            "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 0.0,
            "BewohntWhg_Leerstand_100m_Gitter": 1.0,
            "90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter": 1.0,
        },
    ])

    # Households: two in cell A (matched path), one in cell B (fallback path)
    hh = pd.DataFrame({
        "household_id": ["a", "b", "c"],
        "commune_id": ["031010000000"] * 3,
        "ZENSUS100m": [_CELL_A, _CELL_A, _CELL_B],
        "building_type_3class": ["ein_zweifamilienhaus", "mehrfamilienhaus", "ein_zweifamilienhaus"],
        "household_size": [3, 2, 1],
    })
    return hh, b, cells


def test_every_household_placed_in_its_own_cell():
    """Every household's home must lie inside ITS OWN 100 m cell square (EPSG:3035)."""
    hh, b, cells = _setup()
    out, _ = home_cell.assign_homes_typed(hh, b, cells, random_seed=5)
    homes = out.set_index("household_id").to_crs("EPSG:3035")

    # Build a lookup from the INPUT households frame: hid → own ZENSUS100m id
    cell_by_hid = hh.set_index("household_id")["ZENSUS100m"].to_dict()

    for hid, row in homes.iterrows():
        own_cell = cell_by_hid[hid]
        _, n, e = parse_inspire_id(own_cell)
        assert e <= row.geometry.x <= e + 100, (
            f"household {hid!r}: x={row.geometry.x:.2f} outside "
            f"[{e}, {e + 100}] of cell {own_cell}"
        )
        assert n <= row.geometry.y <= n + 100, (
            f"household {hid!r}: y={row.geometry.y:.2f} outside "
            f"[{n}, {n + 100}] of cell {own_cell}"
        )


def test_zero_building_cell_household_stays_in_cell():
    """Household in a cell with NO buildings (random_point_in_cell fallback) must
    still land inside that cell's own 100 m square."""
    hh, b, cells = _setup()
    out, _ = home_cell.assign_homes_typed(hh, b, cells, random_seed=42)
    homes = out.set_index("household_id").to_crs("EPSG:3035")

    # Household "c" is the cell-B case (zero buildings)
    row = homes.loc["c"]
    _, n, e = parse_inspire_id(_CELL_B)
    assert e <= row.geometry.x <= e + 100, (
        f"cell-B household x={row.geometry.x:.2f} outside [{e}, {e + 100}]"
    )
    assert n <= row.geometry.y <= n + 100, (
        f"cell-B household y={row.geometry.y:.2f} outside [{n}, {n + 100}]"
    )


def test_marginal_identity_household_set_unchanged():
    """Output must contain exactly the input household ids, no duplicates, no drops."""
    hh, b, cells = _setup()
    out, _ = home_cell.assign_homes_typed(hh, b, cells, random_seed=5)
    assert set(out["household_id"]) == set(hh["household_id"])
    assert len(out) == len(hh) and out["household_id"].is_unique
