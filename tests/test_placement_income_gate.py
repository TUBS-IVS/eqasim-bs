import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from braunschweig.analysis.population_validation.placement_income_gate import (  # noqa: E402
    realised_status_by_kreis, status_srmse_vs_h4, donor_replication,
)


def _households():
    # Kreis 03102: 2 very_low, 2 high; Kreis 03101: 4 medium. Two donors cloned in 03102.
    return pd.DataFrame({
        "ars5": ["03102"] * 4 + ["03101"] * 4,
        "economic_status": ["very_low", "very_low", "high", "high"] + ["medium"] * 4,
        "source_household_id": [1, 1, 2, 2, 3, 4, 5, 6],
    })


def test_realised_status_shares_sum_to_one_per_kreis():
    r = realised_status_by_kreis(_households())
    a = r[r["ars5"] == "03102"].iloc[0]
    assert a[["very_low", "low", "medium", "high", "very_high"]].sum() == pytest.approx(1.0)
    assert a["very_low"] == pytest.approx(0.5) and a["high"] == pytest.approx(0.5)


def test_srmse_zero_when_realised_equals_target():
    r = realised_status_by_kreis(_households())
    a = r[r["ars5"] == "03102"].iloc[0]
    h4 = pd.DataFrame([{
        "ars5": "03102",
        "very_low": a["very_low"] * 100, "low": a["low"] * 100,
        "medium": a["medium"] * 100, "high": a["high"] * 100,
        "very_high": a["very_high"] * 100,
    }])
    srmse = status_srmse_vs_h4(r, h4)
    assert srmse["03102"] == pytest.approx(0.0, abs=1e-9)


def test_donor_replication_flags_clones():
    rep = donor_replication(_households())
    a = rep[rep["ars5"] == "03102"].iloc[0]
    assert a["n_households"] == 4 and a["n_unique_donors"] == 2
    assert a["max_clones"] == 2
