"""Wiring of the KREIS input-consistency keys, the guard and the report into the stage (#658, A6).

The five keys (``config_keys.KEY_KREIS_BATCH_APPORTIONMENT`` and neighbours) are read by the stage
into a ``KreisConsistencySettings``, which hands them to ``run_popsim_mid``. These tests drive the
same objects on the synthetic two-batch fixture (``popsim_kreis_batch_fixture``): the table is
written for every batch, the guard names batch, Kreis and pair, the legacy values reproduce the
golden control tables (Part T and the KREIS batch goldens), and the new values change only the
KREIS table.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

# append, never insert(0): 'tests' at the front of sys.path would shadow the braunschweig package.
sys.path.append(str(Path(__file__).resolve().parent))

import popsim_control_build_fixture as part_t  # noqa: E402
import popsim_kreis_batch_fixture as fixture  # noqa: E402

from braunschweig.popsim import input_consistency as ic  # noqa: E402
from braunschweig.popsim import input_consistency_guard as guard  # noqa: E402
from braunschweig.popsim import stage  # noqa: E402
from braunschweig.popsim.mid import batch_folders  # noqa: E402
from braunschweig.popsim.stage import config_keys  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY = ("population", False, "survey", 0.05, 1000)
# The synthetic fixture cells inflate the children of one batch (x2.5) without touching POP_TOTAL, so
# its population-share pair persons_all differs by up to ~19 %: the new-value runs below widen the
# age tolerance to 0.5 to build the folders (and min persons 0, so every one of its tiny Kreis parts
# is judged; the unsplit Kreis 03158 alone carries a rounding residual of 0.1 on 20 children); the
# rule itself is tested with the real 0.05 in test_guard_rules_per_pair_group_and_size and
# test_the_age_universe_pairs_are_guarded_end_to_end.
NEW = ("universe", True, "zensus", 0.5, 0)


def _settings(values=LEGACY, work_dir=None):
    settings = stage.KreisConsistencySettings(*values)
    recorder = settings.open_recorder(work_dir) if work_dir is not None else None
    return settings, recorder


def _build(tmp_path, values=LEGACY, **kwargs):
    settings, recorder = _settings(values, tmp_path / "work")
    folders_ = fixture.build_batch_folders(
        tmp_path / "batches", settings=settings, recorder=recorder, **kwargs)
    return settings, recorder, folders_


# ---------------------------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------------------------


class _Recorder:
    def __init__(self, values=None):
        self.declared, self.values = {}, values or {}

    def config(self, key, default=None, **kwargs):
        self.declared.setdefault(key, default)
        return self.values.get(key, default)

    def stage(self, *args, **kwargs):
        return None


def test_the_five_keys_are_declared_with_their_legacy_defaults():
    recorder = _Recorder()
    stage.configure(recorder)
    assert (recorder.declared[config_keys.KEY_KREIS_BATCH_APPORTIONMENT],
            recorder.declared[config_keys.KEY_KREIS_CONTROLS_FOLLOW_GRIDS],
            recorder.declared[config_keys.KEY_EMPLOYMENT_LEVEL_SOURCE],
            recorder.declared[config_keys.KEY_KREIS_FINE_TOLERANCE],
            recorder.declared[config_keys.KEY_KREIS_FINE_MIN_PERSONS]) == LEGACY


@pytest.mark.parametrize("key, bad", [
    (config_keys.KEY_KREIS_BATCH_APPORTIONMENT, "universes"),
    (config_keys.KEY_EMPLOYMENT_LEVEL_SOURCE, "census"),
    (config_keys.KEY_KREIS_CONTROLS_FOLLOW_GRIDS, "yes"),
    (config_keys.KEY_KREIS_FINE_TOLERANCE, -0.1),
    (config_keys.KEY_KREIS_FINE_MIN_PERSONS, 2.5),
])
def test_an_invalid_key_value_names_the_key(key, bad):
    with pytest.raises(ValueError, match=key.replace(".", r"\.")):
        stage.configure(_Recorder({key: bad}))


def test_execute_reads_the_declared_keys_with_a_one_argument_config():
    values = dict(zip(
        (config_keys.KEY_KREIS_BATCH_APPORTIONMENT, config_keys.KEY_KREIS_CONTROLS_FOLLOW_GRIDS,
         config_keys.KEY_EMPLOYMENT_LEVEL_SOURCE, config_keys.KEY_KREIS_FINE_TOLERANCE,
         config_keys.KEY_KREIS_FINE_MIN_PERSONS), NEW))

    class _ExecuteContext:  # synpp's ExecuteContext.config(key) takes ONE argument
        def config(self, key):
            return values[key]

    settings = stage._read_kreis_consistency_config(_ExecuteContext())
    assert settings.key_values() == values


def test_base_config_carries_the_five_keys_at_their_production_values():
    # Switched by the owner's decision after popsim-ladder-inputs-a-2026-11-18 (ADR-0203); the
    # code defaults stay the legacy values (test_legacy_keys_reproduce_the_control_tables).
    text = (REPO_ROOT / "configs" / "base_bs.yml").read_text(encoding="utf-8")
    for key, value in zip(
            (config_keys.KEY_KREIS_BATCH_APPORTIONMENT, config_keys.KEY_KREIS_CONTROLS_FOLLOW_GRIDS,
             config_keys.KEY_EMPLOYMENT_LEVEL_SOURCE, config_keys.KEY_KREIS_FINE_TOLERANCE,
             config_keys.KEY_KREIS_FINE_MIN_PERSONS),
            ('"universe"', "true", '"zensus"', "0.05", "1000")):
        assert f"  {key}: {value}\n" in text.replace("\r\n", "\n")


def test_the_signature_moves_only_for_a_table_shaping_key():
    assert stage.KreisConsistencySettings(*LEGACY).signature_payload() is None
    assert stage.KreisConsistencySettings("population", False, "survey", 0.2, 5).signature_payload() is None
    payload = stage.KreisConsistencySettings(*NEW).signature_payload()
    assert payload[config_keys.KEY_KREIS_BATCH_APPORTIONMENT] == "universe"
    assert len(payload) == 5


# ---------------------------------------------------------------------------------------------
# load set
# ---------------------------------------------------------------------------------------------


def test_the_cell_load_set_gains_the_single_year_columns_of_every_universe():
    from braunschweig.popsim import kreis_attribute_control as kac

    class _Context:
        def config(self, key, default=None):
            return False

    # One entry (ages 0-5): the Tier-3 person universe (15+) is then the one only "universe" adds.
    entries = [c for c in kac.REGISTRY if c.name == "education_0_5"]

    def load(apportionment):
        return stage._resolve_cell_load_columns(
            _Context(), "csv", "mid", ("tier0",), ["BASE_A"], False, None, entries,
            kreis_batch_apportionment=apportionment, tier3_control_names=["employed_KREIS"])

    legacy, universe = load("population"), load("universe")
    assert {"M_AGE_0", "F_AGE_5"} <= set(legacy)
    assert not {"M_AGE_15", "F_AGE_100"} & set(legacy)
    assert {"M_AGE_0", "F_AGE_5", "M_AGE_15", "F_AGE_100"} <= set(universe)
    assert set(legacy) <= set(universe)
    assert len(universe) == len(set(universe))


# ---------------------------------------------------------------------------------------------
# table and guard
# ---------------------------------------------------------------------------------------------


def test_guard_writes_the_table_for_every_batch(tmp_path):
    _settings_, recorder, _folders = _build(tmp_path)

    table = pd.read_csv(recorder.csv_path, dtype={"key": str})
    assert table.columns[0] == "batch"
    assert list(table.columns[1:]) == ic.SUMMARY_COLUMNS
    assert set(table["batch"]) == set(fixture.BATCH_NAMES)
    assert {"persons_all", "cars_0", "employed"} <= set(table["check"])
    assert recorder.n_batches == 2 and recorder.n_rows == len(table)
    assert recorder.n_violations == 0


def test_guard_raises_on_a_kreis_grid_mismatch(tmp_path, monkeypatch):
    original = batch_folders._follow_grids

    def perturbed(*args, **kwargs):
        out = original(*args, **kwargs)
        out["number_of_cars_0_KREIS"] = out["number_of_cars_0_KREIS"] + 1
        return out

    monkeypatch.setattr(batch_folders, "_follow_grids", perturbed)
    settings, recorder = _settings(("population", True, "survey", 0.05, 1000), tmp_path / "work")

    with pytest.raises(ValueError, match=r"batch batch_000.*Kreis 03101.*pair cars_0"):
        fixture.build_batch_folders(tmp_path / "batches", settings=settings, recorder=recorder)

    # the table of the failing batch is on disk for the diagnosis
    assert set(pd.read_csv(recorder.csv_path)["batch"]) == {"batch_000"}


def test_the_age_universe_pairs_are_guarded_end_to_end(tmp_path):
    settings, recorder = _settings(("universe", False, "survey", 0.05, 100), tmp_path / "work")

    with pytest.raises(ValueError, match=r"batch batch_000.*Kreis 03101.*pair persons_all"):
        fixture.build_batch_folders(tmp_path / "batches", settings=settings, recorder=recorder)


def test_legacy_values_write_the_table_but_never_raise(tmp_path, monkeypatch):
    original = batch_folders._follow_grids
    monkeypatch.setattr(batch_folders, "_follow_grids", lambda *a, **k: original(*a, **k))
    settings, recorder = _settings(LEGACY, tmp_path / "work")
    folders_ = fixture.build_batch_folders(tmp_path / "batches", settings=settings, recorder=recorder)
    assert tuple(folders_) == fixture.BATCH_NAMES
    # the legacy KREIS table does contradict its grids (that is what Part A repairs): measured only
    assert recorder.max_rel_measured > guard.GRID_EQUAL_TOLERANCE
    assert recorder.max_rel_guarded == 0.0  # nothing is judged at the legacy values


def test_the_guard_flags_a_part_against_a_zero_grid_sum_only_when_judged():
    rows = [("kreis", "Z", "persons_all", 1, 3.0, 0.0, float("nan"))]
    judged = guard.guard_violations(
        _summary(rows), check_grid_pairs=False, check_age_pairs=True, age_tolerance=0.05,
        min_persons=0)
    assert list(judged["key"]) == ["Z"]
    exempt = guard.guard_violations(
        _summary(rows), check_grid_pairs=False, check_age_pairs=True, age_tolerance=0.05,
        min_persons=1000)
    assert exempt.empty


def test_measured_and_guarded_differences_differ_by_what_is_judged():
    rows = [
        ("kreis", "A", "cars_1", 1, 3.0, 1000.0, 0.003),
        ("kreis", "B", "persons_all", 1, 40.0, 100.0, 0.4),     # a small part: measured, not judged
        ("kreis", "C", "persons_0_17", 1, 20.0, 1000.0, 0.02),
    ]
    measured, guarded = guard.max_relative_differences(
        _summary(rows), check_grid_pairs=True, check_age_pairs=True, min_persons=1000)
    assert (measured, guarded) == (0.4, 0.02)
    measured, guarded = guard.max_relative_differences(
        _summary(rows), check_grid_pairs=False, check_age_pairs=False, min_persons=1000)
    assert (measured, guarded) == (0.4, 0.0)


def _summary(rows):
    return pd.DataFrame(rows, columns=["scope", "key", "check", "cells", "amount", "base", "share"])


def test_guard_rules_per_pair_group_and_size():
    rows = [
        ("kreis", "A", "cars_1", 1, 3.0, 1000.0, 0.003),                 # grid pair above 1e-6
        ("kreis", "B", "persons_all", 1, 40.0, 1000.0, 0.04),            # age pair below 0.05
        ("kreis", "C", "persons_0_17", 1, 90.0, 1000.0, 0.09),           # age pair above 0.05
        ("kreis", "D", "persons_18plus", 1, 9.0, 100.0, 0.09),           # above, but a small part
        ("kreis", "E", "employed_survey", 0, 5.0, 100.0, 0.9),           # never guarded
        ("kreis", "F", "ratio_reported", 0, 5.0, 100.0, 0.9),
        ("kreis", "households", "skipped", 0, float("nan"), float("nan"), float("nan")),
        ("kreis", "G", "employed", 1, 2.0, 0.0, float("nan")),           # against a grid sum of 0
    ]
    kwargs = dict(age_tolerance=0.05, min_persons=1000)
    both = guard.guard_violations(
        _summary(rows), check_grid_pairs=True, check_age_pairs=True, **kwargs)
    assert list(both["key"]) == ["A", "C", "G"]
    only_age = guard.guard_violations(
        _summary(rows), check_grid_pairs=False, check_age_pairs=True, **kwargs)
    assert list(only_age["key"]) == ["C"]
    none = guard.guard_violations(
        _summary(rows), check_grid_pairs=False, check_age_pairs=False, **kwargs)
    assert none.empty


def test_the_pair_groups_are_the_listed_pairs_of_the_measurement():
    relation = {pair.label: pair.relation
                for pair in (*ic.KREIS_FINE_PAIRS, *ic.STUDENT_PAIRS.values())}
    for label in guard.GRID_EQUAL_PAIRS + guard.AGE_UNIVERSE_PAIRS:
        assert relation[label] == "equal", label
    for label in guard.STUDENT_BOUND_PAIRS:
        assert relation[label] == "at_most", label
    # the grid pairs are exactly the pairs of Task A4's mapping: its cars, bicycles and employed
    from braunschweig.popsim import control_spec
    assert len(guard.GRID_EQUAL_PAIRS) == len(control_spec.kreis_grid_pairs())
    assert not set(guard.GRID_EQUAL_PAIRS) & set(guard.AGE_UNIVERSE_PAIRS)


# ---------------------------------------------------------------------------------------------
# goldens
# ---------------------------------------------------------------------------------------------


def _assert_tables_equal(data_dir, golden_dir, names):
    for name in names:
        pd.testing.assert_frame_equal(
            fixture.read_table(data_dir, name), fixture.read_table(golden_dir, name),
            check_exact=True, obj=f"{data_dir.parent.name}/control_totals_{name}")


def test_legacy_keys_reproduce_the_control_tables(tmp_path):
    # Part T: the control build without a KREIS geography does not depend on the keys.
    built = part_t.build_control_tables()
    for name in part_t.GOLDEN_NAMES:
        pd.testing.assert_frame_equal(
            built[name], part_t.read_golden(name), check_exact=True, obj=f"control_totals_{name}")
    # KREIS batch goldens: legacy values, with the recorder switched on, change no table.
    _settings_, _recorder, data_dirs = _build(tmp_path)
    for batch, data_dir in data_dirs.items():
        golden_dir = fixture.GOLDEN_DIR / batch
        names = fixture.table_names(golden_dir)
        assert fixture.table_names(data_dir) == names
        _assert_tables_equal(data_dir, golden_dir, names)


def test_new_keys_change_only_the_kreis_table(tmp_path):
    settings, recorder, data_dirs = _build(tmp_path, NEW, employed_bridge=0.95)

    for batch, data_dir in data_dirs.items():
        golden_dir = fixture.GOLDEN_DIR / batch
        grid_names = [n for n in fixture.table_names(golden_dir) if n != "KREIS"]
        assert grid_names == ["STAAT", "WELT", "ZENSUS100m", "ZENSUS1km"]
        _assert_tables_equal(data_dir, golden_dir, grid_names)
    kreis_changed = [
        not fixture.read_table(data_dirs[b], "KREIS").equals(
            fixture.read_table(fixture.GOLDEN_DIR / b, "KREIS"))
        for b in fixture.BATCH_NAMES]
    assert all(kreis_changed)
    assert recorder.n_violations == 0  # the guard passed with the new values
    # The defect the split repairs: Kreis 03101 is split over both batches and batch_001 holds 2.5x
    # the children per cell. Split by population share, its KREIS persons 0-17 target missed the
    # 100 m bands by more than 20 % in both parts (legacy keys on this fixture); split by the share of
    # the 0-17 universe it equals the batch's float child count, so only the rounding of the 100 m
    # bands remains (well below 1 %).
    table = pd.read_csv(recorder.csv_path, dtype={"key": str})
    split = table[(table["scope"] == "kreis") & (table["key"] == "03101")
                  & (table["check"] == "persons_0_17")]
    assert sorted(split["batch"]) == list(fixture.BATCH_NAMES)
    assert (split["share"] < 0.01).all()


def test_new_keys_fill_the_report(tmp_path):
    settings, recorder, _ = _build(tmp_path, NEW, employed_bridge=0.95)

    report = settings.report()
    assert report["keys"][config_keys.KEY_KREIS_BATCH_APPORTIONMENT] == "universe"
    assert report["guard"]["batches"] == 2
    assert report["guard"]["max_relative_difference_measured"] == recorder.max_rel_measured
    assert report["guard"]["rows"] > 0 and report["guard"]["rows_above_bound"] == 0
    # Hand count on the fixture: 3 Kreis parts (batch_000: 03101 and 03158; batch_001: 03101).
    # Mapped age-universe controls: 7 Tier-3 controls (15+), the 18 KREIS columns of the 14+
    # employment entries, and 2 each for education 0-5, 6-17 and 18+ = 31 over 5 universe columns.
    assert len(settings.apportion_map) == 31
    assert len(set(settings.apportion_map.values())) == 5
    universe = report["universe_apportionment"]
    assert universe == {"kreis_parts": 3 * 5, "empty_parts": 0, "empty_regions": 0}
    follow = report["kreis_follow_grids"]
    # 4 car classes + 5 bicycle classes + employed = 10 controls per batch, 2 batches.
    assert follow["controls_set_over_batches"] == 20
    assert follow["controls_changed_over_batches"] == 20 and follow["controls_without_grid"] == []
    assert follow["largest_relative_change"] > 0 and follow["largest_relative_change_control"]
    level = report["employment_level"]
    # 3 Kreis parts x 2 families (work_by_employment, employment_status), all reconciled.
    assert level == {"parts_reconciled": 6, "parts_capped": 0, "capped_persons": 0.0,
                     "parts_empty": 0, "parts_unchanged": 0}


def test_the_stage_report_file_and_run_info(tmp_path):
    settings, recorder, _ = _build(tmp_path, NEW, employed_bridge=0.95)

    class _Context:
        def __init__(self):
            self.info = {}

        def path(self):
            return str(tmp_path)

        def set_info(self, key, value):
            self.info[key] = value

    context = _Context()
    # The stage report is ONE file shared with the entries of _write_census_filtered_report, written
    # earlier in execute(): the #658 writer must keep them (regression: it once overwrote the file).
    tenure_entry = stage._write_census_filtered_report(
        context, zgb_cell_filter={"cells_seen": 1}, building_type_enabled=False,
        building_type_report=None, enabled=False, min_households_outside=10.0, report=None)
    path = stage._write_consistency_report(context, settings)

    assert path.name == stage.CENSUS_FILTERED_REPORT_FILE
    content = json.loads(path.read_text(encoding="utf-8"))
    assert content["tenure_owner_limited_to_dwellings"] == tenure_entry
    assert content["zgb_cell_filter"] == {"cells_seen": 1}
    written = content[stage.KREIS_INPUT_CONSISTENCY_REPORT_KEY]
    assert written["guard"]["max_relative_difference_measured"] == recorder.max_rel_measured
    assert written["guard"]["max_relative_difference_guarded"] == recorder.max_rel_guarded
    # the info value is the MEASURED one (all pairs); the guarded one covers the judged rows only
    assert context.info["popsim_input_consistency_max_rel"] == recorder.max_rel_measured
    assert recorder.max_rel_guarded <= recorder.max_rel_measured


def test_the_report_is_strict_json_with_null_for_nan_and_infinity(tmp_path):
    settings = stage.KreisConsistencySettings(*LEGACY)
    recorder = settings.open_recorder(tmp_path / "work")
    recorder.set("employment_level", below_grid_share=float("nan"), bridge=float("inf"), ok=0.5)

    class _Context:
        def path(self):
            return str(tmp_path)

        def set_info(self, key, value):
            pass

    path = stage._write_consistency_report(_Context(), settings)

    # a strict parser (no NaN / Infinity literals) reads it
    def refuse(constant):
        raise AssertionError(f"non-strict JSON constant {constant}")

    written = json.loads(path.read_text(encoding="utf-8"),
                         parse_constant=refuse)[stage.KREIS_INPUT_CONSISTENCY_REPORT_KEY]
    assert written["employment_level"] == {"below_grid_share": None, "bridge": None, "ok": 0.5}


class _BrokenReportContext:
    def path(self):
        raise OSError("disk full")


def test_a_failing_report_never_masks_the_guard_error(caplog):
    settings = stage.KreisConsistencySettings(*LEGACY)

    def guard_fails():
        raise ValueError("guard failed")

    with pytest.raises(ValueError, match="guard failed"):
        stage._run_with_consistency_report(_BrokenReportContext(), settings, guard_fails)
    assert any(r.levelname == "ERROR" and "could not be written" in r.getMessage()
               for r in caplog.records)


def test_a_failing_report_after_a_successful_run_raises(caplog):
    # Success path: no exception in flight, so a report that cannot be written is an error of the
    # run, not swallowed into a log line.
    settings = stage.KreisConsistencySettings(*LEGACY)

    with pytest.raises(OSError, match="disk full"):
        stage._run_with_consistency_report(_BrokenReportContext(), settings, lambda: "merged")
    assert not any(r.levelname == "ERROR" for r in caplog.records)


def test_production_keys_without_a_kreis_table_or_employment_grid_fail_early():
    production = stage.KreisConsistencySettings("universe", True, "zensus", 0.05, 1000)
    with pytest.raises(ValueError) as error:
        production.require_run_support(has_kreis_table=False, employment_grid_on=True)
    for key in (config_keys.KEY_KREIS_BATCH_APPORTIONMENT,
                config_keys.KEY_KREIS_CONTROLS_FOLLOW_GRIDS, config_keys.KEY_EMPLOYMENT_LEVEL_SOURCE):
        assert key in str(error.value)
    with pytest.raises(ValueError, match=config_keys.KEY_EMPLOYMENT_GRID.replace(".", r"\.")):
        production.require_run_support(has_kreis_table=True, employment_grid_on=False)
    production.require_run_support(has_kreis_table=True, employment_grid_on=True)
    # The legacy values need neither (a tier0-2 run keeps working).
    stage.KreisConsistencySettings(*LEGACY).require_run_support(
        has_kreis_table=False, employment_grid_on=False)


def test_run_popsim_mid_puts_the_bridge_of_the_seed_into_the_recorder(tmp_path, monkeypatch):
    # PRIMARY bridge path: run_popsim_mid computes the bridge from the seed (not a hand-passed
    # value) and records it; the spy stops the run at the first batch, after the bridge is set.
    # (test_popsim_employment_level covers that the bridge reaches assemble_batch_folder, not
    # that it reaches the recorder.)
    import test_popsim_employment_level as emp

    class _Stop(Exception):
        pass

    def spy(*args, **kwargs):
        raise _Stop

    monkeypatch.setattr(batch_folders, "assemble_batch_folder", spy)
    controls_df = emp._controls_df()
    table, controls_map = emp._kreis_inputs(controls_df)
    persons, households = emp._persons()
    persons["P_ID"] = 1
    settings, _recorder = _settings(("population", False, "zensus", 0.05, 1000), tmp_path / "work")

    with pytest.raises(_Stop):
        batch_folders.run_popsim_mid(
            emp._cells()[:1], emp.EMPLOYED, controls_df, households, persons,
            work_dir=tmp_path / "batches", settings_yaml="x: 1\n", logging_yaml="version: 1\n",
            max_cells=10, run_one=None, kreis_table=table, kreis_controls_map=controls_map,
            **settings.run_popsim_mid_kwargs())

    level = settings.report()["employment_level"]
    assert level["bridge"] == pytest.approx(0.75)  # the value test_popsim_employment_level pins
    assert {"bridge_family_universe", "below_grid_share", "bridge_source"} <= set(level)


# ---------------------------------------------------------------------------------------------
# student entries (A3): the two age-range pairs and the two at-most pairs
# ---------------------------------------------------------------------------------------------
_WORK_PAIR = "students_work_within_nonemployed_work"
_TICKET_PAIR = "students_flatrate_within_flatrate"
_ALL_STUDENT_ENTRIES = ("student_education_18_29", "student_education_30plus",
                        "student_work_leg", "student_season_ticket")


_BOUND_CROSSWALK = pd.DataFrame({"ZENSUS100m": ["c1"], "ZENSUS1km": ["k1"], "KREIS": ["03101"]})


def _bound_batch(recorder, batch, parts):
    """Record one batch whose KREIS table holds only the two bounds of every Kreis part:
    ``parts`` = {Kreis: (students with a work leg, non-employed with a work leg,
    students with a flat-rate ticket, flat-rate holders)}."""
    kreis = pd.DataFrame({
        "KREIS": list(parts),
        "student_work_leg_student_work_KREIS": [p[0] for p in parts.values()],
        "work_by_employment_nonemployed_work_KREIS": [p[1] for p in parts.values()],
        "student_season_ticket_student_flatrate_KREIS": [p[2] for p in parts.values()],
        "pt_ticket_group4_deutschlandticket_KREIS": [p[3] for p in parts.values()],
        "pt_ticket_group4_other_flatrate_KREIS": 0.0,
    })
    return recorder.record_batch(batch, {"KREIS": kreis}, _BOUND_CROSSWALK)


def _bound_recorder(path, check_age_pairs=True):
    names = {"work_by_employment", "pt_ticket_group4", "student_work_leg", "student_season_ticket"}
    return guard.ConsistencyRecorder(
        path, check_grid_pairs=False, check_age_pairs=check_age_pairs, age_tolerance=0.05,
        min_persons=1000, pairs=ic.kreis_pairs(names))


def test_work_pair_is_guarded_and_ticket_pair_only_reported(tmp_path):
    """Kreis 03101 is split over two batches, 03158 lies in batch_000 only.

    The ticket bound is broken in two parts (excess 10 each): written, counted and reported, never
    raised; summed over its parts 03101 keeps it (40 <= 80), 03158 does not. The work bound is
    strict: an excess of 0.5 in a part of 100,000 raises, with no relative tolerance and no
    minimum part size, whatever the key groups say. A young-heavy part (many persons aged 18 to 29,
    by whose share the student targets are split) breaks the work bound although the Kreis totals
    keep it (35 <= 60): the guard judges the part and raises."""
    recorder = _bound_recorder(tmp_path / "reported.csv")
    _bound_batch(recorder, "batch_000", {"03101": (5.0, 40.0, 30.0, 20.0),
                                         "03158": (1.0, 10.0, 40.0, 30.0)})
    _bound_batch(recorder, "batch_001", {"03101": (5.0, 40.0, 10.0, 60.0)})
    assert recorder.n_violations == 0 and recorder.n_reported_above_bound == 2
    assert recorder.report()["student_bounds"] == {
        "guarded": [_WORK_PAIR], "reported": [_TICKET_PAIR], "rows_above_bound_reported": 2,
        "kreis_totals_above_bound": {_WORK_PAIR: [], _TICKET_PAIR: ["03158"]}}
    table = pd.read_csv(recorder.csv_path, dtype={"key": str})
    ticket = table[table["check"] == _TICKET_PAIR].set_index(["batch", "key"])["amount"]
    assert ticket.to_dict() == {("batch_000", "03101"): 10.0, ("batch_000", "03158"): 10.0,
                                ("batch_001", "03101"): 0.0}

    for check_age_pairs, parts in ((False, {"03101": (100000.5, 100000.0, 0.0, 1.0)}),
                                   (True, {"03101": (30.0, 20.0, 0.0, 1.0)})):
        strict = _bound_recorder(tmp_path / f"work_{check_age_pairs}.csv", check_age_pairs)
        with pytest.raises(ValueError, match=r"batch batch_000.*Kreis 03101, pair " + _WORK_PAIR):
            _bound_batch(strict, "batch_000", parts)
    _bound_batch(strict, "batch_001", {"03101": (5.0, 40.0, 0.0, 1.0)})  # the other part holds
    assert strict.kreis_totals_above_bound()[_WORK_PAIR] == []          # and so does the Kreis


def _student_build(tmp_path, employed_bridge):
    settings = stage.KreisConsistencySettings(*NEW)
    recorder = settings.open_recorder(tmp_path / "work", fixture.entry_names(_ALL_STUDENT_ENTRIES))
    fixture.build_batch_folders(tmp_path / "batches", settings=settings, recorder=recorder,
                                employed_bridge=employed_bridge, student_entries=_ALL_STUDENT_ENTRIES)
    return recorder


def test_bound_pair_is_judged_after_the_bridge(tmp_path):
    """The student pairs on the two-batch fixture with all four student entries (synthetic shares).

    Without the bridge every pair holds: the two age-range pairs replace persons_18plus and meet
    their 100 m bands, and both at-most pairs are written for every Kreis part. A bridge far above
    the grid gives the employed classes every person of each part (capped at the row total), so no
    non-employed person with a work leg is left: the students with a work leg now exceed them, and
    only a check AFTER the bridge sees it."""
    recorder = _student_build(tmp_path / "plain", None)

    table = pd.read_csv(recorder.csv_path, dtype={"key": str})
    kreis = table[table["scope"] == "kreis"]
    parts = {("batch_000", "03101"), ("batch_000", "03158"), ("batch_001", "03101")}
    for label in ("persons_18_29", "persons_30plus", _WORK_PAIR, _TICKET_PAIR):
        rows = kreis[kreis["check"] == label]
        assert set(zip(rows["batch"], rows["key"])) == parts, label
    assert "persons_18plus" not in set(table["check"]) | set(table["key"])
    age_rows = kreis[kreis["check"].isin(["persons_18_29", "persons_30plus"])]
    assert (age_rows["share"] < 0.01).all()
    assert (kreis.loc[kreis["check"] == _WORK_PAIR, "amount"] == 0).all()
    assert recorder.n_violations == 0
    assert recorder.report()["student_bounds"] == {
        "guarded": [_WORK_PAIR], "reported": [_TICKET_PAIR], "rows_above_bound_reported": 0,
        "kreis_totals_above_bound": {_WORK_PAIR: [], _TICKET_PAIR: []}}

    with pytest.raises(ValueError, match=r"batch batch_000.*pair " + _WORK_PAIR):
        _student_build(tmp_path / "bridged", 1000.0)


# SHA-256 of the table and of the report (paths nulled, json.dumps(sort_keys=True)) that the code
# BEFORE the student pairs (commit 10c581a7, the parent of the commit that added them) wrote for
# the fixture below; line endings normalised to LF, since pandas writes the platform's.
_OFF_TABLE_SHA256 = "e5cfdb478bc50258e436b1535069742f23ce7d119f33c1f2f3b717f9c8de45d2"
_OFF_REPORT_SHA256 = "824999b94602ffab8a98a8d68b1eaa47e5a2cf77b50a18e59b1114f483fee45e"


def test_off_path_writes_todays_consistency_table(tmp_path):
    """No student entry: the recorder the stage opens with the run's active entry names writes the
    table and report of the code before the student pairs, byte for byte (no new pair, no new
    skipped row, no new report key)."""
    import hashlib

    settings = stage.KreisConsistencySettings(*NEW)
    recorder = settings.open_recorder(tmp_path / "work", fixture.entry_names())
    fixture.build_batch_folders(tmp_path / "batches", settings=settings, recorder=recorder,
                                employed_bridge=0.95)
    table = recorder.csv_path.read_bytes().replace(b"\r\n", b"\n")
    report = settings.report()
    report["table"] = report["guard"]["table"] = None
    assert hashlib.sha256(table).hexdigest() == _OFF_TABLE_SHA256
    assert hashlib.sha256(json.dumps(report, sort_keys=True).encode("utf-8")).hexdigest() == (
        _OFF_REPORT_SHA256)
