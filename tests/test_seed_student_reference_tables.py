"""Contracts of the seed scripts for the university-site and CHE student-origin tables.

Synthetic rows only: the real tables are filled by a later task and never read here.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sl = _load("seed_lsn_university_sites")
co = _load("seed_che_student_origins")


def test_sites_frame_classifies_scope_and_sums_per_institution():
    rows = [("TU X", "Braunschweig", "03101000", 100), ("Hochschule Y", "Wolfenbuettel", "03158037", 40),
            ("Hochschule Y", "Suderburg", "03360025", 10)]
    frame = sl.build_sites_frame(rows)
    assert list(frame["scope"]) == ["local", "local", "surrounding"]
    assert list(frame["site_kreis_ars5"]) == ["03101", "03158", "03360"]
    assert frame.groupby("institution")["students_persons"].sum().to_dict() == {"Hochschule Y": 50, "TU X": 100}


@pytest.mark.parametrize("rows, message", [
    ([("TU X", "A", "03101000", 1), ("TU X", "A", "03101000", 2)], "duplicate"),
    ([("TU X", "A", "0310100", 1)], "ags8"),
    ([("TU X", "A", "03101000", -1)], "negative"),
])
def test_sites_frame_rejects_bad_rows(rows, message):
    with pytest.raises(ValueError, match=message):
        sl.build_sites_frame(rows)


def test_sites_main_refuses_while_the_site_list_is_empty():
    with pytest.raises(ValueError, match="empty"):
        sl.main([])


def _origins(counts=(50, 30), abroad=5, other=15, total=100):
    return [("TU X", 1, "03101", "Braunschweig", counts[0]), ("TU X", 2, "03157", "Peine", counts[1]),
            ("TU X", "abroad", "", "Ausland", abroad), ("TU X", "other", "", "uebrige", other),
            ("TU X", "total", "", "", total)]


def test_che_rows_must_close_to_the_total():
    with pytest.raises(ValueError, match="does not close"):
        co.build_origins_frame(_origins(other=14), [])


def test_che_listed_rows_are_ranked_and_non_increasing():
    with pytest.raises(ValueError, match="non-increasing"):
        co.build_origins_frame(_origins(counts=(30, 50)), [])


def test_che_frame_round_trip_of_both_views():
    destinations = [("03157", "Peine", 1, "TU X", 30), ("03157", "Peine", 2, "Uni Z", 20),
                    ("03157", "Peine", "total", "", 70)]
    frame = co.build_origins_frame(_origins(), destinations)
    assert set(frame["view"]) == {"institution_origins", "kreis_destinations"}
    kd = frame[frame["view"] == "kreis_destinations"]
    assert kd.loc[kd["row_kind"] == "listed", "students_persons"].sum() <= kd.loc[
        kd["row_kind"] == "total", "students_persons"].item()
    assert frame.loc[frame["row_kind"] == "listed", "rank"].between(1, 10).all()


def test_che_views_must_agree_on_a_shared_cell():
    destinations = [("03157", "Peine", 1, "TU X", 31), ("03157", "Peine", "total", "", 70)]
    with pytest.raises(ValueError, match="views disagree"):
        co.build_origins_frame(_origins(), destinations)


def test_che_main_refuses_while_the_lists_are_empty():
    with pytest.raises(ValueError, match="empty"):
        co.main([])


def test_required_origin_kreise_uses_distance_to_the_polygon():
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Point, box

    # metric CRS, site at the origin; polygons 0 km, 90 km and 150 km away (east edge distances)
    polygons = geopandas.GeoDataFrame(
        {"ars5": ["00001", "00002", "00003"]},
        geometry=[box(-1000, -1000, 1000, 1000), box(90000, 0, 91000, 1000),
                  box(150000, 0, 151000, 1000)],
        crs="EPSG:25832")
    site = geopandas.GeoDataFrame({"institution": ["TU X"]}, geometry=[Point(0, 0)], crs="EPSG:25832")
    assert co.required_origin_kreise(polygons, site, 100.0) == ["00001", "00002"]

    with pytest.raises(ValueError, match="metric"):
        co.required_origin_kreise(polygons.to_crs("EPSG:4326"), site.to_crs("EPSG:4326"), 100.0)
