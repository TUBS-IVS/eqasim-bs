"""Batch pt router wrapper and connection rule (#465, WP-C, C4)."""
import pandas as pd
import pytest

from braunschweig.data.transit import connections

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
    return pd.DataFrame(rows, columns=["identifier", "leg_index", "access_stop_id", "egress_stop_id"])


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


def test_write_router_config_escapes_xml_attribute_values(tmp_path):
    import xml.etree.ElementTree as ET
    connections.write_router_config(str(tmp_path / "c.xml"), network_path='a&b "net".xml',
                                    schedule_path="s<1>.xml", vehicles_path="v.xml")
    root = ET.parse(tmp_path / "c.xml").getroot()  # raises ParseError if unescaped
    values = {p.get("name"): p.get("value") for p in root.iter("param")}
    assert values["inputNetworkFile"] == 'a&b "net".xml'
    assert values["transitScheduleFile"] == "s<1>.xml"
