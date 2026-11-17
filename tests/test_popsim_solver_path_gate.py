"""Tests for the solver-path gate (``braunschweig.popsim.stage.solver_path_gate``, issue #645, E4).

The gate reads the per-batch record ``output/integerizer_paths.csv`` of the group's
PopulationSim fork and turns it into one primary-vs-fallback rate line per geography.
The record files are written by hand here.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from braunschweig.popsim import batch
from braunschweig.popsim.stage import config_keys, solver_path_gate

HEADER = ("row_type,kind,geography,trace_label,status,zones,rows,build_s,solve_s,"
          "outcome,rounded_zones,draw,random_seed")


def _outcome(geography, outcome, rounded=""):
    return f"outcome,simul,{geography},t,,,,,,{outcome},{rounded},off,1"


def _call(status, label="t"):
    return f"call,single,KREIS,{label},{status},1,10,0.1,0.2,,,off,1"


def _batch(work_dir: Path, name: str, lines, *, completed=True, record=True) -> Path:
    folder = work_dir / name
    out = folder / "output"
    out.mkdir(parents=True)
    if completed:
        (folder / batch.COMPLETION_MARKER).write_text("x", encoding="utf-8")
    if record:
        (out / "integerizer_paths.csv").write_text(
            "\n".join([HEADER, *lines]) + ("\n" if lines else ""), encoding="utf-8")
    return folder


def _two_batches(work_dir):
    _batch(work_dir, "batch_001", [
        _outcome("KREIS", "simultaneous"), _outcome("KREIS", "retry_simultaneous"),
        _outcome("ZENSUS1km", "simultaneous"), _outcome("ZENSUS1km", "smart_round_all_subzones", 3),
        _call("OPTIMAL"), _call("NOT_SOLVED"),
    ])
    _batch(work_dir, "batch_002", [
        _outcome("ZENSUS1km", "sequential_retry_failed", 2),
        _outcome("ZENSUS1km", "simultaneous"),
        _outcome("ZENSUS100m", "single_failed_smart_round"),
        _call("NOT_SOLVED", "retry_x"),
    ])
    # an incomplete batch is ignored, even without a record
    _batch(work_dir, "batch_003", [], completed=False, record=False)


def test_counts_per_geography(tmp_path):
    _two_batches(tmp_path)
    records = solver_path_gate.read_records(tmp_path)
    df = solver_path_gate.summarize_solver_paths(records).set_index("geography")
    assert df.loc["KREIS", "parents"] == 2
    assert df.loc["KREIS", "non_simultaneous"] == 0
    z = df.loc["ZENSUS1km"]
    assert z["parents"] == 4
    assert z["simultaneous"] == 2
    assert z["smart_round_all_subzones"] == 1
    assert z["sequential_retry_failed"] == 1
    assert z["non_simultaneous"] == 2
    assert z["rounded_zones"] == 5
    assert df.loc["ZENSUS100m", "single_failed_smart_round"] == 1
    assert df.loc["ZENSUS100m", "parents"] == 0
    assert "not_solved_calls" not in df.columns
    totals = solver_path_gate.summarize_call_totals(records)
    assert totals["not_solved_calls"] == 2
    assert totals["single_failed_smart_round"] == 1
    assert totals["per_batch"]["batch_001"]["not_solved_calls"] == 1
    assert totals["per_batch"]["batch_002"]["single_failed_smart_round"] == 1


def test_rate_logged_with_helper_format(tmp_path, caplog):
    _two_batches(tmp_path)
    with caplog.at_level(logging.INFO):
        solver_path_gate.check_solver_paths(tmp_path, None)
    msgs = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[popsim.solver_paths] primary 2/4 (50.0%), fallback 2 (50.0%)")
               and "ZENSUS1km" in m for m in msgs)
    assert any(m.startswith("[popsim.solver_paths] primary 2/2 (100.0%), fallback 0")
               and "KREIS" in m for m in msgs)
    assert any("NOT_SOLVED" in m and "single_failed_smart_round" in m for m in msgs)
    assert not any("do_simul_integerizing" in m for m in msgs)


def test_error_rate_raises(tmp_path):
    _two_batches(tmp_path)
    with pytest.raises(RuntimeError, match=r"ZENSUS1km 50\.0%.*40\.0%|40\.0%.*ZENSUS1km 50\.0%"):
        solver_path_gate.check_solver_paths(tmp_path, 0.4)
    solver_path_gate.check_solver_paths(tmp_path, 0.5)  # equal to the limit passes


def test_error_rate_none_only_warns(tmp_path, caplog):
    _two_batches(tmp_path)
    with caplog.at_level(logging.INFO):
        df, _ = solver_path_gate.check_solver_paths(tmp_path, None)
    assert len(df) == 3
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_missing_or_empty_record_raises(tmp_path):
    _batch(tmp_path, "batch_001", [_outcome("KREIS", "simultaneous")])
    _batch(tmp_path, "batch_002", [], record=False)
    with pytest.raises(RuntimeError, match="batch_002"):
        solver_path_gate.check_solver_paths(tmp_path, None)
    empty = tmp_path / "e"
    _batch(empty, "batch_007", [])
    with pytest.raises(RuntimeError, match="batch_007"):
        solver_path_gate.read_records(empty)


@pytest.mark.parametrize("bad", [0, 0.0, -0.1, 1.5])
def test_error_rate_validated(tmp_path, bad):
    _two_batches(tmp_path)
    with pytest.raises(ValueError):
        solver_path_gate.check_solver_paths(tmp_path, bad)
    with pytest.raises(ValueError):
        solver_path_gate.validate_error_rate(bad)
    assert solver_path_gate.validate_error_rate(None) is None
    assert solver_path_gate.validate_error_rate(1) == 1.0
    assert config_keys.KEY_SOLVER_FALLBACK_ERROR_RATE.endswith(".solver_fallback_error_rate")


def test_unknown_outcome_or_row_type_raises(tmp_path):
    _batch(tmp_path, "batch_001", [_outcome("KREIS", "simultaneous")])
    _batch(tmp_path, "batch_002", [_outcome("KREIS", "teleported")])
    with pytest.raises(RuntimeError, match="batch_002.*teleported"):
        solver_path_gate.read_records(tmp_path)
    other = tmp_path / "o"
    _batch(other, "batch_005", ["mystery,simul,KREIS,t,,,,,,simultaneous,,off,1"])
    with pytest.raises(RuntimeError, match="batch_005.*mystery"):
        solver_path_gate.read_records(other)


def test_no_outcome_rows_at_all_raises(tmp_path):
    _batch(tmp_path, "batch_001", [_call("OPTIMAL"), _call("NOT_SOLVED")])
    with pytest.raises(RuntimeError, match="no parent outcome"):
        solver_path_gate.check_solver_paths(tmp_path, None)


def test_geography_without_parents_warns(tmp_path, caplog):
    _batch(tmp_path, "batch_001", [_outcome("KREIS", "simultaneous"),
                                   _outcome("ZENSUS100m", "single_failed_smart_round")])
    with caplog.at_level(logging.INFO):
        solver_path_gate.check_solver_paths(tmp_path, 0.1)
    assert any(r.levelno == logging.WARNING and "ZENSUS100m" in r.getMessage()
               for r in caplog.records)


def test_record_missing_column_raises(tmp_path):
    folder = _batch(tmp_path, "batch_004", [])
    (folder / "output" / "integerizer_paths.csv").write_text(
        "row_type,kind,geography\noutcome,simul,KREIS\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="batch_004.*outcome"):
        solver_path_gate.read_records(tmp_path)
