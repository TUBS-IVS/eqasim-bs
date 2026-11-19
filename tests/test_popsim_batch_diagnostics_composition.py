"""Tests for the household-composition table of the PopulationSim batch diagnostics."""
import logging
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.append(str(Path(__file__).parent))

from popsim_batch_fixture import build_work_dir  # noqa: E402

from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import (  # noqa: E402
    load_batch_folder)
from braunschweig.analysis.popsim_batch_diagnostics.composition import (  # noqa: E402
    CENSUS_SOURCE, COUPLE_FAMILY_COLUMNS, FAMILY_HOUSEHOLDS_COLUMN, LONE_PARENT_COLUMNS,
    SQUARE_ID_COLUMN, household_composition)
from braunschweig.analysis.popsim_batch_diagnostics.run import (  # noqa: E402
    _read_squares, main, run_diagnostics)
from braunschweig.data.mid.status_by_hhtype import ADULT_MIN_AGE  # noqa: E402


@pytest.fixture
def batch(tmp_path):
    """Three synthetic households: adult + child, 2 adults + 3 children (ages 5, 12, 3), 2 adults."""
    loaded = load_batch_folder(build_work_dir(tmp_path) / "batch_000")
    persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 2, 2, 2, 2, 3, 3],
        "HP_ALTER": [30, 8, 40, 41, 5, 12, 3, 70, 71]})
    expanded = pd.DataFrame({"H_ID": [1, 2, 3], "ZENSUS1km": ["K1", "K1", "K2"],
                             "ZENSUS100m": ["A", "B", "C"]})
    households = pd.DataFrame({"H_ID": [1, 2, 3]})
    return replace(loaded, seed_persons=persons, expanded=expanded, seed_households=households)


def _squares():
    lone = {c: [1, 2, 7] for c in LONE_PARENT_COLUMNS[:1]} | {LONE_PARENT_COLUMNS[1]: [1, 0, 7]}
    couples = {COUPLE_FAMILY_COLUMNS[0]: [2, 3, 7], COUPLE_FAMILY_COLUMNS[1]: [0, 0, 7],
               COUPLE_FAMILY_COLUMNS[2]: [1, 1, 7]}
    return pd.DataFrame({SQUARE_ID_COLUMN: ["K1", "K2", "K3"],     # K3 is not in the batch
                         FAMILY_HOUSEHOLDS_COLUMN: [10, 20, 99], **lone, **couples})


def test_composition_matches_hand_count_with_census(batch):
    row = household_composition(batch, _squares()).iloc[0]
    assert row["households"] == 3 and row["households_with_minor"] == 2
    assert row["share_households_with_minor"] == pytest.approx(2 / 3)
    assert row["one_adult_share_among_minor_households"] == pytest.approx(0.5)
    assert row["persons_0_17_per_minor_household"] == pytest.approx(2.0)      # (1 + 3) / 2
    assert row["share_persons_0_9_with_one_adult"] == pytest.approx(1 / 3)    # ages 8 | 5, 3
    assert row["share_persons_10_14_with_one_adult"] == 0.0                   # age 12, two adults
    assert row["minor_households_without_adult"] == 0
    # squares K1, K2: family households 30; lone parents 1+1+2+0 = 4; couples 5+0+2 = 7
    assert row["census_family_households"] == 30
    assert row["census_families_with_minor"] == 11
    assert row["census_share_families_with_minor"] == pytest.approx(11 / 30)
    assert row["census_lone_parent_share_among_families_with_minor"] == pytest.approx(4 / 11)
    # fixture 100 m targets: M_AGE_0_17 1 + 0, F_AGE_0_17 0 + 0
    assert row["census_persons_0_17_per_family_with_minor"] == pytest.approx(1 / 11)
    assert row["census_source"].startswith("observed census data")


def test_without_squares_census_columns_are_absent_and_no_adult_households_are_counted(batch):
    persons = batch.seed_persons.copy()
    persons.loc[persons["H_ID"] == 3, "HP_ALTER"] = [10, 11]
    table = household_composition(replace(batch, seed_persons=persons), None)
    assert not [c for c in table.columns if c.startswith("census_")]
    row = table.iloc[0]
    assert row["minor_households_without_adult"] == 1
    assert row["households_with_minor"] == 3
    assert row["one_adult_share_among_minor_households"] == pytest.approx(1 / 3)


def test_square_missing_from_the_census_frame_raises(batch):
    with pytest.raises(ValueError, match="K2"):
        household_composition(batch, _squares().iloc[[0]])


def test_run_diagnostics_and_cli_write_the_table(tmp_path):
    work = build_work_dir(tmp_path)
    out = tmp_path / "out"
    run_diagnostics(work, out)
    assert "census_source" not in pd.read_csv(out / "household_composition.csv").columns
    report = (out / "popsim_batch_diagnostics.md").read_text(encoding="utf-8")
    assert "## Household composition" in report
    cells = tmp_path / "cells.parquet"
    pd.DataFrame({SQUARE_ID_COLUMN: ["K1"],
                  FAMILY_HOUSEHOLDS_COLUMN.replace("_Gitter", "-Gitter"): [10],
                  **{c.replace("_Gitter", "-Gitter"): [1]
                     for c in (*LONE_PARENT_COLUMNS, *COUPLE_FAMILY_COLUMNS)}}).to_parquet(cells)
    assert main(["--work-dir", str(work), "--out-dir", str(tmp_path / "out2"),
                 "--cells-1km", str(cells)]) == 0
    table = pd.read_csv(tmp_path / "out2" / "household_composition.csv")
    assert table.loc[0, "census_families_with_minor"] == 5


def test_summary_prints_the_census_source_once_and_the_csv_keeps_the_column(tmp_path):
    work = build_work_dir(tmp_path)
    cells = tmp_path / "cells.parquet"
    pd.DataFrame({SQUARE_ID_COLUMN: ["K1"],
                  **{c: [1] for c in (FAMILY_HOUSEHOLDS_COLUMN, *LONE_PARENT_COLUMNS,
                                      *COUPLE_FAMILY_COLUMNS)}}).to_parquet(cells)

    assert main(["--work-dir", str(work), "--out-dir", str(tmp_path / "out"),
                 "--cells-1km", str(cells)]) == 0

    table = pd.read_csv(tmp_path / "out" / "household_composition.csv")
    report = (tmp_path / "out" / "popsim_batch_diagnostics.md").read_text(encoding="utf-8")
    assert report.count(CENSUS_SOURCE) == 1
    assert (table["census_source"] == CENSUS_SOURCE).all()


def test_two_raw_columns_cleaning_to_the_same_name_raise(tmp_path):
    cells = tmp_path / "cells.parquet"
    pd.DataFrame({SQUARE_ID_COLUMN: ["K1"],
                  FAMILY_HOUSEHOLDS_COLUMN: [10],
                  FAMILY_HOUSEHOLDS_COLUMN.replace("_Gitter", "-Gitter"): [11],
                  **{c: [1] for c in (*LONE_PARENT_COLUMNS, *COUPLE_FAMILY_COLUMNS)}}
                 ).to_parquet(cells)

    with pytest.raises(ValueError, match="clean to the same"):
        _read_squares(cells)


def test_age_boundary_is_adult_min_age(batch):
    adult, minor = ADULT_MIN_AGE, ADULT_MIN_AGE - 1
    persons = pd.DataFrame({"H_ID": [1, 1, 2, 2, 2, 3],
                            "HP_ALTER": [adult, minor, adult, adult, minor, minor]})

    row = household_composition(replace(batch, seed_persons=persons), None).iloc[0]

    assert row["households_with_minor"] == 3
    assert row["one_adult_share_among_minor_households"] == pytest.approx(1 / 3)  # only H1
    assert row["minor_households_without_adult"] == 1                            # H3
    assert row["persons_0_17_per_minor_household"] == pytest.approx(1.0)


@pytest.mark.parametrize("bad_age", [float("nan"), -1])
def test_missing_or_negative_age_in_a_used_household_raises(batch, bad_age):
    persons = batch.seed_persons.copy()
    persons.loc[0, "HP_ALTER"] = bad_age

    with pytest.raises(ValueError, match="1 person"):
        household_composition(replace(batch, seed_persons=persons), None)


def test_census_nan_in_a_used_square_raises_but_not_in_an_unused_one(batch):
    squares = _squares().astype({FAMILY_HOUSEHOLDS_COLUMN: float})
    squares.loc[2, FAMILY_HOUSEHOLDS_COLUMN] = float("nan")             # K3 is not in the batch
    assert household_composition(batch, squares).iloc[0]["census_family_households"] == 30
    squares.loc[1, FAMILY_HOUSEHOLDS_COLUMN] = float("nan")             # K2 is
    with pytest.raises(ValueError, match="missing"):
        household_composition(batch, squares)


def test_duplicate_square_raises_when_values_differ_and_is_kept_once_when_identical(batch):
    identical = pd.concat([_squares(), _squares().iloc[[0]]], ignore_index=True)
    assert household_composition(batch, identical).iloc[0]["census_family_households"] == 30

    differing = identical.copy()
    differing.loc[3, FAMILY_HOUSEHOLDS_COLUMN] = 11
    with pytest.raises(ValueError, match="K1"):
        household_composition(batch, differing)


def test_absent_100m_targets_give_nan_and_a_warning(batch, caplog):
    (batch.path / "data" / "control_totals_ZENSUS100m.csv").unlink()

    with caplog.at_level(logging.WARNING):
        row = household_composition(batch, _squares()).iloc[0]

    assert np.isnan(row["census_persons_0_17_per_family_with_minor"])
    assert row["census_families_with_minor"] == 11
    assert any("not derived" in r.getMessage() for r in caplog.records)


def test_zero_denominator_gives_nan_and_a_warning(batch, caplog):
    persons = batch.seed_persons.copy()
    persons["HP_ALTER"] = 40                                             # nobody is a minor

    with caplog.at_level(logging.WARNING):
        row = household_composition(replace(batch, seed_persons=persons), None).iloc[0]

    assert row["households_with_minor"] == 0
    assert np.isnan(row["one_adult_share_among_minor_households"])
    assert np.isnan(row["share_persons_0_9_with_one_adult"])
    assert sum("zero denominator" in r.getMessage() for r in caplog.records) >= 2
