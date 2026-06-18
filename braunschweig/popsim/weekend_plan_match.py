"""Give weekend-surveyed donor households weekday plans by remapping their
``source_H_ID``/``source_P_ID`` to a matched weekday household (HH-first, then
person-level fallback). Sibling of ``member_completion``; runs after it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PT_SUBSCRIPTION_CODES = frozenset({3, 4, 5, 6})  # see attributes.map_has_pt_subscription

# Soft keys ordered HIGH→LOW priority (dropped from the END first); ``size`` is a
# separate HARD key (equal size always required so role alignment is feasible).
SOFT_KEYS_BY_PRIORITY = ("car_class", "any_license", "any_pt", "hh_type5", "oek_status", "regiostar7")


def _car_class(n_cars: pd.Series) -> pd.Series:
    n = pd.to_numeric(n_cars, errors="raise").astype(int)
    return np.where(n <= 0, "0", np.where(n == 1, "1", "2plus"))


def build_hh_features(households: pd.DataFrame, persons: pd.DataFrame) -> pd.DataFrame:
    has_lic = persons.assign(_lic=persons["P_FSCHEIN"].eq(1)).groupby("H_ID")["_lic"].any()
    has_pt = persons.assign(
        _pt=persons["P_FKARTE"].isin(PT_SUBSCRIPTION_CODES)
    ).groupby("H_ID")["_pt"].any()
    feats = pd.DataFrame({
        "size": households["H_GR"].astype(int).to_numpy(),
        "hh_type5": households["hh_type5"].to_numpy(),
        "oek_status": households["oek_status"].to_numpy(),
        "regiostar7": households["RegioStaR7"].to_numpy(),
        "car_class": _car_class(households["H_ANZAUTO"]),
    }, index=households["H_ID"].to_numpy())
    feats.index.name = "H_ID"
    feats["any_license"] = has_lic.reindex(feats.index).fillna(False).to_numpy()
    feats["any_pt"] = has_pt.reindex(feats.index).fillna(False).to_numpy()
    return feats


def match_household(target_id, target_feats, weekday_feats, *, rng):
    """Find a weekday household of EQUAL size, matching as many soft keys as
    possible (drop the lowest-priority soft key first). Returns
    ``(matched_H_ID, relaxation_level)`` or ``(None, None)`` if no equal-size
    weekday household exists (caller then uses the person-level fallback).
    """
    pool = weekday_feats[weekday_feats["size"] == target_feats["size"]]
    if len(pool) == 0:
        return None, None
    active = list(SOFT_KEYS_BY_PRIORITY)
    while True:
        narrowed = pool
        for key in active:
            narrowed = narrowed[narrowed[key] == target_feats[key]]
        if len(narrowed) > 0:
            ids = sorted(narrowed.index.tolist())
            level = len(SOFT_KEYS_BY_PRIORITY) - len(active)
            return ids[int(rng.randint(len(ids)))], level
        if not active:
            ids = sorted(pool.index.tolist())  # size-only fallback
            return ids[int(rng.randint(len(ids)))], len(SOFT_KEYS_BY_PRIORITY)
        active.pop()  # drop the lowest-priority remaining soft key
