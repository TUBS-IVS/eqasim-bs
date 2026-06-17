import numpy as np, pandas as pd, geopandas as gpd
from shapely.geometry import Point
from braunschweig.synthesis.locations import home_cell


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
