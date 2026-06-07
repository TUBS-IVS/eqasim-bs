r"""MiD 2023 'Anzahl Autos im HH x oekonomischer Status x Haushaltstyp / Raumtyp'.

This module backs the income/household-type-aware household car count
(``number_of_cars``) -- see ``braunschweig.synthesis.population.enriched`` and
CLAUDE.md. It provides:

  * the canonical car-count vocabulary (``CAR_COUNT_CATEGORIES = (0, 1, 2, 3)``,
    where ``3`` means "3 or more" to match the MiD H7 control) and the German
    block-header -> car-count map used by the extract script;
  * loaders for the two committed tidy CSVs
    (``mid2023_cars_by_status_hhtype.csv`` / ``mid2023_cars_by_raumtyp.csv``);
  * :func:`cars_probabilities` -- ``P(num_cars | hhtype, status, raumtyp)`` by
    Bayes, with the (national) status x hhtype table as the BASE and the raumtyp
    table as a within-region multiplicative tilt, exactly mirroring the
    status x hhtype derivation in
    :mod:`braunschweig.data.mid.status_by_hhtype`.

Bayes derivation
----------------
We want a per-household pmf over the car-count categories. From the status x
hhtype block table we read

    s(hhtype | num_cars, status) = share_pct        (column %)
    b(num_cars, status)          = base_weighted     (weighted household base)

By Bayes within a status column (treated as the BASE distribution; the MiD car
tables are exported for Germany, so the national table is the base -- there is no
Bundesland breakdown for the car cross-tabs, unlike status x hhtype where the
Bundesland table was the NDS base):

    P(num_cars | hhtype, status)
        \propto s(hhtype | num_cars, status) * P(num_cars | status)

with ``P(num_cars | status) = b(num_cars, status) / sum_nc b(nc, status)``.

The raumtyp table supplies ONLY a per-(num_cars, raumtyp) weighted base
``br(num_cars, raumtyp)`` (its share rows are the trivial 100 %), from which

    P(num_cars | raumtyp)  = br(num_cars, raumtyp) / sum_nc br(nc, raumtyp)
    P(num_cars | national) = sum_r br(num_cars, r)  / sum_{nc,r} br(nc, r)

give the within-region tilt

    tilt(num_cars) = P(num_cars | raumtyp) / P(num_cars | national)

and the final pmf is the base re-weighted by the tilt and renormalised:

    P(num_cars | hhtype, status, raumtyp)
        \propto P(num_cars | hhtype, status) * tilt(num_cars)

With ``raumtyp_region is None`` (no RS7 available) the raw base is returned
(tilt = 1).

The numeric reference values live in the committed CSVs produced by
``scripts/extract_mid_cars_by_status.py`` from the local-only raw xlsx;
hard-coding percentages in Python is prohibited (see CLAUDE.md).
"""

from __future__ import annotations

import os
from typing import Iterable

import numpy as np
import pandas as pd

from braunschweig.data.mid.status_by_hhtype import (
    HHTYPE_CATEGORIES,
    HHTYPE_LABEL_TO_KEY,
    REGION_LABEL_TO_KEY_RAUMTYP,
    RS7_TO_RAUMTYP_KEY,
    STATUS_CATEGORIES,
    STATUS_LABEL_TO_KEY,
)

MID_SUBDIR = os.path.join("braunschweig", "mid")

# Canonical car-count categories. MiD reports 0 / 1 / 2 / 3 / 4+ ("Anzahl Autos
# im HH in Gruppen (0 bis 4+)"); we FOLD the "3 Autos" and "4 Autos und mehr"
# levels into a single "3" category ("3+") so the categories match the MiD H7
# Kreis control (mid2023_H7_cars_by_kreis.csv columns 0/1/2/3, where 3 == 3+).
# The fold is applied in the extract script (rows with num_cars in {3, 4} are
# summed into category 3 after converting the column % back to counts via the
# weighted base) -- see scripts/extract_mid_cars_by_status.py.
CAR_COUNT_CATEGORIES: tuple[int, ...] = (0, 1, 2, 3)

# MiD German "Anzahl Autos im HH" block-level label (lower-cased, ws-normalised,
# the part after the last '-') -> canonical (pre-fold) car count. "4 autos und
# mehr" maps to 4 and is folded into 3 downstream.
CAR_LEVEL_LABEL_TO_COUNT: dict[str, int] = {
    "kein auto": 0,
    "1 auto": 1,
    "2 autos": 2,
    "3 autos": 3,
    "4 autos und mehr": 4,
}

# Raumtyp region keys (re-used from status_by_hhtype so the RS7 -> raumtyp_key
# mapping is identical across the two MiD couplings).
__all__ = [
    "CAR_COUNT_CATEGORIES",
    "CAR_LEVEL_LABEL_TO_COUNT",
    "RS7_TO_RAUMTYP_KEY",
    "load_cars_by_status_hhtype",
    "load_cars_by_raumtyp",
    "cars_probabilities",
]


def _path(data_path: str, filename: str) -> str:
    return os.path.join(data_path, MID_SUBDIR, filename)


def load_cars_by_status_hhtype(data_path: str) -> pd.DataFrame:
    """Load the status x hhtype tidy table.

    Columns ``[num_cars, hhtype, status, share_pct, base_weighted]`` where
    ``share_pct = P(hhtype | num_cars, status)`` (column %, 0..100) and
    ``base_weighted`` is the weighted household base of the (num_cars, status)
    cell (repeated across hhtype rows). ``num_cars`` is folded to
    :data:`CAR_COUNT_CATEGORIES` (3 == 3+).
    """
    df = pd.read_csv(_path(data_path, "mid2023_cars_by_status_hhtype.csv"), comment="#")
    expected = {"num_cars", "hhtype", "status", "share_pct", "base_weighted"}
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"mid2023_cars_by_status_hhtype.csv: missing columns {sorted(missing)} "
            f"(have {sorted(df.columns)})"
        )
    bad_status = set(df["status"]) - set(STATUS_CATEGORIES)
    if bad_status:
        raise RuntimeError(
            f"mid2023_cars_by_status_hhtype.csv: unexpected status {sorted(bad_status)}"
        )
    bad_cars = set(df["num_cars"]) - set(CAR_COUNT_CATEGORIES)
    if bad_cars:
        raise RuntimeError(
            f"mid2023_cars_by_status_hhtype.csv: unexpected num_cars {sorted(bad_cars)}"
        )
    return df


def load_cars_by_raumtyp(data_path: str) -> pd.DataFrame:
    """Load the raumtyp tidy table.

    Columns ``[num_cars, region, base_weighted]`` -- the weighted household base
    per (num_cars, raumtyp) cell, used to derive the within-region tilt.
    ``num_cars`` is folded to :data:`CAR_COUNT_CATEGORIES`.
    """
    df = pd.read_csv(_path(data_path, "mid2023_cars_by_raumtyp.csv"), comment="#")
    expected = {"num_cars", "region", "base_weighted"}
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"mid2023_cars_by_raumtyp.csv: missing columns {sorted(missing)} "
            f"(have {sorted(df.columns)})"
        )
    bad_region = set(df["region"]) - set(REGION_LABEL_TO_KEY_RAUMTYP.values())
    if bad_region:
        raise RuntimeError(
            f"mid2023_cars_by_raumtyp.csv: unexpected region {sorted(bad_region)}"
        )
    bad_cars = set(df["num_cars"]) - set(CAR_COUNT_CATEGORIES)
    if bad_cars:
        raise RuntimeError(
            f"mid2023_cars_by_raumtyp.csv: unexpected num_cars {sorted(bad_cars)}"
        )
    return df


def _base_cars_given_hhtype_status(
    df_hhtype: pd.DataFrame,
    include_cars: Iterable[int] = CAR_COUNT_CATEGORIES,
) -> dict[tuple[str, str], np.ndarray]:
    """Bayes base ``{(hhtype, status): P(num_cars | hhtype, status)}``.

    For each status column: ``P(num_cars | status)`` = base_weighted normalised
    over num_cars; then per hhtype the Bayes product
    ``s(hhtype | num_cars, status) * P(num_cars | status)`` normalised over
    num_cars. Each returned vector is ordered like
    :data:`CAR_COUNT_CATEGORIES` and sums to 1.
    """
    cars = list(include_cars)
    out: dict[tuple[str, str], np.ndarray] = {}
    for status in df_hhtype["status"].unique():
        sub = df_hhtype[df_hhtype["status"] == status]
        # P(num_cars | status) from the per-(num_cars, status) weighted base. The
        # base is repeated across hhtype rows, so one value per num_cars.
        base = (
            sub.drop_duplicates("num_cars").set_index("num_cars")["base_weighted"]
            .reindex(cars).fillna(0.0)
        )
        base_sum = base.sum()
        p_cars = (base / base_sum) if base_sum > 0 else pd.Series(
            np.ones(len(cars)) / len(cars), index=cars
        )
        for hhtype in sub["hhtype"].unique():
            row = (
                sub[sub["hhtype"] == hhtype]
                .set_index("num_cars")["share_pct"]
                .reindex(cars).fillna(0.0)
            )
            joint = row.to_numpy(dtype=float) * p_cars.to_numpy(dtype=float)
            total = joint.sum()
            if total > 0:
                out[(hhtype, status)] = joint / total
            else:
                out[(hhtype, status)] = p_cars.to_numpy(dtype=float).copy()
    return out


def _raumtyp_cars_distribution(
    df_raumtyp: pd.DataFrame,
    include_cars: Iterable[int] = CAR_COUNT_CATEGORIES,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Return ``({region: P(num_cars | region)}, P(num_cars | national))``.

    Both come from the per-(num_cars, region) weighted base in the raumtyp table:
    the per-region pmf is the base normalised over num_cars within the region; the
    national pmf is the base summed over regions, normalised over num_cars.
    """
    cars = list(include_cars)
    by_region: dict[str, np.ndarray] = {}
    national = np.zeros(len(cars), dtype=float)
    for region in df_raumtyp["region"].unique():
        sub = df_raumtyp[df_raumtyp["region"] == region]
        vec = (
            sub.drop_duplicates("num_cars").set_index("num_cars")["base_weighted"]
            .reindex(cars).fillna(0.0).to_numpy(dtype=float)
        )
        national += vec
        total = vec.sum()
        by_region[region] = (vec / total) if total > 0 else (
            np.ones(len(cars)) / len(cars)
        )
    nat_total = national.sum()
    national = (national / nat_total) if nat_total > 0 else (
        np.ones(len(cars)) / len(cars)
    )
    return by_region, national


def cars_probabilities(
    df_hhtype: pd.DataFrame,
    df_raumtyp: pd.DataFrame,
    economic_status: str,
    hhtype: str,
    raumtyp_region: str | None,
    include_cars: Iterable[int] = CAR_COUNT_CATEGORIES,
) -> np.ndarray | None:
    """Return ``P(num_cars | hhtype, status, raumtyp)`` as a pmf, or ``None``.

    Combines the status x hhtype Bayes base with the raumtyp within-region tilt
    (see the module docstring). The returned vector is ordered like
    :data:`CAR_COUNT_CATEGORIES` and sums to 1.

    Returns ``None`` (caller logs a FALLBACK -- no silent fallback) when the
    ``(hhtype, status)`` cell is absent from the base table; the caller then
    falls back to the per-Kreis MiD H7 draw. With ``raumtyp_region is None`` or
    absent from the raumtyp table the base is returned untilted.
    """
    base_map = _base_cars_given_hhtype_status(df_hhtype, include_cars)
    base = base_map.get((hhtype, economic_status))
    if base is None:
        return None

    if raumtyp_region is None:
        return base.copy()

    by_region, national = _raumtyp_cars_distribution(df_raumtyp, include_cars)
    reg = by_region.get(raumtyp_region)
    if reg is None:
        return base.copy()

    with np.errstate(divide="ignore", invalid="ignore"):
        tilt = np.where(national > 1e-12, reg / national, 1.0)
    tilted = base * tilt
    total = tilted.sum()
    return (tilted / total) if total > 0 else base.copy()


def cars_probabilities_table(
    df_hhtype: pd.DataFrame,
    df_raumtyp: pd.DataFrame,
    include_cars: Iterable[int] = CAR_COUNT_CATEGORIES,
) -> tuple[
    dict[tuple[str, str], np.ndarray],
    dict[str, np.ndarray],
    np.ndarray,
]:
    """Pre-compute the Bayes base + raumtyp tilt inputs once (vectorisation hook).

    Returns ``(base_map, by_region, national)`` so the per-household assignment in
    the enrichment stage can look up ``base_map[(hhtype, status)]`` and apply
    ``by_region[raumtyp]/national`` without re-deriving the whole base for every
    distinct combination. Mirrors the ``prob_cache`` pattern used by
    ``_derive_economic_status_from_hhtype``.
    """
    base_map = _base_cars_given_hhtype_status(df_hhtype, include_cars)
    by_region, national = _raumtyp_cars_distribution(df_raumtyp, include_cars)
    return base_map, by_region, national
