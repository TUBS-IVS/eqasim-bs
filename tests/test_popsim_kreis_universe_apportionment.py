"""KREIS person controls are apportioned to the batches by their OWN age universe (#658, Task A3).

All values are SYNTHETIC test data. Two Kreise: 03101 is split over both batches, 03158 lies in
batch B only. The batches differ in age structure and in household share, so a population share,
a household share and a universe share all give different numbers.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import control_spec, folders
from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.popsim.mid import batch_folders
from braunschweig.popsim.mid import kreis_controls
from braunschweig.popsim.stage import controls_builder

ARS = "RegionalSchlussel_ARS"
HH = control_spec.HH_TOTAL_CENSUS_COLUMN
KREIS_TARGET = 100.0
EDU_TARGET = 40.0
ENTRY = {e.name: e for e in kac.REGISTRY}
# Persons aged 6-17 / 18 and over per 1 km parent (one 100 m cell each), and households.
#            parent  Kreis    batch  6-17  18+  households
_PARENTS = (("P1", "03101", "A", 30.0, 70.0, 10.0),
            ("P2", "03101", "B", 10.0, 90.0, 30.0),
            ("P3", "03158", "B", 20.0, 80.0, 40.0))
_INFANTS = {"P1": 2.0, "P2": 4.0, "P3": 6.0}


def _cells(young=None) -> pd.DataFrame:
    """One 100 m cell per parent; ``young`` overrides the 6-17 persons per parent."""
    rows = []
    for parent, kreis, _batch, edu, adult, households in _PARENTS:
        row = {"ZENSUS100m": f"c_{parent}", "ZENSUS1km": parent, ARS: kreis + "0000000",
               "POP_TOTAL_100m_adj": edu + adult, HH: households, "B_agg": 1.0}
        row.update({f"{sex}_AGE_{y}": 0.0 for sex in "MF" for y in range(0, 101)})
        row["M_AGE_10"] = (young or {}).get(parent, edu)
        infants = _INFANTS[parent]  # aged 3, so the 0-5 universe is not empty
        row["F_AGE_3"] = infants
        row["M_AGE_30"] = adult - infants
        rows.append(row)
    return pd.DataFrame(rows)


def _universe_cells(cells=None):
    """Cells with the universe weight columns plus the mapping control -> weight column."""
    cells = _cells() if cells is None else cells
    entries = list(kac.REGISTRY)
    tier3 = [f"{c.name}_{c.geography}" for c in control_spec.tier3_controls()]
    universes = controls_builder.apportion_universes(entries, tier3)
    cells, column_of = controls_builder.add_universe_weight_columns(cells, universes)
    return cells, controls_builder.apportion_weight_columns(entries, tier3, column_of)


def _kreis_inputs():
    """Kreis census table (every target 100, education_6_17_edu 40), controls map, household names."""
    controls_map = controls_builder._kreis_controls_map(control_spec.tier3_controls())
    household_names: set[str] = set()
    for ctl in kac.REGISTRY:
        sub = controls_builder._kreis_controls_map(control_spec.attribute_kreis_controls([ctl]))
        controls_map.update(sub)
        if ctl.level == "household":
            household_names.update(sub)
    sources = sorted({c for cols in controls_map.values() for c in cols})
    table = pd.DataFrame({"ARS_kreis": ["03101", "03158"],
                          **{c: [KREIS_TARGET] * 2 for c in sources}})
    table["education_6_17_edu"] = EDU_TARGET
    return table, controls_map, household_names


def _full_target(control: str) -> float:
    """The Kreis target of a control: the row-sum of its census source columns of ``_kreis_inputs``."""
    _table, controls_map, _household = _kreis_inputs()
    return sum(EDU_TARGET if c == "education_6_17_edu" else KREIS_TARGET
               for c in controls_map[control])


def _assemble(tmp_path, cells, mapping, **kwargs) -> dict[str, pd.DataFrame]:
    """Assemble batch A and B; return each batch's KREIS control table indexed by Kreis."""
    table, controls_map, household_names = _kreis_inputs()
    full_xwalk = folders.build_geo_crosswalk(
        cells, id_col_100m="ZENSUS100m", parent_col="ZENSUS1km", ars_col=ARS,
        resolve_parent_kreis=True, kreis_weight_col="POP_TOTAL_100m_adj")
    pop = kreis_controls._kreis_pop_from_crosswalk(cells, full_xwalk)
    hh = kreis_controls._kreis_pop_from_crosswalk(cells, full_xwalk, weight_col=HH)
    by_col = {c: kreis_controls._kreis_pop_from_crosswalk(cells, full_xwalk, weight_col=c)
              for c in sorted(set(mapping.values()))}
    out = {}
    for batch in ("A", "B"):
        parents = [p[0] for p in _PARENTS if p[2] == batch]
        subset = cells[cells["ZENSUS1km"].isin(parents)].copy()
        folder = tmp_path / batch
        batch_folders.assemble_batch_folder(
            folder, subset, ["B_agg"], pd.DataFrame({"a": [1]}),
            pd.DataFrame({"H_ID": [1]}), pd.DataFrame({"H_ID": [1], "P_ID": [1]}),
            settings_yaml="x: 1\n", logging_yaml="version: 1\n",
            kreis_table=table, kreis_controls_map=controls_map,
            kreis_total_pop=pop, kreis_total_hh=hh, household_control_names=household_names,
            apportion_weight_col_by_control=mapping, kreis_totals_by_weight_col=by_col,
            **kwargs)
        frame = pd.read_csv(folder / "data" / "control_totals_KREIS.csv", dtype={"KREIS": str})
        out[batch] = frame.set_index("KREIS")
    return out


def test_universe_weight_columns_sum_the_right_years():
    cells = pd.DataFrame({f"{s}_AGE_{y}": [0.0] for s in "MF" for y in range(0, 101)})
    cells["M_AGE_3"], cells["F_AGE_10"], cells["M_AGE_30"] = 2.0, 1.5, 4.0
    universes = [(0, 5), (6, 17), (18, None), (14, None)]
    out, column_of = controls_builder.add_universe_weight_columns(cells, universes)
    assert [float(out[column_of[u]].iloc[0]) for u in universes] == [2.0, 1.5, 4.0, 4.0]
    assert len(set(column_of.values())) == len(universes)
    with pytest.raises(RuntimeError, match="M_AGE_30"):
        controls_builder.add_universe_weight_columns(cells.drop(columns=["M_AGE_30"]), [(18, None)])


def test_person_control_follows_its_own_universe(tmp_path):
    cells, mapping = _universe_cells()
    tables = _assemble(tmp_path, cells, mapping)
    # education 6-17: batch A holds 30 of the 40 persons aged 6-17 of Kreis 03101, B holds 10.
    assert tables["A"].loc["03101", "education_6_17_edu_KREIS"] == pytest.approx(30.0)
    assert tables["B"].loc["03101", "education_6_17_edu_KREIS"] == pytest.approx(10.0)
    # trip_class has no age universe: it keeps the population share (100 of 200 persons each).
    assert tables["A"].loc["03101", "trip_class_0_KREIS"] == pytest.approx(50.0)
    assert tables["B"].loc["03101", "trip_class_0_KREIS"] == pytest.approx(50.0)
    # a Tier-3 control follows the 15+ universe (aged 30: 68 persons in A, 86 in B of Kreis 03101)
    assert tables["A"].loc["03101", "employed_KREIS"] == pytest.approx(100.0 * 68 / 154)


def test_parts_sum_to_the_kreis_target(tmp_path):
    cells, mapping = _universe_cells()
    tables = _assemble(tmp_path, cells, mapping)
    summed = tables["A"].add(tables["B"], fill_value=0.0)
    for control in summed.columns:
        assert np.allclose(summed.loc["03101", control], _full_target(control),
                           atol=1e-9, rtol=0.0), control


def test_single_batch_kreis_keeps_the_full_target(tmp_path):
    cells, mapping = _universe_cells()
    tables = _assemble(tmp_path, cells, mapping)
    for control in tables["B"].columns:
        assert tables["B"].loc["03158", control] == pytest.approx(_full_target(control)), control


def test_empty_region_universe_gives_zero_and_warns_with_the_kreis(tmp_path, caplog):
    # Kreis 03101 has nobody aged 6-17 at all (region total 0): its target vanishes in both batches.
    caplog.set_level(logging.INFO, logger=batch_folders.logger.name)
    cells, mapping = _universe_cells(_cells(young={"P1": 0.0, "P2": 0.0}))
    tables = _assemble(tmp_path, cells, mapping)
    for batch in ("A", "B"):
        value = tables[batch].loc["03101", "education_6_17_edu_KREIS"]
        assert np.isfinite(value) and value == 0.0
        assert not tables[batch].isna().any().any()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("03101" in m and "region total of 0" in m for m in warnings)
    # the other Kreis of batch B is unaffected
    assert tables["B"].loc["03158", "education_6_17_edu_KREIS"] == pytest.approx(EDU_TARGET)


def test_empty_part_gives_zero_and_is_counted(tmp_path, caplog):
    # Batch A holds nobody aged 6-17 of Kreis 03101, batch B all of them (10 persons).
    caplog.set_level(logging.INFO, logger=batch_folders.logger.name)
    cells, mapping = _universe_cells(_cells(young={"P1": 0.0}))
    tables = _assemble(tmp_path, cells, mapping)
    assert tables["A"].loc["03101", "education_6_17_edu_KREIS"] == 0.0
    assert tables["B"].loc["03101", "education_6_17_edu_KREIS"] == pytest.approx(EDU_TARGET)
    # batch A: one empty part (the 6-17 universe) among its parts, counted in the rate line
    rate_lines = [r.getMessage() for r in caplog.records if "empty_part" in r.getMessage()]
    assert any("fallback 1 (" in m for m in rate_lines)
    assert not any(r.levelno == logging.WARNING and "region total of 0" in r.getMessage()
                   for r in caplog.records)


def test_empty_region_universes_are_reported_with_the_dropped_target():
    table, controls_map, _household = _kreis_inputs()
    mapping = {"education_6_17_edu_KREIS": "W", "education_6_17_noedu_KREIS": "W"}
    dropped = batch_folders.log_empty_region_universes(
        {"W": {"03101": 0.0, "03158": 12.0}}, mapping, table, controls_map)
    assert dropped == {"W": {"03101": EDU_TARGET + KREIS_TARGET}}


def test_assemble_rejects_a_universe_map_without_tier3(tmp_path):
    cells, _mapping = _universe_cells()
    with pytest.raises(ValueError, match="Tier-3"):
        batch_folders.assemble_batch_folder(
            tmp_path / "x", cells, ["B_agg"], pd.DataFrame({"a": [1]}),
            pd.DataFrame({"H_ID": [1]}), pd.DataFrame({"H_ID": [1], "P_ID": [1]}),
            settings_yaml="x: 1\n", logging_yaml="version: 1\n",
            apportion_weight_col_by_control={"employed_KREIS": "W"},
            kreis_totals_by_weight_col={"W": {"03101": 1.0}})


def test_household_controls_keep_the_household_share(tmp_path):
    cells, mapping = _universe_cells()
    tables = _assemble(tmp_path, cells, mapping)
    # households of Kreis 03101: 10 in A, 30 in B (population share would be 0.5 / 0.5)
    assert tables["A"].loc["03101", "number_of_cars_0_KREIS"] == pytest.approx(25.0)
    assert tables["B"].loc["03101", "number_of_cars_0_KREIS"] == pytest.approx(75.0)
    assert "number_of_cars_0_KREIS" not in mapping


# ---------------------------------------------------------------------------
# run_popsim_mid: universe weight columns reach every batch folder (plain and stratified path)
# ---------------------------------------------------------------------------

def _run_inputs(universe_weights=(30.0, 10.0)):
    """Two 1 km parents of one Kreis (population 60 / 40), forced into two batches."""
    cells = pd.DataFrame({
        "ZENSUS100m": ["pA_c1", "pB_c1"], "ZENSUS1km": ["pA", "pB"], "STAAT": 1, "WELT": 1,
        ARS: ["031010000000", "031010000000"], "POP_TOTAL_100m_adj": [60.0, 40.0],
        "POP": [60.0, 40.0], "W": list(universe_weights)})
    controls_df = pd.DataFrame(
        {"target": ["POP_ZENSUS100m_target"], "geography": ["ZENSUS100m"],
         "seed_table": ["persons"], "importance": [1000],
         "control_field": ["POP_ZENSUS100m"], "expression": ["(persons.P_GEW > 0)"]})
    seeds = (pd.DataFrame({"H_ID": [1], "H_GEW": [2.0], "STAAT": [1]}),
             pd.DataFrame({"H_ID": [1], "P_ID": [1], "STAAT": [1]}))
    kreis = dict(kreis_table=pd.DataFrame({"ARS_kreis": ["03101"], "E11": [1000.0]}),
                 kreis_controls_map={"employed": ("E11",)})
    return cells, controls_df, seeds, kreis


def _run(tmp_path, *, mapping, stratify=False, universe_weights=(30.0, 10.0), with_kreis=True):
    """Run run_popsim_mid with a fake PopulationSim; return (employed target per batch, calls)."""
    cells, controls_df, (seed_hh, seed_p), kreis = _run_inputs(universe_weights)
    targets: list[float] = []

    def fake_run_one(folder):
        from braunschweig.popsim import batch as b
        df = pd.read_csv(Path(folder) / "data" / "control_totals_KREIS.csv", dtype={"KREIS": str})
        targets.append(float(df.set_index("KREIS")["employed"]["03101"]))
        xwalk = pd.read_csv(Path(folder) / "data" / "geo_cross_walk.csv", dtype=str)
        out_dir = Path(folder) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        xwalk.assign(H_ID=1).to_csv(out_dir / "final_expanded_household_ids.csv", index=False)
        return b.BatchResult(str(folder), "succeeded", "ok", 0.0)

    extra = dict(source=object(), stratify_regiostar=True) if stratify else {}
    batch_folders.run_popsim_mid(
        cells, ["POP"], controls_df, seed_hh, seed_p, work_dir=tmp_path,
        settings_yaml="x: 1\n", logging_yaml="version: 1\n", max_cells=1,
        run_one=fake_run_one, num_workers=1, apportion_weight_col_by_control=mapping,
        **(kreis if with_kreis else {}), **extra)
    return targets


@pytest.mark.parametrize("stratify", [False, True])
def test_run_popsim_mid_hands_the_universe_weights_to_every_batch(tmp_path, monkeypatch, stratify):
    calls: list[dict] = []
    original = batch_folders.assemble_batch_folder

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(batch_folders, "assemble_batch_folder", spy)
    # stratified path: one stratum per 1 km parent; the donor seed is passed through unchanged
    monkeypatch.setattr(batch_folders, "dominant_stratum_for_1km",
                        lambda cells, source: ({"pA": "s1", "pB": "s2"}, 0.0))
    monkeypatch.setattr(batch_folders, "filter_seed_to_stratum",
                        lambda hh, p, stratum, source: (hh, p))
    targets = _run(tmp_path, mapping={"employed": "W"}, stratify=stratify)
    assert len(calls) == 2
    for kwargs in calls:
        assert kwargs["apportion_weight_col_by_control"] == {"employed": "W"}
        assert kwargs["kreis_totals_by_weight_col"] == {"W": {"03101": 40.0}}
    # own-universe shares 30/40 and 10/40 (the population shares 0.6 / 0.4 would give 600 / 400)
    assert sorted(targets) == pytest.approx([250.0, 750.0])


def test_run_popsim_mid_without_a_universe_map_keeps_the_population_share(tmp_path):
    assert sorted(_run(tmp_path, mapping=None)) == pytest.approx([400.0, 600.0])


def test_run_popsim_mid_rejects_an_absent_universe_column(tmp_path):
    with pytest.raises(ValueError, match="absent"):
        _run(tmp_path, mapping={"employed": "NOT_A_COLUMN"})


def test_run_popsim_mid_rejects_a_universe_map_without_tier3(tmp_path):
    with pytest.raises(ValueError, match="Tier-3"):
        _run(tmp_path, mapping={"employed": "W"}, with_kreis=False)


def test_run_popsim_mid_warns_once_for_a_universe_empty_in_the_whole_region(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=batch_folders.logger.name)
    targets = _run(tmp_path, mapping={"employed": "W"}, universe_weights=(0.0, 0.0))
    assert targets == [0.0, 0.0]
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING and "1000.0" in r.getMessage()]
    assert len(warnings) == 1 and "03101" in warnings[0]
