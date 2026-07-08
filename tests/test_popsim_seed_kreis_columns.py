"""Tests for the generalised seed-column derivation in load_mid_seed (Task 3).

Covers:
- attributes.map_has_ebike (mirrors map_number_of_cars / map_number_of_bicycles).
- load_mid_seed deriving clean, MECE seed columns only for ACTIVE registry entries
  (kreis_control_entries), with number_of_cars/number_of_bicycles/has_ebike using the
  RESOLVED (99-imputed) column and economic_status staying a raw oek_status pass-through.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import attributes
from braunschweig.popsim.kreis_attribute_control import REGISTRY


def _entry(name):
    return next(c for c in REGISTRY if c.name == name)


def test_map_has_ebike_binary_from_source_column():
    hh = pd.DataFrame({"H_EBIKE": [0, 1, 2, 99]})  # count-style source; >=1 -> yes
    out = attributes.map_has_ebike(hh, ebike_col="H_EBIKE", rng=np.random.RandomState(0))
    assert set(out["has_ebike"].unique()) <= {0, 1}
    assert out["has_ebike"].tolist()[:3] == [0, 1, 1]


def test_map_has_ebike_fails_on_absent_column():
    hh = pd.DataFrame({"other": [1, 2]})
    with pytest.raises(KeyError):
        attributes.map_has_ebike(hh, ebike_col="H_EBIKE")


def _write_mini_mid(tmp: Path):
    hh = tmp / "MiD2023_Haushalte.csv"
    pers = tmp / "MiD2023_Personen.csv"
    hh.write_text(
        "H_ID;H_GEW;RegioStaR7;H_GR;H_MIETE;haustyp;hhgr_gr;oek_status;H_ANZAUTO;H_ANZRAD;H_EBIKE\n"
        "1;1.0;71;1;1;1;1;3;1;2;0\n"
        "2;1.0;71;2;2;1;2;4;99;99;1\n", encoding="utf-8")
    # NOTE (deviation from the brief's verbatim fixture): the person id column here is
    # "P_ID" (matching SeedColumns.person_id = "P_ID" for MID_SEED_COLUMNS), not "HP_ID"
    # as literally shown in the brief -- the brief's fixture header used a column name
    # that does not exist in the seed column mapping, which would make load_mid_seed
    # fail with an unrelated "usecols do not match columns" error. See task-3-report.md.
    pers.write_text(
        "P_ID;H_ID;P_GEW;HP_ALTER;HP_SEX;kernwo\n"
        "11;1;1.0;40;1;1\n12;2;1.0;35;2;1\n13;2;1.0;38;1;1\n", encoding="utf-8")


def test_load_mid_seed_derives_only_active_kreis_columns(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid(tmp_path)
    entries = [_entry("number_of_cars")]
    hh, _pers, _rep = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=entries,
        kreis_seed_rng=np.random.RandomState(0))
    assert "number_of_cars" in hh.columns
    assert "number_of_bicycles" not in hh.columns  # bikes entry not active
    assert set(hh["number_of_cars"]).issubset(set(range(0, 11)))  # 99 imputed away


def test_load_mid_seed_count_style_entry_requires_seeded_rng(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid(tmp_path)
    entries = [_entry("number_of_cars")]
    with pytest.raises(ValueError):
        load_mid_seed(tmp_path, day_filter_values=(), kreis_control_entries=entries)


def test_load_mid_seed_include_status_seed_col_alias_matches_economic_status_entry(tmp_path):
    """The deprecated include_status_seed_col=True alias must stay byte-identical:
    oek_status is carried through RAW (no resolve/derivation)."""
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid(tmp_path)
    hh_alias, _p1, _r1 = load_mid_seed(
        tmp_path, day_filter_values=(), include_status_seed_col=True)
    hh_entry, _p2, _r2 = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=[_entry("economic_status")])
    assert "oek_status" in hh_alias.columns
    assert hh_alias["oek_status"].tolist() == hh_entry["oek_status"].tolist()
    # Raw pass-through: values are exactly the source codes (3, 4), not resolved/imputed.
    assert sorted(hh_alias["oek_status"].tolist()) == [3, 4]
