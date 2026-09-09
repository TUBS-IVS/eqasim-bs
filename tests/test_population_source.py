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


def test_population_only_cache_names_the_missing_locations_output(tmp_path):
    """A population-only cache must fail with an ACTIONABLE message, not a bare
    read error from deep inside geopandas.

    A synpp cache that stopped before the locations stage (an interrupted 100 % run, or a
    population-only smoke) carries persons.csv and households.csv but no homes.gpkg. The
    loader used to hand that case straight to gpd.read_file, whose error names a path and
    nothing else -- the #240 smoke and the 2026-08-20 night run both had to fall back to
    reading the stage pickle without the CLI ever saying why. The message must name the
    missing file, say that the cache is population-only, and point at the supported route.
    """
    prefix = "test_"
    pd.DataFrame({"person_id": [1], "household_id": [10], "age": [40],
                  "sex": ["male"]}).to_csv(
        tmp_path / f"{prefix}persons.csv", sep=";", index=False)
    pd.DataFrame({"household_id": [10], "household_size": [1],
                  "number_of_cars": [0]}).to_csv(
        tmp_path / f"{prefix}households.csv", sep=";", index=False)
    # No homes.gpkg written: this is exactly a population-only cache.

    try:
        ps.load_population(sim_cache=str(tmp_path), prefix=prefix)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as error:
        message = str(error)
    assert f"{prefix}homes.gpkg" in message
    assert "population-only" in message
    # The supported alternative is named, so the caller is not left guessing.
    assert "run_population_validation" in message or "stage pickle" in message
