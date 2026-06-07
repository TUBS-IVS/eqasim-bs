import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon
from braunschweig.analysis.population_validation import geo_export as GE


def test_aggregate_numeric_and_categorical():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3], "ars5": ["03101", "03101", "03102"],
        "age": [40, 20, 60], "sex": ["male", "female", "male"],
    })
    agg = GE.aggregate(persons, group_col="ars5",
                       spec=[("age", "numeric"), ("sex", "categorical")],
                       count_name="n_persons")
    r = agg.set_index("ars5").loc["03101"]
    assert r["n_persons"] == 2
    assert abs(r["age_mean"] - 30.0) < 1e-9
    assert abs(r["sex_share_male"] - 0.5) < 1e-9


def test_write_layers_creates_gpkg_and_csv(tmp_path):
    persons = gpd.GeoDataFrame(
        {"person_id": [1], "ars5": ["03101"], "commune_id": ["03101000"], "age": [30]},
        geometry=[Point(605000, 5790000)], crs="EPSG:25832")
    kreis_poly = gpd.GeoDataFrame(
        {"ars5": ["03101"]},
        geometry=[Polygon([(604000, 5789000), (606000, 5789000),
                           (606000, 5791000), (604000, 5791000)])], crs="EPSG:25832")
    paths = GE.write_geo_package(
        out_dir=tmp_path, persons=persons, households=persons.iloc[:0],
        vehicles=None, gemeinde_poly=kreis_poly.rename(columns={"ars5": "commune_id"}),
        kreis_poly=kreis_poly,
        person_spec=[("age", "numeric")], household_spec=[], vehicle_spec=[])
    assert (tmp_path / "population_explorer.gpkg").exists()
    assert (tmp_path / "agg_kreis.csv").exists()
    assert (tmp_path / "level_persons.csv").exists()
