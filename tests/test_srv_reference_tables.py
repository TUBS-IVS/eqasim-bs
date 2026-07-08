"""Tests for the SrV 2023 Braunschweig+RGB aggregate reference-table CSVs.

Covers:
  * Presence and column schema of all 7 committed CSVs written by
    ``scripts/extract_srv_kreis_tables.py``.
  * Structural sanity: exactly the 7 ZGB Kreise (Wolfsburg 03103 absent) at
    level=kreis, shares in [0, 1] and summing to ~1 where applicable, and
    positive n columns.
  * Value pins against a verified first-pass analysis of the committed raw
    microdata (mirrors the pin style used in ``test_mid_reference_tables.py``
    for the MiD tables). These tests read only the COMMITTED CSVs and do not
    require the (local-only) raw SrV microdata.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
SRV = REPO / "eqasim-data" / "data" / "braunschweig" / "srv"

EXPECTED_KREIS_CODES = {"03101", "03102", "03151", "03153", "03154", "03157", "03158"}
WOLFSBURG_KREIS_CODE = "03103"

KREIS_LEVEL_TABLES = [
    "srv2023_cars_by_kreis.csv",
    "srv2023_bikes_incl_ebikes_by_kreis.csv",
    "srv2023_ebike_household_by_kreis.csv",
    "srv2023_income5_by_kreis.csv",
    "srv2023_car_license_17plus_by_kreis.csv",
    "srv2023_dticket_by_kreis.csv",
]

ALL_TABLES = KREIS_LEVEL_TABLES + ["srv2023_covered_municipalities.csv"]

# Columns whose values, per row, are expected to sum to ~1 (a share
# breakdown). Tables not listed here (e.g. the single-share tables) are
# checked individually below instead.
SHARE_GROUP_COLUMNS = {
    "srv2023_cars_by_kreis.csv": ["cars_0", "cars_1", "cars_2", "cars_3plus"],
    "srv2023_bikes_incl_ebikes_by_kreis.csv":
        ["bikes_0", "bikes_1", "bikes_2", "bikes_3", "bikes_4plus"],
    "srv2023_income5_by_kreis.csv": [
        "income_lt_1500", "income_1500_2600", "income_2600_3600",
        "income_3600_5600", "income_ge_5600",
    ],
}

SINGLE_SHARE_COLUMNS = {
    "srv2023_ebike_household_by_kreis.csv": "share_hh_with_ebike",
    "srv2023_car_license_17plus_by_kreis.csv": "share_with_license",
    "srv2023_dticket_by_kreis.csv": "share_deutschlandticket",
}


# ---------------------------------------------------------------------------
# Presence and schema
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname", ALL_TABLES)
def test_csv_exists(fname):
    assert (SRV / fname).exists(), (
        f"{fname} is missing -- run scripts/extract_srv_kreis_tables.py"
    )


@pytest.mark.parametrize("fname", KREIS_LEVEL_TABLES)
def test_kreis_level_schema(fname):
    df = pd.read_csv(SRV / fname, comment="#")
    assert {"level", "code", "name", "n_unweighted", "n_weighted"} <= set(df.columns)
    assert set(df["level"].unique()) <= {"total", "kreis", "stratum"}
    assert "total" in df["level"].values

    kreis_codes = set(df.loc[df["level"] == "kreis", "code"].astype(str))
    assert kreis_codes == EXPECTED_KREIS_CODES, (
        f"{fname}: expected exactly the 7 ZGB Kreise {sorted(EXPECTED_KREIS_CODES)}, "
        f"got {sorted(kreis_codes)}"
    )

    assert (df["n_unweighted"] > 0).all(), f"{fname}: n_unweighted must be positive"
    assert (df["n_weighted"] > 0).all(), f"{fname}: n_weighted must be positive"


def test_wolfsburg_not_covered():
    """Wolfsburg (03103) must never appear as a Kreis row -- the SrV BS+RGB
    add-on survey does not cover it (see srv2023_raw/README.md)."""
    for fname in KREIS_LEVEL_TABLES:
        df = pd.read_csv(SRV / fname, comment="#")
        kreis_codes = set(df.loc[df["level"] == "kreis", "code"].astype(str))
        assert WOLFSBURG_KREIS_CODE not in kreis_codes, (
            f"{fname}: Wolfsburg ({WOLFSBURG_KREIS_CODE}) unexpectedly present"
        )
        # Also guard the stratum-level codes never leak a Wolfsburg municipality.
        assert WOLFSBURG_KREIS_CODE not in set(df["code"].astype(str))


@pytest.mark.parametrize("fname,columns", SHARE_GROUP_COLUMNS.items())
def test_share_group_columns_sum_to_one(fname, columns):
    df = pd.read_csv(SRV / fname, comment="#")
    for col in columns:
        assert df[col].between(0.0, 1.0).all(), f"{fname}: {col} outside [0, 1]"
    totals = df[columns].sum(axis=1)
    assert (totals.sub(1.0).abs() < 0.01).all(), (
        f"{fname}: row shares do not sum to ~1 (max deviation "
        f"{totals.sub(1.0).abs().max():.4f})"
    )


@pytest.mark.parametrize("fname,column", SINGLE_SHARE_COLUMNS.items())
def test_single_share_column_in_unit_interval(fname, column):
    df = pd.read_csv(SRV / fname, comment="#")
    assert df[column].between(0.0, 1.0).all(), f"{fname}: {column} outside [0, 1]"


def test_income_missing_share_in_unit_interval():
    df = pd.read_csv(SRV / "srv2023_income5_by_kreis.csv", comment="#")
    assert "share_income_missing" in df.columns
    assert df["share_income_missing"].between(0.0, 1.0).all()
    # Missing income response is a real, non-trivial fraction of this survey
    # (see the extraction log); the total row must reflect that, not be ~0.
    total_row = df[df["level"] == "total"].iloc[0]
    assert total_row["share_income_missing"] > 0.05


def test_covered_municipalities_schema():
    # kreis_code/ags are zero-padded numeric strings (e.g. "03101"); without
    # an explicit dtype, pandas would infer int64 and silently drop the
    # leading zero since this column, unlike the by-Kreis tables' "code"
    # column, never mixes in a non-numeric level like "total" or "stratum".
    df = pd.read_csv(
        SRV / "srv2023_covered_municipalities.csv", comment="#",
        dtype={"kreis_code": str, "ags": str},
    )
    assert list(df.columns) == [
        "kreis_code", "ags", "municipality_name", "n_households_unweighted",
    ]
    assert (df["n_households_unweighted"] > 0).all()
    assert set(df["kreis_code"].astype(str)) == EXPECTED_KREIS_CODES
    assert WOLFSBURG_KREIS_CODE not in set(df["kreis_code"].astype(str))
    # README.md documents ~44 sampled municipalities.
    assert len(df) == 44


# ---------------------------------------------------------------------------
# Value pins (verified first-pass analysis of the committed raw microdata)
# ---------------------------------------------------------------------------

_PIN_TOLERANCE = 0.003  # 0.3 percentage points, as fractions.


def _kreis_row(fname, code):
    df = pd.read_csv(SRV / fname, comment="#")
    row = df[(df["level"] == "kreis") & (df["code"].astype(str) == code)]
    assert not row.empty, f"{fname}: no kreis row for code {code!r}"
    return row.iloc[0]


def test_pin_cars_braunschweig():
    row = _kreis_row("srv2023_cars_by_kreis.csv", "03101")
    assert row["n_unweighted"] == 2079
    assert abs(row["cars_0"] - 0.215) < _PIN_TOLERANCE
    assert abs(row["cars_1"] - 0.584) < _PIN_TOLERANCE
    assert abs(row["cars_2"] - 0.173) < _PIN_TOLERANCE
    assert abs(row["cars_3plus"] - 0.028) < _PIN_TOLERANCE


def test_pin_cars_salzgitter():
    row = _kreis_row("srv2023_cars_by_kreis.csv", "03102")
    assert row["n_unweighted"] == 791
    assert abs(row["cars_0"] - 0.143) < _PIN_TOLERANCE


def test_pin_ebike_household_gifhorn_and_braunschweig():
    gifhorn = _kreis_row("srv2023_ebike_household_by_kreis.csv", "03151")
    assert abs(gifhorn["share_hh_with_ebike"] - 0.3108) < _PIN_TOLERANCE

    braunschweig = _kreis_row("srv2023_ebike_household_by_kreis.csv", "03101")
    assert abs(braunschweig["share_hh_with_ebike"] - 0.181) < _PIN_TOLERANCE


def test_pin_license_braunschweig():
    row = _kreis_row("srv2023_car_license_17plus_by_kreis.csv", "03101")
    assert row["n_unweighted"] == 3706
    assert abs(row["share_with_license"] - 0.921) < _PIN_TOLERANCE


def test_pin_dticket_braunschweig():
    row = _kreis_row("srv2023_dticket_by_kreis.csv", "03101")
    assert abs(row["share_deutschlandticket"] - 0.088) < _PIN_TOLERANCE
