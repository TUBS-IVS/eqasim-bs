"""Portal gate marking and re-mode rate logging of the MATSim population writer (eqasim-bs#442).

The Java ``PortalTripConstraint`` (eqasim-java-bs) recognises a portal gate as an activity of
type ``outside`` carrying the Boolean activity attribute ``portalGate=true``. The vendored
writer ``matsim.scenario.population`` does not import braunschweig code, so it repeats the
attribute name as a literal; this file pins that literal to the single home in
``braunschweig.synthesis.portal_trips.config_keys``.
"""
from __future__ import annotations

import gzip
import sys
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
import shapely.geometry as geo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.synthesis.portal_trips import config_keys  # noqa: E402
from matsim.scenario import population as pop  # noqa: E402

LOGGER_NAME = pop.__name__


class _Progress:
    def update(self):
        pass


class _Context:
    def __init__(self, remode):
        self._remode = remode

    def config(self, key):
        return {"remode_carless_car_legs": self._remode}[key]

    def progress(self, **kwargs):
        import contextlib

        @contextlib.contextmanager
        def _cm():
            yield _Progress()
        return _cm()


def _persons(person_ids):
    frame = pd.DataFrame({field: [0] * len(person_ids) for field in pop.PERSON_FIELDS})
    frame["person_id"] = person_ids
    frame["household_id"] = person_ids
    frame["household_income"] = "2600-3000"
    frame["sex"] = "female"
    frame["employed"] = "yes"
    frame["high_income"] = False
    frame["has_license"] = True
    frame["has_pt_subscription"] = False
    frame["pt_subscription_type"] = "never_pt"
    return frame[pop.effective_person_fields(frame)]


def _frames(plans, owns_car):
    """``plans``: {person_id: ([purposes], [leg modes])}; ``owns_car``: ids owning a car vehicle."""
    activity_rows, trip_rows, vehicle_rows = [], [], []
    for person_id, (purposes, modes) in plans.items():
        for index, purpose in enumerate(purposes):
            activity_rows.append({
                "person_id": person_id, "activity_index": index, "start_time": float("nan"),
                "end_time": float("nan"), "purpose": purpose,
                "geometry": geo.Point(index * 10.0, 0.0), "location_id": -1})
        for index, mode in enumerate(modes):
            trip_rows.append({"person_id": person_id, "mode": mode,
                              "departure_time": 28800.0 + index * 3600.0, "travel_time": 600.0})
        vehicle_rows.append({"owner_id": person_id, "vehicle_id": "%d:car_passenger" % person_id,
                             "mode": "car_passenger"})
        if person_id in owns_car:
            vehicle_rows.append({"owner_id": person_id, "vehicle_id": "%d:car" % person_id,
                                 "mode": "car"})
    activities = pd.DataFrame(activity_rows).drop(columns=["activity_index"])
    return (_persons(list(plans)), activities,
            pd.DataFrame(trip_rows, columns=pop.TRIP_FIELDS),
            pd.DataFrame(vehicle_rows, columns=pop.VEHICLE_FIELDS))


def _write(tmp_path, frames, remode=False, enable_urban_parking=False):
    output = tmp_path / "population.xml.gz"
    pop.write_population(str(output), *frames, enable_urban_parking=enable_urban_parking,
                         context=_Context(remode))
    with gzip.open(output, "rb") as handle:
        return handle.read()


def _activities(xml_bytes):
    root = ElementTree.fromstring(xml_bytes)
    return [(person.get("id"), activity)
            for person in root.findall("person") for activity in person.findall("./plan/activity")]


def _attributes(activity):
    return {a.get("name"): (a.get("class"), a.text) for a in activity.findall("./attributes/attribute")}


def test_portal_gate_attribute_name_matches_single_home_in_config_keys():
    assert pop.PORTAL_GATE_ATTRIBUTE == config_keys.PORTAL_GATE_ACTIVITY_ATTRIBUTE == "portalGate"
    assert pop.PORTAL_GATE_ACTIVITY_TYPE == config_keys.OUTSIDE_PURPOSE == "outside"


def test_outside_activity_carries_boolean_portal_gate_and_others_do_not(tmp_path):
    frames = _frames({1: (["home", "outside", "home"], ["car", "car"])}, owns_car={1})
    xml = _write(tmp_path, frames)

    by_type = {a.get("type"): _attributes(a) for _, a in _activities(xml)}
    assert by_type["outside"] == {"portalGate": ("java.lang.Boolean", "true")}
    assert by_type["home"] == {}
    assert xml.count(b"portalGate") == 1


def test_portal_gate_merges_with_is_paris_attributes_when_urban_parking_is_on(tmp_path):
    frames = _frames({1: (["home", "outside", "home"], ["car", "car"])}, owns_car={1})
    xml = _write(tmp_path, frames, enable_urban_parking=True)

    by_type = {a.get("type"): _attributes(a) for _, a in _activities(xml)}
    assert by_type["outside"] == {"isParis": ("java.lang.Boolean", "false"),
                                  "portalGate": ("java.lang.Boolean", "true")}
    assert set(by_type["home"]) == {"isParis"}


def test_population_without_outside_activity_has_no_portal_gate_and_is_unchanged(tmp_path):
    frames = _frames({1: (["home", "work", "home"], ["car", "car"])}, owns_car={1})
    xml = _write(tmp_path, frames)
    xml_urban = _write(tmp_path, frames, enable_urban_parking=True)

    assert b"portalGate" not in xml
    assert b"portalGate" not in xml_urban
    # No activity carries an attributes block at all with the flag off -> legacy bytes.
    assert b"<attributes>" in xml  # person level only
    assert all(not a.findall("attributes") for _, a in _activities(xml))


def test_remode_rate_is_logged_once_with_portal_legs(tmp_path, caplog):
    frames = _frames({
        1: (["home", "outside", "home"], ["car", "car"]),   # no car vehicle: 2 re-moded, 2 portal
        2: (["home", "work"], ["car"]),                      # no car vehicle: 1 re-moded
        3: (["home", "work"], ["car"]),                      # owns a car: kept
        4: (["home", "work"], ["walk"]),                     # not a car leg
    }, owns_car={3})
    with caplog.at_level("INFO", logger=LOGGER_NAME):
        xml = _write(tmp_path, frames, remode=True)

    messages = [r.getMessage() for r in caplog.records if "re-moded car -> car_passenger" in r.getMessage()]
    assert len(messages) == 1
    assert "re-moded car -> car_passenger: 3/4 car legs (75.0%), of which 2 portal legs" in messages[0]
    assert xml.count(b'mode="car_passenger"') == 3
    assert xml.count(b'mode="car"') == 1


def test_remode_rate_is_not_logged_when_flag_is_off(tmp_path, caplog):
    frames = _frames({1: (["home", "outside", "home"], ["car", "car"])}, owns_car=set())
    with caplog.at_level("INFO", logger=LOGGER_NAME):
        xml = _write(tmp_path, frames, remode=False)

    assert not [r for r in caplog.records if "re-moded car -> car_passenger" in r.getMessage()]
    assert b'mode="car_passenger"' not in xml
