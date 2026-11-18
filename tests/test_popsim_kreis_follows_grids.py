"""A KREIS control follows the finer-level control of the same variable (#658, Task A4).

All values are SYNTHETIC test data. Three 1 km parents, one 100 m cell each: P1 and P2 lie in Kreis
03101, P3 in Kreis 03158. The grid columns carry values unrelated to the Kreis census table (every
census source column is 100), so a KREIS value can only equal a grid sum if the step ran.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from braunschweig.popsim import control_spec, folders
from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.popsim.mid import batch_folders
from braunschweig.popsim.ownership_grid import BIKES_COLUMNS, CARS_COLUMNS
from braunschweig.popsim.stage import controls_builder

ARS = "RegionalSchlussel_ARS"
ENTRY = {e.name: e for e in kac.REGISTRY}
EMPLOYED = [c.name for c in control_spec.employment_grid_controls()]
GRID_COLUMNS = list(CARS_COLUMNS) + list(BIKES_COLUMNS) + EMPLOYED
#            parent  Kreis    batch
_PARENTS = (("P1", "03101", "A"), ("P2", "03101", "B"), ("P3", "03158", "B"))
# Per-parent value of OWN_CARS_0_agg (cells [3, 2 | 4]); every other grid column is value * (index + 1).
_CARS_0 = {"P1": 3.0, "P2": 2.0, "P3": 4.0}


def _cells() -> pd.DataFrame:
    rows = []
    for parent, kreis, _batch in _PARENTS:
        row = {"ZENSUS100m": f"c_{parent}", "ZENSUS1km": parent, ARS: kreis + "0000000",
               "POP_TOTAL_100m_adj": 100.0}
        for index, col in enumerate(GRID_COLUMNS):
            row[col] = _CARS_0[parent] * (index + 1)
        rows.append(row)
    return pd.DataFrame(rows)


def _controls_df(*, ownership: bool = True, employment: bool = True) -> pd.DataFrame:
    catalog = [c for c in control_spec.tier3_controls() if c.name == "employed"]
    for name in ("number_of_cars", "number_of_bicycles"):
        catalog += control_spec.attribute_kreis_controls([ENTRY[name]])
    if ownership:
        catalog += control_spec.ownership_grid_controls()
    if employment:
        catalog += control_spec.employment_grid_controls()
    return control_spec.render_catalog_csv(catalog, "mid")


def _kreis_inputs(controls_df: pd.DataFrame):
    """Kreis census table (every source column 100) and the KREIS controls map of ``controls_df``."""
    names = set(controls_df["control_field"])
    catalog = [c for c in control_spec.tier3_controls() if c.name == "employed"]
    for name in ("number_of_cars", "number_of_bicycles"):
        catalog += control_spec.attribute_kreis_controls([ENTRY[name]])
    controls_map = controls_builder._kreis_controls_map(
        [c for c in catalog if f"{c.name}_{c.geography}" in names])
    sources = sorted({c for cols in controls_map.values() for c in cols})
    table = pd.DataFrame({"ARS_kreis": ["03101", "03158"], **{c: [100.0, 100.0] for c in sources}})
    return table, controls_map


def _assemble(tmp_path, controls_df, batches=("A", "B"), **kwargs) -> dict[str, pd.DataFrame]:
    """Assemble the batches; return each batch's KREIS control table indexed by Kreis."""
    cells = _cells()
    table, controls_map = _kreis_inputs(controls_df)
    out = {}
    for batch in batches:
        parents = [p for p, _k, b in _PARENTS if b in batch]
        subset = cells[cells["ZENSUS1km"].isin(parents)].copy()
        folder = tmp_path / batch
        batch_folders.assemble_batch_folder(
            folder, subset, GRID_COLUMNS, controls_df,
            pd.DataFrame({"H_ID": [1]}), pd.DataFrame({"H_ID": [1], "P_ID": [1]}),
            settings_yaml="x: 1\n", logging_yaml="version: 1\n",
            kreis_table=table, kreis_controls_map=controls_map, **kwargs)
        frame = pd.read_csv(folder / "data" / "control_totals_KREIS.csv", dtype={"KREIS": str})
        out[batch] = frame.set_index("KREIS")
    return out


def test_kreis_car_classes_equal_the_grid_sums_of_the_batch(tmp_path):
    # One batch holding all parents: Kreis 03101 has cells [3, 2], Kreis 03158 has [4].
    out = _assemble(tmp_path, _controls_df(), batches=("AB",), kreis_follow_grids=True)["AB"]
    assert out["number_of_cars_0_KREIS"].tolist() == [5, 4]
    # Every category follows its own grid column (OWN_CARS_<c>_agg, OWN_BIKES_<c>_agg).
    for position, col in enumerate(CARS_COLUMNS):
        factor = GRID_COLUMNS.index(col) + 1
        label = col[len("OWN_CARS_"):-len("_agg")]
        assert out[f"number_of_cars_{label}_KREIS"].tolist() == [5 * factor, 4 * factor], position
    for col in BIKES_COLUMNS:
        factor = GRID_COLUMNS.index(col) + 1
        label = col[len("OWN_BIKES_"):-len("_agg")]
        assert out[f"number_of_bicycles_{label}_KREIS"].tolist() == [5 * factor, 4 * factor]


def test_employed_equals_the_employed_grid(tmp_path):
    out = _assemble(tmp_path, _controls_df(), batches=("AB",), kreis_follow_grids=True)["AB"]
    factors = sum(GRID_COLUMNS.index(c) + 1 for c in EMPLOYED)
    assert out["employed_KREIS"].tolist() == [5 * factors, 4 * factors]


def test_region_total_stays_the_kreis_anchor(tmp_path):
    """Kreis 03101 lies in both batches: its two parts add up to the sum of its grid cells."""
    out = _assemble(tmp_path, _controls_df(), kreis_follow_grids=True)
    assert out["A"].loc["03101", "number_of_cars_0_KREIS"] == 3
    assert out["B"].loc["03101", "number_of_cars_0_KREIS"] == 2
    assert out["A"].loc["03101", "number_of_cars_0_KREIS"] + out["B"].loc[
        "03101", "number_of_cars_0_KREIS"] == 5


def test_follow_grids_is_off_by_default(tmp_path):
    """Without the argument the KREIS values are the census value (legacy, byte-identical)."""
    out = _assemble(tmp_path, _controls_df())["A"]
    assert set(out["number_of_cars_0_KREIS"]) == {100}
    assert set(out["employed_KREIS"]) == {100}


def test_follow_grids_skips_an_inactive_grid(tmp_path, caplog):
    """Ownership grid off: car and bicycle controls keep their value; the employed pair still follows."""
    controls_df = _controls_df(ownership=False)
    assert control_spec.kreis_controls_following_grids(controls_df).keys() == {"employed_KREIS"}
    with caplog.at_level(logging.INFO):
        out = _assemble(tmp_path, controls_df, batches=("AB",), kreis_follow_grids=True)["AB"]
    assert out["number_of_cars_0_KREIS"].tolist() == [100, 100]
    assert out["employed_KREIS"].iloc[1] == 4 * sum(GRID_COLUMNS.index(c) + 1 for c in EMPLOYED)
    skipped = [r for r in caplog.records if "kreis_follow_grids" in r.getMessage()
               and "number_of_cars_0_KREIS" in r.getMessage()]
    assert skipped, "a skipped pair must be logged"


def test_follow_grids_skips_the_employed_control_when_the_employment_grid_is_off(tmp_path):
    controls_df = _controls_df(employment=False)
    assert "employed_KREIS" not in control_spec.kreis_controls_following_grids(controls_df)
    out = _assemble(tmp_path, controls_df, batches=("AB",), kreis_follow_grids=True)["AB"]
    assert out["employed_KREIS"].tolist() == [100, 100]
    assert out["number_of_cars_0_KREIS"].tolist() == [5, 4]


def test_an_entd_seed_has_no_pair_to_follow():
    """ENTD cannot express the grid or the KREIS ownership controls (expression None), so
    controls_for_seed drops both sides and no pair exists; a grid expression of None never
    reaches the mapping as a half-active pair."""
    catalog = [c for c in control_spec.tier3_controls() if c.name == "employed"]
    catalog += control_spec.attribute_kreis_controls([ENTRY["number_of_cars"]])
    catalog += control_spec.ownership_grid_controls() + control_spec.employment_grid_controls()
    controls_df = control_spec.render_catalog_csv(control_spec.controls_for_seed(catalog, "entd"), "entd")
    assert control_spec.kreis_controls_following_grids(controls_df) == {}
    # positive control: the same catalog on the MiD seed does yield pairs, so the empty result
    # above is the ENTD seed's doing and not an empty catalog
    mid_df = control_spec.render_catalog_csv(control_spec.controls_for_seed(catalog, "mid"), "mid")
    assert control_spec.kreis_controls_following_grids(mid_df)


def test_the_one_km_ownership_column_is_the_sum_of_the_integer_100m_children():
    """The follow-grids sum reads the 1 km table instead of the 100 m children; that is only the
    same number if build_control_totals aggregates the INTEGER children."""
    column = CARS_COLUMNS[0]
    crosswalk = pd.DataFrame({"ZENSUS100m": ["a", "b", "c", "d"],
                              "ZENSUS1km": ["P1", "P1", "P2", "P2"]})
    per_cell = pd.DataFrame({"ZENSUS100m": ["a", "b", "c", "d"], column: [1.4, 2.4, 0.5, 0.6]})
    tables = folders.build_control_totals(per_cell, crosswalk, target_cols=[column])
    fine = tables["ZENSUS100m"].merge(crosswalk, on="ZENSUS100m")
    assert (fine[column] == fine[column].round()).all()
    expected = fine.groupby("ZENSUS1km")[column].sum()
    got = tables["ZENSUS1km"].set_index("ZENSUS1km")[column]
    assert got.to_dict() == expected.to_dict()


def test_an_empty_mapping_logs_that_no_control_follows_a_grid(caplog):
    kreis = pd.DataFrame({"KREIS": ["03101"], "employed": [5]})
    with caplog.at_level(logging.INFO):
        out = folders.set_kreis_controls_from_fine_sums(kreis, {}, pd.DataFrame(), {})
    assert out.equals(kreis)
    text = caplog.text
    assert "no control follows a grid" in text and "(None)" not in text


def test_a_partly_present_grid_is_an_error():
    controls_df = _controls_df()
    controls_df = controls_df[controls_df["control_field"] != "EMPLOYED_F_60plus_agg_ZENSUS100m"]
    with pytest.raises(ValueError, match="EMPLOYED_F_60plus_agg_ZENSUS100m"):
        control_spec.kreis_controls_following_grids(controls_df)


def test_follow_grids_raises_on_a_missing_active_column():
    controls_df = _controls_df()
    mapping = control_spec.kreis_controls_following_grids(controls_df)
    kreis_totals = pd.DataFrame({"KREIS": ["03101"], "employed_KREIS": [100.0]})
    xwalk = pd.DataFrame({"ZENSUS100m": ["c1"], "ZENSUS1km": ["P1"], "KREIS": ["03101"]})
    fine = {"ZENSUS100m": pd.DataFrame({"ZENSUS100m": ["c1"]}),
            "ZENSUS1km": pd.DataFrame({"ZENSUS1km": ["P1"]})}
    with pytest.raises(ValueError, match="EMPLOYED_M_16_29_agg_ZENSUS100m"):
        folders.set_kreis_controls_from_fine_sums(
            kreis_totals, fine, xwalk, {"employed_KREIS": mapping["employed_KREIS"]})


def _inputs():
    """A valid minimal input of set_kreis_controls_from_fine_sums (two Kreise, one 100 m cell each)."""
    kreis_totals = pd.DataFrame({"KREIS": ["03101", "03158"], "employed_KREIS": [100.0, 100.0]})
    xwalk = pd.DataFrame({"ZENSUS100m": ["c1", "c2"], "ZENSUS1km": ["P1", "P2"],
                          "KREIS": ["03101", "03158"]})
    fine = {"ZENSUS100m": pd.DataFrame({"ZENSUS100m": ["c1", "c2"], "E_ZENSUS100m": [7, 9]}),
            "ZENSUS1km": pd.DataFrame({"ZENSUS1km": ["P1", "P2"], "O_ZENSUS1km": [7, 9]})}
    return kreis_totals, fine, xwalk, {"employed_KREIS": ("E_ZENSUS100m",)}


def test_the_valid_minimal_input_sums_per_kreis():
    out = folders.set_kreis_controls_from_fine_sums(*_inputs())
    assert out["employed_KREIS"].tolist() == [7, 9]


def _mixed_geography(kreis_totals, fine, xwalk, mapping):
    return kreis_totals, fine, xwalk, {"employed_KREIS": ("E_ZENSUS100m", "O_ZENSUS1km")}


def _cell_absent_from_crosswalk(kreis_totals, fine, xwalk, mapping):
    return kreis_totals, fine, xwalk.iloc[:1], mapping


def _nan_grid_value(kreis_totals, fine, xwalk, mapping):
    fine["ZENSUS100m"] = fine["ZENSUS100m"].assign(E_ZENSUS100m=[7.0, float("nan")])
    return kreis_totals, fine, xwalk, mapping


def _kreis_column_missing(kreis_totals, fine, xwalk, mapping):
    return kreis_totals.drop(columns=["employed_KREIS"]), fine, xwalk, mapping


def _cell_with_two_kreise(kreis_totals, fine, xwalk, mapping):
    return kreis_totals, fine, pd.concat([xwalk, xwalk.iloc[:1].assign(KREIS="03158")]), mapping


def _kreis_without_a_grid_cell(kreis_totals, fine, xwalk, mapping):
    fine["ZENSUS100m"] = fine["ZENSUS100m"].iloc[:1]
    return kreis_totals, fine, xwalk, mapping


@pytest.mark.parametrize("damage, match", [
    (_mixed_geography, "one geography suffix"),
    (_cell_absent_from_crosswalk, "absent from the crosswalk"),
    (_nan_grid_value, "NaN"),
    (_kreis_column_missing, "not a column of the KREIS table"),
    (_cell_with_two_kreise, "more than one Kreis"),
    (_kreis_without_a_grid_cell, "03158"),
])
def test_set_kreis_controls_raises_on_inconsistent_input(damage, match):
    with pytest.raises(ValueError, match=match):
        folders.set_kreis_controls_from_fine_sums(*damage(*_inputs()))


def test_the_overwritten_column_keeps_its_dtype_in_the_written_table(tmp_path):
    out = _assemble(tmp_path, _controls_df(), batches=("AB",), kreis_follow_grids=True)["AB"]
    legacy = _assemble(tmp_path / "legacy", _controls_df(), batches=("AB",))["AB"]
    assert out["number_of_cars_0_KREIS"].dtype == legacy["number_of_cars_0_KREIS"].dtype
    assert out["number_of_cars_0_KREIS"].tolist() == [5.0, 4.0]


def test_a_large_change_warns_and_one_summary_line_is_logged(caplog):
    kreis_totals, fine, xwalk, mapping = _inputs()
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.folders"):
        folders.set_kreis_controls_from_fine_sums(kreis_totals, fine, xwalk, mapping)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(warnings) == 1 and "employed_KREIS" in warnings[0].getMessage()  # 100 -> 7 / 9
    assert len(infos) == 1 and "employed_KREIS" in infos[0].getMessage()
    caplog.clear()
    near = kreis_totals.assign(employed_KREIS=[8.0, 10.0])  # within the threshold of 7 / 9
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.folders"):
        folders.set_kreis_controls_from_fine_sums(near, fine, xwalk, mapping)
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


class _Stop(Exception):
    """Raised by the spy once it has seen the arguments of the first assemble call."""


@pytest.mark.parametrize("stratify", [False, True])
def test_run_popsim_mid_hands_the_switch_to_both_assemble_paths(monkeypatch, tmp_path, stratify):
    seen: list[dict] = []

    def spy(*args, **kwargs):
        seen.append(kwargs)
        raise _Stop

    monkeypatch.setattr(batch_folders, "assemble_batch_folder", spy)
    monkeypatch.setattr(batch_folders, "dominant_stratum_for_1km", lambda cells, source: ({"P1": "s"}, 0.0))
    monkeypatch.setattr(batch_folders, "filter_seed_to_stratum", lambda hh, p, stratum, source: (hh, p))
    cells = _cells()[:1]
    table, controls_map = _kreis_inputs(_controls_df())
    extra = dict(source=object(), stratify_regiostar=True) if stratify else {}
    for flag in (True, False):
        with pytest.raises(_Stop):
            batch_folders.run_popsim_mid(
                cells, GRID_COLUMNS, _controls_df(), pd.DataFrame({"H_ID": [1]}),
                pd.DataFrame({"H_ID": [1], "P_ID": [1]}), work_dir=tmp_path,
                settings_yaml="x: 1\n", logging_yaml="version: 1\n", max_cells=10,
                run_one=None, kreis_table=table, kreis_controls_map=controls_map,
                kreis_follow_grids=flag, **extra)
        assert seen[-1]["kreis_follow_grids"] is flag


def test_follow_grids_needs_the_kreis_geography(tmp_path):
    with pytest.raises(ValueError, match="Tier-3"):
        batch_folders.assemble_batch_folder(
            tmp_path / "x", _cells(), GRID_COLUMNS, _controls_df(),
            pd.DataFrame({"H_ID": [1]}), pd.DataFrame({"H_ID": [1], "P_ID": [1]}),
            settings_yaml="x: 1\n", logging_yaml="version: 1\n", kreis_follow_grids=True)
