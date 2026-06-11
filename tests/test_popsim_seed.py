"""Tests for the PopulationSim seed provider (braunschweig.popsim.seed).

These tests build tiny SYNTHETIC seed CSVs / DataFrames only. They never read
any real (restricted) MiD microdata; all column names and values here are
invented for the purpose of exercising the provider logic.
"""

import pandas as pd
import pytest

from braunschweig.popsim.seed import (
    MID_SEED_COLUMNS,
    SeedColumns,
    CompletenessReport,
    load_seed,
    filter_complete_households,
    select_seed_columns,
    build_seed,
)


# A compact synthetic column mapping reused across most tests. It mirrors the
# MiD names so that MID_SEED_COLUMNS can be exercised directly where useful.
SYNTHETIC_COLUMNS = SeedColumns(
    household_id="H_ID",
    household_weight="H_GEW",
    person_household_id="HP_ID",
    person_id="P_ID",
    person_weight="P_GEW",
    age="HP_ALTER",
    sex="HP_SEX",
    day_filter_col="kernwo",
    day_filter_values=(1,),
)


def _make_households(ids_weights):
    """Build a synthetic household frame from (household_id, weight) tuples."""
    return pd.DataFrame(
        {
            "H_ID": [i for i, _ in ids_weights],
            "H_GEW": [w for _, w in ids_weights],
        }
    )


def _make_persons(rows):
    """Build a synthetic person frame.

    Each row is (person_id, household_id, weight, age, sex, kernwo).
    """
    return pd.DataFrame(
        {
            "P_ID": [r[0] for r in rows],
            "HP_ID": [r[1] for r in rows],
            "P_GEW": [r[2] for r in rows],
            "HP_ALTER": [r[3] for r in rows],
            "HP_SEX": [r[4] for r in rows],
            "kernwo": [r[5] for r in rows],
        }
    )


def test_mid_seed_columns_constant():
    """The module ships the canonical MiD 2023 column mapping."""
    assert MID_SEED_COLUMNS.household_id == "H_ID"
    assert MID_SEED_COLUMNS.household_weight == "H_GEW"
    # Persons link to their household by H_ID (not HP_ID); using HP_ID would make
    # the complete-household grouping treat each person as its own household.
    assert MID_SEED_COLUMNS.person_household_id == "H_ID"
    assert MID_SEED_COLUMNS.person_id == "P_ID"
    assert MID_SEED_COLUMNS.person_weight == "P_GEW"
    assert MID_SEED_COLUMNS.age == "HP_ALTER"
    assert MID_SEED_COLUMNS.sex == "HP_SEX"
    assert MID_SEED_COLUMNS.day_filter_col == "kernwo"
    assert MID_SEED_COLUMNS.day_filter_values == (1, 2, 3)


def test_seed_columns_is_frozen():
    """SeedColumns is an immutable (frozen) dataclass."""
    columns = SeedColumns(
        household_id="H_ID",
        household_weight="H_GEW",
        person_household_id="HP_ID",
        person_id="P_ID",
        person_weight="P_GEW",
        age="HP_ALTER",
        sex="HP_SEX",
    )
    with pytest.raises(Exception):
        columns.household_id = "other"  # type: ignore[misc]


def test_filter_complete_households_keeps_only_households_that_lost_no_persons():
    """A household survives iff it loses ZERO persons to the day filter.

    HH 1 has two persons both inside the kernwo filter (value 1) -> complete.
    HH 2 has two persons, one outside the filter (value 0) -> incomplete -> dropped.
    """
    df_households = _make_households([(1, 10.0), (2, 20.0)])
    df_persons = _make_persons(
        [
            (101, 1, 1.0, 40, 1, 1),
            (102, 1, 1.0, 38, 2, 1),
            (201, 2, 1.0, 50, 1, 1),
            (202, 2, 1.0, 12, 2, 0),  # outside the filter -> HH 2 incomplete
        ]
    )

    df_hh, df_p, report = filter_complete_households(
        df_households,
        df_persons,
        SYNTHETIC_COLUMNS,
        day_filter_values=(1,),
    )

    assert sorted(df_hh["H_ID"].tolist()) == [1]
    assert sorted(df_p["P_ID"].tolist()) == [101, 102]
    assert report.n_households_in == 2
    assert report.n_households_complete == 1
    assert report.n_households_dropped == 1
    assert report.completeness_rate == pytest.approx(0.5)
    assert report.n_persons_in == 4
    assert report.n_persons_kept == 2


def test_filter_complete_households_completeness_rate():
    """completeness_rate = complete / in (3 of 4 complete -> 0.75)."""
    df_households = _make_households([(1, 1.0), (2, 1.0), (3, 1.0), (4, 1.0)])
    df_persons = _make_persons(
        [
            (1, 1, 1.0, 30, 1, 1),
            (2, 2, 1.0, 30, 1, 1),
            (3, 3, 1.0, 30, 1, 1),
            (4, 4, 1.0, 30, 1, 1),
            (5, 4, 1.0, 30, 1, 0),  # only HH 4 loses a person
        ]
    )

    _, _, report = filter_complete_households(
        df_households, df_persons, SYNTHETIC_COLUMNS, day_filter_values=(1,)
    )
    assert report.n_households_complete == 3
    assert report.completeness_rate == pytest.approx(0.75)


def test_filter_complete_households_day_filter_none_keeps_everything():
    """With no day filter, no person is dropped -> every household is complete."""
    df_households = _make_households([(1, 1.0), (2, 1.0)])
    df_persons = _make_persons(
        [
            (1, 1, 1.0, 30, 1, 0),
            (2, 2, 1.0, 30, 1, 0),  # kernwo=0 but no filter applied
        ]
    )

    df_hh, df_p, report = filter_complete_households(
        df_households, df_persons, SYNTHETIC_COLUMNS, day_filter_values=None
    )
    assert sorted(df_hh["H_ID"].tolist()) == [1, 2]
    assert len(df_p) == 2
    assert report.n_households_complete == 2
    assert report.completeness_rate == pytest.approx(1.0)


def test_select_seed_columns_adds_staat_and_keeps_essentials_and_extras():
    """select_seed_columns keeps essentials + extras and adds STAAT=1 on both."""
    df_households = pd.DataFrame(
        {"H_ID": [1, 2], "H_GEW": [1.0, 2.0], "hh_extra": ["a", "b"], "junk": [9, 9]}
    )
    df_persons = pd.DataFrame(
        {
            "P_ID": [10, 11],
            "HP_ID": [1, 2],
            "P_GEW": [1.0, 1.0],
            "HP_ALTER": [30, 40],
            "HP_SEX": [1, 2],
            "p_extra": ["x", "y"],
            "junk": [9, 9],
        }
    )

    df_hh, df_p = select_seed_columns(
        df_households,
        df_persons,
        SYNTHETIC_COLUMNS,
        extra_household_cols=("hh_extra",),
        extra_person_cols=("p_extra",),
    )

    assert (df_hh["STAAT"] == 1).all()
    assert (df_p["STAAT"] == 1).all()
    assert set(df_hh.columns) == {"H_ID", "H_GEW", "hh_extra", "STAAT"}
    assert set(df_p.columns) == {
        "P_ID",
        "HP_ID",
        "P_GEW",
        "HP_ALTER",
        "HP_SEX",
        "p_extra",
        "STAAT",
    }
    assert "junk" not in df_hh.columns
    assert "junk" not in df_p.columns


def test_select_seed_columns_missing_essential_raises():
    """A missing essential column fails fast with a clear ValueError."""
    df_households = pd.DataFrame({"H_ID": [1]})  # missing H_GEW
    df_persons = pd.DataFrame(
        {
            "P_ID": [10],
            "HP_ID": [1],
            "P_GEW": [1.0],
            "HP_ALTER": [30],
            "HP_SEX": [1],
        }
    )
    with pytest.raises(ValueError, match="H_GEW"):
        select_seed_columns(df_households, df_persons, SYNTHETIC_COLUMNS)


def test_select_seed_columns_missing_extra_raises():
    """A referenced extra column that does not exist also fails fast."""
    df_households = pd.DataFrame({"H_ID": [1], "H_GEW": [1.0]})
    df_persons = pd.DataFrame(
        {
            "P_ID": [10],
            "HP_ID": [1],
            "P_GEW": [1.0],
            "HP_ALTER": [30],
            "HP_SEX": [1],
        }
    )
    with pytest.raises(ValueError, match="missing_col"):
        select_seed_columns(
            df_households,
            df_persons,
            SYNTHETIC_COLUMNS,
            extra_person_cols=("missing_col",),
        )


def test_load_seed_reads_two_csvs(tmp_path):
    """load_seed reads the household and person CSVs written by the test."""
    hh_path = tmp_path / "households.csv"
    p_path = tmp_path / "persons.csv"
    _make_households([(1, 1.0)]).to_csv(hh_path, index=False)
    _make_persons([(10, 1, 1.0, 30, 1, 1)]).to_csv(p_path, index=False)

    df_hh, df_p = load_seed(hh_path, p_path, SYNTHETIC_COLUMNS)
    assert df_hh["H_ID"].tolist() == [1]
    assert df_p["P_ID"].tolist() == [10]


def test_build_seed_end_to_end(tmp_path):
    """build_seed orchestrates load -> filter -> select on synthetic CSVs."""
    hh_path = tmp_path / "households.csv"
    p_path = tmp_path / "persons.csv"

    _make_households([(1, 10.0), (2, 20.0)]).to_csv(hh_path, index=False)
    _make_persons(
        [
            (101, 1, 1.0, 40, 1, 1),
            (102, 1, 1.0, 38, 2, 1),
            (201, 2, 1.0, 50, 1, 1),
            (202, 2, 1.0, 12, 2, 0),  # HH 2 loses a person -> dropped
        ]
    ).to_csv(p_path, index=False)

    df_hh, df_p, report = build_seed(
        hh_path,
        p_path,
        SYNTHETIC_COLUMNS,
        day_filter_values=(1,),
    )

    assert df_hh["H_ID"].tolist() == [1]
    assert sorted(df_p["P_ID"].tolist()) == [101, 102]
    assert (df_hh["STAAT"] == 1).all()
    assert (df_p["STAAT"] == 1).all()
    assert set(df_hh.columns) == {"H_ID", "H_GEW", "STAAT"}
    assert report.n_households_complete == 1
    assert report.completeness_rate == pytest.approx(0.5)


def test_build_seed_uses_columns_default_day_filter_values(tmp_path):
    """When day_filter_values is None, build_seed falls back to the value set
    declared on the SeedColumns mapping (here (1,))."""
    hh_path = tmp_path / "households.csv"
    p_path = tmp_path / "persons.csv"

    _make_households([(1, 10.0), (2, 20.0)]).to_csv(hh_path, index=False)
    _make_persons(
        [
            (101, 1, 1.0, 40, 1, 1),
            (201, 2, 1.0, 50, 1, 0),  # outside default filter -> HH 2 dropped
        ]
    ).to_csv(p_path, index=False)

    df_hh, _, report = build_seed(hh_path, p_path, SYNTHETIC_COLUMNS)
    assert df_hh["H_ID"].tolist() == [1]
    assert report.n_households_complete == 1


def test_load_mid_seed_day_filter_disable_able(tmp_path):
    """day_filter_values=() must DISABLE the filter (keep all households),
    not silently fall back to the weekday default (1, 2, 3)."""
    from braunschweig.popsim import mid

    (tmp_path / "MiD2023_Haushalte.csv").write_text(
        "H_ID,H_GEW,RegioStaR7\n1,1.0,72\n2,1.0,72\n", encoding="utf-8"
    )
    # The person of household 2 reported on a weekend day (kernwo=4): the default
    # weekday filter (1, 2, 3) would drop household 2; with the filter disabled
    # both households must be kept.
    (tmp_path / "MiD2023_Personen.csv").write_text(
        "H_ID,P_ID,P_GEW,HP_ALTER,HP_SEX,kernwo\n1,1,1.0,40,1,2\n2,1,1.0,30,2,4\n",
        encoding="utf-8",
    )

    df_hh, df_p, report = mid.load_mid_seed(tmp_path, day_filter_values=())
    assert set(df_hh[MID_SEED_COLUMNS.household_id]) == {1, 2}
    assert report.n_households_complete == 2
