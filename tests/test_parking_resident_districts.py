"""Resident parking districts, the second layer of the parking cost zones (spec Amendment C3, issue #436).

Covered, and why in this shape:

* **The curation step** (``scripts/curation/parking_zones_2026/resident_districts.py``) builds the committed layer
  from the owner's gitignored packages. It is pinned on synthetic packages written the way the owner supplied them (a
  zip per municipality holding a GeoPackage in EPSG:25832): ids are prefixed per town so that code A of Braunschweig
  and of Goslar cannot collide, the Goslar feature "Parkbereich C - Oberstadt" without a code joins C, an overlap
  between two districts of one town is cut from the later district and recorded, every feature carries the source
  wording of controller ruling R-C1, a changed package or attribute is refused, and the written file is
  deterministic, ASCII and valid as stored.
* **The loader and validator** (``braunschweig.parking.zones.load_resident_districts`` and
  ``validate_resident_districts``) refuse what would mis-assign an activity: duplicate ids, overlapping districts,
  invalid geometry (never repaired: a repair would change the district the file states), missing provenance.
* **The committed layer** loads strictly and has the districts the owner named (Braunschweig A, B, C; Goslar A, B,
  C, F, G, H, J).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, Point, Polygon, box

from braunschweig.parking import zones as pz

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
COMMITTED_LAYER = (REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "parking"
                   / "parking_resident_districts_2026.geojson")
METRIC_CRS = "EPSG:25832"
X0, Y0 = 604_000.0, 5_789_000.0
BS, GS = "03101000", "03153017"
#: Goslar lies far from the Braunschweig layout, so nothing of the two towns touches.
GS_X = 40_000.0
#: A district read back from the file differs from its metric geometry by the WGS84 rounding (7 decimals, about 1 cm):
#: every edge moves by up to a centimetre, so a 100 m square gains up to about 1 m2 and a 1,000 m x 400 m box 4.6 m2.
FILE_ROUNDING = 5e-5
FILE_ROUNDING_ABSOLUTE_M2 = 3.0

#: The source wording of controller ruling R-C1 for the districts, binding for the committed layer. Written out here,
#: independently of the curation step, so that a changed word cannot pass silently.
BRAUNSCHWEIG_PROVENANCE = ("Stadt Braunschweig, resident parking district map (vector PDF 2012 for the inner "
                           "boundaries, the current overview for the outer boundary); digitised (owner-supplied "
                           "package 2026-10-01); working accuracy 30 m; base map Open GeoData dl-de/by-2-0")
GOSLAR_PROVENANCE = ("Stadt Goslar, ArcGIS service Bewohnerparken (last edit 2018-11-22); open reuse licence not "
                     "verified; used by owner decision 2026-10-01")

BS_DISTRICT_IDS = ["bs_district_a", "bs_district_b", "bs_district_c"]
GS_DISTRICT_IDS = ["gs_district_a", "gs_district_b", "gs_district_c", "gs_district_f", "gs_district_g",
                   "gs_district_h", "gs_district_j"]


def _box(x0, y0, x1, y1, dx=0.0):
    return box(X0 + dx + x0, Y0 + y0, X0 + dx + x1, Y0 + y1)


# --------------------------------------------------------------------------- synthetic packages

BS_SOURCE_URL = "https://www.braunschweig.de/leben/stadtplan_verkehr/parken-in-braunschweig/parken-in-der-innenstadt.php"
BS_ATTRIBUTES = {"zone_type": "resident", "source": BS_SOURCE_URL, "source_date": "2012-11 (map code); currently published",
                 "digitized_on": "2026-10-01", "method": "Published-map digitization; affine georeferencing",
                 "estimated_accuracy_m": 30, "registration_rmse_m": 4.88}
GS_EDIT = "2018-11-22T15:42:08.753000+00:00"
#: Goslar G and H overlap by 2 m x 5 m = 10 m2 (the real package: 6.3 m2), more than the 1 m2 digitisation tolerance.
GS_G, GS_H = _box(0, 0, 100, 100, GS_X + 3000), _box(98, 95, 200, 195, GS_X + 3000)
GS_OVERLAP_M2 = 10.0
#: The uncoded Goslar feature "Parkbereich C - Oberstadt" has two parts, away from C.
GS_C = _box(0, 0, 100, 100, GS_X + 1000)
GS_OBERSTADT = MultiPolygon([_box(0, 150, 50, 200, GS_X + 1000), _box(100, 150, 150, 200, GS_X + 1000)])


def _write_package(directory: Path, name: str, layers: dict) -> None:
    """``<directory>/<name>.zip`` holding ``<name>/<name>.gpkg`` with ``layers`` (layer name -> GeoDataFrame)."""
    work = directory / f"{name}_work"
    work.mkdir()
    gpkg = work / f"{name}.gpkg"
    for layer, frame in layers.items():
        frame.to_file(gpkg, layer=layer, driver="GPKG")
    with zipfile.ZipFile(directory / f"{name}.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(gpkg, f"{name}/{name}.gpkg")


def _braunschweig_layer(**changes) -> gpd.GeoDataFrame:
    rows = [dict(BS_ATTRIBUTES, zone_id=code, name=f"Bewohnerparkbezirk {code}") for code in "ABC"]
    frame = gpd.GeoDataFrame(rows, geometry=[_box(0, 0, 500, 500), _box(500, 0, 1000, 500), _box(0, 500, 1000, 900)],
                             crs=METRIC_CRS)
    for column, value in changes.items():
        frame[column] = value
    return frame


def _goslar_layer(drop_uncoded=False, extra_uncoded=False, edit=GS_EDIT) -> gpd.GeoDataFrame:
    codes = ["A", "B", "C", "F", "G", "H", "J", None]
    geometries = [_box(0, 0, 100, 100, GS_X), _box(0, 0, 100, 100, GS_X + 500), GS_C,
                  _box(0, 0, 100, 100, GS_X + 2000), GS_G, GS_H, _box(0, 0, 100, 100, GS_X + 4000), GS_OBERSTADT]
    description = ["Parkbereich A - Oberstadt", "Parkbereich B - Oberstadt", "Parkbereich C - Oberstadt",
                   "Parkbereich F - Unterstadt", "Parkbereich G - Unterstadt", "Parkbereich H - Unterstadt",
                   "Parkbereich J - Georgenberg", "Parkbereich C - Oberstadt"]
    object_ids = [1, 3, 4, 5, 6, 7, 10, 11]
    if drop_uncoded:
        codes, geometries, description, object_ids = codes[:-1], geometries[:-1], description[:-1], object_ids[:-1]
    if extra_uncoded:
        codes.append(None)
        geometries.append(_box(0, 0, 10, 10, GS_X + 6000))
        description.append("Parkbereich X")
        object_ids.append(12)
    return gpd.GeoDataFrame(
        {"OBJECTID": object_ids, "Parkbereiche_Kennzeichen": codes, "Parkbereiche_Beschreibung": description,
         "geometry_repaired": [False] * len(codes), "source_layer_id": [4] * len(codes),
         "source_data_edit_date": [edit] * len(codes)}, geometry=geometries, crs=METRIC_CRS)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _packages(directory: Path, braunschweig=None, goslar=None) -> dict:
    """The two packages the step reads, written into ``directory``; returns their SHA-256 by package name."""
    _write_package(directory, "Braunschweig_Parkzonen",
                   {"resident_parking_zones": braunschweig if braunschweig is not None else _braunschweig_layer()})
    _write_package(directory, "Goslar_Parkdaten",
                   {"resident_parking_zones": goslar if goslar is not None else _goslar_layer()})
    return {name: _sha256(directory / f"{name}.zip") for name in ("Braunschweig_Parkzonen", "Goslar_Parkdaten")}


@pytest.fixture(scope="module")
def curation():
    """The curation step as a module (it imports municipal_zones and curation_common from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("resident_districts_under_test",
                                                      CURATION_DIR / "resident_districts.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


@pytest.fixture(scope="module")
def built(curation, tmp_path_factory):
    """The layer built from the synthetic packages, with the directory and the SHA-256 it was built from."""
    directory = tmp_path_factory.mktemp("municipal_2026-10-01")
    sha256 = _packages(directory)
    frame = curation.build_districts(curation.load_district_packages(directory, expected_sha256=sha256))
    return frame, directory, sha256


# --------------------------------------------------------------------------- curation: the built layer


def test_ids_are_prefixed_per_town_so_that_code_a_of_two_towns_cannot_collide(built):
    frame, _, _ = built
    assert list(frame["district_id"]) == BS_DISTRICT_IDS + GS_DISTRICT_IDS
    assert frame["district_id"].is_unique
    assert list(frame["district_code"]) == list("ABC") + list("ABCFGHJ")
    assert list(frame["municipality_ags"]) == [BS] * 3 + [GS] * 7
    assert frame.crs.to_epsg() == 25832


def test_the_goslar_feature_without_a_code_joins_c_and_the_join_is_recorded(built):
    frame, _, _ = built
    district_c = frame.set_index("district_id").loc["gs_district_c"]
    assert district_c.geometry.area == pytest.approx(GS_C.area + GS_OBERSTADT.area)
    assert district_c.geometry.covers(GS_C) and district_c.geometry.covers(GS_OBERSTADT)
    note = district_c["digitising_note"]
    assert "Parkbereich C - Oberstadt" in note and "OBJECTID 4" in note and "OBJECTID 11" in note
    assert "Joined features" in note
    # no other district is a union, and none is named after the uncoded feature
    assert not [text for district_id, text in zip(frame["district_id"], frame["digitising_note"])
                if district_id != "gs_district_c" and "Joined features" in text]
    assert frame[frame["municipality_ags"] == GS]["district_code"].is_unique


def test_an_overlap_within_a_town_is_cut_from_the_later_district_and_recorded(built):
    frame, _, _ = built
    districts = frame.set_index("district_id")
    assert districts.loc["gs_district_g", "geometry"].area == pytest.approx(GS_G.area)
    assert districts.loc["gs_district_h", "geometry"].area == pytest.approx(GS_H.area - GS_OVERLAP_M2)
    assert districts.loc["gs_district_g", "geometry"].intersection(districts.loc["gs_district_h", "geometry"]).area < 1e-6
    assert max(frame.geometry.iloc[i].intersection(frame.geometry.iloc[j]).area
               for i in range(len(frame)) for j in range(i + 1, len(frame))) < 1e-6
    note = districts.loc["gs_district_h", "digitising_note"]
    assert "overlap" in note and "gs_district_g" in note and f"{GS_OVERLAP_M2:.2f} m2" in note
    assert "overlap" not in districts.loc["gs_district_g", "digitising_note"]


def test_every_feature_carries_the_source_wording_of_its_town(built):
    frame, _, _ = built
    for _, row in frame.iterrows():
        wording = BRAUNSCHWEIG_PROVENANCE if row["municipality_ags"] == BS else GOSLAR_PROVENANCE
        assert row["digitising_note"].startswith(wording), row["district_id"]
        assert row["source_url"].startswith("https://")
        assert row["source_date"] == row["digitised_on"] == "2026-10-01"
    assert set(frame[frame["municipality_ags"] == BS]["geometry_source"]) == {pz.DIGITISED_MAP_GEOMETRY_SOURCE}
    assert set(frame[frame["municipality_ags"] == GS]["geometry_source"]) == {pz.FEATURE_SERVICE_GEOMETRY_SOURCE}
    assert frame[frame["municipality_ags"] == GS]["source_url"].str.endswith("/Bewohnerparken/FeatureServer/4").all()
    # the package SHA-256 is part of every note, so a layer names the exact package it came from
    for _, row in frame.iterrows():
        assert "SHA-256" in row["digitising_note"]


# --------------------------------------------------------------------------- curation: refusals


def test_a_package_other_than_the_pinned_one_is_never_read(curation, tmp_path):
    sha256 = _packages(tmp_path)
    sha256["Goslar_Parkdaten"] = "0" * 64
    with pytest.raises(SystemExit, match="Goslar_Parkdaten.zip.*not the recorded"):
        curation.load_district_packages(tmp_path, expected_sha256=sha256)


def test_a_missing_package_is_named(curation, tmp_path):
    sha256 = _packages(tmp_path)
    (tmp_path / "Braunschweig_Parkzonen.zip").unlink()
    with pytest.raises(SystemExit, match="Braunschweig_Parkzonen.zip missing"):
        curation.load_district_packages(tmp_path, expected_sha256=sha256)


@pytest.mark.parametrize("braunschweig, goslar, message", [
    (_braunschweig_layer().iloc[:2], None, "exactly the districts A, B and C"),
    (_braunschweig_layer(estimated_accuracy_m=25), None, "working accuracy of 30 m"),
    (_braunschweig_layer(digitized_on="2026-09-01"), None, "digitized on"),
    (_braunschweig_layer(source_date="2019-03"), None, "2012"),
    (None, _goslar_layer(drop_uncoded=True), "exactly one feature without a code"),
    (None, _goslar_layer(extra_uncoded=True), "exactly one feature without a code"),
    (None, _goslar_layer(edit="2020-01-01T00:00:00+00:00"), "last edit"),
], ids=["bs_district_missing", "bs_accuracy", "bs_digitised_on", "bs_source_date", "gs_no_uncoded_feature",
        "gs_two_uncoded_features", "gs_last_edit"])
def test_attributes_that_contradict_the_recorded_wording_are_refused(curation, tmp_path, braunschweig, goslar, message):
    sha256 = _packages(tmp_path, braunschweig, goslar)
    with pytest.raises(SystemExit, match=message):
        curation.load_district_packages(tmp_path, expected_sha256=sha256)


def test_the_uncoded_feature_must_be_the_oberstadt_part_of_c(curation, tmp_path):
    goslar = _goslar_layer()
    goslar.loc[goslar["Parkbereiche_Kennzeichen"].isna(), "Parkbereiche_Beschreibung"] = "Parkbereich X - Neustadt"
    sha256 = _packages(tmp_path, goslar=goslar)
    with pytest.raises(SystemExit, match="Parkbereich C - Oberstadt"):
        curation.load_district_packages(tmp_path, expected_sha256=sha256)


def test_the_step_never_replaces_an_existing_file_without_being_told_to(curation, tmp_path):
    existing = tmp_path / "parking_resident_districts_2026.geojson"
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="already exists"):
        curation.main(["--municipal-dir", str(tmp_path), "--out", str(existing)])
    assert existing.read_text(encoding="utf-8") == "{}"


# --------------------------------------------------------------------------- curation: the written file


def test_the_written_file_loads_strictly_is_ascii_and_is_deterministic(curation, built, tmp_path):
    frame, _, _ = built
    first, second = tmp_path / "first.geojson", tmp_path / "second.geojson"
    curation.write_district_file(frame, first)
    curation.write_district_file(frame, second)
    assert first.read_bytes() == second.read_bytes()
    text = first.read_text(encoding="utf-8")
    assert not [character for character in text if ord(character) > 127]
    document = json.loads(text)
    assert BRAUNSCHWEIG_PROVENANCE in document["license"] and GOSLAR_PROVENANCE in document["license"]
    assert "dl-de/by-2-0" in document["attribution"] and "Stadtplanung_Geodaten" in document["attribution"]
    loaded = pz.load_resident_districts(first)
    assert list(loaded["district_id"]) == list(frame["district_id"])
    for district_id, area in zip(frame["district_id"], frame.geometry.area):
        assert loaded.set_index("district_id").geometry[district_id].area == pytest.approx(
            area, rel=FILE_ROUNDING, abs=FILE_ROUNDING_ABSOLUTE_M2)


# --------------------------------------------------------------------------- the loader and validator

DISTRICT_TEMPLATE = {"district_code": "A", "municipality_ags": BS, "name": "Bewohnerparkbezirk A",
                     "geometry_source": pz.DIGITISED_MAP_GEOMETRY_SOURCE, "source_url": "https://example.org/map",
                     "source_date": "2026-10-01", "digitised_on": "2026-10-01", "digitising_note": "synthetic test district"}


def _layer(rows, geometries):
    """A district frame in EPSG:25832; ``rows`` override the template per row."""
    return gpd.GeoDataFrame([dict(DISTRICT_TEMPLATE, **row) for row in rows], geometry=geometries, crs=METRIC_CRS)


def _two_districts():
    return _layer([{"district_id": "bs_district_a"}, {"district_id": "bs_district_b", "district_code": "B"}],
                  [box(0, 0, 100, 100), box(100, 0, 200, 100)])


def _write_layer(frame, path):
    frame.to_crs("EPSG:4326").to_file(path, driver="GeoJSON")
    return path


def test_a_valid_layer_loads_in_the_metric_crs_with_its_provenance(tmp_path):
    path = _write_layer(_two_districts(), tmp_path / "districts.geojson")
    districts = pz.load_resident_districts(path)
    assert districts.crs.to_epsg() == 25832 and list(districts["district_id"]) == ["bs_district_a", "bs_district_b"]
    assert list(districts.columns[:len(pz.DISTRICT_PROVENANCE_COLUMNS)]) == list(pz.DISTRICT_PROVENANCE_COLUMNS)
    assert districts.geometry.is_valid.all()


def test_touching_districts_and_a_sliver_below_the_tolerance_are_accepted():
    pz.validate_resident_districts(_two_districts())
    sliver = _layer([{"district_id": "bs_district_a"}, {"district_id": "bs_district_b", "district_code": "B"}],
                    [box(0, 0, 10, 10), box(9.95, 0, 20, 10)])
    pz.validate_resident_districts(sliver)  # 0.5 m2 < OVERLAP_TOLERANCE_M2


@pytest.mark.parametrize("change, message", [
    ({"second_id": "bs_district_a"}, "duplicate district_id"),
    ({"second_code": "A"}, r"duplicate \(municipality_ags, district_code\)"),
    ({"second_geometry": box(50, 0, 150, 100)}, "overlap by more than"),
    ({"second_ags": "03153017", "second_geometry": box(50, 0, 150, 100)}, "overlap by more than"),
    ({"second_geometry": Point(150, 50)}, "not a polygon"),
    ({"second_geometry": Polygon([(100, 0), (200, 100), (200, 0), (100, 100)])}, "invalid geometry"),
    ({"second_id": "Bs District B"}, "lower-case ASCII"),
    ({"second_ags": "3101000"}, "8-digit AGS"),
    ({"second_ags": "09162000"}, "8-digit AGS of the ZGB counties"),
    ({"second_code": ""}, "district_code"),
], ids=["same_id", "same_code_in_a_town", "overlap_in_a_town", "overlap_across_towns", "point", "bow_tie", "bad_id",
        "short_ags", "ags_outside_the_zgb", "empty_code"])
def test_validate_rejects_what_would_misassign_an_activity(change, message):
    frame = _two_districts()
    frame.loc[1, "district_id"] = change.get("second_id", "bs_district_b")
    frame.loc[1, "district_code"] = change.get("second_code", "B")
    frame.loc[1, "municipality_ags"] = change.get("second_ags", BS)
    if "second_geometry" in change:
        frame.loc[1, "geometry"] = change["second_geometry"]
    with pytest.raises(ValueError, match=message):
        pz.validate_resident_districts(frame)


def test_the_same_code_in_two_towns_is_two_districts():
    frame = _layer([{"district_id": "bs_district_a"}, {"district_id": "gs_district_a", "municipality_ags": GS}],
                   [box(0, 0, 100, 100), box(1000, 0, 1100, 100)])
    pz.validate_resident_districts(frame)


def test_an_empty_layer_and_a_foreign_crs_are_rejected():
    with pytest.raises(ValueError, match="no districts"):
        pz.validate_resident_districts(_two_districts().iloc[0:0])
    with pytest.raises(ValueError, match="EPSG:25832"):
        pz.validate_resident_districts(_two_districts().set_crs("EPSG:4326", allow_override=True))


def test_load_never_repairs_an_invalid_polygon(tmp_path):
    frame = _two_districts()
    frame.loc[0, "geometry"] = Polygon([(0, 0), (100, 100), (100, 0), (0, 100)])  # a bow tie
    path = _write_layer(frame, tmp_path / "districts.geojson")
    with pytest.raises(ValueError, match="bs_district_a.*valid as stored"):
        pz.load_resident_districts(path)


@pytest.mark.parametrize("column", [column for column in pz.DISTRICT_PROVENANCE_COLUMNS])
def test_load_requires_every_provenance_field(tmp_path, column):
    path = _write_layer(_two_districts().drop(columns=column), tmp_path / "districts.geojson")
    with pytest.raises(ValueError, match=column):
        pz.load_resident_districts(path)


@pytest.mark.parametrize("column, value, message", [
    ("geometry_source", "traced_by_hand", "geometry_source"),
    ("source_date", "1.10.2026", "ISO date"),
    ("digitised_on", "2026-13-01", "ISO date"),
    ("digitising_note", "", "digitising_note"),
])
def test_load_checks_the_provenance_values(tmp_path, column, value, message):
    frame = _two_districts()
    frame.loc[0, column] = value
    path = _write_layer(frame, tmp_path / "districts.geojson")
    with pytest.raises(ValueError, match=message):
        pz.load_resident_districts(path)


def test_a_missing_file_is_named():
    with pytest.raises(FileNotFoundError, match="parking resident districts missing"):
        pz.load_resident_districts(Path("no_such_directory") / "districts.geojson")


def test_every_district_municipality_needs_a_row_of_the_coverage_register(caplog):
    register = pd.DataFrame({"ags": [BS, "03153017"], "name": ["Braunschweig", "Goslar"],
                             "status": ["zoned", "not_audited"], "source": ["", ""], "note": ["", ""]})
    districts = _layer([{"district_id": "bs_district_a"}, {"district_id": "gs_district_a", "municipality_ags": GS}],
                       [box(0, 0, 100, 100), box(1000, 0, 1100, 100)])
    with caplog.at_level("WARNING", logger=pz.__name__):
        pz.validate_district_municipalities(districts, register)
    # R2 only acts inside a fee zone, so a district of a municipality that is not zoned is inert: said, not silent
    [warning] = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert GS in warning and "not_audited" in warning and BS not in warning
    with pytest.raises(ValueError, match="03153017"):
        pz.validate_district_municipalities(districts, register[register["ags"] == BS])


# --------------------------------------------------------------------------- assignment


def test_assign_districts_returns_the_id_inside_and_nan_outside():
    districts = _two_districts()
    points = gpd.GeoDataFrame({"person_id": [1, 1, 2]}, geometry=[Point(50, 50), Point(500, 500), Point(150, 50)],
                              crs=METRIC_CRS, index=[7, 7, 9])
    assigned = pz.assign_districts(points, districts)
    assert list(assigned.index) == [7, 7, 9]
    assert list(assigned.fillna("-")) == ["bs_district_a", "-", "bs_district_b"]


def test_assign_districts_refuses_what_assign_zones_refuses():
    overlapping = _layer([{"district_id": "bs_district_a"}, {"district_id": "bs_district_b", "district_code": "B"}],
                         [box(0, 0, 100, 100), box(50, 0, 150, 100)])
    with pytest.raises(ValueError, match="more than one district"):
        pz.assign_districts(gpd.GeoDataFrame({"person_id": [1]}, geometry=[Point(75, 50)], crs=METRIC_CRS), overlapping)
    districts = _two_districts()
    with pytest.raises(ValueError, match="without coordinates"):
        pz.assign_districts(gpd.GeoDataFrame({"person_id": [1]}, geometry=[None], crs=METRIC_CRS), districts)
    with pytest.raises(ValueError, match="CRS"):
        pz.assign_districts(gpd.GeoDataFrame({"person_id": [1]}, geometry=[Point(10.5, 52.2)], crs="EPSG:4326"),
                            districts)


# --------------------------------------------------------------------------- the committed layer


def test_the_committed_layer_has_the_districts_the_owner_named_and_loads_strictly():
    districts = pz.load_resident_districts(COMMITTED_LAYER)
    assert list(districts["district_id"]) == BS_DISTRICT_IDS + GS_DISTRICT_IDS
    assert list(districts["municipality_ags"]) == [BS] * 3 + [GS] * 7
    assert list(districts["district_code"]) == list("ABC") + list("ABCFGHJ")
    assert districts.geometry.is_valid.all() and (districts.geometry.area > 0).all()


def test_the_committed_layer_states_its_source_per_district_and_says_where_it_changed_the_source():
    districts = pz.load_resident_districts(COMMITTED_LAYER).set_index("district_id")
    for district_id in BS_DISTRICT_IDS:
        assert districts.loc[district_id, "digitising_note"].startswith(BRAUNSCHWEIG_PROVENANCE)
    for district_id in GS_DISTRICT_IDS:
        assert districts.loc[district_id, "digitising_note"].startswith(GOSLAR_PROVENANCE)
    assert "Parkbereich C - Oberstadt" in districts.loc["gs_district_c", "digitising_note"]
    assert "overlap" in districts.loc["gs_district_h", "digitising_note"]
    document = json.loads(COMMITTED_LAYER.read_text(encoding="utf-8"))
    assert BRAUNSCHWEIG_PROVENANCE in document["license"] and GOSLAR_PROVENANCE in document["license"]
    assert not [character for character in COMMITTED_LAYER.read_text(encoding="utf-8") if ord(character) > 127]


def test_the_committed_districts_match_the_areas_of_the_owners_packages():
    # Areas of the package layers (Braunschweig Qualitaetspruefung.json, Goslar computed_area_m2), m2; the file stores
    # WGS84 with 7 decimals, Goslar G/H lose their 6.3 m2 overlap and C gains the Oberstadt feature.
    districts = pz.load_resident_districts(COMMITTED_LAYER).set_index("district_id").geometry.area
    expected = {"bs_district_a": 649733.4, "bs_district_b": 1252014.0, "bs_district_c": 658503.2,
                "gs_district_a": 85383.479, "gs_district_b": 147233.429, "gs_district_c": 97034.782 + 7332.902,
                "gs_district_f": 150235.082, "gs_district_g": 168088.03, "gs_district_h": 140804.513 - 6.324,
                "gs_district_j": 176056.477}
    for district_id, area in expected.items():
        assert districts[district_id] == pytest.approx(area, rel=2e-5), district_id
