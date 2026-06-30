import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import geopandas as gpd
from shapely.geometry import Polygon
from braunschweig.gravity.distance_matrix_taz import taz_distance_matrix


def _taz():
    a = Polygon([(0, 0), (0, 1000), (1000, 1000), (1000, 0)])        # centroid (500, 500)
    b = Polygon([(3000, 0), (3000, 1000), (4000, 1000), (4000, 0)])  # centroid (3500, 500)
    return gpd.GeoDataFrame(
        {"taz_id": ["310101901", "310101902"], "commune_id": ["03101000", "03101000"],
         "kreis": ["03101", "03101"], "regiostar7": [72, 74]},
        geometry=[a, b], crs="EPSG:25832",
    )


def test_distance_matrix_shape_and_keys():
    df = taz_distance_matrix(_taz())
    assert set(df.columns) == {"origin_id", "destination_id", "distance_km"}
    assert len(df) == 4
    assert df["origin_id"].map(type).eq(str).all()


def test_distance_matrix_values():
    df = taz_distance_matrix(_taz()).set_index(["origin_id", "destination_id"])["distance_km"]
    assert df[("310101901", "310101901")] == 0.0
    assert abs(df[("310101901", "310101902")] - 3.0) < 1e-9
    assert abs(df[("310101901", "310101902")] - df[("310101902", "310101901")]) < 1e-12
