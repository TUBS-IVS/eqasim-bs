import pandas as pd
from braunschweig.analysis import home_match_validation as v


def test_type_match_share_and_assortativity():
    placed = pd.DataFrame({
        "household_id": [1, 2, 3, 4],
        "building_type_3class": ["ein_zweifamilienhaus", "ein_zweifamilienhaus",
                                 "mehrfamilienhaus", "mehrfamilienhaus"],
        "household_size": [5, 4, 1, 2],
        "home_location_id": [10, 11, 20, 21]})
    bld = pd.DataFrame({"building_id": [10, 11, 20, 21],
                        "btype": ["efh_zfh", "efh_zfh", "mfh", "mfh"],
                        "size": [150.0, 130.0, 50.0, 60.0]})
    m = v.home_match_metrics(placed, bld)
    assert m["type_match_share"] == 1.0      # all in matching type
    assert m["size_assortativity"] > 0.0     # bigger HH in bigger dwelling
    assert m["n_households"] == 4
