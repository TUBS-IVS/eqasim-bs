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

# Escort (Begleitung) W_ZWECK codes (issue #201). Code 6 = Bringen/Holen; code 13
# is classified as escort by BOTH of MiD's own derived purpose variables
# (zweck: 13 -> 6; hwzweck1: 13 -> 7 = Begleitung; verified 2026-07-24 on the raw
# Wege table -- see docs/superpowers/specs/2026-07-24-escort-purpose-design.md).
# The semantic codeplan label of 13 is still to be confirmed (codeplan xlsx not in
# repo); the CATEGORY membership is established by the MiD-internal derivations.
# Deliberately separate from purpose_subtype.OTHER_ESCORT_ZWECK ({6}), which the
# escort-OFF path (secondary_other_subtype_split) continues to use unchanged.
#
# Issue #256 further splits this set: code 6 is the ACTIVE escort leg (the
# escorting adult's own trip) and code 13 is the PASSIVE leg (the escorted
# person's own trip -- 100% minors on the raw MiD file; pinned active/passive
# split shares in eqasim-data/data/braunschweig/mid/mid2023_escort_w_zweck_split.csv,
# derived by scripts/derive_escort_w_zweck_split.py). When escort_passive_education
# is ON (map_purpose below), code 13 is relabelled to "education" instead of
# "escort" because it is the child's own trip to their assigned Kita/school, not
# an escort trip in its own right; code 6 keeps mapping to "escort".
ESCORT_W_ZWECK = frozenset({6, 13})

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


def map_purpose(wege: pd.DataFrame, *, zweck_col: str = "W_ZWECK",
                escort_purpose: bool = False,
                escort_passive_education: bool = False) -> pd.DataFrame:
    """Add the eqasim activity ``purpose`` from MiD ``W_ZWECK``.

    When ``escort_purpose`` is True (issue #201), W_ZWECK codes in
    ``ESCORT_W_ZWECK`` map to the dedicated ``"escort"`` purpose instead of
    ``"other"``; the override is applied on top of ``PURPOSE_BY_W_ZWECK`` so the
    OFF path stays byte-identical. The escort share is logged (W_GEW-weighted
    when the weight column is present) -- no silent re-mapping.

    When ``escort_passive_education`` is ALSO True (issue #256), the PASSIVE
    side of the escort pair (W_ZWECK 13 -- the escorted person's own leg) is
    relabelled to ``"education"`` instead of ``"escort"``: it is the child's own
    trip to their assigned Kita/school (anchored there downstream by the
    plan-based ``has_education_trip`` primary-location machinery, which covers
    both chain sides), not an escort trip in its own right. The ACTIVE side
    (W_ZWECK 6 -- the escorting adult's leg) keeps mapping to ``"escort"``.
    Requires ``escort_purpose`` to also be True (raises ``ValueError``
    otherwise, checked before the escort-specific remapping); default False
    keeps the #201 behaviour -- including the exact log line -- byte-identical.

    Parameters
    ----------
    wege:
        MiD Wege with at least ``zweck_col``. ``W_GEW`` (trip weight), if
        present, is used to log a weighted escort/passive share.
    zweck_col:
        Name of the MiD W_ZWECK column.
    escort_purpose:
        If True (issue #201), map ``ESCORT_W_ZWECK`` codes to ``"escort"``.
    escort_passive_education:
        If True (issue #256), further relabel the passive leg (W_ZWECK 13) to
        ``"education"``. Requires ``escort_purpose=True``.

    Returns
    -------
    pd.DataFrame
        ``wege`` with an added ``purpose`` column.

    Raises
    ------
    ValueError
        If ``escort_passive_education`` is True while ``escort_purpose`` is
        False (there is no passive side to split off without the dedicated
        escort purpose being active).
    """
    out = wege.copy()
    out["purpose"] = out[zweck_col].map(PURPOSE_BY_W_ZWECK).fillna(DEFAULT_PURPOSE)
    if escort_passive_education and not escort_purpose:
        raise ValueError(
            "[popsim.trips] escort_passive_education requires escort_purpose to be ON "
            "(without a dedicated escort purpose there is no passive side to split off)."
        )
    if escort_purpose:
        escort_mask = out[zweck_col].isin(ESCORT_W_ZWECK)
        out.loc[escort_mask, "purpose"] = "escort"
        passive_mask = out[zweck_col] == 13
        if escort_passive_education:
            # Issue #256: W_ZWECK 13 is the escorted person's OWN (passive) leg --
            # 100% minors on the raw file. It becomes the child's own education
            # trip, anchored at their own assigned Kita/school by the plan-based
            # primary machinery (has_education_trip covers both chain sides).
            out.loc[passive_mask, "purpose"] = "education"
        if "W_GEW" in out.columns:
            weights = out["W_GEW"].astype(float)
            total = float(weights.sum())
            share_active = float(weights[escort_mask & ~passive_mask].sum() / total) if total else 0.0
            share_passive = float(weights[passive_mask].sum() / total) if total else 0.0
            basis = "W_GEW-weighted"
        else:
            share_active = float((escort_mask & ~passive_mask).mean()) if len(out) else 0.0
            share_passive = float(passive_mask.mean()) if len(out) else 0.0
            basis = "unweighted"
        if escort_passive_education:
            logger.info(
                "[popsim.trips] escort_passive_education ON: active W_ZWECK 6 -> "
                "'escort' %d legs (%.2f%% %s); passive W_ZWECK 13 -> 'education' "
                "%d legs (%.2f%%) at the child's own school.",
                int((escort_mask & ~passive_mask).sum()), 100.0 * share_active, basis,
                int(passive_mask.sum()), 100.0 * share_passive,
            )
        else:
            logger.info(
                "[popsim.trips] escort_purpose ON: %d/%d legs (%.2f%% %s) mapped to "
                "'escort' (W_ZWECK in %s)",
                int(escort_mask.sum()), len(out), 100.0 * (share_active + share_passive),
                basis, sorted(ESCORT_W_ZWECK),
            )
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
    escort_purpose: bool = False,
    escort_passive_education: bool = False,
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
    escort_purpose:
        If True (issue #201), W_ZWECK codes in ``ESCORT_W_ZWECK`` map to the
        dedicated ``"escort"`` purpose instead of ``"other"`` (forwarded to
        ``map_purpose`` via ``expand_persons_to_trips``). Default False keeps
        the OFF path byte-identical.
    escort_passive_education:
        If True (issue #256), the passive escort leg (W_ZWECK 13) maps to
        ``"education"`` instead of ``"escort"`` (forwarded to ``map_purpose``
        via ``expand_persons_to_trips``). Requires ``escort_purpose=True``.
        Default False keeps the OFF path byte-identical.

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
        escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
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
    escort_purpose: bool = False,
    escort_passive_education: bool = False,
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
    wege = map_mode(map_purpose(
        mid_wege, escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
    ))
    merged = persons.merge(
        wege, on=[household_col, person_col], how="inner", suffixes=("", "_weg")
    )
    merged["trip_id"] = (
        merged["person_id"].astype(str) + "_" + merged[trip_col].astype(str)
    )

    # Instrument the inner join: persons whose donor (H_ID, P_ID) has no Wege
    # row are silently dropped (they become trip-less home-only persons). A
    # low match rate almost always signals a broken donor-key join rather than
    # a genuinely immobile donor population, so log it as an explicit rate
    # (mirrors the ENTD twin, sources/entd.py build_trips).
    n_persons_total = len(persons)
    n_persons_with_trips = merged["person_id"].nunique() if n_persons_total > 0 else 0
    n_persons_without_trips = n_persons_total - n_persons_with_trips
    match_rate = n_persons_with_trips / max(n_persons_total, 1)
    logger.info(
        "[popsim.trips] expand_persons_to_trips: %d/%d persons (%.1f%%) have donor "
        "trips; %d persons without trips.",
        n_persons_with_trips, n_persons_total, 100.0 * match_rate, n_persons_without_trips,
    )
    if n_persons_total > 0 and match_rate < MIN_EXPECTED_TRIP_MATCH_RATE:
        logger.warning(
            "[popsim.trips] expand_persons_to_trips: donor-trip match rate %.1f%% is "
            "below the expected minimum %.1f%% -- this usually indicates a broken "
            "(H_ID, P_ID) join between synthetic persons and MiD Wege, not a "
            "genuinely immobile donor population.",
            100.0 * match_rate, 100.0 * MIN_EXPECTED_TRIP_MATCH_RATE,
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
    escort_purpose: bool = False,
    escort_passive_education: bool = False,
    **kwargs,
):
    """Build the trip table, optionally repair + resample, return (table, ValidationReport).

    Thin convenience wrapper over build_trip_table + PlanValidator. When repair is
    True (default) the PlanValidator enforces home-end closure and logs its repair
    rates (the rates are emitted by repair_trips itself, so they remain observable
    even though the RepairReport is not returned here). When ``resample`` is True,
    unfixable persons go through a two-stage cascade:

    - Stage A (``time_imputation.impute_chain_times``): persons with the
      ``nan_times`` issue (MiD coded times 99/701 — the 701 rbW group are
      systematically REGULAR COMMUTERS) and a complete, code-free own
      ``wegmin_imp1`` keep their OWN chain (purposes/modes/distances are real)
      and only the times are imputed from empirical same-purpose pools; the
      affected persons are then re-repaired (home-end closure now applies).
    - Stage B (``_match_unfixable``): persons still unfixable after stage A
      have their whole chain replaced by the chain of an ATTRIBUTE-MATCHED
      valid donor (hierarchical-relaxation matching via
      ``synthesis.population.matched.match_donors`` on sex, age_class,
      employed, socioprofessional_class[, RegioStaR7]); behavioural
      similarity beats 100 m proximity (the legacy same-cell pool held only
      1-3 donors at 1 % sampling and 31.8 % of persons found none). The
      replaced rows carry ``chain_donor_id`` (the donor's person_id) for
      traceability. Persons that cannot be matched (no donor shares their
      ``sex``, the never-relaxed first key) become trip-less home-only
      persons — the rate is logged loudly and expected to be ~0. Persons
      frames without a ``sex`` column (minimal fixtures) fall back to the
      legacy same-cell resample (``plan_validation.resample_chains``) with a
      loud warning.

    The returned ValidationReport reflects the FINAL (post-repair, post-impute,
    post-resample) table.

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
        If True, replace unfixable persons' chains via the stage A/B cascade
        (time imputation, then attribute-matched donor chains).  Requires
        ``repair=True`` (the unfixable classification comes from the
        RepairReport) and a non-None ``random_seed`` (determinism is mandatory).
    resample_cell_col:
        Column in ``persons`` that defines the donor-matching cell (e.g.
        ``"ZENSUS100m"``).  Only used by the LEGACY same-cell resample path,
        which stage B falls back to when the persons frame carries no ``sex``
        column; on that path ``None`` means every unfixable person falls back
        to a home-only plan (logged loudly by resample_chains).
    random_seed:
        Seed for the stage A/B RNG streams (``np.random.RandomState``; see
        ``TIME_IMPUTATION_SEED_OFFSET`` / ``MATCHED_REPLACEMENT_SEED_OFFSET``).
    escort_purpose:
        If True (issue #201), W_ZWECK codes in ``ESCORT_W_ZWECK`` map to the
        dedicated ``"escort"`` purpose instead of ``"other"`` (forwarded to
        ``build_trip_table`` / ``map_purpose``). Default False keeps the OFF
        path byte-identical.
    escort_passive_education:
        If True (issue #256), the passive escort leg (W_ZWECK 13) maps to
        ``"education"`` instead of ``"escort"`` (forwarded to
        ``build_trip_table`` / ``map_purpose``). Requires ``escort_purpose=True``.
        Default False keeps the OFF path byte-identical.
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

    table = build_trip_table(
        persons, mid_wege, escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education, **kwargs,
    )
    validator = PlanValidator(require_home_closure=require_home_closure)
    repair_report = None
    if repair:
        table, repair_report = validator.repair_trips(table)

    # Cascade stage A: time imputation for coded-time (nan_times) persons with a
    # complete own wegmin_imp1.  Runs AFTER the first repair (the nan_times
    # classification comes from its RepairReport) and BEFORE the resample; the
    # helper re-runs repair_trips on the imputed table so the now-timed chains
    # receive home-end closure, and returns the updated RepairReport whose
    # unfixable set drives stage B (the existing same-cell resample).
    if resample and repair_report is not None and repair_report.unfixable_persons:
        table, repair_report = _impute_nan_time_unfixable(
            table, repair_report, validator, random_seed=random_seed
        )

    if resample and repair_report is not None and repair_report.unfixable_persons:
        table = _match_unfixable(
            table,
            persons,
            repair_report.unfixable_persons,
            resample_cell_col=resample_cell_col,
            random_seed=random_seed,
            resample_chains=resample_chains,
        )

    report = validator.validate_trips(table)
    return table, report


def _impute_nan_time_unfixable(
    table: pd.DataFrame,
    repair_report,
    validator,
    *,
    random_seed: int,
):
    """Cascade stage A: keep coded-time persons' own chains, impute only the times.

    Sequencing (documented because the order is load-bearing):

    1. Runs AFTER the first ``repair_trips`` because the ``nan_times``
       classification (which persons have MiD coded times 99/701) is derived
       from its output — a person is a stage A candidate iff they are in the
       unfixable set AND still carry a NaN departure/arrival time (the exact
       condition PlanValidator flags as ``nan_times``).
    2. ``impute_chain_times`` writes times only for persons whose ``wegmin_imp1``
       is complete and code-free on every trip; everyone else stays NaN.
    3. The FULL table is then re-repaired: the imputed persons were excluded
       from the first home-end closure pass (NaN times), so the second pass
       closes their chains, recomputes trip_id/first-last/durations/trip_index
       globally, and re-classifies.  Re-repairing the full table (instead of a
       subset) is safe because repair_trips is a no-op for already-repaired
       chains (fix_trip_times leaves consistent times unchanged, closure only
       appends for non-home-ending persons, the recompute step is idempotent)
       and it avoids a fragile subset-concat-recompute sequence.
    4. The second RepairReport's unfixable set (imputation-skipped persons,
       plus any chain the closure append pushed past the plan bound) flows to
       stage B, the existing same-cell resample, unchanged.

    The stage A RNG is a child stream of the caller's ``random_seed``
    (``RandomState(random_seed + TIME_IMPUTATION_SEED_OFFSET)``) so the
    imputation draws are decorrelated from the resample and jitter streams,
    which both consume ``RandomState(random_seed)`` directly.
    """
    import numpy as np

    from braunschweig.popsim.time_imputation import (
        TIME_IMPUTATION_SEED_OFFSET,
        impute_chain_times,
    )

    nan_rows = table["departure_time"].isna() | table["arrival_time"].isna()
    nan_time_persons = (
        set(table.loc[nan_rows, "person_id"].unique()) & repair_report.unfixable_persons
    )
    if not nan_time_persons:
        return table, repair_report

    if "wegmin_imp1" not in table.columns:
        # The MiD delivery always carries wegmin_imp1 (MID_WEGE_REQUIRED_COLS);
        # other donor sources (e.g. ENTD) do not.  Without it stage A cannot
        # run — observable, then the legacy stage B resample handles everyone.
        logger.warning(
            "[popsim.trips] stage A time imputation unavailable: column "
            "'wegmin_imp1' is missing from the trip table; %d nan-time persons "
            "go straight to the stage B resample.",
            len(nan_time_persons),
        )
        return table, repair_report

    rng = np.random.RandomState(random_seed + TIME_IMPUTATION_SEED_OFFSET)
    table, imputation_report = impute_chain_times(
        table,
        nan_time_persons,
        rng=rng,
        max_plan_time_seconds=validator.max_plan_time_seconds,
    )

    if imputation_report.n_imputed == 0:
        # Nothing changed; keep the first report (and skip a redundant repair).
        return table, repair_report

    table, second_report = validator.repair_trips(table)
    return table, second_report


# Matching keys for the stage B attribute-matched chain replacement, in
# priority order: match_donors relaxes keys FROM THE END of this list, so the
# order encodes priority and the FIRST key (sex) is never relaxed.  Mirrors the
# ENTD diary-donor chain matching (braunschweig.popsim.sources.entd).  Keys
# missing from the persons frame are dropped with a logged warning.
# RegioStaR7 is the SYNTHETIC HOME's cell RS7, joined onto the merged
# PopulationSim output by braunschweig.popsim.stage.join_cell_attributes and
# expanded onto every person; older cell parquets without the column trigger
# the logged drop and the matching falls back to the non-spatial key list.
MATCHED_REPLACEMENT_COLUMNS = [
    "sex", "age_class", "employed", "socioprofessional_class", "RegioStaR7",
]

# Continuous matching columns derived from a differently-named raw column.
_MATCHED_REPLACEMENT_SOURCE_COLUMN = {"age_class": "age"}

# Child-stream offset for the stage B matching RNG: match_donors consumes
# RandomState(random_seed + MATCHED_REPLACEMENT_SEED_OFFSET), decorrelated from
# the jitter / legacy-resample streams (RandomState(random_seed)) and the
# stage A imputation stream (random_seed + TIME_IMPUTATION_SEED_OFFSET = 4159).
MATCHED_REPLACEMENT_SEED_OFFSET = 7211

# Below this donor-match rate, expand_persons_to_trips is assumed to be hitting a
# join defect (wrong key, format mismatch) rather than the expected small share of
# genuinely immobile / unmatched MiD donors. ASSUMPTION: chosen conservatively, not
# fitted to observed data; revisit if a legitimate high-immobility scenario trips it.
MIN_EXPECTED_TRIP_MATCH_RATE = 0.5


def _recompute_chain_ids(table: pd.DataFrame) -> pd.DataFrame:
    """Re-derive trip_id / first-last / durations / trip_index on the final frame.

    Same recompute sequence as ``PlanValidator.repair_trips`` step 4: replaced
    donor chains carry the DONOR's stale ``trip_id`` / ``trip_index``, which
    would otherwise collide with the kept persons' rows.
    """
    from data.hts import hts

    table = table.sort_values(["person_id", "departure_time"]).reset_index(drop=True)
    table["trip_id"] = range(len(table))
    table = hts.compute_first_last(table)
    table["trip_duration"] = table["arrival_time"] - table["departure_time"]
    hts.compute_activity_duration(table)  # modifies in-place, no return
    table["trip_index"] = table.groupby("person_id").cumcount()
    return table


def _match_unfixable(
    table: pd.DataFrame,
    persons: pd.DataFrame,
    unfixable_persons,
    *,
    resample_cell_col: str | None,
    random_seed: int,
    resample_chains,
) -> pd.DataFrame:
    """Cascade stage B: replace unfixable persons' chains by attribute matching.

    Each unfixable person is matched to a VALID-chain donor person (post
    stage A) via the reusable hierarchical-relaxation matcher
    ``synthesis.population.matched.match_donors`` on
    ``MATCHED_REPLACEMENT_COLUMNS`` and inherits the matched donor's FULL
    final (post-repair) chain; the copied rows carry ``chain_donor_id`` (the
    donor's person_id) for traceability, consistent with the ENTD diary-donor
    chain matching convention (``braunschweig.popsim.sources.entd``).

    Replaces the legacy same-cell (``resample_cell_col``) draw: at 1 % sampling
    31.8 % of unfixable persons had NO same-cell donor (pool of 1-3 chains) and
    fell back to home-only plans — attribute similarity over the whole valid
    pool is both better-covered and behaviourally closer.  Home-only (=
    trip-less, no rows in the table) remains ONLY as the loudly-logged catch
    for match failures: persons whose ``sex`` value (the never-relaxed first
    key) has fewer donors than ``minimum_observations``, or — belt-and-braces —
    a ``RuntimeError`` from full-relaxation failure.

    Fallback transparency: when the persons frame carries no ``sex`` column at
    all (minimal unit-test fixtures), the matching is impossible and the
    legacy same-cell resample (``resample_chains``) handles everyone — logged
    loudly, never silent.

    Determinism: ``match_donors`` is seeded with
    ``random_seed + MATCHED_REPLACEMENT_SEED_OFFSET`` (see the constant's
    comment for the stream layout).
    """
    # Reusing the legacy statistical-matching machinery from the shared
    # synthesis tree is established practice in this package (see
    # braunschweig.popsim.sources.entd, which wraps the same helper).
    from braunschweig.popsim.chain_matching import (
        derive_age_class,
        effective_minimum_observations,
    )
    from synthesis.population.matched import match_donors

    unfixable = set(unfixable_persons)
    n_unfixable = len(unfixable)

    # One attribute row per unique synthetic person (like build_trip_table).
    persons_unique = persons.drop_duplicates(subset="person_id").copy()

    if "sex" not in persons_unique.columns:
        logger.warning(
            "[popsim.trips] stage B: persons frame carries no 'sex' column, so "
            "attribute-matched chain replacement is impossible; falling back to "
            "the legacy same-cell resample for all %d unfixable persons.",
            n_unfixable,
        )
        return _resample_unfixable(
            table, persons, unfixable_persons,
            resample_cell_col=resample_cell_col,
            random_seed=random_seed,
            resample_chains=resample_chains,
        )

    # Matching keys actually available on the persons frame; missing keys are
    # dropped with a logged warning (never silently).
    columns = []
    for column in MATCHED_REPLACEMENT_COLUMNS:
        source_column = _MATCHED_REPLACEMENT_SOURCE_COLUMN.get(column, column)
        if source_column not in persons_unique.columns:
            logger.warning(
                "[popsim.trips] stage B matching: key '%s' dropped because "
                "column '%s' is missing on the persons frame.",
                column, source_column,
            )
            continue
        if column == "age_class":
            persons_unique["age_class"] = derive_age_class(persons_unique["age"])
        columns.append(column)

    # Pool = persons with VALID chains after stage A (rows kept in the table
    # and not classified unfixable); one row per person, weight 1.0 (the
    # synthetic frame is already expanded), hts_id = their person_id.
    valid_table = table[~table["person_id"].isin(unfixable)]
    valid_chain_persons = set(valid_table["person_id"].unique())
    pool = persons_unique[
        persons_unique["person_id"].isin(valid_chain_persons)
    ].copy()
    pool = pool.rename(columns={"person_id": "hts_id"})
    pool["weight"] = 1.0

    targets = persons_unique[persons_unique["person_id"].isin(unfixable)]

    assignment = pd.DataFrame(columns=["person_id", "hts_id"])
    if len(pool) == 0:
        logger.warning(
            "[popsim.trips] stage B matching: the valid-chain donor pool is "
            "EMPTY; all %d unfixable persons become trip-less (home-only). "
            "This signals a broken trip table, not a tolerable fallback.",
            n_unfixable,
        )
    else:
        minimum_observations = effective_minimum_observations(len(pool))

        # Feasibility pre-filter on the FIRST key: match_donors never relaxes
        # it, and a single infeasible target would raise RuntimeError for the
        # whole call. Targets whose first-key value has too few donors are
        # left trip-less individually (logged below) instead of aborting all.
        first_key = columns[0]
        donor_counts = pool[first_key].value_counts()
        feasible_values = set(
            donor_counts[donor_counts >= minimum_observations].index
        )
        feasible = targets[first_key].isin(feasible_values)
        n_infeasible = int((~feasible).sum())
        if n_infeasible > 0:
            logger.warning(
                "[popsim.trips] stage B matching: %d/%d unfixable persons are "
                "unmatchable (their '%s' value has < %d valid-chain donors) "
                "and become trip-less (home-only). A high rate signals a "
                "broken donor pool, not a tolerable fallback.",
                n_infeasible, n_unfixable, first_key, minimum_observations,
            )
        targets = targets.loc[feasible]

        if len(targets) > 0:
            try:
                assignment = match_donors(
                    targets[["person_id"] + columns],
                    pool[["hts_id", "weight"] + columns],
                    matching_columns=columns,
                    minimum_observations=minimum_observations,
                    random_seed=random_seed + MATCHED_REPLACEMENT_SEED_OFFSET,
                )
            except RuntimeError:
                # Belt-and-braces: the pre-filter above should prevent this;
                # if it fires anyway, leave the remaining persons trip-less
                # and report loudly rather than crashing the trips build.
                logger.error(
                    "[popsim.trips] stage B matching: match_donors failed at "
                    "full relaxation for %d unfixable persons; they become "
                    "trip-less (home-only). Investigate the donor pool.",
                    len(targets),
                )

    # Matched persons inherit the matched donor's FULL final chain; the donor
    # is recorded per trip row as chain_donor_id (entd.py convention).
    replacement_rows = None
    if len(assignment) > 0:
        replacement_rows = valid_table.merge(
            assignment.rename(columns={"person_id": "_target_person_id"}),
            left_on="person_id",
            right_on="hts_id",
            how="inner",
        )
        replacement_rows["chain_donor_id"] = replacement_rows["hts_id"]
        replacement_rows["person_id"] = replacement_rows["_target_person_id"]
        replacement_rows = replacement_rows.drop(
            columns=["_target_person_id", "hts_id"]
        )

    n_matched = int(assignment["person_id"].nunique())
    n_home_only = n_unfixable - n_matched
    log = logger.warning if n_home_only > 0 else logger.info
    log(
        "[popsim.trips] stage B: %d unfixable persons -> %d matched chains, "
        "%d home-only (trip-less persons without trip rows; expected ~0).",
        n_unfixable, n_matched, n_home_only,
    )

    if replacement_rows is not None:
        table = pd.concat([valid_table, replacement_rows],
                          ignore_index=True, sort=False)
    else:
        # Everyone unmatched: only the valid persons keep rows (home-only
        # persons are trip-less by the eqasim stay-home convention).
        table = valid_table

    return _recompute_chain_ids(table)


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

    # Re-derive ids and derived columns on the final frame: donor chains carry
    # the donor's trip_id / trip_index, which are stale for the recipient.
    return _recompute_chain_ids(table)
