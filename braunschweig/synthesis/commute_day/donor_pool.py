"""Home-office-day donor pool: MiD attributes + trip chains (ADR-0104, issue #244, Phase B Task 2).

Pure builder over raw MiD person/household/trip frames (raw MiD 2023 microdata is server-only;
the MiD variable glossary this module relies on is documented in the module docstring of
:mod:`braunschweig.calibration.commute_day_state_reference`, which this module imports from).
No synpp stage, no file I/O -- exercised only against tiny synthetic frames in
``tests/test_commute_day_donor_pool.py``. The synpp stage that calls this pure builder against
the real MiD delivery is a later Phase B task (see the package docstring,
``home_office_donors_stage``).

The donor UNIVERSE is MiD persons who worked AT HOME on their reporting day (weekday, worked,
``starb2 == at home`` -- see :func:`select_home_office_day_donors`). Two outputs are built per
donor, both keyed by ``donor_id = HP_ID`` (the MiD person's own unique identifier across the
whole delivery, distinct from the household-relative ``(H_ID, P_ID)`` pair used to join the raw
Wege/trip file):

* :func:`donor_attributes` -- one row per donor: the demographic + household attributes the
  plan-replacement step (a later Phase B task) needs to match a synthetic worker to a donor day.
* :func:`donor_trips` -- the donor's own trip chain in the ``synthesis.population.trips``
  CONTRACT (:data:`braunschweig.popsim.trips_stage.CONTRACT`), built with
  :func:`braunschweig.popsim.trips.build_validated_trip_table` using the keyword arguments
  :func:`braunschweig.popsim.trips_stage.run` passes -- ``resample=True``, the escort /
  round-trip flags, ``random_seed``, and the three plan-structure arguments
  ``exclude_rbw_legs`` / ``drop_leading_arrive_home_leg`` / ``dwell_model`` (issues #366, #367,
  wired here by issue #374) -- ruling R2, but WITHOUT ``trips_stage.run``'s per-person
  departure-time jitter step: the jitter is applied ONCE later, in the plan-replacement step,
  keyed by the RECEIVING synthetic person, not the donor (applying it here would jitter the same
  donor's day twice once it is copied onto a worker).

The donor UNIVERSE is additionally narrowed by :func:`filter_donor_diaries`, which removes
donors whose reporting day cannot serve as a replacement day at all: an uncollected diary
(``anzwege1`` 803/804), a public-holiday diary (``feiertag == 1``) and a diary consisting only of
rbW summary legs. Those are the SAME three exclusions
:mod:`braunschweig.popsim.diary_plan_match` applies to a synthetic person's plan source; see
:func:`filter_donor_diaries` for the one deliberate difference (803 is dropped regardless of
``mobil``).

``household_size`` (MiD ``H_GR``) is carried through UNBINNED: the class binning
(``household_size_class``) is the responsibility of the later matching module, which must apply
the IDENTICAL binning to both the donor and the worker side (ruling R1) -- binning it here would
risk the two sides drifting apart.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration.commute_day_state_reference import (
    MID_AT_HOME,
    MID_CHILD_MAX_AGE,
    MID_ESCORT_ACTIVE,
    MID_MODULE,
    MID_SEX_FEMALE,
    MID_WEEKDAY,
    MID_WORKED_ON_DAY,
    WORK_TRIP_LENGTH_COLUMN,
    classify_commute_distance,
    clean_mid_commute_distance_km,
    first_work_trip_length_km,
)
from braunschweig.constants import ROUTED_DETOUR_FACTOR as DETOUR_FACTOR
from braunschweig.popsim.chain_matching import derive_age_class
from braunschweig.popsim.diary_facts import compute_diary_facts
from braunschweig.popsim.diary_plan_match import MID_HOLIDAY, NO_DIARY_CODES
from braunschweig.popsim.trips import build_validated_trip_table
from braunschweig.popsim.trips_stage import (
    CONTRACT,
    DEFAULT_CLOSURE_DWELL_MIN_OBS,
    build_closure_dwell_model,
)

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day donors]"

# MiD HP_SEX -> the eqasim-style sex label used by the matching module. Code MID_SEX_FEMALE (2)
# is imported from commute_day_state_reference (the single committed source for that code); the
# male code (1) is MiD-codeplan-documented but not otherwise referenced elsewhere in the
# repository, so it is defined locally rather than adding an unused constant to the reference
# module. Codes 3 (diverse) and 9 (no answer) map to NaN -- counted, never guessed.
MID_SEX_MALE = 1
SEX_LABEL_BY_HP_SEX = {MID_SEX_MALE: "male", MID_SEX_FEMALE: "female"}
#: Label substituted for a NaN ``sex`` ONLY on the persons frame handed to the trip-table
#: resample (see :func:`donor_trips`). The attributes frame keeps NaN, which is what every other
#: consumer sees; this label exists because eqasim's ``statistical_matching`` sorts the union of
#: the distinct values of each matching column, and sorting a column that mixes ``float('nan')``
#: with strings raises ``TypeError: '<' not supported between instances of 'float' and 'str'``.
SEX_LABEL_UNKNOWN = "unknown"

#: Values of ``distance_source`` on the attributes frame (see :func:`donor_attributes`).
DISTANCE_SOURCE_P_ARB_ENTF = "P_ARB_ENTF"
DISTANCE_SOURCE_TRIP_LENGTH = "trip_length"
DISTANCE_SOURCE_UNKNOWN = "unknown"

#: ``distance_class`` value for a donor with neither a valid ``P_ARB_ENTF`` nor a usable work
#: trip. A LITERAL string, never ``None``/``NaN``: the later matching module (and
#: :mod:`braunschweig.synthesis.commute_day.state`, which already keys its own unknown class this
#: way) treats ``"unknown"`` as "matches any class" -- a ``None``/``NaN`` key would instead be
#: silently dropped by any ``groupby``/``merge`` the matching module performs on this column.
DISTANCE_CLASS_UNKNOWN = "unknown"

#: eqasim trip purposes marking a leg that ARRIVES AT (or DEPARTS FROM) a FIXED activity the
#: receiving person must be able to anchor to a location of their own. Read from the BUILT donor
#: trip table (:func:`donor_trips`), never from the raw MiD ``W_ZWECK`` codes, so the flags follow
#: the same purpose mapping the day itself was built with -- including
#: ``escort_passive_education``, which relabels a passive escort leg (``W_ZWECK`` 13) to
#: ``"education"``.
EDUCATION_PURPOSE = "education"
WORK_PURPOSE = "work"


def _require_columns(frame: pd.DataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what} is missing the required column(s) {missing} "
                         f"(present: {sorted(frame.columns)})")


def _count_not_in_module(donors: pd.DataFrame) -> int:
    """Count selected donors whose ``M_HOFF`` is not :data:`MID_MODULE`.

    Shared between :func:`select_home_office_day_donors` (which logs it) and
    :func:`build_home_office_donor_pool` (which reports it in ``diagnostics``), so both read the
    identical count. ``M_HOFF`` absent from ``donors`` (a minimal test fixture) counts as zero --
    the check is skipped, not silently failed.
    """
    if "M_HOFF" not in donors.columns:
        return 0
    return int((donors["M_HOFF"] != MID_MODULE).sum())


def _count_household_unmatched(donor_h_ids: pd.Series, households: pd.DataFrame) -> int:
    """Count donors whose ``H_ID`` has no matching row in ``households``.

    Shared between :func:`donor_attributes` (which warns and NaNs ``has_car`` for them) and
    :func:`build_home_office_donor_pool` (which reports the same count as
    ``n_household_unmatched``), so both read the identical definition.
    """
    return int((~donor_h_ids.isin(set(households["H_ID"]))).sum())


def _count_car_unresolved(donor_h_ids: pd.Series, households: pd.DataFrame) -> int:
    """Count donors with a MATCHED household whose ``H_ANZAUTO`` cannot resolve ``has_car``.

    Distinct from :func:`_count_household_unmatched` (which counts an absent ``H_ID``): this
    counts donors whose ``H_ID`` DOES exist in ``households`` but the ``H_ANZAUTO`` value is
    missing (``NaN``), negative, or non-numeric. ``pandas.Series.gt(0)`` reads any of those as
    ``False`` (``NaN > 0`` is ``False``), which would silently read as "no car" -- a hard
    criterion (ruling: ``has_car`` gates a hard match) needs the restrictive reading instead:
    ``has_car = NaN``, i.e. "unresolved", so the matching module treats it as unusable rather
    than as a confirmed non-owner.

    Shared between :func:`donor_attributes` (which warns and NaNs ``has_car`` for them) and
    :func:`build_home_office_donor_pool` (which reports the same count as
    ``n_car_unresolved``), so both read the identical definition.
    """
    household_matched = donor_h_ids.isin(set(households["H_ID"]))
    h_anzauto = pd.to_numeric(
        donor_h_ids.map(households.set_index("H_ID")["H_ANZAUTO"]), errors="coerce")
    return int((household_matched & (h_anzauto.isna() | (h_anzauto < 0))).sum())


def select_home_office_day_donors(persons: pd.DataFrame) -> pd.DataFrame:
    """MiD persons who worked AT HOME on their reporting day (weekday).

    Filter: ``arbwo == MID_WEEKDAY``, ``P_STARB1 == MID_WORKED_ON_DAY``, ``starb2 ==
    MID_AT_HOME``. No ``M_HOFF`` (home-office module) condition is applied here -- ``starb2 ==
    MID_AT_HOME`` is only codeable when the module was asked and answered, so the module
    condition is implied -- but the count of selected donors with ``M_HOFF != MID_MODULE`` is
    logged and, if non-zero, raised as a WARNING (CLAUDE.md fallback transparency): it would mean
    ``starb2`` is populated for persons the module flag says were never asked, a data-consistency
    signal worth surfacing loudly rather than accepting silently.
    """
    _require_columns(persons, ("arbwo", "P_STARB1", "starb2"), "persons frame")
    n_input = len(persons)
    donors = persons[(persons["arbwo"] == MID_WEEKDAY)
                     & (persons["P_STARB1"] == MID_WORKED_ON_DAY)
                     & (persons["starb2"] == MID_AT_HOME)].copy().reset_index(drop=True)
    n_donors = len(donors)
    logger.info("%s home-office-day donor filter: %d/%d persons kept (%.1f%%)",
                _LOG_TAG, n_donors, n_input, 100.0 * n_donors / max(n_input, 1))

    n_not_in_module = _count_not_in_module(donors)
    if n_not_in_module > 0:
        logger.warning(
            "%s %d/%d selected donors have M_HOFF != %d (not flagged as home-office-module "
            "persons) despite starb2 == MID_AT_HOME -- a data-consistency signal, not expected "
            "to occur under a correct MiD extract.", _LOG_TAG, n_not_in_module, n_donors, MID_MODULE)
    return donors


#: Drop rate above which the three diary filters are reported as a WARNING rather than an INFO.
#: A DIAGNOSTIC bound, not a reference value: losing more than half the selected donors to the
#: filters usually signals a broken column join (an ``anzwege1`` read as text, a ``feiertag``
#: recoded in a re-delivery) rather than a genuinely unusable donor universe. Mirrors the
#: identical 50 % bound :func:`donor_attributes` applies to its unknown-distance share.
MAX_EXPECTED_DIARY_FILTER_DROP_RATE = 0.5

#: Keys :func:`filter_donor_diaries` always reports, so a consumer never has to tell "the filter
#: did not fire" apart from "the filter did not run".
DIARY_FILTER_COUNT_KEYS = ("n_donors_before_diary_filters", "n_dropped_no_diary",
                           "n_dropped_no_diary_immobile",
                           "n_dropped_no_diary_mobil_unknown", "n_dropped_holiday",
                           "n_dropped_only_rbw")


def _log_diary_filter(name: str, n_dropped: int, n_input: int) -> None:
    """Log one filter's drop count as an explicit rate (CLAUDE.md fallback transparency)."""
    logger.info("%s donor filter %s: dropped %d/%d (%.1f%%)", _LOG_TAG, name, n_dropped, n_input,
                100.0 * n_dropped / max(n_input, 1))


def _require_numeric_codes(donors: pd.DataFrame, columns, filter_name: str) -> None:
    """Raise unless every named MiD code column has a NUMERIC dtype.

    VALIDATE, never coerce. The filters compare the raw code columns exactly as
    :func:`braunschweig.popsim.diary_plan_match._own_diary_reason` does -- coercing here would
    create a NEW divergence between the two rules, which is precisely what issue #374 exists to
    remove. But an exact comparison against an OBJECT column silently matches nothing: a delivery
    whose ``anzwege1`` arrives as text makes ``Series(["803"]).isin((803, 804))`` ``False`` for
    every row, so the filter runs to a 0 % drop rate that is indistinguishable from a delivery
    genuinely holding no such donors. The drop-rate WARNING cannot catch it either -- 0 % is
    below any upper bound. Failing here is the only honest option (CLAUDE.md: no silent
    fallbacks; a filter that matches nothing must never look healthy).
    """
    for column in columns:
        if not pd.api.types.is_numeric_dtype(donors[column]):
            sample = donors[column].dropna().head(3).tolist()
            raise ValueError(
                f"{_LOG_TAG} the {filter_name} donor filter needs a NUMERIC {column!r} column, "
                f"but the donors frame carries dtype {donors[column].dtype!r} (e.g. {sample}). "
                "The MiD codes are compared exactly (never coerced, so this pool and "
                "diary_plan_match cannot drift apart), and an exact comparison against a "
                "non-numeric column matches nothing -- the filter would silently drop 0 donors. "
                f"Read {column!r} as a number, or switch the filter off.")


def filter_donor_diaries(donors: pd.DataFrame, wege, *, exclude_no_diary: bool,
                         exclude_holidays: bool,
                         exclude_only_rbw: bool) -> tuple[pd.DataFrame, dict]:
    """Drop donors whose reporting day cannot serve as a replacement day (issue #374).

    :func:`select_home_office_day_donors` states WHO worked at home; this states whose day is
    USABLE. The three exclusions are the same ones
    :func:`braunschweig.popsim.diary_plan_match.build_realisable_pool` applies to a synthetic
    person's plan source, read from the same MiD codes, so a home-office day and an ordinary
    weekday plan are drawn from diaries of the same quality:

    * ``exclude_no_diary`` -- the diary was not collected: ``anzwege1`` in
      :data:`braunschweig.popsim.diary_plan_match.NO_DIARY_CODES` (803 "Person ohne
      Wegeerfassung", 804 "Mobilitaet unbekannt"). **Both codes are dropped regardless of
      ``mobil``**, which is the ONE deliberate difference from ``diary_plan_match``: that rule
      keeps an ``803 and mobil != 1`` person because it is deciding whether the person's OWN
      realised day is credible, and such a person genuinely stayed home. Here the day is not the
      donor's own -- it is copied onto a far commuter -- so a day that was never observed cannot
      be a home-office-day donor at all, whether or not its owner was immobile. The size of that
      difference is counted, never left implicit: ``n_dropped_no_diary_immobile`` for the 803
      donors whose ``mobil`` is KNOWN and not 1, and ``n_dropped_no_diary_mobil_unknown`` for
      those whose ``mobil`` is missing (ruling R28 -- ``diary_plan_match``'s ``mobil != 1``
      comparison keeps those too, since NaN compares unequal, but immobility is then an
      artefact of the comparison rather than something the data states, so the two are counted
      apart). ``mobil`` is therefore required whenever this filter is on, exactly as
      ``braunschweig.popsim.completed_donor`` requires it for the plan-source side.
    * ``exclude_holidays`` -- the diary was reported on a public holiday
      (``feiertag == MID_HOLIDAY``); SrV reference days exclude public holidays.
    * ``exclude_only_rbw`` -- the diary consists ONLY of rbW summary legs
      (``n_direct_legs == 0 and n_rbw_legs > 0``, from
      :func:`braunschweig.popsim.diary_facts.compute_diary_facts`), i.e. it carries no
      individually reported trip; with ``exclude_rbw_legs`` on, such a donor's built chain is
      empty and the day would silently degrade to an immobile one.

    Args:
        donors: The selected donors (:func:`select_home_office_day_donors`' output); needs
            ``H_ID``/``P_ID``, plus ``anzwege1``/``mobil`` and ``feiertag`` for the filters that
            read them.
        wege: The raw MiD Wege frame; required (not ``None``) only when ``exclude_only_rbw`` is
            on, and then it must carry
            :data:`braunschweig.popsim.diary_facts.REQUIRED_WEGE_COLUMNS`.
        exclude_no_diary: Apply the ``anzwege1`` 803/804 filter.
        exclude_holidays: Apply the ``feiertag`` filter.
        exclude_only_rbw: Apply the rbW-only filter.

    Returns:
        ``(kept_donors, counts)``. ``kept_donors`` is a row subset of ``donors`` with the index
        reset; with every flag off it is ``donors`` itself, unchanged. ``counts`` always carries
        :data:`DIARY_FILTER_COUNT_KEYS`, zero for a filter that is off. The three drop counts are
        per CLASS and may OVERLAP (a holiday diary can also be rbW-only), so they need not sum to
        the number of donors actually removed; the union is logged.

    Raises:
        ValueError: naming the column (or the missing ``wege`` frame) a live filter needs -- a
            filter is never silently skipped because its input is absent -- or naming a code
            column delivered with a non-numeric dtype, which would make the exact comparison
            match nothing and the filter look healthy at a 0 %% drop rate
            (:func:`_require_numeric_codes`).
    """
    _require_columns(donors, ("H_ID", "P_ID"), "donors frame")
    n_input = len(donors)
    counts = {key: 0 for key in DIARY_FILTER_COUNT_KEYS}
    counts["n_donors_before_diary_filters"] = n_input
    if not (exclude_no_diary or exclude_holidays or exclude_only_rbw):
        logger.info("%s donor diary filters are all off: %d donors kept unfiltered",
                    _LOG_TAG, n_input)
        return donors, counts

    drop = pd.Series(False, index=donors.index)

    if exclude_no_diary:
        _require_columns(donors, ("anzwege1", "mobil"),
                         "donors frame (the no-diary donor filter is on)")
        _require_numeric_codes(donors, ("anzwege1", "mobil"), "no_diary")
        anzwege1_not_collected, _anzwege1_unknown = NO_DIARY_CODES  # 803, 804
        no_diary = donors["anzwege1"].isin(NO_DIARY_CODES)
        counts["n_dropped_no_diary"] = int(no_diary.sum())
        # The donors diary_plan_match would KEEP as legitimately immobile (see the docstring):
        # measuring the divergence is what keeps this a documented decision, not a hidden one.
        # Ruling R28: a MISSING mobil is counted SEPARATELY rather than folded in here. Its
        # `mobil != 1` comparison is True for NaN, so diary_plan_match does keep such a person --
        # but immobility is then an artefact of the comparison, not something the data states,
        # and this count is the number that documents the rule difference. The two counts
        # together are the full divergence; this one alone is the part the data establishes.
        not_collected = donors["anzwege1"] == anzwege1_not_collected
        mobil_known = donors["mobil"].notna()
        counts["n_dropped_no_diary_immobile"] = int(
            (not_collected & mobil_known & (donors["mobil"] != 1)).sum())
        counts["n_dropped_no_diary_mobil_unknown"] = int((not_collected & ~mobil_known).sum())
        drop |= no_diary
        _log_diary_filter("no_diary", counts["n_dropped_no_diary"], n_input)
        if counts["n_dropped_no_diary"] > 0:
            logger.info(
                "%s donor filter no_diary: of those, %d carry anzwege1 %d with a KNOWN mobil != 1 "
                "and %d carry it with mobil missing -- diary_plan_match keeps both as an immobile "
                "OWN day; this pool drops them because a donor day that was never observed cannot "
                "be transplanted onto a worker.", _LOG_TAG,
                counts["n_dropped_no_diary_immobile"], anzwege1_not_collected,
                counts["n_dropped_no_diary_mobil_unknown"])

    if exclude_holidays:
        _require_columns(donors, ("feiertag",), "donors frame (the holiday donor filter is on)")
        _require_numeric_codes(donors, ("feiertag",), "holiday")
        holiday = donors["feiertag"] == MID_HOLIDAY
        counts["n_dropped_holiday"] = int(holiday.sum())
        drop |= holiday
        _log_diary_filter("holiday", counts["n_dropped_holiday"], n_input)

    if exclude_only_rbw:
        if wege is None:
            raise ValueError(
                f"{_LOG_TAG} exclude_only_rbw=True needs the raw MiD wege frame to classify "
                "rbW-only diaries, but none was passed (wege is None).")
        only_rbw = _only_rbw_donors(donors, wege)
        counts["n_dropped_only_rbw"] = int(only_rbw.sum())
        drop |= only_rbw
        _log_diary_filter("only_rbw", counts["n_dropped_only_rbw"], n_input)

    kept = donors[~drop].reset_index(drop=True)
    n_dropped = n_input - len(kept)
    drop_rate = n_dropped / max(n_input, 1)
    log = logger.warning if drop_rate > MAX_EXPECTED_DIARY_FILTER_DROP_RATE else logger.info
    log("%s donor diary filters: %d/%d donors dropped (%.1f%%; no_diary %d, holiday %d, only_rbw "
        "%d -- classes may overlap), %d donors remain", _LOG_TAG, n_dropped, n_input,
        100.0 * drop_rate, counts["n_dropped_no_diary"], counts["n_dropped_holiday"],
        counts["n_dropped_only_rbw"], len(kept))
    if drop_rate > MAX_EXPECTED_DIARY_FILTER_DROP_RATE:
        logger.warning(
            "%s donor diary filters dropped more than %.0f%% of the selected donors, which "
            "usually signals a broken column join rather than a genuinely unusable donor "
            "universe -- check anzwege1/mobil/feiertag and the Wege join before trusting the pool.",
            _LOG_TAG, 100.0 * MAX_EXPECTED_DIARY_FILTER_DROP_RATE)
    return kept, counts


def _only_rbw_donors(donors: pd.DataFrame, wege: pd.DataFrame) -> pd.Series:
    """Boolean mask (indexed like ``donors``) of donors whose diary holds ONLY rbW legs.

    Ruling R5 of the plan-structure fix: rbW-only diaries are detected from the diary FACTS
    (``n_direct_legs == 0 & n_rbw_legs > 0``), derived from the Wege rows the pipeline actually
    builds trips from, rather than from MiD ``mobil_diff`` -- the identical definition
    :func:`braunschweig.popsim.diary_plan_match._own_diary_reason` uses for a plan source. A
    donor with no Wege row at all is NOT rbW-only (it is immobile, which the pool reports
    separately as ``is_immobile``).
    """
    _require_columns(wege, ("H_ID", "P_ID"), "wege frame (the rbW-only donor filter is on)")
    n_donors = len(donors)
    donor_wege = wege[wege["H_ID"].isin(set(donors["H_ID"]))]
    if not len(donor_wege):
        # Two different situations, and only one of them is normal: a genuinely trip-less donor
        # set, or an H_ID key mismatch (e.g. one side read as text) that joined nothing. Neither
        # would ever show up in the drop rate -- both produce 0 rbW-only donors -- so the join
        # itself is reported (CLAUDE.md: a filter that matches nothing must not look healthy).
        log = logger.warning if len(wege) else logger.info
        log("%s donor filter only_rbw: 0/%d donors matched ANY Wege row (donor H_ID dtype %s vs "
            "wege H_ID dtype %s, %d wege rows offered); with no diary to read, no donor can be "
            "classified rbW-only.", _LOG_TAG, n_donors, donors["H_ID"].dtype, wege["H_ID"].dtype
            if len(wege) else "n/a", len(wege))
        return pd.Series(False, index=donors.index)
    facts = compute_diary_facts(donor_wege).reindex(
        pd.MultiIndex.from_arrays([donors["H_ID"], donors["P_ID"]]))
    # How many donors actually resolved to a diary-facts row. A donor without one is either
    # genuinely immobile (no Wege row of its own) or the victim of a (H_ID, P_ID) key mismatch;
    # the rate makes a zero-match join visible on sight instead of hiding it in a 0 % drop rate.
    n_matched = int(facts["n_direct_legs"].notna().sum())
    log = logger.warning if n_matched == 0 else logger.info
    log("%s donor filter only_rbw: %d/%d donors (%.1f%%) matched a diary-facts row on "
        "(H_ID, P_ID); the rest have no Wege row of their own (immobile) -- a 0 %% match rate "
        "instead means a key mismatch, not an immobile pool.", _LOG_TAG, n_matched, n_donors,
        100.0 * n_matched / max(n_donors, 1))
    n_direct = facts["n_direct_legs"].fillna(0).astype(int).to_numpy()
    n_rbw = facts["n_rbw_legs"].fillna(0).astype(int).to_numpy()
    return pd.Series((n_direct == 0) & (n_rbw > 0), index=donors.index)


def _donors_as_persons(donors: pd.DataFrame) -> pd.DataFrame:
    """The donors frame in the shape ``trips_stage.run`` hands ``build_closure_dwell_model``.

    That function reduces its ``persons`` argument to the distinct donor diaries the trip table
    is built from, keyed by ``(source_H_ID, source_P_ID)`` when those columns are present and by
    ``(H_ID, P_ID)`` otherwise (see
    :func:`braunschweig.popsim.trips_stage._donor_diary_frame`). A MiD donor IS its own plan
    source, so both key pairs are the same values here; they are carried explicitly anyway, so
    the donor pool takes the SAME branch of that precedence as the production stage instead of
    relying on the fallback -- the exact class of silent divergence issue #374 exists to close.
    """
    return pd.DataFrame({
        "person_id": donors["HP_ID"].to_numpy(),
        "H_ID": donors["H_ID"].to_numpy(),
        "P_ID": donors["P_ID"].to_numpy(),
        "source_H_ID": donors["H_ID"].to_numpy(),
        "source_P_ID": donors["P_ID"].to_numpy(),
    })


def _work_trip_length_km(wege: pd.DataFrame) -> pd.DataFrame:
    """First valid work-trip length in km per ``(H_ID, P_ID)``, sorted by ``W_ID``.

    Delegates the qualifying condition (``W_ZWECK == MID_WORK_TRIP_PURPOSE``, ``0 < wegkm <
    MID_TRIP_LENGTH_MAX_KM``) to the single committed implementation,
    :func:`braunschweig.calibration.commute_day_state_reference.first_work_trip_length_km`
    (fix round 1 item 3: one definition of the fallback, not two) -- ``wege`` is sorted by
    ``(H_ID, P_ID, W_ID)`` first so that helper's "first in file order" resolves to "first by
    W_ID" deterministically (ruling R2), independent of the caller's row order.

    Returns one row per ``(H_ID, P_ID)`` with a qualifying trip, columns ``H_ID``, ``P_ID``,
    :data:`braunschweig.calibration.commute_day_state_reference.WORK_TRIP_LENGTH_COLUMN`. Persons
    without a qualifying trip are simply absent.
    """
    _require_columns(wege, ("H_ID", "P_ID", "W_ID"), "wege frame")
    sorted_wege = wege.sort_values(["H_ID", "P_ID", "W_ID"])
    first = first_work_trip_length_km(sorted_wege)
    logger.info("%s work-trip-length fallback: %d distinct persons keep their first (by W_ID) "
                "qualifying work trip", _LOG_TAG, len(first))
    return first


def donor_attributes(donors: pd.DataFrame, persons_all: pd.DataFrame, households: pd.DataFrame,
                     wege: pd.DataFrame) -> pd.DataFrame:
    """Per-donor demographic + household attributes, keyed by ``donor_id = HP_ID``.

    ``donors`` -- output of :func:`select_home_office_day_donors` (or an equivalent subset):
    needs ``HP_ID``, ``H_ID``, ``P_ID``, ``HP_SEX``, ``HP_ALTER``, ``P_ARB_ENTF``.
    ``persons_all`` -- the FULL MiD persons frame (donors and non-donors), needed so
    ``has_children_u14`` sees every household member, not only the ones selected as donors.
    ``households`` -- MiD households keyed by ``H_ID``, needs ``H_ANZAUTO`` and ``H_GR``.
    ``wege`` -- the raw MiD Wege/trip file, needs ``H_ID``, ``P_ID``, ``W_ID``, ``W_ZWECK``,
    ``wegkm`` (used for ``has_active_escort`` and the trip-length distance fallback).

    Columns produced:

    * ``sex`` -- ``HP_SEX`` 1/2 -> ``"male"``/``"female"``, else ``NaN`` (counted).
    * ``age`` -- ``HP_ALTER`` verbatim; ``age_class`` --
      :func:`braunschweig.popsim.chain_matching.derive_age_class` of ``age``.
    * ``employed`` -- always ``True`` (the donor universe is worked-on-reporting-day by
      construction).
    * ``has_children_u14`` -- any member of the donor's ``H_ID`` (in ``persons_all``, i.e.
      including non-donor household members) with ``HP_ALTER <= MID_CHILD_MAX_AGE``.
    * ``has_car`` -- ``H_ANZAUTO > 0`` from ``households``, joined on ``H_ID``. A donor whose
      ``H_ID`` has no matching row in ``households`` at all gets ``has_car = NaN`` (never
      silently defaulted to ``False``) and is counted (see ``n_household_unmatched`` on
      :func:`build_home_office_donor_pool`'s diagnostics); a donor with a MATCHED household whose
      ``H_ANZAUTO`` is missing, negative, or non-numeric gets ``has_car = NaN`` too (``.gt(0)``
      alone would silently read a ``NaN``/negative value as ``False``, i.e. "no car", which is
      not the restrictive reading a hard criterion needs) and is counted separately (see
      ``n_car_unresolved`` on :func:`build_home_office_donor_pool`'s diagnostics).
    * ``has_active_escort`` -- any ``wege`` row for the donor's ``(H_ID, P_ID)`` with ``W_ZWECK
      == MID_ESCORT_ACTIVE``.
    * ``household_size`` -- ``H_GR`` from ``households``, joined on ``H_ID``, UNBINNED (ruling
      R1: the class binning is the matching module's responsibility, applied identically to both
      sides).
    * ``distance_km`` / ``distance_class`` / ``distance_source`` -- ``P_ARB_ENTF`` cleaned with
      :func:`clean_mid_commute_distance_km` and classified with the default MiD top-code when
      valid (``distance_source == "P_ARB_ENTF"``); else the donor's first work-trip length in km
      (see :func:`_work_trip_length_km`), classified with ``topcode_km=None`` -- a raw trip
      length was never subject to the MiD ``P_ARB_ENTF`` 200 km top-code (``distance_source ==
      "trip_length"``); else ``distance_class = DISTANCE_CLASS_UNKNOWN`` (the literal string
      ``"unknown"``, never ``NaN`` -- the later matching module treats it as "matches any class")
      and ``distance_source == "unknown"`` (counted -- CLAUDE.md fallback transparency).

    Donors without any qualifying trip (e.g. an immobile home-office day) still get a row here;
    :func:`donor_trips` yields no rows for them, and :func:`build_home_office_donor_pool` is what
    turns that absence into its ``n_immobile`` / ``n_chain_dropped_by_resample`` diagnostics.
    This frame carries no ``n_trips`` / ``has_education_leg`` / ``has_work_leg`` column: those
    three are read from the BUILT chains and are attached afterwards by
    :func:`attach_trip_derived_attributes`, which :func:`build_home_office_donor_pool` calls.
    """
    _require_columns(donors, ("HP_ID", "H_ID", "P_ID", "HP_SEX", "HP_ALTER", "P_ARB_ENTF"),
                     "donors frame")
    _require_columns(persons_all, ("H_ID", "HP_ALTER"), "persons_all frame")
    _require_columns(households, ("H_ID", "H_ANZAUTO", "H_GR"), "households frame")

    attributes = donors[["HP_ID", "H_ID", "P_ID", "HP_SEX", "HP_ALTER", "P_ARB_ENTF"]].copy()
    attributes = attributes.rename(columns={"HP_ID": "donor_id"}).reset_index(drop=True)

    sex = attributes["HP_SEX"].map(SEX_LABEL_BY_HP_SEX)
    n_sex_unknown = int(sex.isna().sum())
    if n_sex_unknown:
        logger.warning("%s sex: %d/%d donors have an HP_SEX code outside {1, 2} (diverse/no "
                       "answer) and get sex=NaN", _LOG_TAG, n_sex_unknown, len(attributes))
    attributes["sex"] = sex

    attributes["age"] = attributes["HP_ALTER"]
    attributes["age_class"] = derive_age_class(attributes["age"])
    attributes["employed"] = True

    children_by_household = (
        persons_all.loc[persons_all["HP_ALTER"] <= MID_CHILD_MAX_AGE, "H_ID"].value_counts()
    )
    attributes["has_children_u14"] = attributes["H_ID"].map(children_by_household).fillna(0).gt(0)

    household_lookup = households.set_index("H_ID")
    n_household_unmatched = _count_household_unmatched(attributes["H_ID"], households)
    n_car_unresolved = _count_car_unresolved(attributes["H_ID"], households)
    if n_household_unmatched > 0 or n_car_unresolved > 0:
        logger.warning(
            "%s households: %d/%d donors have an H_ID absent from the households frame, and "
            "%d/%d have a MATCHED household whose H_ANZAUTO is missing/negative/non-numeric; "
            "has_car is NaN for both groups (never silently defaulted to False via .gt(0)) -- "
            "check the households/persons H_ID join and the MiD H_ANZAUTO coding.", _LOG_TAG,
            n_household_unmatched, len(attributes), n_car_unresolved, len(attributes))
    household_matched = attributes["H_ID"].isin(set(households["H_ID"]))
    h_anzauto = pd.to_numeric(attributes["H_ID"].map(household_lookup["H_ANZAUTO"]), errors="coerce")
    car_value_resolved = h_anzauto.notna() & (h_anzauto >= 0)
    attributes["has_car"] = np.where(household_matched & car_value_resolved, h_anzauto.gt(0), np.nan)
    attributes["household_size"] = attributes["H_ID"].map(household_lookup["H_GR"])

    escort_pairs = set(
        map(tuple, wege.loc[wege["W_ZWECK"] == MID_ESCORT_ACTIVE, ["H_ID", "P_ID"]].to_numpy())
    )
    donor_pairs = list(zip(attributes["H_ID"], attributes["P_ID"]))
    attributes["has_active_escort"] = pd.Series(donor_pairs, index=attributes.index).isin(escort_pairs)
    n_escort = int(attributes["has_active_escort"].sum())
    logger.info("%s active escort: %d/%d donors (%.1f%%) have an active-escort trip (W_ZWECK == %d)",
                _LOG_TAG, n_escort, len(attributes), 100.0 * n_escort / max(len(attributes), 1),
                MID_ESCORT_ACTIVE)

    cleaned_p_arb_entf = clean_mid_commute_distance_km(attributes["P_ARB_ENTF"])
    class_from_p_arb_entf = pd.Series(
        [classify_commute_distance(km) for km in cleaned_p_arb_entf], index=attributes.index)
    has_p_arb_entf = class_from_p_arb_entf.notna()

    work_trip = _work_trip_length_km(wege)
    attributes = attributes.merge(work_trip, on=["H_ID", "P_ID"], how="left")
    class_from_trip_length = pd.Series(
        [classify_commute_distance(km, topcode_km=None)
         for km in attributes[WORK_TRIP_LENGTH_COLUMN]],
        index=attributes.index)
    has_trip_length = class_from_trip_length.notna()

    distance_km = cleaned_p_arb_entf.where(has_p_arb_entf, attributes[WORK_TRIP_LENGTH_COLUMN])
    # DISTANCE_CLASS_UNKNOWN (the literal string "unknown"), never None/NaN -- see the module
    # docstring for why: a None-keyed cells entry would be silently dropped by any
    # groupby/merge the later matching module performs on this column.
    distance_class = (
        class_from_p_arb_entf.where(has_p_arb_entf, class_from_trip_length)
        .fillna(DISTANCE_CLASS_UNKNOWN)
    )
    distance_source = pd.Series(DISTANCE_SOURCE_UNKNOWN, index=attributes.index, dtype=object)
    distance_source[has_p_arb_entf] = DISTANCE_SOURCE_P_ARB_ENTF
    distance_source[(~has_p_arb_entf) & has_trip_length] = DISTANCE_SOURCE_TRIP_LENGTH
    attributes["distance_km"] = distance_km
    attributes["distance_class"] = distance_class
    attributes["distance_source"] = distance_source

    assert attributes["distance_class"].notna().all(), (
        f"{_LOG_TAG} distance_class must never be null -- the unmatched case must fall back to "
        f"the literal DISTANCE_CLASS_UNKNOWN ({DISTANCE_CLASS_UNKNOWN!r}), not None/NaN.")

    source_counts = attributes["distance_source"].value_counts().to_dict()
    n_missing_distance = int((attributes["distance_source"] == DISTANCE_SOURCE_UNKNOWN).sum())
    logger.info(
        "%s distance source over %d donors: %s (%d, %.1f%%, have no usable commute distance)",
        _LOG_TAG, len(attributes), source_counts, n_missing_distance,
        100.0 * n_missing_distance / max(len(attributes), 1),
    )
    if n_missing_distance > 0 and n_missing_distance / max(len(attributes), 1) > 0.5:
        logger.warning(
            "%s distance source: %d/%d donors (%.1f%%) have NO usable commute distance (neither "
            "P_ARB_ENTF nor a work trip) -- above 50%%, which usually signals a broken join or "
            "column mismatch rather than a genuinely undocumented commute.",
            _LOG_TAG, n_missing_distance, len(attributes),
            100.0 * n_missing_distance / max(len(attributes), 1),
        )

    return attributes[[
        "donor_id", "H_ID", "P_ID", "sex", "age", "age_class", "employed",
        "has_children_u14", "has_car", "has_active_escort", "household_size",
        "distance_km", "distance_class", "distance_source",
    ]].reset_index(drop=True)


def donor_trips(donors: pd.DataFrame, attributes: pd.DataFrame, wege: pd.DataFrame, *,
                random_seed: int, escort_purpose: bool, escort_passive_education: bool,
                explicit_round_trip_purposes: bool, exclude_rbw_legs: bool = False,
                drop_leading_arrive_home_leg: bool = False,
                w_zweck_10_as_leisure: bool = False, dwell_model=None) -> pd.DataFrame:
    """The donors' own trip chains in the ``synthesis.population.trips`` CONTRACT, ``donor_id``-keyed.

    Built with :func:`braunschweig.popsim.trips.build_validated_trip_table` using the keyword
    arguments :func:`braunschweig.popsim.trips_stage.run` passes -- ``resample=True``, the
    escort/round-trip/``w_zweck_10_as_leisure`` flags, ``random_seed``, and the three
    plan-structure arguments ``exclude_rbw_legs`` / ``drop_leading_arrive_home_leg`` /
    ``dwell_model`` -- ruling R2, but
    WITHOUT ``trips_stage.run``'s per-person departure-time jitter step
    (:func:`braunschweig.popsim.trips_stage.apply_per_person_jitter`): that jitter is applied
    ONCE later, in the plan-replacement step, keyed by the RECEIVING synthetic person -- applying
    it here would jitter the same donor's day a second time once it is copied onto a worker.
    ``resample_cell_col`` is fixed to ``None``: the legacy same-cell resample fallback needs a
    synthetic-population cell column (e.g. ``ZENSUS100m``) that MiD donors do not carry.

    The three plan-structure arguments were MISSING here until issue #374, while this docstring
    claimed the call already passed "EXACTLY" what ``trips_stage.run`` passes. They have been
    default-ON in production since issues #366/#367, so a donor day kept the rbW summary legs and
    the leading arrive-home leg that the day it replaces does not have, and closed an open chain
    with the constant one-hour dwell instead of the empirical one. The false claim is recorded
    here because it is what hid the defect: the parity is now expressed by NAMING every argument
    on both sides rather than by asserting it. They default to the pre-#374 values so a caller
    that passes none reproduces the old output byte-identically; the stage
    (``home_office_donors_stage``) passes the values of the same config keys ``trips_stage``
    reads, so one base-config value steers both.

    The persons frame handed to ``build_validated_trip_table`` carries ``sex``, ``age`` and
    ``employed`` from ``attributes`` (:func:`donor_attributes`'s output) IN ADDITION to
    ``person_id``/``H_ID``/``P_ID`` (fix round 1 item 2): stage B of the resample cascade
    (``braunschweig.popsim.trips._match_unfixable``) only performs its attribute-matched chain
    replacement when the persons frame carries a ``sex`` column -- without it, EVERY unfixable
    donor falls back to the legacy same-cell resample, which with ``resample_cell_col=None``
    (see above) silently drops every one of them to a trip-less (home-only) day. Carrying the
    same attributes ``trips_stage.run`` carries for the SYNTHETIC persons lets an unfixable donor
    (e.g. one with MiD coded times) be replaced by a behaviourally similar donor from the same
    pool instead, exactly as the real pipeline does for synthetic workers.

    ``donors`` needs ``HP_ID`` (must be unique -- raises otherwise), ``H_ID``, ``P_ID``.
    ``attributes`` needs ``donor_id``, ``sex``, ``age``, ``employed`` (one row per donor,
    matching ``donors`` 1:1). Donors without any MiD trip yield NO rows here (an immobile
    home-office day): the inner join inside ``build_validated_trip_table`` drops persons whose
    donor key matches no Wege row at all. A donor that DOES have Wege rows but whose chain cannot
    be repaired or attribute-matched to a valid donor ALSO ends up with no rows here (dropped by
    the resample, not immobile) -- :func:`build_home_office_donor_pool` distinguishes the two
    cases (``n_immobile`` vs ``n_chain_dropped_by_resample``) since neither this frame nor
    :func:`donor_attributes` carries an ``n_trips`` column of its own.

    Returns one row per (donor, trip): the ``trips_stage.CONTRACT`` columns (``person_id``
    renamed to ``donor_id``) plus ``euclidean_distance`` (metres, from MiD ``wegkm_imp`` * 1000 /
    the ENTD detour factor -- the same convention as ``trips_stage.run``) and ``trip_key``
    (traceability). The raw MiD Wege extras (``W_ZWECK``, ``hvm_imp``, ``wegkm``, ...) are
    dropped to keep the donor pool small; the docstring here is the single place documenting
    that intentional narrowing.
    """
    _require_columns(donors, ("HP_ID", "H_ID", "P_ID"), "donors frame")
    _require_columns(attributes, ("donor_id", "sex", "age", "employed"), "attributes frame")
    if donors["HP_ID"].duplicated().any():
        n_duplicated = int(donors["HP_ID"].duplicated().sum())
        raise ValueError(f"{_LOG_TAG} donors frame has {n_duplicated} duplicate HP_ID value(s); "
                         "HP_ID must be unique per donor.")

    donor_persons = donors[["HP_ID", "H_ID", "P_ID"]].rename(columns={"HP_ID": "person_id"})
    attribute_extras = attributes[["donor_id", "sex", "age", "employed"]].rename(
        columns={"donor_id": "person_id"})
    donor_persons = donor_persons.merge(attribute_extras, on="person_id", how="left",
                                        validate="one_to_one")
    # Stage B of the resample cascade matches on `sex`, and eqasim's statistical_matching sorts
    # the union of that column's distinct values. A column mixing NaN with "male"/"female"
    # therefore raises TypeError ('<' not supported between float and str) and kills the whole
    # donor build -- MEASURED on the real MiD delivery: 12 of 8,026 home-office-day donors carry
    # an HP_SEX code outside {1, 2} (diverse / no answer). Those donors are given an EXPLICIT
    # SEX_LABEL_UNKNOWN stratum here rather than being dropped or silently guessed as male or
    # female: they stay in the pool, they form their own matching stratum (too small to serve as
    # a donor pool, so a same-stratum target is left trip-less and counted by _match_unfixable's
    # own feasibility log), and the substitution is counted and logged. The ATTRIBUTES frame is
    # untouched -- it keeps NaN, which is what donor_attributes documents and what every other
    # consumer sees.
    n_sex_unknown_persons = int(donor_persons["sex"].isna().sum())
    if n_sex_unknown_persons > 0:
        donor_persons["sex"] = donor_persons["sex"].fillna(SEX_LABEL_UNKNOWN)
        logger.info(
            "%s trip-table resample: %d/%d donors (%.2f%%) have sex=NaN and enter the stage-B "
            "matching as the explicit %r stratum (the attributes frame keeps NaN).",
            _LOG_TAG, n_sex_unknown_persons, len(donor_persons),
            100.0 * n_sex_unknown_persons / max(len(donor_persons), 1), SEX_LABEL_UNKNOWN)
    table, report = build_validated_trip_table(
        donor_persons, wege,
        resample=True,
        resample_cell_col=None,
        random_seed=random_seed,
        escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        explicit_round_trip_purposes=explicit_round_trip_purposes,
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        dwell_model=dwell_model,
    )
    n_donors_with_trips = table["person_id"].nunique() if len(table) else 0
    logger.info("%s donor trip table built: %d trips for %d/%d donors (valid=%s)",
                _LOG_TAG, len(table), n_donors_with_trips, len(donors), report.is_valid)

    for col in ("departure_time", "arrival_time"):
        n_nan = int(table[col].isna().sum())
        assert n_nan == 0, (
            f"{_LOG_TAG} {n_nan} rows have NaN {col} after resample; build_validated_trip_table "
            "must replace every coded-time donor before the donor pool is built.")

    if "wegkm_imp" in table.columns:
        table["euclidean_distance"] = table["wegkm_imp"].astype(float) * 1000.0 / DETOUR_FACTOR

    table = table.sort_values(["person_id", "trip_index"]).reset_index(drop=True)
    table = table.rename(columns={"person_id": "donor_id"})

    extra_columns = [column for column in ("euclidean_distance", "trip_key") if column in table.columns]
    contract_columns = ["donor_id" if column == "person_id" else column for column in CONTRACT]
    return table[contract_columns + extra_columns]


def _donors_with_purpose(trips: pd.DataFrame, purpose: str) -> set:
    """Donor ids whose BUILT chain contains an activity of ``purpose`` at either trip end.

    Both ends are inspected (``preceding_purpose`` and ``following_purpose``): the leg TOWARDS a
    fixed activity arrives at it and the return leg departs from it, and either is evidence that
    the day contains that activity.
    """
    if not len(trips):
        return set()
    ids = set()
    for column in ("preceding_purpose", "following_purpose"):
        if column in trips.columns:
            ids |= set(trips.loc[trips[column] == purpose, "donor_id"])
    return ids


def attach_trip_derived_attributes(attributes: pd.DataFrame, trips: pd.DataFrame,
                                   wege: pd.DataFrame) -> pd.DataFrame:
    """Add ``n_trips``, ``is_immobile``, ``has_education_leg`` and ``has_work_leg``.

    These four columns cannot be produced by :func:`donor_attributes`, which never sees the BUILT
    trip table; they are attached here, by :func:`build_home_office_donor_pool`, once that table
    exists. The three trip-derived ones are read from the built chains (never from the raw MiD
    codes), so they follow the same purpose mapping the donor's day was built with; ``is_immobile``
    is read from the RAW ``wege`` file instead, for the reason given below.

    * ``n_trips`` -- rows the donor has in ``trips``; ``0`` both for an immobile day and for a
      chain the repair/resample cascade dropped.
    * ``is_immobile`` -- the donor has NO row at all in the raw ``wege`` file (needs ``H_ID`` and
      ``P_ID`` on both frames). Ruling R9, fix round 1: ``n_trips == 0`` alone does NOT mean
      "immobile" -- :func:`build_home_office_donor_pool` keeps ``n_immobile`` and
      ``n_chain_dropped_by_resample`` apart precisely because a dropped chain is a resample gap,
      not real behaviour, and the plan-replacement step must not read one as the other. This flag
      is the pool's own statement of which of the two a trip-less donor is.
    * ``has_education_leg`` -- the chain contains an ``education`` activity. Ruling R7: such a
      donor is only eligible for a receiving person who HAS an education location, because the
      transplanted activity would otherwise have nowhere to be anchored and the secondary
      chainsolver raises on the ``None`` origin/destination it then builds.
    * ``has_work_leg`` -- the chain contains a ``work`` activity. Reported as a pool diagnostic
      only, never used as a matching criterion: every receiving person is a worker with an
      assigned workplace by construction, so a work leg can always be anchored.
    """
    _require_columns(attributes, ("donor_id", "H_ID", "P_ID"), "attributes frame")
    _require_columns(wege, ("H_ID", "P_ID"), "wege frame")
    attributes = attributes.copy()
    trip_counts = trips["donor_id"].value_counts() if len(trips) else pd.Series(dtype=int)
    attributes["n_trips"] = (attributes["donor_id"].map(trip_counts)
                             .fillna(0).astype(int))

    # Computed from the RAW Wege file, independently of the built trips, so "immobile" and
    # "chain dropped by the resample" can never collapse into one another.
    donor_wege_pairs = set(
        map(tuple, wege[["H_ID", "P_ID"]].drop_duplicates().itertuples(index=False, name=None)))
    attributes["is_immobile"] = pd.Series(
        [(h, p) not in donor_wege_pairs for h, p in zip(attributes["H_ID"], attributes["P_ID"])],
        index=attributes.index)

    education_donors = _donors_with_purpose(trips, EDUCATION_PURPOSE)
    work_donors = _donors_with_purpose(trips, WORK_PURPOSE)
    attributes["has_education_leg"] = attributes["donor_id"].isin(education_donors)
    attributes["has_work_leg"] = attributes["donor_id"].isin(work_donors)
    return attributes


def build_home_office_donor_pool(persons: pd.DataFrame, wege: pd.DataFrame, households: pd.DataFrame,
                                 *, random_seed: int, escort_purpose: bool = False,
                                 escort_passive_education: bool = False,
                                 explicit_round_trip_purposes: bool = True,
                                 exclude_rbw_legs: bool = False,
                                 drop_leading_arrive_home_leg: bool = False,
                                 closure_dwell_model: str | None = None,
                                 closure_dwell_min_obs: int = DEFAULT_CLOSURE_DWELL_MIN_OBS,
                                 w_zweck_10_as_leisure: bool = False,
                                 exclude_no_diary: bool = False,
                                 exclude_holidays: bool = False,
                                 exclude_only_rbw: bool = False
                                 ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Build the MiD home-office-day donor pool: attributes + trip chains + diagnostics.

    Thin orchestration over :func:`select_home_office_day_donors`,
    :func:`filter_donor_diaries`, :func:`donor_attributes` and :func:`donor_trips` (see each for
    the exact per-column semantics); this function's own contribution is the diagnostics summary.

    Every keyword added by issue #374 defaults to the pre-#374 behaviour, so a caller that passes
    none reproduces the previous output byte-identically:

    * ``exclude_rbw_legs`` / ``drop_leading_arrive_home_leg`` -- forwarded to
      :func:`donor_trips` (issue #366).
    * ``closure_dwell_model`` -- ``None`` builds NO dwell model and leaves the synthesised chain
      closure on the constant one-hour dwell, exactly as before. A model NAME (see
      :data:`braunschweig.popsim.trips_stage.CLOSURE_DWELL_MODELS`) is handed to
      :func:`braunschweig.popsim.trips_stage.build_closure_dwell_model` HERE rather than by the
      caller, because the empirical pools must be estimated on the donor diaries this pool
      actually keeps -- i.e. AFTER :func:`filter_donor_diaries` -- and that set exists only
      inside this function. Building it outside would force the caller to repeat the selection
      and the filters, and the two copies could then drift apart.
    * ``closure_dwell_min_obs`` -- minimum observations per (purpose x arrival band) cell of the
      empirical model; inert while ``closure_dwell_model`` is ``None`` or ``"fixed_1h"``.
    * ``w_zweck_10_as_leisure`` -- maps MiD W_ZWECK 10 ("anderer Zweck") to the ``"leisure"``
      purpose instead of ``"other"`` (issue #373, ADR-0111), forwarded to
      :func:`build_closure_dwell_model` (when a dwell model is built) and :func:`donor_trips`,
      following MiD's own hwzweck1 fold. Default False keeps the pre-#373 output byte-identical.
    * ``exclude_no_diary`` / ``exclude_holidays`` / ``exclude_only_rbw`` -- forwarded to
      :func:`filter_donor_diaries`.

    Returns ``(attributes, trips, diagnostics)``. ``attributes`` carries
    :func:`donor_attributes`' columns PLUS the four :func:`attach_trip_derived_attributes` adds
    (``n_trips``, ``is_immobile``, ``has_education_leg``, ``has_work_leg``). ``diagnostics``:

    * ``n_donors`` -- rows in ``attributes`` (every donor that survived the diary filters,
      mobile or immobile).
    * ``n_donors_before_diary_filters``, ``n_dropped_no_diary``, ``n_dropped_no_diary_immobile``,
      ``n_dropped_no_diary_mobil_unknown``, ``n_dropped_holiday``, ``n_dropped_only_rbw`` --
      :func:`filter_donor_diaries`' counts, always present (zero for a filter that is off).
    * ``n_immobile`` -- donors with NO Wege row at all (a genuine immobile home-office day);
      the ``is_immobile`` column of ``attributes`` is the per-donor form of this count.
    * ``n_chain_dropped_by_resample`` -- donors that DID have Wege rows, but ended up with no
      surviving trip in ``trips`` anyway (their chain could not be repaired, and no
      attribute-matched donor could replace it -- see :func:`donor_trips`). Distinct from
      ``n_immobile``: a non-zero rate here is a resample/matching gap, not a real behaviour.
    * ``n_missing_distance`` -- donors with ``distance_source == "unknown"`` (neither
      ``P_ARB_ENTF`` nor a work trip; ``distance_class`` itself is always the literal
      ``DISTANCE_CLASS_UNKNOWN`` in that case, never null).
    * ``n_sex_unknown`` -- donors with ``sex`` unknown (``HP_SEX`` outside {1, 2}).
    * ``n_donors_with_education_leg`` / ``n_donors_with_work_leg`` -- donors whose BUILT chain
      contains an ``education`` / a ``work`` activity (see
      :func:`attach_trip_derived_attributes`). The education count is the size of the pool the
      ruling-R7 hard criterion restricts to persons with an education location of their own; the
      work count is reported for symmetry only (every receiving person has a workplace).
    * ``n_not_in_module`` -- selected donors with ``M_HOFF != MID_MODULE`` (see
      :func:`select_home_office_day_donors`).
    * ``n_household_unmatched`` -- donors whose ``H_ID`` has no row in ``households`` (``has_car``
      is ``NaN`` for them, see :func:`donor_attributes`).
    * ``n_car_unresolved`` -- donors with a MATCHED household whose ``H_ANZAUTO`` is missing,
      negative, or non-numeric (``has_car`` is ``NaN`` for them too, see :func:`donor_attributes`
      and :func:`_count_car_unresolved`).
    * ``distance_source_counts`` -- dict, ``distance_source`` value -> donor count.
    * ``cells`` -- dict, ``(distance_class, has_children_u14, has_active_escort)`` -> donor
      count (one entry per donor; ``distance_class`` is never ``None`` -- the unknown-distance
      cell uses the literal ``DISTANCE_CLASS_UNKNOWN``).
    """
    donors = select_home_office_day_donors(persons)
    donors, diary_filter_counts = filter_donor_diaries(
        donors, wege, exclude_no_diary=exclude_no_diary, exclude_holidays=exclude_holidays,
        exclude_only_rbw=exclude_only_rbw,
    )
    attributes = donor_attributes(donors, persons, households, wege)
    # Built BEFORE the trip table and from the FILTERED donors, mirroring trips_stage.run: the
    # empirical model must see the donor diaries as reported, i.e. before any synthetic closure
    # has been appended to them, and only the diaries this pool actually builds days from.
    dwell_model = None
    if closure_dwell_model is not None:
        dwell_model = build_closure_dwell_model(
            _donors_as_persons(donors), wege,
            closure_dwell_model=closure_dwell_model,
            random_seed=random_seed,
            closure_dwell_min_obs=closure_dwell_min_obs,
            escort_purpose=escort_purpose,
            escort_passive_education=escort_passive_education,
            explicit_round_trip_purposes=explicit_round_trip_purposes,
            exclude_rbw_legs=exclude_rbw_legs,
            drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
            w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        )
    trips = donor_trips(
        donors, attributes, wege, random_seed=random_seed, escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        explicit_round_trip_purposes=explicit_round_trip_purposes,
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        dwell_model=dwell_model,
    )
    if dwell_model is not None:
        # AFTER the trip build, exactly where trips_stage.run logs the identical report: the
        # model's counters (n_draws, n_fallback_purpose_marginal, n_fallback_global, n_capped)
        # are filled BY the build, so logging at construction time would always report zeros and
        # hide a 100 % fallback rate -- the one thing the report exists to make observable.
        #
        # A SNAPSHOT (dict(...)), never the live mapping. ClosureDwellModel.report is one dict
        # that draw() mutates in place, and logging stores the ARGUMENT in LogRecord.args and
        # renders it lazily -- logging.LogRecord.getMessage() recomputes msg % args on every
        # call. A handler that formats late (a QueueHandler, a deferred formatter, a test
        # inspecting the record afterwards) would therefore render the counters as they are at
        # FORMAT time, not as they were at EMIT time: the record would silently report a state
        # the run never actually logged. Freezing the mapping here makes the line mean what it
        # says whenever it is rendered.
        logger.info("%s closure dwell model (%s) report: %s", _LOG_TAG, closure_dwell_model,
                    dict(dwell_model.report))
    # n_trips / is_immobile / has_education_leg / has_work_leg can only be read once the chains
    # exist (rulings R7 and R9); donor_attributes never sees them.
    attributes = attach_trip_derived_attributes(attributes, trips, wege)

    n_donors = len(attributes)
    # A donor with ANY row in the raw Wege file at all is not immobile -- even if that chain was
    # later dropped by the repair/resample cascade (see donor_trips' docstring). Both counts read
    # the SAME is_immobile column the attributes frame now carries, so the diagnostics here and
    # the plan-replacement step downstream can never disagree about which donor is which.
    is_immobile = attributes["is_immobile"].astype(bool)
    has_no_trips = attributes["n_trips"] == 0

    n_immobile = int(is_immobile.sum())
    n_chain_dropped_by_resample = int((~is_immobile & has_no_trips).sum())
    n_missing_distance = int((attributes["distance_source"] == DISTANCE_SOURCE_UNKNOWN).sum())
    n_sex_unknown = int(attributes["sex"].isna().sum())
    n_donors_with_education_leg = int(attributes["has_education_leg"].sum())
    n_donors_with_work_leg = int(attributes["has_work_leg"].sum())
    n_not_in_module = _count_not_in_module(donors)
    n_household_unmatched = _count_household_unmatched(attributes["H_ID"], households)
    n_car_unresolved = _count_car_unresolved(attributes["H_ID"], households)
    distance_source_counts = attributes["distance_source"].value_counts().to_dict()

    cells: dict = {}
    for distance_class, has_children_u14, has_active_escort in zip(
        attributes["distance_class"], attributes["has_children_u14"], attributes["has_active_escort"]
    ):
        key = (distance_class, bool(has_children_u14), bool(has_active_escort))
        cells[key] = cells.get(key, 0) + 1

    diagnostics = {
        "n_donors": n_donors,
        **diary_filter_counts,
        "n_immobile": n_immobile,
        "n_chain_dropped_by_resample": n_chain_dropped_by_resample,
        "n_missing_distance": n_missing_distance,
        "n_sex_unknown": n_sex_unknown,
        "n_donors_with_education_leg": n_donors_with_education_leg,
        "n_donors_with_work_leg": n_donors_with_work_leg,
        "n_not_in_module": n_not_in_module,
        "n_household_unmatched": n_household_unmatched,
        "n_car_unresolved": n_car_unresolved,
        "distance_source_counts": distance_source_counts,
        "cells": cells,
    }
    log_mobility = logger.warning if n_chain_dropped_by_resample > 0 else logger.info
    log_mobility(
        "%s donor mobility: %d/%d donors immobile (no Wege rows, %.1f%%), %d/%d had Wege rows but "
        "no surviving trip chain after repair/resample (%.1f%%, a resample gap, not real "
        "behaviour)", _LOG_TAG, n_immobile, n_donors, 100.0 * n_immobile / max(n_donors, 1),
        n_chain_dropped_by_resample, n_donors, 100.0 * n_chain_dropped_by_resample / max(n_donors, 1),
    )
    logger.info(
        "%s donor pool built: %d donors, %d missing distance (%.1f%%), %d sex unknown, %d not in "
        "home-office module, %d household-unmatched, %d car-unresolved",
        _LOG_TAG, n_donors, n_missing_distance, 100.0 * n_missing_distance / max(n_donors, 1),
        n_sex_unknown, n_not_in_module, n_household_unmatched, n_car_unresolved,
    )
    logger.info(
        "%s fixed-purpose legs in the donor chains: %d/%d donors (%.1f%%) carry an %r activity "
        "(only matchable to persons WITH an education location, ruling R7), %d/%d (%.1f%%) carry "
        "a %r activity (matchable to anyone -- every receiving person has a workplace)",
        _LOG_TAG, n_donors_with_education_leg, n_donors,
        100.0 * n_donors_with_education_leg / max(n_donors, 1), EDUCATION_PURPOSE,
        n_donors_with_work_leg, n_donors, 100.0 * n_donors_with_work_leg / max(n_donors, 1),
        WORK_PURPOSE,
    )
    return attributes, trips, diagnostics
