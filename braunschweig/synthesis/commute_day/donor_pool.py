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
  :func:`braunschweig.popsim.trips.build_validated_trip_table` using EXACTLY the keyword
  arguments :func:`braunschweig.popsim.trips_stage.run` passes (``resample=True``, the escort /
  round-trip flags, ``random_seed``) -- ruling R2 -- but WITHOUT ``trips_stage.run``'s
  per-person departure-time jitter step: the jitter is applied ONCE later, in the plan-replacement
  step, keyed by the RECEIVING synthetic person, not the donor (applying it here would jitter the
  same donor's day twice once it is copied onto a worker).

``household_size`` (MiD ``H_GR``) is carried through UNBINNED: the class binning
(``household_size_class``) is the responsibility of the later matching module, which must apply
the IDENTICAL binning to both the donor and the worker side (ruling R1) -- binning it here would
risk the two sides drifting apart.
"""
from __future__ import annotations

import logging

import pandas as pd

from braunschweig.calibration.commute_day_state_reference import (
    MID_AT_HOME,
    MID_CHILD_MAX_AGE,
    MID_ESCORT_ACTIVE,
    MID_MODULE,
    MID_SEX_FEMALE,
    MID_TRIP_LENGTH_MAX_KM,
    MID_WEEKDAY,
    MID_WORK_TRIP_PURPOSE,
    MID_WORKED_ON_DAY,
    classify_commute_distance,
    clean_mid_commute_distance_km,
)
from braunschweig.constants import ROUTED_DETOUR_FACTOR as DETOUR_FACTOR
from braunschweig.popsim.chain_matching import derive_age_class
from braunschweig.popsim.trips import build_validated_trip_table
from braunschweig.popsim.trips_stage import CONTRACT

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day donors]"

# MiD HP_SEX -> the eqasim-style sex label used by the matching module. Code MID_SEX_FEMALE (2)
# is imported from commute_day_state_reference (the single committed source for that code); the
# male code (1) is MiD-codeplan-documented but not otherwise referenced elsewhere in the
# repository, so it is defined locally rather than adding an unused constant to the reference
# module. Codes 3 (diverse) and 9 (no answer) map to NaN -- counted, never guessed.
MID_SEX_MALE = 1
SEX_LABEL_BY_HP_SEX = {MID_SEX_MALE: "male", MID_SEX_FEMALE: "female"}

#: Values of ``distance_source`` on the attributes frame (see :func:`donor_attributes`).
DISTANCE_SOURCE_P_ARB_ENTF = "P_ARB_ENTF"
DISTANCE_SOURCE_TRIP_LENGTH = "trip_length"
DISTANCE_SOURCE_UNKNOWN = "unknown"


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


def _first_work_trip_km(wege: pd.DataFrame) -> pd.DataFrame:
    """First valid work-trip length in km per ``(H_ID, P_ID)``, sorted by ``W_ID``.

    Same qualifying condition as
    :func:`braunschweig.calibration.commute_day_state_reference.first_work_trip_length_km``
    (``W_ZWECK == MID_WORK_TRIP_PURPOSE`` and ``0 < wegkm < MID_TRIP_LENGTH_MAX_KM``), but sorts
    explicitly by ``W_ID`` before keeping the first qualifying row per person (ruling R2): this
    fallback distance source must be reproducible independent of the caller's row order, whereas
    the reference helper only guarantees "first in file order".

    Returns one row per ``(H_ID, P_ID)`` with a qualifying trip, columns ``H_ID``, ``P_ID``,
    ``work_trip_length_km``. Persons without a qualifying trip are simply absent.
    """
    _require_columns(wege, ("H_ID", "P_ID", "W_ID", "W_ZWECK", "wegkm"), "wege frame")
    length_km = pd.to_numeric(wege["wegkm"], errors="coerce")
    qualifies = ((wege["W_ZWECK"] == MID_WORK_TRIP_PURPOSE) & length_km.notna()
                 & (length_km > 0) & (length_km < MID_TRIP_LENGTH_MAX_KM))
    selected = wege.loc[qualifies, ["H_ID", "P_ID", "W_ID"]].copy()
    selected["work_trip_length_km"] = length_km[qualifies].to_numpy()
    first = (selected.sort_values(["H_ID", "P_ID", "W_ID"])
             .drop_duplicates(subset=["H_ID", "P_ID"], keep="first")
             .reset_index(drop=True))
    logger.info("%s work-trip-length fallback: %d/%d Wege rows qualify; %d distinct persons keep "
                "their first (by W_ID) qualifying trip", _LOG_TAG, int(qualifies.sum()), len(wege),
                len(first))
    return first[["H_ID", "P_ID", "work_trip_length_km"]]


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
    * ``has_car`` -- ``H_ANZAUTO > 0`` from ``households``, joined on ``H_ID``.
    * ``has_active_escort`` -- any ``wege`` row for the donor's ``(H_ID, P_ID)`` with ``W_ZWECK
      == MID_ESCORT_ACTIVE``.
    * ``household_size`` -- ``H_GR`` from ``households``, joined on ``H_ID``, UNBINNED (ruling
      R1: the class binning is the matching module's responsibility, applied identically to both
      sides).
    * ``distance_km`` / ``distance_class`` / ``distance_source`` -- ``P_ARB_ENTF`` cleaned with
      :func:`clean_mid_commute_distance_km` and classified with the default MiD top-code when
      valid (``distance_source == "P_ARB_ENTF"``); else the donor's first work-trip length in km
      (see :func:`_first_work_trip_km`), classified with ``topcode_km=None`` -- a raw trip length
      was never subject to the MiD ``P_ARB_ENTF`` 200 km top-code (``distance_source ==
      "trip_length"``); else ``distance_class = NaN`` and ``distance_source == "unknown"``
      (counted -- CLAUDE.md fallback transparency).

    Donors without any qualifying trip (e.g. an immobile home-office day) still get a row here;
    :func:`donor_trips` is what determines whether they have ``n_trips == 0``.
    """
    _require_columns(donors, ("HP_ID", "H_ID", "P_ID", "HP_SEX", "HP_ALTER", "P_ARB_ENTF"),
                     "donors frame")
    _require_columns(persons_all, ("H_ID", "HP_ALTER"), "persons_all frame")
    _require_columns(households, ("H_ID", "H_ANZAUTO", "H_GR"), "households frame")

    attributes = donors[["HP_ID", "H_ID", "P_ID", "HP_SEX", "HP_ALTER", "P_ARB_ENTF"]].copy()
    attributes = attributes.rename(columns={"HP_ID": "donor_id"})

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
    attributes["has_car"] = attributes["H_ID"].map(household_lookup["H_ANZAUTO"]).gt(0)
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

    work_trip = _first_work_trip_km(wege)
    attributes = attributes.merge(work_trip, on=["H_ID", "P_ID"], how="left")
    class_from_trip_length = pd.Series(
        [classify_commute_distance(km, topcode_km=None) for km in attributes["work_trip_length_km"]],
        index=attributes.index)
    has_trip_length = class_from_trip_length.notna()

    distance_km = cleaned_p_arb_entf.where(has_p_arb_entf, attributes["work_trip_length_km"])
    distance_class = class_from_p_arb_entf.where(has_p_arb_entf, class_from_trip_length)
    distance_source = pd.Series(DISTANCE_SOURCE_UNKNOWN, index=attributes.index, dtype=object)
    distance_source[has_p_arb_entf] = DISTANCE_SOURCE_P_ARB_ENTF
    distance_source[(~has_p_arb_entf) & has_trip_length] = DISTANCE_SOURCE_TRIP_LENGTH
    attributes["distance_km"] = distance_km
    attributes["distance_class"] = distance_class
    attributes["distance_source"] = distance_source

    source_counts = attributes["distance_source"].value_counts().to_dict()
    n_missing_distance = int(attributes["distance_class"].isna().sum())
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


def donor_trips(donors: pd.DataFrame, wege: pd.DataFrame, *, random_seed: int,
                escort_purpose: bool, escort_passive_education: bool,
                explicit_round_trip_purposes: bool) -> pd.DataFrame:
    """The donors' own trip chains in the ``synthesis.population.trips`` CONTRACT, ``donor_id``-keyed.

    Built with :func:`braunschweig.popsim.trips.build_validated_trip_table` using EXACTLY the
    keyword arguments :func:`braunschweig.popsim.trips_stage.run` passes (``resample=True``, the
    escort/round-trip flags, ``random_seed``) -- ruling R2 -- but WITHOUT
    ``trips_stage.run``'s per-person departure-time jitter step
    (:func:`braunschweig.popsim.trips_stage.apply_per_person_jitter`): that jitter is applied
    ONCE later, in the plan-replacement step, keyed by the RECEIVING synthetic person -- applying
    it here would jitter the same donor's day a second time once it is copied onto a worker.
    ``resample_cell_col`` is fixed to ``None``: the legacy same-cell resample fallback needs a
    synthetic-population cell column (e.g. ``ZENSUS100m``) that MiD donors do not carry.

    ``donors`` needs ``HP_ID`` (must be unique -- raises otherwise), ``H_ID``, ``P_ID``. Donors
    without any MiD trip yield NO rows here (an immobile home-office day): the inner join inside
    ``build_validated_trip_table`` drops persons whose donor key matches no Wege row. They still
    appear in :func:`donor_attributes` with ``n_trips == 0`` (computed by the caller from the
    absence of rows here, see :func:`build_home_office_donor_pool`).

    Returns one row per (donor, trip): the ``trips_stage.CONTRACT`` columns (``person_id``
    renamed to ``donor_id``) plus ``euclidean_distance`` (metres, from MiD ``wegkm_imp`` * 1000 /
    the ENTD detour factor -- the same convention as ``trips_stage.run``) and ``trip_key``
    (traceability). The raw MiD Wege extras (``W_ZWECK``, ``hvm_imp``, ``wegkm``, ...) are
    dropped to keep the donor pool small; the docstring here is the single place documenting
    that intentional narrowing.
    """
    _require_columns(donors, ("HP_ID", "H_ID", "P_ID"), "donors frame")
    if donors["HP_ID"].duplicated().any():
        n_duplicated = int(donors["HP_ID"].duplicated().sum())
        raise ValueError(f"{_LOG_TAG} donors frame has {n_duplicated} duplicate HP_ID value(s); "
                         "HP_ID must be unique per donor.")

    donor_persons = donors[["HP_ID", "H_ID", "P_ID"]].rename(columns={"HP_ID": "person_id"})
    table, report = build_validated_trip_table(
        donor_persons, wege,
        resample=True,
        resample_cell_col=None,
        random_seed=random_seed,
        escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        explicit_round_trip_purposes=explicit_round_trip_purposes,
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


def build_home_office_donor_pool(persons: pd.DataFrame, wege: pd.DataFrame, households: pd.DataFrame,
                                 *, random_seed: int, escort_purpose: bool = False,
                                 escort_passive_education: bool = False,
                                 explicit_round_trip_purposes: bool = True
                                 ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Build the MiD home-office-day donor pool: attributes + trip chains + diagnostics.

    Thin orchestration over :func:`select_home_office_day_donors`, :func:`donor_attributes` and
    :func:`donor_trips` (see each for the exact per-column semantics); this function's own
    contribution is the diagnostics summary.

    Returns ``(attributes, trips, diagnostics)``. ``diagnostics``:

    * ``n_donors`` -- rows in ``attributes`` (every selected donor, mobile or immobile).
    * ``n_immobile`` -- donors with no row in ``trips`` (an immobile home-office day).
    * ``n_missing_distance`` -- donors with ``distance_class`` unknown (neither ``P_ARB_ENTF``
      nor a work trip).
    * ``n_sex_unknown`` -- donors with ``sex`` unknown (``HP_SEX`` outside {1, 2}).
    * ``n_not_in_module`` -- selected donors with ``M_HOFF != MID_MODULE`` (see
      :func:`select_home_office_day_donors`).
    * ``distance_source_counts`` -- dict, ``distance_source`` value -> donor count.
    * ``cells`` -- dict, ``(distance_class, has_children_u14, has_active_escort)`` -> donor
      count (one entry per donor; ``distance_class`` is ``None`` for the unknown-distance cell).
    """
    donors = select_home_office_day_donors(persons)
    attributes = donor_attributes(donors, persons, households, wege)
    trips = donor_trips(
        donors, wege, random_seed=random_seed, escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        explicit_round_trip_purposes=explicit_round_trip_purposes,
    )

    n_donors = len(attributes)
    mobile_donor_ids = set(trips["donor_id"].unique()) if len(trips) else set()
    n_immobile = int((~attributes["donor_id"].isin(mobile_donor_ids)).sum())
    n_missing_distance = int(attributes["distance_class"].isna().sum())
    n_sex_unknown = int(attributes["sex"].isna().sum())
    n_not_in_module = _count_not_in_module(donors)
    distance_source_counts = attributes["distance_source"].value_counts().to_dict()

    cells: dict = {}
    for distance_class, has_children_u14, has_active_escort in zip(
        attributes["distance_class"], attributes["has_children_u14"], attributes["has_active_escort"]
    ):
        key = (distance_class, bool(has_children_u14), bool(has_active_escort))
        cells[key] = cells.get(key, 0) + 1

    diagnostics = {
        "n_donors": n_donors,
        "n_immobile": n_immobile,
        "n_missing_distance": n_missing_distance,
        "n_sex_unknown": n_sex_unknown,
        "n_not_in_module": n_not_in_module,
        "distance_source_counts": distance_source_counts,
        "cells": cells,
    }
    logger.info(
        "%s donor pool built: %d donors, %d immobile (%.1f%%), %d missing distance (%.1f%%), "
        "%d sex unknown, %d not in home-office module",
        _LOG_TAG, n_donors, n_immobile, 100.0 * n_immobile / max(n_donors, 1),
        n_missing_distance, 100.0 * n_missing_distance / max(n_donors, 1), n_sex_unknown,
        n_not_in_module,
    )
    return attributes, trips, diagnostics
