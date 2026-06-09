"""Map MiD Wege (trips) onto the synthetic persons -> eqasim activity chains.

Each synthetic popsim_mid person is a copy of a MiD donor person ``(H_ID, P_ID)``;
the donor's MiD Wege (trips) become that person's trip chain. This module maps the
MiD trip purpose and mode to the eqasim vocabulary and joins the donor Wege onto
the synthetic persons. Codes are grounded in the MiD 2023 codebook (Wege sheet),
documented inline, not invented.

The activity-chain construction proper (building the home/work/... activity
sequence with times and coordinates between consecutive trips) builds on the
trip records produced here.
"""

from __future__ import annotations

import logging

import pandas as pd

from data.hts import hts

logger = logging.getLogger(__name__)

# MiD W_ZWECK (Wegezweck) -> eqasim activity type at the trip destination.
# 1 Arbeit, 2 dienstlich -> work; 3 Ausbildung/Schule, 11 Schule, 12 Kita -> education;
# 4 Einkauf -> shop; 7 Freizeit -> leisure; 8 nach Hause, 9 Rueckweg -> home;
# 5 private Erledigungen, 6 Bringen/Holen, 10 anderer Zweck -> other.
PURPOSE_BY_W_ZWECK = {
    1: "work",
    2: "work",
    3: "education",
    4: "shop",
    5: "other",
    6: "other",
    7: "leisure",
    8: "home",
    9: "home",
    10: "other",
    11: "education",
    12: "education",
}
DEFAULT_PURPOSE = "other"

# MiD hvm (Hauptverkehrsmittel) -> eqasim canonical mode.
# 1 zu Fuss -> walk; 2 Fahrrad -> bicycle (canonical eqasim mode, not "bike");
# 3 MIV-Mitfahrer -> car_passenger; 4 MIV-Fahrer -> car; 5 OEPV -> pt;
# 9 keine Angabe -> walk (conservative fallback).
MODE_BY_HVM = {
    1: "walk",
    2: "bicycle",
    3: "car_passenger",
    4: "car",
    5: "pt",
    9: "walk",
}
DEFAULT_MODE = "walk"


def map_purpose(wege: pd.DataFrame, *, zweck_col: str = "W_ZWECK") -> pd.DataFrame:
    """Add the eqasim activity ``purpose`` from MiD ``W_ZWECK``."""
    out = wege.copy()
    out["purpose"] = out[zweck_col].map(PURPOSE_BY_W_ZWECK).fillna(DEFAULT_PURPOSE)
    return out


def map_mode(wege: pd.DataFrame, *, hvm_col: str = "hvm") -> pd.DataFrame:
    """Add the eqasim ``mode`` from MiD ``hvm``."""
    out = wege.copy()
    out["mode"] = out[hvm_col].map(MODE_BY_HVM).fillna(DEFAULT_MODE)
    return out


def mid_time_seconds(wege: pd.DataFrame, hour_col: str, minute_col: str) -> pd.Series:
    """Seconds since midnight from MiD hour + minute columns."""
    return wege[hour_col].astype(float) * 3600.0 + wege[minute_col].astype(float) * 60.0


# Ordered tuple of columns that constitute the eqasim trip schema subset produced by
# build_trip_table.  Downstream stages (data/hts/hts.py fix/validate and
# synthesis/population/activities.py) expect exactly these columns; all other MiD
# Wege columns are carried through as extras.
EQASIM_TRIP_COLUMNS = (
    "person_id",
    "trip_id",
    "departure_time",
    "arrival_time",
    "trip_duration",
    "activity_duration",
    "preceding_purpose",
    "following_purpose",
    "is_first_trip",
    "is_last_trip",
    "mode",
)


def build_trip_table(
    persons: pd.DataFrame,
    mid_wege: pd.DataFrame,
    *,
    household_col: str = "H_ID",
    person_col: str = "P_ID",
    trip_col: str = "W_ID",
) -> pd.DataFrame:
    """Map MiD Wege onto synthetic persons into the eqasim trip schema (+ extras).

    Mirrors ``data/hts/entd/cleaned.py`` exactly, reusing the shared helpers from
    ``data/hts/hts.py`` in the same order as the ENTD path:

    1. ``expand_persons_to_trips`` — join donor Wege onto synthetic persons, map
       purpose and mode, produce a string ``trip_key`` (``<person_id>_<W_ID>``) for
       traceability.
    2. Sort by ``(person_id, trip_col)``; assign an integer global ``trip_id``
       (0..n-1) so that ``hts.compute_first_last`` sorts trips correctly within
       each person.
    3. ``hts.compute_first_last`` — sorts by ``(person_id, trip_id)`` and sets
       ``is_first_trip`` / ``is_last_trip``.
    4. ``preceding_purpose``: per-person shift of ``following_purpose``.
       **ASSUMPTION**: MiD travel diaries start at home, so the first trip's
       ``preceding_purpose`` is hard-set to ``"home"``.  This is the standard
       diary-starts-at-home convention used throughout eqasim.  A log message
       reports the COUNT of first trips this assumption is applied to (the
       magnitude), and explicitly does NOT report a destination-based percentage
       because a first trip almost never has home as its destination — such a
       figure would look like validation while checking nothing about the
       origin.
    5. ``departure_time`` / ``arrival_time`` in float seconds since midnight via
       ``mid_time_seconds``.
    6. ``hts.fix_trip_times`` — repairs negative durations (swap / +24 h midnight
       crossing) and overlapping trips; essential for MiD diaries crossing midnight.
    7. ``trip_duration = arrival_time - departure_time``; ``hts.compute_activity_duration``
       (NaN on last trip of each person).
    8. ``hts.fix_activity_types`` — enforces ``following_purpose[i] == preceding_purpose[i+1]``.
    9. Integer per-person ``trip_index`` = 0-based cumcount (the column consumed by
       ``synthesis/population/activities.py``).

    Produces one row per (synthetic person, MiD trip) with the columns listed in
    ``EQASIM_TRIP_COLUMNS`` (plus ``trip_key``, ``trip_index``, and all original
    MiD Wege columns) so that the eqasim trip-time fix/validation layer and
    activity-chain construction apply unchanged to popsim_mid trips.

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id`` + donor keys ``H_ID`` / ``P_ID``.
        One row per unique synthetic person is expected; duplicates on
        ``person_id`` are dropped before the join so that each unique synthetic
        person gets exactly one copy of the donor trip chain (avoids a
        person x wege cross-join that would produce duplicate trip_key values).
    mid_wege:
        MiD Wege keyed by ``(H_ID, P_ID)``.  All columns are preserved.
    household_col:
        Name of the household-ID column shared by ``persons`` and ``mid_wege``.
    person_col:
        Name of the within-household person-ID column shared by both frames.
    trip_col:
        Name of the within-person trip-sequence column in ``mid_wege`` (used to
        build the unique ``trip_key`` and to sort trips within each person).

    Returns
    -------
    pd.DataFrame
        One row per (synthetic person, MiD trip) sorted by ``(person_id, trip_col)``,
        containing the full eqasim trip schema (see ``EQASIM_TRIP_COLUMNS``) plus
        ``trip_key`` (string traceability id), ``trip_index`` (per-person 0-based
        integer for activities.py), and all original MiD Wege columns.
    """
    # One trip chain per unique synthetic person; avoids a person x wege cross-join
    # that would produce duplicate trip_key values when the caller passes a persons
    # frame that has already been exploded (e.g. one row per household member).
    persons = persons.drop_duplicates(subset="person_id")

    # Step 1: join donor Wege, map purpose and mode.
    # expand_persons_to_trips produces a string trip_id (<person_id>_<W_ID>)
    # which we rename to trip_key for traceability; a global integer trip_id is
    # assigned below so hts.compute_first_last sorts correctly.
    df = expand_persons_to_trips(
        persons,
        mid_wege,
        household_col=household_col,
        person_col=person_col,
        trip_col=trip_col,
    )

    # Step 2: sort by (person_id, trip_col); assign integer trip_id (0..n-1).
    df = df.sort_values(["person_id", trip_col]).reset_index(drop=True)
    df = df.rename(columns={"trip_id": "trip_key"})
    df["trip_id"] = range(len(df))

    # Step 3: hts.compute_first_last returns a (re-sorted) DataFrame with
    # is_first_trip / is_last_trip set.  It sorts by (person_id, trip_id), which
    # is correct because trip_id is now a global integer reflecting within-person
    # order from the sort above.
    df = hts.compute_first_last(df)

    # Step 4: purpose columns.
    # following_purpose = destination activity mapped from W_ZWECK.
    df["following_purpose"] = df["purpose"]
    # preceding_purpose = destination of the previous trip within the same person.
    df["preceding_purpose"] = df.groupby("person_id")["following_purpose"].shift(1)
    # ASSUMPTION: MiD travel diaries start at home (diary-starts-at-home convention).
    # The first trip of each person therefore departs from home regardless of what
    # W_ZWECK recorded.  This is standard eqasim behaviour (mirrors entd/cleaned.py).
    df.loc[df["is_first_trip"], "preceding_purpose"] = "home"

    # Make the home-start ASSUMPTION observable (no silent assumption). MiD records
    # no per-trip origin purpose (only the destination W_ZWECK), so the first trip's
    # origin CANNOT be validated from the data; we apply the diary-starts-at-home
    # convention to every person's first trip. Log the magnitude (how many first
    # trips this touches). The complementary, data-checkable quantity is the home-END
    # closure repair rate, logged by PlanValidator (the day's end IS in the data via
    # the W_ZWECK home codes 8/9). We deliberately do NOT report a destination-based
    # percentage here: a first trip's destination is almost never home, so such a
    # number would look like validation while checking nothing about the origin.
    n_first_trips = int(df["is_first_trip"].sum())
    logger.info(
        "[popsim.trips] home-start assumption applied to %d first trips "
        "(MiD has no per-trip origin purpose; diary-starts-at-home convention, "
        "mirrors entd/cleaned.py). Home-END closure is checked/repaired by PlanValidator.",
        n_first_trips,
    )

    # Step 5: trip times in seconds since midnight.
    df["departure_time"] = mid_time_seconds(df, "W_SZS", "W_SZM").to_numpy()
    df["arrival_time"] = mid_time_seconds(df, "W_AZS", "W_AZM").to_numpy()

    # Step 6: fix_trip_times repairs negative durations (swap / +24h midnight
    # crossing) and overlapping trips — essential for MiD diaries crossing midnight.
    # The function mutates df in place and also returns it.
    df = hts.fix_trip_times(df)

    # Step 7: trip_duration and activity_duration (NaN on last trip of each person).
    df["trip_duration"] = df["arrival_time"] - df["departure_time"]
    hts.compute_activity_duration(df)

    # Step 8: fix_activity_types enforces following_purpose[i] == preceding_purpose[i+1].
    # Mutates df in place, returns None.
    hts.fix_activity_types(df)

    # Step 9: per-person 0-based trip_index consumed by synthesis/population/activities.py.
    df["trip_index"] = df.groupby("person_id").cumcount()

    return df


def expand_persons_to_trips(
    persons: pd.DataFrame,
    mid_wege: pd.DataFrame,
    *,
    household_col: str = "H_ID",
    person_col: str = "P_ID",
    trip_col: str = "W_ID",
) -> pd.DataFrame:
    """Join the donor MiD Wege onto the synthetic persons -> one row per trip.

    Each synthetic person (``person_id``, referencing donor ``(H_ID, P_ID)``) gets
    the donor person's trips, with the purpose and mode mapped to the eqasim
    vocabulary and a unique ``trip_id`` (``<person_id>_<W_ID>``). Persons whose
    donor has no Wege are dropped (they make no trips).

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id`` + donor keys ``H_ID`` / ``P_ID``.
    mid_wege:
        MiD Wege keyed by ``(H_ID, P_ID)``.
    """
    wege = map_mode(map_purpose(mid_wege))
    merged = persons.merge(
        wege, on=[household_col, person_col], how="inner", suffixes=("", "_weg")
    )
    merged["trip_id"] = (
        merged["person_id"].astype(str) + "_" + merged[trip_col].astype(str)
    )
    return merged.reset_index(drop=True)


def build_validated_trip_table(
    persons: pd.DataFrame,
    mid_wege: pd.DataFrame,
    *,
    require_home_closure: bool = True,
    repair: bool = True,
    **kwargs,
):
    """Build the trip table, optionally repair, and return (table, ValidationReport).

    Thin convenience wrapper over build_trip_table + PlanValidator. When repair is
    True (default) the PlanValidator enforces home-end closure and logs its repair
    rates (the rates are emitted by repair_trips itself, so they remain observable
    even though the RepairReport is not returned here). The returned ValidationReport
    reflects the FINAL (post-repair) table.

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id`` + donor keys ``H_ID`` / ``P_ID``.
    mid_wege:
        MiD Wege keyed by ``(H_ID, P_ID)``.
    require_home_closure:
        If True (default) the validator enforces home-end closure.
    repair:
        If True (default) repair fixable issues in the trip table before validation.
    **kwargs:
        Passed to build_trip_table (e.g., household_col, person_col, trip_col).

    Returns
    -------
    tuple[pd.DataFrame, ValidationReport]
        The built (and optionally repaired) trip table and the validation report
        reflecting the final state.
    """
    from braunschweig.popsim.plan_validation import PlanValidator

    table = build_trip_table(persons, mid_wege, **kwargs)
    validator = PlanValidator(require_home_closure=require_home_closure)
    if repair:
        table, _ = validator.repair_trips(table)
    report = validator.validate_trips(table)
    return table, report
