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

import operator
from dataclasses import dataclass
from typing import NoReturn

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
# The two v2 outcomes are appended at the END, so the declaration order of the v1 outcomes (the order of the Java
# ParkingOutcome enum and of the outcome report) is unchanged.
OUTCOMES = (HOME, EMPLOYER_FREE, RESIDENT_FREE, OUTSIDE_FEE_HOURS, FREE_WITHIN_LIMIT, PAID_METERED,
            PAID_LONG_STAY, PAID_CAMPUS_MEMBER, PAID_CAMPUS_GUEST, NO_ZONE, PAID_GARAGE, PAID_COMMUTER)

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
