"""SrV 2023 commute-parking and city-centre parking tables (issue #249).

Pure pandas builders over already-loaded frames of the SrV 2023 "Braunschweig und RGB"
scientific-use file (SciUse_v4): no file access, no synpp dependency, not imported by any
pipeline stage. ``scripts/extract_srv_commute_parking.py`` reads the LOCAL-ONLY raw files and
writes the two committed aggregates:

* :data:`COMMUTE_TABLE_FILE` -- weighted parking place and payment of car commuters per
  workplace class. ``share_free_total`` of a class is the probability of the free-parking draw
  of the parking-cost-zones design (spec 2026-09-28, section 3.4); its assumption A1 transfers
  the share observed among CURRENT car commuters to all workers/students of the class.
* :data:`CITY_CENTER_TABLE_FILE` -- weighted place shares and the paid share of the usual
  Braunschweig city-centre parking of Braunschweig residents; a validation quantity only (spec
  section 6), never a model input.

Survey variables (codebook ``SrV2023_Datenkodierung_SciUse.xlsx``; the audit with every column
name is recorded in the docstring of ``tests/test_srv_parking.py``):

* ``V_BRAU_PARKENAPL`` / ``V_RGB_PARKENAPL`` (the P2 add-on person modules of the Braunschweig
  and the RGB sample): usual parking place when driving to work/education, coded as in
  :data:`PARKENAPL_CODES`; negative values (-8 not asked, -10 implausible) are SrV missing
  codes.
* ``<place column><k>_ENTGELT`` for k in :data:`PAYMENT_FOLLOWUP_CODES` (see
  :func:`payment_followup_column`): free (1) or paid (2) at place k. Only the follow-up that
  matches the reported place is read; a valid answer in a non-matching follow-up is counted and
  logged, never used.
* ``V_BRAU_PARKENCITY`` (Braunschweig module only): usual parking place in the city centre, with
  the DIFFERENT code order of :data:`PARKENCITY_CODES` and its own follow-ups.
* ``GEWICHT_P_ZENSUS`` (person file): expansion weight to Zensus 2022 counts, the weight for
  aggregates across sampling strata (the stratum-internal ``GEWICHT_P`` must not be used; same
  convention as ``scripts/extract_srv_kreis_tables.py``). Every weighted row carries the Kish
  effective sample size ``n_eff = (sum w) ** 2 / sum(w ** 2)``.
* Trips: ``WNR``, ``V_ZWECK``, ``V_ZIEL_AGS``, ``V_ZIEL_OBERBEZIRK``.

Workplace class (spec section 3.4): the destination of the person's FIRST trip of the survey day
(lowest ``WNR``) whose purpose is in :data:`COMMUTE_PURPOSES` -- own workplace or vocational
school/university, the two destinations the add-on question names. The Kreis is the first five
digits of the zero-padded destination AGS
(``braunschweig.calibration.srv_distance_targets.kreis_from_ags``, which turns SrV sentinels into
NaN). Braunschweig destinations are split by the SrV Oberbezirk (``SrV2023_Teilraumkodierung``:
1 Zentrum -> ``bs_zentrum``; 2 Innenbereich West and 3 Innenbereich Ost -> ``bs_innenbereich``;
4 Stadtteilring, 5 Aussenbereich Sued-Ost and 6 Aussenbereich Nord-West -> ``bs_outer``). The
Oberbezirk number is defined per survey area, so it is read for Braunschweig destinations only.
Every other ZGB Kreis is its own class; Wolfsburg (``03103``) is observed through in-commuters
only, because its residents are not in the sample. Four further labels never become class rows
but are counted (:data:`UNCLASSIFIED_LABELS`): ``outside_zgb``, ``unknown_destination`` (missing
AGS), ``bs_unknown_oberbezirk`` (Braunschweig destination whose district could not be computed)
and ``no_commute_trip`` (no work/education trip on the survey day).

No silent fallbacks: every respondent that leaves the universe or the class rows is counted,
returned in a report dict and logged with its rate; unmapped codes, unexpected class labels,
respondents missing from the person file and classes below :data:`MIN_CELL_N` raise. Log
messages carry counts only, never respondent keys.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_distance_targets import (
    PURPOSE_TERTIARY,
    PURPOSE_WORK,
    ZGB_KREISE,
    kreis_from_ags,
)

logger = logging.getLogger(__name__)

_LOG_TAG = "[srv parking]"

# --- Survey codes (codebook SrV2023_Datenkodierung_SciUse.xlsx) -----------------------------

#: V_BRAU_PARKENAPL / V_RGB_PARKENAPL: usual parking place when driving to work/education.
PARKENAPL_CODES = {1: "employer_lot", 2: "street", 3: "garage_large_lot", 4: "no_car_commute", 5: "other"}
#: V_BRAU_PARKENCITY: usual parking place in the Braunschweig city centre. The codebook orders the
#: places differently from PARKENAPL (1 street, 2 garage, 3 employer lot), so it needs its own map.
PARKENCITY_CODES = {1: "street", 2: "garage_large_lot", 3: "employer_lot", 4: "no_car_to_city_center", 5: "other"}
#: V_*_PARKENAPL<k>_ENTGELT and V_BRAU_PARKENCITY<k>_ENTGELT: payment at the reported place.
ENTGELT_CODES = {1: "free", 2: "paid"}
#: Places with a payment follow-up in both question blocks (code 4, "no car", has none).
PAYMENT_FOLLOWUP_CODES = (1, 2, 3, 5)
#: V_ZWECK 1 (own workplace) and 6 (vocational school/university): the add-on question covers both.
COMMUTE_PURPOSES = frozenset({PURPOSE_WORK, PURPOSE_TERTIARY})
#: The eight ZGB Kreis keys, from the single source of truth in srv_distance_targets.
ZGB_KREIS_KEYS = frozenset(ZGB_KREISE)
BRAUNSCHWEIG_KREIS_KEY = "03101"
#: SrV Oberbezirke of Braunschweig (SrV2023_Teilraumkodierung_Braunschweig und RGB.xlsx).
BS_ZENTRUM_OBERBEZIRKE = frozenset({1})
BS_INNENBEREICH_OBERBEZIRKE = frozenset({2, 3})
BS_OUTER_OBERBEZIRKE = frozenset({4, 5, 6})
#: A workplace class with fewer unweighted car commuters raises instead of being written.
MIN_CELL_N = 100

# --- Column names --------------------------------------------------------------------------

PERSON_KEYS = ["HHNR", "PNR"]
TRIP_NUMBER_COLUMN = "WNR"
PURPOSE_COLUMN = "V_ZWECK"
DESTINATION_AGS_COLUMN = "V_ZIEL_AGS"
DESTINATION_OBERBEZIRK_COLUMN = "V_ZIEL_OBERBEZIRK"
WEIGHT_COLUMN = "GEWICHT_P_ZENSUS"
#: Column prefix per add-on module (codebook levels P2_BRAU and P2_RGB).
MODULE_PREFIXES = {"BRAU": "V_BRAU", "RGB": "V_RGB"}
CITY_CENTER_PLACE_COLUMN = "V_BRAU_PARKENCITY"

# --- Labels ----------------------------------------------------------------------------------

NO_CAR_COMMUTE = PARKENAPL_CODES[4]
NO_CAR_TO_CITY_CENTER = PARKENCITY_CODES[4]
#: Parking places of drivers, in table column order.
DRIVER_PARKING_TYPES = ("employer_lot", "street", "garage_large_lot", "other")
PAYMENTS = ("free", "paid")

BS_ZENTRUM = "bs_zentrum"
BS_INNENBEREICH = "bs_innenbereich"
BS_OUTER = "bs_outer"
#: Workplace classes of spec section 3.4, in table row order.
WORKPLACE_CLASSES = (BS_ZENTRUM, BS_INNENBEREICH, BS_OUTER,
                     *sorted(ZGB_KREIS_KEYS - {BRAUNSCHWEIG_KREIS_KEY}))
OUTSIDE_ZGB = "outside_zgb"
UNKNOWN_DESTINATION = "unknown_destination"
BS_UNKNOWN_OBERBEZIRK = "bs_unknown_oberbezirk"
NO_COMMUTE_TRIP = "no_commute_trip"
#: Labels that are counted but never become a class row (and are not pooled in the total row).
UNCLASSIFIED_LABELS = (NO_COMMUTE_TRIP, OUTSIDE_ZGB, UNKNOWN_DESTINATION, BS_UNKNOWN_OBERBEZIRK)

LEVEL_CLASS = "class"
LEVEL_TOTAL = "total"
TOTAL_ROW_CLASS = "total"
PAID_SHARE_OVERALL = "paid_share_overall"

# --- Output tables -----------------------------------------------------------------------------

COMMUTE_TABLE_FILE = "srv2023_commute_parking_by_workplace_class.csv"
CITY_CENTER_TABLE_FILE = "srv2023_city_center_parking.csv"
COMMUTE_TABLE_COLUMNS = (
    "workplace_class", "level", "n_unweighted", "n_eff",
    "share_employer_lot", "share_street", "share_garage_large_lot", "share_other",
    "share_paid_total", "share_free_total",
)
CITY_CENTER_TABLE_COLUMNS = ("parking_type", "share", "n_unweighted")


def payment_followup_column(place_column: str, code: int) -> str:
    """Name of the payment follow-up of ``place_column`` for place ``code``.

    The codebook names them ``<place column><code>_ENTGELT``, e.g.
    ``V_RGB_PARKENAPL2_ENTGELT`` or ``V_BRAU_PARKENCITY3_ENTGELT``.
    """
    return f"{place_column}{code}_ENTGELT"


# --- Small helpers ---------------------------------------------------------------------------

def _rate(count: int, total: int) -> str:
    if total <= 0:
        return f"{count}/{total}"
    return f"{count}/{total} ({100.0 * count / total:.1f}%)"


def _require_columns(frame: pd.DataFrame, columns, source: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{source}: missing required column(s) {missing}")


def _reject_duplicate_persons(frame: pd.DataFrame, source: str) -> None:
    duplicated = frame.duplicated(PERSON_KEYS, keep=False)
    if duplicated.any():
        raise ValueError(
            f"{source}: {int(duplicated.sum())} rows carry a duplicate (HHNR, PNR) person key; "
            "every respondent must appear exactly once or it would be weighted twice")


def _log_report(title: str, report: dict) -> None:
    total = report["n_input"]
    parts = [f"{key} {_rate(value, total)}" for key, value in report.items() if key != "n_input"]
    logger.info("%s %s: n_input=%d, %s", _LOG_TAG, title, total, ", ".join(parts))


def _log_distribution(title: str, labels: pd.Series) -> None:
    total = len(labels)
    parts = [f"{label} {_rate(int(count), total)}" for label, count in labels.value_counts().items()]
    logger.info("%s %s (n=%d): %s", _LOG_TAG, title, total, ", ".join(parts))


def _numeric_codes(values: pd.Series, column: str) -> pd.Series:
    """SrV integer codes as numbers; empty cells stay NaN, anything non-numeric raises."""
    numeric = pd.to_numeric(values, errors="coerce")
    non_numeric = numeric.isna() & values.notna()
    if non_numeric.any():
        raise ValueError(f"{column}: {int(non_numeric.sum())} non-numeric value(s); expected SrV codes")
    fractional = numeric.notna() & (numeric % 1 != 0)
    if fractional.any():
        raise ValueError(f"{column}: {int(fractional.sum())} non-integer value(s); expected SrV codes")
    return numeric


def _map_codes(numeric: pd.Series, codes: dict, column: str) -> pd.Series:
    """Labels for SrV codes: empty cells and negative missing codes become None, an unmapped
    non-negative code raises (it would otherwise vanish from every share)."""
    valid = numeric.notna() & (numeric >= 0)
    unmapped = sorted(set(numeric[valid].astype(int)) - set(codes))
    if unmapped:
        raise ValueError(
            f"{column}: unmapped code(s) {unmapped}; extend the code map deliberately from the "
            "codebook SrV2023_Datenkodierung_SciUse.xlsx")
    labels = pd.Series(None, index=numeric.index, dtype=object)
    labels[valid] = numeric[valid].astype(int).map(codes)
    return labels


def _resolve_parking_answers(frame: pd.DataFrame, place_column: str, place_codes: dict):
    """``(parking_type, payment)`` label series for one question block.

    The payment of a respondent is read from the follow-up of the place they reported; places
    without a follow-up (code 4) and missing follow-up answers give None. A follow-up column is
    required only when some respondent reports its place.
    """
    place_numeric = _numeric_codes(frame[place_column], place_column)
    parking_type = _map_codes(place_numeric, place_codes, place_column)
    payment = pd.Series(None, index=frame.index, dtype=object)
    n_answers_ignored = 0
    for code in PAYMENT_FOLLOWUP_CODES:
        column = payment_followup_column(place_column, code)
        reported = place_numeric == code
        if column not in frame.columns:
            if reported.any():
                raise ValueError(
                    f"{column}: column missing although {int(reported.sum())} respondent(s) report "
                    f"{place_column} == {code}; their payment cannot be resolved")
            continue
        answers = _map_codes(_numeric_codes(frame[column], column), ENTGELT_CODES, column)
        payment[reported] = answers[reported]
        n_answers_ignored += int((answers.notna() & ~reported).sum())
    if n_answers_ignored:
        logger.warning(
            "%s %s: %d payment answer(s) sit in a follow-up that does not match the reported place "
            "(or next to a missing place code); they are ignored", _LOG_TAG, place_column,
            n_answers_ignored)
    return parking_type, payment


def _log_answer_codes(source: str, place_column: str, raw_codes: pd.Series,
                      parking_type: pd.Series, no_car_label: str) -> None:
    numeric = pd.to_numeric(raw_codes, errors="coerce")
    total = len(raw_codes)
    n_driver = int(parking_type.isin(DRIVER_PARKING_TYPES).sum())
    n_no_car = int((parking_type == no_car_label).sum())
    n_not_asked = int((numeric == -8).sum())
    n_implausible = int((numeric == -10).sum())
    n_other_missing = total - n_driver - n_no_car - n_not_asked - n_implausible
    logger.info(
        "%s %s %s: parking place given %s, %s %s, not asked (-8) %s, implausible (-10) %s, "
        "other missing %s", _LOG_TAG, source, place_column, _rate(n_driver, total), no_car_label,
        _rate(n_no_car, total), _rate(n_not_asked, total), _rate(n_implausible, total),
        _rate(n_other_missing, total))


def _attach_person_weight(frame: pd.DataFrame, persons: pd.DataFrame, source: str) -> pd.DataFrame:
    """Left-join ``GEWICHT_P_ZENSUS`` as column ``weight``; an unmatched respondent raises."""
    _require_columns(persons, [*PERSON_KEYS, WEIGHT_COLUMN], "person file")
    _reject_duplicate_persons(persons, "person file")
    merged = frame.merge(persons[[*PERSON_KEYS, WEIGHT_COLUMN]], on=PERSON_KEYS, how="left",
                         indicator=True)
    n_unmatched = int((merged["_merge"] == "left_only").sum())
    if n_unmatched:
        raise ValueError(
            f"{source}: {n_unmatched} respondent(s) have no row in the person file; the add-on "
            "modules and SrV2023_Personen.csv must come from the same delivery")
    merged = merged.drop(columns="_merge").rename(columns={WEIGHT_COLUMN: "weight"})
    merged["weight"] = pd.to_numeric(merged["weight"], errors="coerce")
    return merged


def _invalid_weight(weight: pd.Series) -> pd.Series:
    """Missing, non-finite or non-positive weights (including the SrV missing code -6)."""
    return ~(np.isfinite(weight) & (weight > 0))


# --- Commute parking ---------------------------------------------------------------------------

def unify_addon_modules(bs: pd.DataFrame, rgb: pd.DataFrame) -> pd.DataFrame:
    """Stack the Braunschweig and RGB add-on modules under neutral column names.

    ``bs`` and ``rgb`` are the raw P2 modules (``V_BRAU_*`` and ``V_RGB_*`` columns). Returns one
    row per respondent, Braunschweig first, with ``HHNR``, ``PNR``, ``module`` (``BRAU``/``RGB``),
    ``parking_type`` (a :data:`PARKENAPL_CODES` label; None for a missing code) and ``payment``
    (``free``/``paid``; None when the reported place has no follow-up or no valid answer).

    Raises ValueError for a person key that occurs twice within or across the modules, an
    unmapped code, or a follow-up column that a reported place needs but the frame lacks.
    """
    parts = []
    for module, frame in (("BRAU", bs), ("RGB", rgb)):
        source = f"add-on module {module}"
        place_column = f"{MODULE_PREFIXES[module]}_PARKENAPL"
        _require_columns(frame, [*PERSON_KEYS, place_column], source)
        _reject_duplicate_persons(frame, source)
        parking_type, payment = _resolve_parking_answers(frame, place_column, PARKENAPL_CODES)
        _log_answer_codes(source, place_column, frame[place_column], parking_type, NO_CAR_COMMUTE)
        part = frame[PERSON_KEYS].copy()
        part["module"] = module
        part["parking_type"] = parking_type
        part["payment"] = payment
        parts.append(part)
    unified = pd.concat(parts, ignore_index=True)
    _reject_duplicate_persons(unified, "add-on modules BRAU and RGB combined")
    return unified


def commute_parking_universe(addon: pd.DataFrame, persons: pd.DataFrame):
    """Car commuters with a valid parking place, payment and weight.

    ``addon`` is the output of :func:`unify_addon_modules`; ``persons`` carries ``HHNR``, ``PNR``
    and ``GEWICHT_P_ZENSUS``. Returns ``(universe, report)``: the universe has the add-on columns
    plus ``weight``; the report counts every respondent once, under the first rule it fails, in
    this order: ``dropped_invalid_weight`` (missing or non-positive weight),
    ``dropped_missing_type`` (missing place code), ``excluded_no_car_commute`` (code 4, outside
    the universe by definition), ``dropped_missing_payment`` (no valid follow-up for the reported
    place); plus ``n_input`` and ``n_universe``.

    Raises ValueError when a respondent is missing from the person file or the universe is empty.
    """
    _require_columns(addon, [*PERSON_KEYS, "parking_type", "payment"], "unified add-on frame")
    frame = _attach_person_weight(addon, persons, "unified add-on frame")

    invalid_weight = _invalid_weight(frame["weight"])
    remaining = ~invalid_weight
    missing_type = remaining & frame["parking_type"].isna()
    remaining &= ~missing_type
    no_car = remaining & (frame["parking_type"] == NO_CAR_COMMUTE)
    remaining &= ~no_car
    missing_payment = remaining & frame["payment"].isna()
    remaining &= ~missing_payment

    universe = frame[remaining].reset_index(drop=True)
    report = {
        "n_input": len(frame),
        "dropped_invalid_weight": int(invalid_weight.sum()),
        "dropped_missing_type": int(missing_type.sum()),
        "excluded_no_car_commute": int(no_car.sum()),
        "dropped_missing_payment": int(missing_payment.sum()),
        "n_universe": len(universe),
    }
    _log_report("commute universe", report)
    if universe.empty:
        raise ValueError(
            "commute universe is empty: no respondent reports a parking place with a valid payment "
            "and weight; check the code maps against the codebook")
    _validate_driver_answers(universe, "commute universe")
    return universe, report


def classify_workplace(ags, oberbezirk) -> str:
    """Workplace class of one trip destination (rules in the module docstring).

    ``ags`` is the destination AGS as text or integer (the delivery drops the leading zero);
    empty cells and SrV sentinels give ``unknown_destination``. ``oberbezirk`` is read only for
    Braunschweig; a missing district there gives ``bs_unknown_oberbezirk`` instead of a guessed
    class, and a district outside 1 to 6 raises ValueError.
    """
    kreis = kreis_from_ags(pd.Series([ags], dtype=object)).iloc[0]
    return _classify_destination(kreis, oberbezirk)


def _classify_destination(kreis, oberbezirk) -> str:
    if not isinstance(kreis, str):
        return UNKNOWN_DESTINATION
    if kreis == BRAUNSCHWEIG_KREIS_KEY:
        district = _oberbezirk_code(oberbezirk)
        if district is None:
            return BS_UNKNOWN_OBERBEZIRK
        if district in BS_ZENTRUM_OBERBEZIRKE:
            return BS_ZENTRUM
        if district in BS_INNENBEREICH_OBERBEZIRKE:
            return BS_INNENBEREICH
        if district in BS_OUTER_OBERBEZIRKE:
            return BS_OUTER
        raise ValueError(
            f"Braunschweig destination with Oberbezirk {district}: the SrV Teilraumkodierung "
            "defines only the Oberbezirke 1 to 6 for Braunschweig")
    if kreis in ZGB_KREIS_KEYS:
        return kreis
    return OUTSIDE_ZGB


def _oberbezirk_code(value):
    """Integer Oberbezirk, or None for an empty cell or an SrV missing code (-7, -6)."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{DESTINATION_OBERBEZIRK_COLUMN}: non-numeric district code") from error
    if math.isnan(number) or number < 0:
        return None
    if not number.is_integer():
        raise ValueError(f"{DESTINATION_OBERBEZIRK_COLUMN}: non-integer district code")
    return int(number)


def first_commute_destination(trips: pd.DataFrame) -> pd.DataFrame:
    """Workplace class of each person's first work/education trip of the survey day.

    ``trips`` carries ``HHNR``, ``PNR``, ``WNR``, ``V_ZWECK``, ``V_ZIEL_AGS`` and
    ``V_ZIEL_OBERBEZIRK``. Returns a frame indexed by ``(HHNR, PNR)`` with the single column
    ``workplace_class``. Trips are ordered by ``WNR``, never by file order; persons without a
    trip whose ``V_ZWECK`` is in :data:`COMMUTE_PURPOSES` get no row
    (:func:`attach_workplace_class` labels them ``no_commute_trip``).
    """
    columns = [*PERSON_KEYS, TRIP_NUMBER_COLUMN, PURPOSE_COLUMN, DESTINATION_AGS_COLUMN,
               DESTINATION_OBERBEZIRK_COLUMN]
    _require_columns(trips, columns, "trip file")
    duplicated = trips.duplicated([*PERSON_KEYS, TRIP_NUMBER_COLUMN])
    if duplicated.any():
        raise ValueError(f"trip file: {int(duplicated.sum())} duplicate (HHNR, PNR, WNR) key(s)")

    commute = trips.loc[trips[PURPOSE_COLUMN].isin(sorted(COMMUTE_PURPOSES)), columns]
    first = (commute.sort_values([*PERSON_KEYS, TRIP_NUMBER_COLUMN], kind="mergesort")
             .drop_duplicates(PERSON_KEYS, keep="first"))
    kreis = kreis_from_ags(first[DESTINATION_AGS_COLUMN])
    classes = [_classify_destination(kreis_key, district)
               for kreis_key, district in zip(kreis, first[DESTINATION_OBERBEZIRK_COLUMN])]
    destinations = pd.DataFrame({"workplace_class": classes},
                                index=pd.MultiIndex.from_frame(first[PERSON_KEYS]))

    purpose_counts = first[PURPOSE_COLUMN].value_counts()
    logger.info(
        "%s first commute trip found for %d persons (%d trips with V_ZWECK in %s): purpose 1 "
        "(own workplace) %d, purpose 6 (vocational school/university) %d", _LOG_TAG, len(first),
        len(commute), sorted(COMMUTE_PURPOSES), int(purpose_counts.get(PURPOSE_WORK, 0)),
        int(purpose_counts.get(PURPOSE_TERTIARY, 0)))
    _log_distribution("first commute destination class", destinations["workplace_class"])
    return destinations


def attach_workplace_class(universe: pd.DataFrame, destinations: pd.DataFrame) -> pd.DataFrame:
    """Add ``workplace_class`` to the car-commuter universe.

    ``destinations`` is the output of :func:`first_commute_destination`. A car commuter without
    a commute trip on the survey day is labelled ``no_commute_trip`` (counted and logged, never
    assigned a class). Row count and order of ``universe`` are preserved.
    """
    _require_columns(universe, PERSON_KEYS, "commute universe")
    if "workplace_class" in universe.columns:
        raise ValueError("commute universe already carries a workplace_class column")
    lookup = destinations[["workplace_class"]].reset_index()
    _require_columns(lookup, PERSON_KEYS, "commute destinations index")
    attached = universe.merge(lookup, on=PERSON_KEYS, how="left", validate="many_to_one")
    attached["workplace_class"] = attached["workplace_class"].fillna(NO_COMMUTE_TRIP)
    _log_distribution("car commuters by workplace label", attached["workplace_class"])
    return attached


def _validate_driver_answers(universe: pd.DataFrame, source: str) -> None:
    bad_type = ~universe["parking_type"].isin(DRIVER_PARKING_TYPES)
    bad_payment = ~universe["payment"].isin(PAYMENTS)
    bad_weight = _invalid_weight(universe["weight"])
    if bad_type.any() or bad_payment.any() or bad_weight.any():
        raise ValueError(
            f"{source}: {int(bad_type.sum())} row(s) with a parking_type outside "
            f"{DRIVER_PARKING_TYPES}, {int(bad_payment.sum())} with a payment outside {PAYMENTS}, "
            f"{int(bad_weight.sum())} with a non-positive weight; build it with "
            "commute_parking_universe")


def parking_shares(group: pd.DataFrame) -> dict:
    """Weighted place and payment shares of one group of car commuters.

    Returns ``n_unweighted``, ``n_eff`` (Kish) and the ``share_*`` fractions in [0, 1] of
    :data:`COMMUTE_TABLE_COLUMNS`. The four place shares and the two payment shares each sum to 1
    for a universe built by :func:`commute_parking_universe`.
    """
    weight = group["weight"].astype(float)
    total_weight = float(weight.sum())
    if not total_weight > 0:
        raise ValueError("parking shares: the group has no positive total weight")
    shares = {"n_unweighted": int(len(group)),
              "n_eff": total_weight ** 2 / float((weight ** 2).sum())}
    for parking_type in DRIVER_PARKING_TYPES:
        shares[f"share_{parking_type}"] = float(weight[group["parking_type"] == parking_type].sum()) / total_weight
    shares["share_paid_total"] = float(weight[group["payment"] == "paid"].sum()) / total_weight
    shares["share_free_total"] = float(weight[group["payment"] == "free"].sum()) / total_weight
    return shares


def build_commute_parking_table(universe: pd.DataFrame, min_cell_n: int = MIN_CELL_N) -> pd.DataFrame:
    """Weighted parking place and payment shares per workplace class.

    ``universe`` is the car-commuter universe with ``workplace_class``
    (:func:`attach_workplace_class`). Returns :data:`COMMUTE_TABLE_COLUMNS`: one ``level ==
    "class"`` row per workplace class present, in :data:`WORKPLACE_CLASSES` order, then one
    ``level == "total"`` row (``workplace_class == "total"``) pooling exactly the class rows.
    Respondents with a label in :data:`UNCLASSIFIED_LABELS` are counted and logged but belong to
    no row. Shares are unrounded.

    Raises ValueError naming the class when a class has fewer than ``min_cell_n`` unweighted
    respondents (pool it into a documented class rather than lowering the guard), and for an
    unknown class label.
    """
    _require_columns(universe, ["workplace_class", "weight", "parking_type", "payment"],
                     "commute universe")
    _validate_driver_answers(universe, "commute universe")
    labels = universe["workplace_class"]
    unknown = sorted(set(labels) - set(WORKPLACE_CLASSES) - set(UNCLASSIFIED_LABELS), key=str)
    if unknown:
        raise ValueError(
            f"unknown workplace class label(s) {unknown}; expected one of {WORKPLACE_CLASSES} or a "
            f"counted non-class label {UNCLASSIFIED_LABELS}")

    in_class = labels.isin(WORKPLACE_CLASSES)
    n_total = len(universe)
    outside = ", ".join(f"{label} {_rate(int((labels == label).sum()), n_total)}"
                        for label in UNCLASSIFIED_LABELS)
    logger.info("%s class rows cover %s car commuters; outside every row: %s", _LOG_TAG,
                _rate(int(in_class.sum()), n_total), outside)

    rows = []
    absent = []
    for workplace_class in WORKPLACE_CLASSES:
        group = universe[labels == workplace_class]
        if group.empty:
            absent.append(workplace_class)
            continue
        if len(group) < min_cell_n:
            raise ValueError(
                f"workplace class {workplace_class!r} has {len(group)} unweighted car commuters, "
                f"below min_cell_n={min_cell_n}; pool it into a documented class instead of "
                "lowering the guard")
        rows.append({"workplace_class": workplace_class, "level": LEVEL_CLASS, **parking_shares(group)})
    if absent:
        logger.warning("%s no car commuter for workplace class(es) %s: no row is written for them",
                       _LOG_TAG, absent)
    if not rows:
        raise ValueError("commute universe contains no workplace class of the ZGB")
    rows.append({"workplace_class": TOTAL_ROW_CLASS, "level": LEVEL_TOTAL,
                 **parking_shares(universe[in_class])})
    return pd.DataFrame(rows, columns=list(COMMUTE_TABLE_COLUMNS))


# --- City-centre parking ----------------------------------------------------------------------

def city_center_universe(bs: pd.DataFrame, persons: pd.DataFrame):
    """Braunschweig residents who drive to the city centre, with a valid place and weight.

    ``bs`` is the raw Braunschweig add-on module (``V_BRAU_PARKENCITY`` and its follow-ups);
    ``persons`` carries ``HHNR``, ``PNR`` and ``GEWICHT_P_ZENSUS``. Returns ``(universe,
    report)``; the report counts each respondent once, in this order: ``dropped_invalid_weight``,
    ``dropped_missing_type``, ``excluded_no_car_to_city_center`` (code 4, outside the universe
    by definition); plus ``n_input``, ``n_universe`` and ``n_universe_without_payment`` (kept for
    the place shares, left out of ``paid_share_overall`` only).
    """
    source = "add-on module BRAU"
    _require_columns(bs, [*PERSON_KEYS, CITY_CENTER_PLACE_COLUMN], source)
    _reject_duplicate_persons(bs, source)
    parking_type, payment = _resolve_parking_answers(bs, CITY_CENTER_PLACE_COLUMN, PARKENCITY_CODES)
    _log_answer_codes(source, CITY_CENTER_PLACE_COLUMN, bs[CITY_CENTER_PLACE_COLUMN], parking_type,
                      NO_CAR_TO_CITY_CENTER)
    frame = bs[PERSON_KEYS].copy()
    frame["parking_type"] = parking_type
    frame["payment"] = payment
    frame = _attach_person_weight(frame, persons, source)

    invalid_weight = _invalid_weight(frame["weight"])
    remaining = ~invalid_weight
    missing_type = remaining & frame["parking_type"].isna()
    remaining &= ~missing_type
    no_car = remaining & (frame["parking_type"] == NO_CAR_TO_CITY_CENTER)
    remaining &= ~no_car

    universe = frame[remaining].reset_index(drop=True)
    report = {
        "n_input": len(frame),
        "dropped_invalid_weight": int(invalid_weight.sum()),
        "dropped_missing_type": int(missing_type.sum()),
        "excluded_no_car_to_city_center": int(no_car.sum()),
        "n_universe": len(universe),
        "n_universe_without_payment": int(universe["payment"].isna().sum()),
    }
    _log_report("city-centre universe", report)
    if universe.empty:
        raise ValueError("city-centre universe is empty; check the code map against the codebook")
    return universe, report


def city_center_shares(universe: pd.DataFrame) -> pd.DataFrame:
    """Place shares and ``paid_share_overall`` of the city-centre universe.

    Returns :data:`CITY_CENTER_TABLE_COLUMNS`: one row per place in
    :data:`DRIVER_PARKING_TYPES` (``share`` = weighted share of the universe, ``n_unweighted`` =
    respondents naming the place), then ``paid_share_overall`` (``share`` = weighted share paying
    among the respondents with a valid payment answer, ``n_unweighted`` = those respondents).
    """
    bad_type = ~universe["parking_type"].isin(DRIVER_PARKING_TYPES)
    bad_weight = _invalid_weight(universe["weight"])
    if bad_type.any() or bad_weight.any():
        raise ValueError(
            f"city-centre universe: {int(bad_type.sum())} row(s) with a parking_type outside "
            f"{DRIVER_PARKING_TYPES}, {int(bad_weight.sum())} with a non-positive weight; build it "
            "with city_center_universe")
    weight = universe["weight"].astype(float)
    total_weight = float(weight.sum())
    rows = []
    for parking_type in DRIVER_PARKING_TYPES:
        named = universe["parking_type"] == parking_type
        rows.append({"parking_type": parking_type, "share": float(weight[named].sum()) / total_weight,
                     "n_unweighted": int(named.sum())})
    with_payment = universe["payment"].isin(PAYMENTS)
    if not with_payment.any():
        raise ValueError("city-centre universe: no respondent has a valid payment answer")
    paid = universe["payment"] == "paid"
    rows.append({"parking_type": PAID_SHARE_OVERALL,
                 "share": float(weight[paid].sum()) / float(weight[with_payment].sum()),
                 "n_unweighted": int(with_payment.sum())})
    return pd.DataFrame(rows, columns=list(CITY_CENTER_TABLE_COLUMNS))


def build_city_center_table(bs: pd.DataFrame, persons: pd.DataFrame) -> pd.DataFrame:
    """City-centre table in one call: :func:`city_center_universe` then :func:`city_center_shares`.

    The extractor calls the two steps separately because it also writes the universe report
    into the table header.
    """
    universe, _ = city_center_universe(bs, persons)
    return city_center_shares(universe)
