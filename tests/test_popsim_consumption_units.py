"""Issue #130: OECD-modified consumption units on the popsim path.

Upstream eqasim computes ``consumption_units`` (1.0 + 0.3 per child under 14 +
0.5 per additional adult, ``data/hts/hts.py::calculate_consumption_units``) for
every HTS source; the popsim production path had no such column, so a flat
household-income threshold classified a 5-person household like a single.
These tests pin the popsim wrappers, which REUSE the upstream implementation
(no reimplementation):

- ``income.add_consumption_units``: per synthetic household from person ages.
- ``income.add_income_per_consumption_unit``: equivalised income view, derived
  from the FINAL household_income_eur (after Kreis-Income-Control + tilt, done
  in stage.execute) -- NaN incomes stay NaN.

``high_income`` is deliberately NOT switched to a per-CU basis here: no
traceable per-CU threshold reference exists (no invented references).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import income as inc


def _persons() -> pd.DataFrame:
    # hh1: two adults (40, 38) + one child (10) -> 1.0 + 0.5 + 0.3 = 1.8
    # hh2: single adult (30)                    -> 1.0
    # hh3: adult (50) + 14-year-old             -> 14 counts as over_14 -> 1.5
    return pd.DataFrame({
        "person_id":     [1, 2, 3, 4, 5, 6],
        "household_id":  ["h1", "h1", "h1", "h2", "h3", "h3"],
        "age":           [40, 38, 10, 30, 50, 14],
    })


def test_add_consumption_units_oecd_modified() -> None:
    out = inc.add_consumption_units(_persons())
    got = out.set_index("person_id")["consumption_units"]
    assert got.loc[1] == got.loc[2] == got.loc[3] == pytest.approx(1.8)
    assert got.loc[4] == pytest.approx(1.0)
    assert got.loc[5] == got.loc[6] == pytest.approx(1.5)


def test_add_consumption_units_overwrites_placeholder() -> None:
    persons = _persons()
    persons["consumption_units"] = 1.0  # legacy placeholder
    out = inc.add_consumption_units(persons)
    assert out.set_index("person_id")["consumption_units"].loc[1] == pytest.approx(1.8)


def test_add_income_per_consumption_unit() -> None:
    persons = inc.add_consumption_units(_persons())
    persons["household_income_eur"] = [3600.0, 3600.0, 3600.0, 2000.0, np.nan, np.nan]
    out = inc.add_income_per_consumption_unit(persons)
    got = out.set_index("person_id")["income_per_consumption_unit_eur"]
    assert got.loc[1] == pytest.approx(2000.0)   # 3600 / 1.8
    assert got.loc[4] == pytest.approx(2000.0)   # 2000 / 1.0
    assert np.isnan(got.loc[5])                  # NaN income stays NaN


def test_add_income_per_consumption_unit_requires_columns() -> None:
    with pytest.raises(ValueError, match="consumption_units"):
        inc.add_income_per_consumption_unit(
            pd.DataFrame({"household_income_eur": [1.0]})
        )
