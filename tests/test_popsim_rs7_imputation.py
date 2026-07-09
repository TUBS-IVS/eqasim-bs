"""Issue #131: condition MiD item-nonresponse imputation on RegioStaR7.

The one-dimensional conditioning pools (``alter_gr1`` / ``hhgr_gr``) imputed a
rural household's missing licence / PT subscription / cars / income from a
national pool including big-city respondents, although ``RegioStaR7`` sits on
both frames (donor households: survey home region; expanded synthetic persons:
the PLACED home cell's RS7 via ``join_cell_attributes``). These tests pin:

- ``missing.resolve`` multi-column pools draw from the matching (base, RS7)
  group and COUNT global-pool fallbacks (fallback-transparency mandate).
- ``attributes.imputation_group_cols`` appends RS7 only in ADDITION to the
  base column (frames without the base column keep the old empty grouping).
- Mappers condition on RS7 when present; ``rs7_conditioning=False`` is
  byte-identical to a frame without the RS7 column (OFF escape hatch).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.popsim import attributes
from braunschweig.popsim import missing as m


# --------------------------------------------------------------------------- #
# missing.resolve: two-column pools + fallback counting
# --------------------------------------------------------------------------- #

def _two_col_spec() -> m.AttributeSpec:
    return m.AttributeSpec(
        name="attr",
        source_col="code",
        value_map={1: True, 2: False},
        structural={},
        group_cols=("age_band", "RegioStaR7"),
        default=None,
    )


def test_resolve_two_col_grouping_draws_from_matching_pool() -> None:
    # (a, 1) pool is all-True, (a, 7) pool is all-False -> deterministic draws.
    df = pd.DataFrame({
        "code":       [1, 1, 2, 2, 99, 99],
        "age_band":   ["a", "a", "a", "a", "a", "a"],
        "RegioStaR7": [1, 1, 7, 7, 1, 7],
    })
    out, report = m.resolve(df, _two_col_spec(), rng=np.random.RandomState(0))
    assert out.iloc[4] is True    # imputed from the (a, 1) pool
    assert out.iloc[5] is False   # imputed from the (a, 7) pool
    assert report.n_nonresponse == 2
    assert report.n_group_fallback == 0


def test_resolve_counts_global_pool_fallback_for_unseen_group_key() -> None:
    # The nonresponse row's RS7=5 has no valid pool -> global-pool fallback,
    # which must be COUNTED (no silent fallback).
    df = pd.DataFrame({
        "code":       [1, 1, 99],
        "age_band":   ["a", "a", "a"],
        "RegioStaR7": [1, 1, 5],
    })
    out, report = m.resolve(df, _two_col_spec(), rng=np.random.RandomState(0))
    assert out.iloc[2] is True    # global pool is all-True
    assert report.n_group_fallback == 1


def test_resolve_reports_zero_fallback_without_group_cols() -> None:
    df = pd.DataFrame({"code": [1, 99]})
    spec = m.AttributeSpec(
        name="attr", source_col="code", value_map={1: True, 2: False},
        structural={}, group_cols=(), default=None,
    )
    out, report = m.resolve(df, spec, rng=np.random.RandomState(0))
    assert report.n_group_fallback == 0


# --------------------------------------------------------------------------- #
# imputation_group_cols helper
# --------------------------------------------------------------------------- #

def test_imputation_group_cols_appends_rs7_when_present() -> None:
    df = pd.DataFrame({"alter_gr1": [1], "RegioStaR7": [3]})
    assert attributes.imputation_group_cols(df, "alter_gr1") == (
        "alter_gr1", "RegioStaR7",
    )


def test_imputation_group_cols_base_only_when_rs7_absent() -> None:
    df = pd.DataFrame({"alter_gr1": [1]})
    assert attributes.imputation_group_cols(df, "alter_gr1") == ("alter_gr1",)


def test_imputation_group_cols_empty_when_base_absent() -> None:
    # RS7 alone is NOT used: without the base column the grouping stays empty
    # (minimal deviation from the previous per-mapper guards).
    df = pd.DataFrame({"RegioStaR7": [3]})
    assert attributes.imputation_group_cols(df, "alter_gr1") == ()


def test_imputation_group_cols_off_ignores_rs7() -> None:
    df = pd.DataFrame({"alter_gr1": [1], "RegioStaR7": [3]})
    assert attributes.imputation_group_cols(
        df, "alter_gr1", rs7_conditioning=False,
    ) == ("alter_gr1",)


# --------------------------------------------------------------------------- #
# Mapper-level behaviour (person mapper + household mapper)
# --------------------------------------------------------------------------- #

def test_map_employed_conditions_on_rs7() -> None:
    # Same age band everywhere; the RS7=1 pool is all-employed (P_TAET=1), the
    # RS7=7 pool all-not-employed (P_TAET=11 Rentner). The two keine-Angabe rows
    # (99) must be imputed from their OWN RS7 pool.
    persons = pd.DataFrame({
        "P_TAET":     [1, 11, 99, 99],
        "alter_gr1":  ["a", "a", "a", "a"],
        "RegioStaR7": [1, 7, 1, 7],
    })
    out = attributes.map_employed(persons, rng=np.random.RandomState(0))
    assert out["employed"].tolist() == [True, False, True, False]


def test_map_employed_off_is_byte_identical_to_frame_without_rs7() -> None:
    persons = pd.DataFrame({
        "P_TAET":     [1, 11, 99, 99, 1, 11],
        "alter_gr1":  ["a", "a", "a", "b", "b", "b"],
        "RegioStaR7": [1, 7, 1, 7, 1, 7],
    })
    off = attributes.map_employed(
        persons, rng=np.random.RandomState(0), rs7_conditioning=False,
    )
    legacy = attributes.map_employed(
        persons.drop(columns=["RegioStaR7"]), rng=np.random.RandomState(0),
    )
    assert off["employed"].tolist() == legacy["employed"].tolist()


def test_map_number_of_cars_conditions_on_rs7() -> None:
    # Household mapper analogue: hhgr_gr constant, RS7=1 pool all 2-car,
    # RS7=7 pool all 0-car; the 99 rows impute from their own RS7 pool.
    households = pd.DataFrame({
        "H_ANZAUTO":  [2, 0, 99, 99],
        "hhgr_gr":    [1, 1, 1, 1],
        "RegioStaR7": [1, 7, 1, 7],
    })
    out = attributes.map_number_of_cars(households, rng=np.random.RandomState(0))
    assert out["number_of_cars"].tolist() == [2, 0, 2, 0]
