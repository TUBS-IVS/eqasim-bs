import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from braunschweig.analysis.population_validation import population_source as ps


def _write_run_output(tmp_path):
    prefix = "test_"
    pd.DataFrame({"person_id": [1, 2], "household_id": [10, 10], "age": [40, 8],
                  "sex": ["male", "female"]}).to_csv(
        tmp_path / f"{prefix}persons.csv", sep=";", index=False)
    pd.DataFrame({"household_id": [10], "household_size": [2],
                  "number_of_cars": [1]}).to_csv(
        tmp_path / f"{prefix}households.csv", sep=";", index=False)
    gpd.GeoDataFrame({"household_id": [10]}, geometry=[Point(605000, 5790000)],
                     crs="EPSG:25832").to_file(tmp_path / f"{prefix}homes.gpkg", driver="GPKG")
    return prefix


def test_load_from_run_output(tmp_path):
    prefix = _write_run_output(tmp_path)
    frames = ps.load_population(run_output_dir=str(tmp_path), prefix=prefix)
    assert frames.source_kind == "run_output"
    assert list(frames.persons["person_id"]) == [1, 2]
    assert frames.households["household_size"].iloc[0] == 2
    assert frames.homes.crs.to_epsg() == 25832
    assert frames.vehicles is None  # no vehicles file present


def test_exactly_one_source_required(tmp_path):
    try:
        ps.load_population()  # neither
        assert False, "expected ValueError"
    except ValueError:
        pass
    try:
        ps.load_population(run_output_dir="a", sim_cache="b")  # both
        assert False, "expected ValueError"
    except ValueError:
        pass
