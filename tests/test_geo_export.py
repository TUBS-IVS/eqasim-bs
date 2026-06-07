import fiona
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


def test_aggregate_skips_absent_column():
    """A spec referencing a column not present in the frame is skipped without
    raising; the count column is still returned."""
    persons = pd.DataFrame({
        "person_id": [1, 2], "ars5": ["03101", "03101"], "age": [40, 20],
    })
    agg = GE.aggregate(persons, group_col="ars5",
                       spec=[("age", "numeric"), ("not_a_column", "categorical")],
                       count_name="n_persons")
    assert "n_persons" in agg.columns
    assert agg.set_index("ars5").loc["03101", "n_persons"] == 2
    # The absent column produced no share columns.
    assert not any(c.startswith("not_a_column") for c in agg.columns)


def test_write_geo_package_returns_agg_path_keys(tmp_path):
    """write_geo_package returns a paths dict carrying both aggregate-CSV keys."""
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
    assert "agg_gemeinde" in paths
    assert "agg_kreis" in paths


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


def test_write_layers_includes_household_and_vehicle_aggregates(tmp_path):
    """Household and vehicle specs must produce aggregate columns in both the
    polygon GPKG layers and the agg_kreis.csv / agg_gemeinde.csv mirrors."""
    poly_gem = Polygon([(604000, 5789000), (606000, 5789000),
                        (606000, 5791000), (604000, 5791000)])
    poly_kr = poly_gem

    persons = gpd.GeoDataFrame(
        {"person_id": [1, 2], "ars5": ["03101", "03101"],
         "commune_id": ["03101000", "03101000"], "age": [35, 45]},
        geometry=[Point(605000, 5790000), Point(605100, 5790100)],
        crs="EPSG:25832")

    households = gpd.GeoDataFrame(
        {"household_id": [10, 11], "ars5": ["03101", "03101"],
         "commune_id": ["03101000", "03101000"],
         "number_of_cars": [1, 2], "housing_tenure": ["rent", "own"]},
        geometry=[Point(605000, 5790000), Point(605100, 5790100)],
        crs="EPSG:25832")

    vehicles = gpd.GeoDataFrame(
        {"vehicle_id": [100, 101, 102], "ars5": ["03101", "03101", "03101"],
         "commune_id": ["03101000", "03101000", "03101000"],
         "brand": ["VW", "BMW", "VW"]},
        geometry=[Point(605000, 5790000), Point(605050, 5790050),
                  Point(605100, 5790100)],
        crs="EPSG:25832")

    gemeinde_poly = gpd.GeoDataFrame(
        {"commune_id": ["03101000"]}, geometry=[poly_gem], crs="EPSG:25832")
    kreis_poly = gpd.GeoDataFrame(
        {"ars5": ["03101"]}, geometry=[poly_kr], crs="EPSG:25832")

    paths = GE.write_geo_package(
        out_dir=tmp_path,
        persons=persons,
        households=households,
        vehicles=vehicles,
        gemeinde_poly=gemeinde_poly,
        kreis_poly=kreis_poly,
        person_spec=[("age", "numeric")],
        household_spec=[("number_of_cars", "numeric"), ("housing_tenure", "categorical")],
        vehicle_spec=[("brand", "categorical")])

    # --- agg_gemeinde.csv is returned and exists ---
    assert "agg_gemeinde" in paths
    assert (tmp_path / "agg_gemeinde.csv").exists()

    # --- agg_kreis.csv has household + vehicle aggregate columns ---
    agg_kreis = pd.read_csv(tmp_path / "agg_kreis.csv")
    assert "n_households" in agg_kreis.columns, "household count missing"
    assert "n_vehicles" in agg_kreis.columns, "vehicle count missing"
    assert "number_of_cars_mean" in agg_kreis.columns, "household numeric agg missing"
    assert "housing_tenure_share_rent" in agg_kreis.columns, "housing_tenure categorical agg missing"
    assert "brand_share_VW" in agg_kreis.columns, "brand categorical agg missing"

    row = agg_kreis.iloc[0]
    assert row["n_households"] == 2
    assert row["n_vehicles"] == 3
    assert abs(row["number_of_cars_mean"] - 1.5) < 1e-9
    assert abs(row["housing_tenure_share_rent"] - 0.5) < 1e-9

    # --- kreis_aggregat GPKG layer carries the new columns ---
    gpkg = tmp_path / "population_explorer.gpkg"
    layers = fiona.listlayers(str(gpkg))
    assert "gemeinde_aggregat" in layers, f"gemeinde_aggregat missing; layers={layers}"
    assert "kreis_aggregat" in layers, f"kreis_aggregat missing; layers={layers}"

    kreis_layer = gpd.read_file(str(gpkg), layer="kreis_aggregat")
    assert "number_of_cars_mean" in kreis_layer.columns, \
        "household column missing from kreis_aggregat GPKG layer"
    assert "brand_share_VW" in kreis_layer.columns, \
        "vehicle column missing from kreis_aggregat GPKG layer"
