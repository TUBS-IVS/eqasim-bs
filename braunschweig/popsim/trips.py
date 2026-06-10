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

# MiD hvm_imp (imputed Hauptverkehrsmittel; handbook Kap. 4.2 mandates the
# imputed variant) -> eqasim canonical mode. hvm_imp is fully imputed (codes
# 1..5 only); any other code is a data/contract error and raises.
# 1 zu Fuss -> walk; 2 Fahrrad -> bicycle (canonical eqasim mode, not "bike");
# 3 MIV-Mitfahrer -> car_passenger; 4 MIV-Fahrer -> car; 5 OEPV -> pt.
MODE_BY_HVM = {
    1: "walk",
    2: "bicycle",
    3: "car_passenger",
    4: "car",
    5: "pt",
}


def map_purpose(wege: pd.DataFrame, *, zweck_col: str = "W_ZWECK") -> pd.DataFrame:
    """Add the eqasim activity ``purpose`` from MiD ``W_ZWECK``."""
    out = wege.copy()
    out["purpose"] = out[zweck_col].map(PURPOSE_BY_W_ZWECK).fillna(DEFAULT_PURPOSE)
    return out


def map_mode(wege: pd.DataFrame, *, hvm_col: str = "hvm_imp") -> pd.DataFrame:
    """Add the eqasim ``mode`` from MiD imputed main mode ``hvm_imp``.

    Raises on any unmapped code (no silent walk fallback)."""
    out = wege.copy()
    mapped = out[hvm_col].map(MODE_BY_HVM)
    if mapped.isna().any():
        bad = out.loc[mapped.isna(), hvm_col].value_counts().to_dict()
        raise ValueError(f"[popsim.trips] unmapped {hvm_col} codes: {bad}")
    out["mode"] = mapped
    return out


def mid_time_seconds(wege: pd.DataFrame, hour_col: str, minute_col: str) -> pd.Series:
    """Seconds since midnight from MiD hour + minute columns.

    The MiD Wege time fields (W_SZS/W_SZM/W_AZS/W_AZM) carry missing/design
    codes OUTSIDE the valid clock range, audited against the raw data: 99
    ("keine Angabe", item non-response) and 701 ("bei regelmaessigen
    beruflichen Wegen nicht erhoben", design code for rbW summary records) in
    BOTH the hour and the minute fields. Any out-of-range value (hour not in
    0..23, minute not in 0..59) invalidates the time and returns NaN, so coded
    rows are NOT converted to multi-day timestamps that survive the downstream
    trip-time repairs; the owning person is then classified unfixable and
    replaced by the same-cell resample. A range check is used instead of an
    explicit code list because 9 is a VALID minute (and hour) — only values
    outside the clock range are codes.
    """
    hours = wege[hour_col].astype(float)
    minutes = wege[minute_col].astype(float)
    coded = (~hours.between(0, 23)) | (~minutes.between(0, 59))
    seconds = hours * 3600.0 + minutes * 60.0
    seconds[coded] = float("nan")
    return seconds


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

    # Member completion (braunschweig.popsim.member_completion): a filler person
    # carries a synthetic (host H_ID, fresh P_ID) pair that does NOT exist in the
    # MiD Wege file, so joining on it would silently give fillers no trips. The
    # total traceability columns source_H_ID / source_P_ID reference the MIRROR
    # donor for fillers and the own ids for regular persons, so using them as the
    # effective join keys gives fillers the mirror's Wege and leaves everyone
    # else unchanged. Frames without the columns (legacy path) join as before.
    if "source_H_ID" in persons.columns and "source_P_ID" in persons.columns:
        persons = persons.assign(**{household_col: persons["source_H_ID"],
                                    person_col: persons["source_P_ID"]})

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
    resample: bool = False,
    resample_cell_col: str | None = None,
    random_seed: int | None = None,
    **kwargs,
):
    """Build the trip table, optionally repair + resample, return (table, ValidationReport).

    Thin convenience wrapper over build_trip_table + PlanValidator. When repair is
    True (default) the PlanValidator enforces home-end closure and logs its repair
    rates (the rates are emitted by repair_trips itself, so they remain observable
    even though the RepairReport is not returned here). When ``resample`` is True,
    persons the repair classified as unfixable (e.g. NaN-time persons from the MiD
    coded times 99/701) have their whole chain replaced by a valid donor chain
    drawn from the SAME matching cell (``resample_cell_col``) via
    ``plan_validation.resample_chains``; persons without any same-cell donor fall
    back to a home-only plan (= no trips, the row is dropped) — the fallback rate
    is logged by resample_chains. The returned ValidationReport reflects the FINAL
    (post-repair, post-resample) table.

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id`` + donor keys ``H_ID`` / ``P_ID``
        (+ ``resample_cell_col`` when ``resample`` is True).
    mid_wege:
        MiD Wege keyed by ``(H_ID, P_ID)``.
    require_home_closure:
        If True (default) the validator enforces home-end closure.
    repair:
        If True (default) repair fixable issues in the trip table before validation.
    resample:
        If True, replace unfixable persons' chains with same-cell donor chains.
        Requires ``repair=True`` (the unfixable classification comes from the
        RepairReport) and a non-None ``random_seed`` (determinism is mandatory).
    resample_cell_col:
        Column in ``persons`` that defines the donor-matching cell (e.g.
        ``"ZENSUS100m"``).  ``None`` means no cell information is available:
        every unfixable person falls back to a home-only plan (logged loudly
        by resample_chains).
    random_seed:
        Seed for the resample RNG (``np.random.RandomState``).
    **kwargs:
        Passed to build_trip_table (e.g., household_col, person_col, trip_col).

    Returns
    -------
    tuple[pd.DataFrame, ValidationReport]
        The built (and optionally repaired/resampled) trip table and the
        validation report reflecting the final state.
    """
    from braunschweig.popsim.plan_validation import PlanValidator, resample_chains

    if resample and not repair:
        raise ValueError(
            "[popsim.trips] resample=True requires repair=True: the unfixable-person "
            "classification that drives the resample comes from the RepairReport."
        )
    if resample and random_seed is None:
        raise ValueError(
            "[popsim.trips] resample=True requires an explicit random_seed "
            "(deterministic donor draws are mandatory)."
        )
    if resample and resample_cell_col is not None and resample_cell_col not in persons.columns:
        raise ValueError(
            f"[popsim.trips] resample_cell_col '{resample_cell_col}' is not a column of "
            f"the persons frame (columns: {sorted(persons.columns)}). Pass None to "
            f"resample without cell matching (home-only fallback) or fix the column name."
        )

    table = build_trip_table(persons, mid_wege, **kwargs)
    validator = PlanValidator(require_home_closure=require_home_closure)
    repair_report = None
    if repair:
        table, repair_report = validator.repair_trips(table)

    if resample and repair_report is not None and repair_report.unfixable_persons:
        table = _resample_unfixable(
            table,
            persons,
            repair_report.unfixable_persons,
            resample_cell_col=resample_cell_col,
            random_seed=random_seed,
            resample_chains=resample_chains,
        )

    report = validator.validate_trips(table)
    return table, report


def _resample_unfixable(
    table: pd.DataFrame,
    persons: pd.DataFrame,
    unfixable_persons,
    *,
    resample_cell_col: str | None,
    random_seed: int,
    resample_chains,
) -> pd.DataFrame:
    """Replace unfixable persons' chains with same-cell donor chains; rebuild ids.

    Donor chains are the FINAL (post-repair) chains of the valid persons that
    live in the same ``resample_cell_col`` cell as an unfixable person; donor
    pools are only built for cells that actually contain unfixable persons (the
    full population can be ~1 M persons, so materialising every cell's pool
    would be wasteful).  Unfixable persons without any same-cell donor receive
    resample_chains' home-only fallback row (NaN times); those rows are dropped
    here — in the eqasim trips contract a stay-at-home person simply has no
    trips — and the drop count is logged.

    After the replacement, ``trip_id`` / ``is_first_trip`` / ``is_last_trip`` /
    ``trip_duration`` / ``activity_duration`` / ``trip_index`` are re-derived on
    the final sorted frame (same recompute sequence as
    ``PlanValidator.repair_trips`` step 4) because the donor chains carry the
    DONOR's ids, which would otherwise collide with the kept persons' rows.
    """
    import numpy as np

    from data.hts import hts

    # person -> cell mapping (one row per unique person, like build_trip_table).
    persons_unique = persons.drop_duplicates(subset="person_id")
    if resample_cell_col is not None:
        person_cells = dict(
            zip(persons_unique["person_id"], persons_unique[resample_cell_col])
        )
    else:
        person_cells = {}

    # Donor pools: valid persons' final chains, only for the cells that contain
    # at least one unfixable person.  Chains are stored WITHOUT person_id
    # (resample_chains assigns the recipient's person_id).
    needed_cells = {
        person_cells[p] for p in unfixable_persons if p in person_cells
    }
    donor_chains: dict = {cell: [] for cell in needed_cells}
    valid_table = table[~table["person_id"].isin(set(unfixable_persons))]
    # Sorted iteration over donor person ids for deterministic pool order.
    for person_id, chain in valid_table.sort_values(
        ["person_id", "departure_time"]
    ).groupby("person_id", sort=True):
        cell = person_cells.get(person_id)
        if cell in donor_chains:
            donor_chains[cell].append(chain.drop(columns=["person_id"]))

    table = resample_chains(
        table,
        unfixable_persons,
        person_cells,
        donor_chains,
        rng=np.random.RandomState(random_seed),
    )

    # Drop home-only fallback rows (NaN times): in the trips contract a person
    # without trips simply has no rows; activities.py gives them a single home
    # activity.  Observable, not silent.
    nan_rows = table["departure_time"].isna() | table["arrival_time"].isna()
    if nan_rows.any():
        dropped_persons = table.loc[nan_rows, "person_id"].nunique()
        logger.warning(
            "[popsim.trips] %d resampled persons had no same-cell donor and become "
            "trip-less (home-only) persons; their %d placeholder rows are dropped "
            "from the trips table.",
            dropped_persons, int(nan_rows.sum()),
        )
        table = table[~nan_rows]

    # Re-derive ids and derived columns on the final frame (mirrors
    # PlanValidator.repair_trips step 4): donor chains carry the donor's
    # trip_id / trip_index, which are stale for the recipient.
    table = table.sort_values(["person_id", "departure_time"]).reset_index(drop=True)
    table["trip_id"] = range(len(table))
    table = hts.compute_first_last(table)
    table["trip_duration"] = table["arrival_time"] - table["departure_time"]
    hts.compute_activity_duration(table)  # modifies in-place, no return
    table["trip_index"] = table.groupby("person_id").cumcount()
    return table
