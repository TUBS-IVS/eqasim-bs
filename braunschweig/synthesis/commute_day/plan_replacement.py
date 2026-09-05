"""Reporting-day plan replacement (ADR-0104, issue #244, Phase B Task 3).

Turns a ``commute_day_state`` draw (:mod:`state`) and a donor match
(:mod:`matching`) into the actual reporting-day trips table, one row per (person, trip) in the
``synthesis.population.trips`` CONTRACT (:data:`braunschweig.popsim.trips_stage.CONTRACT`). Pure
module: no file I/O, no synpp stage -- exercised only against tiny synthetic frames in
``tests/test_commute_day_plan_replacement.py``.

Per person, by ``commute_day_state``:

* ``absent`` -- NO rows at all (the person makes no trips on the reporting day).
* ``home`` WITH a donor match -- every one of the person's original trip rows is dropped and
  replaced by the donor's own trip chain (:func:`donor_pool.donor_trips`), with ``person_id`` set
  to the RECEIVING person, ``trip_index`` renumbered ``0..n-1`` in the donor's own order, and
  ``is_first_trip`` / ``is_last_trip`` recomputed from that renumbering. Any column the input
  ``trips`` table carries beyond the CONTRACT plus ``euclidean_distance`` and ``trip_key`` (raw
  MiD extras the donor pool intentionally does not carry, see ``donor_pool.donor_trips``) is set
  to ``NaN`` on the replaced rows -- there is no donor-side value to copy, and inventing one would
  violate CLAUDE.md's ban on invented data. The per-person departure-time jitter
  (:func:`braunschweig.popsim.trips_stage.apply_per_person_jitter`) is applied EXACTLY ONCE,
  across all replaced rows together, keyed by the RECEIVING person_id (ruling R2) -- the donor's
  own chain was built by :func:`donor_pool.donor_trips` WITHOUT that jitter for precisely this
  reason (applying it twice would double-jitter the same donor day once copied onto a person).
* ``home`` WITHOUT a donor match (:func:`matching.match_home_office_donors` found no replaceable
  cell) -- the person's original rows are kept UNCHANGED and counted in
  ``diagnostics["n_home_unmatched"]``; ADR-0104 leaves it to the state stage (a later Phase B
  task) to decide how to treat them (downgrading to ``at_workplace`` is the documented default).
* ``at_workplace``, or any person absent from ``states`` altogether (e.g. a non-worker who was
  never given a commute-day state) -- rows UNCHANGED.

The output is sorted by ``(person_id, trip_index)` with the CONTRACT columns first, followed by
the input ``trips`` table's remaining columns in their original relative order.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim.trips_stage import CONTRACT, apply_per_person_jitter

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day plan replacement]"

#: Extra columns a donor trips frame is documented to carry beyond the CONTRACT (see
#: ``donor_pool.donor_trips``): copied verbatim onto replaced rows, never nulled.
_DONOR_EXTRA_COLUMNS = ("euclidean_distance", "trip_key")

STATE_ABSENT = "absent"
STATE_HOME = "home"
STATE_AT_WORKPLACE = "at_workplace"


def _require_columns(frame: pd.DataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what} is missing the required column(s) {missing} "
                         f"(present: {sorted(frame.columns)})")


def build_day_trips(trips: pd.DataFrame, states: pd.DataFrame, matches: pd.DataFrame,
                    donor_trips: pd.DataFrame, *, random_seed: int) -> tuple[pd.DataFrame, dict]:
    """Build the reporting-day trips table from a state draw and a donor match.

    ``trips`` -- the pre-assignment ``synthesis.population.trips`` table (CONTRACT columns plus
    any extras), one row per (person, trip), for the WHOLE population (workers and non-workers
    alike -- persons absent from ``states`` pass through unchanged, see the module docstring).
    ``states`` -- :func:`state.draw_states` output: needs ``person_id``, ``commute_day_state``.
    ``matches`` -- :func:`matching.match_home_office_donors` output: needs ``person_id``,
    ``donor_id`` (one row per REPLACEABLE ``home`` person; an unreplaceable one is simply
    absent). ``donor_trips`` -- :func:`donor_pool.donor_trips` output: needs ``donor_id`` plus
    the CONTRACT columns (minus ``person_id``) and ``euclidean_distance`` / ``trip_key``.

    Returns ``(day_trips, diagnostics)``. ``diagnostics``: ``n_persons_replaced`` (``home``
    persons with a donor match -- including a donor whose own day has ZERO trips, i.e. a fully
    immobile home-office day: that person legitimately ends up with no rows, which is the
    correct outcome, not an error), ``n_persons_absent``, ``n_trips_removed`` (original rows
    dropped, for both replaced and absent persons), ``n_trips_added`` (donor rows spliced in),
    ``n_home_unmatched``, ``n_extra_columns_nulled`` (count of DISTINCT extra input columns
    nulled on replaced rows, see the module docstring).
    """
    _require_columns(trips, ("person_id", "trip_index"), "trips frame")
    _require_columns(states, ("person_id", "commute_day_state"), "states frame")
    _require_columns(matches, ("person_id", "donor_id"), "matches frame")
    _require_columns(donor_trips, ("donor_id",) + tuple(c for c in CONTRACT if c != "person_id"),
                     "donor_trips frame")

    state_by_person = states.set_index("person_id")["commute_day_state"]
    donor_by_person = matches.set_index("person_id")["donor_id"]

    input_columns = list(trips.columns)
    known_columns = set(CONTRACT) | set(_DONOR_EXTRA_COLUMNS)
    other_extra_columns = [c for c in input_columns if c not in known_columns]
    output_columns = list(CONTRACT) + [c for c in input_columns if c not in CONTRACT]

    absent_persons = set(state_by_person.index[state_by_person == STATE_ABSENT])
    home_persons = set(state_by_person.index[state_by_person == STATE_HOME])
    matched_home_persons = home_persons & set(donor_by_person.index)
    unmatched_home_persons = home_persons - matched_home_persons

    n_persons_absent = len(absent_persons)
    n_home_unmatched = len(unmatched_home_persons)

    # Rows dropped entirely: absent persons (no rows at all) and matched home persons (replaced
    # below). Everyone else -- at_workplace, unmatched-home, and persons never given a state at
    # all -- keeps their original rows completely unchanged.
    persons_to_remove = absent_persons | matched_home_persons
    kept_rows = trips.loc[~trips["person_id"].isin(persons_to_remove)].copy()

    n_trips_removed = int(trips["person_id"].isin(persons_to_remove).sum())

    donor_groups = {donor_id: group.sort_values("trip_index").reset_index(drop=True)
                    for donor_id, group in donor_trips.groupby("donor_id", sort=False)}

    replaced_frames = []
    for person_id in sorted(matched_home_persons):
        donor_id = donor_by_person.loc[person_id]
        donor_rows = donor_groups.get(donor_id)
        if donor_rows is None:
            # The donor has no rows at all in donor_trips (a fully immobile home-office day, see
            # donor_pool.donor_trips); the person legitimately ends up with zero trip rows too.
            continue
        new_rows = donor_rows.copy()
        new_rows["person_id"] = person_id
        n = len(new_rows)
        new_rows["trip_index"] = np.arange(n)
        new_rows["is_first_trip"] = new_rows["trip_index"] == 0
        new_rows["is_last_trip"] = new_rows["trip_index"] == max(n - 1, 0)
        for column in other_extra_columns:
            new_rows[column] = np.nan
        replaced_frames.append(new_rows)

    n_trips_added = sum(len(frame) for frame in replaced_frames)
    n_extra_columns_nulled = len(other_extra_columns) if replaced_frames else 0

    if replaced_frames:
        replaced = pd.concat(replaced_frames, ignore_index=True)
        replaced = replaced.sort_values(["person_id", "trip_index"]).reset_index(drop=True)
        # Ruling R2: the per-person jitter is applied EXACTLY ONCE here, on the replaced rows
        # only, keyed by the RECEIVING person_id -- never on the donor's own chain (donor_pool
        # deliberately omits it) and never on untouched rows.
        replaced = apply_per_person_jitter(replaced, random_seed)
    else:
        replaced = pd.DataFrame(columns=output_columns)

    result = pd.concat([kept_rows, replaced], ignore_index=True, sort=False)
    result = result[output_columns]
    result = result.sort_values(["person_id", "trip_index"]).reset_index(drop=True)

    diagnostics = {
        "n_persons_replaced": len(matched_home_persons),
        "n_persons_absent": n_persons_absent,
        "n_trips_removed": n_trips_removed,
        "n_trips_added": n_trips_added,
        "n_home_unmatched": n_home_unmatched,
        "n_extra_columns_nulled": n_extra_columns_nulled,
    }

    logger.info(
        "%s reporting-day trips built: %d persons replaced (+%d/-%d trips), %d persons absent "
        "(0 rows), %d home persons unmatched (kept unchanged, %d extra column(s) nulled on "
        "replaced rows)", _LOG_TAG, diagnostics["n_persons_replaced"], n_trips_added,
        n_trips_removed, n_persons_absent, n_home_unmatched, n_extra_columns_nulled)
    if n_home_unmatched > 0:
        logger.warning(
            "%s %d home person(s) had no donor match and keep their ORIGINAL (pre-home-office) "
            "day unchanged -- the state stage is expected to downgrade these to at_workplace.",
            _LOG_TAG, n_home_unmatched)

    return result, diagnostics
