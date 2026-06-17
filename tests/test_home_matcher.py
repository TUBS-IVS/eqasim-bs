"""Tests for braunschweig/synthesis/locations/home_matcher.py"""
import braunschweig.synthesis.locations.home_matcher as hm


def test_solve_type_flow_exact_match():
    flow = hm.solve_type_flow({"efh_zfh": 2, "mfh": 1}, {"efh_zfh": 2, "mfh": 1})
    assert flow[("efh_zfh", "efh_zfh")] == 2
    assert flow[("mfh", "mfh")] == 1
    assert sum(flow.values()) == 3


def test_solve_type_flow_overflow_penalty():
    # 3 EFH households but only 1 EFH slot + 2 MFH slots
    flow = hm.solve_type_flow({"efh_zfh": 3}, {"efh_zfh": 1, "mfh": 2})
    assert flow[("efh_zfh", "efh_zfh")] == 1
    # overflow goes somewhere; total must cover 3 households
    assert sum(flow.values()) == 3


def test_type_flow_prefers_matching_type_then_cheapest_substitute():
    # 3 EFH HH, 2 MFH HH; capacity: 2 EFH slots, 3 MFH slots
    flow = hm.solve_type_flow({"efh_zfh": 3, "mfh": 2, "sonst": 0},
                              {"efh_zfh": 2, "mfh": 3, "sonst": 0})
    assert flow[("efh_zfh", "efh_zfh")] == 2     # fill matching first
    assert flow[("mfh", "mfh")] == 2
    assert flow[("efh_zfh", "mfh")] == 1         # leftover EFH HH -> MFH
    assert sum(flow.values()) == 5


def test_match_cell_type_fidelity_and_assortative_size():
    import numpy as np, pandas as pd
    rng = np.random.RandomState(0)
    households = pd.DataFrame({
        "household_id": ["a", "b", "c"],
        "btype": ["efh_zfh", "efh_zfh", "mfh"],
        "household_size": [5, 1, 3],
    })
    slots = pd.DataFrame({
        "slot_id": [0, 1, 2], "building_id": [10, 11, 20],
        "btype": ["efh_zfh", "efh_zfh", "mfh"], "size": [140.0, 60.0, 70.0],
    })
    out, rep = hm.match_cell(households, slots, rng)
    by = out.set_index("household_id")["building_id"]
    assert by["c"] == 20                      # MFH HH -> MFH building
    assert set(by[["a", "b"]]) == {10, 11}    # EFH HH -> EFH buildings
    # assortative: largest HH (a, size 5) -> largest dwelling (140 @ building 10)
    assert by["a"] == 10 and by["b"] == 11
    assert rep.n_overcapacity == 0
