import numpy as np
import pytest

from braunschweig.popsim import income_kreis_control as kic
from braunschweig.data.mid.income_by_size import INCOME_BRACKET_CATEGORIES


def test_bracket_expected_eur_shape_and_values():
    e_b = kic.bracket_expected_eur()
    assert e_b.shape == (len(INCOME_BRACKET_CATEGORIES),)
    # under_500 floored at INCOME_MIN_EUR=100 -> (100+500)/2 = 300
    assert e_b[0] == pytest.approx(300.0)
    # 2000_3000 -> (2000+3000)/2 = 2500 (look up by name, robust to bracket reorder)
    assert e_b[INCOME_BRACKET_CATEGORIES.index("2000_3000")] == pytest.approx(2500.0)
    # open top -> 7000*(1+0.4) = 9800
    assert e_b[-1] == pytest.approx(9800.0)
    # strictly increasing
    assert np.all(np.diff(e_b) > 0)


def test_build_class_midpoint_eur_matches_attributes():
    table = kic.build_class_midpoint_eur()
    assert table["under_500"] == pytest.approx(250.0)
    assert table["over_7000"] == pytest.approx(8000.0)
    assert table["5000_5600"] == pytest.approx(5300.0)


def test_income_class_from_eur_is_monotone():
    table = kic.build_class_midpoint_eur()
    labels = kic.income_class_from_eur(np.array([100.0, 5400.0, 9000.0]), table)
    assert labels[0] == "under_500"
    assert labels[1] == "5000_5600"
    assert labels[2] == "over_7000"
