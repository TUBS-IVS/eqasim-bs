r"""MiD 2023 'monatliches HH-Nettoeinkommen x Wohnen (Miete/Eigentum) x Region'.

This module backs the completeness household attribute ``housing_tenure`` (see
``braunschweig.synthesis.population.enriched`` and CLAUDE.md). It is a COMPLETENESS
attribute: it is written to the MATSim population for downstream studies but is
NOT YET consumed by the simulation (like the HSN/TSN vehicle engine attributes).
The module provides:

  * the canonical 3-class tenure vocabulary (:data:`TENURE_CATEGORIES` =
    ``rent`` / ``own`` / ``other``) and the German tenure column label -> key map
    (``anderes`` and ``keine Angabe`` both fold into ``other``);
  * loaders for the two committed tidy CSVs
    (``mid2023_income_by_tenure_bundesland.csv`` / ``_raumtyp.csv``);
  * :func:`tenure_probabilities_given_income` -- ``P(tenure | income_bracket,
    region)`` by Bayes, with Niedersachsen (Bundesland table) as the BASE and the
    raumtyp table as a within-NDS multiplicative tilt, exactly mirroring the
    income x size / income x status derivations.

Distribution semantics
----------------------
Both source tables report ``Spalten % (gewichtet)`` with the Wohnen (tenure)
classes as the COLUMNS and the 10 income brackets as the ROWS, so within each
tenure column the bracket percentages sum to 100 %, i.e. each column IS
``P(bracket | tenure, region)``. The ``Basis gewichtet`` row gives the weighted
household base per (tenure, region) cell. The tenure MARGINAL P(tenure | region)
is recovered from those weighted bases.

Bayes inversion
---------------
``housing_tenure`` is sampled per household conditional on its income bracket, so
the needed quantity is ``P(tenure | bracket)``, which is obtained by Bayes:

    P(tenure | bracket, region)
        \propto P(bracket | tenure, region) * P(tenure | region)

with ``P(bracket | tenure, region)`` = the column-% ``share_pct`` and
``P(tenure | region)`` = the weighted-base tenure marginal. Niedersachsen is the
base; the raumtyp table tilts each base ``P(bracket | tenure)`` within NDS before
the inversion (same tilt construction as income_by_size / income_by_status).

The ``anderes`` (other rental/ownership arrangement) and ``keine Angabe`` (no
answer) MiD columns are FOLDED into a single ``other`` class: their weighted bases
are summed and their per-bracket masses are base-weighted-averaged at extract time,
so the committed CSV already carries the 3-class partition. ``keine Angabe`` is a
small residual (NDS ~0.3 %); folding it into ``other`` keeps the partition exact
without inventing a tenure for the non-respondents.

The numeric reference values live in the committed CSVs produced by
``scripts/extract_mid_income_by_tenure.py`` from the local-only raw xlsx;
hard-coding percentages in Python is prohibited (see CLAUDE.md).
"""

from __future__ import annotations

import os
from typing import Iterable

import numpy as np
import pandas as pd

from braunschweig.data.mid.income_by_size import (
    INCOME_BRACKET_CATEGORIES,
    REGION_LABEL_TO_KEY_BUNDESLAND,
    REGION_LABEL_TO_KEY_RAUMTYP,
    BUNDESLAND_NIEDERSACHSEN,
    RS7_TO_RAUMTYP_KEY,
)

MID_SUBDIR = os.path.join("braunschweig", "mid")

# Canonical 3-class tenure vocabulary. ``other`` folds the MiD "anderes" and
# "keine Angabe" columns (see the module docstring).
TENURE_CATEGORIES: tuple[str, ...] = ("rent", "own", "other")

# MiD German tenure column label (lower-cased, ws-normalised) -> canonical key.
# "anderes" and "keine angabe" both fold into ``other`` at extract time.
TENURE_LABEL_TO_KEY: dict[str, str] = {
    "miete": "rent",
    "eigentum": "own",
    "anderes": "other",
    "keine angabe": "other",
}

__all__ = [
    "INCOME_BRACKET_CATEGORIES",
    "TENURE_CATEGORIES",
    "TENURE_LABEL_TO_KEY",
    "REGION_LABEL_TO_KEY_BUNDESLAND",
    "REGION_LABEL_TO_KEY_RAUMTYP",
    "BUNDESLAND_NIEDERSACHSEN",
    "RS7_TO_RAUMTYP_KEY",
    "load_tenure_by_income_bundesland",
    "load_tenure_by_income_raumtyp",
    "tenure_probabilities_given_income",
]


def _path(data_path: str, filename: str) -> str:
    return os.path.join(data_path, MID_SUBDIR, filename)


def _load_tidy(data_path: str, filename: str, region_keys: set[str]) -> pd.DataFrame:
    """Load + validate one tidy tenure-by-income CSV.

    Columns ``[region, tenure, income_bracket, share_pct, base_weighted]``.
    ``share_pct = P(income_bracket | tenure, region)`` in percent (a tenure column
    sums to ~100 over brackets); ``base_weighted`` is the weighted household base
    of the (tenure, region) cell, repeated across bracket rows.
    """
    df = pd.read_csv(_path(data_path, filename), comment="#")
    expected = {"region", "tenure", "income_bracket", "share_pct", "base_weighted"}
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"{filename}: missing columns {sorted(missing)} (have {sorted(df.columns)})"
        )
    bad_bracket = set(df["income_bracket"]) - set(INCOME_BRACKET_CATEGORIES)
    if bad_bracket:
        raise RuntimeError(f"{filename}: unexpected income_bracket keys {sorted(bad_bracket)}")
    bad_tenure = set(df["tenure"].astype(str)) - set(TENURE_CATEGORIES)
    if bad_tenure:
        raise RuntimeError(f"{filename}: unexpected tenure keys {sorted(bad_tenure)}")
    bad_region = set(df["region"]) - region_keys
    if bad_region:
        raise RuntimeError(f"{filename}: unexpected region keys {sorted(bad_region)}")
    df["tenure"] = df["tenure"].astype(str)
    return df


def load_tenure_by_income_bundesland(data_path: str) -> pd.DataFrame:
    """Load the 16-Laender Bundesland tidy table."""
    return _load_tidy(
        data_path,
        "mid2023_income_by_tenure_bundesland.csv",
        set(REGION_LABEL_TO_KEY_BUNDESLAND.values()),
    )


def load_tenure_by_income_raumtyp(data_path: str) -> pd.DataFrame:
    """Load the RegioStaR-7 raumtyp tidy table."""
    return _load_tidy(
        data_path,
        "mid2023_income_by_tenure_raumtyp.csv",
        set(REGION_LABEL_TO_KEY_RAUMTYP.values()),
    )


def _bracket_given_tenure_pmf(
    df: pd.DataFrame,
    region: str,
    tenure: str,
    include_brackets: Iterable[str] = INCOME_BRACKET_CATEGORIES,
) -> np.ndarray | None:
    """Return ``P(bracket | tenure, region)`` as a pmf, or ``None`` if absent."""
    brackets = list(include_brackets)
    sub = df[(df["region"] == region) & (df["tenure"].astype(str) == str(tenure))]
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


def _tenure_marginal(df: pd.DataFrame, region: str) -> np.ndarray | None:
    """Return ``P(tenure | region)`` from the per-(tenure, region) weighted bases.

    Ordered like :data:`TENURE_CATEGORIES`; ``None`` if the region carries no base.
    """
    sub = df[df["region"] == region]
    if sub.empty:
        return None
    base = (
        sub.drop_duplicates("tenure").set_index("tenure")["base_weighted"]
        .reindex(TENURE_CATEGORIES).fillna(0.0).to_numpy(dtype=float)
    )
    total = base.sum()
    if total <= 0:
        return None
    return base / total


def _tilted_bracket_given_tenure(
    df_bundesland: pd.DataFrame,
    df_raumtyp: pd.DataFrame,
    tenure: str,
    raumtyp_region: str | None,
    include_brackets: Iterable[str],
) -> np.ndarray | None:
    """``P(bracket | tenure, NDS)`` with the within-NDS raumtyp tilt applied.

    Mirror of the income_by_size / income_by_status tilt: NDS base scaled by
    ``P_raum,region(bracket|tenure) / P_raum,national(bracket|tenure)``.
    """
    brackets = list(include_brackets)
    base = _bracket_given_tenure_pmf(df_bundesland, BUNDESLAND_NIEDERSACHSEN, tenure, brackets)
    if base is None:
        return None
    if raumtyp_region is None:
        return base.copy()

    region_pmf = _bracket_given_tenure_pmf(df_raumtyp, raumtyp_region, tenure, brackets)
    # National raumtyp pool (base-weighted) for this tenure.
    acc = np.zeros(len(brackets), dtype=float)
    base_total = 0.0
    for region in df_raumtyp["region"].unique():
        pmf = _bracket_given_tenure_pmf(df_raumtyp, region, tenure, brackets)
        if pmf is None:
            continue
        sub = df_raumtyp[
            (df_raumtyp["region"] == region)
            & (df_raumtyp["tenure"].astype(str) == str(tenure))
        ]
        b = float(sub["base_weighted"].drop_duplicates().sum())
        if b <= 0:
            b = 1.0
        acc += b * pmf
        base_total += b
    national_pmf = (acc / base_total) if base_total > 0 else None

    if region_pmf is None or national_pmf is None:
        return base.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        tilt = np.where(national_pmf > 1e-12, region_pmf / national_pmf, 1.0)
    tilted = base * tilt
    total = tilted.sum()
    return (tilted / total) if total > 0 else base.copy()


def tenure_probabilities_given_income(
    df_bundesland: pd.DataFrame,
    df_raumtyp: pd.DataFrame,
    raumtyp_region: str | None,
    include_brackets: Iterable[str] = INCOME_BRACKET_CATEGORIES,
) -> np.ndarray | None:
    r"""Return ``P(tenure | income_bracket, region)`` as a ``(n_brackets, 3)`` matrix.

    For each income bracket the row is the pmf over ``TENURE_CATEGORIES``
    (``rent`` / ``own`` / ``other``) obtained by Bayes:

        P(tenure | bracket) \propto P(bracket | tenure) * P(tenure)

    with ``P(bracket | tenure)`` the column-% (NDS base + raumtyp tilt) and
    ``P(tenure)`` the weighted-base tenure marginal of NDS. Niedersachsen is the
    base region; the raumtyp table tilts the per-tenure bracket conditionals within
    NDS (the tenure marginal stays the NDS marginal -- the raumtyp tilt only
    re-shapes the income mix within each tenure, which is what is identified by the
    income-conditioned table). Each returned row sums to 1.

    Returns ``None`` (caller logs a FALLBACK -- no silent fallback) when the NDS
    base is unavailable. With ``raumtyp_region is None`` the untilted NDS Bayes
    inversion is returned.
    """
    brackets = list(include_brackets)
    n_brackets = len(brackets)
    p_tenure = _tenure_marginal(df_bundesland, BUNDESLAND_NIEDERSACHSEN)
    if p_tenure is None:
        return None

    # Build the joint P(bracket, tenure) = P(bracket | tenure) * P(tenure), then
    # normalise per bracket to get P(tenure | bracket).
    joint = np.zeros((n_brackets, len(TENURE_CATEGORIES)), dtype=float)
    for ti, tenure in enumerate(TENURE_CATEGORIES):
        cond = _tilted_bracket_given_tenure(
            df_bundesland, df_raumtyp, tenure, raumtyp_region, brackets
        )
        if cond is None:
            cond = np.zeros(n_brackets, dtype=float)
        joint[:, ti] = cond * p_tenure[ti]

    row_sums = joint.sum(axis=1, keepdims=True)
    out = np.divide(
        joint, row_sums, out=np.zeros_like(joint), where=row_sums > 0
    )
    # A bracket with zero joint mass across all tenures (no data) -> fall back to
    # the unconditional tenure marginal so every bracket row is a valid pmf.
    empty = (row_sums[:, 0] <= 0)
    if empty.any():
        out[empty, :] = p_tenure
    return out
