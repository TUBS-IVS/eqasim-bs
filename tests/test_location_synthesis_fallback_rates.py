"""Fallback-rate reporting of the OSM location stages (CLAUDE.md "Fallback transparency").

Both tests run the stage's ``execute`` on a tiny synthetic input: the primary path must keep
producing the same rows, and the fallback must be counted and reported (warning above the
module's ``FALLBACK_WARN_RATE``).
"""
import importlib
import sys
import types
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely.geometry as geo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CRS = "EPSG:25832"


class _Context:
    def __init__(self, stages):
        self._stages = stages

    def stage(self, name):
        return self._stages[name]

    def path(self, name=None):
        return "unused"

    def progress(self, iterable, total=None):
        return iterable


def _square(x0):
    return geo.box(x0, 0.0, x0 + 100.0, 100.0)


def test_osm_locations_count_dropped_municipality_chunks_and_default_floors(monkeypatch, capsys):
    class _FakeOSM:
        def __init__(self, path):
            self.chunk = Path(path).name.split(".")[0]

        def get_buildings(self, osm_filter):
            if self.chunk == "B":
                raise KeyError("building")  # pyrosm finds no matching feature in this chunk
            return gpd.GeoDataFrame({"building": ["school"]}, geometry=[_square(10.0)], crs=CRS)

    monkeypatch.setitem(sys.modules, "pyrosm", types.SimpleNamespace(OSM=_FakeOSM))
    sys.modules.pop("eqasim_common.data.osm.locations", None)
    module = importlib.import_module("eqasim_common.data.osm.locations")

    zones = gpd.GeoDataFrame(
        {"commune_id": ["A", "B"], "iris_id": ["A0", "B0"]}, geometry=[_square(0.0), _square(1000.0)], crs=CRS)
    context = _Context({"data.spatial.iris": zones, "eqasim_common.data.osm.chunked": ["A", "B"],
                        "data.spatial.municipalities": zones})

    result = module.execute(context)
    output = capsys.readouterr().out

    n_filters = len(module.OSM_FILTERS)
    assert len(result) == n_filters  # primary path: one POI of municipality A per location type
    assert set(result["commune_id"]) == {"A"}
    assert set(result["floors"]) == {module.DEFAULT_FLOORS}
    assert "chunks primary %d/%d" % (n_filters, 2 * n_filters) in output
    assert "WARNING" in output  # 50% of the chunks dropped > FALLBACK_WARN_RATE


def test_education_reports_centroid_fallback_rate(capsys):
    from eqasim_common.locations import education

    municipalities = gpd.GeoDataFrame(
        {"commune_id": ["A", "B"]}, geometry=[_square(0.0), _square(1000.0)], crs=CRS)
    osm = gpd.GeoDataFrame({
        "commune_id": ["A"], "iris_id": ["A0"], "location_type": ["education"], "area": [500.0], "floors": [2],
        "building": ["school"], "amenity": [None]}, geometry=[geo.Point(50.0, 50.0)], crs=CRS)
    context = _Context({"eqasim_common.data.osm.locations": osm, "data.spatial.municipalities": municipalities})

    result = education.execute(context)
    output = capsys.readouterr().out

    assert result["fake"].tolist() == [False, True]  # municipality A real POI, B centroid fallback
    assert "(primary) 1/2" in output
    assert "WARNING" in output  # 50% centroid fallback > FALLBACK_WARN_RATE
