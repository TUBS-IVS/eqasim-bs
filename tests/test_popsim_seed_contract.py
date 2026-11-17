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
