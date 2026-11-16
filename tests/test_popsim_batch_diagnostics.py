"""Tests for braunschweig.analysis.popsim_batch_diagnostics (batch folder + incidence)."""
import sys
import warnings
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import tables

sys.path.append(str(Path(__file__).parent))

from popsim_batch_fixture import TOTAL, build_work_dir  # noqa: E402

from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import (  # noqa: E402
    control_totals, household_incidence, list_batch_folders, load_batch_folder)
from braunschweig.analysis.popsim_batch_diagnostics.coverage import (  # noqa: E402
    coverage_table, universe_table)
from braunschweig.analysis.popsim_batch_diagnostics.fit import (  # noqa: E402
    control_fit, misfit_split, read_weight_table, rounding_shift)
from braunschweig.analysis.popsim_batch_diagnostics.reuse import (  # noqa: E402
    reuse_by_zone, reuse_summary, shares_by_attribute, usage_counts, weight_class_shares)
from braunschweig.analysis.popsim_batch_diagnostics.solver_paths import (  # noqa: E402
    solver_path_table)
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
    assert batch.geographies == ("STAAT", "KREIS", "ZENSUS1km", "ZENSUS100m")


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


def _extra_tenure(target, expression):
    return [dict(target=target, seed_table="households", importance=1, expression=expression)]


def test_overlapping_category_is_counted_as_multi_hit(tmp_path):
    # Households 1 and 4 have H_MIETE == 1, so the extra control doubles the Mieter category.
    work = build_work_dir(tmp_path, extra_controls=_extra_tenure(
        "X_Tenure_ZENSUS100m_target", "(households.H_MIETE == 1)"))
    batch = load_batch_folder(work / "batch_000")
    row = coverage_table(batch, pd.Series({2: 1, 3: 1, 4: 3})).set_index("group").loc["tenure"]
    assert row[["n_universe", "n_uncovered", "n_multi_hit"]].tolist() == [4, 1, 2]


def test_ratio_is_nan_when_no_record_is_uncovered(tmp_path):
    # H_MIETE == 9 (household 3) is the only uncovered tenure record; cover it.
    work = build_work_dir(tmp_path, extra_controls=_extra_tenure(
        "Y_Tenure_ZENSUS100m_target", "(households.H_MIETE == 9)"))
    batch = load_batch_folder(work / "batch_000")
    row = coverage_table(batch, pd.Series({2: 1, 3: 1, 4: 3})).set_index("group").loc["tenure"]
    assert row["n_uncovered"] == 0 and row["seed_weight_share"] == 0
    assert pd.isna(row["ratio"])


def test_reuse_and_weight_class_shares(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    usage = usage_counts(batch.expanded)
    assert usage.to_dict() == {2: 1, 3: 1, 4: 3}
    summary = reuse_summary(usage, n_seed_households=4)
    assert summary["n_synthetic_households"] == 5 and summary["n_distinct_used"] == 3
    assert summary["distinct_share_of_seed"] == 0.75
    assert summary["kish_n_eff"] == pytest.approx(25 / 11)
    assert summary["max_copies"] == 3 and summary["top100_share"] == 1.0
    shares = weight_class_shares(batch.seed_households, usage).set_index("weight_class")
    assert shares["seed_weight_share"].tolist() == pytest.approx([0.0625, 0.1875, 0.25, 0.5, 0.0, 0.0])
    assert shares["synthetic_share"].tolist() == pytest.approx([0.0, 0.2, 0.2, 0.6, 0.0, 0.0])
    assert shares.loc["<1", "difference_pp"] == pytest.approx(-6.25)
    by_size = shares_by_attribute(batch.seed_households, usage, "H_GR").set_index("value")
    assert by_size.loc[2, ["seed_weight_share", "synthetic_share"]].tolist() == pytest.approx([0.6875, 0.8])


def test_reuse_by_zone_and_fail_fast_inputs(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    zones = reuse_by_zone(batch.expanded, "ZENSUS100m").set_index("zone")
    assert zones.loc["A", ["n_synthetic_households", "n_distinct_used"]].tolist() == [3, 2]
    assert zones.loc["A", "kish_n_eff"] == pytest.approx(9 / 5)
    assert zones.loc["B", "kish_n_eff"] == pytest.approx(2.0)
    with pytest.raises(KeyError, match="nope"):
        reuse_by_zone(batch.expanded, "nope")
    usage = usage_counts(batch.expanded)
    negative = batch.seed_households.assign(H_GEW=[0.5, -1.0, 2.0, 4.0])
    with pytest.raises(ValueError, match="negative"):
        weight_class_shares(negative, usage)
    with pytest.raises(ValueError, match="not in the seed"):
        weight_class_shares(batch.seed_households, pd.Series({9: 1}))


def test_nan_attribute_is_its_own_row_and_zero_weight_value_has_nan_ratio(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    seed = batch.seed_households.assign(
        grp=["a", None, "b", "b"], H_GEW=[0.0, 1.5, 2.0, 4.0])
    rows = shares_by_attribute(seed, usage_counts(batch.expanded), "grp")
    nan_row = rows[rows["value"].isna()].iloc[0]
    assert nan_row["n_seed"] == 1 and nan_row["seed_weight_share"] == pytest.approx(0.2)
    assert nan_row["synthetic_share"] == pytest.approx(0.2)
    zero_row = rows[rows["value"] == "a"].iloc[0]
    assert zero_row["seed_weight_share"] == 0 and pd.isna(zero_row["ratio"])


def test_weights_exactly_on_class_edges_fall_into_the_right_left_closed_class(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    seed = batch.seed_households.assign(H_GEW=[1.0, 2.0, 2.0, 4.0])
    shares = weight_class_shares(seed, usage_counts(batch.expanded)).set_index("weight_class")
    assert shares["n_seed"].tolist() == [0, 1, 2, 1, 0, 0]


def test_empty_usage_is_refused(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    empty = usage_counts(batch.expanded).iloc[:0]
    with pytest.raises(ValueError, match="empty"):
        weight_class_shares(batch.seed_households, empty)
    with pytest.raises(ValueError, match="empty"):
        shares_by_attribute(batch.seed_households, empty, "H_GR")


def test_missing_household_id_and_custom_id_column(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    expanded = batch.expanded.astype({"H_ID": float})
    expanded.loc[0, "H_ID"] = float("nan")
    with pytest.raises(ValueError, match="missing"):
        usage_counts(expanded)
    renamed = batch.expanded.rename(columns={"H_ID": "HH"})
    zones = reuse_by_zone(renamed, "ZENSUS100m", household_id_col="HH").set_index("zone")
    assert zones.loc["A", "kish_n_eff"] == pytest.approx(9 / 5)
    summary = reuse_summary(usage_counts(batch.expanded), n_seed_households=4,
                            household_id_col="HH")
    assert summary["kish_n_eff"] == pytest.approx(25 / 11)


# --- Task 4: control fit, misfit split, rounding shift -------------------------------

_FIRST_KEY = "/ZENSUS100m_weights/sub_balancing.geography=ZENSUS100m"
_SECOND_KEY = "/ZENSUS100m_weights/final"


def _store_path(work_dir):
    return work_dir / "batch_000" / "output" / "pipeline.h5"


def _write_keys(work_dir, frames):
    """Add (or replace) fixed-format tables in the fixture's checkpoint store."""
    with warnings.catch_warnings(), pd.HDFStore(str(_store_path(work_dir)), mode="a") as store:
        warnings.filterwarnings("ignore", category=tables.NaturalNameWarning)
        for key, frame in frames.items():
            store.put(key, frame, format="fixed")


def _checkpoints(weights_checkpoints):
    """A ``/checkpoints`` frame: one row per checkpoint, one column per table name."""
    rows = []
    for i, weights_checkpoint in enumerate(weights_checkpoints):
        rows.append({"checkpoint_name": f"step_{i}", "timestamp": f"2026-11-16 10:0{i}",
                     "incidence_table": "setup_data_structures",
                     "control_spec": "setup_data_structures",
                     "ZENSUS100m_controls": "setup_data_structures",
                     "ZENSUS100m_weights": weights_checkpoint})
    return pd.DataFrame(rows)


def test_control_fit_against_targets(work_dir):
    fit = control_fit(load_batch_folder(work_dir / "batch_000")).set_index("control")
    assert fit.loc[TOTAL, "wape"] == 0.0
    assert fit.loc["M_AGE_18_plus_ZENSUS100m_target", ["wape", "bias"]].tolist() == pytest.approx([2 / 3, 0.0])
    assert fit.loc["F_AGE_18_plus_ZENSUS100m_target", ["wape", "bias"]].tolist() == pytest.approx([1 / 3, 1 / 3])
    assert fit.loc["EigentuemerHH_Tenure_ZENSUS100m_target", ["wape", "bias"]].tolist() == pytest.approx([0.5, -0.5])
    assert fit.loc["building_type_mfh_ZENSUS100m_target", ["wape", "bias"]].tolist() == pytest.approx([1.0, -1.0])
    zero = fit.loc["F_AGE_0_17_ZENSUS100m_target"]          # targets sum to zero
    assert zero["target_sum"] == 0 and np.isnan(zero["wape"]) and np.isnan(zero["bias"])


def test_control_fit_names_a_geography_without_control_totals(work_dir):
    (work_dir / "batch_000" / "data" / "control_totals_ZENSUS100m.csv").unlink()
    with pytest.raises(FileNotFoundError, match="control_totals_ZENSUS100m"):
        control_fit(load_batch_folder(work_dir / "batch_000"))


@pytest.mark.parametrize("chunk_rows", [3, 5_000_000])
def test_misfit_split_separates_balancing_from_rounding(work_dir, chunk_rows):
    batch = load_batch_folder(work_dir / "batch_000")
    split = misfit_split(batch, levels=("ZENSUS100m",), chunk_rows=chunk_rows).set_index("control")
    m = split.loc["M_AGE_18_plus_ZENSUS100m_target"]
    assert [m["float_sum"], m["integer_sum"]] == pytest.approx([3.0, 3.0])
    assert [m["balance_wape"], m["round_wape"], m["total_wape"]] == pytest.approx([1.6 / 3, 0.4 / 3, 2 / 3])
    f = split.loc["F_AGE_18_plus_ZENSUS100m_target"]
    assert [f["float_sum"], f["integer_sum"]] == pytest.approx([3.4, 4.0])
    assert [f["bias_float"], f["bias_int"]] == pytest.approx([0.4 / 3, 1 / 3])
    shift = rounding_shift(split.reset_index(), batch.total_hh_control, min_target=2.0).set_index("control")
    assert shift["within_bound"].to_dict() == {
        "M_AGE_18_plus_ZENSUS100m_target": True, "F_AGE_18_plus_ZENSUS100m_target": False,
        "MieterHH_Tenure_ZENSUS100m_target": True, "EigentuemerHH_Tenure_ZENSUS100m_target": True,
        "building_type_efh_ZENSUS100m_target": True, "building_type_mfh_ZENSUS100m_target": False}
    assert shift.loc["F_AGE_18_plus_ZENSUS100m_target", "shift"] == pytest.approx(0.2)
    assert shift.loc["building_type_mfh_ZENSUS100m_target", "shift"] == pytest.approx(0.15)


def test_weight_table_is_the_same_whether_read_in_one_chunk_or_several(work_dir):
    one = read_weight_table(_store_path(work_dir), "ZENSUS100m", chunk_rows=5_000_000)
    several = read_weight_table(_store_path(work_dir), "ZENSUS100m", chunk_rows=3)
    assert len(one) > 3          # chunk_rows=3 really reads several chunks
    pd.testing.assert_frame_equal(several, one)


def test_misfit_split_of_an_all_zero_control_is_nan_without_warnings(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    with warnings.catch_warnings():
        warnings.simplefilter("error")      # a division warning would fail the test
        split = misfit_split(batch, levels=("ZENSUS100m",)).set_index("control")
    zero = split.loc["F_AGE_0_17_ZENSUS100m_target"]
    assert zero["target_sum"] == 0
    assert zero[["balance_wape", "round_wape", "total_wape", "bias_float", "bias_int"]].isna().all()


def test_zone_id_mismatch_above_the_unmatched_share_is_refused(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    totals_file = work_dir / "batch_000" / "data" / "control_totals_ZENSUS100m.csv"
    totals = pd.read_csv(totals_file)
    totals[totals["ZENSUS100m"] != "B"].to_csv(totals_file, index=False)      # zone B: no targets
    with pytest.raises(ValueError, match="max_unmatched_zone_share"):
        control_fit(batch)
    assert len(control_fit(batch, max_unmatched_zone_share=0.6)) == len(batch.control_spec)

    weights = pd.read_hdf(_store_path(work_dir), _FIRST_KEY)
    extra = weights.iloc[:2].assign(ZENSUS100m="C")      # zone C is not in /ZENSUS100m_controls
    _write_keys(work_dir, {_FIRST_KEY: pd.concat([weights, extra], ignore_index=True)})
    with pytest.raises(ValueError, match="max_unmatched_zone_share"):
        misfit_split(batch, levels=("ZENSUS100m",))
    split = misfit_split(batch, levels=("ZENSUS100m",), max_unmatched_zone_share=0.5)
    assert set(split["level"]) == {"ZENSUS100m"}


def test_misfit_split_needs_the_checkpoint_store(tmp_path):
    work = build_work_dir(tmp_path, with_store=False)
    with pytest.raises(FileNotFoundError, match="batch_000"):
        misfit_split(load_batch_folder(work / "batch_000"))


def test_default_levels_skip_levels_absent_from_the_store_and_ignore_non_targets(work_dir):
    batch = load_batch_folder(work_dir / "batch_000")
    split = misfit_split(batch)      # default levels name STAAT, KREIS, ZENSUS1km: not in the store
    assert set(split["level"]) == {"ZENSUS100m"}
    # Only control targets: the incidence's STAAT/WELT/sample_weight/group_* columns are not controls.
    assert set(split["control"]) == set(batch.control_spec["target"])
    assert set(split["control_geography"]) == {"ZENSUS100m"}


@pytest.mark.parametrize("last_row_names, extra_integer", [("second", 8), ("first", 0)])
def test_last_checkpoint_of_the_checkpoints_frame_is_read(work_dir, last_row_names, extra_integer):
    first = pd.read_hdf(_store_path(work_dir), _FIRST_KEY)
    second = first.assign(integer_weight=first["integer_weight"] + 1)
    order = ["first", "second"] if last_row_names == "second" else ["second", "first"]
    names = {"first": "sub_balancing.geography=ZENSUS100m", "second": "final"}
    _write_keys(work_dir, {_SECOND_KEY: second,
                           "/checkpoints": _checkpoints([names[o] for o in order])})
    table = read_weight_table(_store_path(work_dir), "ZENSUS100m")
    assert table.columns.tolist() == ["H_ID", "ZENSUS100m", "balanced_weight", "integer_weight"]
    assert table.index.tolist() == list(range(len(table)))
    assert table["integer_weight"].sum() == first["integer_weight"].sum() + extra_integer


def test_several_weight_checkpoints_without_checkpoints_frame_are_refused(work_dir):
    first = pd.read_hdf(_store_path(work_dir), _FIRST_KEY)
    _write_keys(work_dir, {_SECOND_KEY: first})
    with pytest.raises(ValueError, match="ZENSUS100m_weights"):
        read_weight_table(_store_path(work_dir), "ZENSUS100m")
    with pytest.raises(ValueError, match="ZENSUS100m_weights"):
        misfit_split(load_batch_folder(work_dir / "batch_000"), levels=("ZENSUS100m",))


def test_staat_style_weights_with_h_id_as_index_and_column(work_dir):
    # Real STAAT weights carry H_ID as index name AND as a column, plus seed-level columns.
    ids = pd.Index([1, 2, 3, 4], name="H_ID")
    staat = pd.DataFrame({
        "STAAT": 1, "preliminary_balanced_weight": [0.4, 1.0, 2.0, 3.0],
        "sample_weight": [0.5, 1.5, 2.0, 4.0], "H_ID": ids.to_numpy(),
        "balanced_weight": [0.5, 1.5, 2.0, 4.0], "integer_weight": [1, 1, 2, 4]}, index=ids)
    totals = pd.read_csv(work_dir / "batch_000" / "data" / "control_totals_ZENSUS100m.csv")
    spec = load_batch_folder(work_dir / "batch_000").control_spec
    controls = totals.drop(columns="ZENSUS100m").sum().to_frame().T
    controls.columns = [c + "_target" for c in controls.columns]
    controls = controls[spec["target"]]
    controls.index = pd.Index([1], name="STAAT")
    _write_keys(work_dir, {"/STAAT_weights/final_seed_balancing": staat,
                           "/STAAT_controls/setup_data_structures": controls})
    table = read_weight_table(_store_path(work_dir), "STAAT")
    assert table.columns.tolist() == ["H_ID", "STAAT", "balanced_weight", "integer_weight"]
    assert table.index.tolist() == [0, 1, 2, 3] and table["H_ID"].tolist() == [1, 2, 3, 4]
    split = misfit_split(load_batch_folder(work_dir / "batch_000"),
                         levels=("STAAT", "ZENSUS100m")).set_index(["level", "control"])
    m = split.loc[("STAAT", "M_AGE_18_plus_ZENSUS100m_target")]      # incidence [1, 0, 0, 1]
    assert [m["target_sum"], m["float_sum"], m["integer_sum"]] == pytest.approx([3.0, 4.5, 5.0])
    assert [m["balance_wape"], m["round_wape"], m["total_wape"]] == pytest.approx([0.5, 1 / 6, 2 / 3])


def test_weights_of_a_household_missing_from_the_incidence_are_refused(work_dir):
    weights = pd.read_hdf(_store_path(work_dir), _FIRST_KEY)
    weights.loc[0, "H_ID"] = 99
    _write_keys(work_dir, {_FIRST_KEY: weights})
    with pytest.raises(ValueError, match="99"):
        misfit_split(load_batch_folder(work_dir / "batch_000"), levels=("ZENSUS100m",))


_WRAP = "INFO - populationsim.integerizing.wrappers - do_simul_integerizing "


def _write_log(work_dir, lines):
    (work_dir / "batch_000" / "output" / "populationsim.log").write_text(
        "\n".join(_WRAP + line for line in lines) + "\n", encoding="utf-8")


def test_solver_paths_per_level(work_dir):
    table = solver_path_table(work_dir).set_index("level")
    assert table.loc["KREIS", ["n_parents", "first_optimal"]].tolist() == [1, 1]
    assert table.loc["ZENSUS1km", ["n_parents", "first_infeasible", "retry_not_solved",
                                   "sequential_fallback", "infeasible_subzones",
                                   "non_simultaneous"]].tolist() == [1, 1, 1, 1, 1, 1]
    assert table.loc["ZENSUS100m", ["n_parents", "first_optimal", "first_infeasible",
                                    "first_not_solved", "retry_optimal", "sequential_fallback",
                                    "single_feasible_subzone", "infeasible_subzones",
                                    "non_simultaneous"]].tolist() == [3, 1, 1, 1, 1, 0, 1, 2, 1]


def test_no_infeasible_with_fallback_line_is_a_sequential_fallback(work_dir):
    _write_log(work_dir, ["failed for ZENSUS1km_K1 status INFEASIBLE. ",
                          "failed but found no infeasible sub zones ZENSUS1km_K1. ",
                          "falling back to sequential integerizing for ZENSUS1km_K1."])
    row = solver_path_table(work_dir).set_index("level").loc["ZENSUS100m"]
    assert row[["sequential_fallback", "single_feasible_subzone", "no_infeasible_subzones",
                "non_simultaneous"]].tolist() == [1, 0, 1, 1]


def test_smart_rounded_outcome_is_non_simultaneous(work_dir):
    _write_log(work_dir, ["failed for ZENSUS1km_K1 status INFEASIBLE. ",
                          "returning smart rounded weights for ZENSUS1km_K1."])
    row = solver_path_table(work_dir).set_index("level").loc["ZENSUS100m"]
    assert row[["all_subzones_infeasible", "non_simultaneous"]].tolist() == [1, 1]


@pytest.mark.parametrize("line, message", [
    ("succeeded for ZENSUS1km_K1 status WEIRD. ", "WEIRD"),
    ("failed for GEMEINDE_7 status INFEASIBLE. ", "GEMEINDE_7"),
    ("failed for ZENSUS100m_A status INFEASIBLE. ", "ZENSUS100m_A"),
])
def test_unknown_status_or_unmatched_label_is_refused(work_dir, line, message):
    _write_log(work_dir, [line])
    with pytest.raises(ValueError, match=message):
        solver_path_table(work_dir)


def test_missing_solver_log_is_refused(work_dir):
    (work_dir / "batch_000" / "output" / "populationsim.log").unlink()
    with pytest.raises(FileNotFoundError):
        solver_path_table(work_dir)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


def test_failed_parent_without_outcome_line_is_warned(work_dir, caplog):
    _write_log(work_dir, ["failed for ZENSUS1km_K1 status INFEASIBLE. "])
    with caplog.at_level("WARNING"):
        solver_path_table(work_dir)
    assert any("1 failed first attempts but 0 outcomes" in m for m in _warnings(caplog))


def test_longest_geography_prefix_decides_the_level(work_dir):
    settings = work_dir / "batch_000" / "configs" / "settings.yaml"
    settings.write_text("geographies: [A, A_B, C]\n", encoding="utf-8")
    _write_log(work_dir, ["succeeded for A_B_7 status OPTIMAL. ",
                          "succeeded for A_1 status OPTIMAL. "])
    table = solver_path_table(work_dir).set_index("level")
    assert table["n_parents"].to_dict() == {"C": 1, "A_B": 1}


def test_unrecognised_line_is_counted_and_warned_not_raised(work_dir, caplog):
    _write_log(work_dir, ["succeeded for ZENSUS1km_K1 status OPTIMAL. ",
                          "something new happened for ZENSUS1km_K1"])
    with caplog.at_level("WARNING"):
        table = solver_path_table(work_dir)
    assert table["n_parents"].tolist() == [1]
    messages = _warnings(caplog)
    assert any("something new happened" in m for m in messages)
    assert any("1 unrecognised solver line(s)" in m for m in messages)


def test_outcome_lines_without_first_attempt_are_warned(work_dir, caplog):
    _write_log(work_dir, ["retry succeeded for ZENSUS1km_K1 status OPTIMAL. "])
    with caplog.at_level("WARNING"):
        solver_path_table(work_dir)
    assert any("no first attempt" in m for m in _warnings(caplog))


def test_duplicate_no_infeasible_label_is_warned_and_counted_per_occurrence(work_dir, caplog):
    _write_log(work_dir, ["failed for ZENSUS1km_K1 status INFEASIBLE. ",
                          "failed but found no infeasible sub zones ZENSUS1km_K1. ",
                          "failed but found no infeasible sub zones ZENSUS1km_K1. "])
    with caplog.at_level("WARNING"):
        row = solver_path_table(work_dir).set_index("level").loc["ZENSUS100m"]
    assert row["single_feasible_subzone"] == 2
    assert any("more than one" in m for m in _warnings(caplog))
