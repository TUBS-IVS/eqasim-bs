"""Give persons whose plan source has no realisable MiD diary a matched weekday diary.

Sibling of ``weekend_plan_match`` (spec 2026-09-05-plan-structure-fix-design.md, section 2.1,
issue #365). A plan source is NOT realisable when its diary was not collected (``anzwege1`` 803
"Person ohne Wegeerfassung" with ``mobil == 1``, or 804 "Mobilitaet unbekannt"), when it consists
only of rbW legs that package 2 removes, when it becomes empty after the leading arrive-home leg
is dropped, or when it was recorded on a public holiday (``feiertag == 1``; SrV reference days
exclude public holidays). 803 persons with ``mobil == 0`` genuinely stayed home and keep their
(empty) day. The remap reuses ``weekend_plan_match.match_person`` (has_license, sex, age band,
employed, has_pt; hierarchical relaxation; P_GEW-weighted draw) and continues the caller's rng.

Under ``hard_employment`` (issue #368, Plan B Task 6) the ``employed`` key is never relaxed:
the ladder would otherwise drop it second-from-last, so a non-employed person could inherit an
employed donor's work diary although their own employment attribute comes from a DIFFERENT MiD
respondent -- a plan the employment-conditional work control (work_by_employment) would then be
fighting. This is a GUARD, not a correction: it constrains a boundary that the pool composition
could start crossing more often, and the count of surviving crossings is reported and logged as
a rate (``DiaryMatchReport.n_crossed_employment_boundary``).

Under ``fine_child_age_bands`` (issue #386) the ``age_band`` key uses
``weekend_plan_match.FINE_CHILD_AGE_BAND_EDGES``, which splits the coarse 6-13 child band into
6-9 (primary school) and 10-13 (lower secondary), for THIS match only -- the weekend match keeps
the coarse edges, whose draw sequence is a byte-identity contract pinned by
``tests/test_weekend_plan_match.py``. Under the coarse band a first-grader can inherit a
13-year-old's school day, whose departure times and trip lengths differ systematically. Unlike
the employment guard above, ``age_band`` stays a SOFT key that the relaxation ladder may still
drop, so crossings are legitimate and expected in BOTH arms; the count is therefore reported and
logged as a rate in both (``DiaryMatchReport.n_crossed_fine_child_age_band`` over
``n_remapped_in_split_child_band``) and no threshold is asserted -- the OFF arm of an A/B measures
today's rate, the ON arm the residual.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from braunschweig.popsim.attributes import EMPLOYED_TAET
from braunschweig.popsim.seed import WEEKDAY_KERNWO
from braunschweig.popsim.weekend_plan_match import (
    AGE_BAND_EDGES, FINE_CHILD_AGE_BAND_EDGES, age_band_index, match_person,
)

logger = logging.getLogger(__name__)

NO_DIARY_CODES = (803, 804)
#: MiD ``feiertag`` value marking a diary reported on a public holiday. SrV reference days
#: exclude public holidays, so such a diary is not a realisable weekday plan. Named here (rather
#: than typed as a literal at each comparison) because the home-office donor pool applies the
#: SAME exclusion to its donors and must read the identical code
#: (:func:`braunschweig.synthesis.commute_day.donor_pool.filter_donor_diaries`).
MID_HOLIDAY = 1
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
#: The ``weekend_plan_match.match_person`` key(s) this pass refuses to relax when
#: ``hard_employment`` is on (issue #368). ``employed`` is derived there from
#: ``P_TAET in attributes.EMPLOYED_TAET`` -- the same single source of truth the
#: employment control and the crossing counter below use.
HARD_EMPLOYMENT_KEYS = frozenset({"employed"})


def _split_child_age_range():
    """Inclusive age range of the coarse band the fine edges split, from the edges themselves.

    ``pd.cut`` bins are right-closed, so a coarse band ``(lower, upper]`` covers the ages
    ``lower + 1 .. upper``. The split band is the coarse band that contains the edge(s) the
    fine set adds (here: 9, inside the coarse ``(5, 13]`` band -> ages 6-13).
    """
    added = sorted(set(FINE_CHILD_AGE_BAND_EDGES) - set(AGE_BAND_EDGES))
    if not added:
        raise ValueError("FINE_CHILD_AGE_BAND_EDGES adds no edge to AGE_BAND_EDGES")
    lower = max(edge for edge in AGE_BAND_EDGES if edge < added[0])
    upper = min(edge for edge in AGE_BAND_EDGES if edge >= added[-1])
    return lower + 1, upper


#: The ONE coarse band (:data:`weekend_plan_match.AGE_BAND_EDGES`) that
#: :data:`weekend_plan_match.FINE_CHILD_AGE_BAND_EDGES` splits, as the inclusive age range
#: ``(6, 13)``. The crossing counter below reports over exactly the persons inside it -- the
#: only ones the fine bands can move -- so the rate is not diluted by adults, for whom the
#: two edge sets are identical. Derived from the two edge tuples rather than typed as
#: literals, so it cannot drift away from them.
SPLIT_CHILD_AGE_RANGE = _split_child_age_range()


@dataclass(frozen=True)
class DiaryMatchReport:
    n_persons: int
    n_remapped: int
    counts_by_reason: dict
    match_level_counts: dict
    share_remapped: float
    #: Remapped persons whose NEW donor sits on the other side of the employment
    #: boundary (``P_TAET in EMPLOYED_TAET`` differs). With ``hard_employment`` on this
    #: must be 0; a non-zero value means match_person had to use its whole-pool
    #: fallback because the donor pool held nobody of the person's employment class.
    #: Deliberately WITHOUT a default: a construction that forgets it would otherwise
    #: claim "no crossings" without having counted any.
    n_crossed_employment_boundary: int
    #: Remapped persons whose own age lies in :data:`SPLIT_CHILD_AGE_RANGE`, i.e. inside the
    #: single coarse band the fine child bands split. Denominator of the crossing rate below.
    n_remapped_in_split_child_band: int
    #: Of those, the ones whose NEW donor sits in a different FINE child age band. Measured
    #: against the FINE edges in BOTH arms, so the flag-OFF arm reports today's rate and the
    #: two arms of an A/B are directly comparable. Unlike the employment boundary this is NOT
    #: required to be 0 with the flag on: ``age_band`` remains a SOFT key that the relaxation
    #: ladder may drop, so a residual is legitimate. Deliberately WITHOUT a default, for the
    #: same reason as the employment counter above.
    n_crossed_fine_child_age_band: int


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
    holiday = (donors["feiertag"].to_numpy() == MID_HOLIDAY if "feiertag" in donors.columns
               else np.zeros(len(donors), bool))
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


def _count_employment_boundary_crossings(persons, donor_pool, remapped_index):
    """Count REMAPPED persons whose new donor has the other employment class.

    Both sides are derived exactly as ``weekend_plan_match._person_keys`` derives the
    ``employed`` match key: ``P_TAET in attributes.EMPLOYED_TAET``. ``donor_pool`` is
    the realisable "any" pool, which is a superset of the "mobile" pool and therefore
    contains every donor the match can have drawn; a source that does not resolve in it
    would make the count meaningless, so it RAISES instead of scoring silently.
    """
    if len(remapped_index) == 0:
        return 0
    remapped = persons.loc[remapped_index]
    person_employed = remapped["P_TAET"].isin(EMPLOYED_TAET).to_numpy()
    donor_employed_by_key = donor_pool.set_index(["H_ID", "P_ID"])["P_TAET"].isin(EMPLOYED_TAET)
    donor_index = pd.MultiIndex.from_arrays([remapped["source_H_ID"], remapped["source_P_ID"]])
    donor_employed = donor_employed_by_key.reindex(donor_index)
    n_unresolved = int(donor_employed.isna().sum())
    if n_unresolved:
        raise ValueError(
            f"diary_plan_match: {n_unresolved} remapped plan source(s) do not resolve to a donor "
            "of the realisable pool; the employment-boundary count cannot be computed")
    return int((donor_employed.to_numpy().astype(bool) != person_employed).sum())


def _count_fine_child_band_crossings(persons, donor_pool, remapped_index):
    """``(n_remapped_in_split_child_band, n_crossed_fine_child_age_band)``.

    Both sides are banded with :data:`weekend_plan_match.FINE_CHILD_AGE_BAND_EDGES`
    REGARDLESS of the ``fine_child_age_bands`` flag, so the two arms of an A/B measure the
    same quantity: the OFF arm today's crossing rate, the ON arm the residual the soft-key
    ladder still produces. Only persons whose OWN age lies in :data:`SPLIT_CHILD_AGE_RANGE`
    are counted -- outside it the fine and coarse edges are identical, so including them
    would dilute the rate with adult ladder relaxations the flag cannot influence.

    ``donor_pool`` is the realisable "any" pool, a superset of the "mobile" pool and hence of
    every donor the match can have drawn; an unresolvable source RAISES rather than scoring
    silently (same contract as :func:`_count_employment_boundary_crossings`).
    """
    if len(remapped_index) == 0:
        return 0, 0
    remapped = persons.loc[remapped_index]
    lower, upper = SPLIT_CHILD_AGE_RANGE
    ages = pd.to_numeric(remapped["HP_ALTER"], errors="raise")
    in_split_band = ((ages >= lower) & (ages <= upper)).to_numpy()
    if not in_split_band.any():
        return 0, 0
    person_band = age_band_index(remapped["HP_ALTER"], edges=FINE_CHILD_AGE_BAND_EDGES)
    donor_band_by_key = pd.Series(
        age_band_index(donor_pool["HP_ALTER"], edges=FINE_CHILD_AGE_BAND_EDGES),
        index=pd.MultiIndex.from_arrays([donor_pool["H_ID"], donor_pool["P_ID"]]))
    donor_index = pd.MultiIndex.from_arrays([remapped["source_H_ID"], remapped["source_P_ID"]])
    donor_band = donor_band_by_key.reindex(donor_index)
    n_unresolved = int(donor_band.isna().sum())
    if n_unresolved:
        raise ValueError(
            f"diary_plan_match: {n_unresolved} remapped plan source(s) do not resolve to a donor "
            "of the realisable pool; the fine child age band crossing count cannot be computed")
    crossed = (donor_band.to_numpy() != person_band) & in_split_band
    return int(in_split_band.sum()), int(crossed.sum())


def reassign_diaryless_plan_sources(persons, donor_persons, facts, *, rng, exclude_rbw_legs,
                                    exclude_holidays, drop_leading_arrive_home_leg,
                                    hard_employment, fine_child_age_bands):
    """Remap every person whose plan source has no realisable weekday diary.

    ``hard_employment``: when True, ``employed`` is passed to ``match_person`` as an
    un-relaxable key, so a remapped person can only inherit the diary of a donor of
    their OWN employment class (issue #368). The number of surviving crossings is
    counted over the remapped persons and logged as a rate; with the flag on it must
    be 0 (a non-zero count means the donor pool held nobody of that class, so
    match_person fell back to the whole pool).

    ``fine_child_age_bands``: when True, the ``age_band`` key uses
    :data:`weekend_plan_match.FINE_CHILD_AGE_BAND_EDGES` instead of the coarse production
    edges, so a 6-9-year-old is no longer interchangeable with a 10-13-year-old (issue #386).
    THIS match only -- the weekend match keeps the coarse edges. ``match_person`` draws
    exactly one weighted value per call either way, so the shared completion RNG stream
    consumes the same number of values with the flag on or off. Crossings of the fine band
    are counted in BOTH arms (see :func:`_count_fine_child_band_crossings`) and logged as a
    rate; they are NOT required to be 0 with the flag on, because ``age_band`` stays a soft
    key the ladder may relax.
    """
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
    hard_keys = HARD_EMPLOYMENT_KEYS if hard_employment else frozenset()
    age_band_edges = FINE_CHILD_AGE_BAND_EDGES if fine_child_age_bands else AGE_BAND_EDGES
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
        sh, sp, level = match_person(persons.loc[ridx], pool, rng=rng, hard_keys=hard_keys,
                                     age_band_edges=age_band_edges)
        persons.at[ridx, "source_H_ID"] = sh
        persons.at[ridx, "source_P_ID"] = sp
        match_level.at[ridx] = level
    counts = reasons.value_counts().to_dict()
    n_remapped = int(reasons.isin(REASONS_REMAP).sum())
    share = n_remapped / max(len(persons), 1)
    crossed = _count_employment_boundary_crossings(persons, pools["any"], to_remap)
    n_split_band, crossed_band = _count_fine_child_band_crossings(persons, pools["any"], to_remap)
    report = DiaryMatchReport(n_persons=len(persons), n_remapped=n_remapped, counts_by_reason=counts,
                              match_level_counts=match_level.dropna().astype(int).value_counts().sort_index().to_dict(),
                              share_remapped=share, n_crossed_employment_boundary=crossed,
                              n_remapped_in_split_child_band=n_split_band,
                              n_crossed_fine_child_age_band=crossed_band)
    logger.info("[diary_plan_match] %d/%d persons (%.2f%%) remapped to a realisable weekday diary; reasons %s; "
                "match levels %s (levels count SOFT-key relaxations; hard_employment=%s)",
                n_remapped, len(persons), 100.0 * share, counts, report.match_level_counts, hard_employment)
    # No silent fallback: the boundary crossings are reported as a RATE, and a crossing
    # that survives the hard key means match_person had to draw from the whole pool.
    logger.log(logging.WARNING if (hard_employment and crossed > 0) else logging.INFO,
               "[diary_plan_match] employment boundary crossed by %d/%d remaps (%.2f%%); "
               "hard_employment=%s (with it on the count must be 0 -- a non-zero count means the "
               "donor pool held no realisable diary of the person's employment class)",
               crossed, n_remapped, 100.0 * crossed / max(n_remapped, 1), hard_employment)
    # Same no-silent-fallback rule for the age band, reported over the persons the fine bands
    # can actually move. No threshold is asserted: age_band is a SOFT key, so a residual is a
    # legitimate ladder relaxation -- the OFF/ON arms of an A/B are the comparison.
    logger.info("[diary_plan_match] fine child age band crossed by %d/%d remapped %d-%d-year-olds "
                "(%.2f%%); fine_child_age_bands=%s (measured against the FINE edges in BOTH arms, "
                "so the OFF arm reports today's rate)",
                crossed_band, n_split_band, SPLIT_CHILD_AGE_RANGE[0], SPLIT_CHILD_AGE_RANGE[1],
                100.0 * crossed_band / max(n_split_band, 1), fine_child_age_bands)
    if not (EXPECTED_SHARE_REMAPPED[0] <= share <= EXPECTED_SHARE_REMAPPED[1]) and len(persons) > 1000:
        logger.warning("[diary_plan_match] remapped share %.2f%% outside the expected band %s -- check the donor "
                       "columns anzwege1/mobil/feiertag and the Wege join", 100.0 * share, EXPECTED_SHARE_REMAPPED)
    trace = pd.DataFrame({"H_ID": persons["H_ID"].to_numpy(), "P_ID": persons["P_ID"].to_numpy(),
                          "reason": reasons.to_numpy(), "match_level": match_level.to_numpy(),
                          "plan_source_H_ID": persons["source_H_ID"].to_numpy(),
                          "plan_source_P_ID": persons["source_P_ID"].to_numpy()})
    return persons, trace, report
