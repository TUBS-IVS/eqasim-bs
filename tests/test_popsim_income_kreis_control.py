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


def test_build_kreis_income_targets_mean_one_and_hhsize_correction():
    import pandas as pd
    inkar = pd.DataFrame({"ars5": ["03102", "03103"], "scale": [0.882, 1.091]})
    # UNEQUAL hh_count so the normalization is genuinely household-count-WEIGHTED
    # (equal weights would let an unweighted mean pass and hide the weighting bug).
    stats = pd.DataFrame({
        "ars5": ["03102", "03103"],
        "hh_count": [100.0, 300.0],
        "mean_size": [1.8, 2.1],
    })
    rf = kic.build_kreis_income_targets(inkar, stats, ["03102", "03103"], hhsize_correct=True)
    # household-count-WEIGHTED mean of rf == 1.0 (by construction)
    weighted = (rf["03102"] * 100.0 + rf["03103"] * 300.0) / 400.0
    assert weighted == pytest.approx(1.0)
    # the UNweighted mean is NOT 1.0 -> proves the weighting is actually applied
    assert (rf["03102"] + rf["03103"]) / 2 != pytest.approx(1.0)
    # Wolfsburg richer per-EW AND larger HH -> rf > Salzgitter
    assert rf["03103"] > rf["03102"]


def test_build_kreis_income_targets_degenerate_scale_falls_back_to_one():
    import pandas as pd
    inkar = pd.DataFrame({"ars5": ["03102", "03103"], "scale": [-1.0, -1.0]})
    stats = pd.DataFrame({"ars5": ["03102", "03103"], "hh_count": [100.0, 100.0],
                          "mean_size": [1.8, 2.1]})
    rf = kic.build_kreis_income_targets(inkar, stats, ["03102", "03103"])
    assert rf["03102"] == pytest.approx(1.0)
    assert rf["03103"] == pytest.approx(1.0)


def test_build_kreis_income_targets_single_kreis_is_noop():
    import pandas as pd
    inkar = pd.DataFrame({"ars5": ["03101"], "scale": [1.003]})
    stats = pd.DataFrame({"ars5": ["03101"], "hh_count": [100.0], "mean_size": [2.0]})
    rf = kic.build_kreis_income_targets(inkar, stats, ["03101"])
    assert rf["03101"] == pytest.approx(1.0)


def test_build_kreis_income_targets_hhsize_off_uses_per_ew():
    import pandas as pd
    inkar = pd.DataFrame({"ars5": ["03102", "03103"], "scale": [0.882, 1.091]})
    stats = pd.DataFrame({"ars5": ["03102", "03103"], "hh_count": [100.0, 100.0],
                          "mean_size": [1.8, 2.1]})
    rf = kic.build_kreis_income_targets(inkar, stats, ["03102", "03103"], hhsize_correct=False)
    # per-EW only: rf proportional to scale, mean-1
    assert rf["03103"] / rf["03102"] == pytest.approx(1.091 / 0.882)
