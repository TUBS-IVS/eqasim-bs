"""Tests for braunschweig.popsim.merge.

These tests cover the cell-disjoint batch merge of PopulationSim outputs. They
use tiny synthetic DataFrames and ``tmp_path`` CSVs only -- no real run data.

Core invariant under test: every 100 m cell (``ZENSUS100m``) belongs to exactly
one batch, so household ids (``H_ID``) stay globally unique as the pair
(ZENSUS100m, H_ID) WITHOUT renumbering H_ID across batches. The merge must
preserve this and never renumber H_ID.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from braunschweig.popsim import merge


def _frame(cells, h_ids):
    """Tiny synthetic batch output: one row per (cell, household)."""
    return pd.DataFrame({"ZENSUS100m": list(cells), "H_ID": list(h_ids)})


def _write_batch_output(folder: Path, frame: pd.DataFrame, *, name="final_expanded_household_ids.csv"):
    """Write ``frame`` to ``folder/output/<name>`` (creating the output dir)."""
    output_dir = folder / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_dir / name, index=False)


# --------------------------------------------------------------------------- #
# verify_unique_cells
# --------------------------------------------------------------------------- #
def test_verify_unique_cells_passes_on_disjoint_frames():
    frame_a = _frame(["100mE1N1", "100mE1N2"], [1, 1])
    frame_b = _frame(["100mE2N1", "100mE2N2"], [1, 2])

    # Disjoint cells across batches: must not raise.
    assert merge.verify_unique_cells([frame_a, frame_b]) is None


def test_verify_unique_cells_raises_and_names_overlapping_cell():
    frame_a = _frame(["100mE1N1", "100mSHARED"], [1, 1])
    frame_b = _frame(["100mSHARED", "100mE2N2"], [1, 2])

    with pytest.raises(ValueError) as excinfo:
        merge.verify_unique_cells([frame_a, frame_b])

    # The offending cell id must be named so the partitioning bug is traceable.
    assert "100mSHARED" in str(excinfo.value)


def test_verify_unique_cells_allows_repeated_cell_within_one_frame():
    # A cell may legitimately appear many times WITHIN one batch (one row per
    # household in that cell). Only CROSS-batch duplication is an error.
    frame_a = _frame(["100mE1N1", "100mE1N1", "100mE1N1"], [1, 2, 3])
    frame_b = _frame(["100mE2N1"], [1])

    assert merge.verify_unique_cells([frame_a, frame_b]) is None


# --------------------------------------------------------------------------- #
# merge_results
# --------------------------------------------------------------------------- #
def test_merge_results_concatenates_and_labels_with_index_by_default():
    frame_a = _frame(["100mE1N1", "100mE1N2"], [1, 2])
    frame_b = _frame(["100mE2N1"], [1])

    combined = merge.merge_results([frame_a, frame_b])

    assert len(combined) == 3
    assert "source_batch" in combined.columns
    # Default labels are the frame indices.
    assert sorted(combined["source_batch"].unique().tolist()) == [0, 1]
    assert (combined.loc[combined["ZENSUS100m"] == "100mE2N1", "source_batch"] == 1).all()


def test_merge_results_uses_provided_labels():
    frame_a = _frame(["100mE1N1"], [1])
    frame_b = _frame(["100mE2N1"], [1])

    combined = merge.merge_results([frame_a, frame_b], labels=["batch_north", "batch_south"])

    assert set(combined["source_batch"].unique()) == {"batch_north", "batch_south"}


def test_merge_results_preserves_raw_h_id_and_pair_uniqueness():
    # The SAME H_ID (1 and 2) is reused across different cells/batches -- this is
    # legitimate because uniqueness is the PAIR (ZENSUS100m, H_ID), not H_ID.
    frame_a = _frame(["100mE1N1", "100mE1N1"], [1, 2])
    frame_b = _frame(["100mE2N1", "100mE2N1"], [1, 2])

    combined = merge.merge_results([frame_a, frame_b])

    # H_ID must be byte-identical to the inputs (never renumbered).
    assert sorted(combined["H_ID"].tolist()) == [1, 1, 2, 2]
    # The same H_ID legitimately repeats across batches.
    assert (combined["H_ID"] == 1).sum() == 2
    # But the pair (ZENSUS100m, H_ID) is globally unique.
    assert not combined.duplicated(subset=["ZENSUS100m", "H_ID"]).any()


def test_merge_results_raises_on_cross_batch_duplicate_cell():
    frame_a = _frame(["100mDUP"], [1])
    frame_b = _frame(["100mDUP"], [9])

    with pytest.raises(ValueError) as excinfo:
        merge.merge_results([frame_a, frame_b])
    assert "100mDUP" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# load_batch_outputs
# --------------------------------------------------------------------------- #
def test_load_batch_outputs_reads_present_and_reports_missing(tmp_path):
    folder_present = tmp_path / "batch_0"
    folder_missing = tmp_path / "batch_1"
    folder_missing.mkdir()  # exists, but has no output/ file

    _write_batch_output(folder_present, _frame(["100mE1N1", "100mE1N2"], [1, 2]))

    frames, missing = merge.load_batch_outputs([folder_present, folder_missing])

    assert len(frames) == 1
    assert len(frames[0]) == 2
    assert missing == [str(folder_missing)]


def test_load_batch_outputs_accepts_string_paths_and_custom_name(tmp_path):
    folder = tmp_path / "batch_x"
    _write_batch_output(folder, _frame(["100mA"], [1]), name="custom_out.csv")

    frames, missing = merge.load_batch_outputs(
        [str(folder)], output_name="custom_out.csv"
    )

    assert len(frames) == 1
    assert missing == []


# --------------------------------------------------------------------------- #
# merge_batch_folders (end-to-end)
# --------------------------------------------------------------------------- #
def test_merge_batch_folders_end_to_end(tmp_path):
    folder_0 = tmp_path / "batch_0"
    folder_1 = tmp_path / "batch_1"
    folder_missing = tmp_path / "batch_2"
    folder_missing.mkdir()

    _write_batch_output(folder_0, _frame(["100mE1N1", "100mE1N2"], [1, 2]))
    _write_batch_output(folder_1, _frame(["100mE2N1"], [1]))

    report = merge.merge_batch_folders([folder_0, folder_1, folder_missing])

    assert report.n_folders == 3
    assert report.n_loaded == 2
    assert report.n_missing == 1
    assert report.missing_folders == [str(folder_missing)]
    assert report.n_rows == 3
    assert report.n_cells == 3
    assert not report.combined.duplicated(subset=["ZENSUS100m", "H_ID"]).any()


def test_merge_batch_folders_raises_on_duplicate_cell_across_folders(tmp_path):
    folder_0 = tmp_path / "batch_0"
    folder_1 = tmp_path / "batch_1"

    _write_batch_output(folder_0, _frame(["100mDUP", "100mE1N2"], [1, 2]))
    _write_batch_output(folder_1, _frame(["100mDUP"], [9]))

    with pytest.raises(ValueError) as excinfo:
        merge.merge_batch_folders([folder_0, folder_1])
    assert "100mDUP" in str(excinfo.value)


def test_merge_report_is_frozen(tmp_path):
    folder_0 = tmp_path / "batch_0"
    _write_batch_output(folder_0, _frame(["100mE1N1"], [1]))

    report = merge.merge_batch_folders([folder_0])
    with pytest.raises(Exception):
        report.n_rows = 999  # frozen dataclass: attribute assignment must fail
