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

A FOURTH hard criterion (ruling R7) is not an equality but an implication, which is why it lives
beside :data:`HARD_CRITERIA` rather than in it: a donor whose chain contains an EDUCATION activity
(``has_education_leg``, see ``donor_pool.attach_trip_derived_attributes``) is eligible ONLY for a
person who has an education location of their own (``has_education_location``). A donor WITHOUT an
education leg matches anyone. Measured on the 100 % proof run of 2026-09-05: 36 of the 5,086
persons drawn to ``home`` received such a donor although they have no education location, the
transplanted activity could then not be anchored anywhere, and
``synthesis/population/spatial/secondary/problems.py::find_assignment_problems`` built a problem
with ``origin``/``destination`` ``None`` on which the secondary chainsolver raised
``AttributeError``. Both columns are REQUIRED on their frame: silently skipping this criterion
when a column is absent would re-open exactly that failure (CLAUDE.md "Fallback transparency").

Five SOFT criteria (:data:`SOFT_CRITERIA`) are coarsened, in this fixed order, only after the
hard criteria and every soft criterion still in play have failed to find a large enough donor
cell (``minimum_cell``):

======  ============================================================================
level   soft criteria still enforced, on top of the always-enforced hard ones (the three
        exact-match criteria plus the education-anchor rule above)
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
import math

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

#: Donor column flagging an ``education`` activity in the donor's own chain (ruling R7).
DONOR_EDUCATION_LEG_COLUMN = "has_education_leg"
#: Person column flagging that the receiving person has an assigned education location, i.e. that
#: an education activity transplanted onto their day can be anchored (ruling R7).
PERSON_EDUCATION_LOCATION_COLUMN = "has_education_location"


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


#: Reported in place of an UNRESOLVED cell value in :func:`donor_pool_size_by_hard_cell` -- the
#: same word ``donor_pool.DISTANCE_CLASS_UNKNOWN`` and ``state_stage``'s ``by_assigned_class``
#: already use for a missing class, so one vocabulary covers all of them.
CELL_VALUE_UNKNOWN = "unknown"


def _cell_value(value, *, as_flag: bool = True):
    """One census cell value: a plain ``bool`` / ``str``, or :data:`CELL_VALUE_UNKNOWN`.

    NEVER a bare ``bool(value)`` on a grouped key (PR #417 review): ``bool(numpy.nan)`` is
    ``True``, so an unresolved flag would be filed under the POSITIVE value, and ``bool(pandas.NA)``
    -- what a nullable ``boolean`` column yields -- raises ``TypeError`` and would abort the whole
    matching pass from inside a diagnostic. Both are silent-wrong-answer failure modes of exactly
    the kind CLAUDE.md's fallback-transparency rule forbids, so an unresolved value is named as
    unresolved instead. ``str(numpy.nan)`` is likewise avoided: it would render the literal
    ``"nan"`` as though it were a distance class.

    In production none of the three flags can be unresolved (``donor_pool.donor_attributes``
    builds ``has_children_u14`` and ``has_active_escort`` with ``fillna``/``isin``, and the
    ``has_car``-unresolved donors are excluded from the pass before the census runs), so this is a
    guard on the function's own contract rather than a live fallback -- it must hold for any frame
    handed to this public function, not only for the one the stage happens to pass today.
    """
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return CELL_VALUE_UNKNOWN
    return bool(value) if as_flag else str(value)


def donor_pool_size_by_hard_cell(eligible_donors: pd.DataFrame) -> list:
    """Donor count per HARD matching cell -- the census half of ADR-0104 check 4 (issue #378).

    One record per occupied cell of ``distance_class`` x :data:`HARD_CRITERIA`
    (``has_active_escort`` x ``has_children_u14`` x ``has_car``), i.e. per cell the coarsening
    cascade can never leave: every soft criterion is dropped as a person's cell proves too small,
    but these four bound the candidate set at EVERY level (level 5 widens the distance class by
    one rank and level 6 drops it, which is why the distance class is reported separately rather
    than folded into one key). At most ``len(COMMUTE_CLASS_LABELS) + 1`` x 2 x 2 x 2 records, so
    the census is small enough to serialise in full.

    ``eligible_donors`` must already exclude the ``has_car``-unknown donors
    (:func:`match_home_office_donors` removes them from the ENTIRE pass), because a donor that
    can never satisfy a hard criterion is not available in any cell and counting it would report
    a pool that is larger than the one the matching actually saw.

    NOT a duplicate of ``donor_pool.build_home_office_donor_pool``'s own ``cells`` diagnostic,
    which is deliberately a different quantity and cannot answer ADR-0104 check 4: that one is
    keyed on THREE dimensions (``distance_class``, ``has_children_u14``, ``has_active_escort``)
    and so silently merges cells that the ``has_car`` hard criterion splits, and it counts the
    WHOLE pool, including the ``has_car``-unknown donors that can never be matched to anybody.
    It describes the pool as BUILT; this describes the pool as MATCHED AGAINST.

    Returns a list of plain-Python dicts -- never a frame or a tuple-keyed dict -- sorted by the
    four cell columns, so the census round-trips through strict JSON unchanged and two runs of
    the same pool produce byte-identical output. ``dropna=False`` plus :func:`_cell_value`: a
    donor whose cell column is UNRESOLVED is reported under the literal
    :data:`CELL_VALUE_UNKNOWN` rather than dropped, so the counts always add back up to
    ``len(eligible_donors)`` -- the census is a partition of the pool the matching could use.
    """
    columns = ["distance_class", *HARD_CRITERIA]
    _require_columns(eligible_donors, columns, "eligible donors frame")
    counts = eligible_donors.groupby(columns, dropna=False).size()
    records = [{
        "distance_class": _cell_value(distance_class, as_flag=False),
        "has_active_escort": _cell_value(has_active_escort),
        "has_children_u14": _cell_value(has_children_u14),
        "has_car": _cell_value(has_car),
        "n_donors": int(n_donors),
    } for (distance_class, has_active_escort, has_children_u14, has_car), n_donors
        in counts.items()]
    # str() on every key first: a cell whose value is the bool True and one whose value is the
    # string "unknown" are not comparable, so sorting the raw mixture would raise.
    records.sort(key=lambda record: (str(record["distance_class"]),
                                     str(record["has_active_escort"]),
                                     str(record["has_children_u14"]), str(record["has_car"])))
    return records


def _matched_cell_size_by_level(matched_cell_sizes: dict) -> dict:
    """Distribution of the realised donor-cell size, per coarsening level (issue #378).

    The realised half of ADR-0104 check 4: how many donors the person's day was actually drawn
    from. It is NOT recoverable from :func:`donor_pool_size_by_hard_cell`, because the cell a
    person is served from is narrowed further by the soft criteria still in force at their level
    and by their own education-anchor exclusion (ruling R7).

    Keys are the level as a STRING (JSON object keys are strings, so an int-keyed dict would not
    survive the round trip into ``state_diagnostics.json`` unchanged), and a level nobody matched
    at is OMITTED -- a dense zero row carries no distribution and would only dilute the summary.
    The dense per-level count stays ``matched_by_level``.
    """
    summary = {}
    for level in sorted(matched_cell_sizes):
        sizes = matched_cell_sizes[level]
        if not sizes:
            continue
        array = np.asarray(sizes, dtype=float)
        summary[str(level)] = {
            "n_persons": int(array.size),
            "min": int(array.min()),
            "p25": float(np.percentile(array, 25)),
            "median": float(np.percentile(array, 50)),
            "p75": float(np.percentile(array, 75)),
            "max": int(array.max()),
            # A cell of exactly one donor means that donor's day was the ONLY day on offer: the
            # draw had no freedom at all, which is the sparsity signal check 4 is asking after.
            "n_persons_single_donor_cell": int((array == 1).sum()),
        }
    return summary


def match_home_office_donors(persons_home: pd.DataFrame, donors: pd.DataFrame,
                             rng: np.random.RandomState, *, minimum_cell: int = 1
                             ) -> tuple[pd.DataFrame, dict]:
    """Match every ``home``-state person to one donor from the home-office donor pool.

    ``persons_home`` -- one row per person drawn to ``home`` (``state.draw_states``): needs
    ``person_id``, ``assigned_distance_class``, ``sex``, ``age_class``, ``household_size``,
    ``has_active_escort``, ``has_children_u14``, ``has_car``,
    :data:`PERSON_EDUCATION_LOCATION_COLUMN`, and optionally ``has_license``.
    ``donors`` -- the donor pool's attributes frame (``donor_pool.build_home_office_donor_pool``):
    needs ``donor_id``, ``distance_class``, ``sex``, ``age_class``, ``household_size``,
    ``has_active_escort``, ``has_children_u14``, ``has_car``,
    :data:`DONOR_EDUCATION_LEG_COLUMN`, and optionally ``has_license``.

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
    ENTIRE pass up front -- see the module docstring),
    ``n_persons_hard_criteria_missing`` (``persons_home`` rows with a ``NaN`` in ANY of
    :data:`HARD_CRITERIA` -- such a person can never match any donor, since ``NaN`` never equals
    a donor's exact value, and would otherwise vanish into ``n_not_replaceable`` unexplained),
    ``n_donors_with_education_leg`` and ``n_persons_without_education_location`` (the two sides of
    the ruling-R7 criterion) and ``n_persons_education_restricted`` (persons for whom that
    exclusion actually removed at least one donor they would otherwise have been eligible for --
    NOT merely the persons the rule was evaluated for, a number that would grow with the cohort
    and say nothing about the rule's effect).

    The fourth ADR-0104 check-4 diagnostic (issue #378) is the pair
    ``donor_pool_size_by_hard_cell`` (see :func:`donor_pool_size_by_hard_cell` -- what the POOL
    holds per cell, including cells nobody was drawn from) and ``matched_cell_size_by_level``
    (see :func:`_matched_cell_size_by_level` -- how many donors each matched person's day was
    actually drawn from), plus the headline ``n_persons_matched_from_single_donor_cell``. Neither
    replaces the other: the census cannot see the soft criteria and the ruling-R7 exclusion that
    narrow a person's cell further, and the realised sizes cannot see a cell no person reached.
    """
    _require_columns(persons_home, ("person_id", "assigned_distance_class", "sex", "age_class",
                                    "household_size", "has_active_escort", "has_children_u14",
                                    "has_car", PERSON_EDUCATION_LOCATION_COLUMN),
                     "persons_home frame")
    _require_columns(donors, ("donor_id", "distance_class", "sex", "age_class", "household_size",
                              "has_active_escort", "has_children_u14", "has_car",
                              DONOR_EDUCATION_LEG_COLUMN), "donors frame")

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
    # Ruling R7. An UNRESOLVED flag on either side is read as the restrictive value (the donor
    # may carry an education leg / the person may have no education location), never as the
    # permissive one: an unknown must not be able to reproduce the very crash this criterion
    # exists to prevent. Both cases are counted below.
    donor_education_leg = (eligible_donors[DONOR_EDUCATION_LEG_COLUMN]
                           .fillna(True).astype(bool).to_numpy())
    n_donors_with_education_leg = int(donor_education_leg.sum())
    n_donor_education_leg_unknown = int(eligible_donors[DONOR_EDUCATION_LEG_COLUMN].isna().sum())
    person_education_location = (persons_home[PERSON_EDUCATION_LOCATION_COLUMN]
                                 .fillna(False).astype(bool))
    n_persons_without_education_location = int((~person_education_location).sum())
    n_person_education_location_unknown = int(
        persons_home[PERSON_EDUCATION_LOCATION_COLUMN].isna().sum())
    persons_home[PERSON_EDUCATION_LOCATION_COLUMN] = person_education_location
    if n_donor_education_leg_unknown or n_person_education_location_unknown:
        logger.warning(
            "%s ruling R7: %d donor(s) have an unresolved %r (read as 'has an education leg') "
            "and %d person(s) an unresolved %r (read as 'has no education location') -- the "
            "restrictive reading in both cases; check the upstream attribute sources.",
            _LOG_TAG, n_donor_education_leg_unknown, DONOR_EDUCATION_LEG_COLUMN,
            n_person_education_location_unknown, PERSON_EDUCATION_LOCATION_COLUMN)
    logger.info(
        "%s ruling R7 (education anchor): %d/%d donors carry an education leg; %d/%d persons "
        "have NO education location and can therefore never receive one of them",
        _LOG_TAG, n_donors_with_education_leg, len(eligible_donors),
        n_persons_without_education_location, len(persons_home))

    matches = []
    matched_by_level = {level: 0 for level in range(MAX_COARSENING_LEVEL + 1)}
    #: Realised cell sizes per level (issue #378), filled in the loop below; a level nobody
    #: matched at stays empty and is omitted from the summary, so the distribution is never
    #: diluted by zero rows. ``matched_by_level`` above remains the DENSE count.
    matched_cell_sizes = {level: [] for level in range(MAX_COARSENING_LEVEL + 1)}
    n_not_replaceable = 0

    n_persons_education_restricted = 0
    for person in persons_home.itertuples(index=False):
        hard_mask = ((donor_escort == person.has_active_escort)
                    & (donor_children == person.has_children_u14)
                    & (donor_car == person.has_car))
        # Ruling R7, a hard criterion at EVERY coarsening level: a person without an education
        # location can never anchor a transplanted education activity, so every donor carrying
        # one is excluded for them (a donor without one stays eligible for everybody). Counted
        # only when the exclusion actually removed a donor this person could otherwise have had:
        # a count of "persons the rule was evaluated for" would rise with the cohort size and say
        # nothing about the rule's effect.
        if not getattr(person, PERSON_EDUCATION_LOCATION_COLUMN):
            n_excluded = int((hard_mask & donor_education_leg).sum())
            if n_excluded > 0:
                hard_mask &= ~donor_education_leg
                n_persons_education_restricted += 1

        chosen_donor = None
        chosen_level = None
        chosen_cell_size = None
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
                # The size of the cell the day was actually drawn from (issue #378): the number
                # of donors that were eligible, NOT the one that happened to be drawn. It is
                # recorded here rather than recomputed afterwards because the mask that produced
                # it also carries this person's own education-anchor exclusion (ruling R7), which
                # a later per-cell census could not reproduce.
                chosen_cell_size = count
                break

        if chosen_donor is None:
            n_not_replaceable += 1
        else:
            matches.append((person.person_id, chosen_donor, chosen_level))
            matched_by_level[chosen_level] += 1
            matched_cell_sizes[chosen_level].append(chosen_cell_size)

    n_persons = len(persons_home)
    share_not_replaceable = n_not_replaceable / max(n_persons, 1)
    n_single_donor_cell = sum(1 for sizes in matched_cell_sizes.values()
                              for size in sizes if size == 1)

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
        "n_donors_with_education_leg": n_donors_with_education_leg,
        "n_persons_without_education_location": n_persons_without_education_location,
        "n_persons_education_restricted": n_persons_education_restricted,
        "donor_pool_size_by_hard_cell": donor_pool_size_by_hard_cell(eligible_donors),
        "matched_cell_size_by_level": _matched_cell_size_by_level(matched_cell_sizes),
        "n_persons_matched_from_single_donor_cell": n_single_donor_cell,
    }

    logger.info(
        "%s matched %d/%d persons (%.1f%% not replaceable); by coarsening level (rate of "
        "matched persons): %s", _LOG_TAG, n_persons - n_not_replaceable, n_persons,
        100.0 * share_not_replaceable,
        {level: f"{count}/{n_persons} ({100.0 * count / max(n_persons, 1):.1f}%)"
         for level, count in matched_by_level.items() if count > 0})
    n_matched = n_persons - n_not_replaceable
    logger.info(
        "%s donor-cell size (issue #378, ADR-0104 check 4): %d occupied hard cells over %d "
        "eligible donors; realised cell size per level (median [min-max]): %s; %d/%d matched "
        "persons (%.1f%%) were drawn from a cell of a SINGLE donor",
        _LOG_TAG, len(diagnostics["donor_pool_size_by_hard_cell"]), len(eligible_donors),
        {level: f"{cell['median']:.0f} [{cell['min']}-{cell['max']}]"
         for level, cell in diagnostics["matched_cell_size_by_level"].items()},
        n_single_donor_cell, n_matched, 100.0 * n_single_donor_cell / max(n_matched, 1))
    if share_not_replaceable > 0.5:
        logger.warning(
            "%s %d/%d persons (%.1f%%) are NOT replaceable by any donor at any coarsening level "
            "-- above 50%%, which usually signals a donor pool that is too small or too narrow "
            "for this population rather than a genuinely sparse match.",
            _LOG_TAG, n_not_replaceable, n_persons, 100.0 * share_not_replaceable)

    return result, diagnostics
