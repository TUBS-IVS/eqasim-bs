from __future__ import annotations
import numpy as np
import pandas as pd
from braunschweig.popsim import income_spatial_tilt as ist


def test_renter_index_is_concave_and_kreis_mean_one() -> None:
    # 3 cells in one Kreis, with rent/m2 and household counts.
    cells = pd.DataFrame({
        "cell_id": ["a", "b", "c"],
        "ars5": ["03101", "03101", "03101"],
        "rent_qm": [6.0, 9.0, 12.0],     # cheap / median / expensive
        "n_households": [100, 100, 100],
    })
    out = ist.build_renter_rent_index(
        cells, rent_col="rent_qm", kreis_col="ars5",
        weight_col="n_households", beta=0.3,
    )
    idx = out.set_index("cell_id")["renter_income_index"]
    # concave + monotone: expensive cell > median > cheap, but compressed (beta<1)
    assert idx["a"] < idx["b"] < idx["c"]
    # household-weighted mean over the Kreis is exactly 1
    w = out["n_households"].to_numpy()
    assert abs(float(np.average(out["renter_income_index"], weights=w)) - 1.0) < 1e-9


def test_renter_index_missing_rent_is_neutral() -> None:
    cells = pd.DataFrame({
        "cell_id": ["a", "b"], "ars5": ["03101", "03101"],
        "rent_qm": [np.nan, 0.0], "n_households": [10, 10],
    })
    out = ist.build_renter_rent_index(cells, rent_col="rent_qm", kreis_col="ars5",
                                      weight_col="n_households", beta=0.3)
    # missing/zero rent -> neutral index 1.0 (no tilt), and mean stays 1
    assert (out["renter_income_index"] == 1.0).all()


def test_owner_index_high_ownership_is_higher_and_mean_one() -> None:
    cells = pd.DataFrame({
        "cell_id": ["a", "b", "c"], "ars5": ["03101"] * 3,
        "eigentuemerquote": [0.2, 0.5, 0.8], "n_households": [100, 100, 100],
    })
    out = ist.build_owner_income_index(cells, quote_col="eigentuemerquote",
                                       kreis_col="ars5", weight_col="n_households", beta=0.3)
    idx = out.set_index("cell_id")["owner_income_index"]
    assert idx["a"] < idx["b"] < idx["c"]
    w = out["n_households"].to_numpy()
    assert abs(float(np.average(out["owner_income_index"], weights=w)) - 1.0) < 1e-9
