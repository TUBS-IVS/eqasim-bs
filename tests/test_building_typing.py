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
