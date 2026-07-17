import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim.placement_income import (
    INCOME_LABEL_BOUNDS_EUR, label_expected_eur, draw_own_income_eur,
    donor_expected_income_eur,
)
from braunschweig.popsim.attributes import INCOME_CLASS_BY_GROUP, INCOME_GROUP_MIDPOINT_EUR
from braunschweig.popsim.income_kreis_control import INCOME_MIN_EUR, INCOME_OPEN_TOP_MAX_EUR


def test_label_bounds_cover_all_codebook_labels():
    assert set(INCOME_LABEL_BOUNDS_EUR) == set(INCOME_CLASS_BY_GROUP.values())


def test_closed_label_bounds_match_committed_midpoints():
    # The codebook ranges and the committed midpoints must be two views of the SAME
    # brackets: (low+high)/2 == midpoint for every closed bracket. under_500 is skipped
    # here only because its committed midpoint (250) is checked implicitly and its
    # EXPECTED value differs by design (the draw floors at INCOME_MIN_EUR=100, so
    # label_expected_eur returns 300; covered by test_expected_eur_matches_draw_in_expectation).
    for group, label in INCOME_CLASS_BY_GROUP.items():
        low, high = INCOME_LABEL_BOUNDS_EUR[label]
        if high is None or label == "under_500":
            continue
        assert (low + high) / 2.0 == pytest.approx(INCOME_GROUP_MIDPOINT_EUR[group])


def test_draw_respects_own_bracket_bounds_and_nan():
    rng = np.random.RandomState(7)
    labels = pd.Series(["900_1500"] * 200 + ["over_7000"] * 200 + [np.nan] * 3)
    eur = draw_own_income_eur(labels, rng)
    closed = eur[:200]
    assert (closed >= 900).all() and (closed < 1500).all()
    top = eur[200:400]
    assert (top >= 7000).all() and (top <= INCOME_OPEN_TOP_MAX_EUR).all()
    assert np.isnan(eur[400:]).all()
    assert (eur[:400] >= INCOME_MIN_EUR).all()


def test_draw_is_deterministic_per_seed():
    labels = pd.Series(["2000_2600"] * 50)
    a = draw_own_income_eur(labels, np.random.RandomState(11))
    b = draw_own_income_eur(labels, np.random.RandomState(11))
    np.testing.assert_array_equal(a, b)


def test_expected_eur_matches_draw_in_expectation():
    rng = np.random.RandomState(3)
    exp = label_expected_eur()
    labels = pd.Series(["over_7000"] * 200_000)
    eur = draw_own_income_eur(labels, rng)
    assert eur.mean() == pytest.approx(exp["over_7000"], rel=0.02)
    # Closed bracket: uniform mean == (max(low, INCOME_MIN_EUR)+high)/2.
    assert exp["under_500"] == pytest.approx((INCOME_MIN_EUR + 500.0) / 2.0)


def test_donor_expected_income_maps_hheink_and_flags_missing():
    donors = pd.DataFrame({"H_ID": [1, 2, 3], "hheink_gr1": [3, 15, 99]})
    s = donor_expected_income_eur(donors)
    assert s.loc[1] == pytest.approx(label_expected_eur()["900_1500"])
    assert s.loc[2] == pytest.approx(label_expected_eur()["over_7000"])
    assert np.isnan(s.loc[3])


def test_unknown_nonnull_labels_warn_and_stay_nan(caplog):
    import logging
    rng = np.random.RandomState(5)
    labels = pd.Series(["900_1500", "bogus_label", np.nan, "keine Angabe", "900_1500"])
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.placement_income"):
        eur = draw_own_income_eur(labels, rng)
    assert np.isnan(eur[[1, 2, 3]]).all()
    assert (~np.isnan(eur[[0, 4]])).all()
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("2/5" in m for m in warning_messages)
