"""Home-office donor matching (ADR-0104, issue #244, Phase B Task 3).

Matches every person drawn to the ``home`` reporting-day state (``state.draw_states``) to a
donor from the MiD home-office-day donor pool (``donor_pool.build_home_office_donor_pool``), so
:mod:`plan_replacement` can copy that donor's own trip chain onto the person. Pure module: no
file I/O, no synpp stage -- exercised only against tiny synthetic frames in
``tests/test_commute_day_matching.py``.

Three HARD criteria (:data:`HARD_CRITERIA`) must match EXACTLY, never coarsened: a donor that
does not share the person's escort duty, child-in-household status, or car ownership is never a
valid substitute for that person's day, however sparse the donor pool. A donor whose own
``has_car`` is unresolved (``NaN`` -- an MiD household the donor pool could not join, see
``donor_pool.donor_attributes``) can therefore never satisfy the ``has_car`` hard criterion and
is excluded from the ENTIRE matching pass up front (counted in
``n_donors_hard_excluded_has_car_unknown``), not merely for the one person it happens to fail.

Five SOFT criteria (:data:`SOFT_CRITERIA`) are coarsened, in this fixed order, only after the
hard criteria and every soft criterion still in play have failed to find a large enough donor
cell (``minimum_cell``):

======  ============================================================================
level   criteria still enforced (soft) in addition to the always-enforced hard three
======  ============================================================================
0       distance_class (exact), sex, age_class, household_size_class, has_license*
1       distance_class (exact), sex, age_class, household_size_class
2       distance_class (exact), sex, age_class
3       distance_class (exact), sex
4       distance_class (exact)
5       distance_class widened by one rank (or donor distance_class == "unknown")
6       no distance constraint at all
======  ============================================================================

``*`` ``has_license`` is used as a soft criterion ONLY when both ``persons_home`` and ``donors``
carry it -- the MiD donor pool built by :mod:`donor_pool` carries no ``has_license`` column at
all, so in production this criterion is skipped from level 0 onward (levels 0 and 1 then enforce
the identical soft set) and the omission is logged once, rather than inventing a value
(CLAUDE.md "No invented reference values").

``household_size_class`` is derived HERE, identically on both sides, from the raw
``household_size`` column each frame carries, via
:func:`synthesis.population.matched.household_size_class` (ruling R1) -- never expected
pre-binned, so the two sides cannot silently drift apart under different binnings.

Persons are processed in ``person_id`` order so a run is reproducible independent of the
caller's row order; within the first cell (level) that reaches ``minimum_cell`` donors, one donor
is drawn uniformly at random via ``rng.randint`` (donors may be reused across persons --
sampling is WITH replacement, matching the survey's role as a re-usable pool of representative
days rather than a one-to-one assignment).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration.commute_day_state_reference import COMMUTE_CLASS_LABELS
from braunschweig.synthesis.commute_day.donor_pool import DISTANCE_CLASS_UNKNOWN
from braunschweig.synthesis.commute_day.state import CLASS_RANK

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day matching]"

#: The three criteria that must match EXACTLY for every candidate donor, at every coarsening
#: level -- never coarsened (see the module docstring).
HARD_CRITERIA = ("has_active_escort", "has_children_u14", "has_car")

#: The five criteria coarsened away in this exact order as a person's donor cell proves too
#: small (see the module docstring's table). ``has_license`` is used only when both frames carry
#: it (see :func:`match_home_office_donors`).
SOFT_CRITERIA = ("distance_class", "sex", "age_class", "household_size_class", "has_license")

#: Highest coarsening level (see the module docstring's table): beyond this, a person is not
#: replaceable at all.
MAX_COARSENING_LEVEL = 6


def _require_columns(frame: pd.DataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what} is missing the required column(s) {missing} "
                         f"(present: {sorted(frame.columns)})")


def _widened_distance_labels(assigned_class: str) -> set:
    """Distance classes within one rank of ``assigned_class`` (inclusive), for coarsening level 5.

    Raises ``ValueError`` (naming the offending value) if ``assigned_class`` is not one of the
    known :data:`braunschweig.calibration.commute_day_state_reference.COMMUTE_CLASS_LABELS` --
    a person's ``assigned_distance_class`` must always be a real class (see
    ``state.assigned_distance_class``), so an unresolvable value here signals a caller defect,
    not a legitimate "no class" case, and should fail loudly rather than raise a bare ``KeyError``.
    """
    if assigned_class not in CLASS_RANK:
        raise ValueError(
            f"{_LOG_TAG} assigned_distance_class {assigned_class!r} is not one of the known "
            f"commute-distance classes {list(COMMUTE_CLASS_LABELS)}; cannot widen it for "
            "coarsening level 5.")
    rank = CLASS_RANK[assigned_class]
    lo = max(rank - 1, 0)
    hi = min(rank + 1, len(COMMUTE_CLASS_LABELS) - 1)
    return set(COMMUTE_CLASS_LABELS[lo:hi + 1])


def match_home_office_donors(persons_home: pd.DataFrame, donors: pd.DataFrame,
                             rng: np.random.RandomState, *, minimum_cell: int = 1
                             ) -> tuple[pd.DataFrame, dict]:
    """Match every ``home``-state person to one donor from the home-office donor pool.

    ``persons_home`` -- one row per person drawn to ``home`` (``state.draw_states``): needs
    ``person_id``, ``assigned_distance_class``, ``sex``, ``age_class``, ``household_size``,
    ``has_active_escort``, ``has_children_u14``, ``has_car``, and optionally ``has_license``.
    ``donors`` -- the donor pool's attributes frame (``donor_pool.donor_attributes``): needs
    ``donor_id``, ``distance_class``, ``sex``, ``age_class``, ``household_size``,
    ``has_active_escort``, ``has_children_u14``, ``has_car``, and optionally ``has_license``.

    Returns ``(matches, diagnostics)``. ``matches`` columns: ``person_id``, ``donor_id``,
    ``coarsening_level`` (int, 0-:data:`MAX_COARSENING_LEVEL`) -- one row per REPLACEABLE
    person; a person with no donor cell reaching ``minimum_cell`` at any level is simply absent
    from ``matches`` (counted in ``diagnostics["n_not_replaceable"]`` instead, never raised: the
    plan-replacement step -- and, ultimately, the state stage -- decide how to treat an
    unreplaceable ``home`` person, see :func:`braunschweig.synthesis.commute_day.plan_replacement.build_day_trips`).

    ``diagnostics``: ``n_persons``, ``matched_by_level`` (dict, level -> count, dense over
    ``0..MAX_COARSENING_LEVEL``), ``n_not_replaceable``, ``share_not_replaceable``,
    ``soft_criteria_used`` (the subset of :data:`SOFT_CRITERIA` actually applied --
    ``has_license`` excluded when not carried by both frames),
    ``n_donors_hard_excluded_has_car_unknown`` (donors with ``has_car`` NaN, excluded from the
    ENTIRE pass up front -- see the module docstring), and
    ``n_persons_hard_criteria_missing`` (``persons_home`` rows with a ``NaN`` in ANY of
    :data:`HARD_CRITERIA` -- such a person can never match any donor, since ``NaN`` never equals
    a donor's exact value, and would otherwise vanish into ``n_not_replaceable`` unexplained).
    """
    _require_columns(persons_home, ("person_id", "assigned_distance_class", "sex", "age_class",
                                    "household_size", "has_active_escort", "has_children_u14",
                                    "has_car"), "persons_home frame")
    _require_columns(donors, ("donor_id", "distance_class", "sex", "age_class", "household_size",
                              "has_active_escort", "has_children_u14", "has_car"), "donors frame")

    # Imported here, not at module level: synthesis.population.matched is a synpp stage module
    # with heavy top-level imports, and this pure module must stay importable without them.
    from synthesis.population.matched import household_size_class

    persons_home = persons_home.sort_values("person_id").reset_index(drop=True)
    # Sorted by donor_id (not merely copied) so that a caller-reordered donor frame always
    # yields the identical eligible_donors array order, and therefore identical draws under an
    # identically-seeded rng (fix round 1 item 5).
    donors = donors.sort_values("donor_id").reset_index(drop=True)
    # Ruling R1: bind household size to the same class on both sides, HERE, from the raw
    # (unbinned) household_size column each frame carries.
    persons_home["household_size_class"] = household_size_class(persons_home["household_size"])
    donors["household_size_class"] = household_size_class(donors["household_size"])

    has_license_available = "has_license" in persons_home.columns and "has_license" in donors.columns
    soft_criteria_used = [c for c in SOFT_CRITERIA if c != "has_license" or has_license_available]
    if not has_license_available:
        logger.info(
            "%s has_license: not present on both frames (MiD donors carry no has_license "
            "column) -- skipped as a soft criterion rather than invented.", _LOG_TAG)

    # A person with a NaN hard-criteria value can never match any donor (NaN never equals a
    # donor's exact value at any coarsening level, since the hard criteria are never coarsened);
    # counted separately so such persons do not vanish into n_not_replaceable unexplained (fix
    # round 1 item 4).
    person_hard_missing_mask = persons_home[list(HARD_CRITERIA)].isna().any(axis=1)
    n_persons_hard_criteria_missing = int(person_hard_missing_mask.sum())
    if n_persons_hard_criteria_missing > 0:
        logger.warning(
            "%s %d/%d persons_home rows have a NaN value in a hard-criteria column %s -- these "
            "can never match any donor and will show up in n_not_replaceable; check the upstream "
            "attribute source.", _LOG_TAG, n_persons_hard_criteria_missing, len(persons_home),
            HARD_CRITERIA)

    # A donor with an unresolved has_car can never satisfy the has_car HARD criterion for anyone
    # (CLAUDE.md fallback transparency: excluded loudly, up front, not silently per-person).
    n_donors_hard_excluded_has_car_unknown = int(donors["has_car"].isna().sum())
    if n_donors_hard_excluded_has_car_unknown > 0:
        logger.warning(
            "%s %d/%d donors have has_car=NaN and are excluded from matching entirely (the "
            "has_car hard criterion can never be satisfied by an unresolved value).",
            _LOG_TAG, n_donors_hard_excluded_has_car_unknown, len(donors))
    eligible_donors = donors.loc[donors["has_car"].notna()].reset_index(drop=True)

    donor_id = eligible_donors["donor_id"].to_numpy()
    donor_escort = eligible_donors["has_active_escort"].to_numpy()
    donor_children = eligible_donors["has_children_u14"].to_numpy()
    donor_car = eligible_donors["has_car"].to_numpy()
    donor_distance_class = eligible_donors["distance_class"].to_numpy()
    donor_sex = eligible_donors["sex"].to_numpy()
    donor_age_class = eligible_donors["age_class"].to_numpy()
    donor_hh_class = eligible_donors["household_size_class"].to_numpy()
    donor_license = eligible_donors["has_license"].to_numpy() if has_license_available else None

    matches = []
    matched_by_level = {level: 0 for level in range(MAX_COARSENING_LEVEL + 1)}
    n_not_replaceable = 0

    for person in persons_home.itertuples(index=False):
        hard_mask = ((donor_escort == person.has_active_escort)
                    & (donor_children == person.has_children_u14)
                    & (donor_car == person.has_car))

        chosen_donor = None
        chosen_level = None
        for level in range(MAX_COARSENING_LEVEL + 1):
            mask = hard_mask.copy()
            if level < 4:
                mask &= (donor_sex == person.sex)
            if level < 3:
                mask &= (donor_age_class == person.age_class)
            if level < 2:
                mask &= (donor_hh_class == person.household_size_class)
            if level < 1 and has_license_available:
                mask &= (donor_license == person.has_license)
            if level <= 4:
                mask &= (donor_distance_class == person.assigned_distance_class)
            elif level == 5:
                allowed = _widened_distance_labels(person.assigned_distance_class)
                mask &= (np.isin(donor_distance_class, list(allowed))
                        | (donor_distance_class == DISTANCE_CLASS_UNKNOWN))
            # level 6: no distance constraint at all -- mask left as-is.

            count = int(mask.sum())
            if count >= minimum_cell:
                candidate_positions = np.flatnonzero(mask)
                chosen_position = candidate_positions[rng.randint(0, count)]
                chosen_donor = donor_id[chosen_position]
                chosen_level = level
                break

        if chosen_donor is None:
            n_not_replaceable += 1
        else:
            matches.append((person.person_id, chosen_donor, chosen_level))
            matched_by_level[chosen_level] += 1

    n_persons = len(persons_home)
    share_not_replaceable = n_not_replaceable / max(n_persons, 1)

    result = pd.DataFrame(matches, columns=["person_id", "donor_id", "coarsening_level"])
    if result.empty:
        result = pd.DataFrame(columns=["person_id", "donor_id", "coarsening_level"])
        result["coarsening_level"] = result["coarsening_level"].astype(int)

    diagnostics = {
        "n_persons": n_persons,
        "matched_by_level": matched_by_level,
        "n_not_replaceable": n_not_replaceable,
        "share_not_replaceable": share_not_replaceable,
        "soft_criteria_used": soft_criteria_used,
        "n_donors_hard_excluded_has_car_unknown": n_donors_hard_excluded_has_car_unknown,
        "n_persons_hard_criteria_missing": n_persons_hard_criteria_missing,
    }

    logger.info(
        "%s matched %d/%d persons (%.1f%% not replaceable); by coarsening level (rate of "
        "matched persons): %s", _LOG_TAG, n_persons - n_not_replaceable, n_persons,
        100.0 * share_not_replaceable,
        {level: f"{count}/{n_persons} ({100.0 * count / max(n_persons, 1):.1f}%)"
         for level, count in matched_by_level.items() if count > 0})
    if share_not_replaceable > 0.5:
        logger.warning(
            "%s %d/%d persons (%.1f%%) are NOT replaceable by any donor at any coarsening level "
            "-- above 50%%, which usually signals a donor pool that is too small or too narrow "
            "for this population rather than a genuinely sparse match.",
            _LOG_TAG, n_not_replaceable, n_persons, 100.0 * share_not_replaceable)

    return result, diagnostics
