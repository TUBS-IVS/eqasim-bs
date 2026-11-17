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


_PARENT_COUNTER = iter(range(10**6))


def _outcome(geography, outcome, rounded="", label=None):
    # one outcome per parent zone: a fresh trace_label unless the test asks for a repeat
    label = label if label is not None else f"{geography}_{next(_PARENT_COUNTER)}"
    return f"outcome,simul,{geography},{label},,,,,,{outcome},{rounded},off,1"


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
        _outcome("ZENSUS100m", "single_failed_smart_round", label="seed_level_x"),
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


def test_duplicate_outcome_key_raises(tmp_path):
    # a repeated PopulationSim run appends the same parent again
    _batch(tmp_path, "batch_001", [_outcome("KREIS", "simultaneous", label="p1")])
    _batch(tmp_path, "batch_002", [_outcome("KREIS", "simultaneous", label="p1"),
                                   _outcome("KREIS", "simultaneous", label="p1")])
    with pytest.raises(RuntimeError, match="batch_002.*more than once"):
        solver_path_gate.read_records(tmp_path)
    # same label in another geography, or in another batch, is a different parent
    other = tmp_path / "o"
    _batch(other, "batch_001", [_outcome("KREIS", "simultaneous", label="p1"),
                                _outcome("ZENSUS1km", "simultaneous", label="p1")])
    _batch(other, "batch_002", [_outcome("KREIS", "simultaneous", label="p1")])
    assert len(solver_path_gate.read_records(other)) == 3


def test_not_solved_warns_as_a_rate_and_single_failures_as_labelled_count(tmp_path, caplog):
    _two_batches(tmp_path)  # 3 call rows, 2 NOT_SOLVED; 1 single failure
    with caplog.at_level(logging.INFO):
        _, totals = solver_path_gate.check_solver_paths(tmp_path, None)
    assert totals["call_rows"] == 3
    assert totals["single_failed_trace_labels"] == ["batch_002/seed_level_x"]
    lines = [r for r in caplog.records if "NOT_SOLVED" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.WARNING
    assert "2/3 call rows (66.7%)" in lines[0].getMessage()
    # No rate for the single failures: the single call rows include the backstopped
    # sub-zone calls of the simultaneous fallback, so a denominator would understate them.
    assert "batch_002/seed_level_x" in lines[0].getMessage()
    assert "single call rows" not in lines[0].getMessage()


def test_clean_call_rows_stay_at_info(tmp_path, caplog):
    _batch(tmp_path, "batch_001", [_outcome("KREIS", "simultaneous"), _call("OPTIMAL")])
    with caplog.at_level(logging.INFO):
        solver_path_gate.check_solver_paths(tmp_path, None)
    lines = [r for r in caplog.records if "NOT_SOLVED" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert "0/1 call rows" in lines[0].getMessage()


# A real record of one batch of a ladder run with the fork (arm 1, draw "gumbel"): the
# hand-written records above cannot show a drift of the fork's file format.
REAL_RECORD = Path(__file__).parent / "fixtures" / "popsim" / "integerizer_paths_real.csv"


def test_real_fork_record_is_accepted_with_the_expected_counts(tmp_path):
    folder = _batch(tmp_path, "batch_000", [], record=False)
    (folder / "output" / "integerizer_paths.csv").write_bytes(REAL_RECORD.read_bytes())
    records = solver_path_gate.read_records(tmp_path)
    assert records["row_type"].value_counts().to_dict() == {"call": 504, "outcome": 31}
    summary, totals = solver_path_gate.check_solver_paths(tmp_path, None)
    by_geography = summary.set_index("geography")
    assert by_geography["parents"].to_dict() == {"KREIS": 1, "ZENSUS100m": 26, "ZENSUS1km": 4}
    assert by_geography["non_simultaneous"].to_dict() == {
        "KREIS": 0, "ZENSUS100m": 9, "ZENSUS1km": 0}
    assert totals["not_solved_calls"] == 0 and totals["single_failed_smart_round"] == 0
    assert totals["call_rows"] == 504 and totals["single_failed_trace_labels"] == []


# --- controlled rounding (fork release 0.10.0+bs.2, issue #646) ---------------------------------

CONTROLLED_COLUMNS = ("rounding,x_count,fractional_x,slack_cell,slack_parent_sub,slack_parent,"
                      "slack_conserve,hh_unreachable,hh_excess")


def _simul_call(status, rounding, x_count="", fractional_x="", slack_conserve="",
                hh_unreachable="", hh_excess="", label="p", slack_cell=0, slack_parent_sub=0,
                slack_parent=0):
    # A bs.2 record in controlled mode: the bs.1 columns, then the nine extra ones.
    return (f"call,simul,KREIS,{label},{status},2,10,0.1,0.2,,,off,1,"
            f"{rounding},{x_count},{fractional_x},{slack_cell},{slack_parent_sub},{slack_parent},"
            f"{slack_conserve},{hh_unreachable},{hh_excess}")


def _controlled_batch(work_dir, name, lines):
    folder = work_dir / name
    out = folder / "output"
    out.mkdir(parents=True)
    (folder / batch.COMPLETION_MARKER).write_text("x", encoding="utf-8")
    header = f"{HEADER},{CONTROLLED_COLUMNS}"
    outcome = _outcome("KREIS", "simultaneous") + "," * 9
    (out / "integerizer_paths.csv").write_text(
        "\n".join([header, outcome, *lines]) + "\n", encoding="utf-8")


@pytest.mark.parametrize("rounding", ["", "round"])
def test_controlled_expected_raises_when_a_simul_call_is_not_controlled(tmp_path, rounding):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("OPTIMAL", "controlled", 10, 2, 0, 0, 0, label="a"),
        _simul_call("OPTIMAL", rounding, label="b"),
    ])
    with pytest.raises(RuntimeError, match="rounding"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="controlled")


def _good_controlled_batch(work_dir, name="batch_001"):
    _controlled_batch(work_dir, name, [_simul_call("OPTIMAL", "controlled", 10, 2, 0, 0, 0)])


def _bs1_batch(work_dir, name):
    _batch(work_dir, name, [_outcome("KREIS", "simultaneous"),
                            "call,simul,KREIS,p,OPTIMAL,2,10,0.1,0.2,,,off,1"])


def test_controlled_proof_is_per_batch_a_bs1_batch_among_controlled_ones_raises(tmp_path):
    _good_controlled_batch(tmp_path)
    _bs1_batch(tmp_path, "batch_002")  # lacks the nine extra columns
    with pytest.raises(RuntimeError, match="batch_002"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="controlled")


def test_controlled_proof_is_per_batch_a_non_controlled_simul_call_raises(tmp_path):
    _good_controlled_batch(tmp_path)
    _controlled_batch(tmp_path, "batch_002", [
        _simul_call("OPTIMAL", "controlled", 10, 2, 0, 0, 0, label="a"),
        _simul_call("OPTIMAL", "round", label="b")])
    with pytest.raises(RuntimeError, match="batch_002"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="controlled")


def test_controlled_proof_is_per_batch_parent_outcomes_without_a_controlled_call_raise(tmp_path):
    _good_controlled_batch(tmp_path)
    # batch_002 has a parent outcome (written by _controlled_batch) but only a single call row
    _controlled_batch(tmp_path, "batch_002", [_call("OPTIMAL")])
    with pytest.raises(RuntimeError, match="batch_002"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="controlled")


def test_round_expected_refuses_a_record_that_ran_controlled(tmp_path):
    # The tracked config says "round" but the tool ran controlled: a mode mismatch.
    _bs1_batch(tmp_path, "batch_001")
    _controlled_batch(tmp_path, "batch_002", [_simul_call("OPTIMAL", "controlled", 10, 2, 0, 0, 0)])
    with pytest.raises(RuntimeError, match="batch_002"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="round")


def test_round_expected_accepts_a_bs2_record_that_ran_round(tmp_path):
    _controlled_batch(tmp_path, "batch_001", [_simul_call("OPTIMAL", "round", label="a")])
    _, totals = solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="round")
    assert totals["controlled_simul_calls"] == 0


@pytest.mark.parametrize("count", ["", "n/a"])
def test_controlled_call_with_a_missing_or_non_numeric_count_is_a_damaged_record(tmp_path, count):
    _controlled_batch(tmp_path, "batch_001", [_simul_call("OPTIMAL", "controlled", count, 0, 0, 0, 0)])
    with pytest.raises(RuntimeError, match="damaged"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="controlled")


def test_controlled_call_with_a_non_numeric_slack_is_a_damaged_record(tmp_path):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("OPTIMAL", "controlled", 10, 0, 0, 0, 0, slack_parent="oops")])
    with pytest.raises(RuntimeError, match="damaged"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="controlled")


def test_controlled_expected_raises_for_a_record_without_the_extra_columns(tmp_path):
    # bs.1 shape: the build is too old, or the setting never arrived.
    _batch(tmp_path, "batch_001", [_outcome("KREIS", "simultaneous"),
                                   "call,simul,KREIS,p,OPTIMAL,2,10,0.1,0.2,,,off,1"])
    with pytest.raises(RuntimeError, match="rounding"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="controlled")


def test_round_expected_accepts_a_bs1_record_unchanged(tmp_path):
    _two_batches(tmp_path)
    explicit = solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="round")
    default = solver_path_gate.check_solver_paths(tmp_path, None)
    assert explicit[0].equals(default[0])
    assert explicit[1]["controlled_simul_calls"] == 0
    assert explicit[1]["not_solved_calls"] == 2


def test_controlled_totals_and_rate_line(tmp_path, caplog):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("OPTIMAL", "controlled", 10, 2, 1, 0, 2, label="a",
                    slack_cell=1, slack_parent_sub=2, slack_parent=3),
        _simul_call("FEASIBLE", "controlled", 30, 3, 3, 0, 0, label="b",
                    slack_cell=10, slack_parent_sub=20, slack_parent=30),
        _call("OPTIMAL"),  # a single call is no controlled call and needs no rounding value
    ])
    with caplog.at_level(logging.INFO):
        _, totals = solver_path_gate.check_solver_paths(
            tmp_path, None, expected_rounding="controlled")
    assert totals["controlled_simul_calls"] == 2
    assert totals["controlled_non_success_calls"] == 0
    assert totals["x_count"] == 40 and totals["fractional_x"] == 5
    assert totals["fractional_share"] == pytest.approx(5 / 40)
    assert totals["conserve_slack_share"] == pytest.approx(4 / 40)
    assert totals["hh_unreachable"] == 0 and totals["hh_excess"] == 2
    assert totals["slack_cell"] == 11
    assert totals["slack_parent_sub"] == 22
    assert totals["slack_parent"] == 33
    # A clean run logs every stage line at INFO (the degraded case is the WARNING test below).
    stage_lines = [r for r in caplog.records if r.getMessage().startswith(f"[{solver_path_gate.STAGE_TAG}]")]
    assert stage_lines and all(r.levelno == logging.INFO for r in stage_lines)


@pytest.mark.parametrize("bad_call", [
    _simul_call("INFEASIBLE", "controlled", 10, 0, 0, 0, 0, label="x"),
    _simul_call("OPTIMAL", "controlled", 10, 0, 0, 4, 0, label="x"),
], ids=["non-success-status", "hh-unreachable"])
def test_controlled_degraded_call_is_a_warning(tmp_path, caplog, bad_call):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("OPTIMAL", "controlled", 10, 2, 0, 0, 0, label="a"), bad_call])
    with caplog.at_level(logging.INFO):
        _, totals = solver_path_gate.check_solver_paths(
            tmp_path, None, expected_rounding="controlled")
    lines = [r for r in caplog.records if "controlled" in r.getMessage()
             and r.name == solver_path_gate.logger.name]
    assert len(lines) == 1 and lines[0].levelno == logging.WARNING
    assert totals["controlled_non_success_calls"] + totals["hh_unreachable"] >= 1


def test_unknown_expected_rounding_is_rejected(tmp_path):
    _two_batches(tmp_path)
    with pytest.raises(ValueError, match="expected_rounding"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="banana")
