import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon
from braunschweig.gravity.taz_margins import build_dest_attraction_per_taz


def _taz():  # TAZ stage commune_id is 8-digit AGS (Phase-1 contract)
    a = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    b = Polygon([(10, 0), (10, 10), (20, 10), (20, 0)])
    return gpd.GeoDataFrame(
        {"taz_id": ["T1", "T2"], "commune_id": ["03101000", "03101000"], "kreis": ["03101", "03101"]},
        geometry=[a, b], crs="EPSG:25832",
    )


def _buildings():
    return gpd.GeoDataFrame({"building_id": [0, 1], "potential_work": [30.0, 10.0],
                             "commune_id": ["03101000", "03101000"]},
                            geometry=[Point(5, 5), Point(15, 5)], crs="EPSG:25832")


_AGS_TO_ARS = {"03101000": "031010000000"}   # crosswalk (AGS-8 -> ARS-12)


def test_dest_margin_splits_and_preserves_commune_total():
    emp = pd.DataFrame({"commune_id": ["031010000000"], "weight": [100.0]})  # ARS-12, authoritative
    df, primary, fallback = build_dest_attraction_per_taz(_buildings(), emp, _taz(), _AGS_TO_ARS)
    out = df.set_index("taz_id")["attraction"]
    assert abs(out["T1"] - 75.0) < 1e-9     # 30/40 * 100
    assert abs(out["T2"] - 25.0) < 1e-9     # 10/40 * 100
    assert abs(df.groupby("commune_id")["attraction"].sum().iloc[0] - 100.0) < 1e-9


def test_dest_margin_zero_potential_splits_uniformly():
    b = gpd.GeoDataFrame({"building_id": [0, 1], "potential_work": [0.0, 0.0],
                          "commune_id": ["03101000", "03101000"]},
                         geometry=[Point(5, 5), Point(15, 5)], crs="EPSG:25832")
    emp = pd.DataFrame({"commune_id": ["031010000000"], "weight": [100.0]})
    df, _, _ = build_dest_attraction_per_taz(b, emp, _taz(), _AGS_TO_ARS)
    out = df.set_index("taz_id")["attraction"]
    assert abs(out["T1"] - 50.0) < 1e-9 and abs(out["T2"] - 50.0) < 1e-9


def test_dest_margin_raises_when_employer_commune_missing_from_taz():
    emp = pd.DataFrame({"commune_id": ["031010000000", "035400000000"], "weight": [100.0, 50.0]})
    with pytest.raises(ValueError, match="employees but no TAZ"):
        build_dest_attraction_per_taz(_buildings(), emp, _taz(), _AGS_TO_ARS)
