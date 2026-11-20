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

# The columns the fork appends in controlled mode, in the fork's order (pins the file format).
CONTROLLED_COLUMN_NAMES = (
    "rounding", "method", "solver", "attempts", "x_count", "swaps", "sweeps", "converged",
    "distance_start", "distance_final", "flow_wanted", "flow_achieved", "zone_shortfall",
    "row_shortfall", "fractional_x", "repaired_x", "fractional_after_repair", "repair_failed",
    "slack_cell", "slack_parent_sub", "slack_parent", "slack_conserve", "hh_unreachable",
    "hh_outside_lp", "hh_excess", "objective_lp", "objective_repaired")
CONTROLLED_COLUMNS = ",".join(CONTROLLED_COLUMN_NAMES)

# A column the method of a call does not produce is EMPTY in its row.
_SEARCH_DEFAULTS = dict(
    x_count=10, swaps=3, sweeps=2, converged=1, distance_start=5.0, distance_final=4.0,
    flow_wanted=100, flow_achieved=100, zone_shortfall=0, row_shortfall=0, hh_unreachable=0,
    hh_outside_lp=0, hh_excess=0)
_LP_DEFAULTS = dict(
    solver="SCIP", attempts=1, x_count=10, fractional_x=2, repaired_x=2,
    fractional_after_repair=0, repair_failed=0, slack_cell=0, slack_parent_sub=0, slack_parent=0,
    slack_conserve=0, hh_unreachable=0, hh_outside_lp=0, hh_excess=0, objective_lp=1.5,
    objective_repaired=1.6)
_METHOD_DEFAULTS = {"search": _SEARCH_DEFAULTS, "lp": _LP_DEFAULTS}


def _simul_call(status, method="search", *, rounding="controlled", label="p", **changes):
    """One simultaneous call row of a bs.2 record: the bs.1 columns, then the 27 extra ones.

    ``method`` "" (no defaults) gives a row of a call that was not controlled; ``changes`` set a
    column, "" empties it.
    """
    values = {"rounding": rounding, "method": method, **_METHOD_DEFAULTS.get(method, {}), **changes}
    extra = ",".join(str(values.get(name, "")) for name in CONTROLLED_COLUMN_NAMES)
    return f"call,simul,KREIS,{label},{status},2,10,0.1,0.2,,,off,1,{extra}"


def _controlled_batch(work_dir, name, lines):
    folder = work_dir / name
    out = folder / "output"
    out.mkdir(parents=True)
    (folder / batch.COMPLETION_MARKER).write_text("x", encoding="utf-8")
    header = f"{HEADER},{CONTROLLED_COLUMNS}"
    outcome = _outcome("KREIS", "simultaneous") + "," * len(CONTROLLED_COLUMN_NAMES)
    (out / "integerizer_paths.csv").write_text(
        "\n".join([header, outcome, *lines]) + "\n", encoding="utf-8")


def _check_controlled(work_dir):
    return solver_path_gate.check_solver_paths(work_dir, None, expected_rounding="controlled")


@pytest.mark.parametrize("rounding", ["", "round"])
def test_controlled_expected_raises_when_a_simul_call_is_not_controlled(tmp_path, rounding):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("OPTIMAL", label="a"),
        _simul_call("OPTIMAL", "", rounding=rounding, label="b"),
    ])
    with pytest.raises(RuntimeError, match="rounding"):
        _check_controlled(tmp_path)


def _good_controlled_batch(work_dir, name="batch_001"):
    _controlled_batch(work_dir, name, [_simul_call("OPTIMAL")])


def _bs1_batch(work_dir, name):
    _batch(work_dir, name, [_outcome("KREIS", "simultaneous"),
                            "call,simul,KREIS,p,OPTIMAL,2,10,0.1,0.2,,,off,1"])


def test_controlled_proof_is_per_batch_a_bs1_batch_among_controlled_ones_raises(tmp_path):
    _good_controlled_batch(tmp_path)
    _bs1_batch(tmp_path, "batch_002")  # lacks the 27 extra columns
    with pytest.raises(RuntimeError, match="batch_002"):
        _check_controlled(tmp_path)


def test_controlled_proof_is_per_batch_a_non_controlled_simul_call_raises(tmp_path):
    _good_controlled_batch(tmp_path)
    _controlled_batch(tmp_path, "batch_002", [
        _simul_call("OPTIMAL", label="a"), _simul_call("OPTIMAL", "", rounding="round", label="b")])
    with pytest.raises(RuntimeError, match="batch_002"):
        _check_controlled(tmp_path)


def test_controlled_proof_is_per_batch_parent_outcomes_without_a_controlled_call_raise(tmp_path):
    _good_controlled_batch(tmp_path)
    # batch_002 has a parent outcome (written by _controlled_batch) but only a single call row
    _controlled_batch(tmp_path, "batch_002", [_call("OPTIMAL")])
    with pytest.raises(RuntimeError, match="batch_002"):
        _check_controlled(tmp_path)


def test_round_expected_refuses_a_record_that_ran_controlled(tmp_path):
    # The tracked config says "round" but the tool ran controlled: a mode mismatch.
    _bs1_batch(tmp_path, "batch_001")
    _controlled_batch(tmp_path, "batch_002", [_simul_call("OPTIMAL")])
    with pytest.raises(RuntimeError, match="batch_002"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="round")


def test_round_expected_accepts_a_bs2_record_that_ran_round(tmp_path):
    _controlled_batch(tmp_path, "batch_001", [_simul_call("OPTIMAL", "", rounding="round")])
    _, totals = solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="round")
    assert totals["controlled_simul_calls"] == 0


def test_controlled_expected_raises_for_a_record_without_the_extra_columns(tmp_path):
    # bs.1 shape: the build is too old, or the setting never arrived.
    _bs1_batch(tmp_path, "batch_001")
    with pytest.raises(RuntimeError, match="rounding"):
        _check_controlled(tmp_path)


def test_round_expected_accepts_a_bs1_record_unchanged(tmp_path):
    _two_batches(tmp_path)
    explicit = solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="round")
    default = solver_path_gate.check_solver_paths(tmp_path, None)
    assert explicit[0].equals(default[0])
    assert explicit[1]["controlled_simul_calls"] == 0
    assert explicit[1]["not_solved_calls"] == 2


# A damaged record: a call that succeeded has an empty or non-numeric value in a column the gate
# requires of its method or a `converged` that is neither 0 nor 1, or the method of a call (of any
# status) is unknown.
@pytest.mark.parametrize("call", [
    _simul_call("OPTIMAL", x_count=""),
    _simul_call("OPTIMAL", x_count="n/a"),
    _simul_call("OPTIMAL", swaps=""),
    _simul_call("OPTIMAL", row_shortfall=""),
    _simul_call("OPTIMAL", converged=2),
    _simul_call("FEASIBLE", distance_start=""),
    _simul_call("FEASIBLE", distance_final=""),
    _simul_call("OPTIMAL", "lp", slack_parent="oops"),
    _simul_call("OPTIMAL", "lp", attempts=""),
    _simul_call("OPTIMAL", "teleport"),
    _simul_call("OPTIMAL", ""),
    _simul_call("ABNORMAL", "teleport"),
], ids=["search-x-empty", "search-x-text", "search-swaps-empty", "search-row-shortfall-empty",
        "search-converged-2", "search-distance-start-empty", "search-distance-final-empty",
        "lp-slack-text", "lp-attempts-empty", "unknown-method", "empty-method",
        "unknown-method-of-a-failed-call"])
def test_controlled_call_with_a_damaged_value_or_method_is_a_damaged_record(tmp_path, call):
    # the empty-method row still says rounding "controlled", so only the method is damaged
    _controlled_batch(tmp_path, "batch_001", [call])
    with pytest.raises(RuntimeError, match="damaged"):
        _check_controlled(tmp_path)


def test_search_record_passes_and_the_totals_carry_the_sums(tmp_path):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("OPTIMAL", label="a", swaps=0, sweeps=1, distance_start=0.0, distance_final=0.0,
                    flow_wanted=0, flow_achieved=0),
        _simul_call("FEASIBLE", label="b", swaps=4, distance_start=9.0, distance_final=6.5,
                    flow_wanted=200, flow_achieved=190, row_shortfall=10, zone_shortfall=1,
                    hh_unreachable=0, hh_outside_lp=2, hh_excess=3),
        _call("OPTIMAL"),  # a single call is no controlled call and needs no rounding value
    ])
    _controlled_batch(tmp_path, "batch_002", [
        _simul_call("FEASIBLE", label="c", swaps=5, distance_start=1.0, distance_final=0.5,
                    flow_wanted=100, flow_achieved=100),
    ])
    _, totals = _check_controlled(tmp_path)
    assert totals["controlled_simul_calls"] == 3
    assert totals["search_calls"] == 3 and totals["lp_calls"] == 0
    assert totals["controlled_non_success_calls"] == 0
    assert totals["search_not_converged_calls"] == 0
    assert totals["swaps"] == 9
    assert totals["flow_wanted"] == 300 and totals["flow_achieved"] == 290
    assert totals["row_shortfall"] == 10 and totals["zone_shortfall"] == 1
    assert totals["distance_start"] == pytest.approx(10.0)
    assert totals["distance_final"] == pytest.approx(7.0)
    assert totals["hh_unreachable"] == 0 and totals["hh_outside_lp"] == 2
    assert totals["hh_excess"] == 3
    assert totals["conservation_loss_share"] == pytest.approx(10 / 300)
    assert totals["not_converged_share"] == 0.0
    # No LP call: the LP sums are zero, not an error.
    assert totals["x_count"] == 0 and totals["lp_conserve_slack_share"] == 0.0


def test_lp_record_passes_and_the_totals_carry_the_lp_sums(tmp_path):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("OPTIMAL", "lp", label="a", x_count=10, fractional_x=2, slack_conserve=1,
                    hh_excess=2, slack_cell=1, slack_parent_sub=2, slack_parent=3),
        _simul_call("FEASIBLE", "lp", label="b", x_count=30, fractional_x=3, attempts=2,
                    slack_conserve=3, slack_cell=10, slack_parent_sub=20, slack_parent=30,
                    repaired_x=3),
    ])
    _, totals = _check_controlled(tmp_path)
    assert totals["controlled_simul_calls"] == 2
    assert totals["lp_calls"] == 2 and totals["search_calls"] == 0
    assert totals["x_count"] == 40 and totals["fractional_x"] == 5
    assert totals["lp_calls_with_retries"] == 1
    assert totals["fractional_after_repair"] == 0 and totals["repair_failed"] == 0
    assert totals["slack_cell"] == 11
    assert totals["slack_parent_sub"] == 22
    assert totals["slack_parent"] == 33
    assert totals["slack_conserve"] == 4
    assert totals["lp_conserve_slack_share"] == pytest.approx(4 / 40)
    assert totals["hh_excess"] == 2
    # A LP call has no search columns: the search figures stay zero.
    assert totals["swaps"] == 0 and totals["flow_wanted"] == 0
    assert totals["conservation_loss_share"] == 0.0 and totals["not_converged_share"] == 0.0


def _warning_levels(caplog):
    return [r for r in caplog.records
            if r.name == solver_path_gate.logger.name and r.levelno >= logging.WARNING]


def test_clean_controlled_record_logs_no_warning(tmp_path, caplog):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("OPTIMAL", label="a"), _simul_call("OPTIMAL", "lp", label="b")])
    with caplog.at_level(logging.INFO):
        _check_controlled(tmp_path)
    assert not _warning_levels(caplog)


@pytest.mark.parametrize("bad_call", [
    _simul_call("ABNORMAL", label="x"),
    _simul_call("FEASIBLE", converged=0, label="x"),
    _simul_call("FEASIBLE", row_shortfall=5, label="x"),
    _simul_call("FEASIBLE", zone_shortfall=1, label="x"),
    _simul_call("OPTIMAL", hh_unreachable=4, label="x"),
    _simul_call("FEASIBLE", hh_outside_lp=2, label="x"),
    _simul_call("FEASIBLE", hh_excess=3, label="x"),
    _simul_call("OPTIMAL", "lp", repair_failed=1, label="x"),
    _simul_call("OPTIMAL", "lp", fractional_after_repair=2, label="x"),
    _simul_call("ABNORMAL", "lp", label="x"),
], ids=["search-non-success", "search-not-converged", "row-shortfall", "zone-shortfall",
        "hh-unreachable", "hh-outside-the-method", "hh-excess", "lp-repair-failed",
        "lp-fractional-after-repair", "lp-non-success"])
def test_controlled_degraded_call_is_a_warning(tmp_path, caplog, bad_call):
    _controlled_batch(tmp_path, "batch_001", [_simul_call("OPTIMAL", label="a"), bad_call])
    with caplog.at_level(logging.INFO):
        _check_controlled(tmp_path)
    assert _warning_levels(caplog)


def test_not_converged_and_row_shortfall_give_the_shares(tmp_path):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("FEASIBLE", label="a", converged=0, flow_wanted=150, flow_achieved=140,
                    row_shortfall=10),
        _simul_call("FEASIBLE", label="b", flow_wanted=50, flow_achieved=50),
        _simul_call("OPTIMAL", "lp", label="c", x_count=10),  # no search call, no search share
    ])
    _, totals = _check_controlled(tmp_path)
    assert totals["search_calls"] == 2 and totals["lp_calls"] == 1
    assert totals["search_not_converged_calls"] == 1
    assert totals["not_converged_share"] == pytest.approx(1 / 2)
    assert totals["conservation_loss_share"] == pytest.approx(10 / 200)
    assert totals["row_shortfall"] == 10


def test_a_failed_search_call_is_counted_and_its_numbers_are_not_read(tmp_path):
    # A network start that failed, as the fork records it: both distances empty, the round-ups it
    # wanted to place, none placed.
    failed = _simul_call(
        "ABNORMAL", label="a", swaps=0, sweeps=0, converged=0, distance_start="",
        distance_final="", flow_wanted=120, flow_achieved=0)
    _controlled_batch(tmp_path, "batch_001", [
        failed,
        _simul_call("FEASIBLE", label="b", converged=0, flow_wanted=100, flow_achieved=90,
                    row_shortfall=10),
        _simul_call("FEASIBLE", label="c", flow_wanted=100, flow_achieved=100),
    ])
    _, totals = _check_controlled(tmp_path)
    assert totals["controlled_simul_calls"] == 3 and totals["search_calls"] == 3
    assert totals["controlled_non_success_calls"] == 1
    # The result of the failed call is not used (the wrapper's sequential path ran): it is no
    # "not converged" call and enters no sum and no denominator.
    assert totals["search_not_converged_calls"] == 1
    assert totals["not_converged_share"] == pytest.approx(1 / 2)
    assert totals["flow_wanted"] == 200 and totals["flow_achieved"] == 190
    assert totals["conservation_loss_share"] == pytest.approx(10 / 200)


def test_a_failed_lp_call_is_counted_and_a_solved_one_needs_no_objective(tmp_path):
    # The fork leaves both objectives empty when no attempt solved the LP, and objective_repaired
    # when the repair failed; the gate reads no objective.
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("INFEASIBLE", "lp", label="a", attempts=2, x_count=50, objective_lp="",
                    objective_repaired=""),
        _simul_call("OPTIMAL", "lp", label="b", x_count=10, repair_failed=1,
                    fractional_after_repair=2, objective_repaired=""),
    ])
    _, totals = _check_controlled(tmp_path)
    assert totals["controlled_simul_calls"] == 2 and totals["lp_calls"] == 2
    assert totals["controlled_non_success_calls"] == 1
    assert totals["x_count"] == 10 and totals["lp_calls_with_retries"] == 0
    assert totals["repair_failed"] == 1 and totals["fractional_after_repair"] == 2


def test_unknown_expected_rounding_is_rejected(tmp_path):
    _two_batches(tmp_path)
    with pytest.raises(ValueError, match="expected_rounding"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="banana")


# --- the configured controlled method must be the one that ran (issue #646) ---------------

def _check_method(work_dir, expected_method):
    return solver_path_gate.check_solver_paths(
        work_dir, None, expected_rounding="controlled", expected_method=expected_method)


def test_expected_method_matching_the_record_passes(tmp_path):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("OPTIMAL", label="a"), _simul_call("FEASIBLE", label="b")])
    _, totals = _check_method(tmp_path, "search")
    assert totals["search_calls"] == 2 and totals["lp_calls"] == 0


def test_expected_method_other_than_the_recorded_one_raises_naming_the_batch(tmp_path):
    _good_controlled_batch(tmp_path)
    _controlled_batch(tmp_path, "batch_002", [
        _simul_call("OPTIMAL", label="a"), _simul_call("OPTIMAL", "lp", label="lp-call")])
    with pytest.raises(RuntimeError) as error:
        _check_method(tmp_path, "search")
    message = str(error.value)
    assert "batch_002" in message and "1 of 2" in message
    assert "lp-call" in message and "'lp'" in message and "'search'" in message
    assert "did not run" in message


def test_expected_method_none_keeps_the_behaviour_without_the_method_check(tmp_path):
    _controlled_batch(tmp_path, "batch_001", [_simul_call("OPTIMAL", "lp", label="a")])
    _, totals = solver_path_gate.check_solver_paths(
        tmp_path, None, expected_rounding="controlled", expected_method=None)
    assert totals["lp_calls"] == 1


def test_unknown_expected_method_is_rejected(tmp_path):
    _good_controlled_batch(tmp_path)
    with pytest.raises(ValueError, match="expected_method"):
        _check_method(tmp_path, "banana")


def test_controlled_log_line_gives_the_share_of_failed_calls(tmp_path, caplog):
    _controlled_batch(tmp_path, "batch_001", [
        _simul_call("OPTIMAL", label="a"), _simul_call("OPTIMAL", label="b"),
        _simul_call("OPTIMAL", label="c"), _simul_call("ABNORMAL", label="d")])
    with caplog.at_level(logging.INFO):
        _check_controlled(tmp_path)
    assert "1 not OPTIMAL/FEASIBLE (25.0% of the controlled simultaneous calls" in caplog.text


# A real record of one batch of ladder arm 3 (fork release 0.10.0+bs.2, rounding "controlled",
# method "search"; batch_032 of run manifest popsim-ladder-arm3-2026-11-18): the hand-written
# controlled records above cannot show a drift of the fork's controlled file format.
REAL_CONTROLLED_RECORD = (
    Path(__file__).parent / "fixtures" / "popsim" / "integerizer_paths_real_controlled.csv")


def test_real_controlled_fork_record_proves_the_rounding_and_the_method(tmp_path):
    folder = _batch(tmp_path, "batch_032", [], record=False)
    (folder / "output" / "integerizer_paths.csv").write_bytes(
        REAL_CONTROLLED_RECORD.read_bytes())
    summary, totals = solver_path_gate.check_solver_paths(
        tmp_path, None, expected_rounding="controlled", expected_method="search")
    assert summary.set_index("geography")["non_simultaneous"].to_dict() == {
        "KREIS": 0, "ZENSUS100m": 0, "ZENSUS1km": 0}
    assert (totals["controlled_simul_calls"], totals["search_calls"], totals["lp_calls"]) == (
        31, 31, 0)
    assert totals["controlled_non_success_calls"] == 0
    assert totals["search_not_converged_calls"] == 0
    assert totals["swaps"] == 15111  # the figure the run manifest reports for this batch
    assert totals["row_shortfall"] == 0 and totals["hh_excess"] == 0
    # The same record is refused when the config asks for the other method or for "round".
    with pytest.raises(RuntimeError, match="batch_032"):
        solver_path_gate.check_solver_paths(
            tmp_path, None, expected_rounding="controlled", expected_method="lp")
    with pytest.raises(RuntimeError, match="batch_032"):
        solver_path_gate.check_solver_paths(tmp_path, None, expected_rounding="round")
