"""Wolfsburg area zones of the curation (parking cost zones v3, spec B2, issue #436).

The 15 mobile-parking areas of the Wolfsburg layer with a recorded maximum stay become zones ``wob_area_<id>``, carved
out of their parent tariff zone: the part of the parent's 50 m access area (ASSUMPTION C-a) that is nearer to the area's
street section than to any other section of the parent (ruling R-B-12). The parent loses exactly that part, so zones never
overlap. The 58 areas without a maximum stay stay in the parent (ASSUMPTION W-max). The tests run on a synthetic layer
(the owner's package is gitignored): carving, the parent that keeps the rest, the raw paid-hours text compared with the
parent window, the stop of an area that lies outside every parent, the count guard and the tariff rows.
"""
from __future__ import annotations

import csv
import importlib
import io
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, Point, box
from shapely.ops import unary_union

from braunschweig.parking import zones as pz

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
METRIC_CRS = "EPSG:25832"
BUFFER_M = 50.0


@pytest.fixture(scope="module")
def waz():
    """The curation step as a module (it imports curation_common and municipal_zones from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        yield importlib.import_module("wolfsburg_area_zones")
    finally:
        sys.path.remove(str(CURATION_DIR))


def _section(source_id, name, fee_zone, geometry, *, maximum=None, window="07:00-18:00, Sa 07:00-14:00", short=False):
    return {"source_id": source_id, "name": name, "fee_zone": fee_zone, "paid_hours_raw": window,
            "max_duration_raw": "NULL" if maximum is None else f"{maximum // 60}h{maximum % 60:02d}m",
            "max_duration_minutes": float("nan") if maximum is None else float(maximum),
            "short_stay_available": short, "geometry": MultiPolygon([geometry])}


def _layer(rows):
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=METRIC_CRS)


def _parent_zone(layer, fee_zone):
    """The 50 m area of the sections of one tariff zone: the parent polygon before the carving."""
    return unary_union([geometry.buffer(BUFFER_M) for geometry in layer.loc[layer["fee_zone"] == fee_zone, "geometry"]])


#: Tariff zone 1: the area A (maximum 30 min, strip y 0..10) and the section B (no maximum, strip y 40..50), 30 m apart:
#: the split line between them lies at y = 25. Tariff zone 3: the areas C and D (maximum 120 min) and the section E,
#: far from zone 1.
SECTION_A = box(0, 0, 100, 10)
SECTION_B = box(0, 40, 100, 50)
SECTION_C = box(5000, 0, 5100, 10)
SECTION_D = box(5000, 100, 5100, 110)
SECTION_E = box(5000, 220, 5100, 230)


@pytest.fixture()
def layer():
    return _layer([
        _section("a1", "Area A", 1, SECTION_A, maximum=30),
        _section("b1", "Section B", 1, SECTION_B),
        _section("c3", "Area C", 3, SECTION_C, maximum=120, window="09:00-18:00"),
        _section("d3", "Area D", 3, SECTION_D, maximum=120, window="09:00-17:00"),
        _section("e3", "Section E", 3, SECTION_E, window="09:00-18:00"),
    ])


@pytest.fixture()
def parents(layer):
    return {1: _parent_zone(layer, 1), 3: _parent_zone(layer, 3)}


def _carve(waz, layer, parents, ranking=(1, 3)):
    """The carving of a synthetic layer: its own counts stand in for the pinned 73 areas with 15 recorded maxima."""
    return waz.carve_area_zones(parents, layer, list(ranking), buffer_m=BUFFER_M, expected_areas=len(layer),
                                expected_recorded=int(layer["max_duration_minutes"].notna().sum()))


# --------------------------------------------------------------------------- the layer and its counts


def test_the_recorded_maximum_areas_are_counted_and_logged(waz, layer, capsys):
    recorded = waz.recorded_maximum_areas(layer, expected_areas=5, expected_recorded=3)
    assert sorted(recorded["source_id"]) == ["a1", "c3", "d3"]
    out = capsys.readouterr().out
    assert "5 areas" in out and "3 with a recorded maximum" in out and "2 without" in out and "W-max" in out


def test_another_count_than_the_recorded_one_stops_with_the_list(waz, layer):
    with pytest.raises(SystemExit) as stop:
        waz.recorded_maximum_areas(layer, expected_areas=5, expected_recorded=15)
    message = str(stop.value)
    assert "3 areas with a recorded maximum" in message and "15" in message
    assert "a1" in message and "Area A" in message and "30" in message and "d3" in message
    with pytest.raises(SystemExit, match="73"):
        waz.recorded_maximum_areas(layer, expected_areas=73, expected_recorded=3)


def test_an_area_id_that_is_not_a_unique_lower_case_token_stops(waz, layer):
    layer.loc[0, "source_id"] = "A 1"
    with pytest.raises(SystemExit, match="source_id"):
        waz.recorded_maximum_areas(layer, expected_areas=5, expected_recorded=3)
    layer.loc[0, "source_id"] = "b1"
    with pytest.raises(SystemExit, match="not unique|duplicate"):
        waz.recorded_maximum_areas(layer, expected_areas=5, expected_recorded=3)


# --------------------------------------------------------------------------- carving


def test_an_area_zone_is_the_part_of_the_parent_nearer_to_its_own_section(waz, layer, parents):
    carved = _carve(waz, layer, parents)
    by_zone = {area["zone_id"]: area for area in carved["areas"]}
    assert set(by_zone) == {"wob_area_a1", "wob_area_c3", "wob_area_d3"}
    zone_a = by_zone["wob_area_a1"]["geometry"]
    assert zone_a.contains(Point(50, 5)) and zone_a.contains(Point(50, 24)) and zone_a.contains(Point(-40, 5))
    assert not zone_a.contains(Point(50, 26)) and not zone_a.contains(Point(50, 45))
    assert by_zone["wob_area_a1"]["parent_key"] == 1 and by_zone["wob_area_c3"]["parent_key"] == 3
    # area C and D of zone 3 share the parent with section E: nearer to C, D and E respectively
    zone_c, zone_d = by_zone["wob_area_c3"]["geometry"], by_zone["wob_area_d3"]["geometry"]
    assert zone_c.contains(Point(5050, 5)) and not zone_c.contains(Point(5050, 100))
    assert zone_d.contains(Point(5050, 105)) and not zone_d.contains(Point(5050, 5))
    assert zone_d.contains(Point(5050, 150)) and not zone_d.contains(Point(5050, 175))


def test_the_parent_keeps_the_rest_and_loses_exactly_the_area_zones(waz, layer, parents):
    carved = _carve(waz, layer, parents)
    remainder = carved["parents"][1]
    area_a = next(area["geometry"] for area in carved["areas"] if area["parent_key"] == 1)
    assert remainder.contains(Point(50, 45)) and remainder.contains(Point(50, 26)) and not remainder.contains(Point(50, 5))
    assert remainder.intersection(area_a).area < 1e-3
    assert remainder.union(area_a).symmetric_difference(parents[1]).area < 1e-3
    # parent 3: the three pieces tile the parent
    pieces = [carved["parents"][3]] + [area["geometry"] for area in carved["areas"] if area["parent_key"] == 3]
    assert sum(piece.area for piece in pieces) == pytest.approx(parents[3].area, abs=1e-3)
    assert unary_union(pieces).symmetric_difference(parents[3]).area < 1e-3
    assert carved["parents"][3].contains(Point(5050, 225))


def test_zones_never_overlap_and_the_other_tariff_zone_is_untouched(waz, layer, parents):
    carved = _carve(waz, layer, parents)
    pieces = list(carved["parents"].values()) + [area["geometry"] for area in carved["areas"]]
    for index, first in enumerate(pieces):
        for second in pieces[index + 1:]:
            assert first.intersection(second).area < 1e-3
    # a parent without a recorded maximum area (here: none) would be returned unchanged; zone 1 and 3 are disjoint
    assert not carved["parents"][1].intersects(carved["parents"][3])


def test_a_parent_without_an_area_with_a_maximum_is_returned_unchanged(waz, layer, parents):
    layer = layer.iloc[[1, 2, 3, 4]]
    carved = _carve(waz, layer, parents)
    assert carved["parents"][1].equals(parents[1])
    assert {area["parent_key"] for area in carved["areas"]} == {3}


def test_an_area_section_overlapping_a_section_without_a_maximum_still_belongs_to_the_area(waz):
    # the overlap goes to the area (ties to the higher rank, as in the split between the tariff zones)
    layer = _layer([_section("a1", "Area A", 1, box(0, 0, 100, 10), maximum=30),
                    _section("b1", "Section B", 1, box(50, 0, 150, 10))])
    parents = {1: _parent_zone(layer, 1)}
    carved = _carve(waz, layer, parents, [1])
    area = carved["areas"][0]["geometry"]
    assert area.contains(Point(75, 5)) and area.contains(Point(25, 5)) and not area.contains(Point(125, 5))
    assert carved["parents"][1].contains(Point(125, 5))


def test_an_area_with_a_maximum_outside_every_parent_stops(waz, layer, parents):
    # the tariff zone of the area is not one of the parents
    stranger = _layer([_section("x9", "Area X", 2, box(9000, 0, 9100, 10), maximum=60)])
    with pytest.raises(SystemExit, match="x9.*tariff zone 2|tariff zone 2.*x9"):
        _carve(waz, pd.concat([layer, stranger], ignore_index=True), parents)
    # the parent polygon does not reach the section of the area
    cut_parent = {1: parents[1].difference(SECTION_A.buffer(BUFFER_M + 1.0)), 3: parents[3]}
    with pytest.raises(SystemExit, match="a1.*outside|outside.*a1"):
        _carve(waz, layer, cut_parent)


def test_a_parent_that_loses_everything_stops(waz):
    layer = _layer([_section("a1", "Area A", 1, box(0, 0, 100, 10), maximum=30)])
    with pytest.raises(SystemExit, match="nothing left"):
        _carve(waz, layer, {1: _parent_zone(layer, 1)}, [1])


# --------------------------------------------------------------------------- tariff rows


def _tariff_frame(waz):
    """Parent rows as the committed table spells them (all text), in the real column order."""
    columns = list(pz.TARIFF_COLUMNS)
    base = {column: "" for column in columns}
    rows = []
    for zone_id, rate, start, end in (("wob_tarifzone_1", "2.00", "7.0", "18.0"), ("wob_tarifzone_3", "1.00", "9.0", "18.0")):
        row = dict(base, zone_id=zone_id, name=f"Wolfsburg Handyparkzonen tariff zone {zone_id[-1]}",
                   municipality_ags="03103000", zone_type="street_paid", workplace_class="03103", hourly_rate_eur=rate,
                   billing_unit_min="30", fee_start_h=start, fee_end_h=end, resident_exempt="false",
                   source_url="https://geoviewer.stadt.wolfsburg.de/default/ows/projects/gpt/parken",
                   source_date="2026-10-01", valid_from="2026-10-01", fee_window_source="municipal_page",
                   notes="parent note")
        rows.append(row)
    rows.append(dict(base, zone_id="bs_zone_ia", name="Braunschweig", municipality_ags="03101000"))
    return pd.DataFrame(rows, columns=columns)


def test_the_tariff_row_of_an_area_copies_its_parent_and_states_its_own_maximum(waz, layer, parents):
    carved = _carve(waz, layer, parents)
    rows = waz.area_tariff_rows(_tariff_frame(waz), carved["areas"], package_sha256="ab" * 32)
    assert list(rows.columns) == list(pz.TARIFF_COLUMNS)
    assert list(rows["zone_id"]) == ["wob_area_a1", "wob_area_c3", "wob_area_d3"]
    parent = _tariff_frame(waz).set_index("zone_id")
    for _, row in rows.iterrows():
        source = parent.loc["wob_tarifzone_1" if row["zone_id"] == "wob_area_a1" else "wob_tarifzone_3"]
        for column in ("municipality_ags", "zone_type", "workplace_class", "hourly_rate_eur", "billing_unit_min",
                       "fee_start_h", "fee_end_h", "resident_exempt", "source_url", "source_date", "valid_from",
                       "fee_window_source"):
            assert row[column] == source[column], (row["zone_id"], column)
        for column in waz.EMPTY_COLUMNS:
            assert row[column] == "", (row["zone_id"], column)
    assert dict(zip(rows["zone_id"], rows["max_stay_min"])) == {"wob_area_a1": "30", "wob_area_c3": "120",
                                                                 "wob_area_d3": "120"}
    first = rows.set_index("zone_id").loc["wob_area_a1"]
    assert first["name"].startswith("Wolfsburg Handyparkzone area ") and "Area A" in first["name"]
    assert "wob_tarifzone_1" in first["notes"] and "ASSUMPTION W-max" in first["notes"] and "ASSUMPTION M3" in first["notes"]
    assert "a1" in first["notes"] and "0h30m" in first["notes"] and "07:00-18:00, Sa 07:00-14:00" in first["notes"]
    assert ("ab" * 32)[:12] in first["notes"] and "mobile_parking_areas" in first["notes"]
    assert first["notes"].isascii() and first["name"].isascii()


def test_the_raw_paid_hours_text_is_compared_with_the_parent_window_and_named_on_difference(waz, layer, parents):
    carved = _carve(waz, layer, parents)
    rows = waz.area_tariff_rows(_tariff_frame(waz), carved["areas"], package_sha256="ab" * 32).set_index("zone_id")
    same, different = rows.loc["wob_area_c3"], rows.loc["wob_area_d3"]
    assert "09:00-18:00" in same["notes"] and "agrees with the window of wob_tarifzone_3" in same["notes"]
    assert "DIFFERS" not in same["notes"]
    # area D states 09:00-17:00 against the parent window 9-18 h: named in the note, the window column is NOT changed
    assert "DIFFERS" in different["notes"] and "09:00-17:00" in different["notes"] and "9-18 h" in different["notes"]
    assert different["fee_start_h"] == "9.0" and different["fee_end_h"] == "18.0"
    unreadable = waz.area_tariff_rows(
        _tariff_frame(waz), [dict(carved["areas"][0], paid_hours_raw="nach Beschilderung")], package_sha256="ab" * 32)
    assert "DIFFERS" in unreadable.iloc[0]["notes"] and "nach Beschilderung" in unreadable.iloc[0]["notes"]


def test_a_missing_parent_row_stops(waz, layer, parents):
    carved = _carve(waz, layer, parents)
    frame = _tariff_frame(waz)
    with pytest.raises(SystemExit, match="wob_tarifzone_3"):
        waz.area_tariff_rows(frame[frame["zone_id"] != "wob_tarifzone_3"], carved["areas"], package_sha256="ab" * 32)


def test_the_rows_are_written_after_the_tariff_zones_and_replace_earlier_area_rows(waz, layer, parents, tmp_path):
    carved = _carve(waz, layer, parents)
    frame = _tariff_frame(waz)
    header = ["# a comment line"]
    lines = [",".join(frame.columns)]
    for _, row in frame.iterrows():
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="").writerow(list(row))
        lines.append(buffer.getvalue())
    path = tmp_path / "tariffs.csv"
    path.write_text("\n".join(header + [lines[0], lines[3], lines[1], lines[2]]) + "\n", encoding="utf-8")
    rows = waz.area_tariff_rows(frame, carved["areas"], package_sha256="ab" * 32)
    waz.write_area_rows(path, rows)
    first = path.read_text(encoding="utf-8")
    loaded = pd.read_csv(path, comment="#", dtype=str, keep_default_na=False)
    assert list(loaded["zone_id"]) == ["bs_zone_ia", "wob_tarifzone_1", "wob_tarifzone_3", "wob_area_a1", "wob_area_c3",
                                       "wob_area_d3"]
    assert first.startswith("# a comment line\n")
    waz.write_area_rows(path, rows)                       # idempotent: the second run replaces, never duplicates
    assert path.read_text(encoding="utf-8") == first


def test_the_committed_tariff_rows_are_checked_against_the_areas(waz, layer, parents):
    carved = _carve(waz, layer, parents)
    frame = _tariff_frame(waz)
    rows = waz.area_tariff_rows(frame, carved["areas"], package_sha256="ab" * 32)
    table = pd.concat([frame, rows], ignore_index=True)
    waz.check_area_tariff_rows(carved["areas"], table)             # agrees: no exception
    wrong = table.copy()
    wrong.loc[wrong["zone_id"] == "wob_area_a1", "max_stay_min"] = "45"
    with pytest.raises(SystemExit, match="wob_area_a1.*max_stay_min"):
        waz.check_area_tariff_rows(carved["areas"], wrong)
    missing = table[table["zone_id"] != "wob_area_c3"]
    with pytest.raises(SystemExit, match="wob_area_c3"):
        waz.check_area_tariff_rows(carved["areas"], missing)
    moved = table.copy()
    moved.loc[moved["zone_id"] == "wob_area_d3", "hourly_rate_eur"] = "9.99"
    with pytest.raises(SystemExit, match="wob_area_d3.*hourly_rate_eur"):
        waz.check_area_tariff_rows(carved["areas"], moved)
