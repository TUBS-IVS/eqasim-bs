"""Majority rule over the parking supply (parking cost zones v2, spec Amendment B, issue #436, ADR-0139).

Synthetic EPSG:25832 layouts pin the rule: the supply inventory classes B1 (paid, restricted, free, excluded) with the
assumptions B-a (street parking without a fee tag is free), B-b (disc parking is free) and B-c (off-street lots without
an explicit fee tag are excluded); the capacity B2 (the capacity tag first, else 5.5 m per street space, 12.5 m2 per
street-side area space, 25 m2 per lot space times the levels of a multi-storey car park); the paid-share raster B3
(25 m cells, usable spaces within W = 250 m, classified from 50 usable spaces on); the zone rule B4 (paid_share >= 0.5,
smoothing +12.5 m then -12.5 m, islands below 1 ha dropped); the pre-registered validation B5 (recall and precision
>= 70 %) and the release file of B7. Owner decisions 2 and 3 (Task 1c): the default share threshold 0.3 (a POST HOC
change of B-f), H1 (B5 at that share) and the pre-registered holdout check H2 (pooled area-weighted recall and
precision >= 70 %, every town's recall >= 50 %), which both gate the application; the information arms S (street supply
only) and T (payment evidence within 75 m). The numbers pin arithmetic, not truth.
"""
from __future__ import annotations

import gzip
import importlib.util
import io
import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, Point, box

from braunschweig.parking import supply_share as ss
from braunschweig.parking import supply_share_qa as sq
from braunschweig.parking import supply_variants as sv
from tests.restricted_parking_data import parking_data_path

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
METRIC_CRS = "EPSG:25832"
#: South-west corner of the synthetic layouts in EPSG:25832 (inside Braunschweig), a multiple of the 25 m cell size.
X0, Y0 = 600_000.0, 5_790_000.0
STREET = {"highway": "residential"}


def _line(x0: float, y0: float, x1: float, y1: float) -> LineString:
    return LineString([(X0 + x0, Y0 + y0), (X0 + x1, Y0 + y1)])


def _box(x0: float, y0: float, x1: float, y1: float):
    return box(X0 + x0, Y0 + y0, X0 + x1, Y0 + y1)


def _frame(rows, crs=METRIC_CRS, first_id=1) -> gpd.GeoDataFrame:
    """Synthetic OSM elements: ``rows`` of (geometry, tags), osm ids counted from ``first_id``."""
    return gpd.GeoDataFrame({"osm_type": ["way"] * len(rows), "osm_id": list(range(first_id, first_id + len(rows))),
                             "tags": [dict(tags) for _, tags in rows]},
                            geometry=[geometry for geometry, _ in rows], crs=crs)


@pytest.fixture(scope="module")
def assembly():
    """The assembly script as a module (it imports curation_common from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("assemble_parking_zones_supply_share_test",
                                                      CURATION_DIR / "assemble_parking_zones.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


def _empty() -> gpd.GeoDataFrame:
    return _frame([])


def _elements(ways=(), areas=(), lots=()) -> gpd.GeoDataFrame:
    """Supply elements of synthetic ways (osm ids 1..), street-side areas (100..) and off-street lots (200..)."""
    return ss.supply_elements(_frame(list(ways)), _frame(list(areas), first_id=100), _frame(list(lots), first_id=200))


def _street(**side_tags) -> dict:
    return dict(STREET, **{key.replace("__", ":"): value for key, value in side_tags.items()})


def _area(**tags) -> dict:
    return dict({"amenity": "parking", "parking": "street_side"}, **tags)


def _lot(**tags) -> dict:
    return dict({"amenity": "parking", "parking": "surface"}, **tags)


#: One raster cell of 25 m whose centre is (X0 + 112.5, Y0 + 12.5): every element below lies within 250 m of it.
ONE_CELL = (X0 + 100.0, Y0, X0 + 125.0, Y0 + 25.0)


# --------------------------------------------------------------------------- B2: capacity per element


@pytest.mark.parametrize("kind, geometry, tags, heuristic_spaces", [
    # a 110 m street side: 110 / 5.5 = 20 spaces
    ("street_side", _line(0, 0, 110, 0), {"parking:right": "lane", "parking:right:capacity": "7"}, 20.0),
    # a 250 m2 street-side area: 250 / 12.5 = 20 spaces
    ("street_side_area", _box(0, 0, 10, 25), _area(capacity="7"), 20.0),
    # a 1,000 m2 surface lot: 1,000 / 25 = 40 spaces
    ("lot", _box(0, 0, 20, 50), _lot(fee="no", capacity="7"), 40.0),
    # a 1,000 m2 multi-storey car park with three levels: 3 x 40 spaces
    ("lot", _box(0, 0, 20, 50), _lot(parking="multi-storey", fee="yes", capacity="7", **{"building:levels": "3"}),
     120.0),
], ids=["street_side", "street_side_area", "lot", "multi_storey"])
def test_capacity_tag_wins_over_each_heuristic(kind, geometry, tags, heuristic_spaces):
    def element(element_tags):
        rows = [(geometry, dict(STREET, **element_tags) if kind == "street_side" else element_tags)]
        frame = _elements(**{{"street_side": "ways", "street_side_area": "areas", "lot": "lots"}[kind]: rows})
        return frame[frame["capacity_spaces"] > 0].iloc[0] if kind == "street_side" else frame.iloc[0]

    tagged = element(tags)
    assert (tagged["kind"], tagged["capacity_spaces"], tagged["capacity_source"]) == (kind, 7.0, "tag")
    untagged = element({key: value for key, value in tags.items() if not key.endswith("capacity")})
    assert untagged["capacity_source"] == "heuristic"
    assert untagged["capacity_spaces"] == pytest.approx(heuristic_spaces)


def test_each_capacity_heuristic():
    elements = _elements(
        ways=[(_line(0, 0, 55, 0), _street(**{"parking:both": "lane"}))],
        areas=[(_box(0, 100, 10, 125), _area())],
        lots=[(_box(0, 200, 20, 250), _lot(fee="no")),
              (_box(0, 300, 20, 350), _lot(parking="multi-storey", fee="yes", levels="2", **{"building:levels": "5"})),
              (_box(0, 400, 20, 450), _lot(parking="underground", fee="yes", **{"building:levels": "3"})),
              (Point(X0, Y0 + 500), _lot(fee="yes"))])
    sides = elements[elements["kind"] == "street_side"]
    # parking:both counts on each side: 2 x 55 / 5.5 spaces
    assert sorted(sides["side"]) == ["left", "right"] and sides["capacity_spaces"].tolist() == [10.0, 10.0]
    objects = elements[elements["kind"] != "street_side"].set_index("osm_id")
    # street-side area 250 / 12.5; surface lot 1,000 / 25; multi-storey: 'levels' before 'building:levels', 2 x 40;
    # underground: B2 multiplies the levels of a multi-storey car park only; a node has no extent: 0 spaces
    assert objects["capacity_spaces"].to_dict() == {100: 20.0, 200: 40.0, 201: 80.0, 202: 40.0, 203: 0.0}
    assert objects["capacity_basis"].to_dict() == {100: "area", 200: "area", 201: "area_levels", 202: "area",
                                                   203: "no_extent"}


# --------------------------------------------------------------------------- B1: classes and their reasons


@pytest.mark.parametrize("tags, expected", [
    # B-a: street parking without a fee tag is free
    ({"parking:right": "lane"}, ("free", "no_fee_tag")),
    ({"parking:right": "half_on_kerb", "parking:right:fee": "no"}, ("free", "fee_no")),
    # the fallback value of the street parking scheme: parking exists, its position is unknown
    ({"parking:right": "yes"}, ("free", "no_fee_tag")),
    ({"parking:lane:right": "parallel", "parking:condition:right": "free"}, ("free", "fee_no")),
    # B-b: disc parking is free (new and legacy scheme)
    ({"parking:right": "lane", "parking:right:authentication:disc": "yes"}, ("free", "disc")),
    ({"parking:lane:right": "parallel", "parking:condition:right": "disc"}, ("free", "disc")),
    # paid
    ({"parking:right": "lane", "parking:right:fee": "yes"}, ("paid", "fee")),
    ({"parking:right": "lane", "parking:right:fee": "no",
      "parking:right:fee:conditional": "yes @ (Mo-Sa 09:00-18:00)"}, ("paid", "fee")),
    ({"parking:lane:right": "parallel", "parking:condition:right": "ticket;residents"}, ("paid", "fee")),
    # resident permit: restricted; resident-only parking of the street parking scheme (access=private +
    # private=residents, also through parking:both) and the legacy condition residents as well (B1, fix round 1)
    ({"parking:right": "lane", "parking:right:access": "permit"}, ("restricted", "permit")),
    ({"parking:right": "lane", "parking:right:access": "private", "parking:right:private": "residents"},
     ("restricted", "residents")),
    ({"parking:both": "lane", "parking:both:access": "private", "parking:both:private": "residents"},
     ("restricted", "residents")),
    ({"parking:lane:right": "parallel", "parking:condition:right": "residents"}, ("restricted", "residents")),
    # not public: private (any other private value) and customers are excluded, also on the whole way
    ({"parking:right": "lane", "parking:right:access": "private"}, ("excluded", "not_public")),
    ({"parking:right": "lane", "parking:right:access": "private", "parking:right:private": "employees"},
     ("excluded", "not_public")),
    ({"parking:right": "lane", "parking:right:access": "customers"}, ("excluded", "not_public")),
    ({"access": "private", "parking:right": "lane", "parking:right:fee": "yes"}, ("excluded", "not_public")),
    ({"parking:right": "lane", "parking:right:restriction": "loading_only"}, ("excluded", "not_public")),
    # forbidden, mapped separately (the area carries the side), no parking information, a fee value that is no yes/no
    ({"parking:right": "no"}, ("excluded", "forbidden")),
    ({"parking:right": "lane", "parking:right:restriction": "no_stopping"}, ("excluded", "forbidden")),
    ({"parking:lane:right": "no_parking"}, ("excluded", "forbidden")),
    ({"parking:right": "separate", "parking:right:fee": "yes"}, ("excluded", "separate")),
    ({}, ("excluded", "no_parking_info")),
    ({"parking:right": "lane", "parking:right:fee": "Mo-Fr 09:00-18:00"}, ("excluded", "fee_unrecognised")),
])
def test_classify_supply_side(tags, expected):
    assert ss.classify_supply_side(dict(STREET, **tags), "right") == expected


@pytest.mark.parametrize("tags, expected", [
    # B-a on street parking mapped as its own area
    (_area(), ("free", "no_fee_tag")),
    (_area(parking="layby", fee="no"), ("free", "fee_no")),
    (_area(**{"authentication:disc": "yes"}), ("free", "disc")),
    (_area(fee="yes"), ("paid", "fee")),
    # off-street lots: an explicit fee tag decides; B-c: without one the lot is excluded
    (_lot(fee="yes"), ("paid", "fee")),
    (_lot(fee="no", **{"fee:conditional": "yes @ (Mo-Fr 08:00-18:00)"}), ("paid", "fee")),
    (_lot(parking="multi-storey", fee="no"), ("free", "fee_no")),
    (_lot(), ("excluded", "no_fee_tag_offstreet")),
    ({"amenity": "parking"}, ("excluded", "no_fee_tag_offstreet")),
    (_lot(fee="yes", access="private"), ("excluded", "not_public")),
    (_lot(fee="no", access="customers"), ("excluded", "not_public")),
    (_lot(access="permit"), ("restricted", "permit")),
    # resident-only car parks and street-side areas (access=private + private=residents) are restricted (B1)
    (_lot(access="private", private="residents"), ("restricted", "residents")),
    (_area(access="private", private="residents"), ("restricted", "residents")),
    (_lot(access="private", private="employees"), ("excluded", "not_public")),
    (_lot(fee="12 EUR"), ("excluded", "fee_unrecognised")),
    ({"amenity": "bicycle_parking"}, ("excluded", "no_parking_info")),
])
def test_classify_supply_object(tags, expected):
    assert ss.classify_supply_object(tags) == expected


def test_yes_side_reading_is_literal_b_a_and_the_lever_1_reading_is_a_named_counterfactual():
    # parking:<side>=yes (the fallback value of the street parking scheme) is street parking: free without a fee tag
    # (literal B-a, the default). The counterfactual reads it as no parking information, as lever 1 does; a paid,
    # disc or legacy yes side keeps its class under both readings.
    counterfactual = {"yes_position_is_parking": False}
    yes = dict(STREET, **{"parking:right": "yes"})
    assert ss.classify_supply_side(yes, "right") == ("free", "no_fee_tag")
    assert ss.classify_supply_side(yes, "right", **counterfactual) == ("excluded", "no_parking_info")
    for tags, expected in (({"parking:right:fee": "yes"}, ("paid", "fee")),
                           ({"parking:right:authentication:disc": "yes"}, ("free", "disc"))):
        assert ss.classify_supply_side(dict(yes, **tags), "right", **counterfactual) == expected
    legacy = dict(STREET, **{"parking:lane:right": "yes"})
    assert ss.classify_supply_side(legacy, "right", **counterfactual) == ("free", "no_fee_tag")
    ways = [(_line(0, 0, 55, 0), yes)]
    literal = _elements(ways=ways)
    counted = ss.supply_elements(_frame(ways), _empty(), _empty(), **counterfactual)
    assert literal["capacity_spaces"].sum() == pytest.approx(10.0) and counted["capacity_spaces"].sum() == 0.0
    assert ss.summarise_supply(literal)["free_street_sides_position_yes"] == 1
    assert ss.YES_SIDES_COUNTERFACTUAL == "yes_sides_no_information"


def test_street_side_objects_and_offstreet_lots_are_told_apart_and_misplaced_ones_refused():
    objects = _frame([(_box(0, 0, 5, 20), _area()), (_box(0, 50, 30, 80), _lot()),
                      (_box(0, 100, 5, 120), _area(parking="lane"))])
    areas, lots = ss.split_parking_objects(objects)
    assert areas["osm_id"].tolist() == [1, 3] and lots["osm_id"].tolist() == [2]
    with pytest.raises(ValueError, match="street-side"):
        ss.supply_elements(_empty(), lots, _empty())
    with pytest.raises(ValueError, match="off-street"):
        ss.supply_elements(_empty(), _empty(), areas)


def test_fallback_rates_b_a_b_c_and_b_d_are_logged_and_warned_with_their_region(caplog):
    # B-a: 2 x 110 / 5.5 = 40 free street spaces without a fee tag against 10 tagged fee=no; B-c: a 1,000 m2 lot
    # without a fee tag (40 spaces) against a 250 m2 lot with fee=no (10), a private lot is no public capacity;
    # B-d: every capacity is heuristic
    region = "fixture region: box + 250 m + one cell"
    fallback = _elements(ways=[(_line(0, 0, 110, 0), _street(**{"parking:both": "lane"})),
                               (_line(0, 50, 27.5, 50), _street(**{"parking:both": "lane", "parking:both:fee": "no"}))],
                         lots=[(_box(0, 100, 20, 150), _lot()), (_box(0, 200, 10, 225), _lot(fee="no")),
                               (_box(0, 300, 20, 350), _lot(access="private"))])
    summary = ss.summarise_supply(fallback)
    assert summary["free_street_spaces_without_fee_tag_share"] == pytest.approx(0.8)
    assert summary["offstreet_spaces_without_fee_tag_share"] == pytest.approx(0.8)
    assert summary["offstreet_public_lots"] == 2 and summary["heuristic_capacity_share"] == pytest.approx(1.0)
    caplog.clear()
    with caplog.at_level("INFO", logger=ss.__name__):
        ss.log_fallback_rates(summary, label="03101000", region=region)
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 3 and all(region in message and "03101000" in message for message in warnings)
    assert [any(name in message for message in warnings) for name in ("B-a", "B-c", "B-d")] == [True, True, True]
    info = [record.getMessage() for record in caplog.records if record.levelname == "INFO"]
    assert len(info) == 1 and region in info[0] and all(name in info[0] for name in ("B-a", "B-c", "B-d"))
    # tagged capacity, fee tags and no off-street lot without one: the rates are logged, nothing is warned
    tagged = _elements(areas=[(_box(0, 0, 10, 25), _area(fee="no", capacity="20"))],
                       lots=[(_box(0, 100, 20, 150), _lot(fee="yes", capacity="40"))])
    caplog.clear()
    with caplog.at_level("INFO", logger=ss.__name__):
        ss.log_fallback_rates(ss.summarise_supply(tagged), label="03101000", region=region)
    assert [record.levelname for record in caplog.records] == ["INFO"]
    # supply_elements logs the same rates for its region
    caplog.clear()
    with caplog.at_level("INFO", logger=ss.__name__):
        ss.supply_elements(_frame([(_line(0, 0, 110, 0), _street(**{"parking:both": "lane"}))]), _empty(), _empty(),
                           label="03101000", region=region)
    assert any(record.levelname == "WARNING" and "B-a" in record.getMessage() and region in record.getMessage()
               for record in caplog.records)


def test_supply_elements_need_metric_crs():
    wgs84 = _frame([(LineString([(10.52, 52.26), (10.53, 52.26)]), _street(**{"parking:both": "lane"}))],
                   crs="EPSG:4326")
    with pytest.raises(ValueError, match="EPSG:25832"):
        ss.supply_elements(wgs84, _empty(), _empty())


# --------------------------------------------------------------------------- B3: the paid-share raster


def test_block_of_100_paid_and_20_free_street_spaces_gives_five_sixths_and_a_paid_cell():
    # 275 m paid on both sides (2 x 50 spaces) and 55 m free on both sides (2 x 10 spaces), all within 250 m of the cell
    elements = _elements(ways=[(_line(0, 0, 275, 0), _street(**{"parking:both": "lane", "parking:both:fee": "yes"})),
                               (_line(100, 50, 155, 50), _street(**{"parking:both": "lane"}))])
    raster = ss.paid_share_raster(elements, ONE_CELL)
    assert len(raster) == 1
    cell = raster.iloc[0]
    assert (cell["x_m"], cell["y_m"]) == (X0 + 112.5, Y0 + 12.5)
    assert cell["usable_spaces"] == pytest.approx(120.0)
    assert cell["paid_share"] == pytest.approx(5.0 / 6.0)
    assert bool(cell["classified"])
    assert ss.paid_cells(raster, 0.5).tolist() == [True]


@pytest.mark.parametrize("spaces, classified", [(49, False), (50, True)])
def test_fewer_than_50_usable_spaces_leave_the_cell_unclassified(spaces, classified):
    elements = _elements(areas=[(_box(100, 50, 110, 60), _area(fee="yes", capacity=str(spaces)))])
    cell = ss.paid_share_raster(elements, ONE_CELL, minimum_usable_spaces=50).iloc[0]
    assert cell["usable_spaces"] == pytest.approx(spaces)
    assert bool(cell["classified"]) is classified
    assert ss.paid_cells(pd.DataFrame([cell]), 0.5).tolist() == [classified]


def test_paid_share_exactly_one_half_is_paid_and_restricted_counts_as_charged():
    # 30 paid + 30 restricted against 60 free: (30 + 30) / 120 = 0.5 exactly; excluded supply does not count
    elements = _elements(areas=[(_box(100, 40, 110, 50), _area(fee="yes", capacity="30")),
                                (_box(100, 60, 110, 70), _area(access="permit", capacity="30")),
                                (_box(130, 40, 140, 50), _area(capacity="60"))],
                         lots=[(_box(150, 150, 170, 170), _lot(capacity="500")),
                               (_box(200, 150, 220, 170), _lot(fee="yes", access="private", capacity="500"))])
    cell = ss.paid_share_raster(elements, ONE_CELL).iloc[0]
    assert (cell["paid_spaces"], cell["restricted_spaces"], cell["free_spaces"]) == (30.0, 30.0, 60.0)
    assert cell["paid_share"] == 0.5
    assert ss.paid_cells(pd.DataFrame([cell]), 0.5).tolist() == [True]
    assert ss.paid_cells(pd.DataFrame([cell]), 0.5 + 1e-6).tolist() == [False]


def test_supply_beyond_the_walking_distance_does_not_count():
    elements = _elements(areas=[(_box(100, 40, 110, 50), _area(fee="yes", capacity="100")),
                                (_box(100, 300, 110, 310), _area(capacity="1000"))])
    cell = ss.paid_share_raster(elements, ONE_CELL, walk_m=250.0).iloc[0]
    assert (cell["usable_spaces"], cell["paid_share"]) == (100.0, 1.0)
    wider = ss.paid_share_raster(elements, ONE_CELL, walk_m=400.0).iloc[0]
    assert wider["usable_spaces"] == pytest.approx(1100.0)


def test_capacity_is_spread_so_the_radius_sum_is_length_and_area_correct():
    step_m = ss.DISCRETISATION_STEP_M
    # a 1,000 m street side with one space per metre, 150 m from the cell centre: the 250 m disc cuts a 400 m chord
    centre_y = 12.5
    line = _line(112.5 - 500, centre_y - 150, 112.5 + 500, centre_y - 150)
    elements = _elements(ways=[(line, _street(**{"parking:right": "lane", "parking:right:capacity": "1000"}))])
    cell = ss.paid_share_raster(elements, ONE_CELL, minimum_usable_spaces=0).iloc[0]
    assert cell["usable_spaces"] == pytest.approx(400.0, abs=2 * step_m)
    # a 600 m square lot with one space per square metre around the cell centre: the disc holds pi x 250^2 spaces
    lot = _box(112.5 - 300, centre_y - 300, 112.5 + 300, centre_y + 300)
    elements = _elements(lots=[(lot, _lot(fee="no", capacity="360000"))])
    cell = ss.paid_share_raster(elements, ONE_CELL).iloc[0]
    assert cell["usable_spaces"] == pytest.approx(math.pi * 250.0 ** 2, rel=0.005)
    points = ss.discretise_capacity(elements)
    assert points["spaces"].sum() == pytest.approx(360_000.0)
    assert len(points) == (600 / step_m) ** 2
    # the weights follow length and area, not the number of points: a 7.5 x 5 m area is cut into a full and a half
    # piece (20 and 10 of its 30 spaces), a two-part way of 10 m and 30 m keeps 10 and 30 of its 40 spaces per part
    area = ss.discretise_capacity(_elements(areas=[(_box(0, 0, 7.5, 5), _area(capacity="30"))]))
    assert sorted(area["spaces"]) == pytest.approx([10.0, 20.0])
    parts = MultiLineString([_line(0, 0, 10, 0).coords, _line(0, 50, 30, 50).coords])
    spread = ss.discretise_capacity(_elements(ways=[(parts, _street(**{"parking:right": "lane",
                                                                       "parking:right:capacity": "40"}))]))
    assert spread.loc[spread["y_m"] == Y0, "spaces"].sum() == pytest.approx(10.0)
    assert spread.loc[spread["y_m"] == Y0 + 50, "spaces"].sum() == pytest.approx(30.0)


def test_heuristic_capacity_share_of_the_usable_supply():
    elements = _elements(areas=[(_box(100, 40, 110, 50), _area(fee="yes", capacity="60")),
                                # 500 m2 / 12.5 = 40 heuristic spaces
                                (_box(100, 60, 125, 80), _area())])
    cell = ss.paid_share_raster(elements, ONE_CELL).iloc[0]
    assert cell["usable_spaces"] == pytest.approx(100.0)
    assert cell["heuristic_capacity_share"] == pytest.approx(0.4)
    assert cell["paid_share"] == pytest.approx(0.6)


def test_raster_cells_lie_on_the_cell_lattice_and_cover_the_bounds():
    elements = _elements(areas=[(_box(0, 0, 10, 10), _area(capacity="80"))])
    raster = ss.paid_share_raster(elements, (X0 + 3.0, Y0 + 3.0, X0 + 60.0, Y0 + 40.0))
    # cells [0, 25), [25, 50), [50, 75) in x and [0, 25), [25, 50) in y
    assert len(raster) == 6
    assert np.allclose(np.mod(raster["x_m"], 25.0), 12.5) and np.allclose(np.mod(raster["y_m"], 25.0), 12.5)
    assert raster["x_m"].min() == X0 + 12.5 and raster["x_m"].max() == X0 + 62.5
    assert list(raster.columns[:6]) == ["x_m", "y_m", "paid_share", "usable_spaces", "heuristic_capacity_share",
                                        "classified"]
    empty = ss.paid_share_raster(_elements(), ONE_CELL).iloc[0]
    assert empty["usable_spaces"] == 0.0 and math.isnan(empty["paid_share"]) and not empty["classified"]


# --------------------------------------------------------------------------- B4: zone polygons


def _raster(cells, cell_m=25.0) -> pd.DataFrame:
    """A raster frame from ``cells`` = {(column, row): paid_share}; every listed cell is classified."""
    rows = [{"x_m": X0 + (column + 0.5) * cell_m, "y_m": Y0 + (row + 0.5) * cell_m, "paid_share": share,
             "usable_spaces": 100.0, "heuristic_capacity_share": 1.0, "classified": True}
            for (column, row), share in cells.items()]
    frame = pd.DataFrame(rows)
    frame.attrs["cell_m"] = cell_m
    return frame


def test_smoothing_fills_a_one_cell_hole_and_the_island_rule_drops_small_parts():
    block = {(column, row): 0.9 for column in range(5) for row in range(5)}
    block[(2, 2)] = 0.2                                   # one free cell inside the block
    island = {(20 + column, row): 0.9 for column in range(3) for row in range(3)}   # 9 cells = 5,625 m2
    unclassified = _raster({(40, 0): 0.9}).assign(classified=False)
    raster = pd.concat([_raster({**block, **island}), unclassified], ignore_index=True)
    raster.attrs["cell_m"] = 25.0
    polygons = ss.zone_polygons(raster, share_threshold=0.5, smoothing_m=12.5, minimum_island_m2=10_000.0)
    assert len(polygons) == 1 and polygons.crs.to_epsg() == 25832
    part = polygons.geometry.iloc[0]
    # the 5 x 5 block of 125 m: the closing fills the hole of one cell, the outline keeps its convex corners
    assert part.area == pytest.approx(125.0 * 125.0, rel=1e-3)
    assert len(part.interiors) == 0
    assert polygons["area_m2"].iloc[0] == pytest.approx(part.area, abs=0.1)
    kept_small = ss.zone_polygons(raster, minimum_island_m2=5_000.0)
    assert sorted(round(area) for area in kept_small["area_m2"]) == [5625, 15625]


def test_zone_polygons_of_a_raster_without_paid_cells_are_empty():
    polygons = ss.zone_polygons(_raster({(0, 0): 0.2, (1, 0): 0.49}))
    assert polygons.empty and polygons.crs.to_epsg() == 25832


# --------------------------------------------------------------------------- B5: pre-registered validation


def test_recall_and_precision_on_polygons_with_known_overlap():
    legal = gpd.GeoDataFrame({"zone_id": ["ia", "ib"]}, geometry=[_box(0, 0, 100, 100), _box(100, 0, 200, 100)],
                             crs=METRIC_CRS)
    annex = gpd.GeoDataFrame({"zone": ["ia", "ib", "ii"]},
                             geometry=[_box(0, 0, 100, 100), _box(100, 0, 200, 100), _box(200, 0, 220, 100)],
                             crs=METRIC_CRS)
    frame = _box(-50, -50, 300, 300)
    # 150 x 100 m inside Ia/Ib, 20 x 100 m inside zone II, 30 x 100 m inside the frame but outside every annex zone,
    # and a part outside the annex map frame that neither metric sees
    rule = gpd.GeoDataFrame(geometry=[_box(50, 0, 250, 100), _box(400, 0, 500, 100)], crs=METRIC_CRS)
    metrics = ss.validation_metrics(rule, legal, annex, frame)
    assert metrics["recall"] == pytest.approx(15_000.0 / 20_000.0)
    assert metrics["precision"] == pytest.approx(17_000.0 / 20_000.0)
    assert metrics["rule_inside_frame_m2"] == pytest.approx(20_000.0)
    empty = ss.validation_metrics(rule.iloc[0:0], legal, annex, frame)
    assert empty["recall"] == 0.0 and math.isnan(empty["precision"])


@pytest.mark.parametrize("recall, precision, passes", [
    (0.70, 0.70, True),
    (0.95, 0.81, True),
    (0.6999, 0.95, False),
    (0.95, 0.6999, False),
    (0.95, float("nan"), False),
])
def test_b5_gate_refuses_application_below_70_percent(recall, precision, passes):
    assert ss.passes_validation({"recall": recall, "precision": precision}, minimum=0.70) is passes
    assert ss.passes_validation({"recall": recall, "precision": precision}) is passes


def test_default_share_is_the_owners_0_3_and_every_output_name_carries_it():
    # owner decision 2 (a POST HOC change of B-f): the default share threshold is 0.3; the pre-registered 0.5 of
    # Amendment B stays the centre of its B5 arms
    assert ss.DEFAULT_SHARE_THRESHOLD == 0.3 and ss.SupplyShareParameters().share_threshold == 0.3
    assert ss.DEFAULT_SUPPLY_PARAMETERS.tag() == "w250_t0.3_u50_c25_s12.5_i10000"
    assert ss.PRE_REGISTERED_SUPPLY_PARAMETERS.tag() == "w250_t0.5_u50_c25_s12.5_i10000"
    arms = {arm.tag() for arm in ss.SENSITIVITY_ARMS}
    assert arms == {"w150_t0.5_u50_c25_s12.5_i10000", "w400_t0.5_u50_c25_s12.5_i10000",
                    "w250_t0.3_u50_c25_s12.5_i10000", "w250_t0.7_u50_c25_s12.5_i10000"}
    # B is the default arm; the information arms of owner decisions 2 and 3 (S at 0.3 and 0.5, T at 0.5 and 0.3, S+T at
    # 0.5 and 0.3) carry their share and their inventory variant in the name, so no arm shares a file with B; T names
    # its evidence definition of ruling R-T1c-a (parking elements only), never a file of the superseded _payment75m
    assert sv.DEFAULT_ARM.tag() == ss.DEFAULT_SUPPLY_PARAMETERS.tag()
    assert [arm.tag() for arm in sv.VARIANT_ARMS] == [
        "w250_t0.3_u50_c25_s12.5_i10000_streetonly", "w250_t0.5_u50_c25_s12.5_i10000_streetonly",
        "w250_t0.5_u50_c25_s12.5_i10000_parkingpayment75m", "w250_t0.3_u50_c25_s12.5_i10000_parkingpayment75m",
        "w250_t0.5_u50_c25_s12.5_i10000_streetonly_parkingpayment75m",
        "w250_t0.3_u50_c25_s12.5_i10000_streetonly_parkingpayment75m"]
    with pytest.raises(ValueError, match="share_threshold"):
        ss.SupplyShareParameters(share_threshold=1.5)
    with pytest.raises(ValueError, match="payment_evidence_m"):
        sv.SupplyVariant(payment_evidence_m=0.0)


# --------------------------------------------------------------------------- H2: the pre-registered holdout check


def _references(rows) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"zone_id": [zone_id for zone_id, _ in rows]}, geometry=[geometry for _, geometry in rows],
                            crs=METRIC_CRS)


def test_holdout_recall_and_precision_on_synthetic_references_with_known_overlap():
    # town P (a precision town): a 100 x 100 m reference; the rule covers 60 x 100 m of it, 40 x 100 m more inside the
    # query box and 50 x 100 m beyond the box (outside the precision frame)
    query_box = _box(0, 0, 1000, 1000)
    rule_p = gpd.GeoDataFrame(geometry=[_box(140, 100, 240, 200), _box(1000, 100, 1050, 200)], crs=METRIC_CRS)
    town_p = ss.holdout_town_metrics(rule_p, _references([("p_ref", _box(100, 100, 200, 200))]), query_box=query_box)
    assert town_p["recall"] == pytest.approx(0.6) and town_p["precision"] == pytest.approx(0.6)
    assert (town_p["reference_area_m2"], town_p["rule_inside_reference_m2"], town_p["rule_inside_query_box_m2"]) == \
        (pytest.approx(10_000.0), pytest.approx(6_000.0), pytest.approx(10_000.0))
    # town R (recall only, like the Braunschweig street lists): two references of 100 x 20 m, one covered completely
    rule_r = gpd.GeoDataFrame(geometry=[_box(-10, -10, 110, 30)], crs=METRIC_CRS)
    town_r = ss.holdout_town_metrics(rule_r, _references([("r1", _box(0, 0, 100, 20)), ("r2", _box(0, 100, 100, 120))]))
    assert town_r["recall"] == pytest.approx(0.5) and town_r["precision"] is None
    assert town_r["references"]["r1"]["rule_inside_m2"] == pytest.approx(2_000.0)
    # pooled recall is area-weighted over every reference (8,000 / 14,000, not the mean 0.55 of the town recalls);
    # pooled precision over the precision towns only; the per-town minimum is the smallest town recall
    zones = {"P": ("p_ref",), "R": ("r1", "r2")}
    pooled = ss.holdout_pooled_metrics({"P": town_p, "R": town_r}, reference_zones=zones, precision_towns=("P",))
    assert pooled["pooled_recall"] == pytest.approx(8_000.0 / 14_000.0)
    assert pooled["pooled_precision"] == pytest.approx(0.6) and pooled["minimum_town_recall"] == pytest.approx(0.5)
    assert pooled["passes"] is False
    # a missing town leaves H2 undefined; a precision town's reference must lie inside its frame
    with pytest.raises(ValueError, match="R"):
        ss.holdout_pooled_metrics({"P": town_p}, reference_zones=zones, precision_towns=("P",))
    with pytest.raises(ValueError, match="query box"):
        ss.holdout_town_metrics(rule_p, _references([("p_ref", _box(950, 100, 1050, 200))]), query_box=query_box)


@pytest.mark.parametrize("pooled_recall, pooled_precision, minimum_town_recall, passes", [
    (0.70, 0.70, 0.50, True),
    (0.6999, 0.95, 0.95, False),
    (0.95, 0.6999, 0.95, False),
    (0.95, 0.95, 0.4999, False),
    (0.95, float("nan"), 0.95, False),
])
def test_h2_gate_needs_pooled_recall_and_precision_of_70_percent_and_every_town_at_50(
        pooled_recall, pooled_precision, minimum_town_recall, passes):
    metrics = {"pooled_recall": pooled_recall, "pooled_precision": pooled_precision,
               "minimum_town_recall": minimum_town_recall}
    assert ss.passes_holdout(metrics) is passes


@pytest.mark.parametrize("h1_recall, h2_pooled_precision, passes", [
    (0.70, 0.70, True),       # both at their bounds
    (0.6999, 0.95, False),    # H1 fails, H2 passes
    (0.95, 0.6999, False),    # H1 passes, H2 fails
    (0.6999, 0.6999, False),  # both fail
])
def test_application_gate_needs_h1_and_h2(h1_recall, h2_pooled_precision, passes):
    h1 = {"recall": h1_recall, "precision": 0.9}
    h2 = {"pooled_recall": 0.9, "pooled_precision": h2_pooled_precision, "minimum_town_recall": 0.9}
    assert ss.passes_application_gate(h1, h2) is passes


# --------------------------------------------------------------------------- variants S and T (information arms)


def test_variant_s_drops_offstreet_lots_and_garages_from_the_inventory():
    # 2 x 20 free street spaces and 30 paid street-side spaces, a paid surface lot (400) and a paid garage (300)
    elements = _elements(ways=[(_line(0, 0, 110, 0), _street(**{"parking:both": "lane"}))],
                         areas=[(_box(100, 40, 110, 50), _area(fee="yes", capacity="30"))],
                         lots=[(_box(150, 150, 170, 170), _lot(fee="yes", capacity="400")),
                               (_box(0, 150, 20, 170), _lot(parking="multi-storey", fee="yes", capacity="300"))])
    street = sv.variant_elements(elements, sv.STREET_SUPPLY_ONLY)
    assert sorted(street["kind"]) == ["street_side", "street_side", "street_side_area"]
    assert ss.summarise_supply(street)["elements_by_kind_and_class"]["lot"] == {name: 0 for name in ss.SUPPLY_CLASSES}
    full_cell = ss.paid_share_raster(elements, ONE_CELL).iloc[0]
    street_cell = ss.paid_share_raster(street, ONE_CELL).iloc[0]
    assert full_cell["paid_share"] == pytest.approx(730.0 / 770.0)
    assert street_cell["usable_spaces"] == pytest.approx(70.0) and street_cell["paid_share"] == pytest.approx(3.0 / 7.0)
    # the full inventory is B itself
    assert sv.variant_elements(elements, sv.FULL_INVENTORY)["element_id"].tolist() == elements["element_id"].tolist()


def _osm_objects(rows) -> gpd.GeoDataFrame:
    """OSM elements of any type: ``rows`` of (osm_type, osm_id, geometry, tags)."""
    return gpd.GeoDataFrame({"osm_type": [row[0] for row in rows], "osm_id": [row[1] for row in rows],
                             "tags": [dict(row[3]) for row in rows]}, geometry=[row[2] for row in rows], crs=METRIC_CRS)


@pytest.mark.parametrize("osm_type, tags, evidence", [
    # ruling R-T1c-a: only parking elements with an app-payment key (payment:app*, payment:mobile*, a named
    # parking-app key) count; phone wallets never count, non-parking elements never count
    ("node", {"shop": "bakery", "payment:apple_pay": "yes"}, False),
    ("node", {"shop": "supermarket", "payment:app": "yes"}, False),
    ("node", {"amenity": "charging_station", "payment:app": "yes"}, False),
    ("way", {"amenity": "parking", "parking": "surface", "payment:app": "yes"}, True),
    ("way", {"amenity": "parking", "parking": "surface", "payment:app": "no"}, False),
    ("way", {"amenity": "parking", "parking": "surface", "payment:apple_pay": "yes"}, False),
    ("way", {"amenity": "parking", "parking": "street_side", "payment:easypark": "yes"}, True),
    ("node", {"amenity": "parking", "payment:mobile_phone": "yes"}, True),
    ("way", {"highway": "residential", "parking:right": "lane", "payment:app": "yes"}, True),
    ("way", {"highway": "residential", "payment:app": "yes"}, False),
    # a parking payment device, also when it is no node (as ticket machine only nodes count)
    ("way", {"amenity": "vending_machine", "vending": "parking_tickets", "payment:app": "yes"}, True),
    ("way", {"amenity": "vending_machine", "vending": "parking_tickets"}, False),
    ("node", {"amenity": "vending_machine", "vending": "parking_tickets"}, True),
], ids=["wallet_on_shop", "app_on_shop", "app_on_charging_station", "app_on_parking_area", "app_no_on_parking_area",
        "wallet_on_parking_area", "named_app_on_street_side_area", "mobile_on_parking_node", "app_on_parking_street",
        "app_on_street_without_parking_tags", "app_on_ticket_machine_way", "ticket_machine_way", "ticket_machine_node"])
def test_app_payment_evidence_needs_a_parking_element_and_never_a_phone_wallet(osm_type, tags, evidence):
    geometry = Point(X0, Y0) if osm_type == "node" else _line(0, 0, 50, 0) if "highway" in tags else _box(0, 0, 10, 10)
    assert len(sv.payment_evidence(_osm_objects([(osm_type, 7, geometry, tags)]))) == int(evidence)


def test_variant_t_payment_evidence_turns_nearby_untagged_street_parking_paid():
    machine = {"amenity": "vending_machine", "vending": "parking_tickets;public_transport_tickets"}
    ways = [(_line(50, 0, 50, 400), _street(**{"parking:both": "lane"})),   # 1: 50 m away at its end, centroid 206 m
            (_line(-300, -80, 0, -80), _street(**{"parking:both": "lane"})),  # 2: 80 m from the machine
            (_line(-100, 10, -20, 10), _street(**{"parking:both": "lane", "parking:both:fee": "no"})),  # 3: fee=no
            (_line(-100, -10, -20, -10), _street(**{"parking:both": "lane",
                                                   "parking:both:authentication:disc": "yes"})),  # 4: disc, B-b
            (_line(500, 50, 520, 50), _street(**{"parking:right": "lane"})),  # 5: 30 m from the fee=no lot 201
            (_line(700, 0, 750, 0), _street(**{"parking:right": "lane"}))]  # 6: 20 m from a shop with app tags
    areas = [(_box(0, 60, 10, 70), _area())]                                 # 100: 60 m from the machine
    lots = [(_box(300, 300, 320, 320), _lot(**{"payment:app": "yes"})),       # 200: app payment, no fee tag
            (_box(500, 0, 520, 20), _lot(fee="no", **{"payment:app": "yes"})),  # 201: app payment and fee=no
            (_box(900, 900, 920, 920), _lot())]                               # 202: B-c
    elements = _elements(ways=ways, areas=areas, lots=lots)
    osm = _osm_objects([("node", 1, Point(X0, Y0), machine),
                        ("node", 2, Point(X0 - 50, Y0 - 60), {"amenity": "vending_machine", "vending": "cigarettes"}),
                        ("node", 3, Point(X0 - 100, Y0 - 70), {"shop": "bakery", "payment:app": "no"}),
                        # no parking element, and a phone wallet: no evidence (ruling R-T1c-a)
                        ("node", 4, Point(X0 + 725, Y0 + 20), {"shop": "bakery", "payment:app": "yes",
                                                               "payment:apple_pay": "yes"}),
                        ("way", 200, _box(300, 300, 320, 320), lots[0][1]), ("way", 201, _box(500, 0, 520, 20), lots[1][1])])
    assert sv.is_parking_ticket_machine(machine) and not sv.is_parking_ticket_machine(osm["tags"].iloc[1])
    evidence = sv.payment_evidence(osm)
    assert sorted(zip(evidence["osm_type"], evidence["osm_id"])) == [("node", 1), ("way", 200), ("way", 201)]
    paid = sv.variant_elements(elements, sv.PAYMENT_EVIDENCE, evidence).set_index("element_id")
    expected = {"way/1:left": ("paid", "payment_evidence", "ticket_machine"),
                "way/1:right": ("paid", "payment_evidence", "ticket_machine"),
                "way/2:left": ("free", "no_fee_tag", ""), "way/3:left": ("free", "fee_no", ""),
                "way/4:left": ("free", "disc", ""), "way/5:right": ("paid", "payment_evidence", "app_payment_parking"),
                "way/6:right": ("free", "no_fee_tag", ""),
                "way/100": ("paid", "payment_evidence", "ticket_machine"),
                "way/200": ("paid", "app_payment_tag", "own_app_payment_tag"), "way/201": ("free", "fee_no", ""),
                "way/202": ("excluded", "no_fee_tag_offstreet", "")}
    assert {key: tuple(paid.loc[key, ["class", "reason", "payment_evidence"]]) for key in expected} == expected
    # the machines add no capacity: the same elements with the same spaces; B itself is untouched
    assert paid.index.tolist() == elements["element_id"].tolist()
    assert np.allclose(paid["capacity_spaces"], elements["capacity_spaces"])
    assert (elements.set_index("element_id").loc["way/1:left", "class"], len(elements)) == ("free", len(paid))
    with pytest.raises(ValueError, match="payment evidence"):
        sv.variant_elements(elements, sv.PAYMENT_EVIDENCE)


# --------------------------------------------------------------------------- B7: the release file


def test_release_file_holds_the_classified_cells_as_gzip_csv_in_epsg_25832(assembly, tmp_path):
    # 80 paid spaces at x 0..10 m and 90 free ones at x 300..310 m: the cells beyond 250 m of both stay unclassified
    elements = _elements(areas=[(_box(0, 0, 10, 10), _area(fee="yes", capacity="80")),
                                (_box(300, 0, 310, 10), _area(capacity="90"))])
    raster = ss.paid_share_raster(elements, (X0, Y0, X0 + 25.0 * 24, Y0 + 25.0))
    release = sq.release_frame(raster, "03153017")
    assert list(release.columns) == list(sq.PAID_SHARE_RELEASE_COLUMNS)
    assert 0 < len(release) < len(raster) and (release["usable_spaces"] >= 50).all()
    path = tmp_path / "parking_paid_share_2026.csv.gz"
    assembly.write_paid_share_release(path, release, provenance=["fixture provenance line"])
    data = path.read_bytes()
    assert data[:2] == b"\x1f\x8b"
    text = gzip.decompress(data).decode("ascii")
    header = [line for line in text.splitlines() if line.startswith("#")]
    assert any("EPSG:25832" in line for line in header) and "# fixture provenance line" in header
    for column in sq.PAID_SHARE_RELEASE_COLUMNS:
        assert any(line.startswith(f"# {column}: ") for line in header), column
    loaded = sq.load_paid_share_release(path)
    assert list(loaded.columns) == list(sq.PAID_SHARE_RELEASE_COLUMNS) and len(loaded) == len(release)
    assert loaded["municipality_ags"].eq("03153017").all()
    assert np.allclose(loaded["x_m"], release["x_m"]) and np.allclose(loaded["paid_share"], release["paid_share"])
    # byte-reproducible: gzip without a time stamp
    again = tmp_path / "again.csv.gz"
    assembly.write_paid_share_release(again, release, provenance=["fixture provenance line"])
    assert again.read_bytes() == data
    with pytest.raises(ValueError, match="paid_share"):
        sq.validate_paid_share_release(release.assign(paid_share=1.5))


# --------------------------------------------------------------------------- polygons of the rule (zones.py)


ZONE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "parking" / "parking_zones_fixture.geojson"
SNAPSHOT = "2026-09-29T20:22:51Z"


@pytest.mark.parametrize("changes, message", [
    ({}, None),
    ({"supply_walk_m": None}, "supply_walk_m required for geometry_source osm_supply_majority"),
    ({"paid_share_threshold": 1.5}, "paid_share_threshold must be a share in"),
    ({"minimum_usable_spaces": -1.0}, "minimum_usable_spaces must be"),
    ({"osm_timestamp": "2026-09-29"}, "osm_timestamp must be"),
    ({"osm_timestamp": None}, "osm_timestamp required for geometry_source osm_supply_majority"),
    ({"other_walk": 250.0}, "supply_walk_m only applies to geometry_source osm_supply_majority"),
], ids=["complete", "no_walk", "threshold_above_one", "negative_supply", "date_only", "no_snapshot",
        "walk_on_other_source"])
def test_osm_supply_majority_zones_need_their_rule_provenance(tmp_path, changes, message):
    from braunschweig.parking import zones as pz

    zones = gpd.read_file(ZONE_FIXTURE)
    rule = (zones["zone_id"] == "fx_bs_ib").to_numpy()
    values = {"supply_walk_m": 250.0, "paid_share_threshold": 0.5, "minimum_usable_spaces": 50.0,
              "osm_timestamp": SNAPSHOT}
    values.update({key: value for key, value in changes.items() if key != "other_walk"})
    zones.loc[rule, "geometry_source"] = pz.SUPPLY_MAJORITY_GEOMETRY_SOURCE
    for column, value in values.items():
        zones[column] = [value if flag else (changes.get("other_walk") if column == "supply_walk_m" else None)
                         for flag in rule]
    path = tmp_path / "zones.geojson"
    zones.to_file(path, driver="GeoJSON")
    if message is None:
        loaded = pz.load_zone_polygons(path).set_index("zone_id")
        assert loaded.loc["fx_bs_ib", "supply_walk_m"] == 250.0 and loaded.loc["fx_bs_ib", "osm_timestamp"] == SNAPSHOT
        assert pz.SUPPLY_MAJORITY_GEOMETRY_SOURCE in pz.GEOMETRY_SOURCES
    else:
        with pytest.raises(ValueError, match=message):
            pz.load_zone_polygons(path)


# --------------------------------------------------------------------------- the curation aid (supply-share mode)


FIXTURES = REPO_ROOT / "tests" / "fixtures" / "parking"
OSM_FIXTURE = FIXTURES / "osm_supply_fixture.osm"
REGULATION_FIXTURE = FIXTURES / "overpass_regulation_fixture.json"
#: south, west, north, east of the Overpass regulation fixture (the same elements are in the OSM fixture)
FIXTURE_BBOX = (52.2595, 10.5195, 52.2660, 10.5235)
TAG = ss.DEFAULT_SUPPLY_PARAMETERS.tag()


def test_supply_inventory_reads_the_parking_features_of_the_box_with_the_gdal_osm_driver():
    from scripts.build_parking_zones_from_osm import inventory_evidence, inventory_frames, read_supply_inventory

    layers, seconds = read_supply_inventory(OSM_FIXTURE, FIXTURE_BBOX)
    assert set(seconds) == {"points", "lines", "multipolygons"}
    ids = {layer: sorted((row.osm_type, int(row.osm_id)) for row in frame.itertuples())
           for layer, frame in layers.items()}
    # the footway without parking key, the street outside the box, the building and the cigarette machine 42 are not
    # read; the ticket machine 40 and the node 41 with payment:app=no pass the layer filter of the payment evidence
    assert ids == {"points": [("node", 29), ("node", 40), ("node", 41)],
                   "lines": [("way", 501), ("way", 502), ("way", 503), ("way", 504), ("way", 505), ("way", 507),
                             ("way", 509)],
                   "multipolygons": [("relation", 800), ("way", 601), ("way", 602), ("way", 603)]}
    tags = {int(row.osm_id): row.tags for frame in layers.values() for row in frame.itertuples()}
    # other_tags (HSTORE) with an escaped quote and a comma inside a value, merged with the attribute columns
    assert tags[507] == {"highway": "service", "parking:right": "lane", "description": 'Fixture "quoted", with a comma'}
    assert tags[800]["building:levels"] == "3" and tags[800]["amenity"] == "parking"
    ways, objects = inventory_frames(layers)
    assert ways.crs.to_epsg() == 25832 and objects.crs.to_epsg() == 25832
    assert sorted(ways["osm_id"]) == [501, 502, 503, 504, 505, 507, 509]
    assert sorted(objects["osm_id"]) == [29, 601, 602, 603, 800]
    # variant T: the ticket machine and the street-side area with payment:app=yes; payment:app=no is no evidence
    evidence = inventory_evidence(layers)
    assert evidence.crs.to_epsg() == 25832
    assert sorted(zip(evidence["osm_type"], evidence["osm_id"])) == [("node", 40), ("way", 603)]


def test_extract_digests_are_checked_against_the_geofabrik_md5_before_use(tmp_path):
    import hashlib

    from scripts.build_parking_zones_from_osm import check_extract_md5

    extract = tmp_path / "town-260929.osm.pbf"
    extract.write_bytes(b"synthetic extract")
    md5 = hashlib.md5(b"synthetic extract").hexdigest()
    checksum = tmp_path / "town-260929.osm.pbf.md5"
    checksum.write_text(f"{md5}  town-260929.osm.pbf\n", encoding="ascii")
    digests = check_extract_md5(extract)
    assert digests == {"file": "town-260929.osm.pbf", "bytes": 17, "md5": md5,
                       "sha256": hashlib.sha256(b"synthetic extract").hexdigest(), "md5_file": checksum.name}
    checksum.write_text(f"{'0' * 32}  town-260929.osm.pbf\n", encoding="ascii")
    with pytest.raises(SystemExit, match="MD5"):
        check_extract_md5(extract)
    checksum.write_text(f"{md5}  other-260929.osm.pbf\n", encoding="ascii")
    with pytest.raises(SystemExit, match="names"):
        check_extract_md5(extract)


def _protobuf(*fields) -> bytes:
    """Protobuf wire format of (field number, value): int -> varint, bytes/str -> length-delimited."""
    def varint(number):
        out = bytearray()
        while True:
            byte, number = number & 0x7F, number >> 7
            out.append(byte | (0x80 if number else 0))
            if not number:
                return bytes(out)

    out = b""
    for number, value in fields:
        if isinstance(value, int):
            out += varint(number << 3) + varint(value)
        else:
            value = value.encode("ascii") if isinstance(value, str) else value
            out += varint(number << 3 | 2) + varint(len(value)) + value
    return out


def test_pbf_header_gives_the_replication_timestamp_of_the_extract(tmp_path):
    import struct
    import zlib

    from scripts.build_parking_zones_from_osm import pbf_header

    block = _protobuf((4, "OsmSchema-V0.6"), (4, "DenseNodes"), (16, "osmium/1.16.0"), (32, 1790713371), (33, 4917),
                      (34, "https://download.geofabrik.de/europe/germany/niedersachsen-updates"))
    blob = _protobuf((2, len(block)), (3, zlib.compress(block)))
    blob_header = _protobuf((1, "OSMHeader"), (3, len(blob)))
    path = tmp_path / "fixture.osm.pbf"
    path.write_bytes(struct.pack(">I", len(blob_header)) + blob_header + blob + b"\x00" * 16)
    header = pbf_header(path)
    assert header["osmosis_replication_timestamp"] == "2026-09-29T20:22:51Z"
    assert header["osmosis_replication_sequence_number"] == 4917
    assert header["required_features"] == ["OsmSchema-V0.6", "DenseNodes"]
    assert header["writingprogram"] == "osmium/1.16.0"
    data_header = _protobuf((1, "OSMData"), (3, len(blob)))
    path.write_bytes(struct.pack(">I", len(data_header)) + data_header + blob)
    with pytest.raises(ValueError, match="OSMHeader"):
        pbf_header(path)


def _b5_inputs(directory: Path) -> list:
    """Legal zones, annex zones and an annex map (affine + image) around the fixture box, as CLI arguments."""
    from PIL import Image

    query = gpd.GeoSeries([box(FIXTURE_BBOX[1], FIXTURE_BBOX[0], FIXTURE_BBOX[3], FIXTURE_BBOX[2])],
                          crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    minx, miny, maxx, maxy = query.bounds
    legal = gpd.GeoDataFrame({"zone_id": ["bs_zone_ia", "bs_zone_ib", "bs_other"], "geometry_source": "fixture"},
                             geometry=[box(minx, miny, maxx, miny + 300), box(minx, miny + 300, maxx, miny + 500),
                                       box(minx, miny + 600, maxx, maxy)], crs=METRIC_CRS)
    annex = gpd.GeoDataFrame({"zone": ["ia", "ib", "ii"]},
                             geometry=[box(minx, miny, maxx, miny + 300), box(minx, miny + 300, maxx, miny + 500),
                                       box(minx, miny + 500, maxx, maxy)], crs=METRIC_CRS)
    paths = {"legal": directory / "legal.geojson", "annex": directory / "annex.geojson",
             "affine": directory / "affine.json", "image": directory / "annex.png"}
    legal.to_crs("EPSG:4326").to_file(paths["legal"], driver="GeoJSON")
    annex.to_crs("EPSG:4326").to_file(paths["annex"], driver="GeoJSON")
    # pixels = metres from the upper-left corner of a 400 x 900 m map around the box
    left, top = minx - 50.0, maxy + 50.0
    paths["affine"].write_text(json.dumps({"affine_px": [1.0, 0.0, -left], "affine_py": [0.0, -1.0, top]}),
                               encoding="utf-8")
    Image.new("RGB", (400, 900), "white").save(paths["image"])
    # H2: the Braunschweig holdout references (the Parkscheininseln and zone 132) as small boxes inside the fixture box
    holdout = gpd.GeoDataFrame({"zone_id": list(ss.HOLDOUT_REFERENCE_ZONES["03101000"])},
                               geometry=[box(minx + 20 + 60 * number, miny + 100, minx + 60 + 60 * number, miny + 140)
                                         for number in range(len(ss.HOLDOUT_REFERENCE_ZONES["03101000"]))],
                               crs=METRIC_CRS)
    paths["holdout"] = directory / "holdout_zones.geojson"
    holdout.to_crs("EPSG:4326").to_file(paths["holdout"], driver="GeoJSON")
    return ["--legal-zones", str(paths["legal"]), "--reference", f"03101000={paths['annex']}",
            "--annex-affine", str(paths["affine"]), "--annex-image", str(paths["image"]),
            "--holdout-zones", str(paths["holdout"])]


def test_supply_share_cli_writes_tagged_outputs_with_the_cross_check_and_b5(tmp_path, caplog):
    import hashlib
    import shutil

    from scripts.build_parking_zones_from_osm import build_regulation_query, main

    extract = tmp_path / "fixture-260928.osm"
    shutil.copy(OSM_FIXTURE, extract)
    (tmp_path / "fixture-260928.osm.md5").write_text(f"{hashlib.md5(extract.read_bytes()).hexdigest()}  "
                                                     f"{extract.name}\n", encoding="ascii")
    overpass = tmp_path / "raw_overpass"
    overpass.mkdir()
    shutil.copy(REGULATION_FIXTURE, overpass / "03101000_regulation_overpass_2026-09-30.json")
    (overpass / "03101000_regulation_overpass_2026-09-30.query.txt").write_text(build_regulation_query(FIXTURE_BBOX),
                                                                              encoding="utf-8")
    out = tmp_path / "derived_supply_share"
    town = ["--town", "03101000=" + ",".join(str(value) for value in FIXTURE_BBOX)]
    common = ["--supply-share", "--overpass-dir", str(overpass), "--out-dir", str(out)] + town + _b5_inputs(tmp_path)
    read = ["--osm-extract", str(extract), "--osm-timestamp", "2026-09-28T00:00:00Z"]
    with caplog.at_level("INFO"):
        assert main(common + read) == 0
    # G3: the rates of the query box (the QA numbers) are logged too, naming the region
    assert any("query box (the QA numbers)" in record.getMessage() and "B-a" in record.getMessage()
               for record in caplog.records)

    meta = json.loads((out / "supply_inventory_fixture-260928.json").read_text(encoding="utf-8"))
    assert meta["extract"]["md5"] == hashlib.md5(extract.read_bytes()).hexdigest()
    assert meta["osm_timestamp"] == "2026-09-28T00:00:00Z"
    assert set(meta["read_seconds"]) == {"points", "lines", "multipolygons"}
    assert meta["payment_evidence"] is True
    # the default share 0.3 (owner decision 2) names every output of the run
    qa_path = out / f"03101000_supply_qa_{TAG}.json"
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    assert qa["osm_timestamp"] == "2026-09-28T00:00:00Z"
    assert qa["parameters"] == ss.DEFAULT_SUPPLY_PARAMETERS.as_dict() and qa["parameters"]["share_threshold"] == 0.3
    assert qa["variant"] == sv.FULL_INVENTORY.as_dict()
    # H2 in Braunschweig: recall over the four street-list references only, no precision frame
    assert sorted(qa["holdout"]["references"]) == sorted(ss.HOLDOUT_REFERENCE_ZONES["03101000"])
    assert 0.0 <= qa["holdout"]["recall"] <= 1.0 and qa["holdout"]["precision"] is None
    # the payment evidence belongs to variant T: B carries none
    assert qa["payment_evidence"] is None
    assert qa["supply"]["elements_by_kind_and_class"] == {
        "street_side": {"paid": 2, "restricted": 2, "free": 4, "excluded": 6},
        "street_side_area": {"paid": 0, "restricted": 0, "free": 1, "excluded": 0},
        "lot": {"paid": 2, "restricted": 0, "free": 1, "excluded": 1}}
    # the Overpass fixture lacks the service way 507, the way 509, the street-side area 603, the node lot 29 and the
    # car park 800
    assert qa["cross_check"]["overpass_response"] == "03101000_regulation_overpass_2026-09-30.json"
    assert qa["cross_check"]["difference"] == {
        "street_side": {"paid": 0, "restricted": 0, "free": 2, "excluded": 2},
        "street_side_area": {"paid": 0, "restricted": 0, "free": 1, "excluded": 0},
        "lot": {"paid": 1, "restricted": 0, "free": 1, "excluded": 0}}
    assert qa["rule"]["area_m2"] > 10_000 and 0 < qa["raster"]["classified_cells"] <= qa["raster"]["cells"]
    assert {"recall", "precision", "passes"} <= set(qa["validation"])
    assert 0.0 <= qa["validation"]["recall"] <= 1.0 and 0.0 <= qa["validation"]["precision"] <= 1.0
    for name in (f"03101000_supply_zones_{TAG}.geojson", f"03101000_paid_share_{TAG}.csv.gz",
                 f"03101000_supply_elements_{TAG}.geojson"):
        assert (out / name).is_file(), name
    raster = pd.read_csv(out / f"03101000_paid_share_{TAG}.csv.gz")
    assert list(raster.columns) == list(ss.RASTER_COLUMNS) and len(raster) == qa["raster"]["cells"]

    # the outputs of a parameter set are never replaced silently (write-through data), checked before any reading
    with pytest.raises(SystemExit, match="exists"):
        main(common + read)
    # the cross-check compares like with like: the town box must be the box of the saved Overpass query
    shifted = ["--town", "03101000=52.2595,10.5195,52.2660,10.5240"]
    with pytest.raises(SystemExit, match="query box"):
        main([argument for argument in common if argument not in town] + shifted + read)
    before = qa_path.read_bytes()
    inventory = ["--from-inventory", str(out / "supply_inventory_fixture-260928.gpkg")]
    # re-processing the saved inventory with the Amendment B arms (its pre-registered share 0.5 and the B5 arms other
    # than the default) writes their own tagged files only
    assert main(common + inventory + ["--sensitivity-arms", "--arms-only"]) == 0
    assert [arm.parameters for arm in sv.AMENDMENT_B_ARMS] == [ss.PRE_REGISTERED_SUPPLY_PARAMETERS] + [
        arm for arm in ss.SENSITIVITY_ARMS if arm != ss.DEFAULT_SUPPLY_PARAMETERS]
    for arm in sv.AMENDMENT_B_ARMS:
        assert (out / f"03101000_supply_qa_{arm.tag()}.json").is_file(), arm.tag()
    assert qa_path.read_bytes() == before
    # variant T needs an inventory read with the payment evidence: one saved before Task 1c is refused, nothing written
    for suffix in (".gpkg", ".json"):
        shutil.copy(out / f"supply_inventory_fixture-260928{suffix}", tmp_path / f"old_inventory{suffix}")
    old_meta = json.loads((tmp_path / "old_inventory.json").read_text(encoding="utf-8"))
    del old_meta["payment_evidence"]
    (tmp_path / "old_inventory.json").write_text(json.dumps(old_meta), encoding="utf-8")
    with pytest.raises(SystemExit, match="payment evidence"):
        main(common + ["--from-inventory", str(tmp_path / "old_inventory.gpkg"), "--variant-arms", "--arms-only"])
    assert not any((out / f"03101000_supply_qa_{arm.tag()}.json").exists() for arm in sv.VARIANT_ARMS)
    # the information arms S, T and S+T: their own tagged files (run one variant at a time with --variants); S drops
    # the lots, T turns the service way 507 (11 m from the ticket machine 40) paid and the street-side area 603 by its
    # own payment:app tag; the free side of way 509 lies 89 m away and way 504 carries fee=no
    assert main(common + inventory + ["--variant-arms", "--arms-only", "--variants", "S"]) == 0
    assert not any((out / f"03101000_supply_qa_{arm.tag()}.json").exists() for arm in sv.VARIANT_ARMS
                   if arm.variant.payment_evidence_m is not None)
    assert main(common + inventory + ["--variant-arms", "--arms-only", "--variants", "T", "S+T"]) == 0
    arms = {arm.tag(): json.loads((out / f"03101000_supply_qa_{arm.tag()}.json").read_text(encoding="utf-8"))
            for arm in sv.VARIANT_ARMS}
    street = arms[sv.SupplyArm(ss.DEFAULT_SUPPLY_PARAMETERS, sv.STREET_SUPPLY_ONLY).tag()]
    assert street["variant"] == sv.STREET_SUPPLY_ONLY.as_dict() and street["payment_evidence"] is None
    assert street["supply"]["elements_by_kind_and_class"]["lot"] == {name: 0 for name in ss.SUPPLY_CLASSES}
    payment = arms[sv.SupplyArm(ss.DEFAULT_SUPPLY_PARAMETERS, sv.PAYMENT_EVIDENCE).tag()]
    assert payment["supply"]["elements_by_kind_and_class"] == {
        "street_side": {"paid": 3, "restricted": 2, "free": 3, "excluded": 6},
        "street_side_area": {"paid": 1, "restricted": 0, "free": 0, "excluded": 0},
        "lot": {"paid": 2, "restricted": 0, "free": 1, "excluded": 1}}
    # the payment evidence of the box (the ticket machine 40, the area 603; the bakery 41 has payment:app=no)
    assert payment["payment_evidence"]["evidence"] == {"ticket_machines": 1, "app_payment_elements": 1}
    assert payment["payment_evidence"]["converted"] == {"street_side": 1, "street_side_area": 1, "lot": 0}
    assert payment["payment_evidence"]["converted_by"] == {"ticket_machine": 1, "app_payment_parking": 0,
                                                           "own_app_payment_tag": 1}
    # the cross-check stays the B1 classification of the extract in every arm
    assert payment["cross_check"] == qa["cross_check"] and qa_path.read_bytes() == before
    # G1: the POST HOC counterfactual (yes sides as no information) writes its own tagged files, named in the QA,
    # checked against the Geofabrik MD5 of the extract the inventory was read from
    md5_file = ["--osm-extract-md5", str(tmp_path / "fixture-260928.osm.md5")]
    assert main(common + md5_file + ["--from-inventory", str(out / "supply_inventory_fixture-260928.gpkg"),
                                     "--counterfactual", ss.YES_SIDES_COUNTERFACTUAL]) == 0
    counterfactual = json.loads((out / f"03101000_supply_qa_{TAG}_yesnoinfo.json").read_text(encoding="utf-8"))
    assert counterfactual["counterfactual"] == ss.YES_SIDES_COUNTERFACTUAL and qa_path.read_bytes() == before
    # the left side of way 509 (parking:left=yes) is no parking information in the counterfactual only
    assert counterfactual["supply"]["elements_by_kind_and_class"]["street_side"] == {"paid": 2, "restricted": 2,
                                                                                   "free": 3, "excluded": 7}
    assert json.loads(qa_path.read_text(encoding="utf-8"))["counterfactual"] is None
    (tmp_path / "other.md5").write_text(f"{'0' * 32}  {extract.name}\n", encoding="ascii")
    with pytest.raises(SystemExit, match="MD5"):
        main(common + ["--osm-extract-md5", str(tmp_path / "other.md5"), "--from-inventory",
                       str(out / "supply_inventory_fixture-260928.gpkg"), "--overwrite"])


# --------------------------------------------------------------------------- B6: the assembly of the rule polygons


BS, GS, WOB, SZ, PE, HE = "03101000", "03153017", "03103000", "03102000", "03157006", "03154028"
MD5 = "0c513947b19145d84afb0b3bc36d95f5"
#: synthetic supply of each town placed apart, because the release holds one row per cell
SHIFTS = {BS: 0.0, GS: 10_000.0, WOB: 20_000.0, SZ: 30_000.0, PE: 40_000.0, HE: 50_000.0}
#: the arm T at the default share, whose QA carries the payment evidence of the QA table
T_ARM = sv.SupplyArm(ss.DEFAULT_SUPPLY_PARAMETERS, sv.PAYMENT_EVIDENCE)
T_EVIDENCE = {"distance_m": 75.0, "evidence": {"ticket_machines": 2, "app_payment_elements": 1},
              "converted": {"street_side": 3, "street_side_area": 1, "lot": 0}, "converted_spaces": 40.0,
              "converted_by": {"ticket_machine": 3, "app_payment_parking": 1, "own_app_payment_tag": 0}}


def _holdout_block(ags: str, recall: float, precision: float) -> dict:
    """The holdout block of the builder for a town with 10,000 m2 of references (recall and precision set the areas)."""
    ids = ss.HOLDOUT_REFERENCE_ZONES[ags]
    inside = recall * 10_000.0
    frame = ags in ss.HOLDOUT_PRECISION_TOWNS
    return {"references": {zone_id: {"area_m2": 10_000.0 / len(ids), "rule_inside_m2": inside / len(ids)}
                           for zone_id in ids},
            "reference_area_m2": 10_000.0, "rule_inside_reference_m2": inside, "recall": recall,
            "rule_inside_query_box_m2": inside / precision if frame else None, "precision": precision if frame else None}


def _write_supply_inputs(directory: Path, ags: str, rule_parts, *, validation=None, reference=None, holdout=None,
                         arm=sv.DEFAULT_ARM, payment=None) -> None:
    """The files of ``build_parking_zones_from_osm.py --supply-share`` for one town and arm, on a small synthetic
    supply (placed per town, because the release holds one row per cell and the real town boxes are disjoint)."""
    shift = SHIFTS[ags]
    elements = _elements(ways=[(_line(shift, 0, shift + 110, 0), _street(**{"parking:both": "lane",
                                                                          "parking:both:fee": "yes"}))],
                         areas=[(_box(shift, 10, shift + 10, 35), _area())])
    raster = ss.paid_share_raster(elements, (X0 + shift, Y0, X0 + shift + 100.0, Y0 + 25.0))
    counts = ss.summarise_supply(elements)["elements_by_kind_and_class"]
    rule = gpd.GeoDataFrame({"part_id": [f"z{number:03d}" for number in range(1, len(rule_parts) + 1)]},
                            geometry=list(rule_parts), crs=METRIC_CRS)
    tag, parameters = arm.tag(), arm.parameters
    document = {"ags": ags, "parameters": parameters.as_dict(), "tag": tag, "variant": arm.variant.as_dict(),
                "holdout": holdout, "payment_evidence": payment, "bbox": [52.2, 10.5, 52.3, 10.6],
                "osm_timestamp": SNAPSHOT, "extract": {"file": "niedersachsen-260929.osm.pbf", "md5": MD5,
                                                       "sha256": "c2b33b84", "bytes": 506480293},
                "supply": ss.summarise_supply(elements), "street_ways": 1,
                "cross_check": {"overpass_response": f"{ags}_regulation_overpass_2026-09-30.json",
                                "overpass_osm_timestamp": "2026-09-30T14:24:46Z", "extract": counts,
                                "overpass": counts, "difference": {kind: {name: 0 for name in ss.SUPPLY_CLASSES}
                                                                   for kind in ss.ELEMENT_KINDS}},
                "raster": {"cells": len(raster), "classified_cells": int(raster["classified"].sum()),
                           "classified_cell_share": float(raster["classified"].mean()),
                           "paid_cells": int(ss.paid_cells(raster).sum())},
                "rule": {"area_m2": round(float(rule.geometry.area.sum()), 1), "parts": len(rule)},
                "validation": validation, "reference": reference}
    (directory / f"{ags}_supply_qa_{tag}.json").write_text(json.dumps(document), encoding="utf-8")
    (directory / f"{ags}_supply_zones_{tag}.geojson").write_text(rule.to_crs("EPSG:4326").to_json(), encoding="utf-8")
    (directory / f"{ags}_paid_share_{tag}.csv.gz").write_bytes(
        sq.deterministic_gzip(raster.to_csv(index=False, lineterminator="\n")))


def _annex_zones() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"zone": ["ia", "ib", "ii"]},
                            geometry=[_box(0, 0, 1000, 1000), _box(1000, 0, 1300, 1000), _box(0, -2000, 3000, 0)],
                            crs=METRIC_CRS).set_index("zone")


def _v1_zones(assembly) -> list:
    return [assembly.zone_record("bs_zone_ia", BS, _box(0, 500, 1000, 1000), "ordinance_map", "https://v1", "v1"),
            assembly.zone_record("bs_zone_ib", BS, _box(1000, 0, 1300, 1000), "ordinance_map", "https://v1", "v1"),
            assembly.zone_record("wob_innenstadt", WOB, _box(17_000, 20_000, 18_000, 21_000), "osm_fee_tags",
                                 "https://v1", "v1"),
            assembly.zone_record("gs_altstadt_zone1", GS, _box(20_000, -40_000, 21_000, -39_000),
                                 "centre_approximation", "https://v1", "v1")]


def _assemble(assembly, directory: Path, recall: float, precision: float, *, holdout_recall: float = 0.8,
              holdout_precision: float = 0.9, counterfactuals=()) -> tuple:
    """Rule inputs of Braunschweig (a piece half under the v1 zone Ia, one inside zone II, one outside every annex
    zone; H1 = ``recall`` / ``precision``), Goslar (replaces its centre approximation) and the QA-only holdout towns
    Wolfsburg, Salzgitter, Peine and Helmstedt (H2 = ``holdout_recall`` / ``holdout_precision`` in every holdout town),
    assembled like the script's main."""
    metrics = {"recall": recall, "precision": precision, "rule_area_m2": 1.0, "legal_area_m2": 1.0,
               "rule_inside_legal_m2": 1.0, "rule_inside_frame_m2": 1.0, "rule_inside_annex_m2": 1.0}
    validation = dict(metrics, passes=ss.passes_validation(metrics), minimum=ss.VALIDATION_MINIMUM)
    _write_supply_inputs(directory, BS, [_box(100, 100, 900, 800), _box(500, -700, 1500, -100),
                                         _box(5000, 5000, 5200, 5200)], validation=validation,
                         reference={"path": "annex.geojson", "core_share_inside_reference": 0.8,
                                    "reference_share_covered": 0.2, "largest_outline_distance_m": 900.0},
                         holdout=_holdout_block(BS, holdout_recall, holdout_precision))
    _write_supply_inputs(directory, BS, [_box(100, 100, 900, 800)], arm=T_ARM, payment=T_EVIDENCE,
                         holdout=_holdout_block(BS, holdout_recall, holdout_precision))
    _write_supply_inputs(directory, GS, [_box(20_100, -39_900, 20_900, -39_100)])
    _write_supply_inputs(directory, WOB, [_box(17_100, 20_100, 17_300, 20_300)],
                         holdout=_holdout_block(WOB, holdout_recall, holdout_precision))
    for ags in (SZ, PE, HE):
        _write_supply_inputs(directory, ags, [_box(SHIFTS[ags], 0, SHIFTS[ags] + 200, 200)],
                             holdout=_holdout_block(ags, holdout_recall, holdout_precision))
    supply = assembly.load_supply_inputs(directory, municipalities=(BS, GS, WOB, SZ, PE, HE))
    gate = assembly.supply_gate(supply)
    zones = [zone for zone in _v1_zones(assembly) if zone["zone_id"] != "gs_altstadt_zone1"]
    replacement = assembly.supply_replacement("gs_altstadt_zone1", GS, "https://goslar", "the v1 hull", supply,
                                              gate["passed"])
    zones.append(replacement or [zone for zone in _v1_zones(assembly) if zone["zone_id"] == "gs_altstadt_zone1"][0])
    zones, _ = assembly.apply_precedence(zones)
    pieces = []
    if gate["passed"]:
        records, pieces = assembly.assign_braunschweig_pieces(assembly.supply_rule(supply[BS]), zones, _annex_zones(),
                                                              supply[BS]["qa"], zone_factory=assembly.supply_zone,
                                                              noun="rule polygons")
        zones += records
    assembly.write_zone_file(assembly.zone_frame(zones), directory / "zones.geojson")
    assembly.write_supply_share_qa(directory / "qa.csv", assembly.supply_qa_rows(supply, zones, pieces, gate,
                                                                                counterfactuals=counterfactuals))
    assembly.write_paid_share_release(directory / "release.csv.gz", assembly.paid_share_release(supply),
                                      provenance=assembly.paid_share_provenance(supply))
    loaded = pz_load(directory / "zones.geojson")
    tariffs = pd.DataFrame({"zone_id": list(loaded["zone_id"]),
                            "municipality_ags": [GS if zone.startswith("gs_") else WOB if zone.startswith("wob_")
                                                 else BS for zone in loaded["zone_id"]]})
    qa = sq.load_supply_share_qa(directory / "qa.csv")
    sq.validate_supply_share_qa(qa, loaded, tariffs)
    return loaded.set_index("zone_id"), qa.set_index("ags", drop=False), pieces


def pz_load(path):
    from braunschweig.parking import zones as pz

    return pz.load_zone_polygons(path)


def test_b6_applies_the_rule_polygons_when_h1_and_h2_pass(assembly, tmp_path):
    zones, qa, pieces = _assemble(assembly, tmp_path, recall=0.81, precision=0.90)
    assert set(zones.index) == {"bs_zone_ia", "bs_zone_ib", "wob_innenstadt", "gs_altstadt_zone1", "bs_zone_ia_sued",
                                "bs_zone_ii"}
    goslar = zones.loc["gs_altstadt_zone1"]
    assert goslar["geometry_source"] == "osm_supply_majority" and goslar["osm_timestamp"] == SNAPSHOT
    # the owner's post hoc share 0.3 is the provenance of an applied polygon
    assert (goslar["supply_walk_m"], goslar["paid_share_threshold"], goslar["minimum_usable_spaces"]) == \
        (250.0, 0.3, 50.0)
    assert "holdout check H2" in goslar["digitising_note"] and "POST HOC" in goslar["digitising_note"]
    assert goslar.geometry.area == pytest.approx(800.0 * 800.0, rel=1e-4)
    assert "unavoidable_walk_m" not in zones.columns
    # assigned WHOLE to the annex zone of largest overlap (ruling R-T1-f): the southern half of the first piece
    # (under the v1 zone Ia the northern half is cut out) -> Ia, the second piece -> II; the third overlaps no zone
    assert zones.loc["bs_zone_ia_sued"].geometry.area == pytest.approx(800.0 * 400.0, rel=1e-3)
    assert zones.loc["bs_zone_ii"].geometry.area == pytest.approx(1000.0 * 600.0, rel=1e-4)
    assert zones.loc["bs_zone_ia_sued"].geometry.intersection(zones.loc["bs_zone_ia"].geometry).area == 0.0
    assert [piece["status"] for piece in pieces] == ["assigned", "assigned", "no_annex_zone"]
    assert (qa.loc[BS, "decision"], qa.loc[BS, "applied"], qa.loc[BS, "zone_ids"]) == \
        ("applied", "true", "bs_zone_ia_sued;bs_zone_ii")
    assert (qa.loc[GS, "decision"], qa.loc[GS, "zone_ids"]) == ("applied", "gs_altstadt_zone1")
    assert (qa.loc[WOB, "role"], qa.loc[WOB, "decision"], qa.loc[WOB, "applied"]) == ("qa_only", "qa_only", "false")
    # H1 (B5 at the default share) and the pooled H2 on the Braunschweig row, the holdout overlaps on every holdout
    # town (precision only in the four towns with a query-box frame); a town without references carries none
    assert (qa.loc[BS, "b5_recall"], qa.loc[BS, "b5_passed"]) == ("0.810000", "true") and qa.loc[GS, "b5_passed"] == ""
    assert (qa.loc[BS, "h2_pooled_recall"], qa.loc[BS, "h2_pooled_precision"], qa.loc[BS, "h2_minimum_town_recall"],
            qa.loc[BS, "h2_passed"]) == ("0.800000", "0.900000", "0.800000", "true")
    assert (qa.loc[BS, "holdout_recall"], qa.loc[BS, "holdout_precision"]) == ("0.800000", "")
    assert (qa.loc[WOB, "holdout_references"], qa.loc[WOB, "holdout_precision"]) == ("wob_innenstadt", "0.900000")
    assert qa.loc[GS, ["holdout_references", "holdout_recall", "h2_passed"]].tolist() == ["", "", ""]
    # the payment evidence comes from the arm T at the default share (Goslar ran no T arm)
    assert (qa.loc[BS, "ticket_machines"], qa.loc[BS, "app_payment_elements"],
            qa.loc[BS, "payment_evidence_paid_elements"]) == ("2", "1", "4")
    assert qa.loc[GS, ["ticket_machines", "app_payment_elements"]].tolist() == ["", ""]
    document = json.loads((tmp_path / "zones.geojson").read_text(encoding="utf-8"))
    assert "osm_supply_majority" in document["license"] and "OpenStreetMap contributors" in document["attribution"]
    text = (tmp_path / "qa.csv").read_text(encoding="utf-8")
    assert text.isascii()
    header = [line for line in text.splitlines() if line.startswith("#")]
    for column in sq.SUPPLY_SHARE_QA_COLUMNS:
        assert any(line.startswith(f"# {column}: ") for line in header), column
    release = sq.load_paid_share_release(tmp_path / "release.csv.gz")
    assert sorted(set(release["municipality_ags"])) == sorted([BS, GS, WOB, SZ, PE, HE])


@pytest.mark.parametrize("h1, h2, failed", [
    ((0.689, 0.845), (0.8, 0.9), "H1"),     # B5 at the default share fails, the holdout check passes
    ((0.883, 0.856), (0.8, 0.3), "H2"),     # H1 passes, the pooled holdout precision fails
], ids=["h1_fails", "h2_fails"])
def test_b6_leaves_every_polygon_when_h1_or_h2_fails(assembly, tmp_path, h1, h2, failed):
    # the POST HOC counterfactual is cited in the Braunschweig note next to the interpretation it varies, never applied
    counterfactual = {"ags": BS, "counterfactual": ss.YES_SIDES_COUNTERFACTUAL, "_path": "counterfactual.json",
                      "parameters": ss.PRE_REGISTERED_SUPPLY_PARAMETERS.as_dict(),
                      "validation": {"recall": 0.762837, "precision": 0.854269, "passes": True}}
    zones, qa, pieces = _assemble(assembly, tmp_path, recall=h1[0], precision=h1[1], holdout_recall=h2[0],
                                  holdout_precision=h2[1], counterfactuals=[counterfactual])
    note = qa.loc[BS, "note"]
    assert "parking:<side>=yes" in note and "POST HOC" in note and "0.763" in note and "not a validation" in note
    assert f"{failed} failed" in note and "set value" in note
    assert set(zones["geometry_source"]) == {"ordinance_map", "osm_fee_tags", "centre_approximation"}
    # without a rule-based polygon the release keeps its v1 layout (byte identity of the regenerated file)
    assert list(zones.columns) == ["geometry_source", "source_url", "source_date", "digitised_on", "digitising_note",
                                   "geometry"]
    assert pieces == []
    assert [qa.loc[ags, "decision"] for ags in (BS, GS, WOB, SZ)] == ["gate_failed", "gate_failed", "qa_only",
                                                                     "qa_only"]
    assert (qa["applied"] == "false").all() and (qa["zone_ids"] == "").all()
    assert (qa.loc[BS, "b5_passed"], qa.loc[BS, "h2_passed"]) == (("false", "true") if failed == "H1" else
                                                                  ("true", "false"))


@pytest.mark.parametrize("change, message", [
    ("sensitivity_arm", "default"),
    ("counterfactual", "counterfactual"),
    ("variant", "variant"),
    ("contradicting_gate", "contradicts"),
    ("missing_holdout_town", "holdout"),
], ids=["sensitivity_arm", "counterfactual", "variant", "contradicting_gate", "missing_holdout_town"])
def test_assembly_refuses_supply_inputs_it_cannot_trust(assembly, tmp_path, change, message):
    metrics = {"recall": 0.60, "precision": 0.90}
    if change in ("sensitivity_arm", "counterfactual", "variant"):
        _write_supply_inputs(tmp_path, GS, [_box(0, 0, 200, 200)])
        path = tmp_path / f"{GS}_supply_qa_{TAG}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        if change == "sensitivity_arm":
            document["parameters"]["walk_m"] = 400.0
        elif change == "counterfactual":
            document["counterfactual"] = ss.YES_SIDES_COUNTERFACTUAL
        else:
            document["variant"] = sv.PAYMENT_EVIDENCE.as_dict()
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(SystemExit, match=message):
            assembly.load_supply_inputs(tmp_path, municipalities=(GS,))
    elif change == "contradicting_gate":
        _write_supply_inputs(tmp_path, BS, [_box(0, 0, 200, 200)], validation=dict(metrics, passes=True))
        supply = assembly.load_supply_inputs(tmp_path, municipalities=(BS,))
        with pytest.raises(SystemExit, match=message):
            assembly.supply_b5(supply)
    else:
        # H2 pools every holdout reference: without the Salzgitter holdout block the gate is undefined, not passed
        _write_supply_inputs(tmp_path, BS, [_box(0, 0, 200, 200)], validation=dict(metrics, passes=False),
                             holdout=_holdout_block(BS, 0.8, 0.9))
        for ags in (WOB, PE, HE):
            _write_supply_inputs(tmp_path, ags, [_box(SHIFTS[ags], 0, SHIFTS[ags] + 200, 200)],
                                 holdout=_holdout_block(ags, 0.8, 0.9))
        _write_supply_inputs(tmp_path, SZ, [_box(SHIFTS[SZ], 0, SHIFTS[SZ] + 200, 200)])
        supply = assembly.load_supply_inputs(tmp_path, municipalities=(BS, WOB, PE, HE, SZ))
        with pytest.raises(SystemExit, match=message):
            assembly.supply_gate(supply)


# --------------------------------------------------------------------------- the validator on the committed tables


def test_validator_reapplies_the_h1_and_h2_gates_and_the_default_parameters(assembly, tmp_path, capsys):
    import shutil

    from scripts.validate_parking_zones import main

    target = tmp_path / "data" / "braunschweig" / "parking"
    target.mkdir(parents=True)
    for name in ("parking_zones_2026.geojson", "parking_tariffs_2026.csv", "parking_coverage_register_2026.csv",
                 "parking_zones_2026_qa.csv", "parking_zones_2026_municipal_qa.csv",
                 "parking_resident_districts_2026.geojson", "parking_garages_2026.geojson", "parking_garages_2026_qa.csv"):
        shutil.copy(parking_data_path(name), target / name)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    _assemble(assembly, inputs, recall=0.689, precision=0.845)
    shutil.copy(inputs / "release.csv.gz", target / "parking_paid_share_2026.csv.gz")
    rows = sq.load_supply_share_qa(inputs / "qa.csv")

    def run(table) -> tuple:
        (target / "parking_zones_2026_supply_share_qa.csv").write_text(
            "# QA\n" + table.to_csv(index=False, lineterminator="\n"), encoding="utf-8")
        code = main(["--data-path", str(tmp_path / "data")])
        return code, capsys.readouterr().out

    code, out = run(rows)
    assert code == 0 and "supply-share QA" in out and "H2 pooled recall 0.800000" in out and "paid-share release" in out
    contradicting = rows.copy()
    contradicting.loc[contradicting["ags"] == BS, "b5_passed"] = "true"
    code, out = run(contradicting)
    assert code == 1 and "ags 03101000: b5_passed 'true' but the H1 gate gives 'false'" in out
    # H2 is recomputed from the holdout overlaps of the town rows: a town share, a pooled value or a gate that
    # contradicts them fails (Wolfsburg with 1,000 instead of 8,000 m2 of the rule inside its reference)
    holdout = rows.copy()
    holdout.loc[holdout["ags"] == WOB, "holdout_rule_inside_reference_m2"] = "1000.0"
    code, out = run(holdout)
    assert code == 1 and "ags 03103000: holdout_recall 0.800000 but 0.100000 recomputed" in out
    holdout.loc[holdout["ags"] == WOB, ["holdout_recall", "holdout_precision"]] = ["0.100000", "0.112500"]
    code, out = run(holdout)
    assert code == 1 and "h2_pooled_recall 0.800000 but 0.660000 recomputed from the holdout overlaps" in out
    assert "h2_passed 'true' but the H2 gate gives 'false'" in out
    gate = rows.copy()
    gate.loc[gate["ags"] == BS, "h2_passed"] = "false"
    code, out = run(gate)
    assert code == 1 and "ags 03101000: h2_passed 'false' but the H2 gate gives 'true'" in out
    arm = rows.copy()
    arm.loc[arm["ags"] == GS, "share_threshold"] = "0.5"
    code, out = run(arm)
    assert code == 1 and "ags 03153017: share_threshold = 0.5 is not the default 0.3" in out
