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


def test_population_by_kreis_sums_the_destatis_counts_of_the_requested_kreise(tmp_path):
    from braunschweig.data.census.population import load_population_by_kreis

    def row(ags, name, male, female):
        # DESTATIS 12411-0018 layout: ags;name; then 17 male (count;flag) pairs, then 17 female pairs.
        cells = [f"{male};" for _ in range(17)] + [f"{female};" for _ in range(17)]
        return f"{ags};{name};" + ";".join(cells)

    lines = [f"header line {i}" for i in range(6)] + [
        row("03101", "Kreis A", 10, 20),      # 17 * (10 + 20) = 510
        row("03102", "Kreis B", 1, 2),        # 17 * (1 + 2) = 51
        row("03999", "Kreis outside the request", 5, 5),
    ]
    path = tmp_path / "destatis_12411_0018.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = load_population_by_kreis(str(path), ["03101", "03102", "03103"])
    assert result.to_dict() == {"03101": 510, "03102": 51}      # 03103 is not in the file: absent
