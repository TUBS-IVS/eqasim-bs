"""Unit tests for braunschweig.calibration.srv_distance_targets (synthetic rows only)."""
from __future__ import annotations

import numpy as np
import pandas as pd
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


def _raw_frames():
    """Two households (BS, GF), three persons, six trips."""
    households = pd.DataFrame({"HHNR": [1, 2], "AGS": [3101000, 3151005]})
    persons = pd.DataFrame({
        "HHNR": [1, 1, 2], "PNR": [1, 2, 1], "V_ALTER": [40, 17, 8],
    })
    trips = pd.DataFrame({
        "HHNR":            [1,   1,   1,   1,   2,   2],
        "PNR":             [1,   1,   2,   2,   1,   1],
        "WNR":             [1,   2,   1,   2,   1,   2],
        "V_ZWECK":         [1,   19,  5,   19,  4,   19],   # work, home, school, home, grundschule, home
        "E_START_ZWECK":   [19,  1,   19,  5,   19,  4],
        "V_START_LAGE":    [1,   4,   1,   3,   1,   3],    # 1 = start at own home
        "V_ZIEL_LAGE":     [4,   1,   3,   1,   3,   1],
        "V_START_AGS":     [3101000, 3151005, 3101000, 3101000, 3151005, 3151005],
        "V_ZIEL_AGS":      [3151005, 3101000, 3101000, 3101000, 3151005, 3151005],
        "GIS_LAENGE":      [22.0, 22.5, 3.1, 3.0, -7.0, 1.2],
        "GIS_LAENGE_GUELTIG": [22.0, 22.5, 3.1, 3.0, -7.0, 1.2],
        "GEWICHT_W_ZENSUS": [10.0, 10.0, 5.0, 5.0, 7.0, 7.0],
        "REGIOSTAR7":      [72, 72, 72, 72, 74, 74],
    })
    return trips, persons, households


def test_select_person_observations_work_first_home_to_purpose():
    trips, persons, households = _raw_frames()
    obs, log = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,))
    assert len(obs) == 1
    row = obs.iloc[0]
    assert row["hhnr"] == 1 and row["pnr"] == 1
    assert row["kreis"] == "03101"
    assert row["distance_km"] == pytest.approx(22.0)     # the home->work trip, not work->home
    assert bool(row["intra_gemeinde"]) is False
    assert row["age"] == 40 and row["regiostar7"] == 72
    assert log["n_persons_selected"] == 1


def test_select_person_observations_education_marks_intra_and_excludes_gis_invalid():
    trips, persons, households = _raw_frames()
    obs, log = T.select_person_observations(trips, persons, households, T.EDUCATION_PURPOSES)
    # person (1,2): school trip home->school, intra-Gemeinde; person (2,1): GIS invalid on the
    # home->school leg -> falls back to the school->home leg (1.2 km, valid).
    assert set(zip(obs["hhnr"], obs["pnr"])) == {(1, 2), (2, 1)}
    school = obs[(obs["hhnr"] == 1) & (obs["pnr"] == 2)].iloc[0]
    assert bool(school["intra_gemeinde"]) is True
    assert school["purpose_code"] == 5 and school["age"] == 17
    back = obs[(obs["hhnr"] == 2) & (obs["pnr"] == 1)].iloc[0]
    assert back["distance_km"] == pytest.approx(1.2)
    assert log["n_excluded_gis_invalid"] == 1


def test_select_person_observations_drops_negative_weight_and_over_cap():
    trips, persons, households = _raw_frames()
    trips.loc[0, "GEWICHT_W_ZENSUS"] = -9.0
    obs, log = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,))
    assert obs.empty and log["n_excluded_weight_negative"] == 1
    trips, persons, households = _raw_frames()
    trips.loc[0, ["GIS_LAENGE", "GIS_LAENGE_GUELTIG"]] = 450.0
    obs, log = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,))
    assert obs.empty and log["n_excluded_over_cap"] == 1


def test_select_person_observations_reports_household_vs_start_ags_agreement():
    trips, persons, households = _raw_frames()
    _, log = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,))
    assert log["share_start_ags_equals_household_ags"] == pytest.approx(1.0)
