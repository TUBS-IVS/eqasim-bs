r"""MiD 2023 'monatliches HH-Nettoeinkommen x oekonomischer Status x Region'.

This module backs the EMPIRICAL income x economic-status conditioning that
replaces the rank-alignment heuristic in the distribution-based household income
(``household_income_eur``) -- see ``braunschweig.synthesis.population.enriched``
and CLAUDE.md. It provides:

  * the German economic-status column label -> canonical English key map (the same
    5-class :data:`STATUS_CATEGORIES` used elsewhere; re-exported from
    :mod:`braunschweig.data.mid.status_by_hhtype` so income and status agree);
  * loaders for the two committed tidy CSVs
    (``mid2023_income_by_status_bundesland.csv`` / ``_raumtyp.csv``);
  * :func:`income_bracket_probabilities_by_status` -- ``P(bracket | status,
    raumtyp)`` as a pmf over the 10 brackets, with Niedersachsen (from the
    Bundesland table) as the BASE and the raumtyp table as a within-NDS
    multiplicative tilt, exactly mirroring the size derivation in
    :mod:`braunschweig.data.mid.income_by_size`.

Distribution semantics
----------------------
Both source tables report ``Spalten % (gewichtet)`` with economic status as the
COLUMNS and the 10 income brackets as the ROWS, so within each status column the
bracket percentages sum to 100 %, i.e. each column IS
``P(bracket | status, region)``. The ``Basis gewichtet`` row gives the weighted
household base per (status, region) cell (used as the base-weighted national pool
in the tilt denominator). The bracket marginal is a genuine conditional
distribution, so (unlike the cars/status tables) there is no Bayes inversion:
the column-% is already the target pmf.

This is the income x status analogue of
:mod:`braunschweig.data.mid.income_by_size` (income x size). The two
conditionals are combined into a single per-(size, status, region) bracket pmf
by a 2D rake in the enrichment stage (see
``_apply_distribution_income`` / ``braunschweig.ipf.joint_age_size.rake_2d``).

The numeric reference values live in the committed CSVs produced by
``scripts/extract_mid_income_by_status.py`` from the local-only raw xlsx;
hard-coding percentages in Python is prohibited (see CLAUDE.md).
"""

from __future__ import annotations

import os
from typing import Iterable

import numpy as np
import pandas as pd

# Re-use the canonical bracket + region vocabularies + the tilt-denominator pool
# from income_by_size so the two income conditionals share one source of truth.
from braunschweig.data.mid.income_by_size import (
    INCOME_BRACKET_CATEGORIES,
    INCOME_BRACKET_LABEL_TO_KEY,
    REGION_LABEL_TO_KEY_BUNDESLAND,
    REGION_LABEL_TO_KEY_RAUMTYP,
    BUNDESLAND_NIEDERSACHSEN,
    RS7_TO_RAUMTYP_KEY,
)
# Re-use the 5-class economic-status vocabulary + German-label map so income and
# economic_status are encoded identically across the pipeline.
from braunschweig.data.mid.status_by_hhtype import (
    STATUS_CATEGORIES,
    STATUS_LABEL_TO_KEY,
)

MID_SUBDIR = os.path.join("braunschweig", "mid")

__all__ = [
    "INCOME_BRACKET_CATEGORIES",
    "STATUS_CATEGORIES",
    "STATUS_LABEL_TO_KEY",
    "REGION_LABEL_TO_KEY_BUNDESLAND",
    "REGION_LABEL_TO_KEY_RAUMTYP",
    "BUNDESLAND_NIEDERSACHSEN",
    "RS7_TO_RAUMTYP_KEY",
    "load_income_by_status_bundesland",
    "load_income_by_status_raumtyp",
    "income_bracket_probabilities_by_status",
    "overall_bracket_pmf",
    "combine_size_status_bracket_pmf",
]


def _path(data_path: str, filename: str) -> str:
    return os.path.join(data_path, MID_SUBDIR, filename)


def _load_tidy(data_path: str, filename: str, region_keys: set[str]) -> pd.DataFrame:
    """Load + validate one tidy income-by-status CSV.

    Columns ``[region, status, income_bracket, share_pct, base_weighted]``.
    ``share_pct = P(income_bracket | status, region)`` in percent (a status column
    sums to ~100 over brackets); ``base_weighted`` is the weighted household base
    of the (status, region) cell, repeated across bracket rows.
    """
    df = pd.read_csv(_path(data_path, filename), comment="#")
    expected = {"region", "status", "income_bracket", "share_pct", "base_weighted"}
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"{filename}: missing columns {sorted(missing)} (have {sorted(df.columns)})"
        )
    bad_bracket = set(df["income_bracket"]) - set(INCOME_BRACKET_CATEGORIES)
    if bad_bracket:
        raise RuntimeError(f"{filename}: unexpected income_bracket keys {sorted(bad_bracket)}")
    bad_status = set(df["status"].astype(str)) - set(STATUS_CATEGORIES)
    if bad_status:
        raise RuntimeError(f"{filename}: unexpected status keys {sorted(bad_status)}")
    bad_region = set(df["region"]) - region_keys
    if bad_region:
        raise RuntimeError(f"{filename}: unexpected region keys {sorted(bad_region)}")
    df["status"] = df["status"].astype(str)
    return df


def load_income_by_status_bundesland(data_path: str) -> pd.DataFrame:
    """Load the 16-Laender Bundesland tidy table."""
    return _load_tidy(
        data_path,
        "mid2023_income_by_status_bundesland.csv",
        set(REGION_LABEL_TO_KEY_BUNDESLAND.values()),
    )


def load_income_by_status_raumtyp(data_path: str) -> pd.DataFrame:
    """Load the RegioStaR-7 raumtyp tidy table."""
    return _load_tidy(
        data_path,
        "mid2023_income_by_status_raumtyp.csv",
        set(REGION_LABEL_TO_KEY_RAUMTYP.values()),
    )


def _bracket_pmf_for_region_status(
    df: pd.DataFrame,
    region: str,
    status: str,
    include_brackets: Iterable[str] = INCOME_BRACKET_CATEGORIES,
) -> np.ndarray | None:
    """Return ``P(bracket | status, region)`` as a pmf, or ``None`` if absent.

    ``share_pct`` is already the column-% conditional, so the pmf is the
    bracket-ordered share vector renormalised to sum to 1 (MiD rounds to integer
    percent, so the raw vector sums to ~100). Returns ``None`` when the
    (region, status) cell carries no positive mass (absent / fully suppressed).
    """
    brackets = list(include_brackets)
    sub = df[(df["region"] == region) & (df["status"].astype(str) == str(status))]
    if sub.empty:
        return None
    vec = (
        sub.set_index("income_bracket")["share_pct"]
        .reindex(brackets).fillna(0.0).to_numpy(dtype=float)
    )
    total = vec.sum()
    if total <= 0:
        return None
    return vec / total


def _national_raumtyp_pmf(
    df_raumtyp: pd.DataFrame,
    status: str,
    include_brackets: Iterable[str] = INCOME_BRACKET_CATEGORIES,
) -> np.ndarray | None:
    """Return the base-weighted national raumtyp pmf for one status.

    Pools the raumtyp regions by their per-(status, region) weighted base so the
    denominator of the within-NDS tilt is the national (within-table) bracket
    distribution at that economic status (mirror of the size analogue):

        P_national(bracket | status)
            = sum_r base(status, r) * P_r(bracket | status)
              / sum_r base(status, r)

    Returns ``None`` if no raumtyp region has positive mass at this status.
    """
    brackets = list(include_brackets)
    acc = np.zeros(len(brackets), dtype=float)
    base_total = 0.0
    for region in df_raumtyp["region"].unique():
        pmf = _bracket_pmf_for_region_status(df_raumtyp, region, status, brackets)
        if pmf is None:
            continue
        sub = df_raumtyp[
            (df_raumtyp["region"] == region)
            & (df_raumtyp["status"].astype(str) == str(status))
        ]
        base = float(sub["base_weighted"].drop_duplicates().sum())
        if base <= 0:
            base = 1.0  # degenerate base -> equal weight (does not zero a region)
        acc += base * pmf
        base_total += base
    if base_total <= 0:
        return None
    return acc / base_total


def income_bracket_probabilities_by_status(
    df_bundesland: pd.DataFrame,
    df_raumtyp: pd.DataFrame,
    status: str,
    raumtyp_region: str | None,
    include_brackets: Iterable[str] = INCOME_BRACKET_CATEGORIES,
) -> np.ndarray | None:
    """Return ``P(bracket | status, raumtyp)`` as a pmf over the 10 brackets.

    Niedersachsen (Bundesland table) is the BASE; the raumtyp table is a
    within-NDS multiplicative tilt (see the module docstring). The returned
    vector is ordered like :data:`INCOME_BRACKET_CATEGORIES` and sums to 1.

    Returns ``None`` (caller logs a FALLBACK -- no silent fallback) when the NDS
    base cell for ``status`` is absent. With ``raumtyp_region is None`` or absent
    from the raumtyp table, the untilted NDS base is returned.
    """
    brackets = list(include_brackets)
    base = _bracket_pmf_for_region_status(
        df_bundesland, BUNDESLAND_NIEDERSACHSEN, status, brackets
    )
    if base is None:
        return None

    if raumtyp_region is None:
        return base.copy()

    region_pmf = _bracket_pmf_for_region_status(df_raumtyp, raumtyp_region, status, brackets)
    national_pmf = _national_raumtyp_pmf(df_raumtyp, status, brackets)
    if region_pmf is None or national_pmf is None:
        return base.copy()

    with np.errstate(divide="ignore", invalid="ignore"):
        tilt = np.where(national_pmf > 1e-12, region_pmf / national_pmf, 1.0)
    tilted = base * tilt
    total = tilted.sum()
    return (tilted / total) if total > 0 else base.copy()


def overall_bracket_pmf(
    df_bundesland: pd.DataFrame,
    df_raumtyp: pd.DataFrame,
    raumtyp_region: str | None,
    include_brackets: Iterable[str] = INCOME_BRACKET_CATEGORIES,
) -> np.ndarray | None:
    r"""Return the overall ``P(bracket | region)`` marginal (status-pooled).

    Base-weighted pool of the per-status NDS conditionals (the same construction
    as :func:`_national_raumtyp_pmf` but over the status dimension), giving the
    region-level bracket marginal needed as the denominator of
    :func:`combine_size_status_bracket_pmf`. The raumtyp within-NDS tilt is
    applied exactly as in :func:`income_bracket_probabilities_by_status` so the
    overall marginal lives at the SAME (NDS + raumtyp) region resolution as the
    two conditionals it reconciles. Returns ``None`` if NDS carries no positive
    mass at any status.
    """
    brackets = list(include_brackets)
    acc = np.zeros(len(brackets), dtype=float)
    base_total = 0.0
    nds = df_bundesland[df_bundesland["region"] == BUNDESLAND_NIEDERSACHSEN]
    for status in nds["status"].astype(str).unique():
        pmf = income_bracket_probabilities_by_status(
            df_bundesland, df_raumtyp, status, raumtyp_region, brackets
        )
        if pmf is None:
            continue
        sub = nds[nds["status"].astype(str) == status]
        base = float(sub["base_weighted"].drop_duplicates().sum())
        if base <= 0:
            base = 1.0
        acc += base * pmf
        base_total += base
    if base_total <= 0:
        return None
    total = acc.sum()
    return (acc / total) if total > 0 else None


def combine_size_status_bracket_pmf(
    pmf_by_size: np.ndarray,
    pmf_by_status: np.ndarray,
    overall_pmf: np.ndarray,
) -> np.ndarray:
    r"""Reconcile two bracket conditionals into ``P(bracket | size, status)``.

    Given two empirical conditional bracket distributions on the SAME 10-bracket
    support -- ``P(bracket | hh_size, region)`` (income_by_size) and
    ``P(bracket | status, region)`` (income_by_status) -- and the overall bracket
    marginal ``P(bracket | region)``, returns the per-cell bracket pmf consistent
    with BOTH conditionals.

    Two different full distributions on one axis cannot be matched exactly by a
    single vector, so "match both" is the iterative-proportional-fitting (raking)
    COMPROMISE. For two conditionals on a shared variable that compromise has the
    closed-form odds-multiplication (conditional-independence) estimator

        P(bracket | size, status)
            \propto P(bracket | size) * P(bracket | status) / P(bracket)

    which is the unique fixed point of alternately scaling a seed toward each
    conditional (the two scalings are ``P(b|size)/P(b)`` and ``P(b|status)/P(b)``;
    applying both once and renormalising reaches the fixed point because each
    scaling is idempotent given the other). It is the standard way to combine two
    conditionals under the assumption that size and status are independent GIVEN
    the income bracket (the bracket is the common cause). The shared overall
    marginal ``P(bracket)`` removes the double-counted base level so a bracket
    favoured by BOTH size and status is reinforced while one favoured by neither
    is suppressed.

    Aggregating the resulting per-cell pmf over status (weighted by the size-cell
    composition) reproduces ``P(bracket | size)``, and aggregating over size
    reproduces ``P(bracket | status)`` -- so the realised synthetic income is
    consistent with both empirical conditionals (within the rounding tolerance).
    All three inputs must be pmfs (sum to 1) over the bracket support in the
    canonical order; the result is a renormalised pmf in the same order.

    A bracket impossible under EITHER conditional (mass 0) stays 0 (it is
    impossible in the cell). The eps floor on ``overall`` only guards a 0/0 where
    a bracket has zero overall mass but positive conditional mass.
    """
    eps = 1e-12
    size = np.asarray(pmf_by_size, dtype=float)
    status = np.asarray(pmf_by_status, dtype=float)
    overall = np.maximum(np.asarray(overall_pmf, dtype=float), eps)

    combined = size * status / overall
    total = combined.sum()
    if total <= 0:
        # Both conditionals disjoint on the bracket support (degenerate); fall
        # back to the size conditional so the caller still gets a valid pmf.
        s_total = size.sum()
        return (size / s_total) if s_total > 0 else np.full_like(size, 1.0 / len(size))
    return combined / total
