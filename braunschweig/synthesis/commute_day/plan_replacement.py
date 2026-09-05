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
  ``is_first_trip`` / ``is_last_trip`` recomputed from that renumbering. ``trip_key`` is copied
  VERBATIM from the donor's own row: it therefore still names the DONOR, not the receiving
  person, and is no longer unique once a donor is reused across several persons -- it remains
  useful purely for tracing a replaced row back to its donor trip, never as a per-row identifier;
  no downstream code in this pipeline relies on it for anything but within-person ordering
  (``trip_index`` is authoritative for that). Any column the input
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

#: Share of matched donors with zero rows in ``donor_trips`` -- EXCLUDING the donors the donor
#: attributes report as immobile (``n_trips == 0``, ruling R9) -- above which the replacement
#: warns: what remains after that split can no longer be explained by the donor pool's own
#: immobility and points at a ``donor_id`` key/dtype mismatch.
WARN_DONORS_WITHOUT_TRIPS_SHARE = 0.01

STATE_ABSENT = "absent"
STATE_HOME = "home"
STATE_AT_WORKPLACE = "at_workplace"


def _require_columns(frame: pd.DataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what} is missing the required column(s) {missing} "
                         f"(present: {sorted(frame.columns)})")


def _replaced_rows(donor_blocks, person_ids, other_extra_columns):
    """Assemble every replaced person's donor block into ONE frame, without column-wise inserts.

    ``donor_blocks`` is a list of per-person donor trip frames (already ordered by
    ``trip_index``), ``person_ids`` the receiving person of each block, in the same order.

    The naive shape -- copy each donor block, then write ``person_id``, ``trip_index``,
    ``is_first_trip``, ``is_last_trip`` and every extra column into it one at a time -- performs
    ``len(donor_blocks) * (4 + len(other_extra_columns))`` single-column inserts. On the 100 %
    proof run of 2026-09-05 that was 657,888 inserts, each one emitting a pandas
    ``PerformanceWarning: DataFrame is highly fragmented``, which alone made the run log 254 MB.
    Here the blocks are concatenated ONCE, the four recomputed columns are written on the single
    combined frame (four inserts in total, not four per person), and the NaN extras are built as
    one block and joined with a single ``concat`` -- so the number of insert operations no longer
    grows with the number of replaced persons.
    """
    replaced = pd.concat(donor_blocks, ignore_index=True)
    lengths = np.fromiter((len(block) for block in donor_blocks), dtype=int,
                          count=len(donor_blocks))
    trip_index = np.concatenate([np.arange(length) for length in lengths]) if len(lengths) else \
        np.empty(0, dtype=int)
    # The LAST trip of each block, i.e. the block's own length - 1 repeated over the block; a
    # zero-length block contributes nothing, so max(n-1, 0) never has to be special-cased.
    last_index = np.repeat(np.maximum(lengths - 1, 0), lengths)
    replaced["person_id"] = np.repeat(np.asarray(person_ids), lengths)
    replaced["trip_index"] = trip_index
    replaced["is_first_trip"] = trip_index == 0
    replaced["is_last_trip"] = trip_index == last_index
    if other_extra_columns:
        # One NaN block for every extra column at once (see the docstring): there is no
        # donor-side value to copy and inventing one would violate CLAUDE.md's ban on invented
        # data, so they are nulled -- but nulled in a single operation.
        extras = pd.DataFrame(np.nan, index=replaced.index, columns=list(other_extra_columns))
        replaced = pd.concat([replaced, extras], axis=1)
    return replaced


def build_day_trips(trips: pd.DataFrame, states: pd.DataFrame, matches: pd.DataFrame,
                    donor_trips: pd.DataFrame, *, random_seed: int,
                    donor_attributes: pd.DataFrame = None) -> tuple[pd.DataFrame, dict]:
    """Build the reporting-day trips table from a state draw and a donor match.

    ``trips`` -- the pre-assignment ``synthesis.population.trips`` table (CONTRACT columns plus
    any extras), one row per (person, trip), for the WHOLE population (workers and non-workers
    alike -- persons absent from ``states`` pass through unchanged, see the module docstring).
    ``states`` -- :func:`state.draw_states` output: needs ``person_id``, ``commute_day_state``.
    ``matches`` -- :func:`matching.match_home_office_donors` output: needs ``person_id``,
    ``donor_id`` (one row per REPLACEABLE ``home`` person; an unreplaceable one is simply
    absent). ``donor_trips`` -- :func:`donor_pool.donor_trips` output: needs ``donor_id`` plus
    the CONTRACT columns (minus ``person_id``) and ``euclidean_distance`` / ``trip_key``.
    ``donor_attributes`` -- the donor pool's attributes frame (``donor_id``, ``n_trips``); OPTIONAL
    only so the pure-helper tests can call this function without one, and always passed by
    ``trips_day_stage``. Without it every donor absent from ``donor_trips`` is counted as
    ``n_donors_without_trips``, as before ruling R9.

    Returns ``(day_trips, diagnostics)``. ``diagnostics``: ``n_persons_replaced`` (``home``
    persons with a donor match -- including a donor whose own day has ZERO trips, i.e. a fully
    immobile home-office day: that person legitimately ends up with no rows, which is the
    correct outcome, not an error), ``n_persons_absent``, ``n_trips_removed`` (original rows
    dropped, for both replaced and absent persons), ``n_trips_added`` (donor rows spliced in),
    ``n_home_unmatched``, ``n_extra_columns_nulled`` (count of DISTINCT extra input columns
    nulled on replaced rows, see the module docstring), and -- ruling R9 -- the SPLIT of the
    matched persons whose ``donor_id`` has no rows at all in ``donor_trips``:

    * ``n_donors_immobile`` / ``share_donors_immobile`` -- the donor's own ``n_trips`` is 0, an
      immobile home-office day. EXPECTED (the MiD donor pool is 32.5 % immobile by construction),
      reported at info level, and the person's trip-less day is the correct outcome.
    * ``n_donors_without_trips`` / ``share_donors_without_trips`` -- the donor HAS trips (or is
      absent from ``donor_attributes``, i.e. unknown) yet none of them arrived here. That is the
      symptom of a ``donor_id`` key/dtype mismatch between ``matches`` and ``donor_trips``, which
      silently wipes every affected person's day, so this rate -- and only this one -- warns above
      :data:`WARN_DONORS_WITHOUT_TRIPS_SHARE`.
    * ``n_donors_unknown_trip_count`` -- how many of the latter were the "absent from
      ``donor_attributes``" case (always 0 when no attributes frame is given).

    Raises ``ValueError`` if ``matches["person_id"]`` contains duplicates -- each person can have
    at most one donor match; a duplicate would make the replacement for that person ambiguous.
    """
    _require_columns(trips, ("person_id", "trip_index"), "trips frame")
    _require_columns(states, ("person_id", "commute_day_state"), "states frame")
    _require_columns(matches, ("person_id", "donor_id"), "matches frame")
    _require_columns(donor_trips, ("donor_id",) + tuple(c for c in CONTRACT if c != "person_id"),
                     "donor_trips frame")
    n_duplicated_matches = int(matches["person_id"].duplicated().sum())
    if n_duplicated_matches > 0:
        raise ValueError(
            f"{_LOG_TAG} matches frame has {n_duplicated_matches} duplicate person_id value(s); "
            "each person must have at most one donor match "
            f"(duplicated: {sorted(matches.loc[matches['person_id'].duplicated(), 'person_id'].unique())}).")

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

    # Ruling R9: an absent donor is only an ERROR when that donor actually has trips. The donor
    # attributes carry n_trips (0 for an immobile home-office day, see
    # donor_pool.attach_trip_derived_attributes), so the two cases can be told apart instead of
    # being conflated into one warning -- the pool is 32.5 % immobile BY CONSTRUCTION, which made
    # the conflated rate (27.3 % on the 2026-09-05 proof run) unreadable as a defect signal.
    donor_trip_counts = None
    if donor_attributes is not None:
        _require_columns(donor_attributes, ("donor_id", "n_trips"), "donor_attributes frame")
        donor_trip_counts = donor_attributes.set_index("donor_id")["n_trips"]

    donor_blocks = []
    replaced_person_ids = []
    n_donors_without_trips = 0
    n_donors_immobile = 0
    n_donors_unknown_trip_count = 0
    for person_id in sorted(matched_home_persons):
        donor_id = donor_by_person.loc[person_id]
        donor_rows = donor_groups.get(donor_id)
        if donor_rows is None:
            # The donor has no rows at all in donor_trips. With the donor attributes at hand this
            # splits into the EXPECTED case (n_trips == 0: a fully immobile home-office day, so
            # the person legitimately gets a trip-less day) and the SUSPICIOUS one (n_trips > 0:
            # the donor has trips that did not arrive here, exactly what a donor_id key/dtype
            # mismatch between matches and donor_trips produces). A donor missing from the
            # attributes frame entirely is counted as unknown and treated as suspicious -- an
            # unknown must never be read as the benign case.
            n_trips_of_donor = None
            if donor_trip_counts is not None:
                raw_n_trips = donor_trip_counts.get(donor_id)
                if raw_n_trips is None or pd.isna(raw_n_trips):
                    n_donors_unknown_trip_count += 1
                else:
                    n_trips_of_donor = int(raw_n_trips)
            if n_trips_of_donor == 0:
                n_donors_immobile += 1
            else:
                n_donors_without_trips += 1
            continue
        donor_blocks.append(donor_rows)
        replaced_person_ids.append(person_id)

    n_trips_added = sum(len(block) for block in donor_blocks)
    n_extra_columns_nulled = len(other_extra_columns) if donor_blocks else 0

    if donor_blocks:
        replaced = _replaced_rows(donor_blocks, replaced_person_ids, other_extra_columns)
        replaced = replaced.sort_values(["person_id", "trip_index"]).reset_index(drop=True)
        # Ruling R2: the per-person jitter is applied EXACTLY ONCE here, on the replaced rows
        # only, keyed by the RECEIVING person_id -- never on the donor's own chain (donor_pool
        # deliberately omits it) and never on untouched rows.
        replaced = apply_per_person_jitter(replaced, random_seed)
        result = pd.concat([kept_rows, replaced], ignore_index=True, sort=False)
    else:
        # No rows to splice in at all (no home persons, no matches, or every matched donor was
        # trip-less) -- concatenating with an empty pd.DataFrame(columns=...) placeholder would
        # upcast every CONTRACT column (trip_index, is_first_trip, is_last_trip, trip_duration,
        # ...) to object, silently corrupting the dtypes of every UNTOUCHED row as a side effect.
        # kept_rows alone already carries every row and every original dtype, so it is used as-is.
        result = kept_rows

    result = result[output_columns]
    result = result.sort_values(["person_id", "trip_index"]).reset_index(drop=True)

    n_matched = len(matched_home_persons)
    share_donors_without_trips = n_donors_without_trips / max(n_matched, 1)
    share_donors_immobile = n_donors_immobile / max(n_matched, 1)
    diagnostics = {
        "n_persons_replaced": n_matched,
        "n_persons_absent": n_persons_absent,
        "n_trips_removed": n_trips_removed,
        "n_trips_added": n_trips_added,
        "n_home_unmatched": n_home_unmatched,
        "n_extra_columns_nulled": n_extra_columns_nulled,
        "n_donors_without_trips": n_donors_without_trips,
        "n_donors_immobile": n_donors_immobile,
        "n_donors_unknown_trip_count": n_donors_unknown_trip_count,
        "share_donors_without_trips": float(share_donors_without_trips),
        "share_donors_immobile": float(share_donors_immobile),
    }

    logger.info(
        "%s reporting-day trips built: %d persons replaced (+%d/-%d trips), %d/%d matched donors "
        "were IMMOBILE (%.1f%%, n_trips == 0, an expected trip-less home-office day) and %d/%d "
        "(%.1f%%) had zero rows although their donor has trips; %d persons absent (0 rows), %d "
        "home persons unmatched (kept unchanged, %d extra column(s) nulled on replaced rows)",
        _LOG_TAG, n_matched, n_trips_added, n_trips_removed,
        n_donors_immobile, n_matched, 100.0 * share_donors_immobile,
        n_donors_without_trips, n_matched, 100.0 * share_donors_without_trips,
        n_persons_absent, n_home_unmatched, n_extra_columns_nulled)
    if n_donors_unknown_trip_count > 0:
        logger.warning(
            "%s %d matched donor(s) with no rows in donor_trips are ALSO absent from the donor "
            "attributes frame, so their trip count is unknown; they are counted as "
            "n_donors_without_trips (the suspicious case), never as immobile.",
            _LOG_TAG, n_donors_unknown_trip_count)
    if n_home_unmatched > 0:
        logger.warning(
            "%s %d home person(s) had no donor match and keep their ORIGINAL (pre-home-office) "
            "day unchanged -- the state stage is expected to downgrade these to at_workplace.",
            _LOG_TAG, n_home_unmatched)
    if share_donors_without_trips > WARN_DONORS_WITHOUT_TRIPS_SHARE:
        logger.warning(
            "%s %d/%d matched donors (%.1f%%) have ZERO rows in donor_trips although their donor "
            "attributes report trips (or are missing altogether) -- above %.0f%%, which usually "
            "signals a donor_id key or dtype mismatch between matches and donor_trips (silently "
            "wiping every affected replaced person's day). Donors that are genuinely immobile "
            "(n_trips == 0) are NOT counted here; they are reported as n_donors_immobile.",
            _LOG_TAG, n_donors_without_trips, n_matched, 100.0 * share_donors_without_trips,
            100.0 * WARN_DONORS_WITHOUT_TRIPS_SHARE)

    return result, diagnostics
