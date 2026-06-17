# tests/test_home_match_invariants.py
import numpy as np, pandas as pd, geopandas as gpd
from shapely.geometry import Point
from braunschweig.synthesis.locations import home_cell
from braunschweig.popsim.cells import parse_inspire_id


def _setup():
    rows = [(0, 80.0, 2689100, 4337000), (1, 1000.0, 2689100, 4337000)]
    pts = [Point(e + 50, n + 50) for (_, _, n, e) in rows]
    b = gpd.GeoDataFrame(
        {"building_id": [r[0] for r in rows], "weight": [r[1] for r in rows],
         "area_m2": [r[1] for r in rows], "commune_id": ["031010000000"] * 2},
        geometry=pts, crs="EPSG:3035").to_crs("EPSG:25832")
    cells = pd.DataFrame([{
        "ZENSUS100m": "CRS3035RES100mN2689100E4337000",
        "FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
        "MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
        "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
        "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
        "BewohntWhg_Leerstand_100m_Gitter": 2.0,
        "90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter": 2.0}])
    hh = pd.DataFrame({"household_id": ["a", "b"], "commune_id": ["031010000000"] * 2,
                       "ZENSUS100m": ["CRS3035RES100mN2689100E4337000"] * 2,
                       "building_type_3class": ["ein_zweifamilienhaus", "mehrfamilienhaus"],
                       "household_size": [3, 2]})
    return hh, b, cells


def test_every_household_placed_in_its_own_cell():
    hh, b, cells = _setup()
    out, _ = home_cell.assign_homes_typed(hh, b, cells, random_seed=5)
    homes = out.set_index("household_id").to_crs("EPSG:3035")
    for hid, row in homes.iterrows():
        _, n, e = parse_inspire_id("CRS3035RES100mN2689100E4337000")
        assert e <= row.geometry.x <= e + 100 and n <= row.geometry.y <= n + 100


def test_marginal_identity_household_set_unchanged():
    hh, b, cells = _setup()
    out, _ = home_cell.assign_homes_typed(hh, b, cells, random_seed=5)
    assert set(out["household_id"]) == set(hh["household_id"])
    assert len(out) == len(hh) and out["household_id"].is_unique
