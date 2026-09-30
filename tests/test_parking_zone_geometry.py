"""Rule-based parking zone geometry (lever 1 of parking cost zones v2, issue #436, spec amendment A1).

Synthetic EPSG:25832 street grids pin the corrected construction: R = regulated street segments buffered 25 m and
paid or restricted lots buffered 10 m; fill(R) fills every hole up to 2 ha; F = the street segments with a side
explicitly tagged as free public parking; Z = erode(fill(R), W) minus buffer(F, W), parts below 1 ha dropped. The
numbers of the A1 probe (1.5 km grid, 100 m spacing, 25 m buffers: R = 1.839 km2 with 225 holes and
erode(R, 250 m) = 0; erode(fill(R), 250 m) = (1,550 m - 2 x 250 m)^2) are reproduced here. The fixture
``overpass_regulation_fixture.json`` pins the parsing of the curation aid, not truth.
"""
from __future__ import annotations

import json
import math
import urllib.error
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from braunschweig.parking import zone_geometry as zg

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "parking"
REGULATION_FIXTURE = FIXTURES / "overpass_regulation_fixture.json"
METRIC_CRS = "EPSG:25832"
#: South-west corner of the synthetic street grid in EPSG:25832 (inside Braunschweig, so reprojection is plausible).
ORIGIN = (600_000.0, 5_790_000.0)
PAID = {"highway": "residential", "parking:both": "lane", "parking:both:fee": "yes"}
FREE = {"highway": "residential", "parking:both": "lane", "parking:both:fee": "no"}
MIXED = {"highway": "residential", "parking:left": "lane", "parking:left:fee": "yes", "parking:right": "lane"}
UNTAGGED = {"highway": "residential"}
WALK_M = 250.0


def _line(x0: float, y0: float, x1: float, y1: float) -> LineString:
    return LineString([(ORIGIN[0] + x0, ORIGIN[1] + y0), (ORIGIN[0] + x1, ORIGIN[1] + y1)])


def _ways(rows, crs=METRIC_CRS) -> gpd.GeoDataFrame:
    """Synthetic OSM elements: ``rows`` of (geometry, tags)."""
    return gpd.GeoDataFrame({"osm_id": list(range(1, len(rows) + 1)), "tags": [tags for _, tags in rows]},
                            geometry=[geometry for geometry, _ in rows], crs=crs)


def _grid(extent_m: float = 1500.0, spacing_m: float = 100.0, tags_of=lambda axis, offset: PAID):
    """A square street grid: vertical ('x') and horizontal ('y') streets every ``spacing_m`` over ``extent_m``."""
    rows = []
    for index in range(int(round(extent_m / spacing_m)) + 1):
        offset = index * spacing_m
        rows.append((_line(offset, 0.0, offset, extent_m), tags_of("x", offset)))
        rows.append((_line(0.0, offset, extent_m, offset), tags_of("y", offset)))
    return rows


def _no_lots() -> gpd.GeoDataFrame:
    return zg.classify_lots(_ways([]))


def _parts(geometry):
    return list(getattr(geometry, "geoms", [geometry])) if not geometry.is_empty else []


def _hole_count(geometry) -> int:
    return sum(len(part.interiors) for part in _parts(geometry))


def _core_area(core: gpd.GeoDataFrame) -> float:
    return float(core.geometry.area.sum())


# --------------------------------------------------------------------------- construction (amendment A1)


def test_unfilled_grid_yields_no_core():
    segments = zg.classify_segments(_ways(_grid()))
    regulated = zg.regulated_area(segments, _no_lots())
    # 16 + 16 streets of 1.5 km buffered 25 m enclose 15 x 15 blocks of 50 x 50 m (probe of amendment A1).
    assert regulated.area == pytest.approx(1.839e6, rel=1e-3)
    assert _hole_count(regulated) == 225
    core = zg.unavoidable_core(regulated, zg.free_supply(segments), walk_m=WALK_M)
    assert core.empty
    assert _core_area(core) == 0.0
    assert core.crs.to_epsg() == 25832


def test_filled_grid_yields_core_minus_walk_rim():
    segments = zg.classify_segments(_ways(_grid()))
    filled = zg.fill_holes(zg.regulated_area(segments, _no_lots()), maximum_hole_m2=20_000.0)
    assert _hole_count(filled) == 0
    core = zg.unavoidable_core(filled, zg.free_supply(segments), walk_m=WALK_M, minimum_island_m2=10_000.0)
    assert len(core) == 1
    assert _core_area(core) == pytest.approx((1550.0 - 2 * WALK_M) ** 2, rel=0.01)
    assert core["area_m2"].iloc[0] == pytest.approx(_core_area(core))


def test_single_street_yields_no_zone():
    segments = zg.classify_segments(_ways([(_line(0.0, 0.0, 1500.0, 0.0), PAID)]))
    filled = zg.fill_holes(zg.regulated_area(segments, _no_lots()))
    assert filled.area > 0
    assert zg.unavoidable_core(filled, zg.free_supply(segments), walk_m=WALK_M).empty
    result = zg.build_zone_core(segments, _no_lots())
    assert result.core.empty and result.regulated_segments == 1


@pytest.mark.parametrize("free_tags", [FREE, MIXED], ids=["free_street", "mixed_paid_and_free_street"])
def test_free_street_inside_carves_core(free_tags):
    rows = _grid(tags_of=lambda axis, offset: free_tags if (axis, offset) == ("x", 700.0) else PAID)
    segments = zg.classify_segments(_ways(rows))
    free = zg.free_supply(segments)
    assert len(free) == 1
    filled = zg.fill_holes(zg.regulated_area(segments, _no_lots()))
    core = zg.unavoidable_core(filled, free, walk_m=WALK_M)
    # The eroded square (225 .. 1275 m) loses the band of 2 W around the free street at x = 700 m.
    side_m = 1550.0 - 2 * WALK_M
    assert _core_area(core) == pytest.approx(side_m ** 2 - 2 * WALK_M * side_m, rel=0.01)
    assert len(core) == 2
    free_line = _line(700.0, 0.0, 700.0, 1500.0)
    assert min(part.distance(free_line) for part in core.geometry) >= WALK_M - 0.5


def test_untagged_street_is_no_free_supply():
    mid_block = _line(750.0, 0.0, 750.0, 1500.0)  # not a grid line: runs through the middle of the blocks
    untagged = zg.classify_segments(_ways(_grid() + [(mid_block, UNTAGGED)]))
    assert untagged["way_class"].iloc[-1] == "no_parking_info"
    assert zg.free_supply(untagged).empty
    filled = zg.fill_holes(zg.regulated_area(untagged, _no_lots()))
    core = zg.unavoidable_core(filled, zg.free_supply(untagged), walk_m=WALK_M)
    assert _core_area(core) == pytest.approx((1550.0 - 2 * WALK_M) ** 2, rel=0.01)
    # The mapping gap is reported as tagging completeness instead: 1.5 of 49.5 km of street carry no parking tag.
    assert zg.tagging_completeness(untagged, filled) == pytest.approx(48_000.0 / 49_500.0, rel=1e-3)
    # The same street tagged as free public parking carves the core.
    tagged = zg.classify_segments(_ways(_grid() + [(mid_block, FREE)]))
    carved = zg.unavoidable_core(filled, zg.free_supply(tagged), walk_m=WALK_M)
    assert _core_area(carved) < 0.6 * _core_area(core)


def test_hole_above_maximum_stays_open():
    def loop(inner_side_m: float) -> gpd.GeoDataFrame:
        side = inner_side_m + 50.0  # centreline square: the two 25 m buffers leave an inner hole of inner_side_m
        corners = [(0.0, 0.0), (side, 0.0), (side, side), (0.0, side), (0.0, 0.0)]
        line = LineString([(ORIGIN[0] + x, ORIGIN[1] + y) for x, y in corners])
        return zg.regulated_area(zg.classify_segments(_ways([(line, PAID)])), _no_lots())

    three_hectares = loop(math.sqrt(30_000.0))
    assert _hole_count(three_hectares) == 1
    kept = zg.fill_holes(three_hectares, maximum_hole_m2=20_000.0)
    assert _hole_count(kept) == 1
    assert kept.area == pytest.approx(three_hectares.area)
    one_hectare = loop(100.0)
    filled = zg.fill_holes(one_hectare, maximum_hole_m2=20_000.0)
    assert _hole_count(filled) == 0
    assert filled.area == pytest.approx(one_hectare.area + 10_000.0, rel=1e-6)
    assert zg.hole_counts(three_hectares, 20_000.0) == (0, 1)
    assert zg.hole_counts(one_hectare, 20_000.0) == (1, 0)


def test_island_below_minimum_dropped():
    # A 6 x 6 grid whose filled extent (5 spacings + 50 m) erodes by 250 m to a square of 0.5 ha.
    spacing_m = (2 * WALK_M + math.sqrt(5_000.0) - 50.0) / 5.0
    segments = zg.classify_segments(_ways(_grid(extent_m=5 * spacing_m, spacing_m=spacing_m)))
    filled = zg.fill_holes(zg.regulated_area(segments, _no_lots()))
    kept = zg.unavoidable_core(filled, zg.free_supply(segments), walk_m=WALK_M, minimum_island_m2=4_000.0)
    assert _core_area(kept) == pytest.approx(5_000.0, rel=0.02)
    dropped = zg.unavoidable_core(filled, zg.free_supply(segments), walk_m=WALK_M, minimum_island_m2=10_000.0)
    assert dropped.empty


def test_access_customers_lot_is_neither_supply():
    outline = box(ORIGIN[0], ORIGIN[1], ORIGIN[0] + 60.0, ORIGIN[1] + 40.0)
    lots = zg.classify_lots(_ways([(outline, {"amenity": "parking", "fee": "yes", "access": "customers"}),
                                   (outline, {"amenity": "parking", "access": "customers"})]))
    assert lots["lot_class"].tolist() == ["not_public", "not_public"]
    assert zg.regulated_area(zg.classify_segments(_ways([])), lots).is_empty
    assert zg.free_lots(lots).empty


# --------------------------------------------------------------------------- classification


@pytest.mark.parametrize("tags, expected", [
    # new on-street parking scheme (parking:<side>=*, parking:<side>:*=*)
    ({"highway": "residential", "parking:both": "lane", "parking:both:fee": "yes"}, "paid"),
    # the day-time fee of the new scheme is often fee=no plus fee:conditional=yes @ (hours); the legacy equivalent,
    # parking:condition=ticket with a time interval, is paid as well
    ({"highway": "residential", "parking:both": "lane", "parking:both:fee": "no",
      "parking:both:fee:conditional": "yes @ (Mo-Sa 09:00-20:00)"}, "paid"),
    ({"highway": "residential", "parking:right": "lane", "parking:right:access": "permit"}, "restricted"),
    ({"highway": "residential", "parking:both": "street_side", "parking:both:access": "private"}, "restricted"),
    ({"highway": "tertiary", "parking:both": "no"}, "forbidden"),
    ({"highway": "tertiary", "parking:both": "separate"}, "forbidden"),
    ({"highway": "tertiary", "parking:left": "lane", "parking:left:restriction": "no_stopping"}, "forbidden"),
    ({"highway": "residential", "parking:both": "half_on_kerb"}, "unregulated"),
    ({"highway": "residential", "parking:left": "on_kerb", "parking:left:fee": "no"}, "unregulated"),
    ({"highway": "service", "access": "private", "parking:both": "lane"}, "not_public"),
    ({"highway": "residential", "parking:both": "lane", "parking:both:access": "customers"}, "not_public"),
    ({"highway": "residential", "name": "Untagged"}, "no_parking_info"),
    # legacy scheme (parking:lane:<side>=*, parking:condition:<side>=*)
    ({"highway": "tertiary", "parking:lane:both": "parallel", "parking:condition:both": "ticket"}, "paid"),
    ({"highway": "residential", "parking:lane:right": "parallel", "parking:condition:right": "residents"},
     "restricted"),
    ({"highway": "residential", "parking:lane:both": "diagonal", "parking:condition:both": "disc"}, "restricted"),
    ({"highway": "primary", "parking:lane:both": "no_stopping"}, "forbidden"),
    ({"highway": "residential", "parking:lane:left": "no_parking"}, "forbidden"),
    ({"highway": "residential", "parking:lane:both": "parallel"}, "unregulated"),
    ({"highway": "residential", "parking:lane:both": "perpendicular", "parking:condition:both": "free"},
     "unregulated"),
    ({"highway": "residential", "parking:lane:both": "parallel", "parking:condition:both": "customers"},
     "not_public"),
    ({"highway": "residential", "parking:condition:both:maxstay": "2 h"}, "no_parking_info"),
])
def test_classify_way_new_and_legacy_schema(tags, expected):
    assert zg.classify_way(tags) == expected


def test_classify_way_side_keys_override_both_and_mixed_ways_have_a_free_side():
    tags = {"highway": "residential", "parking:both": "lane", "parking:both:fee": "yes", "parking:right:fee": "no"}
    assert zg.classify_side(tags, "left") == "paid"
    assert zg.classify_side(tags, "right") == "unregulated"
    assert zg.classify_way(tags) == "paid"
    assert zg.has_free_side(tags) and zg.is_regulated(tags)
    assert not zg.has_free_side(PAID) and zg.is_regulated(PAID)
    assert zg.has_free_side(FREE) and not zg.is_regulated(FREE)
    assert not zg.has_free_side(UNTAGGED) and not zg.is_regulated(UNTAGGED)


@pytest.mark.parametrize("tags, lot_class, in_regulated_area, free_lot", [
    ({"amenity": "parking", "fee": "yes"}, "paid", True, False),
    ({"amenity": "parking", "fee": "no", "fee:conditional": "yes @ (Mo-Fr 08:00-18:00)"}, "paid", True, False),
    ({"amenity": "parking", "access": "permit"}, "restricted", True, False),
    ({"amenity": "parking", "fee": "no"}, "unregulated", False, True),
    ({"amenity": "parking"}, "unregulated", False, True),
    ({"amenity": "parking", "fee": "yes", "access": "private"}, "not_public", False, False),
    ({"amenity": "bicycle_parking"}, "no_parking_info", False, False),
])
def test_lot_class_decides_its_supply_role(tags, lot_class, in_regulated_area, free_lot):
    outline = box(ORIGIN[0], ORIGIN[1], ORIGIN[0] + 60.0, ORIGIN[1] + 40.0)
    lots = zg.classify_lots(_ways([(outline, tags)]))
    assert lots["lot_class"].tolist() == [lot_class]
    area = zg.regulated_area(zg.classify_segments(_ways([])), lots)
    assert area.area == (pytest.approx(outline.buffer(10.0).area, rel=1e-6) if in_regulated_area else 0.0)
    assert len(zg.free_lots(lots)) == int(free_lot)


# --------------------------------------------------------------------------- QA helpers and the acceptance rule Q4


def test_tagging_completeness_is_length_weighted_inside_the_filled_area():
    filled = box(ORIGIN[0], ORIGIN[1], ORIGIN[0] + 100.0, ORIGIN[1] + 100.0)
    streets = zg.classify_segments(_ways([(_line(0.0, 50.0, 100.0, 50.0), PAID),
                                          (_line(50.0, 0.0, 50.0, 300.0), UNTAGGED)]))
    # Inside the box: 100 m tagged, 100 m of the 300 m untagged street; the part outside does not count.
    assert zg.tagging_completeness(streets, filled) == pytest.approx(0.5)
    assert math.isnan(zg.tagging_completeness(streets.iloc[0:0], filled))


def test_compare_outlines_reports_overlap_and_largest_outline_distance():
    core = gpd.GeoDataFrame(geometry=[box(ORIGIN[0], ORIGIN[1], ORIGIN[0] + 100.0, ORIGIN[1] + 100.0)],
                            crs=METRIC_CRS)
    reference = gpd.GeoDataFrame(geometry=[box(ORIGIN[0] + 50.0, ORIGIN[1], ORIGIN[0] + 150.0, ORIGIN[1] + 100.0)],
                                 crs=METRIC_CRS)
    result = zg.compare_outlines(core, reference)
    assert result["core_share_inside_reference"] == pytest.approx(0.5)
    assert result["reference_share_covered"] == pytest.approx(0.5)
    assert result["largest_outline_distance_m"] == pytest.approx(50.0, abs=0.5)
    empty = zg.compare_outlines(core.iloc[0:0], reference)
    assert math.isnan(empty["core_share_inside_reference"]) and math.isnan(empty["largest_outline_distance_m"])


@pytest.mark.parametrize("core_area_m2, completeness, accepted", [
    (10_000.0, 0.60, True),
    (250_000.0, 0.95, True),
    (9_999.0, 0.95, False),
    (250_000.0, 0.599, False),
    (0.0, float("nan"), False),
])
def test_acceptance_rule_q4(core_area_m2, completeness, accepted):
    decision = zg.core_acceptance(core_area_m2, completeness)
    assert decision.accepted is accepted
    assert decision.decision == ("accepted" if accepted else "rejected")
    assert (decision.reason == "") is accepted


def test_build_zone_core_reports_counts_and_areas():
    rows = _grid(tags_of=lambda axis, offset: FREE if (axis, offset) == ("x", 700.0) else PAID)
    outline = box(ORIGIN[0] + 300.0, ORIGIN[1] + 300.0, ORIGIN[0] + 340.0, ORIGIN[1] + 330.0)
    lots = zg.classify_lots(_ways([(outline, {"amenity": "parking", "fee": "no"})]))
    result = zg.build_zone_core(zg.classify_segments(_ways(rows)), lots)
    assert result.segment_counts == {"paid": 31, "unregulated": 1}
    assert result.regulated_segments == 31 and result.free_segments == 1 and result.mixed_segments == 0
    assert result.lot_counts == {"unregulated": 1}
    assert result.filled_area_m2 > result.regulated_area_m2 > result.core_area_m2 > 0
    # The free lot (x 300 .. 340 m) lies inside the western core part (x 225 .. 450 m); free lots are no free supply
    # (spec: street parking), they are only reported.
    assert result.free_lots_in_core == 1
    assert result.free_lot_area_in_core_m2 == pytest.approx(40.0 * 30.0, rel=1e-6)
    qa = result.qa()
    assert qa["parameters"] == {"street_buffer_m": 25.0, "lot_buffer_m": 10.0, "maximum_filled_hole_m2": 20_000.0,
                                "walk_m": 250.0, "minimum_island_m2": 10_000.0}
    assert qa["core_parts"] == 2 and qa["tagging_completeness"] == pytest.approx(1.0)
    # erode(fill(R), W) before the free street is subtracted: tells a fill that is too fragmented from a carved core
    assert qa["eroded_filled_area_m2"] == pytest.approx((1550.0 - 2 * WALK_M) ** 2, rel=0.01)
    json.dumps(qa)  # serialisable for <ags>_qa.json


# --------------------------------------------------------------------------- CRS contract


def test_non_metric_input_raises():
    wgs84 = zg.classify_segments(_ways([(LineString([(10.52, 52.26), (10.53, 52.26)]), PAID)], crs="EPSG:4326"))
    with pytest.raises(ValueError, match="EPSG:25832"):
        zg.regulated_area(wgs84, _no_lots())
    with pytest.raises(ValueError, match="EPSG:25832"):
        zg.unavoidable_core(box(0, 0, 1, 1), wgs84)
    with pytest.raises(ValueError, match="EPSG:25832"):
        zg.tagging_completeness(wgs84, box(0, 0, 1, 1))
    with pytest.raises(ValueError, match="EPSG:25832"):
        zg.compare_outlines(wgs84, wgs84)


# --------------------------------------------------------------------------- curation aid (regulation query, offline)


def test_regulation_query_fetches_every_street_and_lot_of_the_box():
    from scripts.build_parking_zones_from_osm import build_regulation_query

    query = build_regulation_query((52.25, 10.50, 52.28, 10.55), timeout_s=180)
    assert query.startswith("[out:json][timeout:180];")
    assert 'way["highway"~"^(primary|secondary|tertiary|unclassified|residential|living_street)$"]' \
           '(52.25,10.5,52.28,10.55);' in query
    assert 'way["highway"][~"^parking:"~"."](52.25,10.5,52.28,10.55);' in query
    assert 'nwr["amenity"="parking"](52.25,10.5,52.28,10.55);' in query
    assert query.rstrip().endswith("out tags geom;")


def test_regulation_cli_writes_the_three_outputs_offline(tmp_path):
    from scripts.build_parking_zones_from_osm import main

    arguments = ["--ags", "03101000", "--bbox", "52.2595,10.5195,52.2660,10.5235", "--out-dir", str(tmp_path),
                 "--regulation", "--offline-response", str(REGULATION_FIXTURE), "--pause-s", "0"]
    assert main(arguments) == 0
    for name in ("03101000_regulated.geojson", "03101000_core.geojson", "03101000_qa.json"):
        assert (tmp_path / name).is_file(), name
    raw = sorted(tmp_path.glob("03101000_regulation_overpass_*.json"))
    assert len(raw) == 1 and raw[0].read_bytes() == REGULATION_FIXTURE.read_bytes()
    assert raw[0].with_suffix(".query.txt").is_file()
    qa = json.loads((tmp_path / "03101000_qa.json").read_text(encoding="utf-8"))
    assert qa["ags"] == "03101000" and qa["osm_timestamp"] == "2026-09-28T00:00:00Z"
    assert qa["raw_response"] == raw[0].name
    assert qa["segment_counts"] == {"paid": 1, "restricted": 1, "forbidden": 1, "unregulated": 1,
                                    "no_parking_info": 1}
    assert qa["lot_counts"] == {"paid": 1, "not_public": 1}
    assert qa["regulated_segments"] == 3 and qa["free_segments"] == 1
    assert qa["core_area_m2"] == 0.0 and qa["decision"] == "rejected"
    assert qa["parameters"]["walk_m"] == 250.0
    regulated = gpd.read_file(tmp_path / "03101000_regulated.geojson")
    assert {"regulated_area", "filled_area", "segment", "lot"} <= set(regulated["kind"])
    classes = {int(row.osm_id): row.element_class for row in regulated.itertuples() if row.kind in ("segment", "lot")}
    assert classes == {501: "paid", 502: "restricted", 503: "forbidden", 504: "unregulated", 505: "no_parking_info",
                       601: "paid", 602: "not_public"}
    assert set(regulated.loc[regulated["kind"] == "lot"].geometry.geom_type) == {"Polygon"}
    core = json.loads((tmp_path / "03101000_core.geojson").read_text(encoding="utf-8"))
    assert core["type"] == "FeatureCollection" and core["features"] == []
    # A second run must not overwrite the raw response it saved (write-through data is never rewritten).
    with pytest.raises(SystemExit, match="exists"):
        main(arguments)


def test_regulation_cli_compares_with_a_reference_outline(tmp_path):
    from scripts.build_parking_zones_from_osm import main

    reference = tmp_path / "reference.geojson"
    outline = box(10.5195, 52.2595, 10.5235, 52.2660)
    gpd.GeoDataFrame({"zone": ["fixture_zone"]}, geometry=[outline], crs="EPSG:4326").to_file(reference,
                                                                                           driver="GeoJSON")
    arguments = ["--ags", "03101000", "--bbox", "52.2595,10.5195,52.2660,10.5235", "--out-dir", str(tmp_path),
                 "--regulation", "--from-raw", str(REGULATION_FIXTURE), "--reference-outline", str(reference),
                 "--walk-m", "150", "--maximum-filled-hole-m2", "10000", "--minimum-island-m2", "5000"]
    assert main(arguments) == 0
    qa = json.loads((tmp_path / "03101000_qa.json").read_text(encoding="utf-8"))
    assert qa["parameters"]["walk_m"] == 150.0 and qa["parameters"]["maximum_filled_hole_m2"] == 10_000.0
    assert qa["parameters"]["minimum_island_m2"] == 5_000.0
    assert qa["reference"]["path"] == str(reference)
    assert qa["reference"]["paid_segments_outside"] == 0
    # four of the five fixture streets carry parking information; all five lie inside the reference box
    assert qa["reference"]["tagging_completeness_inside"] == pytest.approx(0.8, abs=0.01)
    # undefined shares (empty core) are written as JSON null, never as NaN
    assert qa["reference"]["core_share_inside_reference"] is None
    assert "NaN" not in (tmp_path / "03101000_qa.json").read_text(encoding="utf-8")


def test_overpass_retry_backs_off_and_logs_every_failure(tmp_path):
    from scripts.build_parking_zones_from_osm import FAILURE_LOG_NAME, fetch_with_retry

    calls, pauses = [], []

    def flaky(query, **_):
        calls.append(query)
        if len(calls) < 3:
            raise urllib.error.HTTPError("https://overpass-api.de/api/interpreter", 504, "Gateway Timeout", {}, None)
        return b"{}"

    body = fetch_with_retry("q", ags="03153017", out_dir=tmp_path, attempts=3, backoff_s=30.0, fetch=flaky,
                            sleep=pauses.append)
    assert body == b"{}" and len(calls) == 3
    assert pauses == [30.0, 60.0]
    lines = (tmp_path / FAILURE_LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(line.endswith("\t03153017\tHTTP 504") for line in lines)

    def always_down(query, **_):
        raise TimeoutError("timed out")

    with pytest.raises(TimeoutError):
        fetch_with_retry("q", ags="03158037", out_dir=tmp_path, attempts=2, backoff_s=5.0, fetch=always_down,
                         sleep=pauses.append)
    assert len((tmp_path / FAILURE_LOG_NAME).read_text(encoding="utf-8").splitlines()) == 4


def test_regulation_fetch_retries_a_truncated_response(tmp_path, monkeypatch):
    # Overpass answers a server-side timeout with HTTP 200 and a 'remark'; the element list is then incomplete.
    import scripts.build_parking_zones_from_osm as builder

    bodies = [json.dumps({"elements": [], "remark": "runtime error: Query timed out"}).encode("utf-8"),
              json.dumps({"elements": [], "osm3s": {"timestamp_osm_base": "2026-09-30T12:00:00Z"}}).encode("utf-8")]
    monkeypatch.setattr(builder, "fetch_overpass", lambda query, **_: bodies.pop(0))
    body = builder.fetch_with_retry("q", ags="03157006", out_dir=tmp_path, attempts=2, backoff_s=0.0,
                                    fetch=builder.fetch_checked, sleep=lambda seconds: None)
    assert json.loads(body)["osm3s"]["timestamp_osm_base"] == "2026-09-30T12:00:00Z"
    lines = (tmp_path / builder.FAILURE_LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and "OverpassResponseError" in lines[0] and "timed out" in lines[0]
