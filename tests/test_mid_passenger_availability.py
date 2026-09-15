from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import completed_donor, mid, stage
from braunschweig.popsim.passenger_availability import (
    PASSENGER_AVAILABILITY_RNG_OFFSET,
    attach_car_passenger_diary_evidence,
    derive_car_passenger_availability,
)


def _write_mid_attribute_files(directory, *, include_p_vauto):
    (directory / "MiD2023_Haushalte.csv").write_text(
        "H_ID,oek_status,hheink_gr1,H_ANZAUTO,H_ANZRAD,anzpedrad,H_ANZPED,"
        "RegioStaR7,hhgr_gr,H_GR,H_GEW,H_MIETE,haustyp\n"
        "1,3,4,1,2,2,0,72,2,2,1.0,1,1\n",
        encoding="utf-8",
    )
    header = (
        "H_ID,P_ID,HP_ALTER,HP_SEX,P_TAET,P_FSCHEIN,P_FKARTE,P_BKAT,"
        "alter_gr1,anzwege1,P_GEW,kernwo"
    )
    row = "1,1,40,1,1,1,3,1,5,2,1.0,1"
    if include_p_vauto:
        header += ",P_VAUTO"
        row += ",2"
    (directory / "MiD2023_Personen.csv").write_text(
        f"{header}\n{row}\n", encoding="utf-8"
    )


def test_loader_reads_p_vauto_only_when_feature_is_enabled(tmp_path):
    _write_mid_attribute_files(tmp_path, include_p_vauto=True)

    _, legacy = mid.load_mid_attributes(tmp_path)
    _, enabled = mid.load_mid_attributes(
        tmp_path, include_passenger_availability=True
    )

    assert "P_VAUTO" not in legacy.columns
    assert enabled["P_VAUTO"].tolist() == [2]


def test_enabled_loader_fails_when_p_vauto_is_absent(tmp_path):
    _write_mid_attribute_files(tmp_path, include_p_vauto=False)

    with pytest.raises(KeyError, match="P_VAUTO"):
        mid.load_mid_attributes(tmp_path, include_passenger_availability=True)


def test_completed_donor_attaches_diary_evidence_only_when_enabled(tmp_path):
    _write_mid_attribute_files(tmp_path, include_p_vauto=True)
    (tmp_path / "MiD2023_Wege.csv").write_text(
        "H_ID,P_ID,W_ID,W_ZWECK,hvm_imp,W_SZS,W_SZM,W_AZS,W_AZM,wegkm_imp,"
        "wegmin_imp1,W_RBW,W_SO1,HP_ALTER\n"
        "1,1,1,8,3,7,0,7,20,5.0,20,0,1,40\n",
        encoding="utf-8",
    )

    legacy = completed_donor.build_completed_donor(
        tmp_path,
        random_seed=1234,
        seed_day_filter=None,
        weekend_plan_match_on=False,
        diary_plan_match_on=False,
    )
    enabled = completed_donor.build_completed_donor(
        tmp_path,
        random_seed=1234,
        seed_day_filter=None,
        weekend_plan_match_on=False,
        diary_plan_match_on=False,
        passenger_availability_enabled=True,
    )

    assert "P_VAUTO" not in legacy.persons.columns
    assert "src_has_car_passenger_trip" not in legacy.persons.columns
    assert enabled.persons["P_VAUTO"].tolist() == [2]
    assert enabled.persons["src_has_car_passenger_trip"].tolist() == [True]


class _RecordingSource:
    def __init__(self):
        self.kwargs = None

    def load_donor(self, data_dir, **kwargs):
        self.kwargs = kwargs
        return pd.DataFrame({"H_ID": [1]}), pd.DataFrame({"P_ID": [1]}), pd.DataFrame()

    def map_person_attributes(self, persons, households, **kwargs):
        return persons, pd.DataFrame()


def test_noncompletion_loader_forwards_feature_flag_without_touching_off_call():
    source = _RecordingSource()
    stage._load_donor_tables(
        None, source, "mid", "unused", False, None, None,
        passenger_availability_enabled=False,
    )
    assert source.kwargs == {}

    stage._load_donor_tables(
        None, source, "mid", "unused", False, None, None,
        passenger_availability_enabled=True,
    )
    assert source.kwargs == {"include_passenger_availability": True}


def test_stage_supplies_an_independent_seeded_rng_to_passenger_mapper(monkeypatch):
    captured = {}

    def fake_build_persons(*args, **kwargs):
        captured.update(kwargs)
        return pd.DataFrame({"person_id": ["p"]}), pd.DataFrame()

    monkeypatch.setattr(stage.assembly, "build_persons", fake_build_persons)

    class Context:
        def config(self, key):
            return {
                stage.KEY_INCOME_KC: False,
                stage.KEY_INCOME_TILT: False,
            }[key]

        def set_info(self, key, value):
            pass

    primary_rng = np.random.RandomState(17)
    primary_state = primary_rng.get_state()
    stage._expand_donor_households_to_persons(
        Context(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        primary_rng, _RecordingSource(), "mid", None, False,
        passenger_availability_enabled=True,
        random_seed=1234,
    )

    assert captured["passenger_availability_enabled"] is True
    expected = np.random.RandomState(1234 + PASSENGER_AVAILABILITY_RNG_OFFSET).randint(100000)
    assert captured["passenger_rng"].randint(100000) == expected
    current_state = primary_rng.get_state()
    assert current_state[0] == primary_state[0]
    np.testing.assert_array_equal(current_state[1], primary_state[1])
    assert current_state[2:] == primary_state[2:]


def test_stage_configures_passenger_availability_default_on():
    class Context:
        def __init__(self):
            self.configured = {}

        def config(self, key, default=None):
            self.configured[key] = default
            return default

        def stage(self, name, alias=None):
            pass

    context = Context()
    stage.configure(context)

    assert context.configured[stage.KEY_MID_PASSENGER_AVAILABILITY] is True


def test_carless_adult_keeps_own_occasional_passenger_access():
    persons = pd.DataFrame(
        {
            "person_id": ["carless_adult"],
            "household_id": ["household_1"],
            "H_ID": [10],
            "P_ID": [1],
            "age": [35],
            "alter_gr1": [4],
            "P_VAUTO": [2],
            "number_of_cars": [0],
            "has_license": [False],
            "car_availability": ["none"],
        }
    ).set_index("person_id", drop=False)
    households = pd.DataFrame({"H_ID": [10], "RegioStaR7": [72]})

    result = derive_car_passenger_availability(
        persons,
        households,
        rng=np.random.RandomState(1234),
    )

    assert result.loc["carless_adult", "car_passenger_availability"] == "some"
    assert result.loc["carless_adult", "car_availability"] == "none"


def _derive(rows, *, seed=1234):
    persons = pd.DataFrame(rows).set_index("person_id", drop=False)
    households = pd.DataFrame(
        {
            "H_ID": sorted(persons["H_ID"].unique()),
            "RegioStaR7": [72] * persons["H_ID"].nunique(),
        }
    )
    return derive_car_passenger_availability(
        persons,
        households,
        rng=np.random.RandomState(seed),
    )


def _person(person_id, household_id, donor_household_id, donor_person_id, age, response, **overrides):
    row = {
        "person_id": person_id,
        "household_id": household_id,
        "H_ID": donor_household_id,
        "P_ID": donor_person_id,
        "age": age,
        "alter_gr1": age // 10,
        "P_VAUTO": response,
        "number_of_cars": 0,
        "has_license": False,
        "car_availability": "none",
        "src_has_car_passenger_trip": False,
    }
    row.update(overrides)
    return row


def test_own_response_codes_map_literally():
    result = _derive(
        [
            _person("all", "h1", 1, 1, 35, 1),
            _person("some", "h2", 2, 1, 35, 2),
            _person("none", "h3", 3, 1, 35, 3),
        ]
    )

    assert result["car_passenger_availability"].to_dict() == {
        "all": "all",
        "some": "some",
        "none": "none",
    }
    assert set(result["passenger_availability_source"]) == {"own_response"}


def test_age_13_uses_child_evidence_while_age_14_uses_own_response():
    result = _derive(
        [
            _person("adult", "h1", 1, 1, 40, 1),
            _person("age13", "h1", 1, 2, 13, 402),
            _person("age14", "h1", 1, 3, 14, 2),
        ]
    )

    assert result.loc["age13", "car_passenger_availability"] == "some"
    assert result.loc["age13", "passenger_availability_source"] == "child_household_evidence"
    assert result.loc["age14", "car_passenger_availability"] == "some"
    assert result.loc["age14", "passenger_availability_source"] == "own_response"


def test_missing_and_proxy_adult_responses_are_empirically_imputed_reproducibly():
    rows = [
        _person("valid_all", "h1", 1, 1, 35, 1),
        _person("valid_none", "h2", 2, 1, 35, 3),
        _person("missing", "h3", 3, 1, 35, np.nan),
        _person("proxy", "h4", 4, 1, 35, 206),
        _person("clone_a", "clone_h1", 5, 1, 35, 9),
        _person("clone_b", "clone_h2", 5, 1, 35, 9),
    ]

    first = _derive(rows, seed=77)
    second = _derive(rows, seed=77)

    pd.testing.assert_series_equal(
        first["car_passenger_availability"],
        second["car_passenger_availability"],
    )
    assert set(first.loc[["missing", "proxy", "clone_a"], "passenger_availability_source"]) == {
        "adult_empirical_imputation"
    }
    assert first.loc["clone_a", "car_passenger_availability"] == first.loc[
        "clone_b", "car_passenger_availability"
    ]


def test_child_with_household_car_and_licensed_adult_gets_some():
    result = _derive(
        [
            _person("adult", "h1", 1, 1, 40, 3, number_of_cars=1, has_license=True),
            _person("child", "h1", 1, 2, 8, 402, number_of_cars=1),
        ]
    )

    assert result.loc["child", "car_passenger_availability"] == "some"
    assert result.loc["child", "passenger_availability_source"] == "child_household_evidence"


def test_carless_child_inherits_positive_adult_general_access():
    result = _derive(
        [
            _person("adult", "h1", 1, 1, 40, 2),
            _person("child", "h1", 1, 2, 8, 402),
        ]
    )

    assert result.loc["child", "car_passenger_availability"] == "some"
    assert result.loc["child", "passenger_availability_source"] == "child_household_evidence"


def test_carless_child_with_reported_passenger_trip_gets_some():
    result = _derive(
        [
            _person("adult", "h1", 1, 1, 40, 3),
            _person("child", "h1", 1, 2, 8, 402, src_has_car_passenger_trip=True),
        ]
    )

    assert result.loc["child", "car_passenger_availability"] == "some"
    assert result.loc["child", "passenger_availability_source"] == "child_diary_evidence"


def test_resolved_negative_household_evidence_gives_child_none():
    result = _derive(
        [
            _person("adult", "h1", 1, 1, 40, 3),
            _person("child", "h1", 1, 2, 8, 402),
        ]
    )

    assert result.loc["child", "car_passenger_availability"] == "none"
    assert result.loc["child", "passenger_availability_source"] == "child_household_evidence"


def test_child_without_usable_household_evidence_uses_empirical_fallback():
    result = _derive(
        [
            _person("pool_adult", "adult_h", 1, 1, 40, 1),
            _person("child", "child_only_h", 2, 1, 8, 402),
        ]
    )

    assert result.loc["child", "car_passenger_availability"] == "some"
    assert result.loc["child", "passenger_availability_source"] == "child_empirical_fallback"


def test_child_fallback_excludes_imputed_adult_responses_from_empirical_pool():
    result = _derive(
        [
            _person("observed_some", "h1", 1, 1, 40, 2),
            _person("observed_none", "h2", 2, 1, 40, 3),
            _person("missing_adult", "h3", 3, 1, 40, 9),
            _person("child", "child_only_h", 4, 1, 8, 402),
        ],
        seed=1,
    )

    assert result.loc["missing_adult", "passenger_availability_source"] == "adult_empirical_imputation"
    assert result.loc["child", "car_passenger_availability"] == "none"
    assert result.loc["child", "passenger_availability_source"] == "child_empirical_fallback"


def test_no_empirical_adult_pool_fails_clearly():
    with pytest.raises(ValueError, match="valid adult P_VAUTO pool is empty"):
        _derive([_person("unknown", "h1", 1, 1, 35, 9)])


@pytest.mark.parametrize(
    "age,response",
    [(35, 77), (35, 402), (8, 77), (8, 1)],
)
def test_malformed_or_age_incompatible_codes_fail(age, response):
    with pytest.raises(ValueError, match="P_VAUTO"):
        _derive(
            [
                _person("valid_pool", "pool", 9, 1, 40, 1),
                _person("bad", "bad", 1, 1, age, response),
            ]
        )


def test_own_none_is_preserved_when_diary_reports_passenger_trip(caplog):
    caplog.set_level("INFO")
    result = _derive(
        [_person("adult", "h1", 1, 1, 35, 3, src_has_car_passenger_trip=True)]
    )

    assert result.loc["adult", "car_passenger_availability"] == "none"
    assert "own_none_with_reported_passenger=1" in caplog.text


def test_diary_evidence_follows_plan_source_and_excludes_rbw_rows():
    persons = pd.DataFrame(
        {
            "H_ID": [1, 2],
            "P_ID": [1, 1],
            "source_H_ID": [20, 10],
            "source_P_ID": [2, 1],
        }
    )
    wege = pd.DataFrame(
        {
            "H_ID": [10, 20, 20],
            "P_ID": [1, 2, 2],
            "hvm_imp": [3, 3, 4],
            "W_RBW": [0, 1, 0],
        }
    )

    result = attach_car_passenger_diary_evidence(persons, wege)

    assert result["src_has_car_passenger_trip"].tolist() == [False, True]
