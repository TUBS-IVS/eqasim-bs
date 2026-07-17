import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis.population_validation.placement_income_gate import (
    income_attainment_by_kreis, income_coherence_within_cells,
)


def test_income_attainment_by_kreis():
    hh = pd.DataFrame({"ars5": ["03101"] * 2 + ["03102"] * 2,
                       "household_income_eur": [1000.0, 3000.0, 4000.0, 6000.0]})
    out = income_attainment_by_kreis(hh, {"03101": 2500.0, "03102": 5000.0})
    row = out.set_index("ars5").loc["03101"]
    assert row["realized_mean_eur"] == pytest.approx(2000.0)
    assert row["residual_pct"] == pytest.approx(100.0 * (2000.0 - 2500.0) / 2500.0)


def test_income_coherence_positive_when_income_tracks_cars():
    rng = np.random.RandomState(0)
    n = 400
    cars = rng.randint(0, 3, n)
    hh = pd.DataFrame({
        "ars5": ["03101"] * n,
        "economic_status": ["medium"] * n,
        "number_of_cars": cars,
        "household_income_eur": 1000.0 + 1500.0 * cars + rng.normal(0, 100, n),
    })
    res = income_coherence_within_cells(hh)
    assert res["n_cells"] == 1
    assert res["pooled_spearman"] > 0.8
