"""Deterministic parking cost of one car stay in a zone (issues #249, #436): the Python reference implementation.

``parking_cost_cents`` applies the rules of a zone's tariff in a fixed order (listed in its docstring) to the
part of the stay that falls inside the zone's daily fee window (``chargeable_seconds``) and, since schema 2, lets
the stay pay the cheapest of the zone's products (street, garage in its own fee window, commuter); ``ZoneTariff``
validates a tariff against its zone type when it is constructed. The Java ``ParkingCostCalculator``
(eqasim-java-bs) must reproduce this calculation exactly; the shared contract is the golden cases of
``braunschweig.parking.golden_cases``, exported with the fixture tariffs to
``tests/fixtures/parking/parking_golden_cases.json``. All money is in integer euro cents and all times are
in integer simulation seconds, so both implementations run the same integer arithmetic and cannot drift
apart through floating-point rounding.

Assumptions (spec section 7 and the v2 spec, lever 2; their full texts travel with the tariff JSON, see
``braunschweig.parking.tariff_export.ASSUMPTIONS_REGISTER``):

- D1: the simulated day is an average weekday, so a zone's fee window repeats every 86,400 s.
- H1: home activities are free everywhere.
- R1: living inside a resident zone equals holding its permit (``resident_of_zone``).
- R2 (v2, spec Amendment C3, extends R1): living inside a resident parking district equals holding its permit, so a
  stay in the district of the person's home is free (``resident_of_district``) where the zone honours resident
  permits (R2-a). A district is a layer of its own, independent of the fee zones and free to overlap them; the rule
  acts inside a fee zone only (Z1 is decided first) and whatever the zone's ``resident_exempt``.
- R2-a (v2, Amendment C3, ruling R-T1e-a): resident permits are valid at the parking of a street_paid or
  resident_zone row unless its tariff says otherwise (``resident_permits_valid`` false) and never on a campus. The
  separately operated car parks of the city (BgA) are street_paid rows with ``resident_permits_valid`` false, because
  no source states that permits are valid there. The flag switches off R2 only; R1 never reads it.
- C1: members (work and education purposes) pay the campus day product, or the commuter product where the zone has
  a cheaper one (A4); other passes are not modelled.
- M1: the maximum stay is compared with the CHARGEABLE duration; a longer stay buys the long-stay product, and where
  the zone has none it loses the street product (P1 then prices it with the garage or the commuter product).
- T1: a terminal stay pays until the fee window of the arrival day ends (``terminal_departure_s``).
- Z1: outside every zone parking is free (no tariff -> ``NO_ZONE``).
- P1 (v2, issue #436): a stay pays the cheapest product the driver can use, the street product, the garage product
  or the commuter product (drivers know the local products).
- P2 (v2): ``commuter_day_cents`` is the cheapest long-term product per working day (21 working days); regular
  commuters hold it, so it is offered to the commuter purposes work and education only (``COMMUTER_PURPOSES``).

Schema 2 (v2 levers 2 and 4) adds optional tariff fields: the garage product (``garage_*``: a core of rate, billing
unit and fee window, an optional day cap and first period, spec amendment A6), the commuter
product ``commuter_day_cents`` and the parking search time ``search_time_min``. They default to None, so a tariff
without them (schema 1) is valid and prices exactly as in v1. ``search_time_min`` is no price input: it travels with
the tariff to the Java car utility (lever 4) and ``parking_cost_cents`` does not read it.

L1 (ADR-0139) is a run parameter, not a tariff property, so it is not part of that register: every priced stay lasts
at least L minutes (``minimum_stay_departure_s``, applied to the stay before ``parking_cost_cents``). L comes from the
config key ``parking_minimum_stay_min`` and reaches the Java port as the ``braunschweigParking`` module parameter
``minimumStayMinutes``.

Every function is pure: no file or network access and no global state.
"""
from __future__ import annotations

import functools
import math
import operator
from dataclasses import dataclass
from typing import NamedTuple, NoReturn

from braunschweig.parking import garages as parking_garages

SECONDS_PER_MINUTE = 60
#: D1: every fee window repeats with this period (one average weekday).
SECONDS_PER_DAY = 86400

STREET_PAID = "street_paid"
RESIDENT_ZONE = "resident_zone"
CAMPUS = "campus"
ZONE_TYPES = (STREET_PAID, RESIDENT_ZONE, CAMPUS)

#: H1: the activity type that never pays.
HOME_PURPOSE = "home"
#: C1: the activity types that pay the campus member day product; every other purpose pays the guest product.
CAMPUS_MEMBER_PURPOSES = frozenset({"work", "education"})
#: P2: the activity types offered the commuter product (``commuter_day_cents``), on street_paid and campus rows.
COMMUTER_PURPOSES = frozenset({"work", "education"})

# Outcome names: each constant's value is its name; the Java port and the golden cases use the same strings.
HOME = "HOME"
EMPLOYER_FREE = "EMPLOYER_FREE"
RESIDENT_FREE = "RESIDENT_FREE"
OUTSIDE_FEE_HOURS = "OUTSIDE_FEE_HOURS"
FREE_WITHIN_LIMIT = "FREE_WITHIN_LIMIT"
PAID_METERED = "PAID_METERED"
PAID_LONG_STAY = "PAID_LONG_STAY"
PAID_CAMPUS_MEMBER = "PAID_CAMPUS_MEMBER"
PAID_CAMPUS_GUEST = "PAID_CAMPUS_GUEST"
NO_ZONE = "NO_ZONE"
#: Schema 2 (P1): the garage product or the commuter product was the cheapest; ``PAID_METERED`` and
#: ``PAID_LONG_STAY`` still mean that the street product was.
PAID_GARAGE = "PAID_GARAGE"
PAID_COMMUTER = "PAID_COMMUTER"
#: Schema 3 (spec Amendment E, R-4d-5): the stay was priced as the probability-weighted mean over the street and at
#: least one garage option, or over the garage options alone (``parking_cost_with_garages``).
PAID_EXPECTED = "PAID_EXPECTED"
# The v2 outcomes are appended at the END, so the declaration order of the v1 outcomes (the order of the Java
# ParkingOutcome enum and of the outcome report) is unchanged.
OUTCOMES = (HOME, EMPLOYER_FREE, RESIDENT_FREE, OUTSIDE_FEE_HOURS, FREE_WITHIN_LIMIT, PAID_METERED,
            PAID_LONG_STAY, PAID_CAMPUS_MEMBER, PAID_CAMPUS_GUEST, NO_ZONE, PAID_GARAGE, PAID_COMMUTER, PAID_EXPECTED)

#: Schema 2 (spec amendment A6): the garage core, given completely or not at all. The garage day cap
#: (``garage_daily_cap_cents``, None = no cap) and the first-period pair (``GARAGE_FIRST_PERIOD_FIELDS``) are optional,
#: but only with the core: without it they would never be priced.
GARAGE_CORE_FIELDS = ("garage_hourly_rate_cents", "garage_billing_unit_min", "garage_fee_start_s", "garage_fee_end_s")
GARAGE_FIRST_PERIOD_FIELDS = ("garage_first_period_min", "garage_first_period_cents")
#: Every garage field, in the order of the tariff columns.
GARAGE_FIELDS = ("garage_hourly_rate_cents", "garage_billing_unit_min", "garage_first_period_min",
                 "garage_first_period_cents", "garage_daily_cap_cents", "garage_fee_start_s", "garage_fee_end_s")
#: The optional fields schema 2 adds; ``ZoneTariff`` defaults them to None (a schema-1 tariff).
SCHEMA_2_FIELDS = (*GARAGE_FIELDS, "commuter_day_cents", "search_time_min")

#: R2-a: whether resident parking permits are valid at the parking of a zone type when the tariff does not say. A
#: street or resident zone honours them; a campus car park is the university's and honours none.
_DEFAULT_RESIDENT_PERMITS_VALID = {STREET_PAID: True, RESIDENT_ZONE: True, CAMPUS: False}

# Tariff fields that may be None ("not applicable", an empty tariff-table cell).
_OPTIONAL_FIELDS = ("hourly_rate_cents", "billing_unit_min", "free_if_stay_at_most_min", "first_period_min",
                    "first_period_cents", "daily_cap_cents", "max_stay_min", "long_stay_product_cents",
                    "member_day_cents", "guest_day_cents", *SCHEMA_2_FIELDS)
# Spec 3.2 tests these fields for truthiness ("if t.daily_cap_cents: ..."), so a 0 would silently mean
# "not set" (a zero cap would be ignored, not applied). Requiring them to be positive when set makes
# "is set" and "is truthy" the same and keeps the Python and Java readings identical. The garage product reuses the
# primitives, so its billing unit (a divisor), first period and cap are positive for the same reasons.
_POSITIVE_FIELDS = frozenset({"billing_unit_min", "free_if_stay_at_most_min", "first_period_min",
                              "daily_cap_cents", "max_stay_min", "garage_billing_unit_min", "garage_first_period_min",
                              "garage_daily_cap_cents"})
_PAIRED_FIELDS = (("first_period_min", "first_period_cents"), GARAGE_FIRST_PERIOD_FIELDS)
# Spec 3.1 required fields per zone type. billing_unit_min is also required for resident zones although
# spec 3.1 does not list it: the metered step of spec 3.2 divides by it for every non-campus zone. The long-stay
# product of a resident zone is required by the maximum-stay rule of ``_check_tariff`` unless the zone has a garage
# (v2: "long_stay_product_eur becomes optional when a garage family exists").
_REQUIRED_FIELDS = {
    STREET_PAID: ("hourly_rate_cents", "billing_unit_min"),
    RESIDENT_ZONE: ("hourly_rate_cents", "billing_unit_min", "max_stay_min"),
    CAMPUS: ("member_day_cents", "guest_day_cents"),
}
# Fields the zone type does not have (spec 3.1). A value there is rejected, because it would either be ignored
# or change the regime without anybody noticing. Most of them the algorithm never reads for the type: the
# campus day products are returned before any metering step, the day products are read only on campus, and a
# cap cannot bind on a resident zone, whose metered price is 0 ct. On a resident zone, however,
# free_if_stay_at_most_min and the first period WOULD change the price: FREE_WITHIN_LIMIT is evaluated before
# the maximum-stay check, and the first period is charged in the metered step. Schema 2: a campus stay pays a day
# product, so a garage there would be ignored; the commuter product exists on street_paid and campus rows
# only (spec amendment A4), never in a resident zone.
_NOT_APPLICABLE_FIELDS = {
    STREET_PAID: ("member_day_cents", "guest_day_cents"),
    RESIDENT_ZONE: ("free_if_stay_at_most_min", "first_period_min", "first_period_cents", "daily_cap_cents",
                    "member_day_cents", "guest_day_cents", "commuter_day_cents"),
    CAMPUS: ("hourly_rate_cents", "billing_unit_min", "free_if_stay_at_most_min", "first_period_min",
             "first_period_cents", "daily_cap_cents", "max_stay_min", "long_stay_product_cents", *GARAGE_FIELDS),
}


@dataclass(frozen=True, kw_only=True)
class ZoneTariff:
    """The tariff of one parking zone (spec 3.1, 5.3 and 5.4) in integer cents, minutes and seconds.

    None means "not applicable" (an empty cell of the tariff table). Money is in euro cents
    (``*_cents``; ``hourly_rate_cents`` per hour), durations in minutes (``*_min``) and the daily fee window
    ``[fee_start_s, fee_end_s)`` in seconds after midnight of the (average weekday, D1) day. Construction
    validates the tariff against its zone type and raises ``ValueError`` on any inconsistency, so an
    invalid tariff table fails when it is read, not when a stay is priced.

    Schema 2 (issue #436) adds the fields below ``resident_exempt``, all None by default (a schema-1 tariff): the
    garage product (street_paid and resident_zone only) with its core of rate per hour, billing unit and own fee
    window ``[garage_fee_start_s, garage_fee_end_s)``, all set or all None, and, only together with the core, an
    optional first period and an optional day cap (None = no cap, spec amendment A6); the commuter product per
    working day ``commuter_day_cents`` (street_paid and campus only) and the parking search time ``search_time_min``
    (minutes >= 0, every zone type; not a price input). A maximum stay needs a long-stay product or a garage, because
    a longer stay loses the street product.

    ``resident_permits_valid`` (spec Amendment C3, ASSUMPTION R2-a) says whether the parking honours resident parking
    permits, i.e. whether the district rule R2 frees a stay there. Constructed without it (None) it resolves to True for
    a street_paid or resident_zone row and to False for a campus, so it is always a bool afterwards and the table, the
    model JSON and the Java port carry the same resolved value; True on a campus is rejected. It gates R2 only.
    """

    zone_id: str
    zone_type: str
    hourly_rate_cents: int | None
    billing_unit_min: int | None
    free_if_stay_at_most_min: int | None
    first_period_min: int | None
    first_period_cents: int | None
    daily_cap_cents: int | None
    max_stay_min: int | None
    long_stay_product_cents: int | None
    member_day_cents: int | None
    guest_day_cents: int | None
    fee_start_s: int
    fee_end_s: int
    resident_exempt: bool
    garage_hourly_rate_cents: int | None = None
    garage_billing_unit_min: int | None = None
    garage_first_period_min: int | None = None
    garage_first_period_cents: int | None = None
    garage_daily_cap_cents: int | None = None
    garage_fee_start_s: int | None = None
    garage_fee_end_s: int | None = None
    commuter_day_cents: int | None = None
    search_time_min: int | None = None
    resident_permits_valid: bool | None = None

    def __post_init__(self) -> None:
        if self.resident_permits_valid is None and self.zone_type in ZONE_TYPES:
            # Resolved at construction, not at the use, so that every reader of the tariff sees one bool. The class is
            # frozen, hence object.__setattr__; an unknown zone type is left None and rejected by the check below.
            object.__setattr__(self, "resident_permits_valid", _DEFAULT_RESIDENT_PERMITS_VALID[self.zone_type])
        _check_tariff(self)

    @property
    def has_garage(self) -> bool:
        """Whether the zone has a garage product (its core is validated complete at construction)."""
        return self.garage_hourly_rate_cents is not None


def not_applicable_fields(zone_type: str) -> tuple[str, ...]:
    """The ``ZoneTariff`` fields a tariff of ``zone_type`` must leave empty (None), in a fixed order.

    The rule ``ZoneTariff`` enforces, exposed so that ``braunschweig.parking.tariff_export`` can name the
    offending column of the tariff table. Raises ``ValueError`` for an unknown zone type. Pure.
    """
    if zone_type not in ZONE_TYPES:
        raise ValueError(f"unknown zone_type {zone_type!r}; expected one of {ZONE_TYPES}")
    return _NOT_APPLICABLE_FIELDS[zone_type]


def _is_plain_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_tariff(tariff: ZoneTariff) -> None:
    if not isinstance(tariff.zone_id, str) or not tariff.zone_id.strip():
        raise ValueError(f"parking zone id must be a non-empty string, got {tariff.zone_id!r}")

    def fail(problem: str) -> NoReturn:
        raise ValueError(f"parking zone {tariff.zone_id!r}: {problem}")

    if tariff.zone_type not in ZONE_TYPES:
        fail(f"unknown zone_type {tariff.zone_type!r}; expected one of {ZONE_TYPES}")
    for name in _OPTIONAL_FIELDS:
        value = getattr(tariff, name)
        if value is None:
            continue
        if not _is_plain_int(value):
            fail(f"{name} must be an integer or None, got {value!r}")
        minimum = 1 if name in _POSITIVE_FIELDS else 0
        if value < minimum:
            fail(f"{name} must be at least {minimum}, got {value}")
    for name in ("fee_start_s", "fee_end_s"):
        if not _is_plain_int(getattr(tariff, name)):
            fail(f"{name} must be an integer number of seconds, got {getattr(tariff, name)!r}")
    if not 0 <= tariff.fee_start_s < tariff.fee_end_s <= SECONDS_PER_DAY:
        fail(f"fee window [{tariff.fee_start_s}, {tariff.fee_end_s}) s must satisfy "
             f"0 <= fee_start_s < fee_end_s <= {SECONDS_PER_DAY}")
    if not isinstance(tariff.resident_exempt, bool):
        fail(f"resident_exempt must be a bool, got {tariff.resident_exempt!r}")
    if not isinstance(tariff.resident_permits_valid, bool):
        fail(f"resident_permits_valid must be a bool or None, got {tariff.resident_permits_valid!r}")
    if tariff.zone_type == CAMPUS and tariff.resident_permits_valid:
        fail(f"resident_permits_valid must be false for zone_type {tariff.zone_type!r}: a campus car park does not "
             "honour resident parking permits (no source says it does); leave it empty or false")
    for first, second in _PAIRED_FIELDS:
        if (getattr(tariff, first) is None) != (getattr(tariff, second) is None):
            fail(f"{first} and {second} must be given together or both be empty")
    core_missing = [name for name in GARAGE_CORE_FIELDS if getattr(tariff, name) is None]
    if 0 < len(core_missing) < len(GARAGE_CORE_FIELDS):
        fail(f"the garage core {list(GARAGE_CORE_FIELDS)} must be given completely or not at all; "
             f"missing {core_missing}")
    garage_options = [name for name in ("garage_daily_cap_cents", *GARAGE_FIRST_PERIOD_FIELDS)
                      if getattr(tariff, name) is not None]
    if core_missing and garage_options:
        fail(f"{', '.join(garage_options)} need(s) the garage core {list(GARAGE_CORE_FIELDS)}; a garage cap or first "
             "period without a garage is never priced")
    if tariff.has_garage and not 0 <= tariff.garage_fee_start_s < tariff.garage_fee_end_s <= SECONDS_PER_DAY:
        fail(f"garage fee window [{tariff.garage_fee_start_s}, {tariff.garage_fee_end_s}) s must satisfy "
             f"0 <= garage_fee_start_s < garage_fee_end_s <= {SECONDS_PER_DAY}")
    # A long-stay product is sold only above a maximum stay. A stay above the maximum stay loses the street product
    # (spec 3.2 rule 7, v2 lever 2), so it needs the long-stay product or a garage; the commuter product alone does
    # not suffice, because it serves work and education only.
    if tariff.long_stay_product_cents is not None and tariff.max_stay_min is None:
        fail("long_stay_product_cents needs max_stay_min: a long-stay product without a maximum stay is never sold")
    if tariff.max_stay_min is not None and tariff.long_stay_product_cents is None and not tariff.has_garage:
        fail("max_stay_min is set but neither long_stay_product_cents nor the garage core is: a stay above the "
             "maximum stay would have no product")
    for name in _REQUIRED_FIELDS[tariff.zone_type]:
        if getattr(tariff, name) is None:
            fail(f"{name} is required for zone_type {tariff.zone_type!r}")
    for name in _NOT_APPLICABLE_FIELDS[tariff.zone_type]:
        if getattr(tariff, name) is not None:
            fail(f"{name} does not apply to zone_type {tariff.zone_type!r} and must be empty")
    # The first period is charged in full for any use, so a lower cap would replace its price on every metered
    # stay: the tariff contradicts itself. The same holds for an optional garage cap and first period.
    for prefix in ("", "garage_"):
        cap, first_period = getattr(tariff, f"{prefix}daily_cap_cents"), getattr(tariff, f"{prefix}first_period_cents")
        if cap is not None and first_period is not None and cap < first_period:
            fail(f"{prefix}daily_cap_cents {cap} is below {prefix}first_period_cents {first_period}: a daily cap "
                 "below the first-period price contradicts the tariff (every metered stay would pay exactly the cap)")
    if tariff.zone_type == RESIDENT_ZONE:
        if tariff.hourly_rate_cents != 0:
            fail(f"hourly_rate_cents must be 0 for a resident_zone (disc parking), got {tariff.hourly_rate_cents}")
        if not tariff.resident_exempt:
            fail("resident_exempt must be true for a resident_zone")


def _time_s(name: str, value) -> int:
    """A non-negative integer number of simulation seconds; numpy integers are accepted, floats are not."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer number of seconds, got {value!r}")
    try:
        seconds = operator.index(value)
    except TypeError:
        raise TypeError(f"{name} must be an integer number of seconds, got {value!r}") from None
    if seconds < 0:
        raise ValueError(f"{name} must be non-negative simulation seconds, got {seconds}")
    return seconds


def _check_fee_window(fee_start_s: int, fee_end_s: int) -> tuple[int, int]:
    fee_start_s = _time_s("fee_start_s", fee_start_s)
    fee_end_s = _time_s("fee_end_s", fee_end_s)
    if not fee_start_s < fee_end_s <= SECONDS_PER_DAY:
        raise ValueError(f"fee window [{fee_start_s}, {fee_end_s}) s must satisfy "
                         f"0 <= fee_start_s < fee_end_s <= {SECONDS_PER_DAY}")
    return fee_start_s, fee_end_s


def _ceil_div(numerator: int, denominator: int) -> int:
    """Integer ceiling of ``numerator / denominator`` for a positive denominator, without floats."""
    return -(-numerator // denominator)


def chargeable_seconds(arrival_s: int, departure_s: int, fee_start_s: int, fee_end_s: int) -> int:
    """Seconds of the stay ``[arrival_s, departure_s)`` that fall inside the daily fee window (spec 3.2).

    The window ``[fee_start_s, fee_end_s)`` (seconds after midnight) repeats every 86,400 s (D1), so a stay
    across midnight or over several days collects its overlap with the window of every day it touches:
    ``sum over days k of max(0, min(d, k*86400 + fee_end_s) - max(a, k*86400 + fee_start_s))`` for ``k``
    from ``floor(a / 86400)`` to ``floor((d - 1) / 86400)``. A zero-length stay has no chargeable time.
    Raises ``ValueError`` when the departure lies before the arrival, a time is negative or the window is
    not ``0 <= fee_start_s < fee_end_s <= 86400``.
    """
    arrival_s = _time_s("arrival_s", arrival_s)
    departure_s = _time_s("departure_s", departure_s)
    if departure_s < arrival_s:
        raise ValueError(f"departure_s {departure_s} is before arrival_s {arrival_s}")
    fee_start_s, fee_end_s = _check_fee_window(fee_start_s, fee_end_s)
    total_s = 0
    for day in range(arrival_s // SECONDS_PER_DAY, (departure_s - 1) // SECONDS_PER_DAY + 1):
        day_start_s = day * SECONDS_PER_DAY
        overlap_s = min(departure_s, day_start_s + fee_end_s) - max(arrival_s, day_start_s + fee_start_s)
        total_s += max(0, overlap_s)
    return total_s


def terminal_departure_s(arrival_s: int, fee_end_s: int) -> int:
    """Departure assumed for a car whose destination is the last plan element (assumption T1, spec 3.2).

    The car pays until the fee window of the ARRIVAL day ends:
    ``max(arrival_s, floor(arrival_s / 86400) * 86400 + fee_end_s)``. An arrival after the end of that
    window gives a zero-length stay, which has no chargeable time. Seconds in, seconds out.
    """
    arrival_s = _time_s("arrival_s", arrival_s)
    fee_end_s = _time_s("fee_end_s", fee_end_s)
    if not 0 < fee_end_s <= SECONDS_PER_DAY:
        raise ValueError(f"fee_end_s must lie in (0, {SECONDS_PER_DAY}] seconds after midnight, got {fee_end_s}")
    return max(arrival_s, (arrival_s // SECONDS_PER_DAY) * SECONDS_PER_DAY + fee_end_s)


def minimum_stay_departure_s(arrival_s: int, departure_s: int, minimum_stay_s: int) -> int:
    """Departure up to which a car stay is priced under the minimum parked duration (assumption L1, ADR-0139).

    Returns ``max(departure_s, arrival_s + minimum_stay_s)``: a stay shorter than ``minimum_stay_s`` seconds, including
    the zero-length stay of a car that arrives at or after the planned end of its activity, is priced as if it lasted
    ``minimum_stay_s``; a longer stay is priced as it is. Apply it before ``parking_cost_cents``, so every tariff rule
    prices the extended stay unchanged, and for a terminal stay to the departure of the terminal-stay rule
    (``terminal_departure_s``, T1). ``minimum_stay_s = 0`` returns ``departure_s``, the pricing before L1. The priced
    interval changes, never the simulated timing. Seconds in, seconds out.

    Raises ``ValueError`` for a negative time or minimum and for a departure before the arrival (the extension must
    never turn an invalid stay into a valid one), ``TypeError`` for a non-integer value. Pure function.
    """
    arrival_s = _time_s("arrival_s", arrival_s)
    departure_s = _time_s("departure_s", departure_s)
    minimum_stay_s = _time_s("minimum_stay_s", minimum_stay_s)
    if departure_s < arrival_s:
        raise ValueError(f"departure_s {departure_s} is before arrival_s {arrival_s}")
    return max(departure_s, arrival_s + minimum_stay_s)


def _metered_cents(chargeable_s: int, *, hourly_rate_cents: int, billing_unit_min: int, first_period_min: int | None,
                   first_period_cents: int | None, daily_cap_cents: int | None) -> int:
    """The metering primitive of spec 3.2 rule 8, shared by the street and the garage product, in integer cents.

    The first period is charged once in full when any part of it is used, the rest in started billing units at the
    hourly rate rounded half up to the cent, then the daily cap applies once per stay. Without a chargeable second
    nothing is used, not even the first period, so the price is 0 ct: the street product never meters such a stay
    (rule 4 returns before), but the garage meters its own fee window and can miss a stay the street charges.
    """
    if chargeable_s == 0:
        return 0
    remaining_s = chargeable_s
    cents = 0
    if first_period_min is not None:
        cents += first_period_cents
        remaining_s -= min(remaining_s, first_period_min * SECONDS_PER_MINUTE)
    units = _ceil_div(remaining_s, billing_unit_min * SECONDS_PER_MINUTE)
    # Verbatim spec 3.2: units * minutes per unit * cents per hour is 60 times the price in cents; adding 30
    # before the integer division by 60 rounds half up to the cent.
    cents += (units * billing_unit_min * hourly_rate_cents + 30) // 60
    if daily_cap_cents is not None:
        cents = min(cents, daily_cap_cents)
    return cents


def _street_product(tariff: ZoneTariff, chargeable_s: int) -> tuple[int, str] | None:
    """Spec 3.2 rules 6 to 8 on the street columns (v1), or None where the stay may not use the street.

    ``chargeable_s`` (> 0) are the seconds in the STREET fee window. A stay above the maximum stay buys the
    long-stay product (M1); where the zone has none the street product is unavailable (v2 lever 2) and
    ``ZoneTariff`` guarantees a garage instead.
    """
    # "is not None" equals the spec's truthiness test here: ZoneTariff rejects 0 for these fields.
    chargeable_min = _ceil_div(chargeable_s, SECONDS_PER_MINUTE)
    if tariff.free_if_stay_at_most_min is not None and chargeable_min <= tariff.free_if_stay_at_most_min:
        return 0, FREE_WITHIN_LIMIT
    if tariff.max_stay_min is not None and chargeable_min > tariff.max_stay_min:
        if tariff.long_stay_product_cents is None:
            return None
        return tariff.long_stay_product_cents, PAID_LONG_STAY
    cents = _metered_cents(chargeable_s, hourly_rate_cents=tariff.hourly_rate_cents,
                           billing_unit_min=tariff.billing_unit_min, first_period_min=tariff.first_period_min,
                           first_period_cents=tariff.first_period_cents, daily_cap_cents=tariff.daily_cap_cents)
    return cents, (FREE_WITHIN_LIMIT if cents == 0 else PAID_METERED)


def _garage_product(tariff: ZoneTariff, arrival_s: int, departure_s: int) -> tuple[int, str] | None:
    """The garage product (v2 lever 2), or None without a garage core.

    The metering primitive on the garage columns over the stay's seconds in the GARAGE fee window; no free threshold,
    no maximum stay, and no day cap where the zone gives none (spec amendment A6). As for the street, 0 ct is
    ``FREE_WITHIN_LIMIT``, anything else ``PAID_GARAGE``.
    """
    if not tariff.has_garage:
        return None
    garage_s = chargeable_seconds(arrival_s, departure_s, tariff.garage_fee_start_s, tariff.garage_fee_end_s)
    cents = _metered_cents(garage_s, hourly_rate_cents=tariff.garage_hourly_rate_cents,
                           billing_unit_min=tariff.garage_billing_unit_min,
                           first_period_min=tariff.garage_first_period_min,
                           first_period_cents=tariff.garage_first_period_cents,
                           daily_cap_cents=tariff.garage_daily_cap_cents)
    return cents, (FREE_WITHIN_LIMIT if cents == 0 else PAID_GARAGE)


def _commuter_product(tariff: ZoneTariff, purpose: str) -> tuple[int, str] | None:
    """The commuter product per working day (P2) for the commuter purposes, or None."""
    if tariff.commuter_day_cents is None or purpose not in COMMUTER_PURPOSES:
        return None
    return tariff.commuter_day_cents, PAID_COMMUTER


def _cheapest(products) -> tuple[int, str]:
    """The cheapest of the available products (P1); ``min`` keeps the first of equal prices, so a tie goes to the
    product listed first (street, then garage, then commuter; on campus the member day product)."""
    return min((product for product in products if product is not None), key=lambda product: product[0])


def parking_cost_cents(tariff: ZoneTariff | None, arrival_s: int, departure_s: int, *, purpose: str,
                       parking_free: bool, resident_of_zone: bool, resident_of_district: bool = False) -> tuple[int, str]:
    """Parking cost of one car stay in integer euro cents, with the outcome that decided it (spec 3.2, v2 lever 2).

    ``tariff`` is the tariff of the zone the activity lies in, or None when it lies in no zone (Z1: free,
    outcome ``NO_ZONE``, decided before every check below, so also for the home purpose). ``arrival_s`` is
    the car arrival and ``departure_s`` the activity departure in simulation seconds (for a terminal
    activity use ``terminal_departure_s``; under the minimum stay L1 pass the departure of
    ``minimum_stay_departure_s``, so every product below prices the same extended stay, spec amendment A3);
    ``purpose`` is the MATSim activity type, ``parking_free`` the activity attribute ``parkingFree``,
    ``resident_of_zone`` whether the person lives in this zone (R1), ``resident_of_district`` whether the activity lies
    in the resident parking district of the person's home (R2, spec Amendment C3; the caller compares the district ids
    of the activity and of the home, both set; ``False`` is the default, so every call that predates R2 is unchanged).
    The checks run in the order of spec 3.2; the first that applies decides:

    1. home purpose -> 0, ``HOME`` (H1);
    2. ``parking_free`` -> 0, ``EMPLOYER_FREE``;
    3. a resident-exempt zone and a resident of it (R1), or a stay in the person's own district (R2) in a zone that
       honours resident permits (``resident_permits_valid``, R2-a) -> 0, ``RESIDENT_FREE``: one outcome for both, R2
       whether or not the zone exempts residents, and R1 whether or not the zone honours district permits;
    4. no chargeable second in the STREET fee window -> 0, ``OUTSIDE_FEE_HOURS``: a free street beats every garage;
    5. campus -> for work and education the cheaper of the member day product (C1) and the commuter product (A4,
       when set; a tie keeps the member day product), else the guest day product;
    6. street_paid and resident_zone: the cheapest product the driver can use (P1), a tie going to the first of
       a. the street product, spec 3.2 rules 6 to 8: chargeable minutes (seconds rounded up) within the free
          threshold -> 0, ``FREE_WITHIN_LIMIT``; above the maximum stay the long-stay product, ``PAID_LONG_STAY``
          (M1), or no street product where the zone has none; else metered (first period charged once in full when
          any part of it is used, the rest in started billing units at the hourly rate rounded half up to the cent,
          then the daily cap once per stay); 0 ct is ``FREE_WITHIN_LIMIT``, anything else ``PAID_METERED``;
       b. the garage product, the same metering on the garage columns over the seconds in the garage's own fee
          window, without free threshold and maximum stay; 0 ct is ``FREE_WITHIN_LIMIT``, else ``PAID_GARAGE``;
       c. the commuter product ``commuter_day_cents`` for work and education (P2), ``PAID_COMMUTER``.

    A schema-1 tariff (no garage, no commuter product) has only the street product, so it prices exactly as in v1.
    Raises ``ValueError`` for a departure before the arrival or negative times and ``TypeError`` for
    non-integer times or non-bool flags (a text "false" must never count as true). Pure function.
    """
    arrival_s = _time_s("arrival_s", arrival_s)
    departure_s = _time_s("departure_s", departure_s)
    if departure_s < arrival_s:
        raise ValueError(f"departure_s {departure_s} is before arrival_s {arrival_s}")
    if not isinstance(purpose, str) or not purpose:
        raise TypeError(f"purpose must be a non-empty activity type string, got {purpose!r}")
    for name, flag in (("parking_free", parking_free), ("resident_of_zone", resident_of_zone),
                       ("resident_of_district", resident_of_district)):
        if not isinstance(flag, bool):
            raise TypeError(f"{name} must be a bool, got {flag!r}")
    if tariff is None:
        return 0, NO_ZONE
    if not isinstance(tariff, ZoneTariff):
        raise TypeError(f"tariff must be a ZoneTariff or None, got {type(tariff).__name__}")

    chargeable_s = chargeable_seconds(arrival_s, departure_s, tariff.fee_start_s, tariff.fee_end_s)
    if purpose == HOME_PURPOSE:
        return 0, HOME
    if parking_free:
        return 0, EMPLOYER_FREE
    if (tariff.resident_exempt and resident_of_zone) or (resident_of_district and tariff.resident_permits_valid):
        return 0, RESIDENT_FREE
    if chargeable_s == 0:
        return 0, OUTSIDE_FEE_HOURS
    if tariff.zone_type == CAMPUS:
        if purpose in CAMPUS_MEMBER_PURPOSES:
            return _cheapest(((tariff.member_day_cents, PAID_CAMPUS_MEMBER), _commuter_product(tariff, purpose)))
        return tariff.guest_day_cents, PAID_CAMPUS_GUEST
    # Never empty: where the street product is unavailable, ZoneTariff guarantees the garage. A resident zone has no
    # commuter product (ZoneTariff rejects it), so the commuter product competes on street_paid rows only.
    return _cheapest((_street_product(tariff, chargeable_s), _garage_product(tariff, arrival_s, departure_s),
                      _commuter_product(tariff, purpose)))


# --------------------------------------------------------------------------------------------------- garage options
# Spec Amendment E (issue #436): garages are options of a stay next to the street, chosen by distance and priced as an
# expected cost. The code below is the Python reference of that pricing; the Java port (Task 4e) reproduces it and is
# pinned to it by the golden cases (``braunschweig.parking.golden_cases``, families E and O).

#: Amendment E2, ASSUMPTION G2 (default; the model JSON carries the value as ``garage_max_distance_m``): a garage is
#: an option of a stay when its straight-line distance to the destination (metres, EPSG:25832) is at most this.
GARAGE_MAX_DISTANCE_M = 1000.0
#: ASSUMPTION P2 (monthly product per working day): a monthly garage product is divided by this many working days.
WORKING_DAYS_PER_MONTH = 21
MINUTES_PER_DAY = 24 * 60

GARAGE_TIER_FIELDS = ("start_s", "end_s", "unit_min", "price_cents")
GARAGE_BAND_FIELDS = ("from_min", "to_min", "kind", "price_cents", "unit_min")
GARAGE_BAND_KINDS = ("free", "total", "increment")
#: Every key of one entry of the model's ``garages`` list, in the order of the documentation; the Java reader accepts
#: exactly these. The single-window form fills ``hourly_rate_cents`` .. ``fee_end_s``, the tiered form ``tiers`` and the
#: banded form ``bands`` together with the fee window; the keys of the other forms are null.
GARAGE_FIELDS_JSON = ("garage_id", "x_m", "y_m", "hourly_rate_cents", "billing_unit_min", "fee_start_s", "fee_end_s",
                      "first_period_min", "first_period_cents", "first_period_start_s", "first_period_end_s",
                      "daily_cap_cents", "tiers", "bands", "monthly_cents")


@dataclass(frozen=True, kw_only=True)
class GarageTier:
    """One time-of-day tier of a tiered garage (spec E10): a started unit of ``unit_min`` minutes that begins at a time of
    day in ``[start_s, end_s)`` (seconds after midnight; ``end_s`` <= ``start_s`` crosses midnight, 86,400 is midnight
    itself) costs ``price_cents``."""

    start_s: int
    end_s: int
    unit_min: int
    price_cents: int

    def covers(self, time_of_day_s: int) -> bool:
        """Whether a unit that begins at ``time_of_day_s`` (seconds after midnight) lies in the tier."""
        if self.end_s > self.start_s:
            return self.start_s <= time_of_day_s < self.end_s
        return time_of_day_s >= self.start_s or time_of_day_s < self.end_s


@dataclass(frozen=True, kw_only=True)
class GarageBand:
    """One band of a banded garage (spec E11) over the elapsed duration d in minutes: ``from_min < d <= to_min`` (``to_min``
    None = open-ended last band) of ``kind`` ``free``, ``total`` (the stay costs ``price_cents``) or ``increment`` (the
    price reached at the band's start plus ``price_cents`` per started ``unit_min`` counted from the band's start, P8)."""

    from_min: int
    to_min: int | None
    kind: str
    price_cents: int
    unit_min: int | None


@dataclass(frozen=True, kw_only=True)
class GarageTariff:
    """One priced garage of the tariff model (schema 3, spec Amendment E1 and E6): a point in EPSG:25832 with its tariff in
    integer cents, minutes and seconds.

    A garage has exactly one tariff structure: the single-window form (``hourly_rate_cents`` per started
    ``billing_unit_min`` within ``[fee_start_s, fee_end_s)``), the tiered form (``tiers``, no fee window, E10) or the banded
    form (``bands`` with the fee window, E11). The tiered form may carry ONE closed free band in ``bands`` as its grace period
    (ASSUMPTION P10 for tiers, Task 4b3: a stay not longer than the band costs 0, a longer stay is priced by the tiers from its
    arrival); the free schedule of a car park that is free for every stay is the one open free band with the fee window.
    ``first_period_min`` with ``first_period_cents`` (both or neither; not with
    bands) may carry the clock window ``[first_period_start_s, first_period_end_s)`` it is tied to (both or neither, only
    with a first period; ASSUMPTION P6 as amended). ``daily_cap_cents`` (None = none) limits the price of a stay once;
    ``monthly_cents`` (None = no monthly product) is the monthly product that work and education stays use per working day
    (P2). Construction validates and raises ``ValueError`` naming the garage.
    """

    garage_id: str
    x_m: float
    y_m: float
    hourly_rate_cents: int | None = None
    billing_unit_min: int | None = None
    fee_start_s: int | None = None
    fee_end_s: int | None = None
    first_period_min: int | None = None
    first_period_cents: int | None = None
    first_period_start_s: int | None = None
    first_period_end_s: int | None = None
    daily_cap_cents: int | None = None
    tiers: tuple[GarageTier, ...] | None = None
    bands: tuple[GarageBand, ...] | None = None
    monthly_cents: int | None = None

    def __post_init__(self) -> None:
        if self.tiers is not None:
            object.__setattr__(self, "tiers", tuple(self.tiers))
        if self.bands is not None:
            object.__setattr__(self, "bands", tuple(self.bands))
        _check_garage(self)

    @property
    def form(self) -> str:
        """``"tiers"``, ``"bands"`` or ``"core"`` (the single-window form)."""
        if self.tiers is not None:
            return "tiers"
        return "bands" if self.bands is not None else "core"

    @functools.cached_property
    def band_objects(self) -> tuple:
        """The bands as the ``braunschweig.parking.garages.DurationBand`` objects its reference evaluation takes."""
        return tuple(parking_garages.DurationBand(band.from_min, band.to_min, band.kind, band.price_cents / 100.0,
                                                  band.unit_min) for band in self.bands)

    def to_json(self) -> dict:
        """The entry of the model's ``garages`` list: exactly the keys ``GARAGE_FIELDS_JSON``."""
        fields = {name: getattr(self, name) for name in GARAGE_FIELDS_JSON}
        if self.tiers is not None:
            fields["tiers"] = [{name: getattr(tier, name) for name in GARAGE_TIER_FIELDS} for tier in self.tiers]
        if self.bands is not None:
            fields["bands"] = [{name: getattr(band, name) for name in GARAGE_BAND_FIELDS} for band in self.bands]
        return fields

    @classmethod
    def from_json(cls, entry: dict) -> "GarageTariff":
        """The inverse of :meth:`to_json`; the entry must have exactly the keys ``GARAGE_FIELDS_JSON`` and every tier and
        band exactly its keys, otherwise ``ValueError`` (the Java reader is as strict)."""
        if sorted(entry) != sorted(GARAGE_FIELDS_JSON):
            raise ValueError(f"garage entry {entry.get('garage_id')!r}: keys {sorted(entry)} differ from the documented "
                             f"{sorted(GARAGE_FIELDS_JSON)}")
        fields = dict(entry)
        for name, item_class, keys in (("tiers", GarageTier, GARAGE_TIER_FIELDS), ("bands", GarageBand, GARAGE_BAND_FIELDS)):
            if fields[name] is not None:
                for item in fields[name]:
                    if sorted(item) != sorted(keys):
                        raise ValueError(f"garage entry {entry['garage_id']!r}: a {name} item has the keys {sorted(item)}, "
                                         f"expected {sorted(keys)}")
                fields[name] = tuple(item_class(**item) for item in fields[name])
        return cls(**fields)


def _check_garage(garage: GarageTariff) -> None:
    if not isinstance(garage.garage_id, str) or not garage.garage_id.strip():
        raise ValueError(f"garage id must be a non-empty string, got {garage.garage_id!r}")

    def fail(problem: str) -> NoReturn:
        raise ValueError(f"garage {garage.garage_id!r}: {problem}")

    for name in ("x_m", "y_m"):
        value = getattr(garage, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            fail(f"{name} must be a finite number of metres (EPSG:25832), got {value!r}")
    for name in ("hourly_rate_cents", "billing_unit_min", "fee_start_s", "fee_end_s", "first_period_min",
                 "first_period_cents", "first_period_start_s", "first_period_end_s", "daily_cap_cents", "monthly_cents"):
        value = getattr(garage, name)
        if value is None:
            continue
        if not _is_plain_int(value):
            fail(f"{name} must be an integer or None, got {value!r}")
        minimum = 0 if name in ("fee_start_s", "first_period_start_s") else 1
        if value < minimum:
            fail(f"{name} must be at least {minimum}, got {value}")
    core = [name for name in ("hourly_rate_cents", "billing_unit_min", "fee_start_s", "fee_end_s")
            if getattr(garage, name) is not None]
    if garage.tiers is not None:
        if core:
            fail(f"a tiered garage leaves the single-window core empty, found {core}")
        _check_tiers(garage, fail)
        if garage.bands is not None:
            _check_grace_band(garage, fail)
    elif garage.bands is not None:
        rate = [name for name in ("hourly_rate_cents", "billing_unit_min") if getattr(garage, name) is not None]
        if rate:
            fail(f"a banded garage leaves the rate and the billing unit empty, found {rate}")
        if garage.fee_start_s is None or garage.fee_end_s is None:
            fail("a banded garage needs its fee window (fee_start_s and fee_end_s)")
        if garage.first_period_min is not None:
            fail("a banded garage has no first period (its first band is the first period)")
        _check_bands(garage, fail)
    elif len(core) != 4:
        fail("a garage needs exactly one tariff structure: the complete single-window core (hourly_rate_cents, "
             f"billing_unit_min, fee_start_s, fee_end_s; found {core}), tiers or bands")
    if garage.fee_start_s is not None and garage.fee_end_s is not None and not (
            garage.fee_start_s < garage.fee_end_s <= SECONDS_PER_DAY):
        fail(f"fee window [{garage.fee_start_s}, {garage.fee_end_s}) s must satisfy 0 <= start < end <= {SECONDS_PER_DAY}")
    if (garage.first_period_min is None) != (garage.first_period_cents is None):
        fail("first_period_min and first_period_cents must be given together or both be empty")
    if (garage.first_period_start_s is None) != (garage.first_period_end_s is None):
        fail("first_period_start_s and first_period_end_s must be given together or both be empty")
    if garage.first_period_start_s is not None:
        if garage.first_period_min is None:
            fail("a first-period window needs a first period")
        if not garage.first_period_start_s < garage.first_period_end_s <= SECONDS_PER_DAY:
            fail(f"first-period window [{garage.first_period_start_s}, {garage.first_period_end_s}) s must satisfy "
                 f"0 <= start < end <= {SECONDS_PER_DAY}")
    if (garage.daily_cap_cents is not None and garage.first_period_cents is not None
            and garage.daily_cap_cents < garage.first_period_cents):
        fail(f"daily_cap_cents {garage.daily_cap_cents} is below first_period_cents {garage.first_period_cents}")


def _check_tiers(garage: GarageTariff, fail) -> None:
    if not garage.tiers:
        fail("tiers must not be empty")
    covered = []
    for tier in garage.tiers:
        if not isinstance(tier, GarageTier):
            fail(f"a tier must be a GarageTier, got {tier!r}")
        for name in GARAGE_TIER_FIELDS:
            value = getattr(tier, name)
            if not _is_plain_int(value) or value < (0 if name == "start_s" else 1):
                fail(f"tier {name} must be a positive integer (start_s: non-negative), got {value!r}")
        if tier.start_s >= SECONDS_PER_DAY or tier.end_s > SECONDS_PER_DAY or tier.start_s == tier.end_s:
            fail(f"tier [{tier.start_s}, {tier.end_s}) s is no time-of-day interval of positive length")
        covered.extend([(tier.start_s, SECONDS_PER_DAY), (0, tier.end_s)] if tier.end_s <= tier.start_s
                       else [(tier.start_s, tier.end_s)])
    if len({tier.unit_min for tier in garage.tiers}) != 1:
        fail("the tiers must share one unit length (the units of a stay are counted from its arrival, P6)")
    covered.sort()
    if any(later[0] < earlier[1] for earlier, later in zip(covered, covered[1:])):
        fail("tiers overlap")


def _check_grace_band(garage: GarageTariff, fail) -> None:
    """The bands of a tiered garage are its grace period: ONE closed free band (ASSUMPTION P10 for tiers); the tiers and a
    duration schedule exclude each other (one tariff structure per garage), and the first band is the only free stretch, so
    a tiered garage with a grace period has no first period."""
    _check_bands(garage, fail)
    bands = garage.bands
    if len(bands) != 1 or bands[0].kind != "free" or bands[0].to_min is None:
        fail("a tiered garage carries bands only as a grace period (one closed free band); tiers and a duration schedule "
             f"exclude each other (one tariff structure per garage), found {len(bands)} band(s) {[b.kind for b in bands]}")
    if garage.first_period_min is not None:
        fail("a tiered garage with a grace period has no first period (the free band is its only duration band)")


def _check_bands(garage: GarageTariff, fail) -> None:
    if not garage.bands:
        fail("bands must not be empty")
    previous_to = 0
    for position, band in enumerate(garage.bands):
        if not isinstance(band, GarageBand):
            fail(f"a band must be a GarageBand, got {band!r}")
        if band.kind not in GARAGE_BAND_KINDS:
            fail(f"band kind {band.kind!r} is not one of {GARAGE_BAND_KINDS}")
        if previous_to is None or band.from_min != previous_to:
            fail(f"band {position} starts at {band.from_min} min but the band before ends at {previous_to}; bands are "
                 "contiguous from 0 and only the last may be open-ended")
        if band.to_min is not None and (not _is_plain_int(band.to_min) or band.to_min <= band.from_min):
            fail(f"band {position} must end after it starts, got {band.from_min}-{band.to_min}")
        if band.kind == "free" and (band.price_cents != 0 or band.unit_min is not None):
            fail(f"band {position} is free: price_cents must be 0 and unit_min null")
        if band.kind == "total" and (not _is_plain_int(band.price_cents) or band.price_cents < 1
                                     or band.unit_min is not None):
            fail(f"band {position} is a total: price_cents must be positive and unit_min null")
        if band.kind == "increment" and (not _is_plain_int(band.price_cents) or band.price_cents < 1
                                         or not _is_plain_int(band.unit_min) or band.unit_min < 1):
            fail(f"band {position} is an increment: price_cents and unit_min must be positive")
        previous_to = band.to_min
    if garage.bands[0].from_min != 0:
        fail("the first band must start at 0 min")


@dataclass
class GarageOptionCounters:
    """Counts of the garage pricing, so that no fallback fires silently (rates are logged by the callers).

    ``stays`` is every call of ``parking_cost_with_garages``; ``eligible`` the stays in a street or resident zone that
    pass the early rules with garage options switched on; ``with_garages_in_range`` the eligible stays with at least one
    garage within the maximum distance; ``street_free`` those whose street option costs 0 and pay 0 (E4);
    ``street_unavailable`` those priced over the garages alone (E4); ``expected`` the stays priced as an expectation
    (outcome ``PAID_EXPECTED``); ``option_evaluations`` the garage options priced; ``closed_schedule_repeats`` the garage
    options whose stay was longer than a closed duration schedule and was priced by repeating it per started 24 h (P9).
    """

    stays: int = 0
    eligible: int = 0
    with_garages_in_range: int = 0
    street_free: int = 0
    street_unavailable: int = 0
    expected: int = 0
    option_evaluations: int = 0
    closed_schedule_repeats: int = 0

    def summary(self) -> str:
        """One log line with the counts and rates (an empty denominator gives a plain count)."""
        def rate(part: int, whole: int) -> str:
            return f"{part}/{whole} ({100.0 * part / whole:.1f} %)" if whole else f"{part}/{whole}"
        return (f"[parking-garages] stays {self.stays}; eligible {self.eligible}; garages in range "
                f"{rate(self.with_garages_in_range, self.eligible)}; street free (E4) "
                f"{rate(self.street_free, self.with_garages_in_range)}; street unavailable "
                f"{rate(self.street_unavailable, self.with_garages_in_range)}; priced as expectation {self.expected}; "
                f"garage options priced {self.option_evaluations}, closed schedule repeated (P9) "
                f"{rate(self.closed_schedule_repeats, self.option_evaluations)}")


def _first_period_applies(garage: GarageTariff, arrival_s: int) -> bool:
    """ASSUMPTION P6 as amended (spec E10): a first period tied to a clock window is charged only when the arrival lies in
    the window ``[first_period_start_s, first_period_end_s)``; without a window it is charged for every use."""
    if garage.first_period_min is None:
        return False
    if garage.first_period_start_s is None:
        return True
    return garage.first_period_start_s <= arrival_s % SECONDS_PER_DAY < garage.first_period_end_s


def _tiered_cents(garage: GarageTariff, arrival_s: int, departure_s: int) -> int:
    """ASSUMPTION P6: units are counted from the arrival (or from the end of the first period) and each started unit costs
    the price of the tier in force at the unit's start; a start in no tier costs nothing. The day cap applies once.

    A tiered garage with a grace period (one closed free band, ASSUMPTION P10 for tiers) costs 0 for a stay that is not longer
    than the band (the elapsed stay, arrival to departure: the tiered form has no fee window); a longer stay is priced by
    the tiers from its arrival as if there were no free period (the free minutes are not deducted)."""
    if departure_s == arrival_s:
        return 0
    if garage.bands is not None and departure_s - arrival_s <= garage.bands[0].to_min * SECONDS_PER_MINUTE:
        return 0
    cents, unit_start_s = 0, arrival_s
    if _first_period_applies(garage, arrival_s):
        cents += garage.first_period_cents
        unit_start_s = arrival_s + garage.first_period_min * SECONDS_PER_MINUTE
    unit_s = garage.tiers[0].unit_min * SECONDS_PER_MINUTE
    cap = garage.daily_cap_cents
    while unit_start_s < departure_s:
        time_of_day_s = unit_start_s % SECONDS_PER_DAY
        cents += next((tier.price_cents for tier in garage.tiers if tier.covers(time_of_day_s)), 0)
        if cap is not None and cents >= cap:
            return cap
        unit_start_s += unit_s
    return cents


def _banded_cents(garage: GarageTariff, chargeable_s: int, counters: GarageOptionCounters | None) -> int:
    """ASSUMPTION P8 through ``braunschweig.parking.garages.duration_band_price_eur`` (the reference evaluation, called, not
    copied) on the chargeable minutes ``chargeable_s / 60``; ASSUMPTION P9 beyond a closed schedule."""
    if chargeable_s == 0:
        return 0
    cap_eur = None if garage.daily_cap_cents is None else garage.daily_cap_cents / 100.0

    def price_cents(seconds: int) -> int:
        return int(round(parking_garages.duration_band_price_eur(garage.band_objects, seconds / SECONDS_PER_MINUTE,
                                                                 cap_eur) * 100))

    last = garage.bands[-1]
    if last.to_min is None or chargeable_s <= last.to_min * SECONDS_PER_MINUTE:
        return price_cents(chargeable_s)
    # ASSUMPTION P9: a stay longer than a closed schedule (Peine ends at 1440 min) is priced per started 24 h: k full
    # periods at the price of 1440 min plus the price of the remainder, every period capped by the day cap. Counted, never
    # a silent guess; a closed schedule that does not end at 24 h states no rule for a longer stay and raises.
    if last.to_min != MINUTES_PER_DAY:
        raise ValueError(f"garage {garage.garage_id!r}: the stay of {chargeable_s} s is longer than the closed schedule "
                         f"that ends at {last.to_min} min, and P9 repeats only a schedule that ends at {MINUTES_PER_DAY} min")
    if counters is not None:
        counters.closed_schedule_repeats += 1
    full_periods = (chargeable_s - 1) // SECONDS_PER_DAY
    return full_periods * price_cents(SECONDS_PER_DAY) + price_cents(chargeable_s - full_periods * SECONDS_PER_DAY)


def garage_metered_cents(garage: GarageTariff, arrival_s: int, departure_s: int,
                         counters: GarageOptionCounters | None = None) -> int:
    """The metered or day price of a stay at a garage in integer cents, by the garage's tariff structure (no monthly product).

    Single-window form: the metering primitive of spec 3.2 rule 8 over the stay's seconds in the garage's fee window (the
    first period only when its clock window holds the arrival); tiered form: ``_tiered_cents`` (P6, E10; with a grace period
    P10); banded form: the duration schedule over the chargeable minutes (P8, E11, P9; P10 is encoded as bands, and a car
    park that is free for every stay as the one open free band). A stay without a chargeable second costs 0. Raises ``ValueError`` for a negative time or a departure before the arrival. Pure.
    """
    arrival_s = _time_s("arrival_s", arrival_s)
    departure_s = _time_s("departure_s", departure_s)
    if departure_s < arrival_s:
        raise ValueError(f"departure_s {departure_s} is before arrival_s {arrival_s}")
    if garage.tiers is not None:
        return _tiered_cents(garage, arrival_s, departure_s)
    chargeable_s = chargeable_seconds(arrival_s, departure_s, garage.fee_start_s, garage.fee_end_s)
    if garage.bands is not None:
        return _banded_cents(garage, chargeable_s, counters)
    applies = _first_period_applies(garage, arrival_s)
    return _metered_cents(chargeable_s, hourly_rate_cents=garage.hourly_rate_cents,
                          billing_unit_min=garage.billing_unit_min,
                          first_period_min=garage.first_period_min if applies else None,
                          first_period_cents=garage.first_period_cents if applies else None,
                          daily_cap_cents=garage.daily_cap_cents)


def garage_monthly_day_cents(monthly_cents: int) -> int:
    """ASSUMPTION P2: a monthly garage product per working day, ``monthly_cents / 21`` rounded half up to the cent (integer
    arithmetic: ``(2 * monthly_cents + 21) // 42``; the quotient of a whole number of cents by 21 is never an exact half)."""
    return (2 * monthly_cents + WORKING_DAYS_PER_MONTH) // (2 * WORKING_DAYS_PER_MONTH)


def garage_option_cents(garage: GarageTariff, arrival_s: int, departure_s: int, *, purpose: str,
                        counters: GarageOptionCounters | None = None) -> int:
    """The cost of the garage option of a stay (spec E2): the cheaper of its metered or day product and, for the commuter
    purposes work and education only, its monthly product per working day (P2). 0 ct is a valid option. Pure."""
    if counters is not None:
        counters.option_evaluations += 1
    cents = garage_metered_cents(garage, arrival_s, departure_s, counters)
    if garage.monthly_cents is not None and purpose in COMMUTER_PURPOSES:
        cents = min(cents, garage_monthly_day_cents(garage.monthly_cents))
    return cents


def garage_options_in_range(garages, x_m: float, y_m: float, max_distance_m: float = GARAGE_MAX_DISTANCE_M) -> list:
    """The garage options of a destination (spec E2): ``(garage, distance_m)`` for every garage within ``max_distance_m``
    (inclusive) of ``(x_m, y_m)``, ordered by ascending ``garage_id``. The distance is the straight line in EPSG:25832,
    ``sqrt(dx * dx + dy * dy)`` (no ``hypot``, so that Java computes the same double). Raises ``ValueError`` for a
    non-finite coordinate or a non-positive maximum distance and for a duplicate garage id. Pure."""
    for name, value in (("x_m", x_m), ("y_m", y_m), ("max_distance_m", max_distance_m)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number, got {value!r}")
    if max_distance_m <= 0:
        raise ValueError(f"max_distance_m must be positive, got {max_distance_m}")
    ordered = sorted(garages, key=lambda garage: garage.garage_id)
    if len({garage.garage_id for garage in ordered}) != len(ordered):
        raise ValueError("duplicate garage_id among the garages")
    options = []
    for garage in ordered:
        dx, dy = garage.x_m - x_m, garage.y_m - y_m
        distance_m = math.sqrt(dx * dx + dy * dy)
        if distance_m <= max_distance_m:
            options.append((garage, distance_m))
    return options


class GarageStayPrice(NamedTuple):
    """Result of ``parking_cost_with_garages_detail``: the price in cents, the outcome, the sum of the garage
    probabilities (0.0 when no garage acted) and the expected cost before rounding (None when no expectation was formed)."""

    cents: int
    outcome: str
    garage_probability: float
    expected_cents: float | None


#: The outcomes decided before any product is priced; with them, and on a campus, the garages never act.
_EARLY_OUTCOMES = frozenset({NO_ZONE, HOME, EMPLOYER_FREE, RESIDENT_FREE, OUTSIDE_FEE_HOURS})


def parking_cost_with_garages_detail(tariff: ZoneTariff | None, arrival_s: int, departure_s: int, *, purpose: str,
                                     parking_free: bool, resident_of_zone: bool, resident_of_district: bool = False,
                                     garage_options=(), decay_m: float = 0.0,
                                     counters: GarageOptionCounters | None = None) -> GarageStayPrice:
    """``parking_cost_with_garages`` with the unrounded expectation; see there for the rules."""
    cents, outcome = parking_cost_cents(tariff, arrival_s, departure_s, purpose=purpose, parking_free=parking_free,
                                        resident_of_zone=resident_of_zone, resident_of_district=resident_of_district)
    if counters is not None:
        counters.stays += 1
    if isinstance(decay_m, bool) or not isinstance(decay_m, (int, float)) or not math.isfinite(decay_m) or decay_m < 0:
        raise ValueError(f"decay_m must be a finite number of metres >= 0 (0 switches the garage options off), "
                         f"got {decay_m!r}")
    options = list(garage_options)
    for option in options:
        if not (isinstance(option, tuple) and len(option) == 2 and isinstance(option[0], GarageTariff)):
            raise TypeError(f"a garage option is (GarageTariff, distance_m), got {option!r}")
    options.sort(key=lambda option: option[0].garage_id)
    for garage, distance_m in options:
        if (isinstance(distance_m, bool) or not isinstance(distance_m, (int, float)) or not math.isfinite(distance_m)
                or distance_m < 0):
            raise ValueError(f"garage {garage.garage_id!r}: distance_m must be a finite number >= 0, got {distance_m!r}")
    if len({garage.garage_id for garage, _ in options}) != len(options):
        raise ValueError("duplicate garage_id among the garage options")
    unchanged = GarageStayPrice(cents, outcome, 0.0, None)
    # Early rules first (R-4d-2), then campus zones (priced as before: no garages); the garages act on street and resident
    # zones only, with the options switched on and at least one garage in range.
    if outcome in _EARLY_OUTCOMES or tariff.zone_type == CAMPUS or decay_m == 0:
        return unchanged
    if counters is not None:
        counters.eligible += 1
    if not options:
        return unchanged
    if counters is not None:
        counters.with_garages_in_range += 1
    # E8: with garage options the zone-level garage family is superseded; the street option is the street or long-stay
    # product or the zone commuter product, or unavailable (maximum stay exceeded without a long-stay product).
    chargeable_s = chargeable_seconds(arrival_s, departure_s, tariff.fee_start_s, tariff.fee_end_s)
    street_products = [product for product in (_street_product(tariff, chargeable_s), _commuter_product(tariff, purpose))
                       if product is not None]
    street = min(street_products, key=lambda product: product[0]) if street_products else None
    if street is not None and street[0] == 0:
        # E4: nobody pays a garage when the street is free.
        if counters is not None:
            counters.street_free += 1
        return GarageStayPrice(0, street[1], 0.0, None)
    if street is None and counters is not None:
        counters.street_unavailable += 1
    weights, costs = ([1.0], [street[0]]) if street is not None else ([], [])
    for garage, distance_m in options:
        weights.append(math.exp(-distance_m / decay_m))
        costs.append(garage_option_cents(garage, arrival_s, departure_s, purpose=purpose, counters=counters))
    # R-4d-4: P_i = w_i / sum(w) and the expectation are summed left to right in double precision in the order street,
    # then the garages by ascending id; Java does the same operations in the same order.
    total_weight = 0.0
    for weight in weights:
        total_weight += weight
    expected, garage_probability = 0.0, 0.0
    first_garage = 1 if street is not None else 0
    for position, (weight, option_cents) in enumerate(zip(weights, costs)):
        probability = weight / total_weight
        expected += probability * option_cents
        if position >= first_garage:
            garage_probability += probability
    if counters is not None:
        counters.expected += 1
    # Rounded half up to the cent once, at the end.
    return GarageStayPrice(int(math.floor(expected + 0.5)), PAID_EXPECTED, garage_probability, expected)


def parking_cost_with_garages(tariff: ZoneTariff | None, arrival_s: int, departure_s: int, *, purpose: str,
                              parking_free: bool, resident_of_zone: bool, resident_of_district: bool = False,
                              garage_options=(), decay_m: float = 0.0,
                              counters: GarageOptionCounters | None = None) -> tuple[int, str, float]:
    """Parking cost of one car stay with garage options (spec Amendment E): ``(cents, outcome, garage_probability)``.

    ``garage_options`` are ``(GarageTariff, distance_m)`` pairs already filtered to the maximum distance (see
    ``garage_options_in_range``); ``decay_m`` is the decay length lambda in metres (ASSUMPTION G1; 0 = garage options off).
    The early rules, the campus zones and the street option are those of ``parking_cost_cents`` (called, not copied):

    1. a stay that ``parking_cost_cents`` decides before any product (no zone, home, employer-free, resident, outside the
       street fee window), a campus stay, ``decay_m`` 0 or no garage in range prices exactly as ``parking_cost_cents``
       (same outcome, probability 0.0);
    2. otherwise the options are the street (weight 1) and every garage g (weight ``exp(-d_g / decay_m)``, E3). The street
       option costs its cheapest street-side product, the street or long-stay product or the zone commuter product (the
       zone-level garage family is superseded, E8), or is unavailable above the maximum stay without a long-stay product;
       a garage option costs its metered or day product, for work and education also its monthly product per working day
       (P2), whichever is cheaper (0 ct is a valid option; tiers, bands and first periods: ``garage_metered_cents``);
    3. a street option that costs 0 means the stay pays 0 (E4): outcome of the street product, probability 0.0; an
       unavailable street renormalises the weights over the garages;
    4. else ``P_i = w_i / sum(w)``, the price is ``floor(sum(P_i * cost_i) + 0.5)`` cents (rounded half up once, at the end),
       the outcome ``PAID_EXPECTED`` and the probability the sum of the garage ``P_i``.

    ``counters`` (optional) is incremented as documented at ``GarageOptionCounters``. Raises as ``parking_cost_cents``, and
    ``ValueError`` for a negative or non-finite ``decay_m`` or distance and duplicate garage ids. Pure but for ``counters``.
    """
    detail = parking_cost_with_garages_detail(
        tariff, arrival_s, departure_s, purpose=purpose, parking_free=parking_free, resident_of_zone=resident_of_zone,
        resident_of_district=resident_of_district, garage_options=garage_options, decay_m=decay_m, counters=counters)
    return detail.cents, detail.outcome, detail.garage_probability
