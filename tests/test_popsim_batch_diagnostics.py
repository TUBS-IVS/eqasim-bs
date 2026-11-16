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
from braunschweig.analysis.popsim_batch_diagnostics.reuse import (  # noqa: E402
    reuse_by_zone, reuse_summary, shares_by_attribute, usage_counts, weight_class_shares)
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
