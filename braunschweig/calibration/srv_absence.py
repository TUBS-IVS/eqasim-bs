"""SrV 2023 full-day absence aggregates (issue #370, sub-project B).

Two committed tables for the general-day-absence state draw
(:mod:`braunschweig.synthesis.day_absence.absence`):

* ``srv2023_absence_by_age_band.csv`` -- share of persons away from home over the WHOLE reporting
  day (``E_ANZ_WEGE == -7``, :data:`srv_plan_structure.AWAY_FROM_HOME_CODE`) per age band, plus an
  ``all`` row. Weight ``GEWICHT_P_ZENSUS`` (ADR-0055).
* ``srv2023_absence_household_by_size.csv`` -- share of households in which EVERY member is away,
  per household size class (1..5, 5 = five or more), weight ``GEWICHT_HH_ZENSUS``.

Universe: every delivered person with a valid positive person weight (the SrV ``at_home_zero``
universe of :mod:`braunschweig.calibration.srv_plan_structure` -- absence IS the state being
measured, so away persons are kept). Guards raise rather than adapt: ``MITTL_WERKTAG`` must be 1
for every person (pure Tuesday-Thursday delivery) and the only negative ``E_ANZ_WEGE`` code may
be -7. Pure module: no file I/O; ``scripts/extract_srv_absence.py`` owns the CLI and the header.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_plan_structure import AWAY_FROM_HOME_CODE

logger = logging.getLogger(__name__)
_LOG_TAG = "[srv absence]"

ABSENCE_BY_AGE_TABLE = "srv2023_absence_by_age_band.csv"
ABSENCE_HOUSEHOLD_TABLE = "srv2023_absence_household_by_size.csv"

#: Seven bands, every one with >= 1,081 persons and >= 31 absent persons in the 2026-09-09
#: extraction (spec 2026-09-09-general-day-absence-design.md, table 1.1).
AGE_BAND_EDGES = (-1, 5, 17, 29, 44, 64, 74, 200)
AGE_BAND_LABELS = ("0-5", "6-17", "18-29", "30-44", "45-64", "65-74", "75+")
AGE_BAND_BOUNDS = {"0-5": (0, 5), "6-17": (6, 17), "18-29": (18, 29), "30-44": (30, 44),
                   "45-64": (45, 64), "65-74": (65, 74), "75+": (75, 200)}
ALL_BAND = "all"
HOUSEHOLD_SIZE_CLASS_TOP = 5
AVERAGE_WEEKDAY = 1  # MITTL_WERKTAG

PERSON_COLUMNS = ["HHNR", "PNR", "V_ALTER", "E_ANZ_WEGE", "GEWICHT_P_ZENSUS", "MITTL_WERKTAG"]
HOUSEHOLD_COLUMNS = ["HHNR", "GEWICHT_HH_ZENSUS"]
BY_AGE_COLUMNS = ["band", "age_min", "age_max", "n_unweighted", "n_absent_unweighted", "p_absent"]
BY_SIZE_COLUMNS = ["size_class", "n_households_unweighted", "n_all_absent_unweighted", "p_all_absent"]


def _require_columns(frame, required, name):
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} {name}: missing required column(s) {missing}")


def age_band(ages) -> pd.Series:
    """Age (years) -> band label; NaN for a missing age (the caller decides how to treat it)."""
    return pd.cut(pd.to_numeric(pd.Series(ages), errors="coerce"), list(AGE_BAND_EDGES),
                  labels=list(AGE_BAND_LABELS)).astype(object)


def household_size_class(sizes) -> np.ndarray:
    """Household size -> class 1..HOUSEHOLD_SIZE_CLASS_TOP (top class = 'or more')."""
    return np.minimum(np.asarray(sizes, dtype=int), HOUSEHOLD_SIZE_CLASS_TOP)


def prepare_absence_persons(persons: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Harmonise the SrV person file for the absence tables; raises on universe drift."""
    _require_columns(persons, PERSON_COLUMNS, "persons")
    p = persons[PERSON_COLUMNS].copy()
    werktag = pd.to_numeric(p["MITTL_WERKTAG"], errors="coerce")
    if not (werktag == AVERAGE_WEEKDAY).all():
        raise ValueError(f"{_LOG_TAG} MITTL_WERKTAG is not {AVERAGE_WEEKDAY} for every person; "
                         "the delivery is not the pure average-weekday universe this table assumes")
    trips = pd.to_numeric(p["E_ANZ_WEGE"], errors="coerce")
    if trips.isna().any():
        raise ValueError(f"{_LOG_TAG} {int(trips.isna().sum())} persons have a non-numeric E_ANZ_WEGE")
    other_negative = sorted(trips[(trips < 0) & (trips != AWAY_FROM_HOME_CODE)].unique().tolist())
    if other_negative:
        raise ValueError(f"{_LOG_TAG} unexpected negative E_ANZ_WEGE code(s) {other_negative}; only "
                         f"{AWAY_FROM_HOME_CODE} (away from home) is a known state")
    weight = pd.to_numeric(p["GEWICHT_P_ZENSUS"], errors="coerce")
    valid = weight > 0
    n_dropped = int((~valid).sum())
    if n_dropped:
        logger.warning("%s %d/%d persons dropped for a missing/non-positive GEWICHT_P_ZENSUS",
                       _LOG_TAG, n_dropped, len(p))
    p = p[valid].copy()
    out = pd.DataFrame({
        "hhnr": p["HHNR"].values, "pnr": p["PNR"].values,
        "age": pd.to_numeric(p["V_ALTER"], errors="coerce").values,
        "weight": weight[valid].astype(float).values,
        "absent": (trips[valid] == AWAY_FROM_HOME_CODE).values,
    })
    out["band"] = age_band(out["age"]).values
    n_missing_age = int(out["band"].isna().sum())
    if n_missing_age:
        logger.warning("%s %d persons have no age and fall into no band (kept in the 'all' row only)",
                       _LOG_TAG, n_missing_age)
    diagnostics = {"n_persons_raw": int(len(persons)), "n_persons_dropped_weight": n_dropped,
                   "n_persons_universe": int(len(out)), "n_absent": int(out["absent"].sum()),
                   "n_persons_missing_age": n_missing_age}
    logger.info("%s universe %d persons, %d absent (%.2f%% weighted)", _LOG_TAG, len(out),
                diagnostics["n_absent"], 100.0 * out.loc[out["absent"], "weight"].sum() / out["weight"].sum())
    return out, diagnostics


def _weighted_share(weight, mask) -> float:
    total = float(weight.sum())
    return float(weight[mask].sum() / total) if total > 0 else float("nan")


def build_absence_by_age_band(prepared: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for band in AGE_BAND_LABELS:
        g = prepared[prepared["band"] == band]
        lo, hi = AGE_BAND_BOUNDS[band]
        rows.append({"band": band, "age_min": lo, "age_max": hi, "n_unweighted": int(len(g)),
                     "n_absent_unweighted": int(g["absent"].sum()),
                     "p_absent": _weighted_share(g["weight"], g["absent"])})
    rows.append({"band": ALL_BAND, "age_min": 0, "age_max": 200, "n_unweighted": int(len(prepared)),
                 "n_absent_unweighted": int(prepared["absent"].sum()),
                 "p_absent": _weighted_share(prepared["weight"], prepared["absent"])})
    return pd.DataFrame(rows, columns=BY_AGE_COLUMNS)


def build_absence_household_by_size(prepared: pd.DataFrame, households: pd.DataFrame) -> pd.DataFrame:
    _require_columns(households, HOUSEHOLD_COLUMNS, "households")
    per_hh = prepared.groupby("hhnr").agg(n=("pnr", "size"), n_absent=("absent", "sum")).reset_index()
    per_hh["all_absent"] = per_hh["n_absent"] == per_hh["n"]
    per_hh["size_class"] = household_size_class(per_hh["n"])
    hh = households[["HHNR", "GEWICHT_HH_ZENSUS"]].rename(columns={"HHNR": "hhnr"})
    per_hh = per_hh.merge(hh, on="hhnr", how="left")
    n_unweighted_hh = int(per_hh["GEWICHT_HH_ZENSUS"].isna().sum())
    if n_unweighted_hh:
        raise ValueError(f"{_LOG_TAG} {n_unweighted_hh} households of the person file have no row / weight "
                         "in the household file")
    rows = []
    for size_class in range(1, HOUSEHOLD_SIZE_CLASS_TOP + 1):
        g = per_hh[per_hh["size_class"] == size_class]
        rows.append({"size_class": size_class, "n_households_unweighted": int(len(g)),
                     "n_all_absent_unweighted": int(g["all_absent"].sum()),
                     "p_all_absent": _weighted_share(g["GEWICHT_HH_ZENSUS"].astype(float), g["all_absent"])})
    return pd.DataFrame(rows, columns=BY_SIZE_COLUMNS)


def check_invariants(by_age: pd.DataFrame, by_size: pd.DataFrame) -> None:
    if list(by_age["band"]) != list(AGE_BAND_LABELS) + [ALL_BAND]:
        raise ValueError(f"{_LOG_TAG} by-age table must carry exactly the bands {AGE_BAND_LABELS} + 'all'")
    bands = by_age[by_age["band"] != ALL_BAND]
    if int(bands["n_unweighted"].sum()) > int(by_age.loc[by_age["band"] == ALL_BAND, "n_unweighted"].iloc[0]):
        raise ValueError(f"{_LOG_TAG} band person counts exceed the 'all' row")
    for name, table, col in (("by_age", by_age, "p_absent"), ("by_size", by_size, "p_all_absent")):
        shares = table[col].dropna()
        if ((shares < 0) | (shares > 1)).any():
            raise ValueError(f"{_LOG_TAG} {name}: {col} outside [0, 1]")
    if list(by_size["size_class"]) != list(range(1, HOUSEHOLD_SIZE_CLASS_TOP + 1)):
        raise ValueError(f"{_LOG_TAG} by-size table must carry size classes 1..{HOUSEHOLD_SIZE_CLASS_TOP}")
