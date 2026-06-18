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


AGE_BAND_EDGES = (-1, 5, 13, 17, 200)


def _age_band(ages: pd.Series) -> np.ndarray:
    return pd.cut(ages, bins=list(AGE_BAND_EDGES), labels=False).to_numpy()


def align_members(target_members: pd.DataFrame, donor_members: pd.DataFrame):
    """Greedily pair each target member to a distinct donor member by
    (coarse age band, sex), falling back to age band only, then any free donor.
    """
    d_band = _age_band(donor_members["HP_ALTER"])
    d_sex = donor_members["HP_SEX"].to_numpy()
    used = np.zeros(len(donor_members), dtype=bool)
    t_band = _age_band(target_members["HP_ALTER"])
    t_sex = target_members["HP_SEX"].to_numpy()
    pairs = []
    for tpos in range(len(target_members)):
        exact = np.flatnonzero(~used & (d_band == t_band[tpos]) & (d_sex == t_sex[tpos]))
        band_only = np.flatnonzero(~used & (d_band == t_band[tpos]))
        any_free = np.flatnonzero(~used)
        for cand in (exact, band_only, any_free):
            if len(cand) > 0:
                pairs.append((tpos, int(cand[0])))
                used[cand[0]] = True
                break
    return pairs


PERSON_KEYS_BY_PRIORITY = ("has_license", "sex", "age_band", "employed", "has_pt")


def _person_keys(persons: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "age_band": _age_band(persons["HP_ALTER"]),
        "sex": persons["HP_SEX"].to_numpy(),
        "has_license": persons["P_FSCHEIN"].eq(1).to_numpy(),
        "employed": persons["P_TAET"].between(1, 7).to_numpy(),
        "has_pt": persons["P_FKARTE"].isin(PT_SUBSCRIPTION_CODES).to_numpy(),
    }, index=persons.index)


def match_person(target_row, weekday_persons, *, rng):
    if len(weekday_persons) == 0:
        raise ValueError("empty weekday person pool; cannot match weekend person")
    keys = _person_keys(weekday_persons)
    tkeys = _person_keys(pd.DataFrame([target_row])).iloc[0]
    active = list(PERSON_KEYS_BY_PRIORITY)
    while True:
        mask = pd.Series(True, index=weekday_persons.index)
        for key in active:
            mask &= keys[key] == tkeys[key]
        pool = weekday_persons[mask]
        if len(pool) > 0:
            level = len(PERSON_KEYS_BY_PRIORITY) - len(active)
            chosen = pool.sort_values(["H_ID", "P_ID"]).iloc[int(rng.randint(len(pool)))]
            return chosen["H_ID"], chosen["P_ID"], level
        if not active:
            chosen = weekday_persons.sort_values(["H_ID", "P_ID"]).iloc[
                int(rng.randint(len(weekday_persons)))]
            return chosen["H_ID"], chosen["P_ID"], len(PERSON_KEYS_BY_PRIORITY)
        active.pop()


@dataclass(frozen=True)
class WeekendMatchReport:
    n_weekend_households: int
    n_hh_matched: int
    n_person_fallback_households: int
    n_persons_remapped: int
    hh_match_level_counts: dict


def reassign_weekend_plan_sources(households, persons, *, rng, household_id="H_ID"):
    from braunschweig.popsim import day_type as _dt

    persons = persons.copy()
    hh_dt = _dt.household_day_type(persons, household_id=household_id)
    feats = build_hh_features(households, persons)

    weekday_ids = hh_dt.index[hh_dt == "weekday"]
    weekend_ids = hh_dt.index[hh_dt == "weekend"]
    weekday_feats = feats.loc[feats.index.isin(weekday_ids)]
    weekday_persons = persons[persons[household_id].isin(weekday_ids)].copy()

    persons_by_hh = dict(tuple(persons.groupby(household_id, sort=False)))
    # default trace row = own plan (correct for weekday + filler bookkeeping below)
    resolution = pd.Series("own_plan", index=persons.index)
    match_level = pd.Series(np.nan, index=persons.index)
    resolution[persons["member_imputed"].to_numpy()] = "member_completion_filler"

    n_hh_matched = 0
    n_person_fallback = 0
    n_remapped = 0
    level_counts: dict = {}

    for hid in sorted(weekend_ids):
        target_members = persons_by_hh[hid].reset_index()  # keeps original index in 'index'
        matched_id, level = match_household(
            hid, feats.loc[hid], weekday_feats, rng=rng)
        if matched_id is not None:
            donor_members = persons_by_hh[matched_id].reset_index(drop=True)
            for tpos, dpos in align_members(target_members, donor_members):
                ridx = target_members.loc[tpos, "index"]
                persons.loc[ridx, "source_H_ID"] = donor_members.loc[dpos, "source_H_ID"]
                persons.loc[ridx, "source_P_ID"] = donor_members.loc[dpos, "source_P_ID"]
                resolution[ridx] = "hh_match"
                match_level[ridx] = level
                n_remapped += 1
            n_hh_matched += 1
            level_counts[level] = level_counts.get(level, 0) + 1
        else:
            for tpos in range(len(target_members)):
                ridx = target_members.loc[tpos, "index"]
                trow = target_members.loc[tpos]
                sh, sp, plevel = match_person(trow, weekday_persons, rng=rng)
                persons.loc[ridx, "source_H_ID"] = sh
                persons.loc[ridx, "source_P_ID"] = sp
                resolution[ridx] = "person_fallback"
                match_level[ridx] = plevel
                n_remapped += 1
            n_person_fallback += 1

    donor_dt = persons[household_id].map(hh_dt)
    trace = pd.DataFrame({
        "H_ID": persons[household_id].to_numpy(),
        "P_ID": persons["P_ID"].to_numpy(),
        "donor_day_type": donor_dt.to_numpy(),
        "resolution": resolution.to_numpy(),
        "match_level": match_level.to_numpy(),
        "plan_source_H_ID": persons["source_H_ID"].to_numpy(),
        "plan_source_P_ID": persons["source_P_ID"].to_numpy(),
    })
    report = WeekendMatchReport(
        n_weekend_households=len(weekend_ids),
        n_hh_matched=n_hh_matched,
        n_person_fallback_households=n_person_fallback,
        n_persons_remapped=n_remapped,
        hh_match_level_counts=level_counts,
    )
    logger.info(
        "[weekend_plan_match] %d weekend households: %d HH-matched, %d via "
        "person-fallback; %d persons remapped. HH match-level counts: %s",
        report.n_weekend_households, report.n_hh_matched,
        report.n_person_fallback_households, report.n_persons_remapped,
        report.hh_match_level_counts,
    )
    if len(weekend_ids) and report.n_hh_matched == 0:
        logger.warning(
            "[weekend_plan_match] no weekend household matched at HH level; all "
            "%d fell back to person-level matching.", len(weekend_ids))
    return persons, trace, report
