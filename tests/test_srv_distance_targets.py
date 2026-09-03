"""Unit tests for braunschweig.calibration.srv_distance_targets (synthetic rows only)."""
from __future__ import annotations

import numpy as np
import pytest

from braunschweig.calibration import srv_distance_targets as T


def test_band_constants_align_with_gravity_edges():
    from braunschweig.gravity.friction import BAND_EDGES_KM
    assert T.WORK_BAND_EDGES_KM == BAND_EDGES_KM
    assert T.WORK_BAND_EDGES_KM == (0.0, 5.0, 10.0, 20.0, 30.0, 50.0, 100.0, float("inf"))
    assert len(T.WORK_BAND_LABELS) == len(BAND_EDGES_KM) - 1
    assert T.WORK_BAND_LABELS[0] == "0_5" and T.WORK_BAND_LABELS[-1] == "100_plus"
    assert T.EDUCATION_BAND_EDGES_KM == (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, float("inf"))
    assert T.EDUCATION_BAND_LABELS == ("0_1", "1_2", "2_5", "5_10", "10_20", "20_plus")


@pytest.mark.parametrize("purpose, age, level", [
    (3, 4, "kindergarten"),
    (3, 6, "kindergarten"),
    (3, 40, None),          # kita at 40: out of model age band, excluded
    (4, 8, "grundschule"),
    (4, 5, "grundschule"),
    (4, 14, None),          # grundschule at 14: out of model age band, excluded
    (5, 12, "sekundar_1"),
    (5, 15.7, "sekundar_1"),  # float age truncated to int
    (5, 17, "upper_secondary"),
    (6, 18, "upper_secondary"),
    (6, 24, "university"),
    (5, 25, None),          # secondary school at 25: not comparable, excluded
    (6, 14, None),          # tertiary at 14: implausible, excluded
    (7, 10, None),          # other education institution: excluded by design
    (1, 40, None),          # work is not an education level
    (3, np.nan, None),      # kita with NaN age
    (np.nan, 10, None),     # NaN purpose code
])
def test_education_level(purpose, age, level):
    assert T.education_level(purpose, age) == level


def test_education_level_descriptive_splits_upper_secondary():
    assert T.education_level_descriptive(5, 17) == "oberstufe"
    assert T.education_level_descriptive(6, 17) == "bbs"
    assert T.education_level_descriptive(4, 8) == "grundschule"


@pytest.mark.parametrize("age, level", [
    (3, "kindergarten"), (7, "grundschule"), (12, "sekundar_1"),
    (16, "upper_secondary"), (19, "upper_secondary"), (22, "university"),
    (15.7, "sekundar_1"),    # float age truncated to int
    (np.nan, None),
    (None, None),
])
def test_model_education_level_by_age(age, level):
    assert T.model_education_level(age) == level
