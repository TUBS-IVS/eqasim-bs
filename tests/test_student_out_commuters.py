"""The two reports of design B6a: the modes of the out-commuting students and their rewritten legs (issue #140, Task 13).

Toy frames with hand-computed counts for the pure functions, and the stage on a stub context.
"""
from __future__ import annotations

import json
import pathlib
import shutil

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis.synthesis import student_out_commuters as S

REPO = pathlib.Path(__file__).resolve().parents[1]
REAL_DATA = REPO / "eqasim-data" / "data"
LIMIT_M = 45000.0
FACTOR = 1.3


def _trips():
    # person 1: normal education leg by car; 2: gate stay by pt; 3: education leg by pt; 4: gate stay by car,
    # a leisure gate stay of person 5 must not count as an education leg; 6 has no leg at all.
    return pd.DataFrame({
        "person_id": [1, 1, 2, 2, 3, 3, 4, 5, 5],
        "trip_index": [0, 1, 0, 1, 0, 1, 0, 0, 1],
        "preceding_purpose": ["home", "education", "home", "outside", "home", "education", "home", "home",
                              "leisure"],
        "following_purpose": ["education", "home", "outside", "home", "education", "home", "outside", "leisure",
                              "home"],
        "mode": ["car", "car", "pt", "pt", "pt", "pt", "car", "bike", "bike"],
        "portal_leg": [False, False, True, True, False, False, True, False, False],
        "euclidean_distance": [20000.0] * 9})


def _students():
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5, 6],
        "home_kreis": ["03101", "03101", "03102", "03151", "03101", "03101"],
        "destination_id": ["Hannover", "Hildesheim", "Hannover", "Goettingen", "Near", "Hannover"],
        "scope": ["surrounding", "surrounding", "surrounding", "surrounding", "local", "surrounding"],
        "dest_kreis": ["03241", "03254", "03241", "03159", "03101", "03241"],
        "straight_km": [20.0, 50.0, 4.0, 80.0, 1.0, 20.0]})


def test_education_legs_take_the_first_education_or_gate_leg_and_mark_the_gate():
    legs = S.outbound_education_legs(_trips(), [1, 2, 3, 4, 5, 6])
    assert legs.set_index("person_id")["mode"].to_dict() == {1: "car", 2: "pt", 3: "pt", 4: "car"}
    assert legs.set_index("person_id")["gated"].to_dict() == {1: False, 2: True, 3: False, 4: True}


def _trips_with_leisure_gate_before_education():
    # person 7: a gate stay (leisure, rewritten) with its return, then a normal education leg by bike;
    # person 8: two gate stays and no education leg left; person 9: two education legs, no gate.
    return pd.DataFrame({
        "person_id": [7, 7, 7, 7, 8, 8, 8, 8, 9, 9, 9, 9],
        "trip_index": [0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3],
        "preceding_purpose": ["home", "outside", "home", "education", "home", "outside", "home", "outside",
                              "home", "education", "home", "education"],
        "following_purpose": ["outside", "home", "education", "home", "outside", "home", "outside", "home",
                              "education", "home", "education", "home"],
        "mode": ["car", "car", "bike", "bike", "pt", "pt", "pt", "pt", "car", "car", "car", "car"],
        "portal_leg": [True, True, False, False, True, True, True, True, False, False, False, False],
        "euclidean_distance": [50000.0, 50000.0, 3000.0, 3000.0, 50000.0, 50000.0, 50000.0, 50000.0,
                               20000.0, 20000.0, 20000.0, 20000.0]})
_STRAIGHT_KM = pd.Series({7: 50.0, 8: 50.0, 9: 20.0, 10: 20.0})


def test_a_gate_stay_before_the_education_leg_does_not_hide_it_and_is_counted():
    trips = _trips_with_leisure_gate_before_education()
    legs = S.outbound_education_legs(trips, [7, 8, 9]).set_index("person_id")
    # person 7 keeps the bike education leg, not the leisure gate stay; person 8 only has gate legs
    assert legs["mode"].to_dict() == {7: "bike", 8: "pt", 9: "car"}
    assert legs["gated"].to_dict() == {7: False, 8: True, 9: False}
    diagnostics = S.leg_diagnostics(trips, [7, 8, 9, 10], _STRAIGHT_KM).set_index("person_id")
    assert diagnostics["n_gate_stays"].to_dict() == {7: 1, 8: 2, 9: 0, 10: 0}
    assert diagnostics["n_education_legs"].to_dict() == {7: 1, 8: 0, 9: 2, 10: 0}
    assert diagnostics["gate_before_education"].to_dict() == {7: True, 8: False, 9: False, 10: False}
    assert diagnostics["gate_without_education"].to_dict() == {7: False, 8: True, 9: False, 10: False}


def test_a_gate_stay_followed_by_a_shorter_education_leg_is_counted_as_uncertain():
    """Person 7: a gate stay, then an education leg of 3 km against a 50 km straight line to the destination (the
    stay may have swallowed the real commute). Person 11 has the same plan but a longer education leg (60 km on a
    50 km straight line); person 9 has no gate stay at all; person 10 has no trips."""
    extra = pd.DataFrame({
        "person_id": [11, 11], "trip_index": [0, 1], "preceding_purpose": ["home", "outside"],
        "following_purpose": ["outside", "education"], "mode": ["car", "car"], "portal_leg": [True, False],
        "euclidean_distance": [50000.0, 60000.0]})
    trips = pd.concat([_trips_with_leisure_gate_before_education(), extra], ignore_index=True)
    km = pd.Series({7: 50.0, 8: 50.0, 9: 20.0, 10: 20.0, 11: 50.0})
    diagnostics = S.leg_diagnostics(trips, [7, 8, 9, 10, 11], km).set_index("person_id")
    assert diagnostics["gate_then_shorter_education_leg"].to_dict() == {7: True, 8: False, 9: False, 10: False,
                                                                       11: False}
    assert diagnostics["gate_before_education"].to_dict()[11]      # a longer leg is still a gate stay before it


def test_a_missing_gate_marker_is_no_gate_leg():
    """A NaN in portal_leg (a leg the portal rewrite never marked) reads as not a gate leg, as in the owner stage
    outside_tour_modes: person 1 keeps its education leg and person 4 has no leg instead of a phantom gate stay."""
    trips = _trips().astype({"portal_leg": object})
    trips.loc[[0, 6], "portal_leg"] = np.nan
    legs = S.outbound_education_legs(trips, [1, 4]).set_index("person_id")
    assert legs["mode"].to_dict() == {1: "car"} and legs["gated"].to_dict() == {1: False}


def test_a_trip_table_without_the_gate_column_is_refused():
    with pytest.raises(ValueError, match="portal_leg"):
        S.outbound_education_legs(_trips().drop(columns=["portal_leg"]), [1])


def test_out_commuter_modes_count_by_band_against_the_reference():
    reference = {"10_25": {"car": 0.6, "pt": 0.4}, "50_100": {"car": 0.2, "pt": 0.8}, "0_5": {"car": 0.5, "pt": 0.5}}
    table = S.out_commuter_mode_table(_students(), S.outbound_education_legs(_trips(), range(1, 7)), FACTOR,
                                      reference)
    cell = table.set_index(["distance_band", "mode"])
    # person 1: 20 km x 1.3 = 26 km -> 25_50; person 2: 65 km -> 50_100; person 3: 5.2 km -> 5_10;
    # person 4: 104 km -> 100_plus; persons 5 (local) and 6 (no leg) are not counted
    assert cell["n_persons"].to_dict() == {("25_50", "car"): 1, ("50_100", "pt"): 1, ("5_10", "pt"): 1,
                                           ("100_plus", "car"): 1}
    assert cell["n_gated"].to_dict() == {("25_50", "car"): 0, ("50_100", "pt"): 1, ("5_10", "pt"): 0,
                                         ("100_plus", "car"): 1}
    assert cell.loc[("50_100", "pt"), "model_share_of_band"] == 1.0
    assert S.out_commuter_mode_table(_students(), S.outbound_education_legs(_trips(), range(1, 7)), FACTOR, reference,
                                     pooled=frozenset({"50_100"})).set_index(["distance_band", "mode"])[
        "reference_band_pooled"].to_dict() == {("25_50", "car"): False, ("50_100", "pt"): True,
                                              ("5_10", "pt"): False, ("100_plus", "car"): False}
    assert cell.loc[("50_100", "pt"), "reference_share_car_pt"] == pytest.approx(0.8)
    assert np.isnan(cell.loc[("25_50", "car"), "reference_share_car_pt"])   # band absent from the reference dict
    assert table.attrs["n_without_leg"] == 1 and table.attrs["n_out_commuters"] == 5


def test_a_mode_other_than_car_and_pt_has_no_reference_share():
    students = _students().iloc[[0]]
    legs = pd.DataFrame({"person_id": [1], "mode": ["bike"], "gated": [False]})
    table = S.out_commuter_mode_table(students, legs, FACTOR, {"25_50": {"car": 0.5, "pt": 0.5}})
    assert np.isnan(table["reference_share_car_pt"].iloc[0]) and table["n_persons"].iloc[0] == 1


def test_gated_leg_table_counts_beyond_the_routable_distance_only_by_home_and_destination():
    students = _students()
    students["straight_km"] = [20.0, 50.0, 46.0, 80.0, 1.0, 20.0]
    legs = S.outbound_education_legs(_trips(), range(1, 7))
    table = S.gated_leg_table(students, legs, LIMIT_M)
    # beyond 45 km: persons 2 (gated), 3 (46 km, not gated), 4 (gated); 1, 5, 6 are within the distance
    rows = table.set_index(["home_kreis", "destination_id"])
    assert rows["n_beyond"].to_dict() == {("03101", "Hildesheim"): 1, ("03102", "Hannover"): 1,
                                          ("03151", "Goettingen"): 1}
    assert rows["n_gated"].to_dict() == {("03101", "Hildesheim"): 1, ("03102", "Hannover"): 0,
                                         ("03151", "Goettingen"): 1}
    # persons 2 and 4 are gated; person 3 is beyond, not gated, with an education leg; none lacks a leg
    assert rows["n_not_gated_with_leg"].to_dict() == {("03101", "Hildesheim"): 0, ("03102", "Hannover"): 1,
                                                      ("03151", "Goettingen"): 0}
    assert rows["n_not_gated_without_leg"].sum() == 0
    assert rows["dest_in_region"].to_dict() == {("03101", "Hildesheim"): False, ("03102", "Hannover"): False,
                                                ("03151", "Goettingen"): False}


def test_a_student_beyond_the_distance_without_an_education_leg_is_counted_as_not_gated_without_leg():
    students = _students()
    students["straight_km"] = [20.0, 50.0, 46.0, 80.0, 1.0, 60.0]      # person 6 (no leg at all) is now beyond
    table = S.gated_leg_table(students, S.outbound_education_legs(_trips(), range(1, 7)), LIMIT_M)
    rows = table.set_index(["home_kreis", "destination_id"])
    assert rows.loc[("03101", "Hannover"), ["n_beyond", "n_gated", "n_not_gated", "n_not_gated_without_leg",
                                            "n_not_gated_with_leg"]].tolist() == [1, 0, 1, 1, 0]
    assert rows["n_not_gated_without_leg"].sum() == 1 and rows["n_not_gated_with_leg"].sum() == 1


def test_a_leg_between_two_kreise_of_the_region_beyond_the_distance_is_flagged():
    students = pd.DataFrame({"person_id": [1], "home_kreis": ["03102"], "destination_id": ["Wolfsburg X"],
                             "scope": ["local"], "dest_kreis": ["03103"], "straight_km": [50.0]})
    legs = pd.DataFrame({"person_id": [1], "mode": ["pt"], "gated": [True]})
    table = S.gated_leg_table(students, legs, LIMIT_M)
    assert table["dest_in_region"].tolist() == [True] and table["n_gated"].tolist() == [1]


def test_a_student_exactly_at_the_limit_is_within_the_routable_distance():
    students = pd.DataFrame({"person_id": [1], "home_kreis": ["03101"], "destination_id": ["X"], "scope": ["local"],
                             "dest_kreis": ["03101"], "straight_km": [45.0]})
    legs = pd.DataFrame({"person_id": [1], "mode": ["car"], "gated": [False]})
    assert S.gated_leg_table(students, legs, LIMIT_M).empty


def test_literals_repeated_from_their_owners_match_them():
    from braunschweig import constants
    from braunschweig.data.education import student_mode_reference as ref
    from braunschweig.synthesis.portal_trips import config_keys as portal
    from braunschweig.synthesis.portal_trips import rewrite
    from braunschweig.synthesis import student_incommuters
    from braunschweig.synthesis.outside_tour_modes import config_keys as outside
    assert S.TRIPS_STAGE == outside.SIMULATED_TRIPS_ALIAS
    assert S.KEY_MIN_BAND_PERSONS == student_incommuters.KEY_MODE_BAND_MIN_PERSONS
    assert S.DEFAULT_MIN_BAND_PERSONS == student_incommuters.DEFAULT_MODE_BAND_MIN_PERSONS
    assert S.GATE_LEG_COLUMN == rewrite.PORTAL_LEG_COLUMN
    assert S.OUTSIDE_PURPOSE == portal.OUTSIDE_PURPOSE
    assert S.KEY_MAX_ROUTABLE_DISTANCE_M == "braunschweig.portal.max_routable_distance_m"
    assert S.DEFAULT_MIN_BAND_PERSONS == ref.DEFAULT_MIN_BAND_PERSONS
    assert S.DETOUR_FACTOR == constants.ROUTED_DETOUR_FACTOR


# ------------------------------------------------------------------------------------------------ the stage
class _ConfigureRecorder:
    def __init__(self, config=None):
        self.stages, self.keys, self._config = [], {}, dict(config or {})

    def stage(self, name, alias=None, **kwargs):
        self.stages.append(alias or name)

    def config(self, key, *args, **kwargs):
        self.keys[key] = self._config.get(key, args[0] if args else None)
        return self.keys[key]


class _ExecuteContext:
    def __init__(self, stages, config, path):
        self._stages, self._config, self._path = stages, config, path

    def stage(self, name):
        return self._stages[name]

    def config(self, key):
        return self._config[key]

    def path(self, name=None):
        return self._path


def _world():
    import geopandas as gpd
    from shapely.geometry import Point

    crs = "EPSG:25832"

    def point(x):
        return Point(600000.0 + x * 1000.0, 5800000.0)

    persons = pd.DataFrame({"person_id": [1, 2, 3, 4], "household_id": [1, 1, 2, 2], "age": [22, 23, 24, 30],
                            "P_TAET": [10, 10, 10, 8]})
    zones = pd.DataFrame({"household_id": [1, 2], "departement_id": ["03101", "03102"],
                          "commune_id": ["031010000000", "031020000000"]})
    homes = gpd.GeoDataFrame({"household_id": [1, 2], "geometry": [point(0), point(0)]}, crs=crs)
    gravity = gpd.GeoDataFrame({
        "person_id": [1, 2, 3], "destination_id": ["Near | 1", "Far | 9", "Far | 9"],
        "assignment_path": ["plan_length", "plan_length", "absent_plan_length"],
        "geometry": [point(5), point(60), point(60)]}, crs=crs)
    facilities = gpd.GeoDataFrame({"location_id": ["a", "b"], "capacity": [10.0, 10.0],
                                   "destination_id": ["Near | 1", "Far | 9"], "scope": ["local", "surrounding"],
                                   "site_ags8": ["03101000", "03241001"], "geometry": [point(5), point(60)]},
                                  crs=crs)
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2], "trip_index": [0, 1, 0, 1],
        "preceding_purpose": ["home", "education", "home", "outside"],
        "following_purpose": ["education", "home", "outside", "home"],
        "mode": ["car", "car", "pt", "pt"], "portal_leg": [False, False, True, True],
        "euclidean_distance": [5000.0, 5000.0, 60000.0, 60000.0]})
    return {"synthesis.population.enriched": persons, "synthesis.population.spatial.home.zones": zones,
            "synthesis.population.spatial.home.locations": homes,
            "braunschweig.synthesis.locations.education_gravity": gravity,
            "braunschweig.data.schools.university_facilities": facilities,
            S.TRIPS_STAGE: trips}


def _config(tmp_path, data_path, **override):
    config = {"data_path": str(data_path), "output_path": str(tmp_path / "out"),
              S.KEY_MAX_ROUTABLE_DISTANCE_M: LIMIT_M, S.KEY_MIN_BAND_PERSONS: S.DEFAULT_MIN_BAND_PERSONS,
              S.KEY_PORTAL_ENABLED: True, S.KEY_OUTPUT_SUBDIR: S.DEFAULT_OUTPUT_SUBDIR,
              S.KEY_NO_LEG_WARN_SHARE: S.DEFAULT_NO_LEG_WARN_SHARE}
    config.update(override)
    return config


def test_stage_writes_both_reports_on_a_toy_population(tmp_path):
    data_path = tmp_path / "data"
    relative = "braunschweig/mid/mid2023_student_education_distance_mode.csv"
    (data_path / relative).parent.mkdir(parents=True)
    shutil.copy(REAL_DATA / relative, data_path / relative)
    own = tmp_path / "cache"
    own.mkdir()
    result = S.execute(_ExecuteContext(_world(), _config(tmp_path, data_path), str(own)))
    out = tmp_path / "out" / S.DEFAULT_OUTPUT_SUBDIR
    modes = pd.read_csv(out / S.OUTPUT_FILES["modes"], dtype={"distance_band": str})
    # only person 2 is a resident-of-the-day out-commuter (person 3 is absent_*, person 1 goes to a local site)
    assert modes[["distance_band", "mode", "n_persons", "n_gated"]].to_dict("records") == [
        {"distance_band": "50_100", "mode": "pt", "n_persons": 1, "n_gated": 1}]
    assert modes["reference_band_pooled"].dtype == bool
    gated = pd.read_csv(out / S.OUTPUT_FILES["gated"], dtype={"home_kreis": str, "dest_kreis": str})
    assert gated[["home_kreis", "dest_kreis", "n_beyond", "n_gated"]].to_dict("records") == [
        {"home_kreis": "03101", "dest_kreis": "03241", "n_beyond": 1, "n_gated": 1}]
    report = json.loads((out / S.REPORT_FILE).read_text(encoding="utf-8"))
    assert "issue #678" in report["rule_state"] and report["counts"]["n_out_commuters"] == 1
    assert report["counts"]["n_gate_before_education"] == 0 and report["counts"]["n_beyond_not_gated_with_leg"] == 0
    assert report["counts"]["n_gate_without_education"] == 1   # person 2: the leg is the gate stay itself
    assert "pooled_reference_bands" in report and report["pipeline_commit"] is not None
    assert report["counts"]["n_gate_then_shorter_education_leg"] == 0
    assert set(report["columns_relied_on"]) == {"person_id", "trip_index", "mode", "preceding_purpose",
                                                "following_purpose", "portal_leg", "euclidean_distance"}
    assert result["report"]["status"] == S.STATUS


def test_configure_declares_the_inputs_and_refuses_a_run_without_the_portal_layer():
    recorder = _ConfigureRecorder()
    S.configure(recorder)
    assert S.TRIPS_STAGE in recorder.stages and S.GRAVITY_STAGE in recorder.stages
    assert recorder.keys[S.KEY_MAX_ROUTABLE_DISTANCE_M] == 45000.0
    with pytest.raises(ValueError, match="portal"):
        S.configure(_ConfigureRecorder({S.KEY_PORTAL_ENABLED: False}))
    with pytest.raises(ValueError, match="braunschweig.students.flows_enabled"):
        S.configure(_ConfigureRecorder({S.KEY_FLOWS_ENABLED: False}))
