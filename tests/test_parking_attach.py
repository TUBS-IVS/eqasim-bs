"""Attach functions of the zone-based parking costs (issue #249; design spec sections 3.4 and 3.5).

``braunschweig.parking.attach`` adds one column per call to the frames the MATSim plans writer consumes:
``parking_zone`` (activities), ``resident_parking_zone`` (persons) and ``parking_free`` (activities, the
free-parking draw). Every frame here is small and synthetic: the zones are 100 m squares in EPSG:25832 and
the free shares are made up, so these tests pin behaviour, not truth. The committed release is exercised
by ``tests/test_parking_zones_stage.py``, the writer integration by
``tests/test_population_parking_attributes.py``.
"""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.parking import attach
from braunschweig.parking import zones as pz

CRS = "EPSG:25832"
SEED = 1234
INCOMMUTER_ID = 900001
ATTACH_LOGGER = "braunschweig.parking.attach"
KEYS = ["person_id", "activity_index"]

#: Synthetic zones: 100 m squares whose lower-left corner is (x, 5790000) in EPSG:25832.
ZONE_ORIGINS_X = {"z_paid_a": 600000.0, "z_paid_b": 600200.0, "z_res": 600400.0, "z_campus": 600600.0}
ZONE_TYPES = {"z_paid_a": "street_paid", "z_paid_b": "street_paid", "z_res": "resident_zone",
              "z_campus": "campus"}
ZONE_CLASSES = {"z_paid_a": "bs_zentrum", "z_paid_b": "03102", "z_res": "bs_innenbereich",
                "z_campus": "bs_innenbereich"}
OUTSIDE = Point(610000.0, 5795000.0)


def _inside(zone_id, dx=50.0, dy=50.0):
    return Point(ZONE_ORIGINS_X[zone_id] + dx, 5790000.0 + dy)


def _zones():
    return gpd.GeoDataFrame({
        "zone_id": list(ZONE_ORIGINS_X),
        "geometry": [box(x, 5790000.0, x + 100.0, 5790100.0) for x in ZONE_ORIGINS_X.values()],
    }, crs=CRS)


def _tariffs():
    """The three columns the attach functions read; the real table has all spec 5.3 columns."""
    return pd.DataFrame({"zone_id": list(ZONE_TYPES), "zone_type": list(ZONE_TYPES.values()),
                         "workplace_class": [ZONE_CLASSES[zone_id] for zone_id in ZONE_TYPES]})


def _shares(free_by_class=None):
    """An SrV-shaped shares table: one class row per workplace class of ``_tariffs`` plus the total row."""
    classes = {"bs_zentrum": 0.5, "03102": 0.5, "bs_innenbereich": 0.5}
    classes.update(free_by_class or {})
    return pd.DataFrame({
        "workplace_class": list(classes) + ["total"],
        "level": ["class"] * len(classes) + ["total"],
        "share_free_total": list(classes.values()) + [0.9],
    })


def _frames(plans):
    """Activities and locations of ``plans`` = {person_id: [(purpose, point), ...]} in activity order."""
    rows = [(person_id, index, purpose, point)
            for person_id, plan in plans.items() for index, (purpose, point) in enumerate(plan)]
    activities = pd.DataFrame({"person_id": [row[0] for row in rows],
                               "activity_index": [row[1] for row in rows],
                               "purpose": [row[2] for row in rows]})
    locations = gpd.GeoDataFrame({"person_id": [row[0] for row in rows],
                                  "activity_index": [row[1] for row in rows],
                                  "location_id": [-1] * len(rows),
                                  "geometry": [row[3] for row in rows]}, crs=CRS)
    return activities, locations


def _zoned_activities(rows):
    """Activities with ``parking_zone`` attached: rows of (person_id, activity_index, purpose, zone or None)."""
    return pd.DataFrame({
        "person_id": [row[0] for row in rows],
        "activity_index": [row[1] for row in rows],
        "purpose": [row[2] for row in rows],
        "parking_zone": pd.Series([np.nan if row[3] is None else row[3] for row in rows], dtype=object),
    })


def _commuters(person_ids, zone_id, purpose="work"):
    """home -> ``purpose`` in ``zone_id`` -> home for every person."""
    rows = []
    for person_id in person_ids:
        rows += [(person_id, 0, "home", None), (person_id, 1, purpose, zone_id), (person_id, 2, "home", None)]
    return _zoned_activities(rows)


def _mixed_population(n_persons=300):
    """Persons with zero, one or two eligible activities in the three drawn zones, plus campus and shops."""
    rows = []
    zones = ("z_paid_a", "z_paid_b", "z_res")
    for person_id in range(1, n_persons + 1):
        first = zones[person_id % 3]
        rows += [(person_id, 0, "home", None), (person_id, 1, "work", first)]
        if person_id % 4 == 0:
            rows.append((person_id, 2, "education", zones[(person_id + 1) % 3]))
        if person_id % 5 == 0:
            rows.append((person_id, 3, "work", "z_campus"))
        if person_id % 7 == 0:
            rows.append((person_id, 4, "shop", "z_paid_a"))
        rows.append((person_id, 9, "home", None))
    return _zoned_activities(rows)


def _free_by_key(frame):
    return frame.set_index(KEYS)["parking_free"].sort_index()


# --------------------------------------------------------------------------- design constants


def test_design_constants():
    assert attach.PARKING_FREE_SEED_OFFSET == 7371
    assert attach.ELIGIBLE_PURPOSES == ("work", "education")
    assert attach.FREE_DRAW_ZONE_TYPES == ("street_paid", "resident_zone")
    assert set(attach.FREE_DRAW_ZONE_TYPES) < set(pz.ZONE_TYPES)
    assert attach.RESIDENT_ZONE_TYPE in pz.ZONE_TYPES


def test_attached_columns_are_the_ones_the_plans_writer_writes():
    import matsim.scenario.population as writer
    from braunschweig.matsim.scenario import population as wrapper

    assert attach.PARKING_ZONE_COLUMN == wrapper.PARKING_ZONE_COLUMN
    assert attach.PARKING_FREE_COLUMN == wrapper.PARKING_FREE_COLUMN
    assert attach.RESIDENT_PARKING_ZONE_COLUMN == wrapper.RESIDENT_PARKING_ZONE_COLUMN
    assert [attach.PARKING_ZONE_COLUMN, attach.PARKING_FREE_COLUMN] == writer.OPTIONAL_ACTIVITY_FIELDS
    assert attach.RESIDENT_PARKING_ZONE_COLUMN in writer.OPTIONAL_PERSON_FIELDS


def test_share_table_vocabulary_matches_the_srv_table_builder():
    from braunschweig.calibration import srv_parking

    assert attach.LEVEL_CLASS == srv_parking.LEVEL_CLASS
    assert set(attach.WORKPLACE_SHARE_LEVELS) == {srv_parking.LEVEL_CLASS, srv_parking.LEVEL_TOTAL}
    assert attach.FREE_SHARE_COLUMN in srv_parking.COMMUTE_TABLE_COLUMNS
    assert set(srv_parking.WORKPLACE_CLASSES) == set(pz.WORKPLACE_CLASSES)


# --------------------------------------------------------------------------- attach_parking_zones


def test_attach_parking_zones_assigns_zone_ids_and_nan_outside():
    activities, locations = _frames({1: [("home", _inside("z_res")), ("work", _inside("z_paid_a")),
                                         ("shop", OUTSIDE), ("home", _inside("z_res"))]})
    zoned = attach.attach_parking_zones(activities, locations, _zones())
    assert zoned["parking_zone"].dtype == object
    assert list(zoned["parking_zone"].iloc[[0, 1, 3]]) == ["z_res", "z_paid_a", "z_res"]
    assert pd.isna(zoned["parking_zone"].iloc[2])


def test_attach_parking_zones_keeps_rows_order_index_and_the_input():
    activities, locations = _frames({1: [("home", _inside("z_res")), ("work", _inside("z_paid_b"))],
                                     2: [("home", OUTSIDE), ("shop", _inside("z_paid_a"))]})
    # Rows out of key order, a non-default index, and locations in yet another order.
    activities = activities.iloc[[3, 0, 2, 1]]
    activities.index = [40, 10, 30, 20]
    locations = locations.iloc[[1, 3, 0, 2]]
    before = activities.copy()

    zoned = attach.attach_parking_zones(activities, locations, _zones())

    assert list(zoned.index) == [40, 10, 30, 20]
    pd.testing.assert_frame_equal(zoned.drop(columns="parking_zone"), before)
    assert list(zoned["parking_zone"].fillna("-")) == ["z_paid_a", "z_res", "-", "z_paid_b"]
    assert "parking_zone" not in activities.columns


def test_attach_covers_injected_incommuters():
    """In-commuters are appended to the resident frames by the cordon merge (concat, sort, new index);
    their activities inside a zone must be zoned exactly like residents' (plan review focus 4)."""
    residents, resident_locations = _frames({1: [("home", _inside("z_res")), ("work", _inside("z_paid_a"))],
                                             2: [("home", OUTSIDE), ("shop", OUTSIDE)]})
    incommuter, incommuter_locations = _frames({INCOMMUTER_ID: [("home", Point(590000.0, 5770000.0)),
                                                                ("work", _inside("z_paid_b")),
                                                                ("home", Point(590000.0, 5770000.0))]})
    activities = pd.concat([residents, incommuter], ignore_index=True).sort_values(KEYS).reset_index(drop=True)
    locations = gpd.GeoDataFrame(pd.concat([resident_locations, incommuter_locations], ignore_index=True),
                                 geometry="geometry", crs=CRS)

    zoned = attach.attach_parking_zones(activities, locations, _zones()).set_index(KEYS)["parking_zone"]

    assert zoned[(INCOMMUTER_ID, 1)] == "z_paid_b"
    assert pd.isna(zoned[(INCOMMUTER_ID, 0)]) and pd.isna(zoned[(INCOMMUTER_ID, 2)])
    assert zoned[(1, 0)] == "z_res" and zoned[(1, 1)] == "z_paid_a"


@pytest.mark.parametrize("point", [OUTSIDE, Point(10.52, 52.26)], ids=["outside", "lonlat_labelled_utm"])
def test_zero_zone_coverage_raises_as_a_broken_join(point):
    """Not one activity in a zone although zones exist is the signature of a broken join or CRS (e.g.
    WGS84 degrees labelled EPSG:25832), never a population that avoids every zone."""
    activities, locations = _frames({1: [("home", point), ("work", point)]})
    with pytest.raises(ValueError, match="broken"):
        attach.attach_parking_zones(activities, locations, _zones())


def test_no_zones_leave_every_activity_outside_with_a_warning(caplog):
    activities, locations = _frames({1: [("home", _inside("z_res"))]})
    with caplog.at_level(logging.WARNING, logger=ATTACH_LOGGER):
        zoned = attach.attach_parking_zones(activities, locations, _zones().iloc[0:0])
    assert zoned["parking_zone"].isna().all()
    assert any(record.levelno == logging.WARNING and "no parking zones" in record.getMessage()
               for record in caplog.records)


def test_activity_without_a_location_raises():
    activities, locations = _frames({1: [("home", _inside("z_res")), ("work", _inside("z_paid_a"))]})
    with pytest.raises(ValueError, match="no location"):
        attach.attach_parking_zones(activities, locations.iloc[[0]], _zones())


@pytest.mark.parametrize("geometry", [None, Point()], ids=["null", "empty"])
def test_activity_with_a_missing_geometry_raises(geometry):
    activities, locations = _frames({1: [("home", _inside("z_res")), ("work", _inside("z_paid_a"))]})
    locations = locations.copy()
    locations.loc[1, "geometry"] = geometry
    with pytest.raises(ValueError, match="geometry"):
        attach.attach_parking_zones(activities, locations, _zones())


def test_duplicate_location_keys_raise():
    activities, locations = _frames({1: [("home", _inside("z_res")), ("work", _inside("z_paid_a"))]})
    duplicated = gpd.GeoDataFrame(pd.concat([locations, locations.iloc[[1]]], ignore_index=True),
                                  geometry="geometry", crs=CRS)
    with pytest.raises(ValueError, match="person_id, activity_index"):
        attach.attach_parking_zones(activities, duplicated, _zones())


def test_locations_must_be_a_geodataframe_in_the_zone_crs():
    activities, locations = _frames({1: [("home", _inside("z_res"))]})
    with pytest.raises(TypeError, match="GeoDataFrame"):
        attach.attach_parking_zones(activities, pd.DataFrame(locations), _zones())
    with pytest.raises(ValueError, match="CRS"):
        attach.attach_parking_zones(activities, locations.set_crs("EPSG:4326", allow_override=True), _zones())


def test_attach_parking_zones_logs_the_coverage_rate_and_the_top_zones(caplog):
    activities, locations = _frames({1: [("home", _inside("z_res")), ("work", _inside("z_paid_a")),
                                         ("shop", OUTSIDE), ("home", _inside("z_res"))]})
    with caplog.at_level(logging.INFO, logger=ATTACH_LOGGER):
        attach.attach_parking_zones(activities, locations, _zones())
    [line] = [record.getMessage() for record in caplog.records
              if record.getMessage().startswith("[parking] activities in zones:")]
    assert "3/4 (75.0 %)" in line
    assert "z_res 2" in line and "z_paid_a 1" in line


# --------------------------------------------------------------------------- attach_resident_zones


def _persons(person_ids):
    return pd.DataFrame({"person_id": list(person_ids), "household_id": [100 + i for i in person_ids]})


def test_resident_zone_only_from_resident_zone_homes():
    activities = _zoned_activities([
        (1, 0, "home", "z_res"), (1, 1, "work", "z_paid_a"), (1, 2, "home", "z_res"),   # lives in the resident zone
        (2, 0, "home", "z_paid_a"), (2, 1, "shop", None), (2, 2, "home", "z_paid_a"),  # lives in a paid zone
        (3, 0, "home", None), (3, 1, "leisure", "z_res"), (3, 2, "home", None),         # visits the resident zone
        (4, 0, "home", "z_campus"),                                                       # lives on campus
    ])
    persons = attach.attach_resident_zones(_persons([1, 2, 3, 4]), activities, _tariffs())
    assert persons["resident_parking_zone"].dtype == object
    zone = persons.set_index("person_id")["resident_parking_zone"]
    assert zone[1] == "z_res"
    assert zone[[2, 3, 4]].isna().all()


def test_resident_zones_keep_rows_order_index_and_the_input():
    persons = _persons([3, 1, 2])
    persons.index = [7, 5, 6]
    before = persons.copy()
    activities = _zoned_activities([(1, 0, "home", "z_res"), (2, 0, "home", None), (3, 0, "home", "z_res")])

    result = attach.attach_resident_zones(persons, activities, _tariffs())

    assert list(result.index) == [7, 5, 6]
    pd.testing.assert_frame_equal(result.drop(columns="resident_parking_zone"), before)
    assert list(result["resident_parking_zone"].fillna("-")) == ["z_res", "z_res", "-"]
    assert "resident_parking_zone" not in persons.columns


def test_person_without_any_activity_gets_no_resident_zone():
    activities = _zoned_activities([(1, 0, "home", "z_res")])
    result = attach.attach_resident_zones(_persons([1, 2]), activities, _tariffs())
    assert pd.isna(result.set_index("person_id").loc[2, "resident_parking_zone"])


def test_conflicting_home_zones_raise():
    """All home activities of a person are at the household home, so two home zones are a broken frame."""
    activities = _zoned_activities([(1, 0, "home", "z_res"), (1, 1, "work", None), (1, 2, "home", None)])
    with pytest.raises(ValueError, match="home"):
        attach.attach_resident_zones(_persons([1]), activities, _tariffs())


def test_home_zone_without_a_tariff_row_raises():
    activities = _zoned_activities([(1, 0, "home", "z_unknown")])
    with pytest.raises(ValueError, match="z_unknown"):
        attach.attach_resident_zones(_persons([1]), activities, _tariffs())


def test_resident_zones_need_the_attached_parking_zone_column():
    activities = _zoned_activities([(1, 0, "home", "z_res")]).drop(columns="parking_zone")
    with pytest.raises(ValueError, match="parking_zone"):
        attach.attach_resident_zones(_persons([1]), activities, _tariffs())


def test_no_home_activity_at_all_raises():
    """No 'home' purpose anywhere is a label mismatch; silently it would exempt nobody (R1)."""
    activities = _zoned_activities([(1, 0, "Home", "z_res"), (2, 0, "work", None)])
    with pytest.raises(ValueError, match="home"):
        attach.attach_resident_zones(_persons([1, 2]), activities, _tariffs())


def test_resident_zones_log_the_resident_count(caplog):
    activities = _zoned_activities([(1, 0, "home", "z_res"), (2, 0, "home", "z_paid_a"), (3, 0, "home", None)])
    with caplog.at_level(logging.INFO, logger=ATTACH_LOGGER):
        attach.attach_resident_zones(_persons([1, 2, 3, 4]), activities, _tariffs())
    [line] = [record.getMessage() for record in caplog.records
              if record.getMessage().startswith("[parking] persons with a resident parking zone:")]
    assert "1/4 (25.0 %)" in line and "z_res 1" in line
    assert "1 live in a zone of another type" in line
    assert "1 have no home activity" in line


# --------------------------------------------------------------------------- draw_parking_free


def test_draw_skips_campus_zones():
    """Campus members pay the day product (assumption C1): never a parkingFree draw there, even at p = 1
    (plan review focus 3)."""
    activities = _zoned_activities([
        (1, 0, "home", None), (1, 1, "work", "z_campus"), (1, 2, "home", None),
        (2, 0, "home", None), (2, 1, "education", "z_campus"), (2, 2, "work", "z_paid_a"), (2, 3, "home", None),
    ])
    free = _free_by_key(attach.draw_parking_free(activities, _tariffs(), _shares(), SEED, shift=1.0))
    assert not free[(1, 1)] and not free[(2, 1)]
    assert free[(2, 2)]


def test_one_draw_per_person_covers_all_eligible_activities():
    rows = []
    for person_id in range(1, 201):
        rows += [(person_id, 0, "home", None), (person_id, 1, "work", "z_paid_a"),
                 (person_id, 2, "education", "z_paid_b"), (person_id, 3, "work", "z_res"), (person_id, 4, "home", None)]
    drawn = attach.draw_parking_free(_zoned_activities(rows), _tariffs(), _shares(), SEED)
    eligible = drawn[drawn["activity_index"].isin([1, 2, 3])]
    assert (eligible.groupby("person_id")["parking_free"].nunique() == 1).all()
    # Not vacuous: at p = 0.5 both outcomes occur among the 200 persons.
    assert set(eligible.groupby("person_id")["parking_free"].first()) == {True, False}


def test_the_first_eligible_activity_decides_the_workplace_class():
    """Class of the person's FIRST eligible activity by activity_index, whatever the row order."""
    activities = _zoned_activities([
        (1, 3, "work", "z_paid_a"), (1, 1, "work", "z_paid_b"), (1, 0, "home", None),   # first: z_paid_b (03102)
        (2, 1, "work", "z_paid_a"), (2, 3, "work", "z_paid_b"), (2, 0, "home", None),   # first: z_paid_a (bs_zentrum)
    ])
    shares = _shares({"03102": 0.0, "bs_zentrum": 1.0})
    free = _free_by_key(attach.draw_parking_free(activities, _tariffs(), shares, SEED))
    assert not free[(1, 1)] and not free[(1, 3)]
    assert free[(2, 1)] and free[(2, 3)]


def test_non_eligible_purposes_are_never_free():
    activities = _zoned_activities([
        (1, 0, "home", "z_res"), (1, 1, "shop", "z_paid_a"), (1, 2, "leisure", "z_paid_b"), (1, 3, "home", "z_res"),
        (2, 0, "home", None), (2, 1, "work", None), (2, 2, "home", None),
    ])
    drawn = attach.draw_parking_free(activities, _tariffs(), _shares(), SEED, shift=1.0)
    assert drawn["parking_free"].dtype == bool
    assert not drawn["parking_free"].any()


@pytest.mark.parametrize("shift, expected", [(1.0, True), (-1.0, False)], ids=["plus_one", "minus_one"])
def test_shift_plus_one_frees_every_eligible_person_and_minus_one_none(shift, expected):
    activities = _mixed_population()
    shares = _shares({"bs_zentrum": 0.0, "03102": 0.5, "bs_innenbereich": 1.0})
    drawn = attach.draw_parking_free(activities, _tariffs(), shares, SEED, shift=shift)
    eligible = activities["purpose"].isin(("work", "education")) & activities["parking_zone"].isin(
        ("z_paid_a", "z_paid_b", "z_res"))
    assert eligible.sum() > 300
    assert (drawn.loc[eligible, "parking_free"] == expected).all()
    assert not drawn.loc[~eligible, "parking_free"].any()


def test_draw_is_stable_under_row_shuffling():
    activities = _mixed_population()
    shuffled = activities.sample(frac=1.0, random_state=7)
    assert list(shuffled.index) != list(activities.index)
    reference = _free_by_key(attach.draw_parking_free(activities, _tariffs(), _shares(), SEED))
    assert reference.any() and not reference.all()
    pd.testing.assert_series_equal(
        _free_by_key(attach.draw_parking_free(shuffled, _tariffs(), _shares(), SEED)), reference)


def test_draw_is_stable_under_chunked_concatenation():
    """The same persons delivered as two concatenated chunks, in either order, give the draw of the whole
    frame. (Separate calls on disjoint chunks need not agree: the draw sorts the persons of ONE frame.)"""
    activities = _mixed_population()
    first_chunk = activities[activities["person_id"] <= 150]
    second_chunk = activities[activities["person_id"] > 150]
    reference = _free_by_key(attach.draw_parking_free(activities, _tariffs(), _shares(), SEED))
    for chunks in ((first_chunk, second_chunk), (second_chunk, first_chunk)):
        combined = pd.concat(chunks, ignore_index=True)
        pd.testing.assert_series_equal(
            _free_by_key(attach.draw_parking_free(combined, _tariffs(), _shares(), SEED)), reference)


def test_draw_consumes_one_uniform_per_person_of_the_frame_sorted_by_person_id():
    """Independent reproduction of the draw: ONE RandomState(random_seed + 7371), one uniform per person of
    the WHOLE frame (eligible or not) in person_id order, free = u < p for the eligible persons."""
    person_ids = [7, 3, 11, 5, 2, 8]
    zones = {7: "z_paid_a", 3: None, 11: "z_res", 5: "z_paid_b", 2: "z_campus", 8: "z_paid_a"}
    rows = []
    for person_id in person_ids:
        rows += [(person_id, 0, "home", None), (person_id, 1, "work", zones[person_id])]
    shares = _shares({"bs_zentrum": 0.5, "03102": 0.5, "bs_innenbereich": 0.5})

    drawn = attach.draw_parking_free(_zoned_activities(rows), _tariffs(), shares, SEED)

    uniforms = np.random.RandomState(SEED + 7371).random_sample(len(person_ids))
    uniform_by_person = dict(zip(sorted(person_ids), uniforms))
    eligible_persons = {7, 11, 5, 8}  # 3 works outside every zone, 2 on campus
    expected = {person_id: person_id in eligible_persons and uniform_by_person[person_id] < 0.5
                for person_id in person_ids}
    free = _free_by_key(drawn)
    assert {person_id: bool(free[(person_id, 1)]) for person_id in person_ids} == expected
    assert not free.xs(0, level="activity_index").any()
    assert len(set(expected.values())) == 2  # the seed yields both outcomes, so the pin is not vacuous


def test_draw_adds_a_boolean_column_keeping_rows_order_index_and_the_input():
    activities = _commuters([2, 1], "z_paid_a")
    activities.index = [50, 51, 52, 60, 61, 62]
    before = activities.copy()
    drawn = attach.draw_parking_free(activities, _tariffs(), _shares(), SEED, shift=1.0)
    assert list(drawn.index) == [50, 51, 52, 60, 61, 62]
    assert drawn["parking_free"].dtype == bool
    pd.testing.assert_frame_equal(drawn.drop(columns="parking_free"), before)
    assert list(drawn["parking_free"]) == [False, True, False, False, True, False]
    assert "parking_free" not in activities.columns


def test_realised_free_share_matches_p_within_three_points():
    activities = _commuters(range(1, 2001), "z_paid_a")
    drawn = attach.draw_parking_free(activities, _tariffs(), _shares({"bs_zentrum": 0.6}), SEED)
    realised = drawn.loc[drawn["activity_index"] == 1, "parking_free"].mean()
    assert abs(realised - 0.6) <= 0.03


def test_unknown_workplace_class_raises():
    shares = _shares()
    shares = shares[shares["workplace_class"] != "03102"]
    with pytest.raises(ValueError, match="03102"):
        attach.draw_parking_free(_commuters([1], "z_paid_b"), _tariffs(), shares, SEED)


def test_draw_rejects_a_zone_without_a_tariff_row():
    with pytest.raises(ValueError, match="z_unknown"):
        attach.draw_parking_free(_commuters([1], "z_unknown"), _tariffs(), _shares(), SEED)


def test_draw_requires_the_parking_zone_column():
    activities = _commuters([1], "z_paid_a").drop(columns="parking_zone")
    with pytest.raises(ValueError, match="parking_zone"):
        attach.draw_parking_free(activities, _tariffs(), _shares(), SEED)


@pytest.mark.parametrize("shift", [1.5, -1.01, float("nan"), "0.1", True], ids=str)
def test_draw_rejects_a_shift_outside_minus_one_to_one(shift):
    with pytest.raises(ValueError, match="shift"):
        attach.draw_parking_free(_commuters([1], "z_paid_a"), _tariffs(), _shares(), SEED, shift=shift)


@pytest.mark.parametrize("random_seed", [1.5, "1234", True, None], ids=str)
def test_draw_rejects_a_seed_that_is_not_an_integer(random_seed):
    with pytest.raises(TypeError, match="random_seed"):
        attach.draw_parking_free(_commuters([1], "z_paid_a"), _tariffs(), _shares(), random_seed)


def test_draw_logs_per_class_persons_p_free_and_realised(caplog):
    activities = pd.concat([_commuters(range(1, 101), "z_paid_a"), _commuters(range(101, 151), "z_res")],
                           ignore_index=True)
    shares = _shares({"bs_zentrum": 0.6, "bs_innenbereich": 0.2})
    with caplog.at_level(logging.INFO, logger=ATTACH_LOGGER):
        drawn = attach.draw_parking_free(activities, _tariffs(), shares, SEED, shift=0.1)
    messages = [record.getMessage() for record in caplog.records]
    [zentrum] = [message for message in messages if "workplace class bs_zentrum:" in message]
    realised = drawn.loc[(drawn["activity_index"] == 1) & (drawn["person_id"] <= 100), "parking_free"].mean()
    assert "persons 100" in zentrum and "p_free 0.7000" in zentrum and f"realised {realised:.4f}" in zentrum
    [innenbereich] = [message for message in messages if "workplace class bs_innenbereich:" in message]
    assert "persons 50" in innenbereich and "p_free 0.3000" in innenbereich
    assert any("150/150 persons" in message for message in messages)


# --------------------------------------------------------------------------- the shares table


def test_free_share_by_class_reads_the_class_rows_only():
    shares = attach.free_share_by_class(_shares({"03102": 0.25}))
    assert shares.to_dict() == {"bs_zentrum": 0.5, "03102": 0.25, "bs_innenbereich": 0.5}


@pytest.mark.parametrize("change, message", [
    (lambda frame: frame.assign(workplace_class=frame["workplace_class"].replace({"03102": "bs_zentrum"})),
     "duplicate"),
    (lambda frame: frame.assign(share_free_total=1.2), "share_free_total"),
    (lambda frame: frame.assign(workplace_class=frame["workplace_class"].replace({"03102": 3102})), "text"),
    (lambda frame: frame.assign(level=frame["level"].replace({"total": "overall"})), "overall"),
    (lambda frame: frame.drop(columns="share_free_total"), "share_free_total"),
    (lambda frame: frame[frame["level"] == "total"], "no level == 'class' row"),
], ids=["duplicate_class", "share_above_one", "class_not_text", "unknown_level", "missing_column", "no_class_rows"])
def test_free_share_by_class_rejects_a_broken_table(change, message):
    with pytest.raises(ValueError, match=message):
        attach.free_share_by_class(change(_shares()))


def test_check_workplace_classes_names_every_missing_class_with_its_zones():
    shares = attach.free_share_by_class(_shares()).drop(["bs_innenbereich"])
    with pytest.raises(ValueError, match="bs_innenbereich") as error:
        attach.check_workplace_classes(_tariffs(), shares)
    assert "z_campus" in str(error.value) and "z_res" in str(error.value)
    # Restricted to the zone types the draw uses, the campus zone no longer counts.
    with pytest.raises(ValueError, match="z_res") as error:
        attach.check_workplace_classes(_tariffs(), shares, zone_types=attach.FREE_DRAW_ZONE_TYPES)
    assert "z_campus" not in str(error.value)
    attach.check_workplace_classes(_tariffs().iloc[[0, 1]], shares)
