from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.bbsr.regiostar import fill_missing_rs7_nearest_neighbour  # noqa: E402


def test_missing_gemeinde_gets_nearest_neighbour_rs7():
    known = pd.DataFrame({
        "commune_id": ["03153017", "03153016"],
        "ars5": ["03153", "03153"],
        "name": ["Goslar", "Braunlage"],
        "regiostar7": [75, 77],
        "x": [0.0, 100.0],
        "y": [0.0, 0.0],
    })
    expected = pd.DataFrame({
        "commune_id": ["03153017", "03153016", "03153019"],
        "x": [0.0, 100.0, 95.0],
        "y": [0.0, 0.0, 0.0],
    })
    out = fill_missing_rs7_nearest_neighbour(known, expected)
    row = out[out["commune_id"] == "03153019"].iloc[0]
    assert int(row["regiostar7"]) == 77
    assert bool(row["rs7_filled"]) is True
    assert int(out[out["commune_id"] == "03153017"].iloc[0]["regiostar7"]) == 75
    assert bool(out[out["commune_id"] == "03153017"].iloc[0]["rs7_filled"]) is False
