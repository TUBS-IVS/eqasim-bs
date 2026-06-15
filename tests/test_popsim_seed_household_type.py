"""Tests for hh_type5 derivation: map_households_to_hhtype (11-class) -> hh_type5 (5-class).

Task 8 (Tier-1 household_type/Lebensform control, MiD-only).  The 5 Zensus
Familientyp labels are derived from the MiD Haushaltstyp classification produced
by :func:`braunschweig.data.mid.status_by_hhtype.map_households_to_hhtype`.

These tests build MINIMAL SYNTHETIC person frames (no real MiD data required)
and run them through the REAL ``map_households_to_hhtype`` + the
:func:`braunschweig.popsim.seed.derive_hh_type5` collapse without mocking either
function.  This validates both the 11->5 collapse mapping and the DataFrame
wiring.
"""

from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim.seed import derive_hh_type5


# ---------------------------------------------------------------------------
# Helper: build a minimal persons frame for map_households_to_hhtype
# ---------------------------------------------------------------------------

def _persons(rows: list[tuple]) -> pd.DataFrame:
    """Build a minimal persons frame.

    Each row is (household_id, age) -- the minimum columns consumed by
    map_households_to_hhtype.  We pass household_id as the canonical name
    so derive_hh_type5 can use default household_id_col='household_id'.
    """
    hh_ids, ages = zip(*rows)
    return pd.DataFrame({
        "household_id": list(hh_ids),
        "age": list(ages),
    })


# ---------------------------------------------------------------------------
# Single-person households -> einpersonen
# ---------------------------------------------------------------------------

def test_single_person_young_maps_to_einpersonen() -> None:
    """Single-person HH, 25-year-old -> MiD single_18_29 -> einpersonen."""
    persons = _persons([(1, 25)])
    result = derive_hh_type5(persons)
    assert result[1] == "einpersonen", f"Expected einpersonen, got {result[1]!r}"


def test_single_person_middle_maps_to_einpersonen() -> None:
    """Single-person HH, 45-year-old -> MiD single_30_59 -> einpersonen."""
    persons = _persons([(2, 45)])
    result = derive_hh_type5(persons)
    assert result[2] == "einpersonen", f"Expected einpersonen, got {result[2]!r}"


def test_single_person_older_maps_to_einpersonen() -> None:
    """Single-person HH, 70-year-old -> MiD single_60_plus -> einpersonen."""
    persons = _persons([(3, 70)])
    result = derive_hh_type5(persons)
    assert result[3] == "einpersonen", f"Expected einpersonen, got {result[3]!r}"


# ---------------------------------------------------------------------------
# Couple without children -> paar_ohne_kind
# ---------------------------------------------------------------------------

def test_couple_no_children_young_maps_to_paar_ohne_kind() -> None:
    """Two adults (both 25), no children -> MiD couple_youngest_18_29 -> paar_ohne_kind."""
    persons = _persons([(10, 25), (10, 27)])
    result = derive_hh_type5(persons)
    assert result[10] == "paar_ohne_kind", f"Expected paar_ohne_kind, got {result[10]!r}"


def test_couple_no_children_older_maps_to_paar_ohne_kind() -> None:
    """Two adults (60+), no children -> MiD couple_youngest_60_plus -> paar_ohne_kind."""
    persons = _persons([(11, 62), (11, 65)])
    result = derive_hh_type5(persons)
    assert result[11] == "paar_ohne_kind", f"Expected paar_ohne_kind, got {result[11]!r}"


# ---------------------------------------------------------------------------
# Couple with children -> paar_mit_kind
# ---------------------------------------------------------------------------

def test_couple_with_infant_maps_to_paar_mit_kind() -> None:
    """Two adults + 1 child under 6 -> MiD child_under_6 -> paar_mit_kind."""
    persons = _persons([(20, 35), (20, 33), (20, 3)])
    result = derive_hh_type5(persons)
    assert result[20] == "paar_mit_kind", f"Expected paar_mit_kind, got {result[20]!r}"


def test_couple_with_school_child_maps_to_paar_mit_kind() -> None:
    """Two adults + 1 child aged 8 -> MiD child_under_14 -> paar_mit_kind."""
    persons = _persons([(21, 40), (21, 38), (21, 8)])
    result = derive_hh_type5(persons)
    assert result[21] == "paar_mit_kind", f"Expected paar_mit_kind, got {result[21]!r}"


def test_couple_with_teen_maps_to_paar_mit_kind() -> None:
    """Two adults + 1 teen aged 15 -> MiD child_under_18 -> paar_mit_kind."""
    persons = _persons([(22, 42), (22, 40), (22, 15)])
    result = derive_hh_type5(persons)
    assert result[22] == "paar_mit_kind", f"Expected paar_mit_kind, got {result[22]!r}"


# ---------------------------------------------------------------------------
# Single parent -> alleinerziehend
# ---------------------------------------------------------------------------

def test_single_parent_with_child_maps_to_alleinerziehend() -> None:
    """One adult + 1 child -> MiD single_parent -> alleinerziehend."""
    persons = _persons([(30, 35), (30, 7)])
    result = derive_hh_type5(persons)
    assert result[30] == "alleinerziehend", f"Expected alleinerziehend, got {result[30]!r}"


# ---------------------------------------------------------------------------
# 3+ adults without children -> mehrpers_ohne_kernfamilie
# ---------------------------------------------------------------------------

def test_three_adults_no_children_maps_to_mehrpers_ohne_kernfamilie() -> None:
    """Three adults, no children -> MiD three_plus_adults -> mehrpers_ohne_kernfamilie."""
    persons = _persons([(40, 25), (40, 27), (40, 30)])
    result = derive_hh_type5(persons)
    assert result[40] == "mehrpers_ohne_kernfamilie", (
        f"Expected mehrpers_ohne_kernfamilie, got {result[40]!r}"
    )


# ---------------------------------------------------------------------------
# Mixed household batch: verify correct per-HH assignment across all 5 classes
# ---------------------------------------------------------------------------

def test_mixed_batch_all_five_classes() -> None:
    """All five hh_type5 classes appear correctly in a single batch."""
    persons = pd.DataFrame({
        "household_id": [
            # HH 1: single (einpersonen)
            1,
            # HH 2: couple no kid (paar_ohne_kind)
            2, 2,
            # HH 3: couple + infant (paar_mit_kind)
            3, 3, 3,
            # HH 4: single parent (alleinerziehend)
            4, 4,
            # HH 5: 3 adults (mehrpers_ohne_kernfamilie)
            5, 5, 5,
        ],
        "age": [
            50,            # HH 1
            45, 43,        # HH 2
            35, 33, 2,     # HH 3
            32, 10,        # HH 4
            25, 28, 31,    # HH 5
        ],
    })
    result = derive_hh_type5(persons)
    assert result[1] == "einpersonen"
    assert result[2] == "paar_ohne_kind"
    assert result[3] == "paar_mit_kind"
    assert result[4] == "alleinerziehend"
    assert result[5] == "mehrpers_ohne_kernfamilie"


# ---------------------------------------------------------------------------
# hh_type5 column wiring: select_seed_columns retains hh_type5 when requested
# ---------------------------------------------------------------------------

def test_select_seed_columns_retains_hh_type5() -> None:
    """select_seed_columns with extra_household_cols=('hh_type5',) must retain the column."""
    from braunschweig.popsim.seed import MID_SEED_COLUMNS, select_seed_columns

    households = pd.DataFrame({
        "H_ID": [101, 102],
        "H_GEW": [10.0, 20.0],
        "hh_type5": ["einpersonen", "paar_mit_kind"],
    })
    persons = pd.DataFrame({
        "H_ID": [101, 102, 102],
        "P_ID": [1, 2, 3],
        "P_GEW": [10.0, 20.0, 20.0],
        "HP_ALTER": [35, 40, 38],
        "HP_SEX": [1, 1, 2],
    })

    seed_hh, _ = select_seed_columns(
        households, persons, MID_SEED_COLUMNS,
        extra_household_cols=("hh_type5",),
    )

    assert "hh_type5" in seed_hh.columns, (
        f"hh_type5 missing from seed_households: {list(seed_hh.columns)}"
    )
    hh_map = seed_hh.set_index("H_ID")["hh_type5"].to_dict()
    assert hh_map[101] == "einpersonen"
    assert hh_map[102] == "paar_mit_kind"


# ---------------------------------------------------------------------------
# REGRESSION: derive_hh_type5 must work with REAL MiD column names (H_ID / HP_ALTER)
# ---------------------------------------------------------------------------

def test_derive_hh_type5_with_real_mid_column_names() -> None:
    """Regression: derive_hh_type5 must NOT raise with the real MiD persons frame.

    At the production call site (load_mid_seed in braunschweig/popsim/mid.py),
    the persons frame has raw MiD column names: H_ID (household id) and
    HP_ALTER (person age).  Before the fix, derive_hh_type5 only renamed the
    household_id column but NOT the age column, so map_households_to_hhtype
    raised KeyError: ['age'] not in index.

    This test builds a minimal persons frame using the REAL MiD column names
    (H_ID, HP_ALTER) and asserts correct hh_type5 values without any exception.
    It reproduces the exact column names present on the persons frame at the
    load_mid_seed call site.
    """
    # Minimal persons frame using real MiD column names:
    #   H_ID  = household identifier (columns.person_household_id = "H_ID")
    #   HP_ALTER = person age in completed years (columns.age = "HP_ALTER")
    persons_mid = pd.DataFrame({
        "H_ID": [
            # HH 101: single 40-year-old -> einpersonen
            101,
            # HH 102: couple (35, 33) + child (4) -> paar_mit_kind
            102, 102, 102,
            # HH 103: single parent (30) + child (8) -> alleinerziehend
            103, 103,
        ],
        "HP_ALTER": [
            40,
            35, 33, 4,
            30, 8,
        ],
    })

    # Must not raise KeyError (the production crash before the fix).
    result = derive_hh_type5(
        persons_mid,
        household_id_col="H_ID",  # real MiD household id column
        age_col="HP_ALTER",        # real MiD age column
    )

    assert result[101] == "einpersonen", f"HH 101: expected einpersonen, got {result[101]!r}"
    assert result[102] == "paar_mit_kind", f"HH 102: expected paar_mit_kind, got {result[102]!r}"
    assert result[103] == "alleinerziehend", f"HH 103: expected alleinerziehend, got {result[103]!r}"


def test_derive_hh_type5_real_mid_names_does_not_mutate_input() -> None:
    """derive_hh_type5 must not mutate the caller's persons frame.

    With the age_col rename fix, the rename is applied on a copy so the
    original frame retains its raw MiD column names (HP_ALTER, not age).
    """
    persons_mid = pd.DataFrame({
        "H_ID": [201, 201],
        "HP_ALTER": [45, 43],
    })
    original_cols = list(persons_mid.columns)

    derive_hh_type5(persons_mid, household_id_col="H_ID", age_col="HP_ALTER")

    assert list(persons_mid.columns) == original_cols, (
        f"Input frame was mutated: columns changed from {original_cols} to {list(persons_mid.columns)}"
    )
