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


def test_households_with_buildings_land_in_their_own_cell():
    """A household whose OWN 100 m cell contains buildings must be placed inside
    that own cell square (EPSG:3035). (The zero-building neighbour-fallback case is
    covered by the two tests below.)"""
    hh, b, cells = _setup()
    out, _ = home_cell.assign_homes_typed(hh, b, cells, random_seed=5)
    homes = out.set_index("household_id").to_crs("EPSG:3035")

    # Households "a" and "b" live in cell A, which has buildings -> own cell.
    for hid in ("a", "b"):
        row = homes.loc[hid]
        _, n, e = parse_inspire_id(_CELL_A)
        assert e <= row.geometry.x <= e + 100, (
            f"household {hid!r}: x={row.geometry.x:.2f} outside [{e}, {e + 100}] of cell A"
        )
        assert n <= row.geometry.y <= n + 100, (
            f"household {hid!r}: y={row.geometry.y:.2f} outside [{n}, {n + 100}] of cell A"
        )


def test_zero_building_cell_snaps_to_nearest_neighbour_building():
    """ENH: a household whose own cell has NO building, but a NEIGHBOURING cell does
    (the common boundary-building case), is snapped to that nearest neighbour-cell
    building (real building_id), instead of a random in-cell point. In _setup() cell
    B (household 'c') is directly adjacent to cell A, which has buildings."""
    hh, b, cells = _setup()
    out, report = home_cell.assign_homes_typed(hh, b, cells, random_seed=42)
    homes = out.set_index("household_id").to_crs("EPSG:3035")

    row = homes.loc["c"]
    # 'c' must carry a real building id (one of cell A's buildings 0/1)...
    assert not pd.isna(row["home_location_id"]), (
        "cell-B household should be snapped to a neighbour-cell building, got NA"
    )
    assert row["home_location_id"] in (0, 1)
    # ...and its home point lies in the NEIGHBOUR cell A (where that building is).
    _, n_a, e_a = parse_inspire_id(_CELL_A)
    assert e_a <= row.geometry.x <= e_a + 100 and n_a <= row.geometry.y <= n_a + 100, (
        f"cell-B household snapped point {(row.geometry.x, row.geometry.y)} not in cell A"
    )
    # The neighbour-fallback is counted in the report (logged at run time).
    assert report.n_neighbour_cell_placed == 1
    assert report.n_zero_building_cells == 1


def test_isolated_zero_building_cell_falls_back_to_in_cell_point():
    """When the zero-building cell has NO building within NEIGHBOUR_MAX_RING, the
    household keeps a random point inside its OWN cell (Zensus cell authoritative;
    no commune-wide relocation, no neighbour snap)."""
    hh, b, cells = _setup()
    # Move household 'c' to a cell far from cell A (well beyond NEIGHBOUR_MAX_RING),
    # and register that far cell in the cells frame with a residential signal.
    far_cell = "CRS3035RES100mN2695000E4345000"  # ~6-8 km from cell A
    hh.loc[hh["household_id"] == "c", "ZENSUS100m"] = far_cell
    far_row = cells[cells["ZENSUS100m"] == _CELL_B].iloc[0].to_dict()
    far_row["ZENSUS100m"] = far_cell
    cells = pd.concat([cells, pd.DataFrame([far_row])], ignore_index=True)

    out, report = home_cell.assign_homes_typed(hh, b, cells, random_seed=42)
    homes = out.set_index("household_id").to_crs("EPSG:3035")

    row = homes.loc["c"]
    assert pd.isna(row["home_location_id"]), "isolated empty cell should NOT get a building"
    _, n, e = parse_inspire_id(far_cell)
    assert e <= row.geometry.x <= e + 100 and n <= row.geometry.y <= n + 100, (
        "isolated empty-cell household must stay inside its own cell square"
    )
    assert report.n_neighbour_cell_placed == 0
    assert report.n_zero_building_cells == 1


def test_marginal_identity_household_set_unchanged():
    """Output must contain exactly the input household ids, no duplicates, no drops."""
    hh, b, cells = _setup()
    out, _ = home_cell.assign_homes_typed(hh, b, cells, random_seed=5)
    assert set(out["household_id"]) == set(hh["household_id"])
    assert len(out) == len(hh) and out["household_id"].is_unique


def test_typed_path_is_deterministic_same_seed():
    """Two calls with the same seed must produce identical home_location_id and geometry
    for every household, including the zero-building cell-B fallback (random_point_in_cell
    determinism is exercised by the cell-B household 'c')."""
    hh, b, cells = _setup()
    out1, _ = home_cell.assign_homes_typed(hh, b, cells, random_seed=5)
    out2, _ = home_cell.assign_homes_typed(hh, b, cells, random_seed=5)

    h1 = out1.set_index("household_id").to_crs("EPSG:3035")
    h2 = out2.set_index("household_id").to_crs("EPSG:3035")

    for hid in hh["household_id"]:
        bid1 = h1.loc[hid, "home_location_id"]
        bid2 = h2.loc[hid, "home_location_id"]
        both_na = pd.isna(bid1) and pd.isna(bid2)
        assert both_na or (not pd.isna(bid1) and not pd.isna(bid2) and bid1 == bid2), (
            f"household {hid!r}: building_id mismatch ({bid1!r} vs {bid2!r})"
        )
        g1 = h1.loc[hid, "geometry"]
        g2 = h2.loc[hid, "geometry"]
        assert abs(g1.x - g2.x) < 1e-6, (
            f"household {hid!r}: geometry x mismatch ({g1.x} vs {g2.x})"
        )
        assert abs(g1.y - g2.y) < 1e-6, (
            f"household {hid!r}: geometry y mismatch ({g1.y} vs {g2.y})"
        )
