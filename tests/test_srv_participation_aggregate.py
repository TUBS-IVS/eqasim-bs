"""
Test for SrV 2023 per-Kreis trip-participation aggregate builder.
"""

import pandas as pd
from scripts.build_srv_participation_aggregate import compute_participation


def test_participation_shares_weighted():
    """Test that participation shares are correctly computed and weighted."""
    persons = pd.DataFrame({
        "HHNR": [1, 1, 2],
        "PNR": [1, 2, 1],
        "ST_CODE": ["03101"] * 3,
        "GEWICHT_P_ZENSUS": [1.0, 1.0, 2.0],
    })
    wege = pd.DataFrame({
        "HHNR": [1, 2],
        "PNR": [1, 1],
        "E_ZWECK_9": [1, 7],  # (1,1) has work trip, (2,1) has leisure trip
    })
    out = compute_participation(persons, wege)
    row = out[out["code"] == "03101"].iloc[0]
    # Person (1,1): weight 1.0, has work trip -> contributes to work
    # Person (1,2): weight 1.0, no trip -> contributes to neither
    # Person (2,1): weight 2.0, has leisure trip -> contributes to leisure
    # Total weight: 4.0
    # work: 1.0/4.0 = 0.25
    # leisure: 2.0/4.0 = 0.5
    # education: 0.0
    assert abs(row["work"] - 1.0 / 4.0) < 1e-9
    assert abs(row["leisure"] - 2.0 / 4.0) < 1e-9
    assert row["education"] == 0.0
