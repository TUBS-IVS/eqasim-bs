"""Guard tests: the PopulationSim batch run must produce EVERY batch.

A single missing batch (e.g. an OOM-killed worker) silently removes whole
100 m-cell regions from the synthetic population -- scientifically unusable.
Decision 2026-07-10: no tolerated miss rate; ``_run_batches_and_merge`` raises
on ANY missing batch output (previously a 10% miss rate was tolerated).

Uses tiny synthetic batch folders (tmp_path CSVs) and a stub ``run_one`` --
no real PopulationSim subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from braunschweig.popsim import batch, mid


def _write_batch_output(folder: Path, cells, h_ids) -> None:
    output_dir = folder / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ZENSUS100m": list(cells), "H_ID": list(h_ids)}).to_csv(
        output_dir / "final_expanded_household_ids.csv", index=False
    )


def _stub_run_one(folder: str) -> batch.BatchResult:
    """Pretend the PopulationSim subprocess ran; outputs are pre-seeded on disk."""
    return batch.BatchResult(str(folder), "succeeded", "stub", 0.0)


def test_single_missing_batch_raises_even_below_old_ten_percent_rate(tmp_path):
    # 11 batches, 1 missing = 9.1% -- BELOW the old 10% tolerance. The strict
    # guard must raise anyway: one missing batch = missing region = unusable.
    folders = []
    for i in range(11):
        folder = tmp_path / f"batch_{i:03d}"
        if i != 7:  # batch_007 produced no output (e.g. OOM-killed)
            _write_batch_output(folder, [f"100mE{i}N1"], [1])
        else:
            folder.mkdir()
        folders.append(str(folder))

    with pytest.raises(ValueError) as excinfo:
        mid._run_batches_and_merge(folders, _stub_run_one, num_workers=2)

    # The failure must name the miss count so the operator sees the scope.
    assert "1/11" in str(excinfo.value)


def test_all_batches_present_passes(tmp_path):
    folders = []
    for i in range(3):
        folder = tmp_path / f"batch_{i:03d}"
        _write_batch_output(folder, [f"100mE{i}N1"], [1])
        folders.append(str(folder))

    report = mid._run_batches_and_merge(folders, _stub_run_one, num_workers=2)

    assert report.n_missing == 0
    assert report.n_rows == 3
