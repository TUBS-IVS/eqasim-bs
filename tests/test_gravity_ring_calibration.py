from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.calibrate_gravity_per_rs7 import kreis_distance_to_zgb  # noqa: E402


class _Pt:
    def __init__(self, x, y):
        self.x = x
        self.y = y


def test_distance_to_zgb_is_zero_at_centroid_mean():
    kreise = pd.DataFrame({
        "ars5": ["03101", "03102", "09999"],
        "centroid": [_Pt(0.0, 0.0), _Pt(2000.0, 0.0), _Pt(2000.0, 0.0)],
    })
    out = kreis_distance_to_zgb(kreise, zgb=("03101", "03102"))
    d = dict(zip(out["ars5"], out["dist_km"]))
    assert d["03101"] == 1.0
    assert round(d["09999"], 6) == 1.0
