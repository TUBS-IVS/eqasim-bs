"""Contracts of the seed scripts for the university-site and CHE student-origin tables.

Synthetic rows only: the real tables are filled by a later task and never read here, and
every test that runs a script's ``main`` patches the constants and redirects the output
into ``tmp_path``.
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
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


def test_sites_main_refuses_while_the_site_list_is_empty(monkeypatch, tmp_path):
    out = tmp_path / "sites.csv"
    monkeypatch.setattr(sl, "SITE_ROWS", [])
    monkeypatch.setattr(sl, "OUTPUT_PATH", out)
    with pytest.raises(ValueError, match="empty"):
        sl.main([])
    assert not out.exists()


def test_sites_main_writes_comment_header_and_rows(monkeypatch, tmp_path):
    out = tmp_path / "sites.csv"
    monkeypatch.setattr(sl, "SITE_ROWS", [("TU X", "Braunschweig", "03101000", 100),
                                          ("Hochschule Y", "Suderburg", "03360025", 10)])
    monkeypatch.setattr(sl, "SEMESTER", "WS 2000/01")
    monkeypatch.setattr(sl, "ACCESS_DATE", "2000-01-01")
    monkeypatch.setattr(sl, "OUTPUT_PATH", out)
    assert sl.main([]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    header = [line for line in lines if line.startswith("#")]
    assert header and all(line.startswith("#") for line in lines[:len(header)])
    assert any("WS 2000/01" in line and "2000-01-01" in line for line in header)
    back = pd.read_csv(out, comment="#", dtype={"site_ags8": str, "site_kreis_ars5": str})
    assert list(back["scope"]) == ["local", "surrounding"]
    assert list(back["students_persons"]) == [100, 10]


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
    counts = frame.groupby(["view", "row_kind"]).size().to_dict()
    assert counts == {("institution_origins", "listed"): 2, ("institution_origins", "abroad"): 1,
                      ("institution_origins", "other"): 1, ("institution_origins", "total"): 1,
                      ("kreis_destinations", "listed"): 2, ("kreis_destinations", "total"): 1}
    assert str(frame["rank"].dtype) == "Int64"
    assert frame.loc[frame["row_kind"] != "listed", "rank"].isna().all()
    assert frame.loc[frame["row_kind"] == "listed", "rank"].between(1, 10).all()
    kd = frame[frame["view"] == "kreis_destinations"]
    assert kd.loc[kd["row_kind"] == "listed", "students_persons"].sum() <= kd.loc[
        kd["row_kind"] == "total", "students_persons"].item()


def test_che_views_must_agree_on_a_shared_cell():
    destinations = [("03157", "Peine", 1, "TU X", 31), ("03157", "Peine", "total", "", 70)]
    with pytest.raises(ValueError, match="views disagree"):
        co.build_origins_frame(_origins(), destinations)


def test_che_rejects_the_same_origin_kreis_twice_within_an_institution():
    origins = [("TU X", 1, "03101", "Braunschweig", 50), ("TU X", 2, "03101", "Braunschweig", 30),
               ("TU X", "abroad", "", "Ausland", 5), ("TU X", "other", "", "uebrige", 15),
               ("TU X", "total", "", "", 100)]
    with pytest.raises(ValueError, match="listed twice.*TU X"):
        co.build_origins_frame(origins, [])


def test_che_rejects_the_same_institution_twice_within_a_kreis():
    destinations = [("03157", "Peine", 1, "TU X", 30), ("03157", "Peine", 2, "TU X", 20),
                    ("03157", "Peine", "total", "", 70)]
    with pytest.raises(ValueError, match="listed twice.*03157"):
        co.build_origins_frame([], destinations)


def _closed_institution(ars5):
    return [("TU X", 1, ars5, "Braunschweig", 50), ("TU X", "abroad", "", "Ausland", 0),
            ("TU X", "other", "", "uebrige", 0), ("TU X", "total", "", "", 50)]


@pytest.mark.parametrize("origins, destinations, message", [
    (_closed_institution(""), [], "5-digit"),
    (_closed_institution("0310"), [], "5-digit"),
    ([], [("03157", "Peine", 1, "", 30), ("03157", "Peine", "total", "", 70)], "empty institution"),
    ([], [("3157", "Peine", 1, "TU X", 30), ("3157", "Peine", "total", "", 70)], "5-digit"),
])
def test_che_rejects_listed_rows_without_a_valid_key(origins, destinations, message):
    with pytest.raises(ValueError, match=message):
        co.build_origins_frame(origins, destinations)


def test_che_main_refuses_while_the_lists_are_empty(monkeypatch, tmp_path):
    out = tmp_path / "che.csv"
    monkeypatch.setattr(co, "INSTITUTION_ORIGINS", [])
    monkeypatch.setattr(co, "KREIS_DESTINATIONS", [])
    monkeypatch.setattr(co, "OUTPUT_PATH", out)
    with pytest.raises(ValueError, match="empty"):
        co.main([])
    assert not out.exists()


def test_che_main_writes_comment_header_and_both_views(monkeypatch, tmp_path):
    out = tmp_path / "che.csv"
    monkeypatch.setattr(co, "INSTITUTION_ORIGINS", _origins())
    monkeypatch.setattr(co, "KREIS_DESTINATIONS", [("03157", "Peine", 1, "TU X", 30),
                                                   ("03157", "Peine", "total", "", 70)])
    monkeypatch.setattr(co, "ACCESS_DATE", "2000-01-01")
    monkeypatch.setattr(co, "OUTPUT_PATH", out)
    assert co.main([]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    header = [line for line in lines if line.startswith("#")]
    assert header and all(line.startswith("#") for line in lines[:len(header)])
    assert any("owner decision 2026-11-25" in line for line in header)
    back = pd.read_csv(out, comment="#", dtype={"origin_ars5": str})
    assert back["view"].value_counts().to_dict() == {"institution_origins": 5, "kreis_destinations": 2}


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

    with pytest.raises(ValueError, match="EPSG:25832"):
        co.required_origin_kreise(polygons.to_crs("EPSG:4326"), site.to_crs("EPSG:4326"), 100.0)
    # another projected CRS is refused too, not silently accepted
    with pytest.raises(ValueError, match="EPSG:25832"):
        co.required_origin_kreise(polygons.set_crs("EPSG:3035", allow_override=True),
                                  site.set_crs("EPSG:3035", allow_override=True), 100.0)


def test_vg250_frames_use_land_area_only_for_kreise_and_site_points():
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import box

    # Kreis 03101 and commune 03101000 each have a land polygon (GF 4) and a far-away water
    # polygon (GF 1); the water polygon must neither widen the Kreis nor move the site point.
    kreise_raw = geopandas.GeoDataFrame(
        {"ARS": ["03101", "03101"], "GF": [4, 1]},
        geometry=[box(0, 0, 1000, 1000), box(500000, 0, 501000, 1000)], crs="EPSG:25832")
    communes_raw = geopandas.GeoDataFrame(
        {"ARS": ["031010000000", "031010000000", "031580000037"], "GF": [4, 1, 4]},
        geometry=[box(0, 0, 1000, 1000), box(500000, 0, 501000, 1000), box(2000, 0, 3000, 1000)],
        crs="EPSG:25832")
    kreise, points = co.prepare_vg250_frames(kreise_raw, communes_raw, ["03101000"])
    assert list(kreise["ars5"]) == ["03101"]
    assert kreise.geometry.iloc[0].bounds == (0.0, 0.0, 1000.0, 1000.0)
    assert list(points["commune_id"]) == ["03101000"]
    assert points.geometry.iloc[0].x < 1000

    with pytest.raises(ValueError, match="not found"):
        co.prepare_vg250_frames(kreise_raw, communes_raw, ["03999999"])


@pytest.mark.parametrize("kreise_ars, commune_ars, layer", [
    (["0310"], ["031010000000"], "vg250_krs"),
    (["03101"], ["03101000"], "vg250_gem"),
])
def test_vg250_frames_reject_malformed_ars_naming_the_layer(kreise_ars, commune_ars, layer):
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import box

    kreise_raw = geopandas.GeoDataFrame({"ARS": kreise_ars}, geometry=[box(0, 0, 1, 1)],
                                        crs="EPSG:25832")
    communes_raw = geopandas.GeoDataFrame({"ARS": commune_ars}, geometry=[box(0, 0, 1, 1)],
                                          crs="EPSG:25832")
    with pytest.raises(ValueError, match=layer):
        co.prepare_vg250_frames(kreise_raw, communes_raw, ["03101000"])


def test_only_local_sites_are_used_for_the_coverage_list():
    sites = pd.DataFrame({"site_ags8": ["03101000", "03241001"], "scope": ["local", "surrounding"]})
    assert co.local_site_ags8(sites) == ["03101000"]
    with pytest.raises(ValueError, match="local"):
        co.local_site_ags8(sites.assign(scope="surrounding"))
