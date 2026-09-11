"""SrV 2023 participation aggregates on the at-home-or-mobile universe (issue #368).

Builds the two committed aggregates that the regional participation CONTROL TARGETS of the
participation-universe redesign are derived from:

* ``srv2023_work_by_employment_by_kreis.csv`` -- per home Kreis (plus the region total) the
  employed share of the 14+ universe and the two CONDITIONAL work-participation rates
  P(work leg | employed) and P(work leg | not employed). The target builder multiplies the
  employed margin of the blended ``target2026_employment_status_by_kreis.csv`` with these two
  conditionals, so that the ``work_by_employment`` control and the existing
  ``employment_status`` control cannot disagree on the employed margin (spec section 2.2).
* ``srv2023_education_by_age_by_kreis.csv`` -- per home Kreis (plus the region total) and per
  age band (0-5 / 6-17 / 18+) the education-participation rate. The three
  ``education_by_age`` controls read these conditionals directly; their age-range totals come
  from the census cell parquet, so no margin table is needed (spec section 2.3).

Why a NEW universe rather than the existing ``srv2023_participation_by_kreis.csv``: that table
is built over EVERY delivered person, including the persons who were away from home over the
whole reporting day (``E_ANZ_WEGE == -7``, 954 of 18,223 in the 2026-09-06 delivery). A
synthetic population has no such state -- every synthetic person exists and starts the day at
home -- so a control target taken from the all-persons universe asks the population to
reproduce a rate that is diluted by a state it cannot represent. This module therefore uses the
``at_home_only`` universe of :mod:`braunschweig.calibration.srv_plan_structure` (persons with
``E_ANZ_WEGE >= 0``), which is the user decision of 2026-09-05 recorded in the spec.

Definitions (SrV codebook ``SrV2023_Datenkodierung_SciUse.xlsx``):

* ``E_ANZ_WEGE`` (Personen): reported number of legs on the reporting day; ``-7`` marks a person
  who was away from home over the whole day (:data:`srv_plan_structure.AWAY_FROM_HOME_CODE`).
  Any OTHER negative value would be silently swallowed by an ``>= 0`` filter, so it raises.
* ``MITTL_WERKTAG`` (Personen): ``1`` marks an average-weekday (Tuesday-Thursday) person. Every
  person of the delivered file carries it; the guard raises on any other value rather than
  quietly redefining what "a day" means, mirroring the ``STICHTAG_WTAG`` guard of
  :func:`srv_plan_structure.harmonise_srv`.
* ``V_ERW`` (Personen): employment status, asked from age 14. Employed =
  :data:`srv_plan_structure.EMPLOYED_V_ERW` = ``(8, 9, 10, 11)`` (in Ausbildung/Lehre, full-time,
  part-time, marginally employed) -- decision Q5 of the spec, so that this aggregate and the
  MiD-side ``employment_status`` classes (which include ``in_ausbildung``) measure the same
  "employed". A person whose ``V_ERW`` is a missing code (``-8`` not surveyed, ``-10``
  implausible) is classed NOT employed; the count is logged and written into the committed
  file's header rather than absorbed silently.
* ``E_ZWECK_9`` (Wege): 9-class leg purpose. Work = :data:`WORK_E_ZWECK_9` = ``{1, 2}``
  (Arbeit, dienstlich/geschaeftlich), education = :data:`EDUCATION_E_ZWECK_9` = ``{3, 4}``
  (Kita/Schule, Ausbildung/Studium) -- the SAME sets as the existing
  ``scripts/build_srv_participation_aggregate.py``, so the only definitional change between the
  old and the new aggregate is the universe (and, for work, the employment conditioning).
  Participation = "the person has at least one leg of that purpose on the reporting day".
* ``GEWICHT_P_ZENSUS`` (Personen): person expansion weight to Zensus 2022 counts. The
  stratum-internal ``GEWICHT_P`` must never be used across strata (ADR-0055), the same
  convention as every other committed SrV aggregate.
* Household ``AGS`` (Haushalte) -> Kreis: the first five digits of the zero-padded eight-digit
  household AGS, joined to each person via ``HHNR``, via
  :func:`srv_distance_targets.kreis_from_ags` (never re-implemented, and never the survey design
  stratum ``ST_CODE``, which does not correspond 1:1 to a Kreis).

Rows: one row per Kreis PRESENT in the universe plus one region-total row coded
:data:`REGION_CODE`, so ``sum(kreis n_unweighted) == total n_unweighted`` holds by construction
(:func:`check_invariants` enforces it). Wolfsburg (03103) is NOT surveyed by SrV and therefore
has no row -- the same convention as ``srv2023_participation_by_kreis.csv``, whose consumer
``scripts/build_participation_target.py`` fills Wolfsburg from the region total as a documented
assumption. An absent expected Kreis is logged on every run so a delivery that loses another
Kreis is visible.

Known asymmetry to the MiD seed side (documented, not corrected here): the seed column defines
a work leg as a DIRECTLY RECORDED leg (MiD ``W_RBW == 0``), which excludes legs reconstructed
from a "regelmaessiger beruflicher Weg" block. The SrV Wege file has no ``W_RBW`` counterpart, so
no such filter can be applied on this side; the aggregate keeps the plain ``E_ZWECK_9``
definition of the existing committed participation table.

The module is a pure builder over already-loaded SrV person/leg/household frames: no synpp
dependency, no file-system access. ``scripts/extract_srv_participation_universe.py`` is the only
caller that touches the local-only raw microdata, and it owns the CLI and the provenance header.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_distance_targets import (WOLFSBURG_KREIS, ZGB_KREISE,
                                                            kreis_from_ags)
from braunschweig.calibration.srv_plan_structure import AWAY_FROM_HOME_CODE, EMPLOYED_V_ERW

logger = logging.getLogger(__name__)

_LOG_TAG = "[srv participation universe]"

WORK_BY_EMPLOYMENT_TABLE = "srv2023_work_by_employment_by_kreis.csv"
EDUCATION_BY_AGE_TABLE = "srv2023_education_by_age_by_kreis.csv"

WORK_E_ZWECK_9 = frozenset({1, 2})        # Arbeit, dienstlich/geschaeftlich
EDUCATION_E_ZWECK_9 = frozenset({3, 4})   # Kita/Schule, Ausbildung/Studium
MIN_AGE_WORK = 14                         # V_ERW is asked from 14; universe of the 14+ control
AVERAGE_WEEKDAY = 1                       # MITTL_WERKTAG
REGION_CODE = "03ZGB"                     # region-total row code, as in the sibling aggregates

# Age bands of the three education controls: (band name, minimum age years, maximum age years),
# bounds INCLUSIVE. 200 is an upper sentinel far above any plausible age, not a real bound.
EDUCATION_AGE_BANDS = (("education_0_5", 0, 5), ("education_6_17", 6, 17),
                       ("education_18plus", 18, 200))

LEVEL_KREIS = "kreis"
LEVEL_TOTAL = "total"

PERSON_COLUMNS = ["HHNR", "PNR", "V_ALTER", "V_ERW", "E_ANZ_WEGE", "GEWICHT_P_ZENSUS",
                  "MITTL_WERKTAG"]
LEG_COLUMNS = ["HHNR", "PNR", "E_ZWECK_9"]
HOUSEHOLD_COLUMNS = ["HHNR", "AGS"]

UNIVERSE_COLUMNS = ["pid", "kreis", "weight", "age", "employed"]
WORK_COLUMNS = ["code", "level", "n_unweighted", "n_employed_unweighted",
                "n_nonemployed_unweighted", "employed_share", "p_work_employed",
                "p_work_nonemployed"]
EDUCATION_COLUMNS = ["code", "level", "band", "n_unweighted", "p_education"]

WORK_SHARE_COLUMNS = ["employed_share", "p_work_employed", "p_work_nonemployed"]
EDUCATION_SHARE_COLUMNS = ["p_education"]


# --------------------------------------------------------------------------- small helpers
def _require_columns(frame: pd.DataFrame, required, name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError("%s frame is missing required column(s) %s" % (name, missing))


def _log_drop(n_dropped: int, n_before: int, step: str) -> None:
    """Log one universe-filter step as ``dropped/before (rate)``; warn when nothing survives.

    No exclusion class may fire silently (CLAUDE.md fallback-transparency rule): a filter that
    empties a non-empty input is almost always a broken join or a code-value mismatch, not a
    genuinely empty subpopulation, so it is surfaced at WARNING level.
    """
    kept = n_before - n_dropped
    rate = 100.0 * n_dropped / n_before if n_before else float("nan")
    message = "%s %s: %d/%d dropped (%.2f%%), %d kept"
    args = (_LOG_TAG, step, n_dropped, n_before, rate, kept)
    if n_before > 0 and kept == 0:
        logger.warning(message, *args)
    else:
        logger.info(message, *args)


def _person_ids(frame: pd.DataFrame) -> pd.Series:
    """Person key ``<HHNR>_<PNR>``, the same composite key the SrV files are joined on."""
    return frame["HHNR"].astype(str) + "_" + frame["PNR"].astype(str)


def _weighted_share(weight: pd.Series, mask) -> float:
    """Share of ``weight`` carried by the ``mask`` rows; NaN (never 0.0) for an empty base.

    NaN rather than 0.0 because an empty base means "not measurable here", which a consumer
    must not confuse with a measured zero rate.
    """
    weight = np.asarray(weight, dtype=float)
    total = weight.sum()
    if not total > 0:
        return float("nan")
    return float(weight[np.asarray(mask, dtype=bool)].sum() / total)


def persons_with_purpose(legs: pd.DataFrame, purpose_codes) -> tuple[set, dict]:
    """Person ids with at least one leg whose ``E_ZWECK_9`` is in ``purpose_codes``.

    Returns ``(pids, diagnostics)`` with ``n_legs_total`` and ``n_legs_missing_purpose`` (a
    non-numeric or negative ``E_ZWECK_9``, i.e. an SrV missing code): such legs can never
    contribute a participation, so their count is reported instead of being dropped silently.
    """
    _require_columns(legs, LEG_COLUMNS, "legs")
    purpose = pd.to_numeric(legs["E_ZWECK_9"], errors="coerce")
    n_missing = int((purpose.isna() | (purpose < 0)).sum())
    matching = legs.loc[purpose.isin(sorted(purpose_codes))]
    pids = set(_person_ids(matching))
    logger.info("%s legs with E_ZWECK_9 in %s: %d/%d, held by %d persons (%d legs carry a "
                "missing purpose code and can match nothing)", _LOG_TAG, sorted(purpose_codes),
                len(matching), len(legs), len(pids), n_missing)
    return pids, {"n_legs_total": int(len(legs)), "n_legs_missing_purpose": n_missing}


# --------------------------------------------------------------------------- universe
def _employment_flag(employment_code: pd.Series, is_unreadable) -> pd.array:
    """``employed`` as a NULLABLE boolean: ``pd.NA`` where ``V_ERW`` could not be read.

    A plain ``bool`` column cannot express "this person did not answer": ``isin`` returns False
    for a missing or negative code, which reads as "not employed" and puts a non-answer into the
    denominator of every employed share computed from it (ADR-0117). The nullable dtype makes the
    non-answer visible to every consumer instead, and
    :func:`build_work_by_employment_aggregate` -- the only place the flag is USED -- drops those
    rows from its own universe with a logged count.
    """
    flag = pd.array(employment_code.isin(EMPLOYED_V_ERW).values, dtype="boolean")
    flag[is_unreadable] = pd.NA
    return flag


def prepare_universe_persons(persons: pd.DataFrame,
                             households: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """The at-home-or-mobile person universe with its Kreis, weight, age and employed flag.

    ``persons`` must carry :data:`PERSON_COLUMNS`, ``households`` :data:`HOUSEHOLD_COLUMNS`.

    Universe guards (raise ``ValueError``, never warn-and-continue -- an unexpected code value
    would redefine the universe, and a control target built on a silently redefined universe is
    worse than no target):

    1. every ``MITTL_WERKTAG`` equals :data:`AVERAGE_WEEKDAY`;
    2. every negative ``E_ANZ_WEGE`` equals :data:`srv_plan_structure.AWAY_FROM_HOME_CODE`, so
       that the ``>= 0`` universe filter drops exactly the away-from-home persons and nothing
       else;
    3. every resolved Kreis is one of the eight :data:`srv_distance_targets.ZGB_KREISE`.

    Exclusions, applied in this order and each counted and logged as ``dropped/before (rate)``:

    * ``missing_weight`` -- ``GEWICHT_P_ZENSUS`` missing or negative (measured over all input
      persons);
    * ``away_from_home`` -- ``E_ANZ_WEGE == AWAY_FROM_HOME_CODE`` (measured over the persons
      surviving the weight filter);
    * ``missing_kreis`` -- the person's ``HHNR`` has no household row, or the household ``AGS``
      is missing/sentinel so :func:`srv_distance_targets.kreis_from_ags` returns ``NaN``
      (measured over the persons surviving the away-from-home filter; the two sub-reasons are
      logged separately);
    * ``n_unreadable_employment_code`` -- ``V_ERW`` missing or negative (``-8`` "nicht erhoben",
      ``-10`` "unplausibel"), measured over the persons surviving the Kreis filter. Such a person
      answered NOTHING about their employment; before ADR-0117 they were silently classed NOT
      employed, which put a non-answer into the denominator of the employed share this universe's
      control target is built from.

    Returns ``(universe, diagnostics)``. ``universe`` has exactly :data:`UNIVERSE_COLUMNS`:
    ``pid`` (``<HHNR>_<PNR>``), ``kreis`` (5-digit key), ``weight`` (``GEWICHT_P_ZENSUS``),
    ``age`` (``V_ALTER``, ``NaN`` for a negative/missing sentinel) and ``employed``
    (``V_ERW in EMPLOYED_V_ERW``, over the persons whose code could be read). ``diagnostics``
    carries the four exclusion counts above plus ``n_persons_total`` (input rows), ``n_universe``
    (surviving rows) and ``n_missing_age`` (persons whose ``V_ALTER`` is a sentinel, measured on
    the universe) -- meant to be written verbatim into the committed CSV's provenance header, so
    the exclusion counts live IN the committed file.
    """
    _require_columns(persons, PERSON_COLUMNS, "persons")
    _require_columns(households, HOUSEHOLD_COLUMNS, "households")

    n_persons_total = len(persons)

    weekday = pd.to_numeric(persons["MITTL_WERKTAG"], errors="coerce")
    foreign_weekday = weekday[weekday != AVERAGE_WEEKDAY]   # NaN != 1 is True, so NaN is caught
    if len(foreign_weekday) > 0:
        raise ValueError(
            "universe drift: %d of %d persons have MITTL_WERKTAG != %d (found %s, plus %d "
            "non-numeric/missing); these aggregates are defined on the average-weekday "
            "(Tuesday-Thursday) delivery only"
            % (len(foreign_weekday), n_persons_total, AVERAGE_WEEKDAY,
               sorted(foreign_weekday.dropna().unique().tolist()),
               int(foreign_weekday.isna().sum())))

    # A non-numeric E_ANZ_WEGE coerces to NaN, and NaN passes BOTH "< 0" and "== -7" as False:
    # such a person would neither raise nor be counted as away-from-home, and would silently
    # stay in the universe with an unknown reporting-day state. That is exactly the uncounted
    # row this module exists to make impossible, so NaN raises alongside the unexpected
    # negatives (controller ruling R16).
    n_legs_reported = pd.to_numeric(persons["E_ANZ_WEGE"], errors="coerce")
    unexpected_negative = n_legs_reported[(n_legs_reported < 0)
                                          & (n_legs_reported != AWAY_FROM_HOME_CODE)]
    n_not_numeric = int(n_legs_reported.isna().sum())
    if len(unexpected_negative) > 0 or n_not_numeric > 0:
        raise ValueError(
            "universe drift: %d of %d persons have a negative E_ANZ_WEGE other than %d (found "
            "%s) and %d have a missing or non-numeric E_ANZ_WEGE; the 'E_ANZ_WEGE >= 0' "
            "universe filter would drop the former and KEEP the latter, in both cases without "
            "counting them as away-from-home persons"
            % (len(unexpected_negative), n_persons_total, AWAY_FROM_HOME_CODE,
               sorted(unexpected_negative.dropna().unique().tolist()), n_not_numeric))

    weight = pd.to_numeric(persons["GEWICHT_P_ZENSUS"], errors="coerce")
    valid_weight = weight.notna() & (weight >= 0)
    n_missing_weight = int((~valid_weight).sum())
    _log_drop(n_missing_weight, n_persons_total,
              "weight validity (GEWICHT_P_ZENSUS present and >= 0)")
    working = persons[valid_weight].copy()

    n_before_away = len(working)
    away = pd.to_numeric(working["E_ANZ_WEGE"], errors="coerce") == AWAY_FROM_HOME_CODE
    n_away_from_home = int(away.sum())
    _log_drop(n_away_from_home, n_before_away,
              "at-home-or-mobile universe (E_ANZ_WEGE != %d, i.e. the person was at home at "
              "some point on the reporting day)" % AWAY_FROM_HOME_CODE)
    working = working[~away].copy()

    kreis_by_household = households[["HHNR", "AGS"]].copy()
    kreis_by_household["kreis"] = kreis_from_ags(kreis_by_household["AGS"])
    known_households = set(kreis_by_household["HHNR"])
    n_before_kreis = len(working)
    working = working.merge(kreis_by_household[["HHNR", "kreis"]], on="HHNR", how="left",
                            validate="m:1")
    has_household = working["HHNR"].isin(known_households)
    n_no_household = int((~has_household).sum())
    n_invalid_ags = int((has_household & working["kreis"].isna()).sum())
    n_missing_kreis = n_no_household + n_invalid_ags
    _log_drop(n_missing_kreis, n_before_kreis,
              "household-to-Kreis resolution (no_household=%d, invalid_ags=%d)"
              % (n_no_household, n_invalid_ags))
    working = working[working["kreis"].notna()].copy()

    outside_zgb = sorted(set(working.loc[~working["kreis"].isin(ZGB_KREISE), "kreis"]))
    if outside_zgb:
        raise ValueError(
            "universe drift: %d persons resolve to a Kreis outside the 8 surveyed ZGB Kreise "
            "(found %s, expected a subset of %s); the delivery is a Braunschweig + RGB sample, "
            "so a foreign Kreis means the household AGS join is wrong"
            % (int((~working["kreis"].isin(ZGB_KREISE)).sum()), outside_zgb, list(ZGB_KREISE)))

    age = pd.to_numeric(working["V_ALTER"], errors="coerce")

    # A person whose V_ERW cannot be read (missing, or a negative SrV code such as -8 "nicht
    # erhoben" / -10 "unplausibel") answered NOTHING about their employment, so `employed` is
    # UNKNOWN for them rather than False. Before ADR-0117 `isin(EMPLOYED_V_ERW)` silently made it
    # False, which put a non-answer into the DENOMINATOR of the employed share the work control is
    # built from. They stay in the UNIVERSE -- their education participation is perfectly readable
    # and the education control has nothing to do with employment -- and are dropped only where
    # the employed flag is actually used (build_work_by_employment_aggregate), which is the
    # narrowest place that fixes the defect.
    employment_code = pd.to_numeric(working["V_ERW"], errors="coerce")
    is_unreadable_employment = (employment_code.isna() | (employment_code < 0)).values
    n_unreadable_employment_code = int(is_unreadable_employment.sum())

    universe = pd.DataFrame({
        "pid": _person_ids(working).values,
        "kreis": working["kreis"].values,
        "weight": pd.to_numeric(working["GEWICHT_P_ZENSUS"], errors="coerce").astype(float).values,
        "age": age.where(age >= 0).values,
        "employed": _employment_flag(employment_code, is_unreadable_employment),
    })[UNIVERSE_COLUMNS].reset_index(drop=True)

    n_missing_age = int(universe["age"].isna().sum())
    logger.info("%s universe: %d/%d persons (%.2f%%); %d have no valid age (V_ALTER sentinel); "
                "%d carry employed=<NA> because their V_ERW could not be read and are dropped by "
                "the WORK control only (ADR-0117: a non-answer is not an employment status)",
                _LOG_TAG, len(universe), n_persons_total,
                100.0 * len(universe) / n_persons_total if n_persons_total else float("nan"),
                n_missing_age, n_unreadable_employment_code)
    missing_kreise = [code for code in ZGB_KREISE if code not in set(universe["kreis"])]
    if missing_kreise:
        logger.info("%s ZGB Kreise with no person in this universe: %s (%s is Wolfsburg, which "
                    "SrV does not survey, and is expected here; any OTHER code means the "
                    "delivery changed and is rejected by check_kreis_coverage)",
                    _LOG_TAG, missing_kreise, WOLFSBURG_KREIS)

    diagnostics = {
        "n_persons_total": n_persons_total,
        "missing_weight": n_missing_weight,
        "away_from_home": n_away_from_home,
        "missing_kreis": n_missing_kreis,
        "n_universe": int(len(universe)),
        "n_missing_age": n_missing_age,
        "n_unreadable_employment_code": n_unreadable_employment_code,
    }
    return universe, diagnostics


# --------------------------------------------------------------------------- work aggregate
def build_work_by_employment_aggregate(persons: pd.DataFrame, legs: pd.DataFrame,
                                       households: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Employed share and the two conditional work-participation rates per Kreis, ages 14+.

    Universe: :func:`prepare_universe_persons` narrowed to ``age >= MIN_AGE_WORK`` -- the
    universe of the existing ``employment_status`` Kreis control and the ages at which SrV asks
    ``V_ERW`` at all. A person with no valid age falls out here too (counted as
    ``n_missing_age``, since ``NaN >= 14`` is False) rather than being assigned to either class.

    Returns ``(table, diagnostics)``. ``table`` has :data:`WORK_COLUMNS`, one
    ``level == "kreis"`` row per Kreis present in the universe plus one ``level == "total"`` row
    coded :data:`REGION_CODE` over exactly the union of those Kreis rows:

    * ``n_unweighted``, ``n_employed_unweighted``, ``n_nonemployed_unweighted`` -- unweighted
      person counts (the last two partition the first);
    * ``employed_share`` -- ``GEWICHT_P_ZENSUS``-weighted employed share of the 14+ universe;
    * ``p_work_employed`` / ``p_work_nonemployed`` -- weighted share of the employed / not
      employed persons with at least one work leg (``E_ZWECK_9`` in :data:`WORK_E_ZWECK_9`).
      ``NaN``, never 0.0, when the conditioning class is empty in that Kreis.

    ``diagnostics`` extends the universe diagnostics with ``n_below_min_age``, ``n_age_14plus``,
    ``n_legs_total`` and ``n_legs_missing_purpose``.
    """
    universe, diagnostics = prepare_universe_persons(persons, households)
    n_universe = len(universe)
    adults = universe[universe["age"] >= MIN_AGE_WORK].copy()
    n_below_min_age = n_universe - len(adults)
    _log_drop(n_below_min_age, n_universe,
              "work-control universe (age >= %d; persons without a valid age are dropped here "
              "too)" % MIN_AGE_WORK)

    # ADR-0117: a person whose V_ERW could not be read carries employed = pd.NA. They answered
    # nothing about their employment, so they belong in NEITHER the employed nor the not-employed
    # group; leaving them in would deflate employed_share by the non-response rate and would put
    # non-answers into p_work_nonemployed. They stay in the universe for the education control,
    # which does not read this flag.
    n_before_readable = len(adults)
    unknown_employment = adults["employed"].isna()
    n_unknown_employment = int(unknown_employment.sum())
    _log_drop(n_unknown_employment, n_before_readable,
              "readable employment status (V_ERW present and non-negative; a missing or negative "
              "code is a non-answer, not 'not employed')")
    adults = adults[~unknown_employment].copy()

    work_pids, leg_diagnostics = persons_with_purpose(legs, WORK_E_ZWECK_9)
    adults["has_work"] = adults["pid"].isin(work_pids)

    rows = [_work_row(LEVEL_KREIS, code, group)
            for code, group in sorted(adults.groupby("kreis"), key=lambda item: item[0])]
    rows.append(_work_row(LEVEL_TOTAL, REGION_CODE, adults))
    table = pd.DataFrame(rows, columns=WORK_COLUMNS)

    diagnostics.update(n_below_min_age=n_below_min_age,
                       n_unknown_employment_status=n_unknown_employment,
                       n_age_14plus=int(len(adults)), **leg_diagnostics)
    total = table[table["level"] == LEVEL_TOTAL].iloc[0]
    logger.info("%s work-by-employment table: %d rows (%d Kreis + 1 %s); %s row "
                "(n=%d): employed_share=%.4f, p_work_employed=%.4f, p_work_nonemployed=%.4f",
                _LOG_TAG, len(table), len(table) - 1, REGION_CODE, REGION_CODE,
                int(total["n_unweighted"]), total["employed_share"], total["p_work_employed"],
                total["p_work_nonemployed"])
    return table, diagnostics


def _work_row(level: str, code: str, group: pd.DataFrame) -> dict:
    """One row of the work-by-employment table (see :func:`build_work_by_employment_aggregate`)."""
    employed = group["employed"].astype(bool)
    weight = group["weight"].astype(float)
    return {
        "code": code,
        "level": level,
        "n_unweighted": int(len(group)),
        "n_employed_unweighted": int(employed.sum()),
        "n_nonemployed_unweighted": int((~employed).sum()),
        "employed_share": _weighted_share(weight, employed),
        "p_work_employed": _weighted_share(weight[employed.values],
                                           group.loc[employed.values, "has_work"]),
        "p_work_nonemployed": _weighted_share(weight[~employed.values],
                                              group.loc[~employed.values, "has_work"]),
    }


# --------------------------------------------------------------------------- education aggregate
def build_education_by_age_aggregate(persons: pd.DataFrame, legs: pd.DataFrame,
                                     households: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Education-participation rate per Kreis and age band on the at-home-or-mobile universe.

    Universe: :func:`prepare_universe_persons` (all ages). Each person falls into exactly one
    band of :data:`EDUCATION_AGE_BANDS` (bounds inclusive); a person with no valid age falls
    into NO band and is counted as ``n_missing_age``.

    Returns ``(table, diagnostics)``. ``table`` has :data:`EDUCATION_COLUMNS`, one row per
    (Kreis present in the universe) x band plus one ``level == "total"`` row per band coded
    :data:`REGION_CODE`. A band with no person in a Kreis is still emitted, with
    ``n_unweighted == 0`` and a ``NaN`` share -- never dropped, so a consumer never has to guess
    whether a band is missing or empty. ``p_education`` is the ``GEWICHT_P_ZENSUS``-weighted
    share of the band's persons with at least one education leg (``E_ZWECK_9`` in
    :data:`EDUCATION_E_ZWECK_9`).

    ``diagnostics`` extends the universe diagnostics with ``n_legs_total``,
    ``n_legs_missing_purpose`` and one ``n_band_<band>`` count per band. The number of persons
    in NO band is ``n_universe - sum(n_band_*)`` and its two causes are logged separately (no
    valid ``V_ALTER`` vs a valid age above the top band's upper bound); the split is not a
    separate diagnostics key because it is already derivable from ``n_missing_age`` and the band
    counts, all of which the committed header carries.
    """
    universe, diagnostics = prepare_universe_persons(persons, households)
    education_pids, leg_diagnostics = persons_with_purpose(legs, EDUCATION_E_ZWECK_9)
    universe = universe.copy()
    universe["has_education"] = universe["pid"].isin(education_pids)

    codes = sorted(set(universe["kreis"]))
    rows = []
    for code in codes:
        in_kreis = universe[universe["kreis"] == code]
        rows.extend(_education_rows(LEVEL_KREIS, code, in_kreis))
    rows.extend(_education_rows(LEVEL_TOTAL, REGION_CODE, universe))
    table = pd.DataFrame(rows, columns=EDUCATION_COLUMNS)

    diagnostics.update(**leg_diagnostics)
    for band, _, _ in EDUCATION_AGE_BANDS:
        total = table[(table["level"] == LEVEL_TOTAL) & (table["band"] == band)].iloc[0]
        diagnostics["n_band_%s" % band] = int(total["n_unweighted"])
        logger.info("%s education band %s: n=%d, p_education=%.4f", _LOG_TAG, band,
                    int(total["n_unweighted"]), total["p_education"])
    # A person can fall into no band for TWO reasons: no valid V_ALTER, or a valid age outside
    # every band (only possible above the top band's sentinel upper bound). The two are counted
    # separately so the warning names the actual cause instead of asserting the more likely one.
    n_no_band = int(len(universe)) - sum(diagnostics["n_band_%s" % band]
                                         for band, _, _ in EDUCATION_AGE_BANDS)
    if n_no_band:
        top_bound = max(maximum for _, _, maximum in EDUCATION_AGE_BANDS)
        n_above_top_band = n_no_band - diagnostics["n_missing_age"]
        logger.warning("%s %d/%d universe persons fall into no education age band: %d have no "
                       "valid V_ALTER and %d have a valid age outside every band (i.e. above "
                       "the top band's upper bound of %d years); they are in no band row and "
                       "in no band total", _LOG_TAG, n_no_band, len(universe),
                       diagnostics["n_missing_age"], n_above_top_band, top_bound)
    logger.info("%s education-by-age table: %d rows (%d Kreis x %d bands + %d total bands)",
                _LOG_TAG, len(table), len(codes), len(EDUCATION_AGE_BANDS),
                len(EDUCATION_AGE_BANDS))
    return table, diagnostics


def _education_rows(level: str, code: str, group: pd.DataFrame) -> list:
    """The three age-band rows of one code (see :func:`build_education_by_age_aggregate`)."""
    rows = []
    for band, minimum_age, maximum_age in EDUCATION_AGE_BANDS:
        in_band = group[(group["age"] >= minimum_age) & (group["age"] <= maximum_age)]
        rows.append({
            "code": code,
            "level": level,
            "band": band,
            "n_unweighted": int(len(in_band)),
            "p_education": _weighted_share(in_band["weight"].astype(float),
                                           in_band["has_education"]),
        })
    return rows


# --------------------------------------------------------------------------- invariants
def check_invariants(work_table: pd.DataFrame, education_table: pd.DataFrame) -> None:
    """Raise ``ValueError`` on anything that would silently corrupt the two committed targets.

    1. Both tables carry exactly their expected columns.
    2. Each table has exactly one ``total`` row (per band, for the education table) coded
       :data:`REGION_CODE`, and every other row is a ``kreis`` row.
    3. ``n_unweighted == n_employed_unweighted + n_nonemployed_unweighted`` on every work row --
       the two classes must partition the row, otherwise the conditional rates are conditioned
       on something other than the stated universe.
    4. The Kreis rows sum to the total row's ``n_unweighted`` (per band, for the education
       table): the total row is DEFINED as the union of the Kreis rows, and a target builder
       that falls back to the total row for Wolfsburg relies on it.
    5. Every share is ``NaN`` or inside ``[0, 1]``.

    Kreis COVERAGE is deliberately not checked here -- see :func:`check_kreis_coverage`, which
    the extraction script calls in addition to this function.
    """
    if list(work_table.columns) != WORK_COLUMNS:
        raise ValueError("work table columns %s != expected %s"
                         % (list(work_table.columns), WORK_COLUMNS))
    if list(education_table.columns) != EDUCATION_COLUMNS:
        raise ValueError("education table columns %s != expected %s"
                         % (list(education_table.columns), EDUCATION_COLUMNS))

    bands = [band for band, _, _ in EDUCATION_AGE_BANDS]
    found_bands = sorted(set(education_table["band"]))
    if found_bands != sorted(bands):
        raise ValueError("education table bands %s != expected %s" % (found_bands, sorted(bands)))

    _check_levels(work_table, "work")
    for band in bands:
        _check_levels(education_table[education_table["band"] == band],
                      "education band %s" % band)

    partition = (work_table["n_employed_unweighted"] + work_table["n_nonemployed_unweighted"])
    mismatch = work_table[partition != work_table["n_unweighted"]]
    if len(mismatch):
        raise ValueError(
            "work table: n_unweighted != n_employed_unweighted + n_nonemployed_unweighted on "
            "%d row(s), e.g. %s" % (len(mismatch), mismatch.head().to_dict("records")))

    _check_kreis_sum(work_table, "work")
    for band in bands:
        _check_kreis_sum(education_table[education_table["band"] == band],
                         "education band %s" % band)

    for table, columns, name in ((work_table, WORK_SHARE_COLUMNS, "work"),
                                 (education_table, EDUCATION_SHARE_COLUMNS, "education")):
        for column in columns:
            values = table[column]
            outside = values[values.notna() & ((values < -1e-12) | (values > 1 + 1e-12))]
            if len(outside):
                raise ValueError("%s table: %s outside [0, 1] on %d row(s), e.g. %s"
                                 % (name, column, len(outside),
                                    sorted(outside.unique().tolist())[:5]))


def check_kreis_coverage(work_table: pd.DataFrame, education_table: pd.DataFrame,
                         expected_kreise=ZGB_KREISE) -> None:
    """Raise ``ValueError`` if a Kreis that SrV surveys has no row in either table.

    Both builders emit one row per Kreis PRESENT in the universe, which keeps
    ``sum(kreis) == total`` exact but means a delivery that lost a whole Kreis would produce a
    table that is one row shorter and still satisfies every invariant of
    :func:`check_invariants`. Wolfsburg (:data:`srv_distance_targets.WOLFSBURG_KREIS`) is the
    one legitimately absent code -- SrV does not survey it -- so exactly that code may be
    missing and nothing else.

    Separate from :func:`check_invariants` because coverage is a property of a FULL delivery,
    not of the builders: a caller working on a subset of Kreise (a unit-test fixture, a
    single-Kreis diagnostic) passes a narrower ``expected_kreise`` or does not call this at all,
    whereas ``scripts/extract_srv_participation_universe.py`` always calls it with the full
    :data:`srv_distance_targets.ZGB_KREISE`.
    """
    expected = {code for code in expected_kreise if code != WOLFSBURG_KREIS}
    for table, name in ((work_table, "work"), (education_table, "education")):
        present = set(table.loc[table["level"] == LEVEL_KREIS, "code"])
        absent = sorted(expected - present)
        if absent:
            raise ValueError(
                "%s table: expected a kreis row for every surveyed ZGB Kreis but %s "
                "%s missing (present: %s; %s is Wolfsburg, which SrV does not survey, and is "
                "the only code allowed to be absent). A delivery that lost a Kreis must not "
                "silently produce a shorter table."
                % (name, absent, "is" if len(absent) == 1 else "are", sorted(present),
                   WOLFSBURG_KREIS))
        unexpected = sorted(present - set(expected_kreise))
        if unexpected:
            raise ValueError(
                "%s table: kreis row(s) %s are not in the expected Kreis set %s"
                % (name, unexpected, list(expected_kreise)))


def _check_levels(table: pd.DataFrame, name: str) -> None:
    totals = table[table["level"] == LEVEL_TOTAL]
    if len(totals) != 1:
        raise ValueError("%s: expected exactly 1 %r row, found %d"
                         % (name, LEVEL_TOTAL, len(totals)))
    if totals["code"].iloc[0] != REGION_CODE:
        raise ValueError("%s: the %r row must be coded %r, found %r"
                         % (name, LEVEL_TOTAL, REGION_CODE, totals["code"].iloc[0]))
    unexpected = sorted(set(table["level"]) - {LEVEL_KREIS, LEVEL_TOTAL})
    if unexpected:
        raise ValueError("%s: unexpected level value(s) %s" % (name, unexpected))


def _check_kreis_sum(table: pd.DataFrame, name: str) -> None:
    kreis_sum = int(table.loc[table["level"] == LEVEL_KREIS, "n_unweighted"].sum())
    total = int(table.loc[table["level"] == LEVEL_TOTAL, "n_unweighted"].iloc[0])
    if kreis_sum != total:
        raise ValueError(
            "%s: the Kreis rows sum to n_unweighted %d but the %s row has %d; the total row is "
            "defined as exactly the union of the Kreis rows"
            % (name, kreis_sum, REGION_CODE, total))
