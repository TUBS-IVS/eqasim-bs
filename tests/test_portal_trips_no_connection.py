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


@pytest.mark.parametrize("override", [dict(shift_step_s=0.0), dict(shift_step_s=-1.0), dict(shift_window_s=-1.0),
                                      dict(min_stay_s=-1.0), dict(bound_s=float("inf")),
                                      dict(bound_s=float("nan"))])
def test_invalid_shift_parameters_raise(override):
    stays = pd.DataFrame([_stay(1, 1, 2)])
    with pytest.raises(ValueError):
        nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(False, False), _gates([None]),
                                 _no_probe, fallback_gates=_fallback(1), **{**KW, **override})


def test_return_bound_uses_next_existing_trip_index():
    trips = _chain()
    trips.loc[3, "departure_time"] = 60500 + 1000
    trips["trip_index"] = [0, 1, 2, 10, 11]  # non-consecutive: the next trip after 2 is 10
    seen = []

    def probe(positions, out_dep, ret_dep):
        seen.append(ret_dep.tolist())
        return _gates([None] * len(positions))

    stays = pd.DataFrame([_stay(1, 1, 2)])
    nc.resolve_no_connection(stays, trips, _modes(1), _avail_int(False, False), _gates([None]), probe,
                             fallback_gates=_fallback(1), **KW)
    assert not any(any(np.isclose(r, 60000 + 1800)) for r in seen)


def test_both_variant_found_only_when_neither_leg_alone_works():
    def probe(positions, out_dep, ret_dep):
        found = np.isclose(out_dep, 40000 + 1800) & np.isclose(ret_dep, 60000 + 1800)
        return _gates(["s" if f else None for f in found])

    stays = pd.DataFrame([_stay(1, 1, 2)])
    m, t, g = nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(False, False), _gates([None]),
                                       probe, fallback_gates=_fallback(1), **KW)
    assert m["no_connection_action"].tolist() == [nc.ACTION_DEPARTURE_SHIFTED]
    assert t.loc[1, "departure_time"] == 41800 and t.loc[2, "departure_time"] == 61800


def test_mixed_outcome_keeps_pt_gates_aligned():
    def trips_for(person, mode="pt"):
        return _trips(person, [("home", "work", mode, 3600, 4000), ("work", "out", mode, 40000, 40500),
                               ("out", "home", mode, 60000, 60500)])

    trips = pd.concat([trips_for(p) for p in (1, 2, 3, 4)], ignore_index=True)
    stays = pd.DataFrame([_stay(p, 1, 2) for p in (1, 2, 3, 4)])
    availability = pd.DataFrame({"can_car": [False, True, False, False], "can_car_passenger": False},
                                index=pd.Index([1, 2, 3, 4], name="person_id"))
    fallback = pd.DataFrame({"gate_id": ["f1", "f2", "f3", "f4"], "x": [10.0, 20.0, 30.0, 40.0], "y": 0.0})

    def probe(positions, out_dep, ret_dep):
        found = np.isclose(out_dep, 40000 + 1800) & (positions == 2)
        return _gates(["shift" if f else None for f in found])

    m, t, g = nc.resolve_no_connection(stays, trips, _modes(4), availability, _gates(["found", None, None, None]),
                                       probe, fallback_gates=fallback, **KW)
    assert m["no_connection_action"].tolist() == [nc.ACTION_GATE_FOUND, nc.ACTION_TOUR_TO_CAR,
                                                  nc.ACTION_DEPARTURE_SHIFTED, nc.ACTION_KEPT]
    assert g["gate_id"].tolist() == ["found", "shift", "f4"]
    assert g["x"].iloc[2] == 40.0


def _two_stay_person_trips():
    # Two pt stays of person 1, each its own home-based tour: A = trips 0/1, B = trips 2/3.
    return _trips(1, [("home", "out", "pt", 10000, 10500), ("out", "home", "pt", 12000, 12600),
                      ("home", "out", "pt", 13000, 13500), ("out", "home", "pt", 20000, 20500)])


def test_two_stays_of_one_person_never_overlap_when_the_second_outbound_is_shifted():
    # Stay A shifts its return by 300 s (arrival 12900), stay B shifts its outbound by 300 s (departure 13300).
    # The return bound of A is the CURRENT departure of B's outbound, so the trips stay in order.
    def probe(positions, out_dep, ret_dep):
        found = np.where(positions == 0, np.isclose(ret_dep, 12000 + 300) & np.isclose(out_dep, 10000),
                         np.isclose(out_dep, 13000 + 300) & np.isclose(ret_dep, 20000))
        return _gates(["s" if f else None for f in found])

    stays = pd.DataFrame([_stay(1, 0, 1), _stay(1, 2, 3)])
    kw = {**KW, "shift_step_s": 300.0, "shift_window_s": 1200.0}
    m, t, g = nc.resolve_no_connection(stays, _two_stay_person_trips(), _modes(2), _avail_int(False, False),
                                       _gates([None, None]), probe, fallback_gates=_fallback(2), **kw)
    assert m["no_connection_action"].tolist() == [nc.ACTION_DEPARTURE_SHIFTED] * 2
    assert t["departure_time"].tolist() == [10000, 12300, 13300, 20000]
    assert (t["arrival_time"].to_numpy()[:-1] <= t["departure_time"].to_numpy()[1:]).all()


def test_return_shift_never_overruns_the_unshifted_next_outbound():
    # A's return could only be found at +600 s (arrival 13200 > B's departure 13000): kept, not overlapping.
    def probe(positions, out_dep, ret_dep):
        found = (positions == 0) & np.isclose(ret_dep, 12000 + 600) & np.isclose(out_dep, 10000)
        return _gates(["s" if f else None for f in found])

    stays = pd.DataFrame([_stay(1, 0, 1), _stay(1, 2, 3)])
    kw = {**KW, "shift_step_s": 300.0, "shift_window_s": 1200.0}
    m, t, _ = nc.resolve_no_connection(stays, _two_stay_person_trips(), _modes(2), _avail_int(False, False),
                                       _gates([None, None]), probe, fallback_gates=_fallback(2), **kw)
    assert m["no_connection_action"].tolist() == [nc.ACTION_KEPT, nc.ACTION_KEPT]
    assert t["departure_time"].tolist() == [10000, 12000, 13000, 20000]


def test_persons_without_a_pt_stay_are_returned_unchanged():
    other = _trips(9, [("home", "work", "car", 3600, 4000), ("work", "home", "car", 40000, 40500)])
    trips = pd.concat([_chain(), other], ignore_index=True)
    stays = pd.DataFrame([_stay(1, 1, 2)])
    m, t, g = nc.resolve_no_connection(stays, trips, _modes(1), _avail_int(True, True), _gates([None]),
                                       _no_probe, fallback_gates=_fallback(1), **KW)
    assert t.iloc[5:]["mode"].tolist() == ["car", "car"]
    assert t.iloc[5:]["departure_time"].tolist() == [3600.0, 40000.0]
    assert m["no_connection_action"].tolist() == [nc.ACTION_TOUR_TO_CAR]


# eqasim-bs#465 (WP-E smoke): 14 of 123 pt stays were kept without a connection, mostly late returns (21-30 h) that a
# LATER shift cannot serve, and the router turned them into walks of up to 125 km. Two additions, both off in the
# function's defaults (the production config switches them on): an EARLIER return, and a lift as the last resort.

def _probe_only(ret_dep_s):
    def probe(positions, out_dep, ret_dep):
        found = np.isclose(ret_dep, ret_dep_s) & np.isclose(out_dep, 40000)
        return _gates(["s" if f else None for f in found])
    return probe


def test_an_earlier_return_is_tried_after_the_later_shifts_and_keeps_the_minimum_stay():
    stays = pd.DataFrame([_stay(1, 1, 2)])
    m, t, g = nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(False, False), _gates([None]),
                                       _probe_only(60000 - 3600), fallback_gates=_fallback(1), **KW,
                                       return_earlier=True)
    assert m["no_connection_action"].tolist() == [nc.ACTION_DEPARTURE_SHIFTED]
    assert (t.loc[2, "departure_time"], t.loc[2, "arrival_time"]) == (56400.0, 56900.0)
    assert t.loc[1, "departure_time"] == 40000 and g["gate_id"].tolist() == ["s"]
    # off (the function default): the same stay stays kept
    m, *_ = nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(False, False), _gates([None]),
                                     _probe_only(60000 - 3600), fallback_gates=_fallback(1), **KW)
    assert m["no_connection_action"].tolist() == [nc.ACTION_KEPT]
    # never closer than min_stay_s to the outbound arrival (40500): 60000 - 7200 = 52800 is allowed, so only a
    # return that would leave less than 1800 s of stay is skipped
    trips = _chain()
    trips.loc[2, ["departure_time", "arrival_time"]] = [43000.0, 43500.0]
    seen = []

    def probe(positions, out_dep, ret_dep):
        seen.extend(ret_dep.tolist())
        return _gates([None] * len(positions))
    nc.resolve_no_connection(stays, trips, _modes(1), _avail_int(False, False), _gates([None]), probe,
                             fallback_gates=_fallback(1), **KW, return_earlier=True)
    assert min(seen) >= 40500 + 1800


def test_the_last_resort_lift_replaces_a_kept_stay_by_car_passenger_without_availability():
    stays = pd.DataFrame([_stay(1, 1, 2)])

    def probe(positions, out_dep, ret_dep):
        return _gates([None] * len(positions))

    m, t, g = nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(False, False), _gates([None]),
                                       probe, fallback_gates=_fallback(1), **KW, return_earlier=True,
                                       last_resort=nc.LAST_RESORT_LIFT)
    assert m["no_connection_action"].tolist() == [nc.ACTION_TOUR_TO_CAR_PASSENGER_LIFT]
    assert t["mode"].tolist()[:3] == ["car_passenger"] * 3 and t["mode"].tolist()[3:] == ["walk"] * 2
    row = m.iloc[0]
    assert (row["mode"], row["substituted_from"]) == ("car_passenger", "pt")
    assert len(g) == 0                                   # no longer a pt stay


def test_an_unknown_last_resort_is_refused():
    stays = pd.DataFrame([_stay(1, 1, 2)])
    with pytest.raises(ValueError, match="last_resort"):
        nc.resolve_no_connection(stays, _chain(), _modes(1), _avail_int(False, False), _gates([None]), _no_probe,
                                 fallback_gates=_fallback(1), **KW, last_resort="walk")
