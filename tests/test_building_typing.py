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
