"""Timetable check of the in-commuters' pt days (eqasim-bs#488).

In run i465-wpe-smoke-1pct-2026-10-23, 22 of about 91 pt in-commuters had a trip realised as a walk over 5 km
(median 52 km, mostly the return from work at 16-23 h): their times come from survey donors and were never checked
against the timetable. ``braunschweig.synthesis.incommuter_pt_connections`` checks both trips of a pt in-commuter's
day with the batch pt router; a day without a connection moves as a whole (earlier first, then later, in steps up to a
window) and, when no offset gives both trips a connection, the in-commuter drives instead (board at the road gate).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis import incommuter_pt_connections as P
from braunschweig.synthesis.incommuters import build_incommuter_frames
from tests.test_incommuter_assembly import _inputs

H = 3600.0


def _service_probe(first_s, last_s, seen=None):
    """A probe whose timetable serves every departure in [first_s, last_s]."""
    def probe(tasks):
        if seen is not None:
            seen.append(tasks.copy())
        served = tasks["departure_time"].between(first_s, last_s)
        return pd.Series(served.to_numpy(), index=tasks["identifier"].astype(str).to_numpy())
    return probe


def _days(departures_h):
    """Arrays for agents with (depart_home, arrive_work, depart_work, arrive_home) in hours."""
    times = np.array(departures_h, dtype=float) * H
    return times[:, 0], times[:, 1], times[:, 2], times[:, 3]


def _resolve(departures_h, probe, *, is_pt=None, car_allowed=True, bound_s=29 * H):
    n = len(departures_h)
    dh, aw, dw, ah = _days(departures_h)
    return P.resolve_pt_days(
        person_ids=np.arange(100, 100 + n), is_pt=np.ones(n, bool) if is_pt is None else np.asarray(is_pt),
        home_xy=np.zeros((n, 2)), work_xy=np.ones((n, 2)) * 1000.0,
        depart_home_s=dh, arrive_work_s=aw, depart_work_s=dw, arrive_home_s=ah, probe=probe,
        shift_window_s=2 * H, shift_step_s=0.5 * H, bound_s=bound_s, car_allowed=car_allowed)


def test_the_offsets_alternate_earlier_first_up_to_the_window():
    assert P.shift_offsets_s(2 * H, 0.5 * H) == [-1800.0, 1800.0, -3600.0, 3600.0, -5400.0, 5400.0, -7200.0, 7200.0]
    assert P.shift_offsets_s(0.0, 0.5 * H) == []
    with pytest.raises(ValueError, match="shift"):
        P.shift_offsets_s(2 * H, 0.0)


def test_each_pt_day_is_checked_shifted_or_switched_to_car_and_counted():
    days = [(7.0, 8.0, 16.0, 17.0),     # served as planned
            (7.0, 8.0, 21.5, 22.5),     # return after the last departure (21:00): one hour earlier works
            (7.0, 8.0, 23.9, 24.9),     # nothing within two hours: drives
            (7.0, 8.0, 16.0, 17.0)]     # not pt: never probed
    seen = []
    result = _resolve(days, _service_probe(5 * H, 21 * H, seen), is_pt=[True, True, True, False])

    assert list(result.actions) == [P.ACTION_CONNECTION_FOUND, P.ACTION_DAY_SHIFTED, P.ACTION_TO_CAR, None]
    # -1800 s leaves the return at 21:00, which the timetable still serves: the smallest earlier shift wins
    np.testing.assert_array_equal(result.offsets_s, [0.0, -1800.0, 0.0, 0.0])
    assert result.report["n_pt_days"] == 3 and result.report["actions"][P.ACTION_TO_CAR] == 1
    probed = pd.concat(seen)
    assert not probed["identifier"].str.startswith("103:").any()      # the car agent is never routed
    assert set(probed.loc[probed["identifier"] == "100:0:out", "departure_time"]) == {7 * H}


def test_a_day_is_never_shifted_before_midnight_or_after_the_bound():
    # The only served offset would be -8 h (beyond the window) or end after the bound: no shift, car.
    days = [(1.0, 2.0, 27.0, 28.5)]
    result = _resolve(days, _service_probe(29 * H, 30 * H), bound_s=29 * H)
    assert list(result.actions) == [P.ACTION_TO_CAR]
    assert result.report["actions"][P.ACTION_DAY_SHIFTED] == 0


def test_without_car_the_day_is_kept_and_counted_as_no_connection(caplog):
    result = _resolve([(7.0, 8.0, 23.9, 24.9)], _service_probe(5 * H, 21 * H), car_allowed=False)
    assert list(result.actions) == [P.ACTION_KEPT]
    with caplog.at_level("INFO"):
        P.log_report(result.report, warn_share=0.05)
    assert any(r.levelname == "WARNING" and P.ACTION_KEPT in r.getMessage() for r in caplog.records)


def _frames(probe, *, mode_reference, return_h=(17.0, 18.0)):
    gates, assignment, flows, zgb_work, hp, ht = _inputs()
    ht.loc[ht["preceding_purpose"] == "work", ["departure_time", "arrival_time"]] = [return_h[0] * H, return_h[1] * H]
    pt_stops = pd.DataFrame({"source_ars5": ["03241"], "stop_id": ["stopA"], "x": [604000.0], "y": [5841000.0],
                             "reach": ["direct"], "ewz": [50000.0]})
    pt_check = None if probe is None else P.PtCheck(probe=probe, shift_window_s=2 * H, shift_step_s=0.5 * H,
                                                    bound_s=29 * H)
    return build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.005, gates=gates, assignment=assignment,
        zgb_work=zgb_work, mode_reference=mode_reference, band_edges=(10,), hts_persons=hp, hts_trips=ht,
        person_col="person_id", n_residents=100, n_resident_households=40, rng=np.random.default_rng(1),
        gate_speed_kmh=30.0, pt_entry_stops=pt_stops, pt_check=pt_check,
        time_bound_s=29 * H)  # the stage passes the same number as PtCheck.bound_s


def test_the_frames_carry_the_shifted_day_of_a_pt_in_commuter():
    # The donor works 8:00-17:00 and is home at 18:00; the timetable ends at 16:00: one hour earlier.
    frames = _frames(_service_probe(5 * H, 16 * H), mode_reference={">=10": {"pt": 1.0}})
    trips = frames["trips"].sort_values(["person_id", "trip_index"])
    assert (trips["mode"] == "pt").all()
    assert set(trips.loc[trips["trip_index"] == 1, "departure_time"]) == {16 * H}
    work = frames["activities"][frames["activities"]["purpose"] == "work"]
    assert set(work["end_time"] - work["start_time"]) == {9 * H}           # duration kept
    assert frames["pt_connection"]["actions"][P.ACTION_DAY_SHIFTED] == len(frames["persons"])


def test_a_pt_in_commuter_without_any_connection_drives_from_the_road_gate():
    frames = _frames(_service_probe(0.0, -1.0), mode_reference={">=10": {"pt": 1.0}})
    assert (frames["trips"]["mode"] == "car").all()
    assert (frames["persons"]["car_availability"] == "all").all()
    home = frames["locations"][frames["locations"]["activity_index"] == 0]
    assert np.allclose(home.geometry.x, 605000.0) and np.allclose(home.geometry.y, 5840000.0)   # the road gate
    assert (frames["validation"]["entry_kind"] == "road_gate").all()
    assert set(frames["vehicles"]["mode"]) == {"car", "car_passenger"}
    assert frames["pt_connection"]["actions"][P.ACTION_TO_CAR] == len(frames["persons"])


def test_a_late_day_moves_before_the_bound_before_its_trips_are_checked():
    # The donor returns 28.5 h -> 29.5 h; with the bound at 29 h the whole day moves 0.5 h earlier first (the rule
    # of the population writer's day-end margin), and the timetable is asked at the moved times.
    seen = []
    frames = _frames(_service_probe(0.0, 30 * H, seen), mode_reference={">=10": {"pt": 1.0}}, return_h=(28.5, 29.5))
    probed = pd.concat(seen)
    assert set(probed.loc[probed["identifier"].str.endswith(":ret"), "departure_time"]) == {28.0 * H}
    assert frames["trips"]["arrival_time"].max() == 29.0 * H
    assert frames["pt_connection"]["n_days_moved_before_bound"] == len(frames["persons"])


def test_without_a_check_the_frames_are_unchanged():
    base = _frames(None, mode_reference={">=10": {"pt": 1.0}})
    assert (base["trips"]["mode"] == "pt").all() and "pt_connection" not in base


# --- Station re-draw (#467, D2): a pt day that no shift serves tries the other stations of the source Kreis ---------

STATION_A = (604000.0, 5841000.0)      # the drawn station of the frame tests
STATION_B = (603000.0, 5842000.0)      # a second station of the same Kreis


def _station_probe(served_xy, seen=None):
    """A probe whose timetable serves exactly the stations at ``served_xy`` (out: origin, ret: destination)."""
    served_xy = {tuple(map(float, xy)) for xy in served_xy}

    def probe(tasks):
        if seen is not None:
            seen.append(tasks.copy())
        is_out = tasks["identifier"].astype(str).str.endswith(":out").to_numpy()
        x = np.where(is_out, tasks["origin_x"], tasks["destination_x"])
        y = np.where(is_out, tasks["origin_y"], tasks["destination_y"])
        ok = [(float(a), float(b)) in served_xy for a, b in zip(x, y)]
        return pd.Series(ok, index=tasks["identifier"].astype(str).to_numpy())
    return probe


def test_an_unserved_day_takes_the_first_connected_station_of_its_kreis_in_one_probe_call():
    n = 4
    dh, aw, dw, ah = _days([(7.0, 8.0, 16.0, 17.0)] * n)
    home_xy = np.array([[10.0, 10.0], [20.0, 20.0], [30.0, 30.0], [40.0, 40.0]])
    cand = {1: [P.StationCandidate("s1", 21.0, 21.0, 6.5 * H), P.StationCandidate("s2", 22.0, 22.0, 6.4 * H)],
            2: [P.StationCandidate("s3", 31.0, 31.0, 6.5 * H)]}
    # agent 0: served as drawn; agent 1: s2 is served; agent 2: no candidate served; agent 3: no candidates (in-ring)
    seen = []
    result = P.resolve_pt_days(
        person_ids=np.arange(100, 100 + n), is_pt=np.ones(n, bool), home_xy=home_xy, work_xy=np.ones((n, 2)) * 1e3,
        depart_home_s=dh, arrive_work_s=aw, depart_work_s=dw, arrive_home_s=ah,
        probe=_station_probe([(10.0, 10.0), (22.0, 22.0)], seen),
        shift_window_s=2 * H, shift_step_s=0.5 * H, bound_s=29 * H, station_candidates=cand)

    assert list(result.actions) == [P.ACTION_CONNECTION_FOUND, P.ACTION_STATION_REDRAWN, P.ACTION_TO_CAR,
                                    P.ACTION_TO_CAR]
    assert result.stations[1] == cand[1][1] and result.stations[0] is None and result.stations[2] is None
    np.testing.assert_array_equal(result.offsets_s, np.zeros(n))
    assert result.report["actions"][P.ACTION_STATION_REDRAWN] == 1
    assert result.report["n_station_redraw_days"] == 2
    station_calls = [t for t in seen if t["identifier"].str.count(":").eq(3).any()]
    assert len(station_calls) == 1                                    # all candidates of all agents in ONE call
    tasks = station_calls[0].set_index("identifier")
    assert len(tasks) == 2 * 3                                         # both trips of each of the 3 candidates
    assert tasks.loc["101:1:s2:out", "departure_time"] == 6.4 * H      # the candidate's own home departure
    assert tasks.loc["101:1:s2:ret", "departure_time"] == 16.0 * H     # the unshifted work departure
    assert tasks.loc["101:1:s2:out", ["origin_x", "origin_y"]].to_list() == [22.0, 22.0]
    assert not station_calls[0]["identifier"].str.startswith(("100:", "103:")).any()


def test_a_day_the_shift_serves_never_reaches_the_station_step():
    dh, aw, dw, ah = _days([(7.0, 8.0, 21.5, 22.5)])
    seen = []
    result = P.resolve_pt_days(
        person_ids=np.array([100]), is_pt=np.ones(1, bool), home_xy=np.zeros((1, 2)), work_xy=np.ones((1, 2)),
        depart_home_s=dh, arrive_work_s=aw, depart_work_s=dw, arrive_home_s=ah,
        probe=_service_probe(5 * H, 21 * H, seen), shift_window_s=2 * H, shift_step_s=0.5 * H, bound_s=29 * H,
        station_candidates={0: [P.StationCandidate("s1", 1.0, 1.0, 7 * H)]})
    assert list(result.actions) == [P.ACTION_DAY_SHIFTED]
    assert not any(t["identifier"].str.contains(":s1:").any() for t in seen)


def test_the_report_counts_the_station_redraws_and_warns_above_the_share(caplog):
    report = {"n_pt_days": 10, "actions": {P.ACTION_CONNECTION_FOUND: 8, P.ACTION_DAY_SHIFTED: 0,
                                           P.ACTION_STATION_REDRAWN: 2, P.ACTION_TO_CAR: 0, P.ACTION_KEPT: 0},
              "shift_abs_s_median": None, "n_station_redraw_days": 2}
    with caplog.at_level("INFO"):
        P.log_report(report, warn_share=0.05)
    assert any(r.levelname == "WARNING" and P.ACTION_STATION_REDRAWN in r.getMessage() for r in caplog.records)


def test_station_candidates_skip_in_ring_agents_and_follow_the_weight_order():
    # Tested on the helper: the builder's in-ring path needs the real-origin Gemeinde inputs.
    from braunschweig.synthesis.incommuters import _station_candidates
    weighted = pd.DataFrame({"source_ars5": ["03241"] * 4 + ["03153"],
                             "stop_id": ["a", "b", "c", "d", "e"], "weight": [0.4, 0.1, 0.3, 0.2, 1.0],
                             "x": [3000.0, 2000.0, 3000.0, 4000.0, 5000.0], "y": [0.0] * 5})
    out = _station_candidates(
        positions=[0, 1, 2], orig_ars=["03241", "03241", "03153"], drawn_stop_ids=["a", "c", "e"],
        weighted=weighted, gate_xy=np.zeros((3, 2)), arrive_work_s=np.full(3, 8 * H), dist_km=np.full(3, 10.0),
        is_in_ring=np.array([False, True, False]), n_candidates=3, speed_kmh=30.0, detour_factor=1.0)
    assert [c.stop_id for c in out[0]] == ["c", "d"]       # without the drawn "a", descending weight, at most n-1
    assert 1 not in out                                     # in-ring origin: home is a Gemeinde point, no re-draw
    assert 2 not in out                                     # the Kreis has no other station
    # home departure recomputed for the candidate's access distance (3 km + 10 km at 30 km/h, detour 1)
    assert out[0][0].depart_home_s == pytest.approx(8 * H - 13.0 / 30.0 * H)
    assert (out[0][0].x, out[0][0].y) == (3000.0, 0.0)


def _two_station_frames(probe, *, n_candidates=P.DEFAULT_STATION_CANDIDATES):
    gates, assignment, flows, zgb_work, hp, ht = _inputs()
    pt_stops = pd.DataFrame({"source_ars5": ["03241", "03241"], "stop_id": ["stopA", "stopB"],
                             "x": [STATION_A[0], STATION_B[0]], "y": [STATION_A[1], STATION_B[1]],
                             "reach": ["direct", "direct"], "ewz": [90000.0, 10000.0]})
    pt_check = None if probe is None else P.PtCheck(probe=probe, shift_window_s=2 * H, shift_step_s=0.5 * H,
                                                    bound_s=29 * H, station_candidates=n_candidates)
    return build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.005, gates=gates, assignment=assignment,
        zgb_work=zgb_work, mode_reference={">=10": {"pt": 1.0}}, band_edges=(10,), hts_persons=hp, hts_trips=ht,
        person_col="person_id", n_residents=100, n_resident_households=40, rng=np.random.default_rng(1),
        gate_speed_kmh=30.0, pt_entry_stops=pt_stops, pt_check=pt_check, time_bound_s=29 * H)


def test_a_pt_in_commuter_whose_station_has_no_connection_boards_at_another_station_of_its_kreis():
    frames = _two_station_frames(_station_probe([STATION_B]))
    actions = frames["pt_connection"]["actions"]
    assert actions[P.ACTION_STATION_REDRAWN] >= 1                       # most draw the heavier stopA
    assert actions[P.ACTION_CONNECTION_FOUND] + actions[P.ACTION_STATION_REDRAWN] == len(frames["persons"])
    assert (frames["trips"]["mode"] == "pt").all()
    home = frames["locations"][frames["locations"]["activity_index"] == 0]
    assert np.allclose(home.geometry.x, STATION_B[0]) and np.allclose(home.geometry.y, STATION_B[1])
    # one gate, one workplace, one donor: the home departure recomputed for stopB equals the drawn stopB one
    assert frames["trips"].loc[frames["trips"]["trip_index"] == 0, "departure_time"].nunique() == 1
    assert np.allclose(frames["validation"]["entry_x"], STATION_B[0])


def test_without_a_connected_station_the_in_commuter_drives_from_the_road_gate():
    frames = _two_station_frames(_station_probe([]))
    assert frames["pt_connection"]["actions"][P.ACTION_TO_CAR] == len(frames["persons"])
    assert (frames["trips"]["mode"] == "car").all()
    home = frames["locations"][frames["locations"]["activity_index"] == 0]
    assert np.allclose(home.geometry.x, 605000.0) and np.allclose(home.geometry.y, 5840000.0)   # home_x == gate_x
    cars = frames["vehicles"][frames["vehicles"]["mode"] == "car"]
    assert len(cars) == len(frames["persons"])                          # the legacy vehicles frame carries the cars


def test_a_single_station_candidate_disables_the_redraw():
    frames = _two_station_frames(_station_probe([STATION_B]), n_candidates=1)
    assert frames["pt_connection"]["actions"][P.ACTION_STATION_REDRAWN] == 0
