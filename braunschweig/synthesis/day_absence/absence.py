"""General day absence: the seeded two-stage state draw (issue #370, ADR-0110).

Every person gets ``day_absence_state`` in {present, absent_household, absent_individual}:

1. HOUSEHOLD stage -- with probability ``p_all_absent[size_class]`` (SrV, by household size) the
   whole household is away (family vacation, joint travel): ``absent_household``.
2. INDIVIDUAL residual stage -- per age band the SrV person-level rate ``r(a)`` minus what stage 1
   already realised in that band, ``r_hh(a)``, gives ``p_individual(a) = (r(a) - r_hh(a)) /
   (1 - r_hh(a))`` (clipped at 0, an overshoot is WARNED); every still-present person is
   ``absent_individual`` with that probability. In expectation the per-band rates equal the SrV
   rates while the household clustering (56 % of absent persons in fully absent households) is
   reproduced by construction. Pure pandas/numpy; no file I/O apart from the reference loader.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_absence import (ABSENCE_BY_AGE_TABLE, ABSENCE_HOUSEHOLD_TABLE,
                                                  AGE_BAND_LABELS, ALL_BAND, HOUSEHOLD_SIZE_CLASS_TOP,
                                                  age_band, household_size_class)

logger = logging.getLogger(__name__)
_LOG_TAG = "[day absence]"

DAY_ABSENCE_SEED_OFFSET = 7351

STATE_PRESENT = "present"
STATE_ABSENT_HOUSEHOLD = "absent_household"
STATE_ABSENT_INDIVIDUAL = "absent_individual"
STATES = (STATE_PRESENT, STATE_ABSENT_HOUSEHOLD, STATE_ABSENT_INDIVIDUAL)
REASON_HOUSEHOLD = "household_draw"
REASON_INDIVIDUAL = "individual_draw"
REASON_PRESENT = "present"
REASON_DISABLED = "disabled"
ABSENCE_COLUMNS = ("person_id", "household_id", "day_absence_state", "age_band",
                   "household_size_class", "p_household", "p_individual", "reason")


def _check_reference_coverage(name: str, values: dict, expected_keys: set) -> None:
    """Raise unless ``values`` covers exactly ``expected_keys`` with every rate in [0, 1].

    Called from :meth:`AbsenceReference.__post_init__` so an incomplete or out-of-range reference
    fails at construction rather than surfacing later as a silently mapped NaN inside the
    probability draw (the "scalar-default-when-map-missing" pattern CLAUDE.md forbids).
    """
    missing = sorted(expected_keys - set(values), key=str)
    extra = sorted(set(values) - expected_keys, key=str)
    if missing or extra:
        raise ValueError(f"{_LOG_TAG} {name} must cover exactly {sorted(expected_keys, key=str)}; "
                         f"missing {missing}, unexpected {extra}")
    out_of_range = {k: v for k, v in values.items() if not (0.0 <= float(v) <= 1.0)}
    if out_of_range:
        raise ValueError(f"{_LOG_TAG} {name} values must be in [0, 1]; out of range: {out_of_range}")


def _map_size_class_probability(size_classes: pd.Series, table: dict) -> pd.Series:
    """Map ``household_size_class`` -> ``p_all_absent_by_size``; raise on an uncovered class.

    ``AbsenceReference`` is a frozen dataclass, but its dict fields stay mutable after
    construction, so this is a second, point-of-use guard against the same missing-key failure
    mode ``__post_init__`` already checks: a plain ``Series.map`` would turn a missing size class
    into a silent NaN (``NaN < probability`` is False, so the household would be "never absent"
    with no signal that anything went wrong); this raises instead.
    """
    mapped = size_classes.map(table).astype(float)
    if mapped.isna().any():
        missing = sorted(set(size_classes[mapped.isna()].unique().tolist()))
        raise ValueError(f"{_LOG_TAG} p_all_absent_by_size has no rate for household size "
                         f"class(es) {missing}; every class 1..{HOUSEHOLD_SIZE_CLASS_TOP} must be covered")
    return mapped


@dataclass(frozen=True)
class AbsenceReference:
    """Household-size and age-band absence rates behind :func:`draw_absence`.

    ``p_absent_by_band`` must cover exactly ``AGE_BAND_LABELS`` and ``p_all_absent_by_size``
    exactly the size classes ``1..HOUSEHOLD_SIZE_CLASS_TOP``, every rate in [0, 1]; checked eagerly
    in ``__post_init__`` (see :func:`_check_reference_coverage`).
    """
    p_absent_by_band: dict
    p_all_absent_by_size: dict

    def __post_init__(self) -> None:
        _check_reference_coverage("p_absent_by_band", self.p_absent_by_band, set(AGE_BAND_LABELS))
        _check_reference_coverage("p_all_absent_by_size", self.p_all_absent_by_size,
                                  set(range(1, HOUSEHOLD_SIZE_CLASS_TOP + 1)))


def load_absence_reference(srv_dir: str) -> AbsenceReference:
    """Read the two committed tables BY COLUMN NAME; raise on a missing band / size class."""
    by_age = pd.read_csv(os.path.join(srv_dir, ABSENCE_BY_AGE_TABLE), comment="#")
    by_size = pd.read_csv(os.path.join(srv_dir, ABSENCE_HOUSEHOLD_TABLE), comment="#")
    bands = by_age[by_age["band"] != ALL_BAND].set_index("band")["p_absent"]
    missing = sorted(set(AGE_BAND_LABELS) - set(bands.index))
    if missing or bands.isna().any():
        raise ValueError(f"{_LOG_TAG} {ABSENCE_BY_AGE_TABLE} lacks a rate for band(s) "
                         f"{missing or bands[bands.isna()].index.tolist()}")
    sizes = by_size.set_index("size_class")["p_all_absent"]
    expected = list(range(1, HOUSEHOLD_SIZE_CLASS_TOP + 1))
    if sorted(sizes.index.tolist()) != expected or sizes.isna().any():
        raise ValueError(f"{_LOG_TAG} {ABSENCE_HOUSEHOLD_TABLE} must carry a rate for size classes {expected}")
    return AbsenceReference(p_absent_by_band={b: float(bands[b]) for b in AGE_BAND_LABELS},
                            p_all_absent_by_size={int(k): float(v) for k, v in sizes.items()})


def absent_person_ids(absence: pd.DataFrame) -> set:
    return set(absence.loc[absence["day_absence_state"] != STATE_PRESENT, "person_id"])


def draw_absence(persons: pd.DataFrame, reference: AbsenceReference, rng: np.random.RandomState, *,
                 household_stage: bool = True) -> tuple[pd.DataFrame, dict]:
    """Two-stage seeded draw over ``persons`` (``person_id``, ``household_id``, ``age``).

    Row order of ``persons`` does not matter (sorted internally); the draw sequence is
    households (sorted by household_id) then persons (sorted by household_id, person_id).
    Raises ``ValueError`` on a missing age (a person that cannot be banded cannot be drawn).
    """
    missing = [c for c in ("person_id", "household_id", "age") if c not in persons.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} persons frame is missing {missing}")
    p = persons[["person_id", "household_id", "age"]].sort_values(["household_id", "person_id"]).reset_index(drop=True)
    if p["person_id"].duplicated().any():
        raise ValueError(f"{_LOG_TAG} duplicated person_id in the persons frame")
    band = age_band(p["age"])
    n_missing_age = int(band.isna().sum())
    if n_missing_age:
        raise ValueError(f"{_LOG_TAG} {n_missing_age} persons have no age and cannot be assigned an "
                         "absence band; the enriched persons frame must carry a numeric age for everyone")
    p["age_band"] = band.values
    sizes = p.groupby("household_id")["person_id"].transform("size")
    p["household_size_class"] = household_size_class(sizes.values)

    households = p.drop_duplicates("household_id")[["household_id", "household_size_class"]].reset_index(drop=True)
    p_hh = (_map_size_class_probability(households["household_size_class"], reference.p_all_absent_by_size)
            if household_stage else pd.Series(0.0, index=households.index))
    u_hh = rng.random_sample(len(households))
    hh_absent = pd.Series(u_hh < p_hh.to_numpy(), index=households["household_id"].values)
    p["p_household"] = (_map_size_class_probability(p["household_size_class"], reference.p_all_absent_by_size)
                        if household_stage else 0.0)
    is_hh_absent = p["household_id"].map(hh_absent).astype(bool).to_numpy()

    residual_by_band, by_band, n_overshoot = {}, {}, 0
    for label in AGE_BAND_LABELS:
        in_band = (p["age_band"] == label).to_numpy()
        n_band = int(in_band.sum())
        target = reference.p_absent_by_band[label]
        realised_hh = float(is_hh_absent[in_band].mean()) if n_band else 0.0
        # An overshoot is realised_hh > target, checked directly rather than only via a negative
        # residual: at realised_hh == 1.0 (a band fully absorbed by the household stage) the
        # residual formula below is bypassed by the division-by-zero guard, so a residual-only
        # check would silently miss that case and never emit the mandatory WARNING.
        overshoot = realised_hh > target
        residual = (target - realised_hh) / (1.0 - realised_hh) if realised_hh < 1.0 else 0.0
        if overshoot:
            n_overshoot += 1
            logger.warning("%s band %s: household stage realised %.2f%% > reference %.2f%% (overshoot); "
                           "residual set to 0", _LOG_TAG, label, 100 * realised_hh, 100 * target)
        residual = float(min(max(residual, 0.0), 1.0))
        residual_by_band[label] = residual
        by_band[label] = {"n": n_band, "reference_rate": target, "realised_household_rate": realised_hh,
                          "residual_p": residual}
    p["p_individual"] = p["age_band"].map(residual_by_band).astype(float)
    u_ind = rng.random_sample(len(p))
    is_ind_absent = (~is_hh_absent) & (u_ind < p["p_individual"].to_numpy())

    state = np.full(len(p), STATE_PRESENT, dtype=object)
    reason = np.full(len(p), REASON_PRESENT, dtype=object)
    state[is_hh_absent] = STATE_ABSENT_HOUSEHOLD
    reason[is_hh_absent] = REASON_HOUSEHOLD
    state[is_ind_absent] = STATE_ABSENT_INDIVIDUAL
    reason[is_ind_absent] = REASON_INDIVIDUAL
    p["day_absence_state"] = state
    p["reason"] = reason
    out = p[list(ABSENCE_COLUMNS)]

    absent = out["day_absence_state"] != STATE_PRESENT
    for label in AGE_BAND_LABELS:
        in_band = out["age_band"] == label
        by_band[label]["realised_rate"] = float(absent[in_band].mean()) if in_band.any() else float("nan")
    n_abs = int(absent.sum())
    # Vectorised over households (a per-household Python-level lambda does not scale to a full
    # population): a household is fully absent iff every member's `absent` flag is True.
    hh_all_absent = absent.groupby(out["household_id"]).transform("all")
    share_clustered = float((absent & hh_all_absent).sum() / n_abs) if n_abs else float("nan")
    diagnostics = {"n_persons": int(len(out)), "n_households": int(len(households)),
                   "n_absent_household": int(is_hh_absent.sum()), "n_absent_individual": int(is_ind_absent.sum()),
                   "n_absent_total": n_abs, "share_absent_total": float(n_abs / max(len(out), 1)),
                   "share_absent_in_fully_absent_households": share_clustered,
                   "n_bands_overshoot": n_overshoot, "household_stage": bool(household_stage), "by_band": by_band}
    logger.info("%s %d/%d persons absent (%.2f%%): %d by the household stage, %d by the individual stage; "
                "%.1f%% of absent persons live in a fully absent household; per band %s", _LOG_TAG, n_abs,
                len(out), 100.0 * diagnostics["share_absent_total"], diagnostics["n_absent_household"],
                diagnostics["n_absent_individual"], 100.0 * share_clustered if n_abs else float("nan"),
                {b: f"{100 * v['realised_rate']:.2f}% vs {100 * v['reference_rate']:.2f}%" for b, v in by_band.items()})
    return out, diagnostics
