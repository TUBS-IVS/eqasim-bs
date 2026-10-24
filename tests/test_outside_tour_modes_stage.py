"""The outside-tour-modes stage: declared keys, the OFF pass-through (output, counters, no warning), the pure
entry point on a fixture and the report (eqasim-bs#453)."""
import json
import logging
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.calibration import srv_mode_by_distance_band as M
from braunschweig.synthesis.outside_tour_modes import config_keys as keys
from braunschweig.synthesis.outside_tour_modes import stage
from braunschweig.synthesis.outside_tour_modes.srv_table import ModeShareTable

CRS = "EPSG:25832"


class _RecordingConfigureContext:
    def __init__(self, values=None):
        self.calls, self.stages, self._values = {}, [], values or {}

    def config(self, key, default=None, **kwargs):
        self.calls[key] = default
        return self._values.get(key, default)

    def stage(self, name, alias=None, **kwargs):
        self.stages.append(name)


def test_configure_declares_the_keys_and_the_inputs():
    context = _RecordingConfigureContext({"cordon_enabled": True})
    stage.configure(context)
    for key in (keys.KEY_ENABLED, keys.KEY_MIN_SHARE, keys.KEY_WARN_SHARE, "random_seed", "data_path",
                "cordon_enabled", "cordon_network_buffer_fraction"):
        assert key in context.calls
    assert context.calls[keys.KEY_MIN_SHARE] == keys.DEFAULT_MIN_SHARE
    assert context.stages[:4] == [keys.FINAL_TRIPS_STAGE, keys.LOCATIONS_STAGE, keys.PERSONS_STAGE,
                                  keys.MUNICIPALITIES_STAGE]
    # The timetable check (ADR-0159, on by default) routes on the pre-cut supply with the eqasim jar.
    assert {stage.SUPPLY_STAGE, stage.GTFS_STAGE, "matsim.runtime.eqasim"} <= set(context.stages[4:])
    assert context.calls[keys.KEY_LAST_RESORT] == "car_passenger"
    without_check = _RecordingConfigureContext({"cordon_enabled": True, keys.KEY_PT_CHECK_ENABLED: False})
    stage.configure(without_check)
    assert stage.SUPPLY_STAGE not in without_check.stages


def test_configure_off_declares_only_the_flag_and_the_trips_stage():
    context = _RecordingConfigureContext({keys.KEY_ENABLED: False})
    stage.configure(context)
    assert context.stages == [keys.FINAL_TRIPS_STAGE]
    assert set(context.calls) == {keys.KEY_ENABLED}


def test_configure_rejects_on_without_the_cordon():
    with pytest.raises(ValueError, match="cordon_enabled"):
        stage.configure(_RecordingConfigureContext({"cordon_enabled": False}))


def test_configure_rejects_an_invalid_min_share():
    with pytest.raises(ValueError, match=keys.KEY_MIN_SHARE.replace(".", r"\.")):
        stage.configure(_RecordingConfigureContext({"cordon_enabled": True, keys.KEY_MIN_SHARE: 1.5}))


class _ValidateContext:
    """Mimics synpp's ValidateContext: config() raises for an option the stage did not declare."""

    def __init__(self, values):
        self._values = values

    def config(self, option):
        if option not in self._values:
            raise KeyError(f"Config option {option} is not requested")
        return self._values[option]


def test_validate_token_is_a_stable_structured_token_and_needs_only_the_flag_when_off():
    context = _ValidateContext({keys.KEY_ENABLED: False})
    token = stage.validate(context)
    assert token == stage.validate(context) and token["format"] == 2 and token["modules"]
    assert "files" not in token


def test_validate_token_covers_the_committed_table_bytes_when_on(tmp_path):
    srv_dir = tmp_path.joinpath(*stage.SRV_SUBDIR)
    srv_dir.mkdir(parents=True)
    table_path = srv_dir / M.TABLE_FILE_NAME
    context = _ValidateContext({keys.KEY_ENABLED: True, "data_path": str(tmp_path)})
    with pytest.raises(FileNotFoundError, match=M.TABLE_FILE_NAME):
        stage.validate(context)
    table_path.write_text("# a\npurpose_group,band\n", encoding="utf-8")
    first = stage.validate(context)
    table_path.write_text("# b\npurpose_group,band\n", encoding="utf-8")
    second = stage.validate(context)
    assert first != second
    assert set(first["files"]) == {M.TABLE_FILE_NAME} and first["files"] != second["files"]
    assert first["modules"] == second["modules"]
    assert first != stage.validate(_ValidateContext({keys.KEY_ENABLED: False}))


def test_execute_off_passes_the_frame_through_without_counters_or_warnings(caplog, tmp_path):
    trips = pd.DataFrame({"person_id": [1], "trip_index": [0], "mode": ["walk"]})

    class _Context:
        def config(self, key):
            assert key == keys.KEY_ENABLED
            return False

        def stage(self, name):
            assert name == keys.FINAL_TRIPS_STAGE
            return trips

        def path(self):
            return str(tmp_path)

    with caplog.at_level(logging.INFO):
        result = stage.execute(_Context())
    assert result is trips
    assert not os.path.exists(os.path.join(str(tmp_path), keys.REPORT_FILE_NAME))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def _table(overrides=None):
    rows = []
    cells = {
        ("work", 0.0, 5.0): {"walk": 0.3, "bicycle": 0.3, "car": 0.3, "car_passenger": 0.05, "pt": 0.05},
        ("work", 5.0, float("inf")): {"walk": 0.0, "bicycle": 0.0, "car": 0.9, "car_passenger": 0.05, "pt": 0.05},
        ("education", 0.0, float("inf")): {"walk": 0.3, "bicycle": 0.3, "car": 0.2, "car_passenger": 0.1, "pt": 0.1},
        ("other", 0.0, float("inf")): {"walk": 0.3, "bicycle": 0.1, "car": 0.5, "car_passenger": 0.05, "pt": 0.05},
        ("all", 0.0, 45.0): {"walk": 0.3, "bicycle": 0.2, "car": 0.4, "car_passenger": 0.05, "pt": 0.05},
        ("all", 45.0, float("inf")): {"walk": 0.0, "bicycle": 0.0, "car": 0.7, "car_passenger": 0.05, "pt": 0.25},
    }
    cells.update(overrides or {})
    for (group, lower, upper), shares in cells.items():
        for mode in M.MODES:
            rows.append((group, M.band_label(lower, upper), lower, upper, mode, shares[mode], 100,
                         int(shares[mode] * 100)))
    return ModeShareTable.from_frame(pd.DataFrame(rows, columns=M.TABLE_COLUMNS))


def _fixture():
    extent = box(0.0, 0.0, 10000.0, 10000.0)
    persons = pd.DataFrame({"person_id": [1, 2, 3], "car_availability": ["all", "none", "all"],
                            "has_license": [True, False, True], "bicycle_availability": ["all", "all", "all"],
                            "car_passenger_availability": ["some", "none", "some"]})
    # person 1: walks 20 km to a workplace outside the extent (implausible -> re-moded, car only candidate
    #           with share 0.9 besides car_passenger/pt); person 2: walks 1 km inside (untouched);
    # person 3: drives to a portal gate (plausible in all/45_plus; untouched)
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2, 3, 3], "trip_index": [0, 1, 0, 1, 0, 1],
        "preceding_purpose": ["home", "work", "home", "shop", "home", "outside"],
        "following_purpose": ["work", "home", "shop", "home", "outside", "home"],
        "mode": ["walk", "walk", "walk", "walk", "car", "car"],
        "portal_leg": [False, False, False, False, True, True],
    })
    points = {(1, 0): (5000.0, 5000.0), (1, 1): (25000.0, 5000.0), (1, 2): (5000.0, 5000.0),
              (2, 0): (5000.0, 5000.0), (2, 1): (5000.0, 6000.0), (2, 2): (5000.0, 5000.0),
              (3, 0): (5000.0, 5000.0), (3, 1): (9900.0, 5000.0), (3, 2): (5000.0, 5000.0)}
    locations = gpd.GeoDataFrame({"person_id": [k[0] for k in points], "activity_index": [k[1] for k in points]},
                                 geometry=[Point(*xy) for xy in points.values()], crs=CRS)
    return dict(trips=trips, locations=locations, persons=persons, polygon=extent, table=_table(),
                min_share=0.01, warn_share=0.5, seed=7)


def test_remode_outside_tours_re_modes_the_frozen_walk_tour_only_and_reports_it(caplog):
    with caplog.at_level(logging.INFO):
        out, report = stage.remode_outside_tours(**_fixture())
    assert out["mode"].tolist()[2:] == ["walk", "walk", "car", "car"]
    assert out["mode"].tolist()[0] == out["mode"].tolist()[1] and out["mode"].tolist()[0] in {"car", "car_passenger", "pt"}
    assert report["n_outside_tours"] == 2 and report["n_outside_tours_remoded"] == 1
    assert report["n_outside_tours_by_group"] == {"work": 1, "all": 1}
    assert report["n_legs_remoded"] == 2 and report["n_legs_swept"] == 0
    assert report["n_portal_legs_as_all_group"] == 2
    assert list(report["substitution_matrix"]) == [f"walk->{out['mode'].tolist()[0]}"]
    assert report["cells_used"] == {"work/5_plus": 1}
    records = [r for r in caplog.records if "[outside_tour_modes]" in r.getMessage()]
    assert any("re-moded 1/2" in r.getMessage() for r in records)
    assert not [r for r in records if r.levelno >= logging.WARNING]


def test_remode_outside_tours_warns_above_the_warn_share(caplog):
    fixture = _fixture()
    fixture["warn_share"] = 0.4
    with caplog.at_level(logging.INFO):
        stage.remode_outside_tours(**fixture)
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "re-moded 1/2" in r.getMessage()]


def test_remode_outside_tours_raises_on_a_crs_mismatch():
    fixture = _fixture()
    fixture["locations"] = fixture["locations"].to_crs("EPSG:4326")
    fixture["municipalities_crs"] = CRS
    with pytest.raises(ValueError, match="CRS"):
        stage.remode_outside_tours(**fixture)


def test_execute_on_writes_the_report_and_returns_the_re_moded_frame(tmp_path, monkeypatch):
    fixture = _fixture()
    municipalities = gpd.GeoDataFrame({"id": [1]}, geometry=[box(0.0, 0.0, 10000.0, 10000.0)], crs=CRS)
    values = {keys.KEY_ENABLED: True, keys.KEY_MIN_SHARE: 0.01, keys.KEY_WARN_SHARE: 0.5, "random_seed": 1,
              "data_path": str(tmp_path), "cordon_network_buffer_fraction": 0.0,
              keys.KEY_LAST_RESORT: "car_passenger", keys.KEY_PT_CHECK_ENABLED: False}
    stages = {keys.FINAL_TRIPS_STAGE: fixture["trips"], keys.LOCATIONS_STAGE: fixture["locations"],
              keys.PERSONS_STAGE: fixture["persons"], keys.MUNICIPALITIES_STAGE: municipalities}
    monkeypatch.setattr(stage, "_load_table", lambda srv_dir: fixture["table"])

    class _Context:
        def config(self, key):
            return values[key]

        def stage(self, name):
            return stages[name]

        def path(self):
            return str(tmp_path)

    out = stage.execute(_Context())
    assert out["mode"].tolist()[0] in {"car", "car_passenger", "pt"}
    with open(os.path.join(str(tmp_path), keys.REPORT_FILE_NAME), encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["enabled"] is True and report["n_outside_tours_remoded"] == 1


def test_remode_outside_tours_keeps_order_index_and_columns_of_a_shuffled_input():
    fixture = _fixture()
    shuffled = fixture["trips"].sample(frac=1.0, random_state=3)
    fixture["trips"] = shuffled
    out, report = stage.remode_outside_tours(**fixture)
    assert out.index.tolist() == shuffled.index.tolist() and list(out.columns) == list(shuffled.columns)
    by_person = out.groupby("person_id")["mode"].agg(lambda modes: sorted(set(modes)))
    assert by_person[2] == ["walk"] and by_person[3] == ["car"]
    assert len(by_person[1]) == 1 and by_person[1][0] != "walk"
    assert shuffled["mode"].tolist() == fixture["trips"]["mode"].tolist()
    assert report["n_outside_tours_remoded"] == 1


def test_remode_outside_tours_without_an_implausible_leg_changes_nothing_and_reports_zero(caplog):
    fixture = _fixture()
    fixture["trips"] = fixture["trips"][fixture["trips"]["person_id"] != 1]
    with caplog.at_level(logging.INFO):
        out, report = stage.remode_outside_tours(**fixture)
    assert out["mode"].tolist() == fixture["trips"]["mode"].tolist()
    assert report["n_outside_tours"] == 1 and report["n_outside_tours_remoded"] == 0
    assert report["share_outside_tours_remoded"] == 0.0 and report["substitution_matrix"] == {}
    assert report["cells_used"] == {} and report["n_legs_remoded"] == 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------------------------------------------
# Final-review fixes (eqasim-bs#453): relaxation and post-condition (A1), portal gate class (A2), unknown donor
# modes (A3), categorical mode column (A4).

def _with_portal_person(portal_mode, availability="all"):
    """The base fixture plus person 4: walks 8 km to a workplace inside the extent (implausible in work/5_plus),
    then a portal stay (work -> outside -> home) whose gate mode is ``portal_mode``."""
    fixture = _fixture()
    extra = pd.DataFrame({
        "person_id": [4, 4, 4], "trip_index": [0, 1, 2],
        "preceding_purpose": ["home", "work", "outside"], "following_purpose": ["work", "outside", "home"],
        "mode": ["walk", portal_mode, portal_mode], "portal_leg": [False, True, True]})
    fixture["trips"] = pd.concat([fixture["trips"], extra], ignore_index=True)
    points = {(4, 0): (1000.0, 5000.0), (4, 1): (9000.0, 5000.0), (4, 2): (9900.0, 5000.0),
              (4, 3): (1000.0, 5000.0)}
    more = gpd.GeoDataFrame({"person_id": [k[0] for k in points], "activity_index": [k[1] for k in points]},
                            geometry=[Point(*xy) for xy in points.values()], crs=CRS)
    fixture["locations"] = pd.concat([fixture["locations"], more], ignore_index=True)
    fixture["persons"] = pd.concat([fixture["persons"], pd.DataFrame({
        "person_id": [4], "car_availability": [availability], "has_license": [availability == "all"],
        "bicycle_availability": [availability], "car_passenger_availability": [availability]})],
        ignore_index=True)
    return fixture


def _fixture_with_an_unserved_pt_tour():
    """The base fixture with times, plus person 5 (no car, no lift): a pt leisure tour to (30 km, 5 km), outside the
    extent, leaving at 18:00 and returning at 22:00."""
    fixture = _fixture()
    hours = {1: (8.0, 8.5, 17.0, 17.5), 2: (9.0, 9.2, 10.0, 10.2), 3: (7.0, 7.5, 16.0, 16.5)}
    trips = fixture["trips"]
    trips["departure_time"] = [hours[p][0 if i == 0 else 2] * 3600.0 for p, i in zip(trips["person_id"],
                                                                                       trips["trip_index"])]
    trips["arrival_time"] = [hours[p][1 if i == 0 else 3] * 3600.0 for p, i in zip(trips["person_id"],
                                                                                     trips["trip_index"])]
    extra = pd.DataFrame({"person_id": [5, 5], "trip_index": [0, 1], "preceding_purpose": ["home", "leisure"],
                          "following_purpose": ["leisure", "home"], "mode": ["pt", "pt"],
                          "portal_leg": [False, False], "departure_time": [18 * 3600.0, 22 * 3600.0],
                          "arrival_time": [18.5 * 3600.0, 22.5 * 3600.0]})
    fixture["trips"] = pd.concat([trips, extra], ignore_index=True)
    points = {(5, 0): (5000.0, 5000.0), (5, 1): (30000.0, 5000.0), (5, 2): (5000.0, 5000.0)}
    fixture["locations"] = pd.concat([fixture["locations"], gpd.GeoDataFrame(
        {"person_id": [k[0] for k in points], "activity_index": [k[1] for k in points]},
        geometry=[Point(*xy) for xy in points.values()], crs=CRS)], ignore_index=True)
    fixture["persons"] = pd.concat([fixture["persons"], pd.DataFrame({
        "person_id": [5], "car_availability": ["none"], "has_license": [False], "bicycle_availability": ["none"],
        "car_passenger_availability": ["none"]})], ignore_index=True)
    return fixture


def test_the_pt_check_moves_an_unserved_non_portal_pt_tour_and_writes_its_times_back():
    # #465, ADR-0159 (persons 125291 / 905013 of the 1 % rerun): a pt leisure tour to a destination outside the
    # extent whose return (22:00) leaves after the last service (21:00). The check runs after the re-moding, probes
    # only the pt legs of non-portal outside tours, and the whole tour moves one hour earlier in the returned frame.
    fixture = _fixture_with_an_unserved_pt_tour()
    probed = []

    def probe(tasks):
        probed.append(tasks.copy())
        return pd.Series(tasks["departure_time"].le(21 * 3600.0).to_numpy(),
                         index=tasks["identifier"].astype(str).to_numpy())

    from braunschweig.synthesis.outside_tour_modes import pt_connections

    out, report = stage.remode_outside_tours(**fixture, last_resort="car_passenger",
                                             pt_check=pt_connections.PtCheck(probe=probe))
    person5 = out[out["person_id"] == 5]
    assert person5["mode"].tolist() == ["pt", "pt"]
    assert person5["departure_time"].tolist() == [17 * 3600.0, 21 * 3600.0]
    assert person5["arrival_time"].tolist() == [17.5 * 3600.0, 21.5 * 3600.0]
    others = out["person_id"] != 5
    assert out.loc[others, "departure_time"].tolist() == fixture["trips"].loc[others, "departure_time"].tolist()
    assert out.loc[out["person_id"] == 3, "mode"].tolist() == ["car", "car"]       # portal tour: never probed
    assert all(9900.0 not in set(tasks["origin_x"]) for tasks in probed)        # the gate end of person 3
    assert report["pt_connection"]["actions"]["tour_shifted"] == 1
    assert report["pt_connection"]["actions"]["kept_no_connection"] == 0


def test_only_the_first_probe_of_the_pt_check_treats_a_batch_without_any_route_as_a_broken_run():
    # PR #505 review: the first probe covers every checked pt leg, so no route at all there means a broken router;
    # the narrowed per-offset batches that follow may legitimately find no route for their one or two tours.
    calls = []

    def router(tasks, allow_no_route_at_all=False):
        calls.append(allow_no_route_at_all)
        return "trips", "legs"

    probe = stage._probe_from(router, lambda trips, legs, tasks: pd.Series(dtype=bool))
    tasks = pd.DataFrame({"identifier": ["0"]})
    probe(tasks)
    probe(tasks)
    probe(tasks)
    assert calls == [False, True, True]


def test_execute_with_the_pt_check_on_wires_the_production_probe(tmp_path, monkeypatch):
    # PR #505 review: the production probe (_pt_check) -- router config and work dir, the bound of the simulated day
    # minus the day-end margin, the connection rule over ALL schedule stops, and the strict first call -- runs here
    # with a fake batch router: a trip departing by 21:00 is a pt trip between two stops that exist only in the
    # uncut schedule, a later one an only-walk of 25 km.
    from braunschweig.data.transit import connections, schedule as schedule_module

    fixture = _fixture_with_an_unserved_pt_tour()
    municipalities = gpd.GeoDataFrame({"id": [1]}, geometry=[box(0.0, 0.0, 10000.0, 10000.0)], crs=CRS)
    monkeypatch.setattr(stage, "_load_table", lambda srv_dir: fixture["table"])
    monkeypatch.setattr(schedule_module, "read_schedule",
                        lambda path: type("Schedule", (), {"stops": {"far_a": None, "far_b": None}})())
    calls = []

    def fake_route_batch(context, tasks, *, config_path, threads, work_dir, allow_no_route_at_all=False):
        calls.append({"config": config_path, "work_dir": work_dir, "threads": threads,
                      "allow": allow_no_route_at_all, "n": len(tasks)})
        served = tasks["departure_time"].le(21 * 3600.0).to_numpy()
        ids = tasks["identifier"].astype(str).to_numpy()
        trips = pd.DataFrame({"identifier": ids, "is_only_walk": np.where(served, 0, 1),
                              "access_distance_km": np.where(served, 0.2, 25.0), "egress_distance_km": 0.2,
                              "transfer_distance_km": 0.0, "total_walk_distance_km": np.where(served, 0.4, 25.0)})
        legs = pd.DataFrame({"identifier": ids[served], "access_stop_id": "far_a", "egress_stop_id": "far_b"})
        return trips, legs

    monkeypatch.setattr(connections, "route_batch", fake_route_batch)
    supply_dir, gtfs_dir = tmp_path / "supply", tmp_path / "gtfs"
    values = {keys.KEY_ENABLED: True, keys.KEY_MIN_SHARE: 0.01, keys.KEY_WARN_SHARE: 0.5, "random_seed": 7,
              "data_path": str(tmp_path), "cordon_network_buffer_fraction": 0.0,
              keys.KEY_LAST_RESORT: "car_passenger", keys.KEY_PT_CHECK_ENABLED: True,
              keys.KEY_PT_SHIFT_WINDOW_S: 7200.0, keys.KEY_PT_SHIFT_STEP_S: 1800.0,
              keys.KEY_PT_NO_CONNECTION_WARN_SHARE: 0.05, "matsim_threads": 3, "processes": 8,
              "braunschweig.day_end_margin.enabled": True,
              "braunschweig.day_end_margin.last_arrival_margin_s": 3600.0}
    stages = {keys.FINAL_TRIPS_STAGE: fixture["trips"], keys.LOCATIONS_STAGE: fixture["locations"],
              keys.PERSONS_STAGE: fixture["persons"], keys.MUNICIPALITIES_STAGE: municipalities,
              stage.SUPPLY_STAGE: {"schedule_path": "schedule.xml.gz", "network_path": "network.xml.gz"},
              stage.GTFS_STAGE: {"vehicles_path": "vehicles.xml.gz"}}
    paths = {None: str(tmp_path), stage.SUPPLY_STAGE: str(supply_dir), stage.GTFS_STAGE: str(gtfs_dir)}

    class _Context:
        def config(self, key):
            return values[key]

        def stage(self, name):
            return stages[name]

        def path(self, name=None):
            return paths[name]

    out = stage.execute(_Context())
    person5 = out[out["person_id"] == 5]
    assert person5["mode"].tolist() == ["pt", "pt"]
    assert person5["departure_time"].tolist() == [17 * 3600.0, 21 * 3600.0]
    assert [c["allow"] for c in calls] == [False] + [True] * (len(calls) - 1) and len(calls) >= 2
    assert all(c["threads"] == 3 for c in calls)
    assert calls[0]["config"] == os.path.join(str(tmp_path), stage.ROUTER_CONFIG_NAME)
    assert calls[0]["work_dir"] == os.path.join(str(tmp_path), stage.ROUTER_WORK_DIR_NAME)
    with open(os.path.join(str(tmp_path), keys.REPORT_FILE_NAME), encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["pt_connection"]["bound_s"] == 29 * 3600.0
    assert report["pt_connection"]["actions"]["tour_shifted"] == 1


def test_portal_tour_at_a_road_gate_is_never_moved_to_pt_and_is_reported():
    for seed in range(25):
        fixture = _with_portal_person("car")
        fixture["seed"] = seed
        out, report = stage.remode_outside_tours(**fixture)
        tour = out[out["person_id"] == 4]["mode"].tolist()
        assert len(set(tour)) == 1 and tour[0] in {"car", "car_passenger"}
        assert report["n_portal_tours_remoded"] == 1 and report["n_portal_tours_class_restricted"] == 1


def test_portal_tour_at_a_rail_gate_stays_pt_and_is_reported():
    fixture = _with_portal_person("pt")
    out, report = stage.remode_outside_tours(**fixture)
    assert out[out["person_id"] == 4]["mode"].tolist() == ["pt", "pt", "pt"]
    assert report["n_portal_tours_remoded"] == 1 and report["n_portal_tours_class_restricted"] == 1


def test_a_trips_frame_without_the_portal_leg_column_warns_when_outside_activities_exist(caplog):
    fixture = _fixture()
    fixture["trips"] = fixture["trips"].drop(columns=["portal_leg"])
    with caplog.at_level(logging.INFO):
        _, report = stage.remode_outside_tours(**fixture)
    assert report["portal_leg_column_present"] is False and report["n_portal_tours_remoded"] == 0
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "portal_leg" in r.getMessage()]


def test_the_report_carries_the_portal_flag_column_presence_and_no_warning_when_it_is_present(caplog):
    with caplog.at_level(logging.INFO):
        _, report = stage.remode_outside_tours(**_fixture())
    assert report["portal_leg_column_present"] is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def _relaxation_fixture():
    """Person 1: 20 km on foot to a workplace outside the extent (work/5_plus: walk 0.0), 1 km shop leg, 19 km back.

    The shop cell (other) is plausible for walk and bicycle only, the work cell for car, car_passenger and pt:
    the strict intersection is empty, so the draw relaxes to the cell of the longest leg (work/5_plus) and the
    shop-cell legs end implausible after the draw (counted by the post-condition).
    """
    fixture = _fixture()
    overrides = {("other", 0.0, float("inf")): {"walk": 0.5, "bicycle": 0.5, "car": 0.0, "car_passenger": 0.0,
                                                "pt": 0.0}}
    fixture["table"] = _table(overrides)
    fixture["trips"] = pd.DataFrame({
        "person_id": [1, 1, 1], "trip_index": [0, 1, 2],
        "preceding_purpose": ["home", "work", "shop"], "following_purpose": ["work", "shop", "home"],
        "mode": ["walk", "walk", "walk"], "portal_leg": [False, False, False]})
    fixture["persons"] = fixture["persons"][fixture["persons"]["person_id"] == 1]
    points = {(1, 0): (5000.0, 5000.0), (1, 1): (25000.0, 5000.0), (1, 2): (24000.0, 5000.0),
              (1, 3): (5000.0, 5000.0)}
    fixture["locations"] = gpd.GeoDataFrame(
        {"person_id": [1] * 4, "activity_index": [0, 1, 2, 3]},
        geometry=[Point(*points[(1, i)]) for i in range(4)], crs=CRS)
    return fixture


def test_relaxed_draws_and_the_post_condition_are_counted_and_logged(caplog):
    with caplog.at_level(logging.INFO):
        out, report = stage.remode_outside_tours(**_relaxation_fixture())
    assert out["mode"].nunique() == 1 and out["mode"].iloc[0] in {"car", "car_passenger", "pt"}
    assert report["n_draws_relaxed_to_longest_leg"] == 1
    assert report["share_draws_relaxed_to_longest_leg"] == 1.0
    # The substitute is plausible in work/5_plus (the cell of the longest leg) but not in the shop cell (walk,
    # bicycle only): the 1 km shop leg and the 19 km home-bound leg (judged by its origin purpose, shop) remain.
    assert report["n_outside_legs_implausible_after"] == 2
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "implausible after" in r.getMessage()]
    assert any("relaxed" in r.getMessage() for r in caplog.records if r.levelno == logging.INFO)


def test_no_implausible_outside_leg_remains_when_the_strict_draw_succeeds(caplog):
    with caplog.at_level(logging.INFO):
        _, report = stage.remode_outside_tours(**_fixture())
    assert report["n_outside_legs_implausible_after"] == 0 and report["n_draws_relaxed_to_longest_leg"] == 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_unknown_donor_modes_are_counted_by_mode_and_logged_at_warning(caplog):
    fixture = _fixture()
    fixture["trips"] = fixture["trips"].copy()
    fixture["trips"].loc[fixture["trips"]["person_id"] == 1, "mode"] = "ride_hailing"
    with caplog.at_level(logging.INFO):
        out, report = stage.remode_outside_tours(**fixture)
    assert report["n_unknown_donor_mode"] == 2 and report["unknown_donor_modes"] == {"ride_hailing": 2}
    assert set(out.loc[out["person_id"] == 1, "mode"]) <= {"car", "car_passenger", "pt"}
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "ride_hailing" in r.getMessage()]


def test_no_unknown_donor_mode_means_a_zero_count_and_no_warning(caplog):
    with caplog.at_level(logging.INFO):
        _, report = stage.remode_outside_tours(**_fixture())
    assert report["n_unknown_donor_mode"] == 0 and report["unknown_donor_modes"] == {}


def test_remode_outside_tours_refuses_a_categorical_mode_column_even_when_no_tour_is_re_moded():
    fixture = _fixture()
    fixture["trips"] = fixture["trips"][fixture["trips"]["person_id"] != 1].copy()   # nothing to re-mode
    fixture["trips"]["mode"] = fixture["trips"]["mode"].astype("category")
    with pytest.raises(ValueError, match="convert it to plain strings"):
        stage.remode_outside_tours(**fixture)


def test_the_validate_token_covers_the_module_that_defines_the_portal_flag_column():
    from braunschweig.synthesis.portal_trips import rewrite

    assert stage.PORTAL_LEG_COLUMN == rewrite.PORTAL_LEG_COLUMN == "portal_leg"
    assert rewrite in stage._HELPER_MODULES


def test_road_gate_walk_portal_tour_of_a_person_with_only_pt_and_walk_is_left_unchanged(caplog):
    # Ruling R26: pt is removed by the road gate class, walk is implausible in every cell of the tour -> no
    # candidate even after the relaxation; the portal tour keeps its donor modes and the run does not abort.
    fixture = _with_portal_person("walk", availability="none")
    with caplog.at_level(logging.INFO):
        out, report = stage.remode_outside_tours(**fixture)
    assert out[out["person_id"] == 4]["mode"].tolist() == ["walk", "walk", "walk"]
    assert report["n_portal_tours_left_unchanged"] == 1
    assert report["portal_tours_left_unchanged_by_reason"] == {"mixed_gate_class": 0, "no_candidate_in_gate_class": 1}
    assert report["n_portal_tours_remoded"] == 0
    assert report["n_outside_tours_remoded"] == 1                       # person 1 only
    assert report["n_outside_legs_implausible_after"] == 3              # the unchanged tour's walk legs
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "left unchanged" in r.getMessage()]
    assert report["n_portal_tours_lifted"] == 0


def test_road_gate_walk_portal_tour_without_a_candidate_becomes_a_lift_with_the_lift_last_resort():
    # #465, ADR-0159 decision 4 (person 343377 of the 1 % rerun: an 82.5 km walk to a road gate, stuck at the end of
    # the day): with the lift last resort the tour becomes car_passenger (a road mode, so the gate class holds)
    # although the person has no passenger availability; it is counted, not left unchanged.
    fixture = _with_portal_person("walk", availability="none")
    out, report = stage.remode_outside_tours(**fixture, last_resort="car_passenger")
    assert out[out["person_id"] == 4]["mode"].tolist() == ["car_passenger"] * 3
    assert report["n_portal_tours_lifted"] == 1 and report["n_portal_tours_left_unchanged"] == 0
    assert report["n_portal_tours_remoded"] == 1


def test_portal_tour_with_both_gate_classes_is_left_unchanged_at_stage_level():
    fixture = _with_portal_person("pt")
    trips = fixture["trips"]
    extra = pd.DataFrame({
        "person_id": [5] * 4, "trip_index": [0, 1, 2, 3],
        "preceding_purpose": ["home", "work", "outside", "shop"],
        "following_purpose": ["work", "outside", "shop", "home"],
        "mode": ["walk", "pt", "car", "car"], "portal_leg": [False, True, True, False]})
    fixture["trips"] = pd.concat([trips, extra], ignore_index=True)
    points = {(5, 0): (1000.0, 5000.0), (5, 1): (9000.0, 5000.0), (5, 2): (9900.0, 5000.0),
              (5, 3): (9500.0, 5000.0), (5, 4): (1000.0, 5000.0)}
    more = gpd.GeoDataFrame({"person_id": [k[0] for k in points], "activity_index": [k[1] for k in points]},
                            geometry=[Point(*xy) for xy in points.values()], crs=CRS)
    fixture["locations"] = pd.concat([fixture["locations"], more], ignore_index=True)
    fixture["persons"] = pd.concat([fixture["persons"], pd.DataFrame({
        "person_id": [5], "car_availability": ["all"], "has_license": [True], "bicycle_availability": ["all"],
        "car_passenger_availability": ["all"]})], ignore_index=True)
    out, report = stage.remode_outside_tours(**fixture)
    assert out[out["person_id"] == 5]["mode"].tolist() == ["walk", "pt", "car", "car"]
    assert report["portal_tours_left_unchanged_by_reason"] == {"mixed_gate_class": 1, "no_candidate_in_gate_class": 0}
    assert report["n_portal_tours_left_unchanged"] == 1


def test_no_portal_tour_is_left_unchanged_in_the_plain_fixture(caplog):
    with caplog.at_level(logging.INFO):
        _, report = stage.remode_outside_tours(**_fixture())
    assert report["n_portal_tours_left_unchanged"] == 0
    assert report["portal_tours_left_unchanged_by_reason"] == {"mixed_gate_class": 0, "no_candidate_in_gate_class": 0}
