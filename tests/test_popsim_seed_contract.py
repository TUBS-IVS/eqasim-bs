"""Contract check before PopulationSim (issue #647, goal G6)."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import control_spec as cs
from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.popsim.control_groups import UNIVERSE_RATIO_TOLERANCE, unmatched_controls
from braunschweig.popsim.seed_contract import (
    check_household_group_totals, check_seed_coverage)
from tests.popsim_batch_fixture import CONTROLS, SEED_HOUSEHOLDS, SEED_PERSONS, TOTAL, _controls_frame

SPEC = _controls_frame([])


def _resolved():
    households = SEED_HOUSEHOLDS.copy()
    persons = SEED_PERSONS.copy()
    persons["HP_SEX"] = [1, 2, 1, 1, 1, 2]          # sex 3 resolved
    households["H_MIETE"] = [1, 2, 2, 1]            # tenure 9 resolved
    households["haustyp"] = [2, 1, 1, 3]            # haustyp 95 resolved
    return households, persons


def test_coverage_passes_on_resolved_seed(caplog):
    households, persons = _resolved()
    with caplog.at_level(logging.INFO):
        table = check_seed_coverage(households, persons, SPEC, TOTAL)
    assert table[["n_uncovered", "n_multi_hit"]].to_numpy().sum() == 0
    assert set(table["group"]) == {"age_sex", "tenure", "building_type"}
    assert any("coverage" in r.message and r.levelno == logging.INFO for r in caplog.records)


def test_production_education_entries_keep_the_seed_contract():
    """The production entry set (student education entries on, education_18plus dropped, A3,
    ADR-0211): the education-by-age bands and the two student education entries together cover
    every person exactly once, and the soft student entries every person aged 18 to 29.

    The run-server smoke stopped here: the bands 0-5 and 6-17 alone left every adult uncovered.
    """
    from braunschweig.popsim.stage import active_kreis_entries
    from tests.stage_context import KreisToggleContext
    active = active_kreis_entries(KreisToggleContext(), "mid")
    education = [entry for entry in active
                 if entry.name in kac.EDUCATION_BY_AGE_ENTRY_NAMES or entry.name in kac.STUDENT_ENTRY_NAMES]
    assert "education_18plus" not in {entry.name for entry in education}
    rendered = cs.render_catalog_csv(
        cs.controls_for_seed(cs.attribute_kreis_controls(education), "mid"), "mid")
    spec = pd.concat([SPEC, rendered], ignore_index=True)
    households, persons = _resolved()
    # ages 22 (a student), 40, 8, 70, 50 and 3: every band and both student age ranges
    persons = persons.assign(
        HP_ALTER=[22, 40, 8, 70, 50, 3],
        education_flag=["edu", "noedu", "edu", "noedu", "edu", "edu"],
        student_education_18_29=["student_edu", "other_noedu", "other_edu", "other_noedu",
                                 "other_edu", "other_edu"],
        student_education_30plus=["student", "other_noedu", "other_edu", "other_noedu",
                                  "other_edu", "other_edu"],
        student_work_leg=["student_nowork"] + ["not_student"] * 5,
        student_season_ticket=["student_flatrate"] + ["not_student"] * 5)
    table = check_seed_coverage(households, persons, spec, TOTAL).set_index("group")
    assert table[["n_uncovered", "n_multi_hit"]].to_numpy().sum() == 0
    assert table.loc["education_band", "n_universe"] == 6
    assert table.loc[["student_work_leg", "student_season_ticket"], "n_universe"].tolist() == [1, 1]
    # A student education entry next to education_18plus counts the adults twice: refused.
    adult_band = [entry for entry in kac.REGISTRY if entry.name == "education_18plus"]
    both = pd.concat([spec, cs.render_catalog_csv(
        cs.controls_for_seed(cs.attribute_kreis_controls(adult_band), "mid"), "mid")],
        ignore_index=True)
    with pytest.raises(ValueError, match=r"education_band.*match several"):
        check_seed_coverage(households, persons, both, TOTAL)


def test_coverage_raises_on_unresolved_sex():
    households, persons = _resolved()
    persons["HP_SEX"] = SEED_PERSONS["HP_SEX"]       # one person with sex 3
    with pytest.raises(ValueError, match=r"age_sex.*1 of 6.*match no category"):
        check_seed_coverage(households, persons, SPEC, TOTAL)


def test_coverage_raises_on_haustyp_95():
    households, persons = _resolved()
    households["haustyp"] = SEED_HOUSEHOLDS["haustyp"]
    with pytest.raises(ValueError, match=r"building_type.*1 of 4.*\[4\]"):
        check_seed_coverage(households, persons, SPEC, TOTAL)


def test_coverage_raises_on_multi_hit():
    households, persons = _resolved()
    spec = SPEC.copy()
    spec.loc[spec["target"] == "EigentuemerHH_Tenure_ZENSUS100m_target", "expression"] = \
        "(households.H_MIETE >= 1)"
    with pytest.raises(ValueError, match=r"tenure.*match several"):
        check_seed_coverage(households, persons, spec, TOTAL)


def test_coverage_warn_mode_logs_and_returns(caplog):
    households, persons = _resolved()
    persons["HP_SEX"] = SEED_PERSONS["HP_SEX"]
    households["haustyp"] = SEED_HOUSEHOLDS["haustyp"]
    with caplog.at_level(logging.WARNING):
        table = check_seed_coverage(households, persons, SPEC, TOTAL, mode="warn")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2                         # one per failing group
    assert int(table.set_index("group").loc["age_sex", "n_uncovered"]) == 1


def test_unknown_mode_is_rejected():
    households, persons = _resolved()
    with pytest.raises(ValueError, match="seed_contract_mode"):
        check_seed_coverage(households, persons, SPEC, TOTAL, mode="ignore")


def test_group_without_control_is_skipped():
    households, persons = _resolved()
    spec = SPEC[~SPEC["target"].str.startswith("building_type_")]
    households["haustyp"] = SEED_HOUSEHOLDS["haustyp"]   # would fail if the group were checked
    table = check_seed_coverage(households, persons, spec, TOTAL)
    assert "building_type" not in set(table["group"])


def _cells(owner_extra: float = 0.0):
    cells = pd.DataFrame({
        "KREIS": ["k1", "k1", "k2"],
        "HH_TOTAL": [10.0, 10.0, 20.0],
        "MieterHH_Tenure": [6.0, 5.0, 12.0],
        "EigentuemerHH_Tenure": [4.0, 5.0, 8.0],
        "building_type_efh": [5.0, 5.0, 10.0],
        "building_type_mfh": [5.0, 5.0, 10.0],
    })
    cells.loc[2, "building_type_efh"] += owner_extra
    return cells


def _controls_df():
    frame = SPEC.copy()
    frame.loc[frame["target"] == TOTAL, "control_field"] = "HH_TOTAL_ZENSUS100m"
    frame["control_field"] = frame["control_field"].replace(
        {TOTAL[:-len("_target")]: "HH_TOTAL_ZENSUS100m"})
    return frame


def test_household_totals_pass_after_rescale():
    table = check_household_group_totals(_cells(), _controls_df(), TOTAL, kreis_col="KREIS")
    assert set(table["kreis"]) == {"k1", "k2", "ALL"}
    assert np.allclose(table["ratio"], 1.0)


def test_household_totals_raise_on_dwelling_targets():
    cells = _cells()
    cells.loc[:1, "building_type_efh"] *= 1.2        # dwellings exceed households in k1
    cells.loc[:1, "building_type_mfh"] *= 1.2
    with pytest.raises(ValueError, match=r"building_type, Kreis k1.*ratio 1\.2000"):
        check_household_group_totals(cells, _controls_df(), TOTAL, kreis_col="KREIS")
    table = check_household_group_totals(cells, _controls_df(), TOTAL, kreis_col="KREIS",
                                         mode="warn")
    assert table.set_index(["group", "kreis"]).loc[("building_type", "k1"), "ratio"] \
        == pytest.approx(1.2)


def test_household_totals_skip_group_without_cells_column(caplog):
    cells = _cells().drop(columns=["building_type_efh"])
    with caplog.at_level(logging.INFO):
        table = check_household_group_totals(cells, _controls_df(), TOTAL, kreis_col="KREIS")
    assert "building_type" not in set(table["group"])
    assert any("skipped (building_type)" in r.message for r in caplog.records)
    assert UNIVERSE_RATIO_TOLERANCE == 0.02


def test_legacy_path_warns_not_raises(caplog):
    # The stage runs the legacy (complete_members False) path in warn mode.
    from braunschweig.popsim import stage as popsim_stage
    households, persons = _resolved()
    persons["HP_SEX"] = SEED_PERSONS["HP_SEX"]
    with caplog.at_level(logging.INFO):
        popsim_stage._check_seed_contract(
            households, persons, _controls_df(), TOTAL, _cells(),
            kreis_col="KREIS", mode="error", complete_members=False, source_name="mid")
    assert any(r.levelno == logging.WARNING and "age_sex" in r.message for r in caplog.records)
    assert any("legacy" in r.message for r in caplog.records)


def test_production_control_spec_has_no_unmatched_control():
    catalog = cs.full_catalog(
        include_tiers=("tier0", "tier1", "tier2", "tier3"), include_employment_grid=True,
        include_ownership_grid=True,
        kreis_control_names=[c.name for c in kac.REGISTRY])
    spec = cs.render_catalog_csv(cs.controls_for_seed(catalog, "mid"), "mid")
    total = f"{cs.HH_TOTAL_CENSUS_COLUMN}_ZENSUS100m_target"
    assert unmatched_controls(spec, total) == []


def test_configure_declares_contract_mode_error_and_rejects_other_values():
    from braunschweig.popsim import stage as popsim_stage
    values = {}

    class _Recorder:
        def __init__(self, mode=None):
            self.mode = mode

        def config(self, key, default=None, **kwargs):
            values.setdefault(key, default)
            if key == popsim_stage.KEY_SEED_CONTRACT_MODE and self.mode is not None:
                return self.mode
            return default

        def stage(self, *args, **kwargs):
            return None

    popsim_stage.configure(_Recorder())
    assert values[popsim_stage.KEY_SEED_CONTRACT_MODE] == "error"
    with pytest.raises(ValueError, match="seed_contract_mode"):
        popsim_stage.configure(_Recorder("ignore"))


def test_error_mode_raises_on_complete_members_path():
    from braunschweig.popsim import stage as popsim_stage
    households, persons = _resolved()
    persons["HP_SEX"] = SEED_PERSONS["HP_SEX"]       # one unresolved sex
    with pytest.raises(ValueError, match="age_sex"):
        popsim_stage._check_seed_contract(
            households, persons, _controls_df(), TOTAL, _cells(), kreis_col="KREIS",
            mode="error", complete_members=True, source_name="mid")


def test_missing_kreis_and_ars_column_warns_or_raises(caplog):
    from braunschweig.popsim import stage as popsim_stage
    households, persons = _resolved()
    cells = _cells().drop(columns=["KREIS"])
    kwargs = dict(kreis_col="KREIS", ars_col="ARS", source_name="mid")
    with caplog.at_level(logging.WARNING):
        popsim_stage._check_seed_contract(
            households, persons, _controls_df(), TOTAL, cells, mode="warn",
            complete_members=False, **kwargs)
    assert any("skipped" in r.message and "ARS" in r.message for r in caplog.records)
    with pytest.raises(ValueError, match="neither the Kreis column"):
        popsim_stage._check_seed_contract(
            households, persons, _controls_df(), TOTAL, cells, mode="error",
            complete_members=True, **kwargs)


def test_kreis_derived_from_ars_when_kreis_column_is_absent():
    """No KREIS column but an ARS column: the Kreis comes from the ARS and the check runs.

    Defect caught: the ARS branch skipping the check (or mislabelling the Kreis) would let
    dwelling-based targets through on a cells frame that only carries the ARS.
    """
    from braunschweig.popsim import stage as popsim_stage
    households, persons = _resolved()
    cells = _cells().drop(columns=["KREIS"])
    cells["ARS"] = ["031010000000", "031010000000", "031020000000"]
    # The grid keys and weight the Kreis resolution reads (one 1 km parent per Kreis here).
    cells["ZENSUS100m"] = ["a", "b", "c"]
    cells["ZENSUS1km"] = ["P1", "P1", "P2"]
    cells["POP_TOTAL_100m_adj"] = [1.0, 1.0, 1.0]
    cells.loc[:1, ["building_type_efh", "building_type_mfh"]] *= 1.2   # dwellings > households
    with pytest.raises(ValueError, match=r"building_type, Kreis 03101.*ratio 1\.2000"):
        popsim_stage._check_seed_contract(
            households, persons, _controls_df(), TOTAL, cells, kreis_col="KREIS",
            ars_col="ARS", mode="error", complete_members=True, source_name="mid")


def test_border_cell_is_grouped_with_its_resolved_kreis():
    """A border cell counts toward its 1 km parent's dominant Kreis, not its raw ARS[:5].

    Defect caught: grouping by the raw ARS Kreis while the targets are raked per RESOLVED
    Kreis (``mid.resolved_kreis_per_cell``) compares two different Kreis universes. Here the
    raw grouping balances (03102: 12 + 8 = 20 of 20 households) and hides the resolved
    imbalance (03101: 32 of 30, 03102: 8 of 10).
    """
    from braunschweig.popsim import stage as popsim_stage
    households, persons = _resolved()
    cells = pd.DataFrame({
        "ZENSUS100m": ["a", "b", "c", "d"],
        "ZENSUS1km": ["P1", "P1", "P1", "P2"],
        "ARS": ["031010000000", "031010000000", "031020000000", "031020000000"],
        "POP_TOTAL_100m_adj": [100.0, 100.0, 1.0, 50.0],
        "HH_TOTAL": [10.0, 10.0, 10.0, 10.0],
        "MieterHH_Tenure": [5.0, 5.0, 5.0, 5.0],
        "EigentuemerHH_Tenure": [5.0, 5.0, 5.0, 5.0],
        "building_type_efh": [5.0, 5.0, 6.0, 4.0],
        "building_type_mfh": [5.0, 5.0, 6.0, 4.0],
        # A raw ARS[:5] column, as the employment grid leaves it on the stage's cells.
        "KREIS": ["03101", "03101", "03102", "03102"],
    })
    with pytest.raises(ValueError, match=r"building_type, Kreis 03101.*ratio 1\.0667"):
        popsim_stage._check_seed_contract(
            households, persons, _controls_df(), TOTAL, cells, kreis_col="KREIS",
            ars_col="ARS", mode="error", complete_members=True, source_name="mid")


def _ars_cells():
    cells = _cells().drop(columns=["KREIS"])
    cells["ARS"] = ["031010000000", "031010000000", "031020000000"]
    cells["ZENSUS100m"] = ["a", "b", "c"]
    cells["ZENSUS1km"] = ["P1", "P1", "P2"]
    cells["POP_TOTAL_100m_adj"] = [1.0, 1.0, 1.0]
    return cells


def test_missing_resolution_columns_keep_warn_mode_advisory_and_raise_in_error_mode(caplog):
    """ARS present but a Kreis-resolution column missing: warn skips, error raises by name.

    Defect caught: ``mid.resolved_kreis_per_cell`` raising a bare KeyError inside a check
    that is advisory in warn mode.
    """
    from braunschweig.popsim import stage as popsim_stage
    households, persons = _resolved()
    cells = _ars_cells().drop(columns=["ZENSUS1km", "POP_TOTAL_100m_adj"])
    kwargs = dict(kreis_col="KREIS", ars_col="ARS", source_name="mid")
    with caplog.at_level(logging.WARNING):
        popsim_stage._check_seed_contract(
            households, persons, _controls_df(), TOTAL, cells, mode="warn",
            complete_members=False, **kwargs)
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING
                and "ZENSUS1km" in r.message and "POP_TOTAL_100m_adj" in r.message]
    assert len(warnings) == 1
    with pytest.raises(ValueError, match=r"ZENSUS1km.*POP_TOTAL_100m_adj"):
        popsim_stage._check_seed_contract(
            households, persons, _controls_df(), TOTAL, cells, mode="error",
            complete_members=True, **kwargs)


def test_cells_without_resolved_kreis_are_counted_and_the_rest_still_checked(
        caplog, monkeypatch):
    """A NaN resolved Kreis is counted and logged, stays in the ALL ratio, and the check runs.

    Defect caught: the per-Kreis groupby silently dropping the unresolved cell.
    """
    from braunschweig.popsim import stage as popsim_stage
    households, persons = _resolved()
    cells = _ars_cells()
    cells.loc[:1, ["building_type_efh", "building_type_mfh"]] *= 1.2   # k 03101 imbalanced
    monkeypatch.setattr(
        popsim_stage.mid, "resolved_kreis_per_cell",
        lambda frame, **kw: pd.Series(["03101", "03101", np.nan], index=frame.index))
    with caplog.at_level(logging.INFO):
        with pytest.raises(ValueError, match=r"building_type, Kreis 03101.*ratio 1\.2000"):
            popsim_stage._check_seed_contract(
                households, persons, _controls_df(), TOTAL, cells, kreis_col="KREIS",
                ars_col="ARS", mode="error", complete_members=True, source_name="mid")
    assert any("seed_contract.unresolved_kreis" in r.message and "primary 2/3" in r.message
               for r in caplog.records)


def test_unbalanced_unresolved_cell_is_no_pseudo_kreis(caplog, monkeypatch):
    """An UNBALANCED cell without a resolved Kreis stays in ALL only, never in a 'nan' Kreis.

    Defect caught: astype(str) turning a NaN Kreis key into the group "nan", which was
    then checked against the tolerance although the log says such cells are left out.
    """
    from braunschweig.popsim import stage as popsim_stage
    households, persons = _resolved()
    cells = _ars_cells()
    cells.loc[2, ["building_type_efh", "building_type_mfh"]] *= 1.03   # unresolved cell only: 3 % off, ALL stays within tolerance
    monkeypatch.setattr(
        popsim_stage.mid, "resolved_kreis_per_cell",
        lambda frame, **kw: pd.Series(["03101", "03101", np.nan], index=frame.index))
    with caplog.at_level(logging.INFO):
        popsim_stage._check_seed_contract(
            households, persons, _controls_df(), TOTAL, cells, kreis_col="KREIS",
            ars_col="ARS", mode="error", complete_members=True, source_name="mid")
    assert not any(r.levelno >= logging.WARNING and "Kreis nan" in r.message
                   for r in caplog.records)


def test_household_group_totals_skip_nan_kreis_keys_but_keep_them_in_all(caplog):
    """The NaN-Kreis cell is out of the per-Kreis rows, counted in the log, and in ALL.

    Hand-computed from the fixture: household total 10 + 10 + 20 = 40; building_type target
    sum 10 + 10 + 30 (the unresolved cell scaled by 1.5) = 50, so the ALL ratio is 1.25.
    """
    from braunschweig.popsim.seed_contract import check_household_group_totals
    cells = _ars_cells()
    cells["KREIS"] = ["03101", "03101", np.nan]
    cells.loc[2, ["building_type_efh", "building_type_mfh"]] *= 1.5
    with caplog.at_level(logging.INFO):
        table = check_household_group_totals(cells, _controls_df(), TOTAL, kreis_col="KREIS",
                                             mode="warn")
    assert "nan" not in set(table["kreis"].astype(str))
    all_row = table[(table["group"] == "building_type") & (table["kreis"] == "ALL")].iloc[0]
    assert all_row["household_total"] == pytest.approx(40.0)
    assert all_row["target_sum"] == pytest.approx(50.0)
    assert all_row["ratio"] == pytest.approx(1.25)
    assert any("seed_contract.unresolved_kreis" in r.message and "primary 2/3" in r.message
               and "household total 20.0 of 40.0" in r.message for r in caplog.records)


def test_group_selection_is_shared_between_column_list_and_check():
    """The columns handed to the check are exactly the columns it reads.

    Defect caught: a group the check covers whose columns the caller's narrow frame drops
    would be skipped silently (counted as 'absent column').
    """
    from braunschweig.popsim.seed_contract import household_group_columns
    controls = _controls_df()
    needed = household_group_columns(controls, TOTAL)
    cells = _cells()
    narrow = cells[[c for c in needed if c in cells.columns] + ["KREIS"]]
    full = check_household_group_totals(cells, controls, TOTAL, kreis_col="KREIS")
    reduced = check_household_group_totals(narrow, controls, TOTAL, kreis_col="KREIS")
    pd.testing.assert_frame_equal(full, reduced)
    assert set(full["group"])      # something was checked


def test_missing_seed_column_names_group_table_and_column():
    households, persons = _resolved()
    households = households.drop(columns=["haustyp"])
    for mode in ("error", "warn"):               # a wiring defect raises in both modes
        with pytest.raises(ValueError, match=r"building_type.*households.*haustyp"):
            check_seed_coverage(households, persons, SPEC, TOTAL, mode=mode)


def test_wp1_threshold_default_is_the_shared_constant():
    from braunschweig.analysis.popsim_batch_diagnostics.run import Thresholds
    assert Thresholds().universe_ratio_tolerance == UNIVERSE_RATIO_TOLERANCE
