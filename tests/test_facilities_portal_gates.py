"""Every portal gate is a real facility that the population writer can reference (eqasim-bs#442, ruling R30).

The eqasim core Java ``LinkAssignment`` throws ``Facility ... does not exist`` for any activity whose facility id is
missing from ``facilities.xml.gz``. The location rows of a portal stay therefore carry ``portal_<gate_id>`` and
``braunschweig.matsim.scenario.facilities`` registers one facility per used gate at the gate coordinate, the same way
it registers the ``home_<household_id>`` facility of an in-commuter at the gate-point home.
"""
import contextlib
import gzip
import logging
from xml.etree import ElementTree

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

import matsim.scenario.facilities as base
import matsim.scenario.population as pop
from braunschweig.matsim.scenario import facilities as bs_facilities
from braunschweig.matsim.scenario.facilities import validate_secondary_coverage
from braunschweig.synthesis.locations.secondary_chainsolvers import portal_anchors
from braunschweig.synthesis.portal_trips.config_keys import PORTAL_LOCATION_ID_PREFIX

CRS = "EPSG:25832"


def _realised(ids, geometries=None):
    geometries = geometries if geometries is not None else [Point(float(i), 0.0) for i in range(len(ids))]
    return gpd.GeoDataFrame({"person_id": range(len(ids)), "activity_index": 1,
                             "location_id": pd.Series(ids, dtype=object)}, geometry=geometries, crs=CRS)


def test_the_portal_prefix_is_the_single_home_value():
    assert PORTAL_LOCATION_ID_PREFIX == "portal_"


def test_portal_facility_frame_has_one_row_per_distinct_gate_at_the_gate_coordinate():
    realised = _realised(["sec_b_1", "portal_gate_e", "portal_gate_e", "portal_gate_w"],
                         [Point(0, 0), Point(40000, 0), Point(40000, 0), Point(-40000, 5)])
    frame = bs_facilities.portal_facility_frame(realised)
    assert list(frame.columns) == base.PORTAL_FIELDS
    assert sorted(frame["location_id"]) == ["portal_gate_e", "portal_gate_w"]
    east = frame.loc[frame["location_id"] == "portal_gate_e", "geometry"].iloc[0]
    assert (east.x, east.y) == (40000.0, 0.0)


def test_portal_facility_frame_is_empty_when_no_portal_row_exists():
    frame = bs_facilities.portal_facility_frame(_realised(["sec_b_1", 5, "sec_b_2"]))
    assert len(frame) == 0
    assert list(frame.columns) == base.PORTAL_FIELDS


def test_portal_facility_frame_rejects_one_gate_at_two_coordinates():
    realised = _realised(["portal_gate_e", "portal_gate_e"], [Point(40000, 0), Point(40001, 0)])
    with pytest.raises(RuntimeError, match="portal_gate_e"):
        bs_facilities.portal_facility_frame(realised)


def test_coverage_accepts_registered_portal_ids_and_logs_their_number(caplog):
    realised = pd.DataFrame({"location_id": pd.Series([5, "portal_gate_e"], dtype=object)})
    with caplog.at_level(logging.INFO):
        validate_secondary_coverage(realised, pd.DataFrame({"location_id": [5]}),
                                    portal_facility_ids={"portal_gate_e"})
    assert any("1 portal gate facilities" in message for message in caplog.messages)


def test_coverage_raises_for_a_dangling_portal_id():
    realised = pd.DataFrame({"location_id": pd.Series([5, "portal_gate_x"], dtype=object)})
    with pytest.raises(RuntimeError, match="portal_gate_x"):
        validate_secondary_coverage(realised, pd.DataFrame({"location_id": [5]}),
                                    portal_facility_ids={"portal_gate_e"})


@pytest.mark.parametrize("placeholder", [-1, -1.0, "-1"])
def test_the_minus_one_placeholder_is_no_longer_excluded(placeholder):
    realised = pd.DataFrame({"location_id": pd.Series(["sec_1", placeholder], dtype=object)})
    with pytest.raises(RuntimeError, match="1 realised secondary location id"):
        validate_secondary_coverage(realised, pd.DataFrame({"location_id": ["sec_1"]}))


def test_the_location_rows_of_the_anchors_carry_the_portal_facility_id():
    anchors = gpd.GeoDataFrame({"person_id": [7], "activity_index": [2], "gate_id": ["gate_e"]},
                               geometry=[Point(40000.0, 0.0)], crs=CRS)
    rows = portal_anchors.location_rows(anchors)
    assert rows["location_id"].tolist() == ["portal_gate_e"]
    assert rows["location_id"].dtype == object


# ---------------------------------------------------------------------------------------------------------------------
# End to end through the REAL facilities stage function and the REAL population writer (no MATSim run).
# ---------------------------------------------------------------------------------------------------------------------

class _Progress:
    def update(self):
        pass


class _FacilitiesContext:
    """Fake synpp context for ``facilities.execute``: config values and the staged frames."""

    def __init__(self, tmp_path, realised):
        self._path = tmp_path
        self._stages = {
            "synthesis.population.spatial.home.locations": gpd.GeoDataFrame(
                {"household_id": [1]}, geometry=[Point(0.0, 0.0)], crs=CRS),
            "synthesis.population.spatial.primary.locations": (
                gpd.GeoDataFrame({"location_id": ["work_1"]}, geometry=[Point(100.0, 0.0)], crs=CRS),
                gpd.GeoDataFrame({"location_id": ["edu_1"]}, geometry=[Point(200.0, 0.0)], crs=CRS)),
            "synthesis.locations.secondary": gpd.GeoDataFrame({
                "location_id": ["sec_b_1"], "offers_leisure": [False], "offers_shop": [True],
                "offers_other": [False], "offers_escort": [False]},
                geometry=[Point(10.0, 0.0)], crs=CRS),
            "synthesis.population.spatial.secondary.locations": (realised, None),
        }

    def config(self, key, default=None, volatile=False):
        return {"secondary_building_potentials": False, "cordon_enabled": False,
                "escort_household_link": False, "escort_purpose": False}.get(key, default)

    def stage(self, name):
        return self._stages[name]

    def path(self):
        return str(self._path)

    def progress(self, total, label):
        return contextlib.nullcontext(_Progress())


class _PopulationContext:
    def config(self, key):
        return {"remode_carless_car_legs": False}[key]

    def progress(self, **kwargs):
        return contextlib.nullcontext(_Progress())


def _plan_frames(purposes, location_ids, geometries):
    person = pd.DataFrame({field: [0] for field in pop.PERSON_FIELDS})
    person["person_id"] = 1
    person["household_id"] = 1
    person["household_income"] = "2600-3000"
    person["sex"] = "female"
    person["employed"] = "yes"
    person["high_income"] = False
    person["has_license"] = True
    person["has_pt_subscription"] = False
    person["pt_subscription_type"] = "never_pt"
    activities = pd.DataFrame({"person_id": 1, "activity_index": range(len(purposes)),
                               "start_time": float("nan"), "end_time": float("nan"), "purpose": purposes})
    locations = gpd.GeoDataFrame({"person_id": 1, "activity_index": range(len(purposes)),
                                  "location_id": pd.Series(location_ids, dtype=object)},
                                 geometry=geometries, crs=CRS)
    trips = pd.DataFrame({"person_id": 1, "mode": "car", "departure_time": 28800.0,
                          "arrival_time": 29400.0}, index=range(len(purposes) - 1))
    vehicles = pd.DataFrame({"owner_id": [1, 1], "vehicle_id": ["1:car", "1:car_passenger"],
                             "mode": ["car", "car_passenger"]})
    return person, activities, locations, trips, vehicles


def _facility_ids_and_options(path):
    with gzip.open(path, "rb") as handle:
        root = ElementTree.fromstring(handle.read())
    return {f.get("id"): [a.get("type") for a in f.findall("activity")] for f in root.findall("facility")}


def _activity_facility_ids(path):
    with gzip.open(path, "rb") as handle:
        root = ElementTree.fromstring(handle.read())
    return [a.get("facility") for a in root.findall("person/plan/activity")]


def _write_both(tmp_path, realised, purposes, location_ids, geometries):
    facilities_dir = tmp_path / "facilities"
    facilities_dir.mkdir()
    bs_facilities.execute(_FacilitiesContext(facilities_dir, realised))
    population_path = tmp_path / "population.xml.gz"
    frames = pop.prepare_frames(*_plan_frames(purposes, location_ids, geometries))
    pop.write_population(str(population_path), *frames, enable_urban_parking=False, context=_PopulationContext())
    return facilities_dir / "facilities.xml.gz", population_path


def test_every_activity_facility_of_a_portal_person_exists_in_the_written_facilities(tmp_path):
    purposes = ["home", "shop", "outside", "home"]
    location_ids = ["h1", "sec_b_1", "portal_gate_e", "h1"]
    geometries = [Point(0.0, 0.0), Point(10.0, 0.0), Point(40000.0, 5.0), Point(0.0, 0.0)]
    realised = _realised(["sec_b_1", "portal_gate_e"], [Point(10.0, 0.0), Point(40000.0, 5.0)])

    facilities_path, population_path = _write_both(tmp_path, realised, purposes, location_ids, geometries)

    facilities = _facility_ids_and_options(facilities_path)
    activity_facilities = _activity_facility_ids(population_path)
    assert len(activity_facilities) == 4
    assert None not in activity_facilities
    assert set(activity_facilities) <= set(facilities)
    assert facilities["portal_gate_e"] == ["outside"]
    assert facilities["home_1"] == ["home"]


def test_the_portal_facility_sits_at_the_gate_coordinate(tmp_path):
    purposes = ["home", "outside", "home"]
    geometries = [Point(0.0, 0.0), Point(40000.0, 5.0), Point(0.0, 0.0)]
    realised = _realised(["portal_gate_e"], [Point(40000.0, 5.0)])
    facilities_path, _ = _write_both(tmp_path, realised, purposes, ["h1", "portal_gate_e", "h1"], geometries)
    with gzip.open(facilities_path, "rb") as handle:
        root = ElementTree.fromstring(handle.read())
    gate = [f for f in root.findall("facility") if f.get("id") == "portal_gate_e"][0]
    assert (float(gate.get("x")), float(gate.get("y"))) == (40000.0, 5.0)


def test_without_portal_stays_no_portal_facility_is_written_and_the_file_is_unchanged(tmp_path):
    purposes = ["home", "shop", "home"]
    geometries = [Point(0.0, 0.0), Point(10.0, 0.0), Point(0.0, 0.0)]
    realised = _realised(["sec_b_1"], [Point(10.0, 0.0)])
    facilities_path, population_path = _write_both(tmp_path, realised, purposes, ["h1", "sec_b_1", "h1"], geometries)

    facilities = _facility_ids_and_options(facilities_path)
    assert not [facility_id for facility_id in facilities if facility_id.startswith("portal_")]
    assert set(_activity_facility_ids(population_path)) <= set(facilities)

    # The same frames through the unextended base writer give the identical file.
    reference_path = tmp_path / "reference.xml.gz"
    context = _FacilitiesContext(tmp_path, realised)
    df_homes, df_primary, df_secondary = base.load_facility_frames(context)
    base.write_facilities(str(reference_path), df_homes, df_primary, df_secondary, context)
    with gzip.open(reference_path, "rb") as reference, gzip.open(facilities_path, "rb") as written:
        assert reference.read() == written.read()
