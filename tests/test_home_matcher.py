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


def test_match_cell_over_capacity_places_all_and_counts():
    """Over-capacity: 5 EFH/ZFH households, only 2 efh_zfh slots.
    All 5 must receive a non-NA building_id; the 3 extras count as n_overcapacity;
    each over-capacity household is placed on an efh_zfh building (same-type pool)."""
    import numpy as np, pandas as pd
    rng = np.random.RandomState(0)
    households = pd.DataFrame({
        "household_id": ["a", "b", "c", "d", "e"],
        "btype": ["efh_zfh"] * 5,
        "household_size": [5, 4, 3, 2, 1],
    })
    slots = pd.DataFrame({
        "slot_id": [0, 1], "building_id": [10, 11],
        "btype": ["efh_zfh", "efh_zfh"], "size": [140.0, 80.0],
    })
    out, rep = hm.match_cell(households, slots, rng)
    # All households must be placed (no NA building_id)
    assert out["building_id"].notna().all(), "Some households left unplaced (NA building_id)"
    assert rep.n_overcapacity == 3, f"Expected 3 overcapacity, got {rep.n_overcapacity}"
    # Determine which households were placed via over-capacity (not in the flow slots)
    # The first 2 (largest) get the normal flow slots; the remaining 3 are over-capacity
    by = out.set_index("household_id")["building_id"]
    # All over-capacity must be placed on an efh_zfh building (ids 10 or 11)
    efh_building_ids = {10, 11}
    for hid in ["c", "d", "e"]:
        assert by[hid] in efh_building_ids, (
            f"Overcapacity household {hid!r} placed on unexpected building {by[hid]!r}"
        )


def test_match_cell_cross_type_substitution_assigns_other_type_building():
    """Cross-type substitution: 3 efh_zfh households, 1 efh_zfh slot + 2 mfh slots.
    1 household gets the efh_zfh building; 2 get mfh building_ids (cross-type realized
    in actual building assignment, not just flow dict). n_type_match==1, n_overcapacity==0."""
    import numpy as np, pandas as pd
    rng = np.random.RandomState(0)
    households = pd.DataFrame({
        "household_id": ["x", "y", "z"],
        "btype": ["efh_zfh", "efh_zfh", "efh_zfh"],
        "household_size": [5, 3, 1],
    })
    slots = pd.DataFrame({
        "slot_id": [0, 1, 2], "building_id": [10, 20, 21],
        "btype": ["efh_zfh", "mfh", "mfh"], "size": [120.0, 90.0, 70.0],
    })
    out, rep = hm.match_cell(households, slots, rng)
    by = out.set_index("household_id")["building_id"]
    # Exactly 1 household on the efh_zfh building
    assert (by == 10).sum() == 1, f"Expected 1 household on efh_zfh building 10, got {(by==10).sum()}"
    # The other 2 households are on mfh buildings
    mfh_bids = {20, 21}
    for hid in ["x", "y", "z"]:
        if by[hid] != 10:
            assert by[hid] in mfh_bids, (
                f"Cross-type household {hid!r} placed on unexpected building {by[hid]!r}"
            )
    assert rep.n_type_match == 1, f"Expected n_type_match==1, got {rep.n_type_match}"
    assert rep.n_overcapacity == 0, f"Expected n_overcapacity==0, got {rep.n_overcapacity}"


def test_random_point_in_cell_lands_in_the_cell_square():
    import numpy as np
    import geopandas as gpd
    from shapely.geometry import Point
    from braunschweig.synthesis.locations import home_matcher as hm
    rng = np.random.RandomState(3)
    cell = "CRS3035RES100mN2689100E4337000"
    p = hm.random_point_in_cell(cell, rng)
    back = gpd.GeoSeries([p], crs="EPSG:25832").to_crs("EPSG:3035").iloc[0]
    assert 4337000 <= back.x <= 4337100
    assert 2689100 <= back.y <= 2689100 + 100
