"""Tests for the pt no-connection fallback chain (eqasim-bs#465, C6)."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.portal_trips.gates import PT_GATE_COLUMNS
from braunschweig.synthesis.portal_trips.modes import MODE_COLUMNS
from braunschweig.synthesis.portal_trips import no_connection as nc

BOUND = 30 * 3600.0
KW = dict(shift_window_s=7200.0, shift_step_s=1800.0, min_stay_s=1800.0, bound_s=BOUND)


def _trips(person, rows):
    """rows: (preceding, following, mode, departure, arrival) per trip, indices from 0."""
    return pd.DataFrame([dict(person_id=person, trip_index=i, preceding_purpose=a, following_purpose=b,
                              mode=m, departure_time=float(d), arrival_time=float(r))
                         for i, (a, b, m, d, r) in enumerate(rows)])


def _stay(person, out, ret=None):
    return dict(person_id=person, outbound_trip_index=out, return_trip_index=ret, n_removed_legs=0)


def _modes(n, mode="pt", ret=True):
    frame = pd.DataFrame({"mode": mode, "outbound_mode": mode, "return_mode": mode if ret else None,
                          "substituted_from": None, "substitution_reason": None,
                          "return_mode_differs": False}, index=range(n))
    return frame[MODE_COLUMNS]


def _avail(**per_person):
    return pd.DataFrame({p: {"can_car": c, "can_car_passenger": cp} for p, (c, cp) in per_person.items()}
                        ).T.rename_axis("person_id")


def _gates(ids):
    return pd.DataFrame({"gate_id": ids, "x": [1.0 if i else np.nan for i in ids],
                         "y": [2.0 if i else np.nan for i in ids],
                         "reason": ["gate_found" if i else "no_connection" for i in ids],
                         "n_in_direction": 1, "n_served": 1, "n_connected": [1 if i else 0 for i in ids]},
                        columns=PT_GATE_COLUMNS)


def _fallback(n):
    return pd.DataFrame({"gate_id": [f"fb{i}" for i in range(n)], "x": 9.0, "y": 8.0})


def _no_probe(positions, out_dep, ret_dep):  # pragma: no cover - must not be called
    raise AssertionError("probe must not be called")


def _chain():
    # home -> work -> out -> home -> shop -> home
    return _trips(1, [("home", "work", "pt", 3600, 4000), ("work", "out", "pt", 40000, 40500),
                      ("out", "home", "pt", 60000, 60500), ("home", "shop", "walk", 70000, 70200),
                      ("shop", "home", "walk", 71000, 71200)])


def test_tour_trip_indices():
    assert list(nc.tour_trip_indices(_chain(), 1, 1)) == [0, 1, 2]
    assert list(nc.tour_trip_indices(_chain(), 1, 3)) == [3, 4]


def test_gate_found_is_untouched():
    trips = _chain()
    stays = pd.DataFrame([_stay(1, 1, 2)])
    m, t, g = nc.resolve_no_connection(stays, trips, _modes(1), _avail(**{"1": (True, True)}).rename(index=int),
                                       _gates(["g"]), _no_probe, fallback_gates=_fallback(1), **KW)
    assert m["no_connection_action"].tolist() == [nc.ACTION_GATE_FOUND]
    assert t.equals(trips.astype({"departure_time": float, "arrival_time": float}))
    assert g["gate_id"].tolist() == ["g"]


def _avail_int(c, cp):
    return pd.DataFrame({"can_car": [c], "can_car_passenger": [cp]}, index=pd.Index([1], name="person_id"))


def test_tour_to_car_switches_only_its_tour():
    trips = _chain()
    trips.loc[3:4, "mode"] = "pt"  # the second tour is pt too but has no stay
    stays = pd.DataFrame([_stay(1, 1, 2)])
    m, t, g = nc.resolve_no_connection(stays, trips, _modes(1), _avail_int(True, True), _gates([None]),
                                       _no_probe, fallback_gates=_fallback(1), **KW)
    assert t["mode"].tolist() == ["car"] * 3 + ["pt"] * 2
    assert m["no_connection_action"].tolist() == [nc.ACTION_TOUR_TO_CAR]
    row = m.iloc[0]
    assert (row["mode"], row["outbound_mode"], row["return_mode"]) == ("car", "car", "car")
    assert (row["substituted_from"], row["substitution_reason"]) == ("pt", nc.REASON_NO_PT_CONNECTION)
    assert len(g) == 0


def test_tour_to_car_passenger_without_car():
    stays = pd.DataFrame([_stay(1, 1, 2)])
    m, t, g = nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(False, True), _gates([None]),
                                       _no_probe, fallback_gates=_fallback(1), **KW)
    assert m["no_connection_action"].tolist() == [nc.ACTION_TOUR_TO_CAR_PASSENGER]
    assert t["mode"].tolist()[:3] == ["car_passenger"] * 3


def test_outbound_shift_with_probe_once_per_step():
    calls = []

    def probe(positions, out_dep, ret_dep):
        calls.append((positions.tolist(), out_dep.tolist()))
        found = np.isclose(out_dep, 40000 + 3600) & np.isclose(ret_dep, 60000)
        frame = _gates(["s" if f else None for f in found])
        return frame

    stays = pd.DataFrame([_stay(1, 1, 2)])
    m, t, g = nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(False, False), _gates([None]),
                                       probe, fallback_gates=_fallback(1), **KW)
    assert m["no_connection_action"].tolist() == [nc.ACTION_DEPARTURE_SHIFTED]
    assert t.loc[1, "departure_time"] == 43600 and t.loc[1, "arrival_time"] == 44100
    assert t.loc[2, "departure_time"] == 60000 and t.loc[2, "arrival_time"] == 60500
    assert g["gate_id"].tolist() == ["s"]
    assert len(calls) == 2 and all(len(c[0]) == 3 for c in calls)  # steps 1800, 3600 (found); one call per step


def test_shift_leaving_less_than_min_stay_is_kept():
    trips = _chain()
    trips.loc[2, "departure_time"] = 40500 + 1800 + 100  # only 100 s of slack beyond min_stay
    stays = pd.DataFrame([_stay(1, 1, 2)])

    def probe(positions, out_dep, ret_dep):
        return _gates([None] * len(positions))

    m, t, g = nc.resolve_no_connection(stays, trips, _modes(1), _avail_int(False, False), _gates([None]),
                                       probe, fallback_gates=_fallback(1), shift_window_s=7200.0,
                                       shift_step_s=3600.0, min_stay_s=1800.0, bound_s=BOUND)
    assert m["no_connection_action"].tolist() == [nc.ACTION_KEPT]
    assert t.loc[1, "departure_time"] == 40000
    assert g["gate_id"].tolist() == ["fb0"] and g["x"].tolist() == [9.0]


def test_return_shift_bounded_by_next_trip_and_bound():
    trips = _chain()
    trips.loc[3, "departure_time"] = 60500 + 1000  # next trip leaves 1000 s after the return arrival
    stays = pd.DataFrame([_stay(1, 1, 2)])
    seen = []

    def probe(positions, out_dep, ret_dep):
        seen.append(ret_dep.tolist())
        return _gates([None] * len(positions))

    m, *_ = nc.resolve_no_connection(stays, trips, _modes(1), _avail_int(False, False), _gates([None]), probe,
                                     fallback_gates=_fallback(1), **KW)
    assert m["no_connection_action"].tolist() == [nc.ACTION_KEPT]
    assert all(60000 in r or 60000 + 1800 not in r for r in seen)
    assert not any(any(np.isclose(r, 60000 + 1800)) for r in seen)  # the return shift of 1800 s overruns
    bound_trips = _chain()
    bound_trips.loc[2, ["departure_time", "arrival_time"]] = [BOUND - 1000, BOUND - 500]
    bound_trips = bound_trips.iloc[:3]
    seen.clear()
    nc.resolve_no_connection(stays, bound_trips, _modes(1), _avail_int(False, False), _gates([None]), probe,
                             fallback_gates=_fallback(1), **KW)
    assert not any(any(np.isclose(r, BOUND - 1000 + 1800)) for r in seen)


def test_return_shift_applied():
    def probe(positions, out_dep, ret_dep):
        found = np.isclose(ret_dep, 60000 + 1800) & np.isclose(out_dep, 40000)
        return _gates(["s" if f else None for f in found])

    stays = pd.DataFrame([_stay(1, 1, 2)])
    m, t, g = nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(False, False), _gates([None]),
                                       probe, fallback_gates=_fallback(1), **KW)
    assert m["no_connection_action"].tolist() == [nc.ACTION_DEPARTURE_SHIFTED]
    assert t.loc[2, "departure_time"] == 61800 and t.loc[1, "departure_time"] == 40000


def test_two_stays_in_one_tour_switch_together():
    trips = _trips(1, [("home", "work", "pt", 3600, 4000), ("work", "out", "pt", 10000, 10500),
                       ("out", "work", "pt", 20000, 20500), ("work", "out", "pt", 30000, 30500),
                       ("out", "home", "pt", 40000, 40500)])
    stays = pd.DataFrame([_stay(1, 1, 2), _stay(1, 3, 4)])
    m, t, g = nc.resolve_no_connection(stays, trips, _modes(2), _avail_int(True, False), _gates(["g", None]),
                                       _no_probe, fallback_gates=_fallback(2), **KW)
    assert m["mode"].tolist() == ["car", "car"]
    assert m["no_connection_action"].tolist() == [nc.ACTION_TOUR_TO_CAR] * 2
    assert t["mode"].tolist() == ["car"] * 5
    assert len(g) == 0


def test_stay_without_return_checks_only_outbound():
    seen = []

    def probe(positions, out_dep, ret_dep):
        seen.append((out_dep.tolist(), ret_dep.tolist()))
        found = np.isclose(out_dep, 40000 + 1800)
        return _gates(["s" if f else None for f in found])

    trips = _chain().iloc[:2]
    stays = pd.DataFrame([_stay(1, 1, None)])
    stays["return_trip_index"] = stays["return_trip_index"].astype(float)
    m, t, g = nc.resolve_no_connection(stays, trips, _modes(1, ret=False), _avail_int(False, False),
                                       _gates([None]), probe, fallback_gates=_fallback(1), **KW)
    assert m["no_connection_action"].tolist() == [nc.ACTION_DEPARTURE_SHIFTED]
    assert t.loc[1, "departure_time"] == 41800
    assert all(np.isnan(r).all() for _, r in seen)
    assert len(seen[0][0]) == 1  # no return variants


def test_misaligned_inputs_raise():
    stays = pd.DataFrame([_stay(1, 1, 2)])
    with pytest.raises(ValueError):
        nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(True, True), _gates([None, None]),
                                 _no_probe, fallback_gates=_fallback(1), **KW)
    with pytest.raises(ValueError):
        nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(True, True), _gates([None]),
                                 _no_probe, fallback_gates=_fallback(2), **KW)
    other = _avail_int(True, True).rename(index={1: 2})
    with pytest.raises(ValueError):
        nc.resolve_no_connection(stays, _chain(), _modes(1), other, _gates([None]), _no_probe,
                                 fallback_gates=_fallback(1), **KW)
