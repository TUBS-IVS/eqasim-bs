"""Tests for braunschweig.analysis.popsim_batch_diagnostics (batch folder + incidence)."""
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent))

from popsim_batch_fixture import TOTAL, build_work_dir  # noqa: E402

from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import (  # noqa: E402
    control_totals, household_incidence, list_batch_folders, load_batch_folder)


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
