"""Tests for popsim zone-ID derivation (bug D1 fix).

Verifies that ``derive_zone_ids`` and ``build_persons`` produce ``commune_id``,
``departement_id``, and ``iris_id`` in the format required by
``synthesis.population.spatial.home.zones``, matching the default IPF producer
(``braunschweig.ipf.attributed`` lines 814-816, ``braunschweig.ipf.prepare``
line 126).

The default IPF producer format:
  commune_id     -- 8-digit AGS string, e.g. "03101000"
  departement_id -- 5-digit Kreis string = commune_id[:5], e.g. "03101"
  iris_id        -- commune_id + "0000" as a category, e.g. "031010000000"

This module tests the small helper ``derive_zone_ids`` directly (so it runs
without the full pipeline) and also exercises it through ``build_persons``
with a realistic 12-digit ARS.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.data.bbsr.regiostar import ars_to_ags8
from braunschweig.popsim import assembly
from braunschweig.popsim.assembly import ARS_COLUMN, derive_zone_ids


# ---------------------------------------------------------------------------
# Known Braunschweig ARS values for the tests
# ---------------------------------------------------------------------------

# Braunschweig city (kreisfreie Stadt 03101, Gemeinde suffix 000):
#   12-digit ARS  = "031010000000"
#   8-digit AGS   = "03101000"      (ARS[0:5] + ARS[9:12])
#   Kreis prefix  = "03101"
BS_ARS = "031010000000"
BS_AGS8 = "03101000"
BS_KREIS = "03101"

# Salzgitter (03102), a second Kreis to test different departement_id values:
SZ_ARS = "031020000000"
SZ_AGS8 = "03102000"
SZ_KREIS = "03102"


# ---------------------------------------------------------------------------
# Tests for derive_zone_ids helper
# ---------------------------------------------------------------------------

def test_derive_zone_ids_commune_id_equals_ars_to_ags8():
    """commune_id must equal ars_to_ags8(ARS) for every row."""
    df = pd.DataFrame({
        ARS_COLUMN: [BS_ARS, SZ_ARS],
        "other_col": [1, 2],
    })
    out = derive_zone_ids(df)

    assert "commune_id" in out.columns, "commune_id column must be present"
    assert out.loc[0, "commune_id"] == ars_to_ags8(BS_ARS), (
        f"commune_id row 0: expected {ars_to_ags8(BS_ARS)!r}, "
        f"got {out.loc[0, 'commune_id']!r}"
    )
    assert out.loc[1, "commune_id"] == ars_to_ags8(SZ_ARS), (
        f"commune_id row 1: expected {ars_to_ags8(SZ_ARS)!r}, "
        f"got {out.loc[1, 'commune_id']!r}"
    )


def test_derive_zone_ids_commune_id_is_8_digit_ags():
    """commune_id must be a zero-padded 8-digit AGS string."""
    df = pd.DataFrame({ARS_COLUMN: [BS_ARS, SZ_ARS]})
    out = derive_zone_ids(df)

    assert (out["commune_id"] == BS_AGS8).iloc[0], (
        f"Expected commune_id={BS_AGS8!r}, got {out['commune_id'].iloc[0]!r}"
    )
    assert (out["commune_id"] == SZ_AGS8).iloc[1], (
        f"Expected commune_id={SZ_AGS8!r}, got {out['commune_id'].iloc[1]!r}"
    )
    # commune_id length must be 8 for all rows
    lengths = out["commune_id"].str.len().unique().tolist()
    assert lengths == [8], f"All commune_id values must be 8 chars; got lengths {lengths}"


def test_derive_zone_ids_departement_id_is_kreis_prefix():
    """departement_id must equal the first 5 chars of commune_id (Kreis prefix).

    Matches braunschweig.ipf.prepare line 126:
        df_population["commune_id"].str[:5]
    """
    df = pd.DataFrame({ARS_COLUMN: [BS_ARS, SZ_ARS]})
    out = derive_zone_ids(df)

    assert "departement_id" in out.columns, "departement_id column must be present"
    assert out.loc[0, "departement_id"] == BS_KREIS, (
        f"departement_id row 0: expected {BS_KREIS!r}, got {out.loc[0, 'departement_id']!r}"
    )
    assert out.loc[1, "departement_id"] == SZ_KREIS, (
        f"departement_id row 1: expected {SZ_KREIS!r}, got {out.loc[1, 'departement_id']!r}"
    )
    # departement_id must be exactly 5 chars (Kreis-5 format)
    lengths = out["departement_id"].str.len().unique().tolist()
    assert lengths == [5], f"All departement_id values must be 5 chars; got lengths {lengths}"


def test_derive_zone_ids_departement_id_matches_commune_id_prefix():
    """departement_id must always equal commune_id[:5] (consistency check)."""
    df = pd.DataFrame({ARS_COLUMN: [BS_ARS, SZ_ARS]})
    out = derive_zone_ids(df)

    mismatch = out[out["departement_id"] != out["commune_id"].str[:5]]
    assert mismatch.empty, (
        f"departement_id != commune_id[:5] for rows: {mismatch[['commune_id', 'departement_id']].to_dict()}"
    )


def test_derive_zone_ids_iris_id_is_commune_id_plus_four_zeros():
    """iris_id must equal commune_id + '0000'.

    Matches braunschweig.ipf.attributed lines 815-816:
        df["iris_id"] = df["commune_id"] + "0000"
        df["iris_id"] = df["iris_id"].astype("category")

    Germany has no sub-commune IRIS zones; "0000" is the eqasim placeholder used
    by the default IPF producer.
    """
    df = pd.DataFrame({ARS_COLUMN: [BS_ARS, SZ_ARS]})
    out = derive_zone_ids(df)

    assert "iris_id" in out.columns, "iris_id column must be present"
    expected_bs = BS_AGS8 + "0000"
    expected_sz = SZ_AGS8 + "0000"
    assert str(out.loc[0, "iris_id"]) == expected_bs, (
        f"iris_id row 0: expected {expected_bs!r}, got {str(out.loc[0, 'iris_id'])!r}"
    )
    assert str(out.loc[1, "iris_id"]) == expected_sz, (
        f"iris_id row 1: expected {expected_sz!r}, got {str(out.loc[1, 'iris_id'])!r}"
    )


def test_derive_zone_ids_iris_id_is_category():
    """iris_id must be stored as a categorical (matching ipf/attributed.py)."""
    df = pd.DataFrame({ARS_COLUMN: [BS_ARS, SZ_ARS]})
    out = derive_zone_ids(df)

    assert hasattr(out["iris_id"], "cat"), (
        f"iris_id dtype must be category; got {out['iris_id'].dtype}"
    )


def test_derive_zone_ids_no_nulls():
    """All three zone ID columns must be non-null for a valid ARS."""
    df = pd.DataFrame({ARS_COLUMN: [BS_ARS, SZ_ARS]})
    out = derive_zone_ids(df)

    for col in ("commune_id", "departement_id", "iris_id"):
        assert out[col].notna().all(), f"{col} has unexpected null values"


def test_derive_zone_ids_raises_on_missing_ars_column():
    """derive_zone_ids must raise KeyError when the ARS column is absent.

    A missing ARS column means stage.py did not join it before calling
    build_persons; the error must be informative (not a bare pandas KeyError).
    """
    df = pd.DataFrame({"ZENSUS100m": ["A"], "H_ID": [1]})
    with pytest.raises(KeyError, match=r"RegionalSchlussel_ARS"):
        derive_zone_ids(df)


def test_derive_zone_ids_idempotent_for_8digit_ars_input():
    """ars_to_ags8 is idempotent on 8-digit input; derive_zone_ids must follow."""
    # If the ARS column already contains an 8-digit AGS (unusual but possible
    # in synthetic test data), the result must still be a valid commune_id.
    df = pd.DataFrame({ARS_COLUMN: [BS_AGS8]})
    out = derive_zone_ids(df)

    assert out.loc[0, "commune_id"] == BS_AGS8, (
        f"8-digit ARS input should be returned unchanged; got {out.loc[0, 'commune_id']!r}"
    )
    assert out.loc[0, "departement_id"] == BS_KREIS


# ---------------------------------------------------------------------------
# Integration: build_persons emits zone ID columns
# ---------------------------------------------------------------------------

def _merged_households_with_ars():
    """Minimal merged PopulationSim output with the ARS column attached."""
    return pd.DataFrame({
        "ZENSUS100m": ["A", "B"],
        "ZENSUS1km": ["KA", "KB"],
        "H_ID": [1, 2],
        # Braunschweig 12-digit ARS for both households.
        ARS_COLUMN: [BS_ARS, BS_ARS],
    })


def _mid_persons():
    return pd.DataFrame({
        "H_ID":      [1,  2],
        "P_ID":      [1,  1],
        "HP_ALTER":  [40, 25],
        "HP_SEX":    [1,  1],
        "P_TAET":    [1,  8],
        "P_FSCHEIN": [1,  1],
        "P_FKARTE":  [3,  1],
        "P_BKAT":    [1,  7],
    })


def _mid_households():
    return pd.DataFrame({
        "H_ID":       [1, 2],
        "oek_status": [3, 5],
        "hheink_gr1": [4, 15],
        "H_ANZAUTO":  [1, 0],
        "H_ANZRAD":   [2, 1],
    })


def test_build_persons_emits_commune_id():
    """build_persons must produce a non-null commune_id equal to ars_to_ags8(ARS)."""
    persons = assembly.build_persons(
        _merged_households_with_ars(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    assert "commune_id" in persons.columns, "build_persons must emit commune_id"
    assert persons["commune_id"].notna().all(), "commune_id must be non-null"
    assert (persons["commune_id"] == BS_AGS8).all(), (
        f"commune_id should be {BS_AGS8!r}; got {persons['commune_id'].unique().tolist()}"
    )


def test_build_persons_emits_departement_id():
    """build_persons must produce departement_id = commune_id[:5] (Kreis prefix)."""
    persons = assembly.build_persons(
        _merged_households_with_ars(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    assert "departement_id" in persons.columns, "build_persons must emit departement_id"
    assert persons["departement_id"].notna().all(), "departement_id must be non-null"
    assert (persons["departement_id"] == BS_KREIS).all(), (
        f"departement_id should be {BS_KREIS!r}; got {persons['departement_id'].unique().tolist()}"
    )


def test_build_persons_emits_iris_id():
    """build_persons must produce iris_id = commune_id + '0000' (eqasim placeholder)."""
    persons = assembly.build_persons(
        _merged_households_with_ars(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    assert "iris_id" in persons.columns, "build_persons must emit iris_id"
    assert persons["iris_id"].notna().all(), "iris_id must be non-null"
    expected_iris = BS_AGS8 + "0000"
    assert (persons["iris_id"].astype(str) == expected_iris).all(), (
        f"iris_id should be {expected_iris!r}; got {persons['iris_id'].unique().tolist()}"
    )


def test_build_persons_zone_ids_consistent():
    """commune_id / departement_id / iris_id must be mutually consistent."""
    persons = assembly.build_persons(
        _merged_households_with_ars(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    # departement_id must equal commune_id[:5]
    mismatch_dep = persons[persons["departement_id"] != persons["commune_id"].str[:5]]
    assert mismatch_dep.empty, (
        f"departement_id != commune_id[:5] for {len(mismatch_dep)} persons"
    )
    # iris_id must equal commune_id + "0000"
    expected = persons["commune_id"] + "0000"
    mismatch_iris = persons[persons["iris_id"].astype(str) != expected]
    assert mismatch_iris.empty, (
        f"iris_id != commune_id + '0000' for {len(mismatch_iris)} persons"
    )


def test_build_persons_zone_ids_for_two_kreise():
    """Zone IDs must reflect the per-household ARS when two Kreise are present."""
    merged = pd.DataFrame({
        "ZENSUS100m": ["A", "B"],
        "ZENSUS1km": ["KA", "KB"],
        "H_ID": [1, 2],
        ARS_COLUMN: [BS_ARS, SZ_ARS],  # Braunschweig and Salzgitter
    })
    persons = assembly.build_persons(
        merged, _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    hh_a = persons[persons["household_id"].str.startswith("A_")]
    hh_b = persons[persons["household_id"].str.startswith("B_")]

    assert (hh_a["commune_id"] == BS_AGS8).all(), (
        f"Household A should have commune_id={BS_AGS8!r}"
    )
    assert (hh_a["departement_id"] == BS_KREIS).all(), (
        f"Household A should have departement_id={BS_KREIS!r}"
    )
    assert (hh_b["commune_id"] == SZ_AGS8).all(), (
        f"Household B should have commune_id={SZ_AGS8!r}"
    )
    assert (hh_b["departement_id"] == SZ_KREIS).all(), (
        f"Household B should have departement_id={SZ_KREIS!r}"
    )
