"""Tests for the generalised seed-column derivation in load_mid_seed (Task 3) and
project_completed_seed (Task 4 critical-review fix + 2026-07-08 server verification).

Covers:
- attributes.map_has_ebike (mirrors map_number_of_cars / map_number_of_bicycles); default
  source column H_ANZPED (Anzahl Pedelecs), verified 2026-07-08 against the MiD B1
  household microdata.
- load_mid_seed deriving clean, MECE seed columns only for ACTIVE registry entries
  (kreis_control_entries), with number_of_cars/number_of_bicycles/has_ebike using the
  RESOLVED (99-imputed) column and economic_status staying a raw oek_status pass-through.
- project_completed_seed (the default complete_members=True donor path) deriving the same
  count-style columns from the completed donor's raw H_ANZAUTO/anzpedrad/H_ANZPED, now
  INCLUDING has_ebike (formerly rejected pending server verification; issue #116 resolved).
- active_kreis_entries defaulting ALL FOUR entries (including has_ebike) ON now that the
  has_ebike source column is server-verified and wired on both seed paths.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import attributes
from braunschweig.popsim import mid
from braunschweig.popsim import sources
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


# --- Task 1 (2026-07-08 plan): attributes.map_trip_class (person-level, anzwege1) ---


def test_map_trip_class_classes_0_to_3_on_valid_trip_counts():
    persons = pd.DataFrame({"anzwege1": [0, 1, 2, 3, 4, 5, 50]})
    out = attributes.map_trip_class(persons, rng=np.random.RandomState(0))
    assert out["trip_class"].tolist() == [0, 1, 1, 2, 2, 3, 3]


def test_map_trip_class_imputes_missing_codes_803_804_away():
    # 803/804 mark trip-module-not-covered persons (item non-response), never a
    # deterministic class; they must be imputed from the valid pool (here: single
    # age band, so the global-within-group pool is [0, 1, 2, 3, 4, 5, 50]).
    persons = pd.DataFrame({
        "anzwege1": [0, 1, 2, 3, 4, 5, 50, 803, 804],
        "alter_gr1": [1, 1, 1, 1, 1, 1, 1, 1, 1],
    })
    out = attributes.map_trip_class(persons, rng=np.random.RandomState(0))
    assert set(out["trip_class"].unique()) <= {0, 1, 2, 3}
    imputed = out["trip_class"].iloc[7:9]
    assert imputed.isin([0, 1, 2, 3]).all()


def test_map_trip_class_imputation_is_seeded_and_deterministic():
    persons = pd.DataFrame({
        "anzwege1": [0, 1, 2, 3, 4, 5, 50, 803, 804],
        "alter_gr1": [1, 1, 1, 1, 1, 1, 1, 1, 1],
    })
    out_a = attributes.map_trip_class(persons, rng=np.random.RandomState(42))
    out_b = attributes.map_trip_class(persons, rng=np.random.RandomState(42))
    assert out_a["trip_class"].tolist() == out_b["trip_class"].tolist()


def test_map_trip_class_fails_on_absent_column():
    persons = pd.DataFrame({"other": [1, 2]})
    with pytest.raises(KeyError):
        attributes.map_trip_class(persons)


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


# --- Task 4 critical-review fix: project_completed_seed (complete_members=True path) ---


def _completed_donor_frames():
    """Minimal completed-donor households/persons frame, carrying the raw columns
    (H_ANZAUTO / H_ANZRAD / anzpedrad / H_ANZPED / hhgr_gr / oek_status) that
    mid.MID_HOUSEHOLD_ATTR_COLS already loads for the completed_donor stage."""
    cols = sources.get_source("mid").seed_columns()
    households = pd.DataFrame({
        cols.household_id: ["h1", "h2", "h3"],
        cols.household_weight: [1.0, 1.0, 1.0],
        "H_GR": [1, 2, 2], "H_MIETE": [1, 2, 1], "haustyp": [1, 5, 1],
        "RegioStaR7": [73, 74, 73],
        "hhgr_gr": [1, 2, 2],
        "oek_status": [2, 4, 3],
        "H_ANZAUTO": [1, 99, 2],  # 99 = keine Angabe -> imputed away
        "H_ANZRAD": [2, 0, 99],
        # anzpedrad = bicycles INCLUDING pedelecs (H_ANZRAD + H_ANZPED, top-coded at 10;
        # verified 2026-07-08); the number_of_bicycles default source column.
        "anzpedrad": [3, 0, 99],
        # H_ANZPED = Anzahl Pedelecs (verified 2026-07-08); the has_ebike default source.
        "H_ANZPED": [1, 0, 99],
    })
    persons = pd.DataFrame({
        cols.person_household_id: ["h1", "h2", "h2", "h3"],
        cols.person_id: ["p1", "p2", "p3", "p4"],
        cols.person_weight: [1.0, 1.0, 1.0, 1.0],
        cols.age: [40, 38, 10, 45],
        cols.sex: [1, 2, 1, 2],
    })
    return cols, households, persons


def test_project_completed_seed_derives_active_count_columns():
    cols, households, persons = _completed_donor_frames()
    entries = [_entry("number_of_cars"), _entry("number_of_bicycles")]
    seed_hh, _seed_p = mid.project_completed_seed(
        households, persons, cols,
        kreis_control_entries=entries, kreis_seed_rng=np.random.RandomState(0),
    )
    assert "number_of_cars" in seed_hh.columns
    assert "number_of_bicycles" in seed_hh.columns
    # 99 (keine Angabe) is imputed away: every value lies in the valid 0..10 range.
    assert set(seed_hh["number_of_cars"]).issubset(set(range(0, 11)))
    assert set(seed_hh["number_of_bicycles"]).issubset(set(range(0, 11)))
    assert 99 not in set(seed_hh["number_of_cars"])
    assert 99 not in set(seed_hh["number_of_bicycles"])


def test_project_completed_seed_count_style_entry_requires_seeded_rng():
    cols, households, persons = _completed_donor_frames()
    entries = [_entry("number_of_cars")]
    with pytest.raises(ValueError):
        mid.project_completed_seed(households, persons, cols, kreis_control_entries=entries)


def test_project_completed_seed_derives_has_ebike():
    """has_ebike is now fully wired on the completed-donor path (issue #116 resolved,
    2026-07-08 server verification): derived from the raw H_ANZPED column the
    completed-donor households already carry, producing a clean 0/1 column."""
    cols, households, persons = _completed_donor_frames()
    entries = [_entry("has_ebike")]
    seed_hh, _seed_p = mid.project_completed_seed(
        households, persons, cols,
        kreis_control_entries=entries, kreis_seed_rng=np.random.RandomState(0),
        ebike_seed_column="H_ANZPED",
    )
    assert "has_ebike" in seed_hh.columns
    assert set(seed_hh["has_ebike"].unique()) <= {0, 1}


def test_project_completed_seed_has_ebike_requires_ebike_seed_column():
    """has_ebike active without a configured ebike_seed_column must fail fast (no
    silent fallback to a guessed column name), mirroring load_mid_seed."""
    cols, households, persons = _completed_donor_frames()
    entries = [_entry("has_ebike")]
    with pytest.raises(ValueError, match="ebike_seed_column"):
        mid.project_completed_seed(
            households, persons, cols,
            kreis_control_entries=entries, kreis_seed_rng=np.random.RandomState(0),
        )


# --- has_ebike now defaults ON like the other three entries (server-verified 2026-07-08) ---


class _FakeContext:
    """Minimal synpp ExecuteContext stand-in: config(key) takes NO default argument
    (matches the real execute-time contract; declared defaults come from
    stage._KREIS_CONTROL_DEFAULT exactly as configure() declares them)."""

    def __init__(self, values=None):
        self._values = values or {}

    def config(self, key):
        if key in self._values:
            return self._values[key]
        from braunschweig.popsim import stage
        for name, toggle_key in stage._KREIS_CONTROL_TOGGLE_KEY.items():
            if key == toggle_key:
                return stage._KREIS_CONTROL_DEFAULT[name]
        raise KeyError(f"_FakeContext: no value or declared default for config key {key!r}")


def test_all_four_kreis_entries_default_on():
    from braunschweig.popsim.stage import active_kreis_entries

    active = active_kreis_entries(_FakeContext(), "mid")
    names = {c.name for c in active}
    assert names == {
        "economic_status", "number_of_cars", "number_of_bicycles", "has_ebike"
    }
