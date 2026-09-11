"""Give weekend-surveyed donor households weekday plans by remapping their
``source_H_ID``/``source_P_ID`` to a matched weekday household (HH-first, then
person-level fallback). Sibling of ``member_completion``; runs after it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from braunschweig.popsim.attributes import EMPLOYED_TAET, PT_SUBSCRIPTION_FKARTE
from braunschweig.popsim.sampling import weighted_choice

logger = logging.getLogger(__name__)

# Soft keys ordered HIGH→LOW priority (dropped from the END first). ``size`` AND
# ``regiostar7`` are separate HARD keys (always required): equal size so the
# person-to-person role alignment is feasible, equal RegioStaR so the borrowed
# weekday plan comes from the SAME spatial type (distances / mode choice depend
# strongly on urbanity) -- user decision 2026-06-18.
SOFT_KEYS_BY_PRIORITY = ("car_class", "any_license", "any_pt", "hh_type5", "oek_status")
HARD_KEYS = ("size", "regiostar7")


def _car_class(n_cars: pd.Series) -> np.ndarray:
    n = pd.to_numeric(n_cars, errors="raise").astype(int)
    return np.where(n <= 0, "0", np.where(n == 1, "1", "2plus"))


def build_hh_features(households: pd.DataFrame, persons: pd.DataFrame) -> pd.DataFrame:
    has_lic = persons.assign(_lic=persons["P_FSCHEIN"].eq(1)).groupby("H_ID")["_lic"].any()
    has_pt = persons.assign(
        _pt=persons["P_FKARTE"].isin(PT_SUBSCRIPTION_FKARTE)
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
    feats["H_GEW"] = households.set_index("H_ID")["H_GEW"].reindex(feats.index).to_numpy()
    return feats


def match_household(target_id, target_feats, weekday_feats, *, rng, weekday_by_key=None):
    """Find a weekday household of EQUAL size AND EQUAL RegioStaR (the hard keys),
    matching as many soft keys as possible (drop the lowest-priority soft key
    first). Returns ``(matched_H_ID, relaxation_level)`` or ``(None, None)`` if no
    weekday household shares the target's size and RegioStaR (caller then uses the
    person-level fallback). The match never crosses spatial type.

    ``weekday_by_key`` is an optional precomputed ``{(size, regiostar7): subframe}``
    lookup (``weekday_feats`` grouped on the two hard keys). When given, the pool is
    fetched in O(1) instead of re-scanning the full ``weekday_feats`` on every call
    (the dominant cost over tens of thousands of weekend households). The grouped
    subframe preserves ``weekday_feats``'s row order and ``weighted_choice`` draws
    over ``sorted(ids)``, so the result is BYTE-IDENTICAL to the boolean-mask path.
    """
    if weekday_by_key is not None:
        pool = weekday_by_key.get(
            (target_feats["size"], target_feats["regiostar7"])
        )
        if pool is None or len(pool) == 0:
            return None, None
    else:
        pool = weekday_feats[
            (weekday_feats["size"] == target_feats["size"])
            & (weekday_feats["regiostar7"] == target_feats["regiostar7"])
        ]
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
            return weighted_choice(ids, narrowed.loc[ids, "H_GEW"].to_numpy(), rng=rng), level
        if not active:
            ids = sorted(pool.index.tolist())  # size + RegioStaR only
            return (weighted_choice(ids, pool.loc[ids, "H_GEW"].to_numpy(), rng=rng),
                    len(SOFT_KEYS_BY_PRIORITY))
        active.pop()  # drop the lowest-priority remaining soft key


AGE_BAND_EDGES = (-1, 5, 13, 17, 200)
#: Refinement of :data:`AGE_BAND_EDGES` that splits the 6-13 child band into 6-9
#: (primary school, Grundschule) and 10-13 (lower secondary), for the DIARY plan
#: match only (issue #386). Under the coarse band a first-grader and a 13-year-old
#: are interchangeable donors although their school days differ systematically:
#: on the raw MiD 2023 B1 (measured 2026-09-09) a first education leg before 07:00
#: occurs for 5.4 % of 6-9-year-olds but 18.2 % of 10-13-year-olds, and
#: secondary-school ways are longer than primary-school ways. Every coarse edge is
#: KEPT and only the value 9 is added, so the 0-5, 14-17 and 18+ bands -- and
#: therefore which adult donors are interchangeable -- are unchanged.
FINE_CHILD_AGE_BAND_EDGES = (-1, 5, 9, 13, 17, 200)


def _validate_age_band_edges(edges, *, what: str) -> list:
    """Return ``edges`` as a list after checking it is a usable ``pd.cut`` bin spec.

    Raises here, naming the parameter and the caller, rather than letting ``pd.cut``
    fail deep inside :func:`_person_keys` with a message that names neither.
    """
    values = list(edges)
    if len(values) < 2:
        raise ValueError(f"{what}: age_band_edges needs at least 2 edges, got {values}")
    if any(b <= a for a, b in zip(values, values[1:])):
        raise ValueError(f"{what}: age_band_edges must be strictly increasing, got {values}")
    return values


def age_band_index(ages: pd.Series, edges=AGE_BAND_EDGES) -> np.ndarray:
    """Band index of each age under ``edges`` (default: the coarse production bands)."""
    return pd.cut(ages, bins=list(edges), labels=False).to_numpy()


def align_members(target_members: pd.DataFrame, donor_members: pd.DataFrame, *,
                  age_band_edges=AGE_BAND_EDGES):
    """Greedily pair each target member to a distinct donor member by
    (age band, sex), falling back to age band only, then any free donor.

    Each paired target member inherits its donor's plan source, so the bands decide
    whose recorded day a child receives: under the coarse :data:`AGE_BAND_EDGES` a
    first-grader and a 13-year-old are interchangeable here exactly as they were in the
    diary match (issue #386). Pass :data:`FINE_CHILD_AGE_BAND_EDGES` to separate them.

    The number of pairs returned is ``min(len(target_members), len(donor_members))``
    under ANY edge set -- every target member takes some free donor through the
    ``any_free`` fallback -- and this function consumes no rng. Changing the bands
    therefore changes WHICH donor a member is paired with without changing how many
    person-level fallback draws the caller makes afterwards, i.e. without moving the
    shared completion rng stream.
    """
    d_band = age_band_index(donor_members["HP_ALTER"], edges=age_band_edges)
    d_sex = donor_members["HP_SEX"].to_numpy()
    used = np.zeros(len(donor_members), dtype=bool)
    t_band = age_band_index(target_members["HP_ALTER"], edges=age_band_edges)
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


def _person_keys(persons: pd.DataFrame, *, age_band_edges=AGE_BAND_EDGES) -> pd.DataFrame:
    return pd.DataFrame({
        "age_band": age_band_index(persons["HP_ALTER"], edges=age_band_edges),
        "sex": persons["HP_SEX"].to_numpy(),
        "has_license": persons["P_FSCHEIN"].eq(1).to_numpy(),
        # Canonical MiD employment (ILO-style `erwerb`), same single source of truth
        # as the Tier-3 employment control (attributes.EMPLOYED_TAET, control_spec.py):
        # P_TAET in {1, 2, 3, 4, 6, 8} = employed, incl. 8 = Azubi/Ausbildung.
        # 5 = Elternzeit and 7 = Wehr-/Bundesfreiwilligendienst/FSJ are NOT employed.
        "employed": persons["P_TAET"].isin(EMPLOYED_TAET).to_numpy(),
        "has_pt": persons["P_FKARTE"].isin(PT_SUBSCRIPTION_FKARTE).to_numpy(),
    }, index=persons.index)


def match_person(target_row, weekday_persons, *, rng, hard_keys=frozenset(),
                 age_band_edges=AGE_BAND_EDGES):
    """Draw ONE weekday donor person for ``target_row``, P_GEW-weighted.

    Matches on as many of ``PERSON_KEYS_BY_PRIORITY`` as possible, dropping the
    lowest-priority remaining SOFT key when no donor qualifies. Returns
    ``(H_ID, P_ID, relaxation_level)``.

    ``hard_keys`` names keys that are NEVER relaxed (default: none). They are
    required on every candidate pool, and the ladder pops only the soft keys, in
    the same order as without them. When the soft keys are exhausted the draw comes
    from the hard-key-only pool; only when even THAT pool is empty does the
    whole-pool fallback of the unconditional ladder apply -- it still returns a
    donor rather than raising, and the CALLER is responsible for counting how often
    the boundary was crossed (see ``diary_plan_match.reassign_diaryless_plan_sources``,
    ``DiaryMatchReport.n_crossed_employment_boundary``).

    ``age_band_edges`` are the ``pd.cut`` bin edges behind the ``age_band`` key
    (default :data:`AGE_BAND_EDGES`, today's coarse 0-5 / 6-13 / 14-17 / 18+ bands).
    :data:`FINE_CHILD_AGE_BAND_EDGES` additionally separates 6-9 from 10-13 and is
    used by the DIARY match only (issue #386); the weekend match keeps the default,
    whose draw sequence is a byte-identity contract. Like ``hard_keys``, the parameter
    changes WHICH donor is drawn, never HOW MANY rng values are consumed.

    ``level`` counts SOFT-key relaxations, so its maximum is ``len(soft keys)``:
    with ``hard_keys`` empty that is ``len(PERSON_KEYS_BY_PRIORITY)`` (unchanged),
    with one hard key it is one less. Callers that log level histograms must
    therefore read them against the hard-key setting of the run.

    BYTE-IDENTITY (mandatory): with the defaults ``hard_keys=frozenset()`` and
    ``age_band_edges=AGE_BAND_EDGES`` this function takes the same branches, builds
    the same pools in the same order and
    makes the same number of ``weighted_choice`` calls (exactly one, whichever
    branch returns) as the pre-``hard_keys`` implementation. Both callers --
    ``reassign_weekend_plan_sources`` and ``diary_plan_match.
    reassign_diaryless_plan_sources`` -- draw from ONE shared seeded RandomState
    whose draw sequence is a documented byte-identity contract (see the
    ``braunschweig.popsim.completed_donor`` module docstring); the frozen baseline in
    tests/test_weekend_plan_match.py::
    test_match_person_with_empty_hard_keys_reproduces_todays_draw_sequence pins it.
    """
    if len(weekday_persons) == 0:
        raise ValueError("empty weekday person pool; cannot match weekend person")
    if hard_keys:
        # A misspelled key would otherwise be silently dropped by the comprehensions
        # below, leaving the caller believing a boundary is guarded when it is not.
        unknown_keys = sorted(set(hard_keys) - set(PERSON_KEYS_BY_PRIORITY))
        if unknown_keys:
            raise ValueError(
                f"match_person: unknown hard match key(s) {unknown_keys}; valid keys are "
                f"{list(PERSON_KEYS_BY_PRIORITY)}")
    edges = _validate_age_band_edges(age_band_edges, what="match_person")
    keys = _person_keys(weekday_persons, age_band_edges=edges)
    tkeys = _person_keys(pd.DataFrame([target_row]), age_band_edges=edges).iloc[0]
    hard = [key for key in PERSON_KEYS_BY_PRIORITY if key in hard_keys]
    soft = [key for key in PERSON_KEYS_BY_PRIORITY if key not in hard_keys]
    hard_mask = pd.Series(True, index=weekday_persons.index)
    for key in hard:
        hard_mask &= keys[key] == tkeys[key]
    active = list(soft)
    while True:
        mask = hard_mask.copy()
        for key in active:
            mask &= keys[key] == tkeys[key]
        pool = weekday_persons[mask]
        if len(pool) > 0:
            level = len(soft) - len(active)
            h, p = weighted_choice(list(zip(pool["H_ID"], pool["P_ID"])),
                                   pool["P_GEW"].to_numpy(), rng=rng)
            return h, p, level
        if not active:
            h, p = weighted_choice(list(zip(weekday_persons["H_ID"], weekday_persons["P_ID"])),
                                   weekday_persons["P_GEW"].to_numpy(), rng=rng)
            hard_note = f" and no donor shares the hard key(s) {hard}" if hard else ""
            logger.debug(
                "[weekend_plan_match] match_person hit the whole-pool size-only "
                "fallback (all soft person keys dropped%s); drew weekday (%s, %s).",
                hard_note, h, p)
            return h, p, len(soft)
        active.pop()


@dataclass(frozen=True)
class WeekendMatchReport:
    n_weekend_households: int
    n_hh_matched: int
    n_person_fallback_households: int
    n_persons_remapped: int
    hh_match_level_counts: dict
    n_swept: int = 0


def reassign_weekend_plan_sources(households, persons, *, rng, household_id="H_ID",
                                  fine_child_age_bands=True):
    """Give every weekend-reporting donor a matched WEEKDAY plan source.

    ``fine_child_age_bands`` (default True, issue #386): band 6-13-year-olds finely
    (6-9 / 10-13) wherever this pass matches on age -- the household-level member
    alignment (:func:`align_members`), the person-level fallback and the mixed-household
    sweep -- so a primary-school child cannot inherit a 13-year-old's school day. All
    three consume the same number of rng values under either edge set (``align_members``
    draws none and returns the same number of pairs; ``match_person`` draws exactly one
    per call), so the flag changes the ASSIGNMENT, never the draw sequence of the
    completion stream this pass shares with member completion and the diary match.
    """
    from braunschweig.popsim import day_type as _dt
    from braunschweig.popsim import seed as _seed
    from braunschweig.popsim.day_type import WEEKEND_KERNWO
    from braunschweig.popsim.seed import WEEKDAY_KERNWO

    age_band_edges = FINE_CHILD_AGE_BAND_EDGES if fine_child_age_bands else AGE_BAND_EDGES
    persons = persons.copy()
    hh_dt = _dt.household_day_type(persons, household_id=household_id)
    # completed_donor_households (load_completed_donor) do NOT carry hh_type5 --
    # it is derived later in mid.project_completed_seed, which runs AFTER this
    # remap. Derive it here on a LOCAL copy using the same helper, so build_hh_features
    # never hits a KeyError. Behaviour is identical when hh_type5 is already present.
    if "hh_type5" not in households.columns:
        hh_type5 = _seed.derive_hh_type5(
            persons, household_id_col=household_id, age_col="HP_ALTER")
        households = households.join(
            hh_type5.rename("hh_type5"), on=household_id)
    feats = build_hh_features(households, persons)

    weekday_ids = hh_dt.index[hh_dt == "weekday"]
    weekend_ids = hh_dt.index[hh_dt == "weekend"]
    weekday_feats = feats.loc[feats.index.isin(weekday_ids)]

    # A1: Build the person-fallback pool from REAL (non-imputed) weekday-REPORTING
    # persons, not from all persons belonging to weekday households. This ensures
    # that weekend-reporting members of genuinely-mixed households (resolved to
    # "weekday" by majority vote) are never offered as weekday-plan donors.
    weekday_pool = persons[
        (~persons["member_imputed"].astype(bool))
        & persons["kernwo"].isin(WEEKDAY_KERNWO)
    ]

    persons_by_hh = dict(tuple(persons.groupby(household_id, sort=False)))
    # default trace row = own plan (correct for weekday + filler bookkeeping below)
    resolution = pd.Series("own_plan", index=persons.index)
    match_level = pd.Series(np.nan, index=persons.index)
    resolution[persons["member_imputed"].to_numpy()] = "member_completion_filler"

    n_hh_matched = 0
    n_person_fallback = 0
    n_remapped = 0
    level_counts: dict = {}

    # Pre-group the weekday pool on the two HARD match keys (size, regiostar7) ONCE,
    # so match_household does an O(1) lookup per weekend household instead of
    # re-scanning weekday_feats each time (was O(n_weekend x n_weekday), the
    # dominant cost of this stage). Byte-identical: see match_household docstring.
    weekday_by_key = {
        key: g for key, g in weekday_feats.groupby(["size", "regiostar7"], sort=False)
    }

    weekend_ids_sorted = sorted(weekend_ids)
    n_weekend_total = len(weekend_ids_sorted)
    progress_step = max(1, n_weekend_total // 10)  # ~10 plain-text heartbeats

    for loop_index, hid in enumerate(weekend_ids_sorted):
        if loop_index % progress_step == 0 and loop_index > 0:
            logger.info(
                "[weekend_plan_match] progress %d/%d weekend households (%.0f%%)",
                loop_index, n_weekend_total, 100.0 * loop_index / n_weekend_total,
            )
        target_members = persons_by_hh[hid].reset_index()  # keeps original index in 'index'
        matched_id, level = match_household(
            hid, feats.loc[hid], weekday_feats, rng=rng, weekday_by_key=weekday_by_key)
        if matched_id is not None:
            donor_members = persons_by_hh[matched_id].reset_index(drop=True)
            paired = align_members(target_members, donor_members,
                                   age_band_edges=age_band_edges)
            for tpos, dpos in paired:
                ridx = target_members.loc[tpos, "index"]
                persons.loc[ridx, "source_H_ID"] = donor_members.loc[dpos, "source_H_ID"]
                persons.loc[ridx, "source_P_ID"] = donor_members.loc[dpos, "source_P_ID"]
                resolution[ridx] = "hh_match"
                match_level[ridx] = level
                n_remapped += 1
            n_hh_matched += 1
            level_counts[level] = level_counts.get(level, 0) + 1
            # An UNFILLABLE donor keeps H_GR larger than its real person count, so
            # align_members can run out of donor members and leave surplus weekend
            # persons unpaired. They would otherwise silently keep their WEEKEND
            # source (no-silent-fallback violation); route each to the person-level
            # fallback so no weekend plan leaks into the weekday population.
            paired_tpos = {tpos for tpos, _ in paired}
            for tpos in range(len(target_members)):
                if tpos in paired_tpos:
                    continue
                ridx = target_members.loc[tpos, "index"]
                trow = target_members.loc[tpos]
                sh, sp, plevel = match_person(trow, weekday_pool, rng=rng,
                                              age_band_edges=age_band_edges)
                persons.loc[ridx, "source_H_ID"] = sh
                persons.loc[ridx, "source_P_ID"] = sp
                resolution[ridx] = "person_fallback"
                match_level[ridx] = plevel
                n_remapped += 1
        else:
            for tpos in range(len(target_members)):
                ridx = target_members.loc[tpos, "index"]
                trow = target_members.loc[tpos]
                sh, sp, plevel = match_person(trow, weekday_pool, rng=rng)
                persons.loc[ridx, "source_H_ID"] = sh
                persons.loc[ridx, "source_P_ID"] = sp
                resolution[ridx] = "person_fallback"
                match_level[ridx] = plevel
                n_remapped += 1
            n_person_fallback += 1

    # A2: Safety-net sweep: resolve every person's plan-source to its donor's kernwo;
    # any person whose source is a weekend-diary donor (e.g. a weekend-reporting member
    # of a genuinely-mixed household that household_day_type resolved to "weekday" by
    # majority) gets a weekday plan. Makes the invariant "no person sources a weekend
    # plan" hold universally, regardless of household-level classification.
    real_persons = persons[~persons["member_imputed"].astype(bool)]
    donor_kernwo = real_persons.set_index([household_id, "P_ID"])["kernwo"]
    src_idx = pd.MultiIndex.from_arrays([persons["source_H_ID"], persons["source_P_ID"]])
    src_kernwo = pd.Series(donor_kernwo.reindex(src_idx).to_numpy(), index=persons.index)
    # Defense-in-depth (no silent fallback): a plan-source MUST resolve to a real donor
    # person. A NaN here means a person points at a source absent from the donor frame
    # (upstream corruption / a dropped donor); such a person would be silently skipped by
    # the weekend-source sweep below, so surface it loudly rather than hide it.
    n_unresolved = int(src_kernwo.isna().sum())
    if n_unresolved:
        logger.warning(
            "[weekend_plan_match] %d person(s) have a plan-source that does not resolve "
            "to a real donor person; they are not swept and may carry an inconsistent "
            "plan. This indicates upstream donor/source corruption.", n_unresolved)
    sweep_mask = src_kernwo.isin(WEEKEND_KERNWO)
    n_swept = 0
    for ridx in sorted(persons.index[sweep_mask].tolist()):  # deterministic order
        trow = persons.loc[ridx]
        sh, sp, plevel = match_person(trow, weekday_pool, rng=rng,
                                      age_band_edges=age_band_edges)
        persons.loc[ridx, "source_H_ID"] = sh
        persons.loc[ridx, "source_P_ID"] = sp
        resolution[ridx] = "mixed_person_sweep"
        match_level[ridx] = plevel
        n_remapped += 1
        n_swept += 1

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
        n_swept=n_swept,
    )
    logger.info(
        "[weekend_plan_match] %d weekend households: %d HH-matched, %d via "
        "person-fallback; %d persons remapped, %d swept by per-person safety net. "
        "HH match-level counts: %s",
        report.n_weekend_households, report.n_hh_matched,
        report.n_person_fallback_households, report.n_persons_remapped,
        report.n_swept, report.hh_match_level_counts,
    )
    if len(weekend_ids) and report.n_hh_matched == 0:
        logger.warning(
            "[weekend_plan_match] no weekend household matched at HH level; all "
            "%d fell back to person-level matching.", len(weekend_ids))
    if n_swept:
        logger.warning(
            "[weekend_plan_match] safety-net sweep caught %d person(s) sourcing "
            "weekend plans (mixed households resolved to weekday by majority); "
            "remapped each to a weekday-reporting donor.", n_swept)
    return persons, trace, report
