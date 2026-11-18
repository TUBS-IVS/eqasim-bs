"""One employment level for every employment control (#658, Task A5, owner decision D-emp).

All values are SYNTHETIC test data. Three 1 km parents with one 100 m cell each: P1 and P2 lie in
Kreis 03101, P3 in Kreis 03158. Every Kreis census source column is 100, so a family of four
``work_by_employment`` classes sums to 400 and the seven ``employment_status`` classes to 700.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from braunschweig.popsim import control_spec, employment_level
from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.popsim.mid import batch_folders
from braunschweig.popsim.stage import controls_builder

ARS = "RegionalSchlussel_ARS"
ENTRY = {e.name: e for e in kac.REGISTRY}
EMPLOYED = [c.name for c in control_spec.employment_grid_controls()]
_PARENTS = (("P1", "03101", 1.0), ("P2", "03101", 2.0), ("P3", "03158", 5.0))
# Employed grid sum per Kreis part: ten columns of the per-parent value.
_GRID_SUM = {"03101": 10 * (1.0 + 2.0), "03158": 10 * 5.0}


def _persons(extra_age: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Four persons in households of weight 1, 1, 2, 1; employed by P_TAET: the first three."""
    persons = pd.DataFrame({
        "H_ID": [1, 2, 3, 4], "HP_ALTER": [30, 40, 50, 60], "P_TAET": [1, 2, 3, 9],
        "work_by_employment": ["employed_work", "nonemployed_work", "employed_nowork",
                               "nonemployed_nowork"]})
    households = pd.DataFrame({"H_ID": [1, 2, 3, 4], "H_GEW": [1.0, 1.0, 2.0, 1.0]})
    if extra_age is not None:
        persons = pd.concat([persons, pd.DataFrame({
            "H_ID": [5], "HP_ALTER": [extra_age], "P_TAET": [1],
            "work_by_employment": ["nonemployed_nowork"]})], ignore_index=True)
        households = pd.concat([households, pd.DataFrame({"H_ID": [5], "H_GEW": [7.0]})],
                               ignore_index=True)
    return persons, households


def test_reconcile_keeps_total_and_inner_proportions():
    values = {"employed_work": 60.0, "employed_nowork": 40.0,
              "nonemployed_work": 80.0, "nonemployed_nowork": 20.0}
    out, capped = employment_level.reconcile_employed_level(
        values, ["employed_work", "employed_nowork"], ["nonemployed_work", "nonemployed_nowork"], 110.0)
    assert out == pytest.approx({"employed_work": 66.0, "employed_nowork": 44.0,
                                 "nonemployed_work": 72.0, "nonemployed_nowork": 18.0})
    assert capped is False


def test_reconcile_caps_at_the_universe_and_reports_it():
    values = {"employed_work": 60.0, "employed_nowork": 40.0,
              "nonemployed_work": 80.0, "nonemployed_nowork": 20.0}
    out, capped = employment_level.reconcile_employed_level(
        values, ["employed_work", "employed_nowork"], ["nonemployed_work", "nonemployed_nowork"], 250.0)
    assert out == pytest.approx({"employed_work": 120.0, "employed_nowork": 80.0,
                                 "nonemployed_work": 0.0, "nonemployed_nowork": 0.0})
    assert capped is True


def test_reconcile_raises_when_a_group_must_carry_mass_but_is_empty():
    with pytest.raises(ValueError, match="employed"):
        employment_level.reconcile_employed_level(
            {"e": 0.0, "n": 10.0}, ["e"], ["n"], 4.0)


def test_bridge_is_the_weighted_ratio():
    persons, households = _persons()
    assert employment_level.employed_definition_bridge(persons, households, min_age=14) == 0.75


def test_bridge_ignores_persons_below_min_age():
    persons, households = _persons(extra_age=10)
    assert employment_level.employed_definition_bridge(persons, households, min_age=14) == 0.75
    # Without the age bound the weight-7 child is employed by P_TAET only: 3 / 11.
    assert employment_level.employed_definition_bridge(persons, households, min_age=0) == \
        pytest.approx(3 / 11)


def test_bridge_raises_without_any_employed_person():
    persons, households = _persons()
    persons["P_TAET"] = 9
    with pytest.raises(ValueError, match="P_TAET"):
        employment_level.employed_definition_bridge(persons, households, min_age=14)


# --------------------------------------------------------------------------- #
# Batch level
# --------------------------------------------------------------------------- #

def _cells() -> pd.DataFrame:
    rows = []
    for parent, kreis, value in _PARENTS:
        row = {"ZENSUS100m": f"c_{parent}", "ZENSUS1km": parent, ARS: kreis + "0000000",
               "POP_TOTAL_100m_adj": 100.0}
        row.update({col: value for col in EMPLOYED})
        rows.append(row)
    return pd.DataFrame(rows)


def _controls_df(*, grid: bool = True, families=("work_by_employment", "employment_status")):
    catalog = [c for c in control_spec.tier3_controls() if c.name == "employed"]
    catalog += control_spec.attribute_kreis_controls([ENTRY[name] for name in families])
    if grid:
        catalog += control_spec.employment_grid_controls()
    return control_spec.render_catalog_csv(catalog, "mid")


def _kreis_inputs(controls_df):
    names = set(controls_df["control_field"])
    catalog = [c for c in control_spec.tier3_controls() if c.name == "employed"]
    catalog += control_spec.attribute_kreis_controls(
        [e for e in kac.REGISTRY if e.name in ("work_by_employment", "employment_status")])
    controls_map = controls_builder._kreis_controls_map(
        [c for c in catalog if f"{c.name}_{c.geography}" in names])
    sources = sorted({c for cols in controls_map.values() for c in cols})
    table = pd.DataFrame({"ARS_kreis": ["03101", "03158"], **{c: [100.0, 100.0] for c in sources}})
    return table, controls_map


def _assemble(tmp_path, controls_df, base_cols=EMPLOYED, **kwargs) -> pd.DataFrame:
    """One batch holding all three parents; its KREIS control table indexed by Kreis."""
    table, controls_map = _kreis_inputs(controls_df)
    cells = _cells()
    batch_folders.assemble_batch_folder(
        tmp_path / "b", cells, base_cols, controls_df,
        pd.DataFrame({"H_ID": [1]}), pd.DataFrame({"H_ID": [1], "P_ID": [1]}),
        settings_yaml="x: 1\n", logging_yaml="version: 1\n",
        kreis_table=table, kreis_controls_map=controls_map, **kwargs)
    frame = pd.read_csv(tmp_path / "b" / "data" / "control_totals_KREIS.csv", dtype={"KREIS": str})
    return frame.set_index("KREIS")


def _employed_columns(family: str) -> list[str]:
    employed = (employment_level.EMPLOYMENT_FAMILIES[family][0])
    return [f"{family}_{label}_KREIS" for label in employed]


def test_batch_employed_classes_get_the_zensus_level_of_the_grid(tmp_path):
    out = _assemble(tmp_path, _controls_df(), employed_bridge=0.8)
    for kreis in ("03101", "03158"):
        target = 0.8 * _GRID_SUM[kreis]
        for family, n_all in (("work_by_employment", 400.0), ("employment_status", 700.0)):
            employed = _employed_columns(family)
            row = out.loc[kreis]
            assert row[employed].sum() == pytest.approx(target)
            # The family keeps its universe (the sum of its columns) and the inner proportions.
            all_cols = [c for c in out.columns if c.startswith(family + "_")]
            assert row[all_cols].sum() == pytest.approx(n_all)
            # The employed classes started equal (all 100), so they stay equal.
            assert row[employed].tolist() == pytest.approx([target / len(employed)] * len(employed))


def test_batch_legacy_without_bridge_keeps_the_survey_values(tmp_path):
    out = _assemble(tmp_path, _controls_df())
    assert set(out["work_by_employment_employed_work_KREIS"]) == {100}
    assert set(out["employment_status_vollzeit_KREIS"]) == {100}


def test_batch_caps_a_part_whose_target_exceeds_the_universe(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.employment_level"):
        out = _assemble(tmp_path, _controls_df(), employed_bridge=10.0)
    # Kreis 03158: target 500 > 400 persons of work_by_employment -> capped, others 0, never negative.
    row = out.loc["03158"]
    assert row["work_by_employment_employed_work_KREIS"] == pytest.approx(200.0)
    assert row["work_by_employment_nonemployed_work_KREIS"] == pytest.approx(0.0)
    assert (out.drop(columns=[c for c in out.columns if not c.endswith("_KREIS")]) >= 0).all().all()
    lines = [r.getMessage() for r in caplog.records if "employment_level" in r.getMessage()]
    assert len(lines) == 1, lines
    assert "bridge" in lines[0] and "capped" in lines[0]


def test_batch_skips_an_inactive_family_and_raises_without_the_grid(tmp_path, caplog):
    only_work = _controls_df(families=("work_by_employment",))
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.employment_level"):
        out = _assemble(tmp_path, only_work, employed_bridge=0.8)
    assert out.loc["03101", _employed_columns("work_by_employment")].sum() == pytest.approx(0.8 * 30)
    assert "employment_status" in caplog.text  # the skipped family is logged
    with pytest.raises(ValueError, match="EMPLOYED_M_16_29_agg"):
        _assemble(tmp_path / "nogrid", _controls_df(grid=False), base_cols=[], employed_bridge=0.8)


# --------------------------------------------------------------------------- #
# run_popsim_mid wiring
# --------------------------------------------------------------------------- #

class _Stop(Exception):
    """Raised by the spy once it has seen the arguments of the first assemble call."""


@pytest.mark.parametrize("stratify", [False, True])
def test_run_popsim_mid_computes_the_bridge_once_and_hands_it_to_both_paths(
        monkeypatch, tmp_path, stratify):
    seen: list[dict] = []

    def spy(*args, **kwargs):
        seen.append(kwargs)
        raise _Stop

    monkeypatch.setattr(batch_folders, "assemble_batch_folder", spy)
    monkeypatch.setattr(batch_folders, "dominant_stratum_for_1km", lambda cells, source: ({"P1": "s"}, 0.0))
    monkeypatch.setattr(batch_folders, "filter_seed_to_stratum", lambda hh, p, stratum, source: (hh, p))
    controls_df = _controls_df()
    table, controls_map = _kreis_inputs(controls_df)
    persons, households = _persons()
    persons["P_ID"] = 1
    extra = dict(source=object(), stratify_regiostar=True) if stratify else {}
    for source, expected in (("zensus", 0.75), ("survey", None)):
        with pytest.raises(_Stop):
            batch_folders.run_popsim_mid(
                _cells()[:1], EMPLOYED, controls_df, households, persons, work_dir=tmp_path,
                settings_yaml="x: 1\n", logging_yaml="version: 1\n", max_cells=10, run_one=None,
                kreis_table=table, kreis_controls_map=controls_map,
                employment_level_source=source, **extra)
        assert seen[-1]["employed_bridge"] == expected


def test_run_popsim_mid_rejects_an_unknown_employment_level_source(tmp_path):
    with pytest.raises(ValueError, match="employment_level_source"):
        batch_folders.run_popsim_mid(
            _cells(), EMPLOYED, _controls_df(), pd.DataFrame({"H_ID": [1]}),
            pd.DataFrame({"H_ID": [1], "P_ID": [1]}), work_dir=tmp_path, settings_yaml="x: 1\n",
            logging_yaml="version: 1\n", max_cells=10, run_one=None, employment_level_source="bogus")
