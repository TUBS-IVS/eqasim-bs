# tests/test_building_typing.py
import numpy as np, pandas as pd
from braunschweig.synthesis.locations import building_typing as bt


def _fp(areas):
    return pd.DataFrame({"building_id": range(len(areas)), "area_m2": list(areas)})


def test_largest_footprints_become_mfh_in_census_proportion():
    rng = np.random.RandomState(0)
    fp = _fp([50, 60, 70, 1000, 1200])   # 5 footprints
    # census: 4 EFH buildings, 1 MFH -> mfh_share 0.2 -> round(5*0.2)=1 MFH
    out = bt.assign_building_types(fp, {"efh_zfh": 4, "mfh": 1, "sonst": 0}, rng)
    typ = out.set_index("building_id")["btype"]
    assert typ[4] == "mfh"            # area 1200 (largest) is the MFH
    assert (typ == "efh_zfh").sum() == 4


def test_all_zero_counts_default_to_efh():
    rng = np.random.RandomState(0)
    out = bt.assign_building_types(_fp([100, 200]), {"efh_zfh": 0, "mfh": 0, "sonst": 0}, rng)
    assert (out["btype"] == "efh_zfh").all()


def test_no_census_signal_types_tall_buildings_as_mfh_via_height():
    """When the census carries NO building-type counts for a cell (suppression),
    use LoD2 height: >= MFH_MIN_FLOORS (4) floors -> MFH, else EFH. (Without height
    the cell would be forced all-EFH, mis-typing the tall MFH blocks that stand there.)"""
    rng = np.random.RandomState(0)
    fp = pd.DataFrame({"building_id": [0, 1, 2], "area_m2": [100.0, 100.0, 100.0],
                       "height_m": [12.0, 3.0, 30.0]})  # floors ~ 4, 1, 10
    out = bt.assign_building_types(fp, {"efh_zfh": 0, "mfh": 0, "sonst": 0}, rng)
    typ = out.set_index("building_id")["btype"]
    assert typ[0] == "mfh"      # 12 m ~ 4 floors -> MFH
    assert typ[1] == "efh_zfh"  # 3 m ~ 1 floor  -> EFH
    assert typ[2] == "mfh"      # 30 m ~ 10 floors -> MFH


def test_no_census_signal_floor_threshold_boundary():
    """Pin the MFH_MIN_FLOORS boundary exactly: 3 floors -> EFH, 4 floors -> MFH.
    Guards against an off-by-one regression if `>=` or the constant is changed."""
    rng = np.random.RandomState(0)
    fp = pd.DataFrame({"building_id": [0, 1], "area_m2": [100.0, 100.0],
                       "height_m": [9.0, 12.0]})  # 9/3=3 floors vs 12/3=4 floors
    out = bt.assign_building_types(fp, {"efh_zfh": 0, "mfh": 0, "sonst": 0}, rng)
    typ = out.set_index("building_id")["btype"]
    assert bt.MFH_MIN_FLOORS == 4               # boundary this test pins
    assert typ[0] == "efh_zfh"  # 3 floors  < 4 -> EFH
    assert typ[1] == "mfh"      # 4 floors >= 4 -> MFH


def test_no_census_signal_all_nan_height_stays_all_efh():
    """Contract: no census counts AND no usable height -> byte-identical all-EFH
    fallback (preserves the pre-height behaviour for height-less / all-NaN cells)."""
    rng = np.random.RandomState(0)
    fp = pd.DataFrame({"building_id": [0, 1], "area_m2": [100.0, 200.0],
                       "height_m": [float("nan"), float("nan")]})
    out = bt.assign_building_types(fp, {"efh_zfh": 0, "mfh": 0, "sonst": 0}, rng)
    assert (out["btype"] == "efh_zfh").all()


def test_build_slots_capacity_and_assortative_size():
    rng = np.random.RandomState(0)
    typed = pd.DataFrame({"building_id": [0, 1, 2], "area_m2": [80.0, 90.0, 1000.0],
                          "btype": ["efh_zfh", "efh_zfh", "mfh"]})
    # 2 EFH dwellings, 8 MFH dwellings, 10 occupied; sizes: 2x130, 8x60
    slots = bt.build_slots(typed, {"efh_zfh": 2, "mfh": 8, "sonst": 0}, occupied=10.0,
                           size_hist=[(130.0, 2.0), (60.0, 8.0)], rng=rng)
    assert len(slots) == 10
    assert (slots[slots.btype == "mfh"]["building_id"] == 2).all()      # all MFH on the block
    assert (slots[slots.btype == "efh_zfh"]).shape[0] == 2
    # assortative: the two largest dwellings (130) go to the EFH slots
    efh_sizes = sorted(slots[slots.btype == "efh_zfh"]["size"].tolist())
    assert efh_sizes == [130.0, 130.0]


def test_build_slots_efh_not_overstacked_when_dwellings_exceed_footprints():
    """Regression: EFH capacity must spread proportionally to area, not dump all
    excess onto the single largest footprint (old code gave 6 slots on an 85 m² house)."""
    rng = np.random.RandomState(0)
    typed = pd.DataFrame({
        "building_id": [0, 1, 2],
        "area_m2": [85.0, 44.0, 82.0],
        "btype": ["efh_zfh", "efh_zfh", "efh_zfh"],
    })
    size_hist = [(70.0, 8.0)]  # 8 dwellings at 70 m²
    slots = bt.build_slots(
        typed,
        whg_by_type={"efh_zfh": 8, "mfh": 0, "sonst": 0},
        occupied=8,
        size_hist=size_hist,
        rng=rng,
    )
    assert len(slots) == 8, f"expected 8 slots, got {len(slots)}"
    counts = slots.groupby("building_id").size()
    # No single EFH building should absorb more than 4 slots (old code gave 6)
    assert counts.max() <= 4, f"over-stacked: {counts.to_dict()}"
    # Largest-area footprint (id 0, 85 m²) should get at least as many slots as smallest (id 1, 44 m²)
    assert counts.get(0, 0) >= counts.get(1, 0), f"area ordering violated: {counts.to_dict()}"


def test_assign_building_types_zero_footprints():
    """Zero-footprint frame must return an empty DataFrame with a 'btype' column (no crash)."""
    rng = np.random.RandomState(0)
    empty_fp = pd.DataFrame({"building_id": pd.Series(dtype=int), "area_m2": pd.Series(dtype=float)})
    out = bt.assign_building_types(empty_fp, {"efh_zfh": 3, "mfh": 1, "sonst": 0}, rng)
    assert "btype" in out.columns, "Output frame missing 'btype' column"
    assert len(out) == 0, f"Expected 0 rows, got {len(out)}"


def test_assign_building_types_fewer_footprints_than_census():
    """2 footprints but census counts sum to 8 buildings.
    Must return exactly 2 rows; every 'btype' is a known class; no exception."""
    rng = np.random.RandomState(0)
    fp = _fp([100.0, 200.0])
    out = bt.assign_building_types(fp, {"efh_zfh": 4, "mfh": 4, "sonst": 0}, rng)
    assert len(out) == 2, f"Expected 2 rows, got {len(out)}"
    valid_btypes = {"efh_zfh", "mfh", "sonst"}
    bad = set(out["btype"].unique()) - valid_btypes
    assert not bad, f"Unknown btype values: {bad}"


def test_build_slots_handles_suppressed_dwelling_types():
    """Regression: when all whg_by_type counts are zero (census suppression),
    build_slots must not crash and must place T slots on the real footprints."""
    rng = np.random.RandomState(0)
    typed = pd.DataFrame({
        "building_id": [0, 1],
        "area_m2": [80.0, 120.0],
        "btype": ["efh_zfh", "efh_zfh"],
    })
    whg_by_type = {"efh_zfh": 0, "mfh": 0, "sonst": 0}  # all-zero suppression case
    size_hist = [(70.0, 6.0)]  # 6 dwellings available
    slots = bt.build_slots(typed, whg_by_type=whg_by_type, occupied=5,
                           size_hist=size_hist, rng=rng)
    assert len(slots) == 5, f"expected 5 slots, got {len(slots)}"
    assert slots["building_id"].isin({0, 1}).all(), "slots placed on wrong buildings"
    assert (slots["btype"] == "efh_zfh").all(), "expected all slots on efh_zfh footprints"


def test_build_slots_volume_weights_tall_building():
    import numpy as np, pandas as pd
    from braunschweig.synthesis.locations import building_typing as bt
    # two MFH footprints, equal area; one is tall (height 30 -> 10 floors), one flat (NaN)
    typed = pd.DataFrame({"building_id": [0, 1], "area_m2": [200.0, 200.0],
                          "btype": ["mfh", "mfh"], "height_m": [30.0, float("nan")]})
    slots = bt.build_slots(typed, {"efh_zfh": 0, "mfh": 11, "sonst": 0}, occupied=11,
                           size_hist=[(60.0, 11.0)], rng=np.random.RandomState(0))
    cap = slots.groupby("building_id").size()
    assert cap[0] > cap[1]            # tall building gets MORE dwellings than the flat one
    assert len(slots) == 11           # total preserved


def test_building_volume_height_fallbacks():
    """None/NaN/0/negative height all fall back to 1 floor; valid height scales correctly."""
    assert bt.building_volume(100.0, None) == 100.0
    assert bt.building_volume(100.0, float("nan")) == 100.0
    assert bt.building_volume(100.0, 0) == 100.0
    assert bt.building_volume(100.0, -5) == 100.0
    assert bt.building_volume(100.0, 30.0) == 1000.0   # 30 / 3.0 = 10 floors


def test_build_slots_total_equals_occupied_with_unbalanced_shares():
    """Hamilton apportionment guarantees len(slots) == round(occupied).

    With equal shares (1/3 each) and occupied=10, naive independent rounding gives
    int(round(10/3)) + int(round(10/3)) + int(round(10/3)) = 3+3+3 = 9, not 10.
    The fix must produce exactly 10 slots.
    """
    rng = np.random.RandomState(0)
    # one building of each type
    typed = pd.DataFrame({
        "building_id": [0, 1, 2],
        "area_m2": [100.0, 200.0, 150.0],
        "btype": ["efh_zfh", "mfh", "sonst"],
    })
    size_hist = [(60.0, 10.0)]  # 10 dwellings at 60 m²
    slots = bt.build_slots(
        typed,
        whg_by_type={"efh_zfh": 1, "mfh": 1, "sonst": 1},
        occupied=10,
        size_hist=size_hist,
        rng=rng,
    )
    assert len(slots) == 10, f"expected 10 slots, got {len(slots)}"


def test_assign_building_types_ranks_by_volume_when_tall():
    import numpy as np, pandas as pd
    from braunschweig.synthesis.locations import building_typing as bt
    # small footprint that is very tall vs a larger flat one; census says 1 MFH building
    fp = pd.DataFrame({"building_id": [0, 1], "area_m2": [120.0, 300.0],
                       "height_m": [30.0, 4.0]})   # vol: 0=120*10=1200, 1=300*1=300
    out = bt.assign_building_types(fp, {"efh_zfh": 1, "mfh": 1, "sonst": 0}, np.random.RandomState(0))
    typ = out.set_index("building_id")["btype"]
    assert typ[0] == "mfh"        # the tall small footprint is the MFH (by volume), not the big flat one


def test_build_slots_all_nan_height_equals_area():
    """All-NaN height_m column must produce byte-identical results to no height_m column.

    Pins the contract: when height data is present but entirely missing, build_slots
    must fall through to area-only weighting, not diverge due to NaN arithmetic.
    """
    rng_a = np.random.RandomState(42)
    rng_b = np.random.RandomState(42)
    areas = [80.0, 120.0, 200.0, 95.0, 150.0]
    whg = {"efh_zfh": 0, "mfh": 12, "sonst": 0}
    size_hist = [(60.0, 12.0)]

    typed_with_nan = pd.DataFrame({
        "building_id": list(range(len(areas))),
        "area_m2": areas,
        "btype": ["mfh"] * len(areas),
        "height_m": [float("nan")] * len(areas),
    })
    typed_no_col = pd.DataFrame({
        "building_id": list(range(len(areas))),
        "area_m2": areas,
        "btype": ["mfh"] * len(areas),
    })

    slots_with_nan = bt.build_slots(typed_with_nan, whg, occupied=12, size_hist=size_hist, rng=rng_a)
    slots_no_col = bt.build_slots(typed_no_col, whg, occupied=12, size_hist=size_hist, rng=rng_b)

    pd.testing.assert_frame_equal(
        slots_with_nan.reset_index(drop=True),
        slots_no_col.reset_index(drop=True),
        check_like=False,
    )
