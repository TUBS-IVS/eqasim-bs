"""One employment level for every employment control (#658, Task A5, owner decision D-emp).

All values are SYNTHETIC test data. Three 1 km parents with one 100 m cell each: P1 and P2 lie in
Kreis 03101, P3 in Kreis 03158. Every Kreis census source column is 100, so a family of four
``work_by_employment`` classes sums to 400 and the seven ``employment_status`` classes to 700.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from braunschweig.popsim import control_spec, employment_grid, employment_level
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
    result = employment_level.reconcile_employed_level(
        values, ["employed_work", "employed_nowork"], ["nonemployed_work", "nonemployed_nowork"], 110.0)
    assert result.values == pytest.approx({"employed_work": 66.0, "employed_nowork": 44.0,
                                           "nonemployed_work": 72.0, "nonemployed_nowork": 18.0})
    assert result.capped is False and result.unchanged_reason is None


def test_reconcile_caps_at_the_universe_and_reports_it():
    values = {"employed_work": 60.0, "employed_nowork": 40.0,
              "nonemployed_work": 80.0, "nonemployed_nowork": 20.0}
    result = employment_level.reconcile_employed_level(
        values, ["employed_work", "employed_nowork"], ["nonemployed_work", "nonemployed_nowork"], 250.0)
    assert result.values == pytest.approx({"employed_work": 120.0, "employed_nowork": 80.0,
                                           "nonemployed_work": 0.0, "nonemployed_nowork": 0.0})
    assert result.capped is True and result.unchanged_reason is None


@pytest.mark.parametrize("values, target, reason", [
    ({"e": 0.0, "n": 10.0}, 4.0, employment_level.EMPLOYED_GROUP_ZERO),
    ({"e": 10.0, "n": 0.0}, 4.0, employment_level.OTHER_GROUP_ZERO),
])
def test_reconcile_leaves_a_part_unchanged_when_a_group_must_carry_mass_but_is_empty(values, target, reason):
    result = employment_level.reconcile_employed_level(values, ["e"], ["n"], target)
    assert result.values == values and result.unchanged_reason == reason and result.capped is False


def test_reconcile_zero_total_is_empty_not_capped():
    result = employment_level.reconcile_employed_level({"e": 0.0, "n": 0.0}, ["e"], ["n"], 30.0)
    assert result.values == {"e": 0.0, "n": 0.0}
    assert result.capped is False and result.unchanged_reason == employment_level.EMPTY_TOTAL


def test_bridge_is_the_weighted_ratio():
    persons, households = _persons()
    assert employment_level.employed_definition_bridge(persons, households, min_age=14) == 0.75


def test_bridge_ignores_persons_below_min_age():
    persons, households = _persons(extra_age=10)
    assert employment_level.employed_definition_bridge(persons, households, min_age=14) == 0.75
    # Without the age bound the weight-7 child is employed by P_TAET only: 3 / 11.
    assert employment_level.employed_definition_bridge(persons, households, min_age=0) == \
        pytest.approx(3 / 11)


def test_bridge_works_on_an_employment_status_only_seed(caplog):
    persons, households = _persons()
    persons = persons.drop(columns="work_by_employment")
    persons["employment_status"] = ["vollzeit", "nicht_erwerbstaetig", "in_ausbildung", "nicht_erwerbstaetig"]
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.employment_level"):
        report = employment_level.employed_bridge_report(
            persons, households, grid_min_age=16, family_min_age=14)
    assert report.bridge == 0.75 and report.source == "employment_status"
    assert "employment_status" in caplog.text  # the source used is logged


def test_grid_universe_is_the_employed_grids_lower_age_and_the_report_compares_it(caplog):
    # The grid's lowest band starts at 16: the band table, the grid builder and the rendered control agree.
    expression = next(c for c in control_spec.employment_grid_controls() if c.name == "EMPLOYED_M_16_29_agg")
    assert employment_level.employed_grid_min_age() == employment_grid.MIN_EMPLOYMENT_AGE == 16
    assert "HP_ALTER>15" in expression.seed_expressions["mid"]
    persons, households = _persons()
    # A 15-year-old apprentice (weight 2) is employed by P_TAET and in an employed class.
    persons = pd.concat([persons, pd.DataFrame({
        "H_ID": [5], "HP_ALTER": [15], "P_TAET": [8], "work_by_employment": ["employed_work"]})],
        ignore_index=True)
    households = pd.concat([households, pd.DataFrame({"H_ID": [5], "H_GEW": [2.0]})], ignore_index=True)
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.employment_level"):
        report = employment_level.employed_bridge_report(
            persons, households, grid_min_age=employment_level.employed_grid_min_age(), family_min_age=14)
    assert report.bridge == 0.75                      # persons aged 16+: 3 / 4
    assert report.bridge_family_universe == pytest.approx(5 / 6)   # persons aged 14+: 5 / 6
    assert report.below_grid_share == pytest.approx(2 / 5)         # weight 2 of employed-class weight 5
    lines = [r.getMessage() for r in caplog.records if "bridge" in r.getMessage()]
    assert len(lines) == 1 and "0.7500" in lines[0] and "0.8333" in lines[0] and "40.00%" in lines[0]


def test_family_labels_must_equal_the_registry_categories_both_ways(monkeypatch):
    import dataclasses
    # Today's families partition their registry entries.
    for family, (employed, other) in employment_level.EMPLOYMENT_FAMILIES.items():
        entry = next(e for e in kac.REGISTRY if e.name == family)
        assert set(employed) | set(other) == {label for label, _ in entry.categories}
    # A new registry category that nobody classified must fail, not land in "other".
    entry = next(e for e in kac.REGISTRY if e.name == "employment_status")
    grown = dataclasses.replace(entry, categories=entry.categories + (("neu", "== 'neu'"),))
    monkeypatch.setattr(kac, "REGISTRY", tuple(grown if e is entry else e for e in kac.REGISTRY))
    with pytest.raises(ValueError, match="neu"):
        employment_level.build_employment_families()


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


def _kreis_inputs(controls_df, overrides=None):
    names = set(controls_df["control_field"])
    catalog = [c for c in control_spec.tier3_controls() if c.name == "employed"]
    catalog += control_spec.attribute_kreis_controls(
        [e for e in kac.REGISTRY if e.name in ("work_by_employment", "employment_status")])
    controls_map = controls_builder._kreis_controls_map(
        [c for c in catalog if f"{c.name}_{c.geography}" in names])
    sources = sorted({c for cols in controls_map.values() for c in cols})
    table = pd.DataFrame({"ARS_kreis": ["03101", "03158"], **{c: [100.0, 100.0] for c in sources}})
    for column, pair in (overrides or {}).items():
        assert column in table.columns, column
        table[column] = pair
    return table, controls_map


def _assemble(tmp_path, controls_df, base_cols=EMPLOYED, overrides=None, **kwargs) -> pd.DataFrame:
    """One batch holding all three parents; its KREIS control table indexed by Kreis."""
    table, controls_map = _kreis_inputs(controls_df, overrides)
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


_UNEQUAL = {  # one value per Kreis (03101, 03158), the same in both Kreise
    "work_by_employment_employed_work": 60.0, "work_by_employment_employed_nowork": 40.0,
    "work_by_employment_nonemployed_work": 80.0, "work_by_employment_nonemployed_nowork": 20.0,
    "employment_status_vollzeit": 50.0, "employment_status_teilzeit": 30.0,
    "employment_status_geringfuegig": 10.0, "employment_status_in_ausbildung": 10.0,
    "employment_status_sonstiges": 40.0, "employment_status_erwerbstaetig_unspec": 10.0,
    "employment_status_nicht_erwerbstaetig": 150.0}


def test_batch_keeps_the_proportions_inside_each_group_and_writes_no_scratch_column(tmp_path):
    overrides = {col: [v, v] for col, v in _UNEQUAL.items()}
    out = _assemble(tmp_path, _controls_df(), overrides=overrides, employed_bridge=0.8)
    assert "employed_grid_sum_KREIS" not in out.columns
    for kreis in ("03101", "03158"):
        row, target = out.loc[kreis], 0.8 * _GRID_SUM[kreis]
        for family, (employed, other) in employment_level.EMPLOYMENT_FAMILIES.items():
            for group, mass in ((employed, target), (other, None)):
                cols = [f"{family}_{label}_KREIS" for label in group]
                old = [_UNEQUAL[f"{family}_{label}"] for label in group]
                assert row[cols].tolist() == pytest.approx(
                    [v * (row[cols].sum() / sum(old)) for v in old])  # same inner proportions
                if mass is not None:
                    assert row[cols].sum() == pytest.approx(mass)
            family_cols = [c for c in out.columns if c.startswith(family + "_")]
            assert row[family_cols].sum() == pytest.approx(sum(
                v for k, v in _UNEQUAL.items() if k.startswith(family + "_")))


def test_batch_leaves_a_part_with_an_empty_group_unchanged_and_logs_the_fallback(tmp_path, caplog):
    zero = {f"work_by_employment_{label}": [100.0, 0.0] for label in ("employed_work", "employed_nowork")}
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.employment_level"):
        out = _assemble(tmp_path, _controls_df(), overrides=zero, employed_bridge=0.8)
    # Kreis 03158: the employed classes are 0 but must carry the target -> unchanged; 03101 is reconciled.
    assert out.loc["03158", _employed_columns("work_by_employment")].tolist() == [0, 0]
    assert out.loc["03101", _employed_columns("work_by_employment")].sum() == pytest.approx(0.8 * 30)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("03158" in m and "work_by_employment" in m and employment_level.EMPLOYED_GROUP_ZERO in m
               for m in warnings), warnings
    assert any("unchanged_part" in r.getMessage() and "primary" in r.getMessage() for r in caplog.records)


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
    lines = [r.getMessage() for r in caplog.records if "Zensus employment level on" in r.getMessage()]
    assert len(lines) == 1, lines
    assert "bridge" in lines[0] and "capped" in lines[0]


def test_batch_skips_an_inactive_family_and_raises_without_the_grid(tmp_path, caplog):
    only_work = _controls_df(families=("work_by_employment",))
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.employment_level"):
        out = _assemble(tmp_path, only_work, employed_bridge=0.8)
    assert out.loc["03101", _employed_columns("work_by_employment")].sum() == pytest.approx(0.8 * 30)
    assert "employment_status" in caplog.text  # the skipped family is logged
    rate = [r.getMessage() for r in caplog.records if "unchanged_part" in r.getMessage()]
    # the absent family is counted and named in the rate line, not silently left out of it
    assert len(rate) == 1 and "not active" in rate[0] and "employment_status" in rate[0]
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
