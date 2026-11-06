"""Batch pt router wrapper and connection rule (#465, WP-C, C4)."""
import pandas as pd
import pytest

from braunschweig.data.transit import connections
from braunschweig.matsim.scenario import day_end_margin
from braunschweig.popsim.plan_validation import MAX_PLAN_TIME_SECONDS

INSIDE = {"s_in_a", "s_in_b"}
WALK_MAX_M = 5000.0


def _trips(rows):
    base = dict(is_only_walk=0, access_distance_km=0.3, egress_distance_km=0.3, transfer_distance_km=0.0)
    out = pd.DataFrame([{**base, **r} for r in rows])
    # The Java tool writes total_walk_distance_km = access + egress + transfer (BatchPublicTransportRouter).
    if "total_walk_distance_km" not in out:
        out["total_walk_distance_km"] = (out["access_distance_km"] + out["egress_distance_km"]
                                         + out["transfer_distance_km"])
    return out


def _legs(rows):
    """Rows are (identifier, leg_index, access_stop, egress_stop) or, with the time, plus egress_time (s)."""
    columns = ["identifier", "leg_index", "access_stop_id", "egress_stop_id"]
    if rows and len(rows[0]) == 5:
        columns.append("egress_time")
    return pd.DataFrame(rows, columns=columns)


def _mask(trips, legs):
    return connections.connection_mask(trips, legs, inside_stop_ids=INSIDE, walk_max_m=WALK_MAX_M)


def test_connection_rule_cases():
    # Real Java behaviour for an only-walk trip: the single walk leg is the FIRST leg, so the whole walk is
    # in access_distance_km and egress_distance_km stays 0 (BatchPublicTransportRouter, isFirstLeg branch).
    trips = _trips([
        {"identifier": "ok"},
        {"identifier": "egress_outside"},
        {"identifier": "short_walk", "is_only_walk": 1, "access_distance_km": 0.4, "egress_distance_km": 0.0},
        {"identifier": "long_walk", "is_only_walk": 1, "access_distance_km": 3.0, "egress_distance_km": 0.0},
        {"identifier": "far_access", "access_distance_km": 6.0},
        {"identifier": "far_transfer", "transfer_distance_km": 5.5},
        {"identifier": "no_legs"},
        {"identifier": "blank_id"},
        {"identifier": "walk_unknown", "is_only_walk": float("nan")},
        # Java books a pt trip's egress walk as TRANSFER (the isLastLeg test counts interaction activities),
        # so egress stays 0; the rule treats access, egress and transfer alike (shared walk_max_m).
        {"identifier": "egress_as_transfer", "egress_distance_km": 0.0, "transfer_distance_km": 0.4},
        {"identifier": "short_walk_split", "is_only_walk": 1, "access_distance_km": 0.3,
         "egress_distance_km": 0.2},
        {"identifier": "long_walk_split", "is_only_walk": 1, "access_distance_km": 0.4,
         "egress_distance_km": 0.3},
    ])
    legs = _legs([
        ("ok", 0, "s_in_a", "s_in_b"),
        ("egress_outside", 0, "s_in_a", "s_out"),
        ("far_access", 0, "s_in_a", "s_in_b"),
        ("far_transfer", 0, "s_in_a", "s_in_b"),
        ("blank_id", 0, "s_in_a", ""),
        ("walk_unknown", 0, "s_in_a", "s_in_b"),
        ("egress_as_transfer", 0, "s_in_a", "s_in_b"),
    ])
    mask = _mask(trips, legs)
    assert mask.to_dict() == {"ok": True, "egress_outside": False, "short_walk": True,
                              "long_walk": False, "far_access": False, "far_transfer": False,
                              "no_legs": False, "blank_id": False, "walk_unknown": False,
                              "egress_as_transfer": True, "short_walk_split": True,
                              "long_walk_split": False}


def test_duplicate_trip_identifiers_raise():
    trips = _trips([{"identifier": "a"}, {"identifier": "a"}])
    with pytest.raises(ValueError, match="duplicate"):
        _mask(trips, _legs([("a", 0, "s_in_a", "s_in_b")]))


def test_plausible_walk_boundary_is_inclusive():
    trips = _trips([
        {"identifier": "at", "is_only_walk": 1, "access_distance_km": 0.6, "egress_distance_km": 0.0},
        {"identifier": "above", "is_only_walk": 1, "access_distance_km": 0.61, "egress_distance_km": 0.0},
    ])
    mask = _mask(trips, _legs([]))
    assert mask.to_dict() == {"at": True, "above": False}


TRIPS_HEADER = ("identifier,is_only_walk,access_distance_km,egress_distance_km,transfer_distance_km,"
                "total_walk_distance_km")
LEGS_HEADER = "identifier,leg_index,access_stop_id,egress_stop_id"


def _fake_run(trips_csv, legs_csv):
    def run(context, command, arguments):
        args = dict(zip(arguments[::2], arguments[1::2]))
        run.calls.append((command, args))
        with open(args["--output-trips-path"], "w", encoding="utf-8") as f:
            f.write(trips_csv)
        with open(args["--output-legs-path"], "w", encoding="utf-8") as f:
            f.write(legs_csv)
    run.calls = []
    return run


def _tasks(x=1.0):
    return pd.DataFrame({"identifier": ["t1"], "origin_x": [x], "origin_y": [2.0],
                         "destination_x": [3.0], "destination_y": [4.0], "departure_time": [3600.0],
                         "extra": ["dropped"]})


def test_route_batch_roundtrip_and_columns(monkeypatch, tmp_path):
    run = _fake_run(TRIPS_HEADER + "\nt1,0,0.3,0.3,0,0.6\n",
                    LEGS_HEADER + "\nt1,0,0012,s_in_b\nt1,1,,\n")
    monkeypatch.setattr(connections.eqasim, "run", run)
    trips, legs = connections.route_batch(None, _tasks(), config_path="cfg.xml", threads=2,
                                          work_dir=str(tmp_path))
    written = pd.read_csv(tmp_path / "router_tasks.csv")
    assert list(written.columns) == connections.TASK_COLUMNS
    command, args = run.calls[0]
    assert command == "org.eqasim.core.tools.routing.RunBatchPublicTransportRouter"
    assert args["--threads"] == "2" and args["--config-path"] == "cfg.xml"
    assert "access_stop_id" in legs.columns
    # stop ids stay strings (leading zeros kept); blank ids stay empty strings, not NaN
    assert list(legs["access_stop_id"]) == ["0012", ""]
    assert len(trips) == 1


def test_route_batch_keeps_numeric_looking_identifiers_as_strings(monkeypatch, tmp_path):
    run = _fake_run(TRIPS_HEADER + "\n007,0,0.3,0.3,0,0.6\n", LEGS_HEADER + "\n007,0,1,2\n")
    monkeypatch.setattr(connections.eqasim, "run", run)
    tasks = _tasks().assign(identifier=["007"])
    trips, legs = connections.route_batch(None, tasks, config_path="c", threads=1, work_dir=str(tmp_path))
    assert list(trips["identifier"]) == ["007"] and list(legs["identifier"]) == ["007"]


def test_route_batch_rejects_unexpected_identifiers(monkeypatch, tmp_path):
    monkeypatch.setattr(connections.eqasim, "run", _fake_run(
        TRIPS_HEADER + "\nt1,0,0.3,0.3,0,0.6\nt9,0,0.3,0.3,0,0.6\n", LEGS_HEADER + "\n"))
    with pytest.raises(ValueError, match="unexpected 1"):
        connections.route_batch(None, _tasks(), config_path="c", threads=1, work_dir=str(tmp_path))


def test_route_batch_rejects_duplicate_trip_identifiers(monkeypatch, tmp_path):
    monkeypatch.setattr(connections.eqasim, "run", _fake_run(
        TRIPS_HEADER + "\nt1,0,0.3,0.3,0,0.6\nt1,0,0.3,0.3,0,0.6\n", LEGS_HEADER + "\n"))
    with pytest.raises(ValueError, match="duplicate"):
        connections.route_batch(None, _tasks(), config_path="c", threads=1, work_dir=str(tmp_path))


def test_route_batch_rejects_duplicate_task_identifiers_before_java(monkeypatch, tmp_path):
    run = _fake_run("", "")
    monkeypatch.setattr(connections.eqasim, "run", run)
    tasks = pd.concat([_tasks(), _tasks()], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate task identifiers"):
        connections.route_batch(None, tasks, config_path="c", threads=1, work_dir=str(tmp_path))
    assert run.calls == []


def _two_tasks():
    return pd.concat([_tasks(), _tasks().assign(identifier=["t2"])], ignore_index=True)


def test_route_batch_missing_trips_rows_are_a_router_outcome(monkeypatch, tmp_path, caplog):
    # Java writes no trips row when calcRoute returns null (no route): count it, do not raise.
    monkeypatch.setattr(connections.eqasim, "run", _fake_run(
        TRIPS_HEADER + "\nt1,0,0.3,0.3,0,0.6\n", LEGS_HEADER + "\nt1,0,s_in_a,s_in_b\n"))
    with caplog.at_level("INFO"):
        trips, _ = connections.route_batch(None, _two_tasks(), config_path="c", threads=1,
                                           work_dir=str(tmp_path))
    assert list(trips["identifier"]) == ["t1"]
    infos = [r for r in caplog.records if "no trips row" in r.getMessage()]
    assert infos and infos[0].levelname == "INFO" and "1/2" in infos[0].getMessage()


def test_route_batch_warns_when_most_tasks_have_no_trips_row(monkeypatch, tmp_path, caplog):
    tasks = pd.concat([_tasks().assign(identifier=[f"t{i}"]) for i in range(3)], ignore_index=True)
    monkeypatch.setattr(connections.eqasim, "run", _fake_run(
        TRIPS_HEADER + "\nt0,0,0.3,0.3,0,0.6\n", LEGS_HEADER + "\n"))
    with caplog.at_level("INFO"):
        connections.route_batch(None, tasks, config_path="c", threads=1, work_dir=str(tmp_path))
    assert any(r.levelname == "WARNING" and "no trips row" in r.getMessage() for r in caplog.records)


def test_route_batch_raises_when_all_tasks_have_no_trips_row(monkeypatch, tmp_path):
    monkeypatch.setattr(connections.eqasim, "run", _fake_run(TRIPS_HEADER + "\n", LEGS_HEADER + "\n"))
    with pytest.raises(ValueError, match="missing 1"):
        connections.route_batch(None, _tasks(), config_path="c", threads=1, work_dir=str(tmp_path))


def test_route_batch_can_return_a_narrowed_batch_without_any_route(monkeypatch, tmp_path):
    # PR #505 review: a follow-up batch of one or two tasks with no route at all is a router outcome, not a broken run;
    # the caller allows it explicitly and the tasks count as not connected (reason no_route).
    monkeypatch.setattr(connections.eqasim, "run", _fake_run(TRIPS_HEADER + "\n", LEGS_HEADER + "\n"))
    trips, legs = connections.route_batch(None, _tasks(), config_path="c", threads=1, work_dir=str(tmp_path),
                                          allow_no_route_at_all=True)
    mask = connections.connection_mask(trips, legs, inside_stop_ids=set(), walk_max_m=5000.0, task_ids=["t1"])
    assert mask.to_dict() == {"t1": False}


def test_route_batch_rejects_legs_without_stop_id_columns(monkeypatch, tmp_path):
    monkeypatch.setattr(connections.eqasim, "run", _fake_run(
        TRIPS_HEADER + "\nt1,0,0.3,0.3,0,0.6\n", "identifier,leg_index\nt1,0\n"))
    with pytest.raises(ValueError, match="feat/batch-router-leg-stops"):
        connections.route_batch(None, _tasks(), config_path="c", threads=1, work_dir=str(tmp_path))


def test_route_batch_rejects_trips_without_required_columns(monkeypatch, tmp_path):
    monkeypatch.setattr(connections.eqasim, "run", _fake_run(
        "identifier,is_only_walk\nt1,0\n", LEGS_HEADER + "\nt1,0,s_in_a,s_in_b\n"))
    with pytest.raises(ValueError, match="access_distance_km"):
        connections.route_batch(None, _tasks(), config_path="c", threads=1, work_dir=str(tmp_path))


def test_connection_mask_rejects_missing_columns():
    with pytest.raises(ValueError, match="egress_stop_id"):
        _mask(_trips([{"identifier": "a"}]), pd.DataFrame({"identifier": ["a"], "access_stop_id": ["s_in_a"]}))
    with pytest.raises(ValueError, match="is_only_walk"):
        _mask(pd.DataFrame({"identifier": ["a"]}), _legs([]))


def test_connection_mask_task_without_trips_row_is_no_route(caplog):
    trips = _trips([{"identifier": "ok"}])
    legs = _legs([("ok", 0, "s_in_a", "s_in_b")])
    with caplog.at_level("INFO"):
        mask = connections.connection_mask(trips, legs, inside_stop_ids=INSIDE, walk_max_m=WALK_MAX_M,
                                           task_ids=["ok", "lost"])
    assert mask.to_dict() == {"ok": True, "lost": False}
    assert any("no_route 1" in r.getMessage() for r in caplog.records)


def test_route_batch_rejects_nan_coordinates_before_java(monkeypatch, tmp_path):
    run = _fake_run("", "")
    monkeypatch.setattr(connections.eqasim, "run", run)
    with pytest.raises(ValueError, match="origin_x"):
        connections.route_batch(None, _tasks(float("nan")), config_path="c", threads=1,
                                work_dir=str(tmp_path))
    assert run.calls == []


def test_write_router_config(tmp_path):
    path = connections.write_router_config(str(tmp_path / "c.xml"), network_path="net.xml.gz",
                                           schedule_path="sched.xml.gz", vehicles_path="veh.xml.gz")
    text = (tmp_path / "c.xml").read_text(encoding="utf-8")
    assert path == str(tmp_path / "c.xml")
    assert 'name="useTransit" value="true"' in text
    for p in ("net.xml.gz", "sched.xml.gz", "veh.xml.gz"):
        assert p in text


def test_write_router_config_sets_the_qsim_values_the_eqasim_injector_requires(tmp_path):
    # RunBatchPublicTransportRouter builds the full eqasim injector. EqasimAnalysisModule refuses a config whose
    # qsim.personInitializedEvents is not "all", and TravelTimeRecorder sizes its bins by qsim.endTime (unset ->
    # Double.MAX_VALUE -> OutOfMemoryError). Both failed the first real-data run of the gate choice (#465 WP-E smoke).
    import xml.etree.ElementTree as ET

    from braunschweig.popsim.plan_validation import MAX_PLAN_TIME_SECONDS
    connections.write_router_config(str(tmp_path / "c.xml"), network_path="n.xml", schedule_path="s.xml",
                                    vehicles_path="v.xml")
    root = ET.parse(tmp_path / "c.xml").getroot()
    qsim = [m for m in root.iter("module") if m.get("name") == "qsim"]
    assert len(qsim) == 1
    values = {p.get("name"): p.get("value") for p in qsim[0].iter("param")}
    assert values["personInitializedEvents"] == "all"
    hours, minutes, seconds = (int(x) for x in values["endTime"].split(":"))
    assert hours * 3600 + minutes * 60 + seconds == MAX_PLAN_TIME_SECONDS


def test_write_router_config_escapes_xml_attribute_values(tmp_path):
    import xml.etree.ElementTree as ET
    connections.write_router_config(str(tmp_path / "c.xml"), network_path='a&b "net".xml',
                                    schedule_path="s<1>.xml", vehicles_path="v.xml")
    root = ET.parse(tmp_path / "c.xml").getroot()  # raises ParseError if unescaped
    values = {p.get("name"): p.get("value") for p in root.iter("param")}
    assert values["inputNetworkFile"] == 'a&b "net".xml'
    assert values["transitScheduleFile"] == "s<1>.xml"


# --- latest-arrival bound (#573) ---------------------------------------------------------------------------------
# The limit is the day-end limit of the prepare stage: MAX_PLAN_TIME_SECONDS minus the default last-arrival margin.
LATEST_ARRIVAL_S = MAX_PLAN_TIME_SECONDS - day_end_margin.DEFAULT_LAST_ARRIVAL_MARGIN_S


def _bounded_mask(trips, legs, latest_arrival_s=LATEST_ARRIVAL_S, label="test-caller"):
    return connections.connection_mask(trips, legs, inside_stop_ids=INSIDE, walk_max_m=WALK_MAX_M,
                                       latest_arrival_s=latest_arrival_s, label=label)


def _timed(identifier, egress_time_s, final_walk_min=5.0):
    """One pt trip with a single inside-stop leg that ends at egress_time_s, plus its final walk in minutes."""
    return ({"identifier": identifier, "egress_travel_time_min": final_walk_min},
            (identifier, 0, "s_in_a", "s_in_b", egress_time_s))


def _timed_frames(*specs):
    return _trips([t for t, _ in specs]), _legs([leg for _, leg in specs])


def test_latest_arrival_bound_keeps_a_daytime_connection_and_rejects_an_overnight_wait():
    # Primary path first: a normal daytime trip stays a connection with the bound active. The overnight wait
    # (routed arrival after the day-end limit, #573) is rejected although every other test passes.
    trips, legs = _timed_frames(_timed("day", 9 * 3600.0), _timed("overnight", LATEST_ARRIVAL_S + 3000.0))
    assert _bounded_mask(trips, legs).to_dict() == {"day": True, "overnight": False}
    # Without the bound the same overnight trip is a connection (today's behaviour).
    unbounded = connections.connection_mask(trips, legs, inside_stop_ids=INSIDE, walk_max_m=WALK_MAX_M)
    assert unbounded.to_dict() == {"day": True, "overnight": True}


def test_latest_arrival_bound_is_inclusive_and_adds_the_final_walk():
    # arrival = last pt egress_time + final walk; exactly the limit counts, one second later does not.
    at_limit = _timed("at", LATEST_ARRIVAL_S - 300.0, final_walk_min=5.0)
    one_late = _timed("late", LATEST_ARRIVAL_S - 299.0, final_walk_min=5.0)
    trips, legs = _timed_frames(at_limit, one_late)
    assert _bounded_mask(trips, legs).to_dict() == {"at": True, "late": False}


def test_latest_arrival_uses_the_last_pt_leg_of_a_multi_leg_trip():
    trips = _trips([{"identifier": "two", "egress_travel_time_min": 0.0}])
    legs = _legs([("two", 0, "s_in_a", "s_in_b", 3600.0), ("two", 1, "s_in_b", "s_in_a", LATEST_ARRIVAL_S + 1.0)])
    assert _bounded_mask(trips, legs).to_dict() == {"two": False}


def test_latest_arrival_bound_does_not_touch_only_walk_trips():
    trips = _trips([{"identifier": "walk", "is_only_walk": 1, "access_distance_km": 0.4,
                     "egress_distance_km": 0.0, "egress_travel_time_min": 0.0}])
    legs = pd.DataFrame(columns=["identifier", "leg_index", "access_stop_id", "egress_stop_id", "egress_time"])
    assert _bounded_mask(trips, legs, latest_arrival_s=1.0).to_dict() == {"walk": True}


def test_latest_arrival_bound_needs_the_time_columns_and_a_valid_limit():
    trips = _trips([{"identifier": "a"}])
    legs = _legs([("a", 0, "s_in_a", "s_in_b")])
    with pytest.raises(ValueError, match="egress_time"):
        _bounded_mask(trips, legs)
    trips, legs = _timed_frames(_timed("a", 3600.0))
    with pytest.raises(ValueError, match="egress_travel_time_min"):
        _bounded_mask(trips.drop(columns=["egress_travel_time_min"]), legs)
    for bad in (float("nan"), float("inf"), -1.0):
        with pytest.raises(ValueError, match="latest_arrival_s"):
            _bounded_mask(trips, legs, latest_arrival_s=bad)


def test_latest_arrival_bound_non_finite_arrival_is_not_a_connection():
    trips, legs = _timed_frames(_timed("nan_time", float("nan")), _timed("nan_walk", 3600.0, float("nan")),
                                _timed("ok", 3600.0))
    assert _bounded_mask(trips, legs).to_dict() == {"nan_time": False, "nan_walk": False, "ok": True}


def test_latest_arrival_bound_logs_the_rejection_rate_per_caller(caplog):
    trips, legs = _timed_frames(_timed("a", 3600.0), _timed("b", 7200.0), _timed("c", 9000.0),
                                _timed("late", LATEST_ARRIVAL_S + 1.0))
    with caplog.at_level("INFO"):
        _bounded_mask(trips, legs, label="unit pt check")
    bound = [r for r in caplog.records if "latest-arrival bound" in r.getMessage()]
    assert len(bound) == 1 and bound[0].levelname == "INFO"
    message = bound[0].getMessage()
    assert "[unit pt check]" in message and "1/4" in message
    summary = [r for r in caplog.records if "late arrival 1" in r.getMessage()]
    assert summary and "[unit pt check]" in summary[0].getMessage()


def test_latest_arrival_bound_warns_when_it_rejects_most_otherwise_good_trips(caplog):
    trips, legs = _timed_frames(_timed("ok", 3600.0), _timed("l1", LATEST_ARRIVAL_S + 1.0),
                                _timed("l2", LATEST_ARRIVAL_S + 2.0))
    with caplog.at_level("INFO"):
        _bounded_mask(trips, legs)
    bound = [r for r in caplog.records if "latest-arrival bound" in r.getMessage()]
    assert len(bound) == 1 and bound[0].levelname == "WARNING" and "2/3" in bound[0].getMessage()


class _FakeContext:
    """synpp in its two phases: ``connections.configure`` declares the keys with their defaults, and the
    execute-time read takes the key alone, as ``ExecuteContext.config`` does (a default there is a TypeError)."""

    def __init__(self, **values):
        declared = {}

        class _Configure:
            def config(self, key, default=None, **_options):
                declared[key] = default

        connections.configure(_Configure())
        self.values = {**declared, **values}

    def config(self, key):
        return self.values[key]


def test_latest_arrival_s_follows_the_day_end_margin_and_the_switch():
    switch = connections.KEY_LATEST_ARRIVAL_CHECK
    assert connections.latest_arrival_s(_FakeContext()) == LATEST_ARRIVAL_S  # defaults: check on, margin on
    custom = _FakeContext(**{day_end_margin.KEY_LAST_ARRIVAL_MARGIN_S: 1800.0})
    assert connections.latest_arrival_s(custom) == MAX_PLAN_TIME_SECONDS - 1800.0
    margin_off = _FakeContext(**{day_end_margin.KEY_ENABLED: False})
    assert connections.latest_arrival_s(margin_off) == MAX_PLAN_TIME_SECONDS
    assert connections.latest_arrival_s(_FakeContext(**{switch: False})) is None
