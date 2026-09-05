"""Give persons whose plan source has no realisable MiD diary a matched weekday diary.

Sibling of ``weekend_plan_match`` (spec 2026-09-05-plan-structure-fix-design.md, section 2.1,
issue #365). A plan source is NOT realisable when its diary was not collected (``anzwege1`` 803
"Person ohne Wegeerfassung" with ``mobil == 1``, or 804 "Mobilitaet unbekannt"), when it consists
only of rbW legs that package 2 removes, when it becomes empty after the leading arrive-home leg
is dropped, or when it was recorded on a public holiday (``feiertag == 1``; SrV reference days
exclude public holidays). 803 persons with ``mobil == 0`` genuinely stayed home and keep their
(empty) day. The remap reuses ``weekend_plan_match.match_person`` (has_license, sex, age band,
employed, has_pt; hierarchical relaxation; P_GEW-weighted draw) and continues the caller's rng.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from braunschweig.popsim.seed import WEEKDAY_KERNWO
from braunschweig.popsim.weekend_plan_match import match_person

logger = logging.getLogger(__name__)

NO_DIARY_CODES = (803, 804)
REASON_KEEP = "realisable"
REASON_KEEP_IMMOBILE = "nodiary_immobile_keep"
REASONS_REMAP = ("nodiary_mobile", "nodiary_unknown", "only_rbw", "holiday", "emptied_by_arrive_home_drop")
#: Ruling R5 (2026-09-05, plan-structure-fix review): only-rbW sources are detected from the
#: diary facts (n_direct_legs == 0 & n_rbw_legs > 0), which carries the same information as MiD
#: mobil_diff == 2 but derived from the Wege the pipeline actually uses. mobil_diff is therefore
#: NOT required or read here; it stays an optional MiD column loaded for diagnostics elsewhere.
REQUIRED_DONOR_COLUMNS = ("H_ID", "P_ID", "anzwege1", "mobil", "kernwo", "P_GEW",
                          "HP_ALTER", "HP_SEX", "P_FSCHEIN", "P_TAET", "P_FKARTE")
#: Columns match_person() reads from its target row (persons frame), validated up front so a
#: missing column fails here with a named message instead of a raw KeyError inside
#: weekend_plan_match._person_keys.
PERSON_MATCH_COLUMNS = ("H_ID", "P_ID", "HP_ALTER", "HP_SEX", "P_FSCHEIN", "P_TAET", "P_FKARTE", "P_GEW")
#: Expected band of the remapped share on the real MiD (2026-09-05 measurement: 14.1 % no-diary
#: + 5.2 % holiday + 1.6 % only-rbW, overlapping); outside it the build WARNS.
EXPECTED_SHARE_REMAPPED = (0.05, 0.30)


@dataclass(frozen=True)
class DiaryMatchReport:
    n_persons: int
    n_remapped: int
    counts_by_reason: dict
    match_level_counts: dict
    share_remapped: float


def _require(frame, columns, what):
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise KeyError(f"diary_plan_match: {what} lacks required column(s) {missing}")


def _own_diary_reason(donors, facts, *, exclude_rbw_legs, exclude_holidays, drop_leading_arrive_home_leg):
    """Reason for each DONOR person's OWN diary (index = donors.index)."""
    idx = pd.MultiIndex.from_arrays([donors["H_ID"], donors["P_ID"]])
    f = facts.reindex(idx)
    n_direct = f["n_direct_legs"].fillna(0).astype(int).to_numpy()
    n_rbw = f["n_rbw_legs"].fillna(0).astype(int).to_numpy()
    arriving = f["starts_arriving_home"].fillna(False).astype(bool).to_numpy()
    anz = donors["anzwege1"].to_numpy()
    mobil = donors["mobil"].to_numpy()
    holiday = donors["feiertag"].to_numpy() == 1 if "feiertag" in donors.columns else np.zeros(len(donors), bool)
    reason = np.full(len(donors), REASON_KEEP, dtype=object)
    remaining_direct = n_direct - (arriving.astype(int) if drop_leading_arrive_home_leg else 0)
    only_rbw = (n_direct == 0) & (n_rbw > 0)
    emptied = (n_direct > 0) & (remaining_direct <= 0)
    reason[emptied] = "emptied_by_arrive_home_drop"
    if exclude_rbw_legs:
        reason[only_rbw] = "only_rbw"
    anzwege1_not_collected, anzwege1_unknown = NO_DIARY_CODES  # 803, 804
    reason[anz == anzwege1_unknown] = "nodiary_unknown"
    reason[(anz == anzwege1_not_collected) & (mobil == 1)] = "nodiary_mobile"
    reason[(anz == anzwege1_not_collected) & (mobil != 1)] = REASON_KEEP_IMMOBILE
    if exclude_holidays:
        reason[holiday] = "holiday"
    return pd.Series(reason, index=donors.index)


def classify_plan_sources(persons, donor_persons, facts, *, exclude_rbw_legs, exclude_holidays,
                          drop_leading_arrive_home_leg):
    _require(persons, ("source_H_ID", "source_P_ID"), "persons frame")
    _require(donor_persons, REQUIRED_DONOR_COLUMNS, "donor persons frame")
    if exclude_holidays and "feiertag" not in donor_persons.columns:
        raise KeyError("diary_plan_match: exclude_holidays=True requires the MiD column 'feiertag'")
    real = donor_persons[~donor_persons["member_imputed"].astype(bool)] if "member_imputed" in donor_persons.columns else donor_persons
    own = _own_diary_reason(real, facts, exclude_rbw_legs=exclude_rbw_legs, exclude_holidays=exclude_holidays,
                            drop_leading_arrive_home_leg=drop_leading_arrive_home_leg)
    own.index = pd.MultiIndex.from_arrays([real["H_ID"], real["P_ID"]])
    src = pd.MultiIndex.from_arrays([persons["source_H_ID"], persons["source_P_ID"]])
    mapped = own.reindex(src)
    n_unresolved = int(mapped.isna().sum())
    if n_unresolved:
        raise ValueError(f"diary_plan_match: {n_unresolved} plan source(s) do not resolve to a real donor "
                         "person; upstream donor/source corruption")
    return pd.Series(mapped.to_numpy(), index=persons.index)


def build_realisable_pool(donor_persons, facts, *, exclude_rbw_legs, exclude_holidays,
                          drop_leading_arrive_home_leg, mobility):
    if mobility not in ("mobile", "any"):
        raise ValueError(f"mobility must be 'mobile' or 'any', got {mobility!r}")
    real = donor_persons[~donor_persons["member_imputed"].astype(bool)] if "member_imputed" in donor_persons.columns else donor_persons
    real = real[real["kernwo"].isin(WEEKDAY_KERNWO)]
    own = _own_diary_reason(real, facts, exclude_rbw_legs=exclude_rbw_legs, exclude_holidays=exclude_holidays,
                            drop_leading_arrive_home_leg=drop_leading_arrive_home_leg)
    pool = real[(own == REASON_KEEP).to_numpy()]
    if mobility == "mobile":
        idx = pd.MultiIndex.from_arrays([pool["H_ID"], pool["P_ID"]])
        f = facts.reindex(idx)
        legs = f["n_direct_legs"].fillna(0).astype(int).to_numpy()
        if drop_leading_arrive_home_leg:
            legs = legs - f["starts_arriving_home"].fillna(False).astype(bool).to_numpy().astype(int)
        if not exclude_rbw_legs:
            legs = legs + f["n_rbw_legs"].fillna(0).astype(int).to_numpy()
        pool = pool[legs > 0]
    return pool


def reassign_diaryless_plan_sources(persons, donor_persons, facts, *, rng, exclude_rbw_legs,
                                    exclude_holidays, drop_leading_arrive_home_leg):
    flags = dict(exclude_rbw_legs=exclude_rbw_legs, exclude_holidays=exclude_holidays,
                 drop_leading_arrive_home_leg=drop_leading_arrive_home_leg)
    # match_person() is called below on rows of `persons` (not `donor_persons`); validate its
    # required columns on `persons` up front so a missing one fails fast with a named message
    # here instead of a raw KeyError surfacing from inside weekend_plan_match._person_keys.
    _require(persons, PERSON_MATCH_COLUMNS, "persons frame")
    reasons = classify_plan_sources(persons, donor_persons, facts, **flags)
    pools = {m: build_realisable_pool(donor_persons, facts, mobility=m, **flags) for m in ("mobile", "any")}
    for name, pool in pools.items():
        if len(pool) == 0:
            raise ValueError(f"diary_plan_match: no realisable weekday diary in the '{name}' pool; cannot remap")
    persons = persons.copy()
    match_level = pd.Series(np.nan, index=persons.index)
    for_mobile = {"nodiary_mobile", "only_rbw", "emptied_by_arrive_home_drop"}
    to_remap = persons.index[reasons.isin(REASONS_REMAP)]
    # Build the source-anzwege1 lookup ONCE before the loop (not per row) -- same
    # behaviour as a per-row set_index/get, but O(n_donors) instead of O(n_remap x n_donors).
    donor_anzwege1 = donor_persons.set_index(["H_ID", "P_ID"])["anzwege1"]
    # Iterate in SORTED index order, not the frame's row order: this fixes the RNG draw
    # sequence independent of row order on its own, which matters here because the
    # completed-donor frame this consumes is built in a fixed order upstream
    # (member_completion / seed) that this function must not rely on for reproducibility.
    for ridx in sorted(to_remap.tolist()):
        reason = reasons.loc[ridx]
        pool = pools["mobile"] if reason in for_mobile else pools["any"]
        if reason == "holiday":
            # keep the source's own mobility state: mobile source -> mobile pool
            src_anz = donor_anzwege1.get(
                (persons.at[ridx, "source_H_ID"], persons.at[ridx, "source_P_ID"]), 0)
            pool = pools["mobile"] if (isinstance(src_anz, (int, np.integer)) and 0 < src_anz < 800) else pools["any"]
        sh, sp, level = match_person(persons.loc[ridx], pool, rng=rng)
        persons.at[ridx, "source_H_ID"] = sh
        persons.at[ridx, "source_P_ID"] = sp
        match_level.at[ridx] = level
    counts = reasons.value_counts().to_dict()
    n_remapped = int(reasons.isin(REASONS_REMAP).sum())
    share = n_remapped / max(len(persons), 1)
    report = DiaryMatchReport(n_persons=len(persons), n_remapped=n_remapped, counts_by_reason=counts,
                              match_level_counts=match_level.dropna().astype(int).value_counts().sort_index().to_dict(),
                              share_remapped=share)
    logger.info("[diary_plan_match] %d/%d persons (%.2f%%) remapped to a realisable weekday diary; reasons %s; "
                "match levels %s", n_remapped, len(persons), 100.0 * share, counts, report.match_level_counts)
    if not (EXPECTED_SHARE_REMAPPED[0] <= share <= EXPECTED_SHARE_REMAPPED[1]) and len(persons) > 1000:
        logger.warning("[diary_plan_match] remapped share %.2f%% outside the expected band %s -- check the donor "
                       "columns anzwege1/mobil/feiertag and the Wege join", 100.0 * share, EXPECTED_SHARE_REMAPPED)
    trace = pd.DataFrame({"H_ID": persons["H_ID"].to_numpy(), "P_ID": persons["P_ID"].to_numpy(),
                          "reason": reasons.to_numpy(), "match_level": match_level.to_numpy(),
                          "plan_source_H_ID": persons["source_H_ID"].to_numpy(),
                          "plan_source_P_ID": persons["source_P_ID"].to_numpy()})
    return persons, trace, report
