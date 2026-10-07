"""Build the parking tariff model JSON (schema 3) that the Java parking cost model reads (issues #249, #436).

The tariff table (design spec section 5.3: money in euros, fee hours as decimal hours of the weekday) is
converted ONCE, here, into integer euro cents and integer seconds after midnight; the Java side reads only
the result (spec 5.4 contract, pinned by ``tests/test_parking_tariff_export.py``). Every row becomes a
``braunschweig.parking.cost.ZoneTariff``, whose construction validates it against its zone type, so an
inconsistent table fails at export instead of being priced wrongly in the simulation.

Schema 2 (parking cost zones v2, levers 2 and 4) is additive: every zone entry carries the schema-1 keys plus
``garage_hourly_rate_cents``, ``garage_billing_unit_min``, ``garage_first_period_min``, ``garage_first_period_cents``,
``garage_daily_cap_cents``, ``garage_fee_start_s``, ``garage_fee_end_s``, ``commuter_day_cents`` and
``search_time_min``, null where the table leaves the column empty or does not have it. A schema-1 table therefore
exports as schema 2 with these keys null and prices as before.

Schema 3 (spec Amendment E, Task 4d) adds the top-level keys ``garages`` (the priced garages of the garage dataset as
``braunschweig.parking.cost.GarageTariff`` entries sorted by ``garage_id``: position in EPSG:25832 metres, tariff in integer
cents, minutes and seconds, the tiers, bands and first-period window where the garage has them, the monthly product in
cents), ``garage_decay_m`` (the decay length lambda in metres; 0 switches the garage options off, E6) and
``garage_max_distance_m`` (D_max, ASSUMPTION G2). A model built without a garage dataset lists no garages and carries a decay
of 0; schema 1 and 2 files still load and price as before (the Java reader treats the three keys as absent there).

Spec Amendment C3 (resident parking districts) adds two more keys without a new schema version, because they are
additive: every zone entry carries ``resident_permits_valid``, a bool that is never null (an empty table cell resolves to
the default of the zone type, see ``braunschweig.parking.cost.ZoneTariff``; ASSUMPTION R2-a), and the model carries the
top-level list ``resident_districts`` of ``{"district_id", "municipality_ags"}`` entries sorted by district id, the ids
a plan attribute ``parkingDistrict`` or ``residentParkingDistrict`` may take, so that the Java plan check can reject an
unknown one. A model built without a district layer lists none.

The model carries the assumptions register of spec section 7 as ``ASSUMPTION <id>: ...`` texts and the
provenance of its inputs (``sources``: path and LF-normalised sha256 of every input file, supplied by the
caller, which knows the paths). Nothing here reads the tariff table itself; the functions are pure except
``content_sha256`` (reads a file) and the two writers.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from braunschweig.parking import garages as parking_garages
from braunschweig.parking.cost import (GARAGE_MAX_DISTANCE_M, SECONDS_PER_DAY, ZONE_TYPES, GarageBand, GarageTariff,
                                       GarageTier, ZoneTariff, not_applicable_fields)

#: 3 since the garage options of parking cost zones v2 (spec Amendment E, issue #436): the model gained ``garages``,
#: ``garage_decay_m`` and ``garage_max_distance_m``; 2 added the optional schema-2 keys of the zone entries.
SCHEMA_VERSION = 3
CURRENCY = "EUR"
#: Assumption T1. The only rule implemented; the config key ``parking_terminal_stay_rule`` is reserved.
TERMINAL_STAY_RULE_UNTIL_FEE_END = "until_fee_end"
SUPPORTED_TERMINAL_STAY_RULES = (TERMINAL_STAY_RULE_UNTIL_FEE_END,)

SECONDS_PER_HOUR = 3600
CENTS_PER_EURO = 100
#: Garage positions are written in metres rounded to millimetres, so that the model is byte-stable and Java reads the same
#: double as Python (the dataset stores WGS84 with 7 decimals, about 1 cm; the projected value is not finer than that).
COORDINATE_DECIMALS_M = 3
# A euro amount is a whole number of cents when euros * 100 lies this close to an integer; the slack only
# absorbs binary floating-point noise (1.80 * 100 = 180.00000000000003), never a real sub-cent amount.
_WHOLE_CENT_TOLERANCE = 1e-6

# Table column (euros) -> ZoneTariff field (cents).
EURO_COLUMNS = {"hourly_rate_eur": "hourly_rate_cents", "first_period_eur": "first_period_cents",
                "daily_cap_eur": "daily_cap_cents", "long_stay_product_eur": "long_stay_product_cents",
                "member_day_eur": "member_day_cents", "guest_day_eur": "guest_day_cents",
                "garage_hourly_rate_eur": "garage_hourly_rate_cents",
                "garage_first_period_eur": "garage_first_period_cents",
                "garage_daily_cap_eur": "garage_daily_cap_cents", "commuter_day_eur": "commuter_day_cents"}
# Table columns in whole minutes, same name in the table and in ZoneTariff.
MINUTE_COLUMNS = ("billing_unit_min", "free_if_stay_at_most_min", "first_period_min", "max_stay_min",
                  "garage_billing_unit_min", "garage_first_period_min", "search_time_min")
# Table column (decimal hours of the day) -> ZoneTariff field (seconds after midnight). The street fee window is
# required; the garage fee window belongs to the optional garage core (spec amendment A6).
HOUR_COLUMNS = {"fee_start_h": "fee_start_s", "fee_end_h": "fee_end_s"}
GARAGE_HOUR_COLUMNS = {"garage_fee_start_h": "garage_fee_start_s", "garage_fee_end_h": "garage_fee_end_s"}
#: The spec 5.3 columns this module reads. The others are not part of the model: workplace_class serves the
#: zone attachment (spec 3.4); name, municipality_ags, source_url, source_date, valid_from, fee_window_source
#: and notes document provenance in the table itself.
TARIFF_COLUMNS = ("zone_id", "zone_type", *EURO_COLUMNS, *MINUTE_COLUMNS, *HOUR_COLUMNS, *GARAGE_HOUR_COLUMNS,
                  "resident_exempt", "resident_permits_valid")
#: The schema-2 columns (v2 levers 2 and 4) and the permit flag of Amendment C3: a table or row without them converts
#: with their fields None, i.e. no garage, no commuter product and the default of the zone type for the permit flag.
OPTIONAL_COLUMNS = ("garage_hourly_rate_eur", "garage_billing_unit_min", "garage_first_period_min",
                    "garage_first_period_eur", "garage_daily_cap_eur", "garage_fee_start_h", "garage_fee_end_h",
                    "commuter_day_eur", "search_time_min", "resident_permits_valid")
#: The columns every table and row must have (schema 1).
REQUIRED_COLUMNS = tuple(column for column in TARIFF_COLUMNS if column not in OPTIONAL_COLUMNS)
# ZoneTariff money/minute/garage-hour field -> the table column it is read from, so that row errors name the column.
_COLUMN_OF_FIELD = {**{field: column for column, field in EURO_COLUMNS.items()},
                    **{column: column for column in MINUTE_COLUMNS},
                    **{field: column for column, field in GARAGE_HOUR_COLUMNS.items()}}

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_AGS = re.compile(r"\d{8}")
#: The keys of one ``sources`` entry; the Java ``ParkingTariffs`` reader accepts exactly these (``SOURCE_FIELDS``).
SOURCE_KEYS = ("source_id", "path", "sha256")
#: The keys of one ``resident_districts`` entry (spec Amendment C3): the district id of the plan attributes and the AGS of
#: the municipality that owns the district.
RESIDENT_DISTRICT_KEYS = ("district_id", "municipality_ags")


@dataclass(frozen=True)
class Assumption:
    """One row of the assumptions register (design spec section 7)."""

    assumption_id: str
    statement: str
    where_it_bites: str
    sensitivity: str

    def render(self) -> str:
        return (f"ASSUMPTION {self.assumption_id}: {self.statement}. Where it bites: {self.where_it_bites}. "
                f"Sensitivity: {self.sensitivity}.")


#: The assumptions register of the design spec section 7, in its order (A1-b, the Wolfsburg share proxy, and C2, the
#: campus free share, of the v2 Amendment D5 and D6 follow the assumption they refine), followed by the product-minimum
#: assumptions P1 and P2 of the v2 design spec (lever 2; M1 and C1 name how they interact) and the resident district
#: rule R2 of its Amendment C3 with its scope R2-a. The model JSON carries the rendered texts, so every tariff file
#: states the assumptions it is priced under.
_GARAGE_ASSUMPTION_WHERE_IT_BITES = {
    "P3": "Garages whose notes name ASSUMPTION P3 (a night tariff that is no per-unit rate)",
    "P4": "Garages whose published rate states no rounding (billed per started unit)",
    "P5": "Garages whose published tariff states no charging times (fee window 0 to 24 h)",
    "P6": "Tiered garages (tariff_tiers), stays that cross a tier boundary or a night",
    "P7": "Garages that publish several caps",
    "P8": "Banded garages (tariff_duration_bands), stays beyond the first band",
    "P10": "Garages with a free first duration band read as a grace period, stays of the free minutes or a little longer",
    "P11": "Garages priced from secondary evidence (the garages that name ASSUMPTION P11 in their notes)",
}

ASSUMPTIONS_REGISTER = (
    Assumption("Z1", "Outside every zone parking is free", "Municipalities marked not_audited",
               "Register status; A/B by adding zones"),
    Assumption("D1", "The simulated day is an average weekday; Saturday/holiday windows are not modelled",
               "All zones", "none"),
    Assumption("T1", "A terminal stay pays until the fee window of the arrival day ends",
               "Non-home last activities in zones (rare)", "parking_terminal_stay_rule reserved"),
    Assumption("M1", "Maximum stay compares the chargeable duration; a longer stay buys the zone's long-stay "
               "product, and where the zone has none it loses the street product (P1)", "Ia, Peine, resident zones",
               "long_stay_product_eur per zone"),
    Assumption("A1", "The free share observed for current SrV car commuters applies to all workers/students "
               "of the class", "Work/education in paid zones", "parking_workplace_free_share_shift"),
    # Parking cost zones v2, Amendment D5: the Wolfsburg class share is a proxy, set in the configuration.
    Assumption("A1-b", "The free share of the SrV class bs_zentrum (Braunschweig Oberbezirk Zentrum) applies to the "
               "persons of the workplace class 03103 (Wolfsburg) instead of the class share, which was measured on "
               "in-commuters only and is dominated by commuters to the Volkswagen plant, which has its own free parking "
               "and lies outside every paid zone, while the paid zones are the city centre with paid street parking "
               "like the Braunschweig centre; the zones keep their county workplace class",
               "Work/education in the paid zones of workplace class 03103",
               "parking_free_share_proxy_classes (a null entry, {\"03103\": null}, restores the class share)"),
    Assumption("C1", "Members (work/education on campus) pay the day product, or the commuter product where the zone "
               "has a cheaper one (P2); other passes are not modelled", "TU zones",
               "set member_day_eur to 0 in an arm"),
    # Parking cost zones v2, Amendment D6: owner estimate without a source.
    Assumption("C2", "A share of the persons who drive to a campus for work or education find a free parking place "
               "(owner estimate, no source: the SrV 2023 was surveyed before the TU ticketing of the Parkordnung 2026), "
               "drawn once per person like the employer-free draw (outcome EMPLOYER_FREE); the others pay the campus "
               "products (C1); guests pay the guest product", "Work/education in campus zones",
               "parking_campus_free_share (0.0 and 0.4 are the sensitivity arms)"),
    Assumption("R1", "Residence inside a resident zone equals permit possession",
               "Non-home activities of residents in their own zone", "none (small)"),
    Assumption("H1", "Home activities are free everywhere", "All", "none"),
    Assumption("F1", "Fee windows without an ordinance/signage source are marked 'assumption' in the table",
               "Per zone", "documented per row"),
    Assumption("S1", "Each zone has ONE regime; mixed streets are digitised as separate zones or take the "
               "dominant regime with a note", "Per zone", "notes"),
    # Parking cost zones v2 (issue #436), lever 2 of the v2 design spec.
    Assumption("P1", "A stay pays the cheapest product the driver can use (street, garage, commuter): drivers know "
               "the local products", "Zones with garage or commuter columns",
               "leave the garage and commuter columns empty in an arm (the v1 pricing)"),
    Assumption("P2", "commuter_day_eur is the cheapest long-term product per working day (21 working days); regular "
               "work/education commuters hold it, an effective daily cost for regulars, never a day tariff; the monthly "
               "product of a garage (monthly_cents) is divided by the same 21 working days, rounded half up to the cent, "
               "and is offered to work and education only",
               "Work/education stays in zones with commuter_day_eur and at garages with a monthly product",
               "commuter_day_eur presence; monthly product of the garage dataset"),
    # Parking cost zones v2, Amendment E and the garage dataset parking_garages_2026: the assumptions P3 to P11 of the
    # dataset (their texts are the single source ``braunschweig.parking.garages.ASSUMPTIONS``, the data record
    # parking_garages_2026 is their source text), P9 of the pricing code, and the choice model G1, G2 and G3.
    *(Assumption(assumption_id, parking_garages.ASSUMPTIONS[assumption_id], _GARAGE_ASSUMPTION_WHERE_IT_BITES[assumption_id],
                 "garage dataset rows that name it (column assumptions of parking_garages_2026)")
      for assumption_id in ("P3", "P4", "P5", "P6", "P7", "P8")),
    Assumption("P9", "a stay longer than a closed duration schedule (one that ends at 1440 min, e.g. Peine) is priced per "
               "started 24 h: k full periods at the price of 1440 min plus the price of the remainder, every period capped by "
               "the day cap; the pricing code counts and logs how often it applies", "Stays longer than 24 h at garages "
               "with a closed schedule that ends at 1440 min", "none: the rate is reported by the pricing "
               "counters"),
    *(Assumption(assumption_id, parking_garages.ASSUMPTIONS[assumption_id], _GARAGE_ASSUMPTION_WHERE_IT_BITES[assumption_id],
                 "garage dataset rows that name it (column assumptions of parking_garages_2026)")
      for assumption_id in ("P10", "P11")),
    Assumption("G1", "A car stay in a paid zone has the options street (weight 1) and every priced garage within the maximum "
               "distance, weight exp(-d / lambda) with d the straight-line distance in metres (a gravity-type choice by "
               "distance); the price of the stay is the probability-weighted mean of the option costs (an expected cost, no "
               "random draw), the price does not enter the weights and capacity or occupancy are not modelled",
               "Stays in street_paid and resident_zone zones with a garage in range (never campus zones, never a stay that "
               "is free by an early rule or whose street option costs 0)",
               "garage_decay_m (0 switches the garage options off; lambda is calibrated, see G3)"),
    Assumption("G2", "A garage is an option of a stay when its straight-line distance to the destination (EPSG:25832) is at "
               "most 1000 m", "Garages far from a destination carry a small weight (exp(-d / lambda)) but widen the option "
               "set; a garage just inside the limit is an option, one just outside is none",
               "garage_max_distance_m (config parking_garage_max_distance_m)"),
    Assumption("G3", "The decay length lambda is calibrated, not estimated: it is set so that the mean garage probability of "
               "the non-home, non-work, non-education stays at activities inside bs_zone_ia and bs_zone_ib (all modes, "
               "destination universe, the plans of the reference scenario) equals the SrV 2023 share of garages and large "
               "lots among the Braunschweig residents who park on the street or in a garage when they drive to the city "
               "centre (srv2023_city_center_parking, rows garage_large_lot and street); the same lambda applies in every "
               "town (transfer assumption); SrV asks residents about their usual place, the model averages over "
               "destinations; the garage share is therefore a calibration target and no validation; the value of a release "
               "is recorded with its calibration table, and a model with garage_decay_m 0 has no garage options",
               "Every stay with a garage option", "garage_decay_m"),
    # Parking cost zones v2, Amendment C3: the resident parking districts, a layer of their own, and where their permits
    # are valid (ruling R-T1e-a).
    Assumption("R2", "Residence inside a resident parking district equals permit possession (extends R1): a stay "
               "inside the district of the person's home is free where the zone honours resident permits (R2-a); the "
               "districts are independent of the fee zones and may overlap them, and a stay outside every fee zone "
               "stays free by Z1",
               "Non-home activities of residents inside their own district (data record "
               "parking_resident_districts_2026)", "none: the district layer is a release input, not a parameter"),
    Assumption("R2-a", "Resident parking permits are valid at street and resident-zone parking unless the tariff row "
               "says otherwise (resident_permits_valid) and never on a campus; the separately operated car parks of "
               "the city (BgA) and the Goslar car park Klubgartenstrasse/ZOB (gs_parkplatz_klubgartenstrasse_zob) are marked "
               "not valid, because no source states that permits are valid there",
               "Stays of residents inside their own district at the BgA car parks of Braunschweig, at the ZOB car park of "
               "Goslar and on a campus (rows with resident_permits_valid false)",
               "set resident_permits_valid to true on a BgA row in an arm (a campus row rejects true)"),
)


def assumption_texts() -> list[str]:
    """The register as the ``assumptions`` list of the model: ``ASSUMPTION <id>: ...`` strings."""
    return [assumption.render() for assumption in ASSUMPTIONS_REGISTER]


def content_sha256(path) -> str:
    """sha256 hex digest of a committed text file as git stores it (``\\r\\n`` replaced by ``\\n``).

    Same convention as ``braunschweig.data.vrb.fare_model_export.content_sha256``: a Windows checkout with
    core.autocrlf holds CRLF, and hashing the LF-normalised bytes keeps the recorded provenance identical on
    every platform and equal to the committed content.
    """
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _is_empty(value) -> bool:
    """None, pandas NA, NaN or blank text: an empty tariff-table cell ("not applicable")."""
    if value is None or value is pd.NA:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (float, np.floating)) and math.isnan(value)


# The helpers below take ``where`` ("tariff row 'fx_sz'") so every message names the row and the column.
def _number(value, column: str, where: str) -> float | None:
    """A finite number from a table cell (numeric or text), None for an empty cell."""
    if _is_empty(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{where}: {column} must be a number, got the boolean {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: {column} must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{where}: {column} must be finite, got {value!r}")
    return number


def _euros_to_cents(value, column: str, where: str) -> int | None:
    euros = _number(value, column, where)
    if euros is None:
        return None
    cents = int(round(euros * CENTS_PER_EURO))
    if abs(euros * CENTS_PER_EURO - cents) > _WHOLE_CENT_TOLERANCE:
        raise ValueError(f"{where}: {column} = {euros!r} EUR is not a whole number of cents")
    return cents


def _whole_minutes(value, column: str, where: str) -> int | None:
    minutes = _number(value, column, where)
    if minutes is None:
        return None
    if not minutes.is_integer():
        raise ValueError(f"{where}: {column} must be a whole number of minutes, got {value!r}")
    return int(minutes)


def _hours_to_seconds(value, column: str, where: str, *, required: bool = True) -> int | None:
    hours = _number(value, column, where)
    if hours is None:
        if required:
            raise ValueError(f"{where}: {column} is required (decimal hours of the day)")
        return None
    # Decimal hours cannot hold every minute exactly (08:20 = 8.3333...), so the fee boundary is the nearest
    # second; ZoneTariff then checks 0 <= start < end <= 86400.
    return int(round(hours * SECONDS_PER_HOUR))


def _flag(value, column: str, where: str) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValueError(f"{where}: {column} must be true or false, got {value!r}")


def _optional_flag(value, column: str, where: str) -> bool | None:
    """True or False from a table cell, None for an empty cell (the default of the zone type applies then)."""
    if _is_empty(value):
        return None
    return _flag(value, column, where)


def _identifier(value, column: str, where: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{where}: {column} must be a non-empty text without surrounding whitespace, "
                         f"got {value!r}")
    return value


def _check_fee_window_columns(row: Mapping, fields: Mapping, where: str) -> None:
    """Report an invalid street or garage fee window in the table's decimal-hour columns; ``ZoneTariff`` reports
    seconds.

    Checked on the converted seconds, the values the model carries, so a window that rounding to whole seconds
    empties is caught as well. A garage window with one bound empty is left to the family check of ``ZoneTariff``.
    """
    for prefix in ("", "garage_"):
        start_s, end_s = fields[f"{prefix}fee_start_s"], fields[f"{prefix}fee_end_s"]
        if start_s is None or end_s is None or 0 <= start_s < end_s <= SECONDS_PER_DAY:
            continue
        start, end = f"{prefix}fee_start_h", f"{prefix}fee_end_h"
        start_h, end_h = _number(row[start], start, where), _number(row[end], end, where)
        raise ValueError(f"{where}: {start} = {start_h:g} h and {end} = {end_h:g} h do not form a fee window; the "
                         f"decimal hours of the weekday must satisfy 0 <= {start} < {end} <= 24 (compared after "
                         "rounding to whole seconds)")


def _check_empty_cells(fields: Mapping, where: str) -> None:
    """A column the zone type does not have (``cost.not_applicable_fields``) must be an empty cell."""
    zone_type = fields["zone_type"]
    if zone_type not in ZONE_TYPES:
        return  # not a fallback: ZoneTariff rejects the unknown zone type right after this check
    for field in not_applicable_fields(zone_type):
        if fields[field] is not None:
            raise ValueError(f"{where}: {_COLUMN_OF_FIELD[field]} does not apply to zone_type {zone_type!r}; "
                             "leave the cell empty")


def tariff_row_to_zone(row: Mapping) -> ZoneTariff:
    """Convert one tariff-table row (spec 5.3 columns; a dict or a pandas Series) into a ``ZoneTariff``.

    Euros become integer cents with ``int(round(eur * 100))`` (a value that is not a whole number of cents
    raises), minutes must be whole numbers, decimal hours become seconds after midnight with
    ``int(round(h * 3600))``, ``resident_exempt`` accepts booleans or the texts true/false (any case), and
    an empty cell (None, NaN or blank text) becomes None. Text cells are parsed as numbers, so a table read
    with ``dtype=str`` converts the same way. The schema-2 columns (``OPTIONAL_COLUMNS``) may be absent from the
    row, which converts like empty cells (a schema-1 row). Raises ``ValueError`` for a missing schema-1 column or a
    cell it cannot convert exactly; ``ZoneTariff`` then validates the tariff against its zone type. Two of those
    errors are reported in the table's own terms before ``ZoneTariff`` sees the row: an invalid street or garage fee
    window names its ``*fee_start_h`` and ``*fee_end_h`` columns in hours, and a filled cell of a column the zone type
    does not have names that column and says to leave the cell empty. ``resident_permits_valid`` is read like
    ``resident_exempt`` (true/false in any case) but may be empty or absent; ``ZoneTariff`` then resolves the default of
    the zone type (ASSUMPTION R2-a) and rejects true on a campus.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in row]
    if missing:
        raise ValueError(f"tariff row is missing the columns {missing}; spec 5.3 requires {list(REQUIRED_COLUMNS)}")
    zone_id = _identifier(row["zone_id"], "zone_id", "tariff row")
    where = f"tariff row {zone_id!r}"

    def cell(column):
        return row[column] if column in row else None

    fields = {"zone_id": zone_id, "zone_type": _identifier(row["zone_type"], "zone_type", where)}
    for column, field in EURO_COLUMNS.items():
        fields[field] = _euros_to_cents(cell(column), column, where)
    for column in MINUTE_COLUMNS:
        fields[column] = _whole_minutes(cell(column), column, where)
    for column, field in HOUR_COLUMNS.items():
        fields[field] = _hours_to_seconds(row[column], column, where)
    for column, field in GARAGE_HOUR_COLUMNS.items():
        fields[field] = _hours_to_seconds(cell(column), column, where, required=False)
    fields["resident_exempt"] = _flag(row["resident_exempt"], "resident_exempt", where)
    fields["resident_permits_valid"] = _optional_flag(cell("resident_permits_valid"), "resident_permits_valid", where)
    _check_fee_window_columns(row, fields, where)
    _check_empty_cells(fields, where)
    return ZoneTariff(**fields)


def _tier_cents(eur: float) -> int:
    return int(round(eur * CENTS_PER_EURO))


def garage_tiers_from_text(text) -> tuple[GarageTier, ...]:
    """The tiers of a ``tariff_tiers`` text of the garage dataset (``braunschweig.parking.garages.parse_tariff_tiers``, which
    validates the text) as ``GarageTier`` objects: clock times in seconds after midnight (an end of 24:00 is 86,400),
    prices in whole cents (the text carries two decimals, so the conversion is exact)."""
    return tuple(GarageTier(start_s=tier.start_min * 60, end_s=tier.end_min * 60, unit_min=tier.unit_min,
                            price_cents=_tier_cents(tier.eur)) for tier in parking_garages.parse_tariff_tiers(text))


def garage_bands_from_text(text) -> tuple[GarageBand, ...]:
    """The bands of a ``tariff_duration_bands`` text of the garage dataset (``parse_duration_bands``, which validates the
    text) as ``GarageBand`` objects in whole cents; a free band has price 0 and no unit."""
    return tuple(GarageBand(from_min=band.from_min, to_min=band.to_min, kind=band.kind,
                            price_cents=0 if band.kind == "free" else _tier_cents(band.eur), unit_min=band.unit_min)
                 for band in parking_garages.parse_duration_bands(text))


def garage_row_to_tariff(row: Mapping) -> GarageTariff:
    """Convert one row of the garage dataset (``braunschweig.parking.garages.load_garages``, a pandas Series with its
    ``geometry``) of a PRICED garage into a ``GarageTariff``.

    Euros become integer cents (a value that is not a whole number of cents raises, as for the zone rows), decimal hours
    seconds after midnight (``int(round(h * 3600))``), the position the point's EPSG:25832 coordinates in metres rounded to
    millimetres (``COORDINATE_DECIMALS_M``), the tier and band texts their integer forms. An empty cell is None. Raises
    ``ValueError`` for an unpriced garage (listed, never priced: no value is approximated) and for anything
    ``GarageTariff`` rejects.
    """
    garage_id = _identifier(row["garage_id"], "garage_id", "garage row")
    where = f"garage row {garage_id!r}"
    if not bool(row["priced"]):
        raise ValueError(f"{where}: the garage is not priced ({row['not_priced_reason']}); an unpriced garage is no option")
    point = row["geometry"]
    if point is None or point.is_empty or point.geom_type != "Point":
        raise ValueError(f"{where}: the geometry must be a point")
    tiers, bands = row["tariff_tiers"], row["tariff_duration_bands"]
    fields = {
        "garage_id": garage_id,
        "x_m": round(float(point.x), COORDINATE_DECIMALS_M), "y_m": round(float(point.y), COORDINATE_DECIMALS_M),
        "hourly_rate_cents": _euros_to_cents(row["garage_hourly_rate_eur"], "garage_hourly_rate_eur", where),
        "billing_unit_min": _whole_minutes(row["garage_billing_unit_min"], "garage_billing_unit_min", where),
        "fee_start_s": _hours_to_seconds(row["garage_fee_start_h"], "garage_fee_start_h", where, required=False),
        "fee_end_s": _hours_to_seconds(row["garage_fee_end_h"], "garage_fee_end_h", where, required=False),
        "first_period_min": _whole_minutes(row["garage_first_period_min"], "garage_first_period_min", where),
        "first_period_cents": _euros_to_cents(row["garage_first_period_eur"], "garage_first_period_eur", where),
        "first_period_start_s": _hours_to_seconds(row["garage_first_period_start_h"], "garage_first_period_start_h", where,
                                                  required=False),
        "first_period_end_s": _hours_to_seconds(row["garage_first_period_end_h"], "garage_first_period_end_h", where,
                                                required=False),
        "daily_cap_cents": _euros_to_cents(row["garage_daily_cap_eur"], "garage_daily_cap_eur", where),
        "tiers": None if _is_empty(tiers) else garage_tiers_from_text(tiers),
        "bands": None if _is_empty(bands) else garage_bands_from_text(bands),
        "monthly_cents": _euros_to_cents(row["monthly_eur"], "monthly_eur", where),
    }
    return GarageTariff(**fields)


def garage_entries(garages) -> list[dict]:
    """The ``garages`` list of the model: the PRICED garages of the dataset (``braunschweig.parking.garages.load_garages``,
    EPSG:25832) as ``GarageTariff.to_json`` entries sorted by ``garage_id``. None is no garage dataset and gives an empty
    list. Unpriced garages are left out (spec E1: listed, not priced); callers log how many. Raises ``ValueError`` for a
    CRS other than EPSG:25832 (a metric CRS is required for the distances), a missing column or a duplicate id. Pure."""
    if garages is None:
        return []
    if garages.crs is None or garages.crs.to_epsg() != 25832:
        raise ValueError(f"the garage dataset must be in EPSG:25832 (metres), found {garages.crs}")
    missing = [column for column in parking_garages.DATASET_COLUMNS if column not in garages.columns]
    if missing:
        raise ValueError(f"the garage dataset lacks the columns {missing}")
    entries, seen = [], set()
    for _, row in garages.iterrows():
        if not bool(row["priced"]):
            continue
        garage = garage_row_to_tariff(row)
        if garage.garage_id in seen:
            raise ValueError(f"duplicate garage_id {garage.garage_id!r}")
        seen.add(garage.garage_id)
        entries.append(garage)
    return [garage.to_json() for garage in sorted(entries, key=lambda garage: garage.garage_id)]


def garages_from_model(model: Mapping) -> list[GarageTariff]:
    """The model's garage entries as ``GarageTariff`` objects (inverse of ``garage_entries``); a model without the key (a
    schema 1 or 2 file) has none."""
    return [GarageTariff.from_json(entry) for entry in model.get("garages", [])]


def zone_to_json(zone: ZoneTariff) -> dict:
    """The zone entry of the model JSON: every ``ZoneTariff`` field except ``zone_id``, which is its key."""
    fields = asdict(zone)
    del fields["zone_id"]
    return fields


def check_snapshot_date(snapshot_date) -> str:
    """Return ``snapshot_date`` if it is the text of a real calendar date in ISO form ``YYYY-MM-DD``.

    The tariff snapshot date names the tariff model file (``tariff_model_file_name``) and is recorded in the
    model (``tariff_snapshot_date``), so only its exact text form is accepted: no ``datetime.date`` object (an
    unquoted YAML date), no unpadded or other format, no impossible day. Raises ``ValueError`` otherwise. Pure;
    the configure-time check of ``braunschweig.matsim.simulation.prepare`` and the export share it.
    """
    try:
        parsed = datetime.date.fromisoformat(snapshot_date)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None or parsed.isoformat() != snapshot_date:
        raise ValueError(f"snapshot_date must be an ISO date text YYYY-MM-DD, got {snapshot_date!r}")
    return snapshot_date


def _check_sources(sources: Sequence[Mapping]) -> list[dict]:
    if not sources:
        raise ValueError("at least one source is required: the model must name the files it was built from")
    checked, seen = [], set()
    for source in sources:
        for key in SOURCE_KEYS:
            if not isinstance(source.get(key), str) or not source[key]:
                raise ValueError(f"source {dict(source)} needs a non-empty text {key!r}")
        unknown = sorted(set(source) - set(SOURCE_KEYS), key=str)
        if unknown:
            raise ValueError(f"source {source['source_id']!r} has the unknown key(s) {', '.join(map(repr, unknown))}; "
                             f"an entry has exactly the keys {list(SOURCE_KEYS)}, the only ones the Java "
                             "ParkingTariffs reader accepts")
        if not _SHA256_HEX.fullmatch(source["sha256"]):
            raise ValueError(f"source {source['source_id']!r}: sha256 must be 64 lowercase hex characters, "
                             f"got {source['sha256']!r}")
        if "\\" in source["path"]:
            raise ValueError(f"source {source['source_id']!r}: path must use POSIX separators, got {source['path']!r}")
        if source["source_id"] in seen:
            raise ValueError(f"duplicate source_id {source['source_id']!r}")
        seen.add(source["source_id"])
        checked.append(dict(source))
    return checked


def resident_district_entries(districts: pd.DataFrame | None) -> list[dict]:
    """The ``resident_districts`` list of the model: ``{"district_id", "municipality_ags"}`` per district, sorted by id.

    ``districts`` is the district layer of the zones release (a ``GeoDataFrame`` of
    ``braunschweig.parking.zones.load_resident_districts`` or any frame with the columns ``RESIDENT_DISTRICT_KEYS``);
    only those two columns are read. None means no district layer and gives an empty list. Raises ``ValueError`` for a
    missing column, an id that is not a non-empty text, a duplicate id or an AGS that is not an 8-digit text. Pure.
    """
    if districts is None:
        return []
    missing = [column for column in RESIDENT_DISTRICT_KEYS if column not in districts.columns]
    if missing:
        raise ValueError(f"resident districts lack the columns {missing}; the model lists {list(RESIDENT_DISTRICT_KEYS)}")
    entries, seen = [], set()
    for district_id, ags in zip(districts["district_id"], districts["municipality_ags"]):
        _identifier(district_id, "district_id", "resident district")
        where = f"resident district {district_id!r}"
        if not isinstance(ags, str) or not _AGS.fullmatch(ags):
            raise ValueError(f"{where}: municipality_ags must be an 8-digit text, got {ags!r}")
        if district_id in seen:
            raise ValueError(f"duplicate district_id {district_id!r}")
        seen.add(district_id)
        entries.append({"district_id": district_id, "municipality_ags": ags})
    return sorted(entries, key=lambda entry: entry["district_id"])


def check_garage_parameters(garage_decay_m, garage_max_distance_m) -> None:
    """Raise ``ValueError`` unless ``garage_decay_m`` is a finite number of metres >= 0 (0 switches the garage options off)
    and ``garage_max_distance_m`` a finite number of metres > 0; a bool is no number. Shared by the export and the
    configure-time check of ``braunschweig.matsim.simulation.prepare``."""
    for name, value, minimum_exclusive in (("garage_decay_m", garage_decay_m, False),
                                           ("garage_max_distance_m", garage_max_distance_m, True)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (
                value <= 0 if minimum_exclusive else value < 0):
            raise ValueError(f"{name} must be a finite number of metres {'> 0' if minimum_exclusive else '>= 0'}"
                             f"{'' if minimum_exclusive else ' (0 switches the garage options off)'}, got {value!r}")


def build_tariff_model(tariffs: pd.DataFrame, *, snapshot_date: str, sources: Sequence[Mapping],
                       terminal_stay_rule: str = TERMINAL_STAY_RULE_UNTIL_FEE_END,
                       resident_districts: pd.DataFrame | None = None, garages=None, garage_decay_m: float = 0.0,
                       garage_max_distance_m: float = GARAGE_MAX_DISTANCE_M) -> dict:
    """The tariff model of spec 5.4 (schema 3) for a tariff table with the spec 5.3 columns.

    ``snapshot_date`` (ISO date) names the tariff state the table records. ``sources`` lists the input
    files as ``{"source_id", "path" (POSIX, repository-relative), "sha256" (see ``content_sha256``)}``;
    an entry with any other key raises, because the Java ``ParkingTariffs`` reader accepts exactly these
    three (``SOURCE_KEYS``). Only the terminal-stay rule ``until_fee_end`` (T1) exists. The schema-2 columns are
    optional: a schema-1 table exports with every schema-2 key null. ``resident_districts`` is the district layer of
    the release (``resident_district_entries``): the model lists its ids for the plan check of the Java side, an empty
    list when none is given. ``garages`` is the garage dataset of the release (``garage_entries``: the priced garages, sorted
    by id, None = no garage dataset); ``garage_decay_m`` is the decay length lambda in metres (ASSUMPTION G1, calibrated;
    0 = garage options off) and ``garage_max_distance_m`` the maximum distance D_max (G2, > 0). Raises ``ValueError`` for an
    empty table, a missing schema-1 column, a duplicate zone id, an invalid row, invalid sources, invalid districts, an
    invalid garage or an invalid garage parameter. The returned dict is plain JSON data: integer cents and
    seconds, None for "not applicable".
    """
    if terminal_stay_rule not in SUPPORTED_TERMINAL_STAY_RULES:
        raise ValueError(f"terminal_stay_rule {terminal_stay_rule!r} is not implemented; supported: "
                         f"{SUPPORTED_TERMINAL_STAY_RULES}")
    check_snapshot_date(snapshot_date)
    checked_sources = _check_sources(sources)
    district_entries = resident_district_entries(resident_districts)
    check_garage_parameters(garage_decay_m, garage_max_distance_m)
    garage_list = garage_entries(garages)
    if garage_decay_m > 0 and not garage_list:
        raise ValueError(f"garage_decay_m is {garage_decay_m} (the garage options are on) but the release lists no priced "
                         "garage: set garage_decay_m to 0 or give the garage dataset (parking_garages_path)")
    missing = [column for column in REQUIRED_COLUMNS if column not in tariffs.columns]
    if missing:
        raise ValueError(f"tariff table is missing the columns {missing}; spec 5.3 requires {list(REQUIRED_COLUMNS)}")
    if tariffs.empty:
        raise ValueError("tariff table has no rows")
    zones: dict[str, dict] = {}
    for row in tariffs.to_dict(orient="records"):
        zone = tariff_row_to_zone(row)
        if zone.zone_id in zones:
            raise ValueError(f"tariff table has a duplicate zone_id {zone.zone_id!r}")
        zones[zone.zone_id] = zone_to_json(zone)
    return {
        "schema_version": SCHEMA_VERSION,
        "tariff_snapshot_date": snapshot_date,
        "currency": CURRENCY,
        "terminal_stay_rule": terminal_stay_rule,
        # D1: the fee windows are those of an average weekday.
        "weekday_only": True,
        "assumptions": assumption_texts(),
        "sources": checked_sources,
        "resident_districts": district_entries,
        "garages": garage_list,
        "garage_decay_m": float(garage_decay_m),
        "garage_max_distance_m": float(garage_max_distance_m),
        "zones": zones,
    }


def write_json_document(path, document: Mapping) -> Path:
    """Write ``document`` as sorted-key, two-space-indented ASCII JSON with LF line endings.

    Bytes are written directly, so the file is identical on every platform; NaN or infinity raise instead of
    producing invalid JSON. An existing file at ``path`` is replaced; the parent directory must exist.
    """
    path = Path(path)
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
    path.write_bytes(text.encode("ascii"))
    return path


def write_tariff_model(path, model: Mapping) -> Path:
    """Write the tariff model (see ``write_json_document`` for the byte format)."""
    return write_json_document(path, model)


def tariff_model_file_name(prefix: str, snapshot_date: str) -> str:
    """File name of the tariff model next to ``<prefix>config.xml``; it carries the tariff snapshot date."""
    return f"{prefix}parking_tariffs_{check_snapshot_date(snapshot_date)}.json"


def inputs_report_name(prefix: str) -> str:
    """File name of the preparation report that lists the parking input files."""
    return f"{prefix}parking_inputs_report.json"
