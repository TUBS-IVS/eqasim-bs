"""Batch pt router wrapper and connection rule (#465, WP-C, C4)."""
import pandas as pd
import pytest

from braunschweig.data.transit import connections

INSIDE = {"s_in_a", "s_in_b"}
WALK_MAX_M = 5000.0


def _trips(rows):
    base = dict(is_only_walk=0, access_distance_km=0.3, egress_distance_km=0.3, transfer_distance_km=0.0)
    return pd.DataFrame([{**base, **r} for r in rows])


def _legs(rows):
    return pd.DataFrame(rows, columns=["identifier", "leg_index", "access_stop_id", "egress_stop_id"])


def _mask(trips, legs):
    return connections.connection_mask(trips, legs, inside_stop_ids=INSIDE, walk_max_m=WALK_MAX_M)


def test_connection_rule_cases():
    trips = _trips([
        {"identifier": "ok"},
        {"identifier": "egress_outside"},
        {"identifier": "short_walk", "is_only_walk": 1, "access_distance_km": 0.2, "egress_distance_km": 0.2},
        {"identifier": "long_walk", "is_only_walk": 1, "access_distance_km": 1.5, "egress_distance_km": 1.5},
        {"identifier": "far_access", "access_distance_km": 6.0},
        {"identifier": "far_transfer", "transfer_distance_km": 5.5},
    ])
    legs = _legs([
        ("ok", 0, "s_in_a", "s_in_b"),
        ("egress_outside", 0, "s_in_a", "s_out"),
        ("far_access", 0, "s_in_a", "s_in_b"),
        ("far_transfer", 0, "s_in_a", "s_in_b"),
        ("ok", 1, "", ""),  # defensive: blank stop ids are ignored
    ])
    mask = _mask(trips, legs)
    assert mask.to_dict() == {"ok": True, "egress_outside": False, "short_walk": True,
                              "long_walk": False, "far_access": False, "far_transfer": False}


def test_plausible_walk_boundary_is_inclusive():
    trips = _trips([
        {"identifier": "at", "is_only_walk": 1, "access_distance_km": 0.3, "egress_distance_km": 0.3},
        {"identifier": "above", "is_only_walk": 1, "access_distance_km": 0.31, "egress_distance_km": 0.3},
    ])
    mask = _mask(trips, _legs([]))
    assert mask.to_dict() == {"at": True, "above": False}


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
    run = _fake_run("identifier,is_only_walk\nt1,0\n",
                    "identifier,leg_index,access_stop_id,egress_stop_id\nt1,0,0012,s_in_b\nt1,1,,\n")
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


def test_trip_without_leg_rows_and_not_only_walk_has_no_outside_stop():
    # The router writes legs rows for pt legs only; a trip absent from legs has no stop to reject.
    trips = _trips([{"identifier": "no_legs", "is_only_walk": 0}])
    assert _mask(trips, _legs([])).to_dict() == {"no_legs": True}
