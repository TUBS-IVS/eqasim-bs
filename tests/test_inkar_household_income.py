"""The INKAR Kreis income scale is relative to the population-weighted national mean (ADR-0191)."""
import pandas as pd
import pytest

from braunschweig.data.inkar.household_income import kreis_income_scale


def _income():
    return pd.DataFrame({"ars5": ["01001", "09162"], "einkommen_eur": [1000.0, 2000.0]})


def test_scale_divides_by_the_population_weighted_national_mean():
    # Hand-computed: (3 * 1000 + 1 * 2000) / 4 = 1250 EUR. The unweighted Kreis mean (1500)
    # would give 0.667 / 1.333 instead.
    population = pd.Series({"01001": 3, "09162": 1})
    out, national_mean = kreis_income_scale(_income(), population)
    assert national_mean == pytest.approx(1250.0)
    assert out.set_index("ars5")["scale"].to_dict() == pytest.approx({"01001": 0.8, "09162": 1.6})


def test_a_kreis_without_population_fails_instead_of_shifting_the_reference():
    with pytest.raises(ValueError, match="09162"):
        kreis_income_scale(_income(), pd.Series({"01001": 3}))
