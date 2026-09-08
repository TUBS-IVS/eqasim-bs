"""SrV 2023 plan-structure reference (daily trip/plan structure per person segment).

Builds the committed aggregate table ``srv2023_plan_structure_reference.csv`` from the
LOCAL-ONLY SrV 2023 "Braunschweig und RGB" scientific-use microdata: for a fixed set of person
segments (all / age band / employment-and-life-phase group / sex / home Kreis) the weighted
daily plan structure -- mobility rate, trips per person, trip-count distribution, per-purpose
participation and trip rates, purpose shares, home-based share, tour and day-closure indicators,
work-activity durations and departure-hour profiles. It is a VALIDATION REFERENCE for the
plan-structure fix (issue #369), NOT a control target: no synthesis or location stage reads it.

The module is a pure builder over already-loaded SrV person/trip/household frames (no synpp
dependency, no file system access); ``scripts/extract_srv_plan_structure.py`` is the only
caller that touches the raw microdata.

Why the metric functions are split the way they are: :func:`harmonise_srv` is the ONLY function
that knows SrV column names. :func:`person_level` and :func:`segment_metrics` operate purely on
the harmonised schema

    persons: pid, weight, age, age_band, sex, employed, group, kreis, n_trips
             (+ away_from_home, reported_at_home -- SrV-side universe flags, unused by the
             metrics themselves)
    trips:   pid, seq, weight, purpose, prev_purpose, dep_min, arr_min

so that the MODEL side of the comparison (synthetic population) can be pushed through exactly
the same metric code after being harmonised to the same schema. Any metric that needed an SrV
column would silently make the two sides incomparable, which is the failure mode this split
exists to prevent.

Universe conventions (both are emitted; the primary one is ``at_home_zero``):

* ``at_home_zero`` -- the whole delivered SrV person file with a valid expansion weight. The
  per-person trip count is ``E_ANZ_WEGE2``, which counts a person who was away from home over
  the reporting day (``E_ANZ_WEGE == -7``) as 0 trips. This is the universe that matches a
  synthetic population, in which every person is present and at home at the start of the day.
* ``at_home_only`` -- sensitivity: persons with ``E_ANZ_WEGE >= 0``, i.e. away-from-home
  persons excluded entirely. On the 2026-09-05 delivery the only negative ``E_ANZ_WEGE`` code
  present is ``-7`` (954 of 18,223 persons), so this universe is exactly "not away from home";
  :func:`harmonise_srv` carries both flags and logs any disagreement rather than assuming it.

Weights: ``GEWICHT_P_ZENSUS`` for persons and ``GEWICHT_W_ZENSUS`` for trips (expansion to
Zensus 2022 counts; the stratum-internal ``GEWICHT_P``/``GEWICHT_W`` must not be used across
strata -- same convention as ``braunschweig.calibration.srv_distance_targets``, ADR-0055).

Deliberate deviation from the session analysis this module ports (scratchpad script
``plan_structure_vs_srv.py``, report 2026-09-05): trips with ``V_ZWECK == -10`` map to
``"unknown"`` and are COUNTED as trips (they are in ``n_trips``, ``trips_per_person`` and the
trip-count distribution) but are EXCLUDED from the denominator of ``purpose_share_*``, so the
seven purpose shares sum to exactly 1.0. Their weight is reported separately as
``share_trips_unknown_purpose``, so nothing is hidden. Every other metric definition is
identical to the session script (verified against its published headline values, see the data
record ``docs/registry/data/srv2023_plan_structure_reference.yml``).
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_distance_targets import kreis_from_ags

logger = logging.getLogger(__name__)

_LOG_TAG = "[srv plan structure]"

PLAN_STRUCTURE_TABLE = "srv2023_plan_structure_reference.csv"

# ----------------------------------------------------------------------------- SrV code maps
# SrV V_ZWECK / E_START_ZWECK -> harmonised purpose (codebook SrV2023_Datenkodierung_SciUse.xlsx).
# 1-2 Arbeit/dienstlich, 3-7 Ausbildung, 8-9 Einkauf, 10-11 Erledigung, 12 Begleitung,
# 13-18 Freizeit, 19 nach Hause, 70 sonstiges. Codes outside this map (in particular -10 "keine
# Angabe" and, for E_START_ZWECK, -7 "war nicht zu Hause") become UNKNOWN_PURPOSE.
PURPOSE_BY_V_ZWECK = {1: "work", 2: "work", 3: "education", 4: "education", 5: "education",
                      6: "education", 7: "education", 8: "shop", 9: "shop", 10: "other",
                      11: "other", 12: "escort", 13: "leisure", 14: "leisure", 15: "leisure",
                      16: "leisure", 17: "leisure", 18: "leisure", 19: "home", 70: "other"}
UNKNOWN_PURPOSE = "unknown"
PURPOSES = ("work", "education", "shop", "leisure", "escort", "other", "home")
# Single letter per purpose, used to spell a person's day as a pattern string (e.g. "HWH").
PATTERN_LETTER = {"home": "H", "work": "W", "education": "E", "shop": "S", "leisure": "L",
                  "escort": "B", "other": "O", UNKNOWN_PURPOSE: "?"}

# V_ERW: 8 in Ausbildung/Lehre, 9 full-time, 10 part-time, 11 marginally employed. Code 8 is an
# apprenticeship WITH an employment contract and is counted as employed by decision Q5 of the
# participation-universe spec (issue #368): the MiD-side employment_status classes that the
# regional controls target include `in_ausbildung`, and ADR-0060 treats MiD in_ausbildung
# (1.93 %) and SrV V_ERW 8 (1.87 %) as apples-to-apples. Before that realignment this reference
# and the control measured different populations -- a 16-year-old apprentice was `employed` on
# the MiD side but `school_age_6_17_not_employed` here. The arm-3 aggregates under
# calibration/plan_structure_fix_arm3_100pct_2026-09-07/ were built with the narrower (9, 10, 11)
# definition and stay historical; see docs/registry/data/srv2023_plan_structure_reference.yml.
EMPLOYED_V_ERW = (8, 9, 10, 11)
SEX_BY_V_GESCHLECHT = {1: "male", 2: "female"}   # 3 "divers" / 4 "keine Angabe" -> NaN
AWAY_FROM_HOME_CODE = -7              # E_ANZ_WEGE: person was away from home all reporting day
EXPECTED_STICHTAG_WTAG = (2, 3, 4)    # delivered reporting days: Tuesday, Wednesday, Thursday
EXPECTED_GEWICHT_P_WERKTAG = -6.0     # "not applicable" in this delivery (no weekday weight)

AGE_BINS = (-1, 5, 9, 17, 24, 44, 64, 74, 200)
AGE_LABELS = ("0-5", "6-9", "10-17", "18-24", "25-44", "45-64", "65-74", "75+")
GROUPS = ("employed", "child_0_5", "school_age_6_17_not_employed", "adult_18_64_not_employed",
          "senior_65plus_not_employed")
SEXES = ("male", "female")

# Departure-hour profile: reported for hours 4..23 (the hours a reporting day realistically
# spans). Shares are normalised over ALL trips of the purpose with a valid departure time
# (clipped into 0..27 as in the session analysis), so the 20 reported hours need not sum to 1.
DEPARTURE_HOURS = tuple(range(4, 24))
HOUR_CLIP = (0, 27)

# Work-activity duration filter (hours): a work activity is the gap between the arrival of a
# trip TO work and the person's next departure; implausible values are excluded.
WORK_ACTIVITY_MAX_H = 20.0
SHORT_WORK_ACTIVITY_H = 2.0

UNIVERSE_AT_HOME_ZERO = "at_home_zero"
UNIVERSE_AT_HOME_ONLY = "at_home_only"
UNIVERSES = (UNIVERSE_AT_HOME_ZERO, UNIVERSE_AT_HOME_ONLY)

PERSON_COLUMNS = ["HHNR", "PNR", "V_ALTER", "V_ERW", "V_GESCHLECHT", "E_ANZ_WEGE", "E_ANZ_WEGE2",
                  "GEWICHT_P_ZENSUS", "STICHTAG_WTAG", "GEWICHT_P_WERKTAG"]
TRIP_COLUMNS = ["HHNR", "PNR", "WNR", "V_ZWECK", "E_START_ZWECK", "E_BEGINN", "E_ANKUNFT",
                "GEWICHT_W_ZENSUS"]
HOUSEHOLD_COLUMNS = ["HHNR", "AGS"]

HARMONISED_PERSON_COLUMNS = ["pid", "weight", "age", "age_band", "sex", "employed", "group",
                             "kreis", "n_trips", "away_from_home", "reported_at_home"]
HARMONISED_TRIP_COLUMNS = ["pid", "seq", "weight", "purpose", "prev_purpose", "dep_min",
                           "arr_min"]

REFERENCE_COLUMNS = ["universe", "segment", "metric", "value", "n_unweighted"]


# ----------------------------------------------------------------------------- small helpers
def weighted_mean(values, weights) -> float:
    """Weighted mean; NaN (not a ZeroDivisionError) for an empty or zero-weight input."""
    weights = np.asarray(weights, dtype=float)
    values = np.asarray(values, dtype=float)
    total = weights.sum()
    if not total > 0:
        return float("nan")
    return float(np.sum(values * weights) / total)


def _weighted_share(weights, mask) -> float:
    """Share of the total weight carried by rows where ``mask`` is True; NaN if no weight."""
    weights = np.asarray(weights, dtype=float)
    total = weights.sum()
    if not total > 0:
        return float("nan")
    return float(weights[np.asarray(mask, dtype=bool)].sum() / total)


def _require_columns(frame: pd.DataFrame, required, name: str) -> None:
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError("%s frame is missing required column(s) %s" % (name, missing))


def harmonised_group(persons: pd.DataFrame) -> np.ndarray:
    """Employment x life-phase group that BOTH comparison sides can express.

    The synthetic population has no pupil/student flag, so the non-employed are split by age
    instead of by the SrV ``V_ERW`` education codes. Order matters: ``employed`` wins over every
    age rule. A person with a missing age (``NaN``) falls through every age comparison and lands
    in the ``senior_65plus_not_employed`` default -- 2 of 18,223 persons in the 2026-09-05
    delivery; kept identical to the session analysis rather than "fixed" silently, and reported
    as ``n_persons_missing_age`` in the extraction diagnostics.
    """
    _require_columns(persons, ["employed", "age"], "persons")
    age = persons["age"]
    return np.select(
        [persons["employed"].astype(bool), age < 6, age <= 17, age <= 64],
        ["employed", "child_0_5", "school_age_6_17_not_employed", "adult_18_64_not_employed"],
        default="senior_65plus_not_employed")


# ----------------------------------------------------------------------------- harmonisation
def harmonise_srv(persons: pd.DataFrame, trips: pd.DataFrame,
                  households: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Harmonise raw SrV person/trip/household frames to the comparison schema.

    ``persons`` must carry :data:`PERSON_COLUMNS`, ``trips`` :data:`TRIP_COLUMNS`,
    ``households`` :data:`HOUSEHOLD_COLUMNS`.

    Universe guards (raise ``ValueError``, never warn-and-continue): the delivery is a pure
    Tuesday-Thursday ("mittlerer Werktag") sample, so every ``STICHTAG_WTAG`` must be in
    :data:`EXPECTED_STICHTAG_WTAG`, and ``GEWICHT_P_WERKTAG`` must be the "not applicable"
    sentinel :data:`EXPECTED_GEWICHT_P_WERKTAG` everywhere. A future delivery that breaks either
    assumption changes what "a day" means in every metric below, so it must stop the extraction
    rather than silently shift the reference.

    Exclusions (each counted and logged, never silent): persons with a missing or negative
    ``GEWICHT_P_ZENSUS`` and their trips; trips with a missing or negative ``GEWICHT_W_ZENSUS``.

    Returns ``(persons, trips)`` with exactly :data:`HARMONISED_PERSON_COLUMNS` and
    :data:`HARMONISED_TRIP_COLUMNS`, trips sorted by ``(pid, seq)``.
    """
    _require_columns(persons, PERSON_COLUMNS, "persons")
    _require_columns(trips, TRIP_COLUMNS, "trips")
    _require_columns(households, HOUSEHOLD_COLUMNS, "households")

    weekday = pd.to_numeric(persons["STICHTAG_WTAG"], errors="coerce")
    foreign = weekday[~weekday.isin(EXPECTED_STICHTAG_WTAG)]
    if len(foreign) > 0:
        raise ValueError(
            "universe drift: %d of %d persons have a STICHTAG_WTAG outside %s (found %s); this "
            "reference is defined on the Tuesday-Thursday delivery only"
            % (len(foreign), len(persons), list(EXPECTED_STICHTAG_WTAG),
               sorted(foreign.dropna().unique().tolist())))
    werktag = pd.to_numeric(persons["GEWICHT_P_WERKTAG"], errors="coerce")
    if not (werktag == EXPECTED_GEWICHT_P_WERKTAG).all():
        raise ValueError(
            "universe drift: GEWICHT_P_WERKTAG is not %s for every person (found %s); the "
            "delivery used to build this reference carries no weekday weight, so a delivery "
            "that does may need a different weight than GEWICHT_P_ZENSUS"
            % (EXPECTED_GEWICHT_P_WERKTAG,
               sorted(werktag[werktag != EXPECTED_GEWICHT_P_WERKTAG].dropna().unique().tolist())))

    kreis_by_hhnr = households.assign(kreis=kreis_from_ags(households["AGS"]))[["HHNR", "kreis"]]

    p = persons.merge(kreis_by_hhnr, on="HHNR", how="left")
    person_weight = pd.to_numeric(p["GEWICHT_P_ZENSUS"], errors="coerce")
    valid_weight = person_weight.notna() & (person_weight >= 0)
    n_persons_total = len(p)
    p = p[valid_weight].copy()
    logger.info("%s persons: %d/%d kept (%.1f%%), %d dropped for a missing/negative "
                "GEWICHT_P_ZENSUS", _LOG_TAG, len(p), n_persons_total,
                100.0 * len(p) / n_persons_total if n_persons_total else float("nan"),
                n_persons_total - len(p))

    age = pd.to_numeric(p["V_ALTER"], errors="coerce")
    out = pd.DataFrame({
        "pid": p["HHNR"].astype(str) + "_" + p["PNR"].astype(str),
        "weight": pd.to_numeric(p["GEWICHT_P_ZENSUS"], errors="coerce").astype(float),
        "age": age.where(age >= 0),
        "sex": p["V_GESCHLECHT"].map(SEX_BY_V_GESCHLECHT),
        "employed": p["V_ERW"].isin(EMPLOYED_V_ERW).values,
        "kreis": p["kreis"].values,
        "n_trips": pd.to_numeric(p["E_ANZ_WEGE2"], errors="coerce").astype(int).values,
        "away_from_home": (pd.to_numeric(p["E_ANZ_WEGE"], errors="coerce")
                           == AWAY_FROM_HOME_CODE).values,
        "reported_at_home": (pd.to_numeric(p["E_ANZ_WEGE"], errors="coerce") >= 0).values,
    })
    out["age_band"] = pd.cut(out["age"], list(AGE_BINS), labels=list(AGE_LABELS)).astype(str)
    out["group"] = harmonised_group(out)
    out = out[HARMONISED_PERSON_COLUMNS].reset_index(drop=True)

    # The two SrV-side universe flags must be complements: any person who is not "at home" on
    # the reporting day should carry exactly the -7 away code. Logged (not assumed) because a
    # future delivery may introduce further negative E_ANZ_WEGE codes, which would make
    # "at_home_only" quietly mean something else than "away persons excluded".
    disagreeing = int((out["away_from_home"] == out["reported_at_home"]).sum())
    if disagreeing:
        logger.warning("%s %d persons have E_ANZ_WEGE negative but different from %d; the "
                       "'%s' universe then excludes more than the away-from-home persons",
                       _LOG_TAG, disagreeing, AWAY_FROM_HOME_CODE, UNIVERSE_AT_HOME_ONLY)
    logger.info("%s persons away from home on the reporting day: %d (%.2f%% unweighted)",
                _LOG_TAG, int(out["away_from_home"].sum()),
                100.0 * out["away_from_home"].mean() if len(out) else float("nan"))
    no_kreis = int(out["kreis"].isna().sum())
    if no_kreis:
        logger.warning("%s %d persons have no resolvable home Kreis (household AGS missing or "
                       "sentinel); they stay in the 'all' segment but in no kreis segment",
                       _LOG_TAG, no_kreis)

    t = trips.copy()
    trip_weight = pd.to_numeric(t["GEWICHT_W_ZENSUS"], errors="coerce")
    n_trips_total = len(t)
    t = t[trip_weight.notna() & (trip_weight >= 0)].copy()
    n_after_weight = len(t)
    dep = pd.to_numeric(t["E_BEGINN"], errors="coerce")
    arr = pd.to_numeric(t["E_ANKUNFT"], errors="coerce")
    harmonised_trips = pd.DataFrame({
        "pid": t["HHNR"].astype(str) + "_" + t["PNR"].astype(str),
        "seq": pd.to_numeric(t["WNR"], errors="coerce").astype(int).values,
        "weight": pd.to_numeric(t["GEWICHT_W_ZENSUS"], errors="coerce").astype(float).values,
        "purpose": t["V_ZWECK"].map(PURPOSE_BY_V_ZWECK).fillna(UNKNOWN_PURPOSE).values,
        "prev_purpose": t["E_START_ZWECK"].map(PURPOSE_BY_V_ZWECK).fillna(UNKNOWN_PURPOSE).values,
        "dep_min": dep.where(dep >= 0).astype(float).values,
        "arr_min": arr.where(arr >= 0).astype(float).values,
    })
    # Trips of a dropped person are dropped with the person, so the trip table never contains a
    # trip whose person is outside the reference universe.
    harmonised_trips = harmonised_trips[harmonised_trips["pid"].isin(set(out["pid"]))]
    harmonised_trips = (harmonised_trips.sort_values(["pid", "seq"])[HARMONISED_TRIP_COLUMNS]
                        .reset_index(drop=True))
    logger.info("%s trips: %d/%d kept (%.1f%%), %d dropped for a missing/negative "
                "GEWICHT_W_ZENSUS, %d dropped with their person", _LOG_TAG,
                len(harmonised_trips), n_trips_total,
                100.0 * len(harmonised_trips) / n_trips_total if n_trips_total else float("nan"),
                n_trips_total - n_after_weight, n_after_weight - len(harmonised_trips))
    logger.info("%s trips with an unmapped purpose: destination %d (%.2f%%), origin %d (%.2f%%);"
                " invalid departure time %d, invalid arrival time %d", _LOG_TAG,
                int((harmonised_trips["purpose"] == UNKNOWN_PURPOSE).sum()),
                100.0 * (harmonised_trips["purpose"] == UNKNOWN_PURPOSE).mean(),
                int((harmonised_trips["prev_purpose"] == UNKNOWN_PURPOSE).sum()),
                100.0 * (harmonised_trips["prev_purpose"] == UNKNOWN_PURPOSE).mean(),
                int(harmonised_trips["dep_min"].isna().sum()),
                int(harmonised_trips["arr_min"].isna().sum()))
    return out, harmonised_trips


def harmonisation_diagnostics(raw_persons: pd.DataFrame, raw_trips: pd.DataFrame,
                              persons: pd.DataFrame, trips: pd.DataFrame) -> dict:
    """Exclusion / data-quality counts of one harmonisation, for the committed file's header.

    Counts are over the FULL raw input unless the key says otherwise; every ``n_persons_*``
    key after ``n_persons_negative_weight`` is measured on the harmonised (kept) persons, and
    every ``n_trips_*`` key after ``n_trips_negative_weight`` on the harmonised trips.
    """
    person_weight = pd.to_numeric(raw_persons["GEWICHT_P_ZENSUS"], errors="coerce")
    trip_weight = pd.to_numeric(raw_trips["GEWICHT_W_ZENSUS"], errors="coerce")
    n_trips_after_weight = int((trip_weight.notna() & (trip_weight >= 0)).sum())
    return {
        "n_persons_raw": int(len(raw_persons)),
        "n_persons_negative_weight": int(len(raw_persons)
                                         - (person_weight.notna() & (person_weight >= 0)).sum()),
        "n_persons_universe": int(len(persons)),
        "n_persons_away_from_home": int(persons["away_from_home"].sum()),
        "n_persons_missing_age": int(persons["age"].isna().sum()),
        "n_persons_unmapped_sex": int(persons["sex"].isna().sum()),
        "n_persons_no_kreis": int(persons["kreis"].isna().sum()),
        "n_trips_raw": int(len(raw_trips)),
        "n_trips_negative_weight": int(len(raw_trips) - n_trips_after_weight),
        "n_trips_person_dropped": int(n_trips_after_weight - len(trips)),
        "n_trips_universe": int(len(trips)),
        "n_trips_unknown_purpose": int((trips["purpose"] == UNKNOWN_PURPOSE).sum()),
        "n_trips_unknown_prev_purpose": int((trips["prev_purpose"] == UNKNOWN_PURPOSE).sum()),
        "n_trips_invalid_departure_time": int(trips["dep_min"].isna().sum()),
        "n_trips_invalid_arrival_time": int(trips["arr_min"].isna().sum()),
    }


# ----------------------------------------------------------------------------- person level
def person_level(persons: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
    """Attach the per-person day structure of ``trips`` to the harmonised ``persons`` frame.

    Adds ``n_trips`` (observed, from the trip table), ``n_<purpose>`` for all seven purposes,
    ``n_home_returns``, ``n_nonhome_acts``, ``first_from_home``, ``last_to_home``,
    ``first_dep_min``, ``last_arr_min``, ``mobile`` and ``pattern`` (the day spelled with
    :data:`PATTERN_LETTER`, starting with the origin purpose of the first trip, e.g. ``"HWH"``).

    A person without any trip is a legitimate immobile person, not missing data: all counts
    become 0, ``pattern`` becomes ``"H"``, and ``first_from_home``/``last_to_home`` become True
    (the person stayed at home).

    If ``persons`` already carries an ``n_trips`` column (the SrV-reported ``E_ANZ_WEGE2``), it
    is validated against the reconstructed count and a mismatch raises: a reference built on a
    trip table that does not reproduce the delivered trip count would be wrong in a way no
    downstream check could see.
    """
    _require_columns(persons, ["pid", "weight"], "persons")
    _require_columns(trips, ["pid", "seq", "purpose", "prev_purpose", "dep_min", "arr_min"],
                     "trips")
    # A trip whose person is not in ``persons`` would be silently dropped by the left merge
    # below and quietly shrink every trip-level metric, so it stops the build instead.
    orphaned = int((~trips["pid"].isin(set(persons["pid"]))).sum())
    if orphaned:
        raise ValueError(
            "%d of %d trips belong to a person that is not in the person frame; restrict the "
            "trips to the person universe before calling person_level" % (orphaned, len(trips)))

    t = trips.sort_values(["pid", "seq"]).copy()
    t["is_home"] = t["purpose"] == "home"
    grouped = t.groupby("pid")
    per = pd.DataFrame({
        "n_trips": grouped.size(),
        "n_home_returns": grouped["is_home"].sum(),
        "first_from_home": grouped["prev_purpose"].first().eq("home"),
        "last_to_home": grouped["purpose"].last().eq("home"),
        "first_dep_min": grouped["dep_min"].first(),
        "last_arr_min": grouped["arr_min"].last(),
    })
    # One pass over (pid, purpose) instead of one grouped pass per purpose: the model side of
    # the comparison runs this over millions of trips, where seven grouped passes are the
    # dominant cost. A purpose that occurs in no trip at all is absent from the crosstab and
    # gets an explicit zero column (never a missing one).
    counts_by_purpose = pd.crosstab(t["pid"], t["purpose"])
    for purpose in PURPOSES:
        per["n_%s" % purpose] = (counts_by_purpose[purpose] if purpose in counts_by_purpose.columns
                                 else 0)
    letters = t["purpose"].map(PATTERN_LETTER).fillna(PATTERN_LETTER[UNKNOWN_PURPOSE])
    origin = (grouped["prev_purpose"].first().map(PATTERN_LETTER)
              .fillna(PATTERN_LETTER[UNKNOWN_PURPOSE]))
    per["pattern"] = origin + t.assign(letter=letters).groupby("pid")["letter"].agg("".join)

    reported = persons["n_trips"] if "n_trips" in persons.columns else None
    base = persons.drop(columns=["n_trips"], errors="ignore")
    out = base.merge(per, left_on="pid", right_index=True, how="left")
    out = out.fillna({c: 0 for c in per.columns if c.startswith("n_")})
    out["pattern"] = out["pattern"].fillna(PATTERN_LETTER["home"])
    out["first_from_home"] = out["first_from_home"].fillna(True).astype(bool)
    out["last_to_home"] = out["last_to_home"].fillna(True).astype(bool)
    for column in [c for c in per.columns if c.startswith("n_")]:
        out[column] = out[column].astype(int)
    out["mobile"] = out["n_trips"] > 0
    out["n_nonhome_acts"] = out["n_trips"] - out["n_home_returns"]

    if reported is not None:
        mismatched = int((out["n_trips"].values != reported.values).sum())
        if mismatched:
            raise ValueError(
                "trip count mismatch: %d of %d persons have a reconstructed trip count that "
                "differs from the reported one (E_ANZ_WEGE2); the trip table does not describe "
                "the same reporting day as the person table. Most likely causes: trips dropped "
                "by the GEWICHT_W_ZENSUS exclusion in harmonise_srv (a person keeps its reported "
                "count but loses trips), a trip file from a different delivery, or a caller "
                "that filtered trips without filtering persons the same way"
                % (mismatched, len(out)))
    return out.reset_index(drop=True)


# ----------------------------------------------------------------------------- metrics
def _departure_hour_shares(trips: pd.DataFrame, purpose) -> dict:
    """Weighted departure-hour distribution of one purpose ("all" for every trip).

    Denominator: all trips of that purpose with a valid departure time, hour clipped into
    :data:`HOUR_CLIP`; only the hours in :data:`DEPARTURE_HOURS` are reported, so the returned
    shares do not sum to 1 when the purpose has departures outside 4..23.
    """
    label = "all" if purpose is None else purpose
    selected = trips if purpose is None else trips[trips["purpose"] == purpose]
    selected = selected[selected["dep_min"].notna()]
    total = float(selected["weight"].sum()) if len(selected) else 0.0
    if not total > 0:
        return {"dep_hour_share_%s_%d" % (label, h): float("nan") for h in DEPARTURE_HOURS}
    hour = np.clip(np.floor(selected["dep_min"].values / 60.0), *HOUR_CLIP)
    by_hour = pd.Series(selected["weight"].values).groupby(hour).sum()
    return {"dep_hour_share_%s_%d" % (label, h): float(by_hour.get(float(h), 0.0) / total)
            for h in DEPARTURE_HOURS}


def _work_activity_metrics(trips: pd.DataFrame) -> dict:
    """Duration of the work activity following a trip TO work: next departure minus arrival.

    A work activity is only measurable when the person made a further trip afterwards (the last
    activity of a reporting day has no end); durations outside ``[0, WORK_ACTIVITY_MAX_H]`` are
    excluded as implausible. Both exclusions shrink the denominator, so
    ``n_work_activities_measured`` is reported alongside the two metrics.
    """
    keys = {"mean_work_activity_h": float("nan"), "share_work_activities_lt_2h": float("nan"),
            "n_work_activities_measured": 0.0}
    if len(trips) == 0:
        return keys
    t = trips.sort_values(["pid", "seq"]).copy()
    t["next_dep_min"] = t.groupby("pid")["dep_min"].shift(-1)
    work = t[(t["purpose"] == "work") & t["next_dep_min"].notna() & t["arr_min"].notna()].copy()
    work["duration_h"] = (work["next_dep_min"] - work["arr_min"]) / 60.0
    work = work[(work["duration_h"] >= 0) & (work["duration_h"] <= WORK_ACTIVITY_MAX_H)]
    if len(work) == 0:
        return keys
    return {
        "mean_work_activity_h": weighted_mean(work["duration_h"], work["weight"]),
        "share_work_activities_lt_2h": _weighted_share(
            work["weight"], (work["duration_h"] < SHORT_WORK_ACTIVITY_H).values),
        "n_work_activities_measured": float(len(work)),
    }


def segment_metrics(per: pd.DataFrame, trips: pd.DataFrame, segment_label: str) -> dict:
    """Every plan-structure metric for one person subset, weighted.

    ``per`` is a :func:`person_level` frame restricted to the segment; ``trips`` may be the full
    harmonised trip table (it is restricted to the segment's persons here). Person-level metrics
    are weighted by the person weight, trip-level metrics by the trip weight. An empty segment
    yields ``NaN`` for every metric except the three counts ``n_persons_unweighted``,
    ``n_persons_weighted`` and ``n_work_activities_measured``, which are 0, rather than raising,
    so a segment that is empty in one universe still occupies its row.

    Metric groups (all names are stable; Task 10 compares the model side by name):

    * counts: ``n_persons_unweighted``, ``n_persons_weighted``
    * volume: ``mobility_rate``, ``trips_per_person``, ``trips_per_mobile_person``,
      ``nonhome_acts_per_mobile``, ``tours_per_mobile``, ``share_trips_0`` .. ``share_trips_7``,
      ``share_trips_8plus`` (weighted share of persons with that many trips)
    * purposes: ``participation_<p>`` (share of persons with >= 1 trip to ``p``),
      ``trips_per_person_<p>``, ``purpose_share_<p>`` (share of the segment's trips, denominator
      excluding unknown-purpose trips), ``share_trips_unknown_purpose``
    * structure: ``share_trips_home_based`` (trip starts or ends at home),
      ``share_mobile_first_from_home``, ``share_mobile_last_to_home``,
      ``share_mobile_odd_trip_count`` (an odd trip count cannot be a set of closed home tours),
      ``share_trips_followed_by_same_purpose`` (the next trip of the same person has the same
      harmonised purpose -- consecutive activities of one type; denominator is ALL trips of the
      segment, and a pair of unknown purposes never counts)
    * timing: ``mean_work_activity_h``, ``share_work_activities_lt_2h``,
      ``n_work_activities_measured``, ``dep_hour_share_<purpose|all>_<hour>`` for hours 4..23
    """
    _require_columns(per, ["pid", "weight", "n_trips", "mobile", "n_home_returns",
                           "n_nonhome_acts", "first_from_home", "last_to_home"]
                     + ["n_%s" % purpose for purpose in PURPOSES], "per")
    _require_columns(trips, ["pid", "seq", "weight", "purpose", "prev_purpose", "dep_min",
                             "arr_min"], "trips")
    weights = per["weight"].values if len(per) else np.zeros(0)
    mobile = per["mobile"].values.astype(bool) if len(per) else np.zeros(0, dtype=bool)
    mobile_weights = weights[mobile] if len(per) else np.zeros(0)
    t = trips[trips["pid"].isin(set(per["pid"]))] if len(per) else trips.iloc[0:0]

    row = {
        "segment": segment_label,
        "n_persons_unweighted": int(len(per)),
        "n_persons_weighted": float(np.asarray(weights, dtype=float).sum()),
        "mobility_rate": weighted_mean(mobile, weights),
        "trips_per_person": weighted_mean(per["n_trips"], weights),
        "trips_per_mobile_person": weighted_mean(per.loc[mobile, "n_trips"], mobile_weights),
        "nonhome_acts_per_mobile": weighted_mean(per.loc[mobile, "n_nonhome_acts"],
                                                 mobile_weights),
        "tours_per_mobile": weighted_mean(per.loc[mobile, "n_home_returns"], mobile_weights),
        "share_mobile_first_from_home": weighted_mean(per.loc[mobile, "first_from_home"],
                                                      mobile_weights),
        "share_mobile_last_to_home": weighted_mean(per.loc[mobile, "last_to_home"],
                                                   mobile_weights),
        "share_mobile_odd_trip_count": weighted_mean(
            (per.loc[mobile, "n_trips"] % 2 == 1) if len(per) else [], mobile_weights),
    }

    trip_counts = per["n_trips"].clip(upper=8).values if len(per) else np.zeros(0)
    for k in range(9):
        key = "share_trips_8plus" if k == 8 else "share_trips_%d" % k
        row[key] = _weighted_share(weights, trip_counts == k) if len(per) else float("nan")

    for purpose in PURPOSES:
        row["participation_%s" % purpose] = weighted_mean(
            per["n_%s" % purpose] > 0 if len(per) else [], weights)
        row["trips_per_person_%s" % purpose] = weighted_mean(
            per["n_%s" % purpose] if len(per) else [], weights)

    # Purpose shares: unknown-purpose trips are counted as trips everywhere else, but excluded
    # from this denominator so the seven shares sum to exactly 1.0 (see the module docstring).
    by_purpose = t.groupby("purpose")["weight"].sum() if len(t) else pd.Series(dtype=float)
    total_trip_weight = float(by_purpose.sum())
    known_trip_weight = float(by_purpose.drop(labels=[UNKNOWN_PURPOSE], errors="ignore").sum())
    for purpose in PURPOSES:
        row["purpose_share_%s" % purpose] = (float(by_purpose.get(purpose, 0.0)
                                                   / known_trip_weight)
                                             if known_trip_weight > 0 else float("nan"))
    row["share_trips_unknown_purpose"] = (
        float(by_purpose.get(UNKNOWN_PURPOSE, 0.0) / total_trip_weight)
        if total_trip_weight > 0 else float("nan"))

    home_based = ((t["purpose"] == "home") | (t["prev_purpose"] == "home")).values if len(t) \
        else np.zeros(0, dtype=bool)
    row["share_trips_home_based"] = _weighted_share(
        t["weight"].values if len(t) else np.zeros(0), home_based)

    if len(t):
        ordered = t.sort_values(["pid", "seq"])
        next_purpose = ordered.groupby("pid")["purpose"].shift(-1)
        repeated = ((ordered["purpose"] == next_purpose)
                    & (ordered["purpose"] != UNKNOWN_PURPOSE)).values
        row["share_trips_followed_by_same_purpose"] = _weighted_share(
            ordered["weight"].values, repeated)
    else:
        row["share_trips_followed_by_same_purpose"] = float("nan")

    row.update(_work_activity_metrics(t))
    row.update(_departure_hour_shares(t, None))
    for purpose in PURPOSES:
        row.update(_departure_hour_shares(t, purpose))
    return row


# ----------------------------------------------------------------------------- reference table
def segment_frames(per: pd.DataFrame):
    """The fixed segment set, as ``(label, person subset)`` pairs.

    Age bands, groups and sexes are always emitted (an empty one gets a NaN row rather than
    disappearing, so a consumer never has to guess whether a segment is missing or empty); Kreis
    segments follow the Kreise actually present in ``per``.
    """
    yield "all", per
    for band in AGE_LABELS:
        yield "age_%s" % band, per[per["age_band"] == band]
    for group in GROUPS:
        yield "group_%s" % group, per[per["group"] == group]
    for sex in SEXES:
        yield "sex_%s" % sex, per[per["sex"] == sex]
    for kreis in sorted(per["kreis"].dropna().unique()):
        yield "kreis_%s" % kreis, per[per["kreis"] == kreis]


def build_reference(persons: pd.DataFrame, trips: pd.DataFrame, *, universe: str) -> pd.DataFrame:
    """Long plan-structure reference table for one universe.

    ``persons``/``trips`` are :func:`harmonise_srv` output. ``universe`` is
    :data:`UNIVERSE_AT_HOME_ZERO` (primary: every delivered person, an away-from-home day
    counting as 0 trips) or :data:`UNIVERSE_AT_HOME_ONLY` (sensitivity: persons with
    ``E_ANZ_WEGE >= 0``, i.e. away-from-home persons excluded).

    Returns one row per (segment, metric) with columns :data:`REFERENCE_COLUMNS`;
    ``n_unweighted`` repeats the segment's unweighted person count on every one of its rows so a
    consumer can filter thin segments without a second lookup.
    """
    if universe not in UNIVERSES:
        raise ValueError("unknown universe %r; expected one of %s" % (universe, list(UNIVERSES)))
    _require_columns(persons, HARMONISED_PERSON_COLUMNS, "persons")
    _require_columns(trips, HARMONISED_TRIP_COLUMNS, "trips")

    if universe == UNIVERSE_AT_HOME_ONLY:
        selected = persons[persons["reported_at_home"].astype(bool)].copy()
    else:
        selected = persons.copy()
    selected_trips = trips[trips["pid"].isin(set(selected["pid"]))]
    logger.info("%s universe '%s': %d persons (%.1f%% of %d), %d trips", _LOG_TAG, universe,
                len(selected), 100.0 * len(selected) / len(persons) if len(persons) else
                float("nan"), len(persons), len(selected_trips))

    per = person_level(selected, selected_trips)
    rows = []
    for label, subset in segment_frames(per):
        metrics = segment_metrics(subset, selected_trips, label)
        n_unweighted = int(metrics["n_persons_unweighted"])
        for metric, value in metrics.items():
            if metric == "segment":
                continue
            rows.append({"universe": universe, "segment": label, "metric": metric,
                         "value": float(value), "n_unweighted": n_unweighted})
    return pd.DataFrame(rows, columns=REFERENCE_COLUMNS)


def load_plan_structure_reference(srv_dir) -> pd.DataFrame:
    """Load the committed plan-structure reference table from ``srv_dir``.

    ``srv_dir`` is the directory holding the committed SrV tables (normally
    ``eqasim-data/data/braunschweig/srv``). The provenance header is skipped (``comment="#"``),
    so the returned frame has exactly :data:`REFERENCE_COLUMNS`. Raises ``FileNotFoundError``
    naming the path and the regeneration script rather than returning an empty frame -- a
    consumer must never silently compare against nothing.
    """
    path = os.path.join(str(srv_dir), PLAN_STRUCTURE_TABLE)
    if not os.path.exists(path):
        raise FileNotFoundError(
            "Committed SrV plan-structure reference missing: %s. Regenerate with "
            "scripts/extract_srv_plan_structure.py (the raw SrV 2023 microdata is local-only "
            "and never committed)." % path)
    return pd.read_csv(path, comment="#")
