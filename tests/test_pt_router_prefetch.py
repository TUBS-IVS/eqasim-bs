"""The router prefetch of the timetable search loops gives byte-identical stage results (ADR-0196, audit H).

Each test runs one search twice on the same deterministic fake router: once as before (every round starts the
router) and once with the loop's superset prefetched through ``PrefetchRouter``. The results must be identical, the
prefetched run must start the router fewer times, no round may fall back to a direct call, and every task whose
answer the decision code read must have been asked of the router (no answer is invented by the cache).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.data.transit import connections
from braunschweig.data.transit.router_cache import PrefetchRouter, task_keys
from tests.pt_router_fake import FakeRouter

STOPS = ("s1", "s2")


class _Probe:
    """``connection_mask`` over a router, recording the task keys whose answers it read."""

    def __init__(self, router):
        self.router = router
        self.read: set = set()

    def __call__(self, tasks, **kwargs):
        trips, legs = self.router(tasks, **kwargs)
        self.read.update(task_keys(tasks))
        return connections.connection_mask(trips, legs, inside_stop_ids=set(STOPS), walk_max_m=5000.0,
                                           task_ids=tasks["identifier"].astype(str))


def _assert_same(left, right):
    if isinstance(left, pd.DataFrame):
        pd.testing.assert_frame_equal(left, right)
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            _assert_same(left[key], right[key])
    elif isinstance(left, np.ndarray):
        np.testing.assert_array_equal(left, right)
    else:
        assert left == right


def test_incommuter_pt_days_with_rebalancing_are_identical_with_fewer_router_starts():
    """F: employee in-commuters with the pt re-balancing (the stage runs ``resolve_pt_days`` once plus once per
    re-balancing round); defect caught: a prefetched answer that differs from the direct one changes an action,
    offset, station or promoted agent."""
    from braunschweig.synthesis.incommuter_pt_connections import PtCheck
    from braunschweig.synthesis.incommuters import build_incommuter_frames
    from tests.test_incommuters import _minimal_inputs

    # Variety, so the timetable answers differ between agents: 12 donors with day times 10 min apart and four
    # entry stations of the source Kreis (the station re-draw has alternatives).
    gates, assignment, flows, zgb_work, hp0, ht0 = _minimal_inputs()
    hp = pd.concat([hp0.assign(person_id=k + 1) for k in range(12)], ignore_index=True)
    ht = pd.concat([ht0.assign(person_id=k + 1, departure_time=ht0["departure_time"] + 600.0 * k,
                               arrival_time=ht0["arrival_time"] + 600.0 * k) for k in range(12)], ignore_index=True)
    stations = pd.DataFrame({"source_ars5": ["03241"] * 4, "stop_id": ["railA", "railB", "railC", "railD"],
                             "x": [604000.0, 606000.0, 608000.0, 610000.0], "y": [5841000.0] * 4,
                             "reach": ["direct"] * 4, "ewz": [50000.0, 30000.0, 20000.0, 10000.0]})

    # Two full stage builds (about 10 s each): the re-balancing rounds exist only inside build_incommuter_frames.
    def run(prefetch: bool):
        fake = FakeRouter(STOPS, connect_share=0.3)
        router = PrefetchRouter(fake, label="test") if prefetch else fake
        probe = _Probe(router)
        frames = build_incommuter_frames(
            flows=flows, zgb_kreise={"03101"}, sampling_rate=0.2, gates=gates, assignment=assignment,
            zgb_work=zgb_work, mode_reference={">=10": {"pt": 0.5, "car": 0.5}}, band_edges=(10,),
            hts_persons=hp, hts_trips=ht, person_col="person_id", n_residents=100, n_resident_households=40,
            rng=np.random.default_rng(11), gate_speed_kmh=30.0, pt_entry_stops=stations,
            mode_balance=True, time_bound_s=29 * 3600.0,
            pt_check=PtCheck(probe=probe, bound_s=29 * 3600.0,
                             prefetch=router.prefetched if prefetch else None))
        return frames, fake, router, probe

    direct, direct_fake, _, _ = run(prefetch=False)
    cached, cached_fake, router, probe = run(prefetch=True)

    _assert_same(direct, cached)
    actions = direct["pt_connection"]["actions"]
    # every step of the chain really ran (shift loop, station re-draw, switch to car)
    assert actions["day_shifted"] > 0 and actions["station_redrawn"] > 0 and actions["to_car"] > 0, actions
    n_resolves = 1 + direct["pt_rebalance"]["n_iterations"]
    assert n_resolves > 1                                                       # and the re-balancing too
    assert direct_fake.calls > cached_fake.calls
    assert cached_fake.calls <= 3 * n_resolves     # planned days + one prefetch + one station probe per resolve
    assert router.fallback_tasks == 0
    assert probe.read <= cached_fake.asked


def _outside_tour_day(n_persons: int, seed: int):
    """Persons with two home-based pt tours each (the first ending close before the second, so a shift of one tour
    changes what the other may do), random times and endpoints, mixed car / car_passenger availability."""
    rng = np.random.default_rng(seed)
    rows, endpoints = [], []
    for person in range(n_persons):
        start = rng.uniform(5.0, 12.0)
        gap = rng.choice([0.25, 0.75, 1.5])
        times = [start, start + 1.0, start + 3.0, start + 4.0, start + 4.0 + gap, start + 5.0 + gap,
                 start + 8.0 + gap, start + 9.0 + gap]
        purposes = [("home", "leisure"), ("leisure", "home"), ("home", "shop"), ("shop", "home")]
        for trip, (before, after) in enumerate(purposes):
            rows.append((person, trip, before, after, "pt", times[2 * trip] * 3600.0, times[2 * trip + 1] * 3600.0))
            endpoints.append(rng.uniform(0.0, 40000.0, size=4).round(1))
    trips = pd.DataFrame(rows, columns=["person_id", "trip_index", "preceding_purpose", "following_purpose", "mode",
                                        "departure_time", "arrival_time"])
    endpoints = pd.DataFrame(endpoints, columns=["ox", "oy", "dx", "dy"])
    availability = pd.DataFrame({"can_car": rng.random(n_persons) < 0.4,
                                 "can_car_passenger": rng.random(n_persons) < 0.5},
                                index=pd.Index(range(n_persons), name="person_id"))
    return trips, endpoints, availability


def test_outside_tour_shifts_are_identical_with_one_prefetch_call():
    """G: ``resolve_pt_tours``; defect caught: a prefetched answer that differs from the direct one, or a superset
    that misses a task the shift loop asks after an earlier shift moved a neighbouring tour."""
    from braunschweig.synthesis.outside_tour_modes import pt_connections as P
    from braunschweig.synthesis.outside_tour_modes.tours import home_based_tour_ids

    trips, endpoints, availability = _outside_tour_day(60, seed=3)

    def run(prefetch: bool):
        fake = FakeRouter(STOPS, connect_share=0.4)
        router = PrefetchRouter(fake, label="test") if prefetch else fake
        probe = _Probe(router)
        result = P.resolve_pt_tours(
            trips=trips, tour_ids=home_based_tour_ids(trips), outside=np.ones(len(trips), bool),
            portal_tour=np.zeros(len(trips), bool), endpoints=endpoints, availability=availability, probe=probe,
            shift_window_s=7200.0, shift_step_s=1800.0, bound_s=29 * 3600.0,
            prefetch=router.prefetched if prefetch else None)
        return result, fake, router, probe

    direct, direct_fake, _, _ = run(prefetch=False)
    cached, cached_fake, router, probe = run(prefetch=True)

    pd.testing.assert_frame_equal(direct.trips, cached.trips)
    pd.testing.assert_frame_equal(direct.tour_actions, cached.tour_actions)
    assert direct.report == cached.report
    actions = direct.report["actions"]
    assert actions[P.ACTION_TOUR_SHIFTED] > 0 and actions[P.ACTION_TOUR_TO_CAR] > 0, actions
    assert direct_fake.calls == 9 and cached_fake.calls == 2     # planned legs + 8 offsets vs planned + prefetch
    assert router.fallback_tasks == 0
    assert probe.read <= cached_fake.asked


def test_portal_gates_and_both_grid_walks_are_identical_with_four_router_starts():
    """H: ``build_portal_trips`` with pt stays the timetable serves only partly (gate choice, the departure-shift
    walk and the earlier-return walk of the no-connection chain); defect caught: a gate, shift or chain action
    that differs because the prefetched superset or the plan/decision split of ``choose_pt_gates`` asked other
    tasks than the per-step probe."""
    from braunschweig.synthesis.portal_trips import stage
    from tests.test_portal_trips_stage import _fixture

    base = _fixture()
    rng = np.random.default_rng(5)
    n = 40   # persons like the fixture's person 2: no car, no lift, a far leisure stay (always a pt stay)
    persons = pd.DataFrame({"person_id": range(1, n + 1), "household_id": [20] * n, "car_availability": ["none"] * n,
                            "has_license": [False] * n, "bicycle_availability": ["all"] * n,
                            "car_passenger_availability": ["none"] * n})
    rows = []
    for person in range(1, n + 1):
        start = rng.uniform(5.0, 18.0) * 3600.0
        back = start + rng.uniform(2.5, 9.0) * 3600.0
        rows += [(person, 0, "home", "leisure", "car", 120000.0, start, start + 7200.0, False),
                 (person, 1, "leisure", "home", "car", 120000.0, back, back + 7200.0, False)]
    # (one stay per person: the bound against the next trip is covered by the deterministic test below)
    trips = pd.DataFrame(rows, columns=["person_id", "trip_index", "preceding_purpose", "following_purpose", "mode",
                                        "euclidean_distance", "departure_time", "arrival_time",
                                        "is_synthetic_closure"])
    stops = tuple(sorted(base["cut_schedule"].stops))

    def run(prefetch: bool):
        fake = FakeRouter(stops, connect_share=0.3)
        router = PrefetchRouter(fake, label="test") if prefetch else fake
        fixture = dict(base, persons=persons, trips=trips, df_work=base["df_work"].iloc[0:0], router=router,
                       gate_settings=dict(base["gate_settings"], shift_return_earlier=True,
                                          no_connection_last_resort="car_passenger"))
        return stage.build_portal_trips(**fixture), fake, router

    direct, direct_fake, _ = run(prefetch=False)
    cached, cached_fake, router = run(prefetch=True)

    pd.testing.assert_frame_equal(direct["trips"], cached["trips"])
    pd.testing.assert_frame_equal(direct["anchors"], cached["anchors"])
    assert direct["report"] == cached["report"]
    actions = direct["report"]["no_connection_actions"]
    assert actions.get("gate_found", 0) > 0 and actions.get("departure_shifted", 0) > 0, actions
    assert actions.get("tour_to_car_passenger_lift", 0) > 0, actions
    # out + ret of the planned gate choice, then one prefetch per grid walk
    assert cached_fake.calls <= 4 < direct_fake.calls
    assert router.fallback_tasks == 0


def test_a_return_shift_freed_by_the_next_stays_outbound_shift_is_in_the_portal_superset():
    """H, the grid walk's bound against the person's NEXT trip (``next_dep``), which an earlier step's shift moves:
    stay 1's outbound moves +30 min at step 1, which frees stay 0's +1 h return at step 2. A superset filtered by
    the next trip's planned time misses that task (fallback > 0); the result must still equal direct routing."""
    from braunschweig.synthesis.portal_trips import no_connection as nc
    from braunschweig.synthesis.portal_trips.gates import PT_GATE_COLUMNS
    from tests.test_portal_trips_no_connection import KW, _fallback, _gates, _modes, _stay, _trips

    trips = _trips(1, [("home", "out", "pt", 30000, 31000), ("out", "home", "pt", 40000, 41000),
                       ("home", "out", "pt", 42800, 43800), ("out", "home", "pt", 60000, 61000)])
    stays = pd.DataFrame([_stay(1, 0, 1), _stay(1, 2, 3)])
    availability = pd.DataFrame({"can_car": [False], "can_car_passenger": [False]},
                                index=pd.Index([1], name="person_id"))

    def answer(key):  # key = (stay position, outbound departure, return departure, 0, 0)
        position, out_s, ret_s = key[0], key[1], key[2]
        served = (position == 1 and out_s == 42800 + 1800) or (position == 0 and ret_s == 40000 + 3600)
        return "walk" if served else None

    def run(prefetch: bool):
        fake = FakeRouter(STOPS, answer=answer)
        router = PrefetchRouter(fake, label="test") if prefetch else fake

        def tasks(positions, out_s, ret_s):
            return pd.DataFrame({"identifier": [str(i) for i in range(len(positions))], "origin_x": positions,
                                 "origin_y": out_s, "destination_x": np.nan_to_num(ret_s, nan=-1.0),
                                 "destination_y": 0.0, "departure_time": 0.0})

        def probe(positions, out_s, ret_s):
            frame = tasks(positions, out_s, ret_s)
            trips_rows, legs = router(frame, allow_no_route_at_all=True)
            ok = connections.connection_mask(trips_rows, legs, inside_stop_ids=set(STOPS), walk_max_m=5000.0,
                                             task_ids=frame["identifier"])
            return pd.DataFrame({"gate_id": ["g" if v else None for v in ok], "x": 1.0, "y": 2.0,
                                 "reason": "gate_found", "n_in_direction": 1, "n_served": 1, "n_connected": 1},
                                columns=PT_GATE_COLUMNS)

        out = nc.resolve_no_connection(
            stays, trips, _modes(2), availability, _gates([None, None]), probe, fallback_gates=_fallback(2),
            prefetch=(lambda p, o, r: router.prefetched(tasks(p, o, r), allow_no_route_at_all=True))
            if prefetch else None, **KW)
        return out, fake, router

    direct, direct_fake, _ = run(prefetch=False)
    cached, cached_fake, router = run(prefetch=True)
    for left, right in zip(direct, cached):
        pd.testing.assert_frame_equal(left, right)
    assert direct[0]["no_connection_action"].tolist() == [nc.ACTION_DEPARTURE_SHIFTED] * 2
    assert direct[1]["departure_time"].tolist() == [30000.0, 40000.0 + 3600.0, 42800.0 + 1800.0, 60000.0]
    assert cached_fake.calls == 1 < direct_fake.calls == 2
    assert router.fallback_tasks == 0
