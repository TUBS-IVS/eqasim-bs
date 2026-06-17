# tests/test_home_matcher.py
from braunschweig.synthesis.locations import home_matcher as hm


def test_type_flow_prefers_matching_type_then_cheapest_substitute():
    # 3 EFH HH, 2 MFH HH; capacity: 2 EFH slots, 3 MFH slots
    flow = hm.solve_type_flow({"efh_zfh": 3, "mfh": 2, "sonst": 0},
                              {"efh_zfh": 2, "mfh": 3, "sonst": 0})
    assert flow[("efh_zfh", "efh_zfh")] == 2     # fill matching first
    assert flow[("mfh", "mfh")] == 2
    # 1 leftover EFH HH must go to MFH (only remaining capacity)
    assert flow[("efh_zfh", "mfh")] == 1
    assert sum(flow.values()) == 5
