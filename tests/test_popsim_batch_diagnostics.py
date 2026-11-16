"""Tests for braunschweig.analysis.popsim_batch_diagnostics (batch folder + incidence)."""
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).parent))

from popsim_batch_fixture import TOTAL, build_work_dir  # noqa: E402

from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import (  # noqa: E402
    control_totals, household_incidence, list_batch_folders, load_batch_folder)
from braunschweig.analysis.popsim_batch_diagnostics.coverage import (  # noqa: E402
    coverage_table, universe_table)
from braunschweig.popsim.control_groups import unmatched_controls  # noqa: E402


@pytest.fixture
def work_dir(tmp_path):
    return build_work_dir(tmp_path)


def test_household_incidence_counts_persons_per_household(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    incidence = household_incidence(batch)
    assert incidence.index.tolist() == [1, 2, 3, 4]
    assert incidence["M_AGE_18_plus_ZENSUS100m_target"].tolist() == [1, 0, 0, 1]
    assert incidence["M_AGE_0_17_ZENSUS100m_target"].tolist() == [0, 1, 0, 0]
    assert incidence["F_AGE_18_plus_ZENSUS100m_target"].tolist() == [0, 1, 0, 1]
    assert incidence["building_type_mfh_ZENSUS100m_target"].tolist() == [1, 0, 0, 0]
    assert incidence[TOTAL].tolist() == [1, 1, 1, 1]
    assert batch.total_hh_control == TOTAL and batch.has_store
    totals = control_totals(batch, "ZENSUS100m")
    assert totals.loc["B", "M_AGE_18_plus_ZENSUS100m_target"] == 2


def test_incomplete_batch_is_refused(work_dir):
    (work_dir / "batch_000" / "output" / "final_expanded_household_ids.csv").unlink()
    with pytest.raises(ValueError, match="batch_000"):
        load_batch_folder(work_dir / "batch_000")


def test_missing_input_file_is_named(work_dir):
    (work_dir / "batch_000" / "data" / "seed_persons.csv").unlink()
    with pytest.raises(FileNotFoundError, match="seed_persons.csv"):
        load_batch_folder(work_dir / "batch_000")


def test_list_batch_folders_sorted_and_requested_name_must_exist(work_dir):
    assert [p.name for p in list_batch_folders(work_dir)] == ["batch_000"]
    assert [p.name for p in list_batch_folders(work_dir, ["batch_000"])] == ["batch_000"]
    with pytest.raises(FileNotFoundError, match="batch_007"):
        list_batch_folders(work_dir, ["batch_007"])


def test_orphan_person_household_is_refused(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    persons = batch.seed_persons.copy()
    persons.loc[0, batch.household_id_col] = 99
    with pytest.raises(ValueError, match="99"):
        household_incidence(replace(batch, seed_persons=persons))


def test_seed_fingerprint_and_no_store(tmp_path):
    batch = load_batch_folder(build_work_dir(tmp_path, with_store=False) / "batch_000")
    assert not batch.has_store
    assert len(batch.seed_fingerprint) == 64
    assert batch.geographies == ("STAAT", "ZENSUS1km", "ZENSUS100m")


def test_duplicate_seed_household_ids_are_refused(work_dir):
    batch_dir = work_dir / "batch_000"
    path = batch_dir / "data" / "seed_households.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines + [lines[1]]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="batch_000.*duplicate"):
        household_incidence(load_batch_folder(batch_dir))


@pytest.mark.parametrize("bad_id", ["", "abc"])
def test_malformed_person_household_id_is_refused(work_dir, bad_id):
    path = work_dir / "batch_000" / "data" / "seed_persons.csv"
    path.write_text(path.read_text(encoding="utf-8").replace("1.0,1,30", f"{bad_id},1,30"),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="batch_000"):
        load_batch_folder(work_dir / "batch_000")


def test_coverage_finds_records_no_category_counts(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    usage = pd.Series({2: 1, 3: 1, 4: 3})
    table = coverage_table(batch, usage).set_index("group")
    assert table.loc["age_sex", ["n_universe", "n_uncovered", "n_multi_hit"]].tolist() == [6, 1, 0]
    assert table.loc["age_sex", "seed_weight_share"] == pytest.approx(2.0 / 13.5)
    assert table.loc["age_sex", "synthetic_share"] == pytest.approx(1 / 9)
    assert table.loc["age_sex", "ratio"] == pytest.approx(0.75)
    assert table.loc["tenure", ["n_uncovered", "ratio"]].tolist() == pytest.approx([1, 0.8])
    assert table.loc["building_type", ["n_uncovered", "ratio"]].tolist() == pytest.approx([1, 1.2])
    assert "household_size" not in table.index          # no such control in this batch


def test_universe_compares_group_targets_with_household_total(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    table = universe_table(batch).set_index("group")
    assert table.loc["tenure", ["target_sum", "household_total", "ratio"]].tolist() == [5, 5, 1.0]
    assert table.loc["building_type", "ratio"] == pytest.approx(7 / 5)


def test_control_outside_every_group_is_reported(tmp_path):
    work = build_work_dir(tmp_path, extra_controls=[dict(
        target="odd_thing_ZENSUS100m_target", seed_table="households", importance=1,
        expression="(households.H_GR == 1)")])
    batch = load_batch_folder(work / "batch_000")
    assert unmatched_controls(batch.control_spec, batch.total_hh_control) == ["odd_thing_ZENSUS100m_target"]


def test_every_production_control_belongs_to_exactly_one_group():
    from braunschweig.popsim import kreis_attribute_control
    from braunschweig.popsim.stage import build_controls_df
    spec = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0", "tier1", "tier2", "tier3"),
        employment_grid=True, ownership_grid=True,
        kreis_control_names=tuple(c.name for c in kreis_attribute_control.REGISTRY))
    total = spec.loc[spec["target"].str.contains("Insgesamt_Haushalte"), "target"].iloc[0]
    assert unmatched_controls(spec, total) == []
