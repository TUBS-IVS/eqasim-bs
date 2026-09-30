"""Deterministic parking cost of one car stay in a zone (issue #249): the Python reference implementation.

This module implements the algorithm of the design spec section 3.2
(``docs/superpowers/specs/2026-09-28-parking-cost-zones-design.md``, local). The Java
``ParkingCostCalculator`` (eqasim-java-bs) must reproduce it exactly; the shared contract is the golden
cases of ``braunschweig.parking.golden_cases``, exported with the fixture tariffs to
``tests/fixtures/parking/parking_golden_cases.json``. All money is in integer euro cents and all times are
in integer simulation seconds, so both implementations run the same integer arithmetic and cannot drift
apart through floating-point rounding.

Assumptions (spec section 7; their full texts travel with the tariff JSON, see
``braunschweig.parking.tariff_export.ASSUMPTIONS_REGISTER``):

- D1: the simulated day is an average weekday, so a zone's fee window repeats every 86,400 s.
- H1: home activities are free everywhere.
- R1: living inside a resident zone equals holding its permit (``resident_of_zone``).
- C1: members (work and education purposes) pay the campus day product; passes are not modelled.
- M1: the maximum stay is compared with the CHARGEABLE duration; a longer stay buys the long-stay product.
- T1: a terminal stay pays until the fee window of the arrival day ends (``terminal_departure_s``).
- Z1: outside every zone parking is free (no tariff -> ``NO_ZONE``).

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
OUTCOMES = (HOME, EMPLOYER_FREE, RESIDENT_FREE, OUTSIDE_FEE_HOURS, FREE_WITHIN_LIMIT, PAID_METERED,
            PAID_LONG_STAY, PAID_CAMPUS_MEMBER, PAID_CAMPUS_GUEST, NO_ZONE)

# Tariff fields that may be None ("not applicable", an empty tariff-table cell).
_OPTIONAL_FIELDS = ("hourly_rate_cents", "billing_unit_min", "free_if_stay_at_most_min", "first_period_min",
                    "first_period_cents", "daily_cap_cents", "max_stay_min", "long_stay_product_cents",
                    "member_day_cents", "guest_day_cents")
# Spec 3.2 tests these fields for truthiness ("if t.daily_cap_cents: ..."), so a 0 would silently mean
# "not set" (a zero cap would be ignored, not applied). Requiring them to be positive when set makes
# "is set" and "is truthy" the same and keeps the Python and Java readings identical.
_POSITIVE_FIELDS = frozenset({"billing_unit_min", "free_if_stay_at_most_min", "first_period_min",
                              "daily_cap_cents", "max_stay_min"})
_PAIRED_FIELDS = (("first_period_min", "first_period_cents"), ("max_stay_min", "long_stay_product_cents"))
# Spec 3.1 required fields per zone type. billing_unit_min is also required for resident zones although
# spec 3.1 does not list it: the metered step of spec 3.2 divides by it for every non-campus zone.
_REQUIRED_FIELDS = {
    STREET_PAID: ("hourly_rate_cents", "billing_unit_min"),
    RESIDENT_ZONE: ("hourly_rate_cents", "billing_unit_min", "max_stay_min", "long_stay_product_cents"),
    CAMPUS: ("member_day_cents", "guest_day_cents"),
}
# Fields the zone type does not have (spec 3.1): a value there would be a tariff element the cost
# algorithm never applies, so it is rejected instead of being silently ignored.
_NOT_APPLICABLE_FIELDS = {
    STREET_PAID: ("member_day_cents", "guest_day_cents"),
    RESIDENT_ZONE: ("free_if_stay_at_most_min", "first_period_min", "first_period_cents", "daily_cap_cents",
                    "member_day_cents", "guest_day_cents"),
    CAMPUS: ("hourly_rate_cents", "billing_unit_min", "free_if_stay_at_most_min", "first_period_min",
             "first_period_cents", "daily_cap_cents", "max_stay_min", "long_stay_product_cents"),
}


@dataclass(frozen=True, kw_only=True)
class ZoneTariff:
    """The tariff of one parking zone (spec 3.1, 5.3 and 5.4) in integer cents, minutes and seconds.

    None means "not applicable" (an empty cell of the tariff table). Money is in euro cents
    (``*_cents``; ``hourly_rate_cents`` per hour), durations in minutes (``*_min``) and the daily fee window
    ``[fee_start_s, fee_end_s)`` in seconds after midnight of the (average weekday, D1) day. Construction
    validates the tariff against its zone type and raises ``ValueError`` on any inconsistency, so an
    invalid tariff table fails when it is read, not when a stay is priced.
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

    def __post_init__(self) -> None:
        _check_tariff(self)


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
    for first, second in _PAIRED_FIELDS:
        if (getattr(tariff, first) is None) != (getattr(tariff, second) is None):
            fail(f"{first} and {second} must be given together or both be empty")
    for name in _REQUIRED_FIELDS[tariff.zone_type]:
        if getattr(tariff, name) is None:
            fail(f"{name} is required for zone_type {tariff.zone_type!r}")
    for name in _NOT_APPLICABLE_FIELDS[tariff.zone_type]:
        if getattr(tariff, name) is not None:
            fail(f"{name} does not apply to zone_type {tariff.zone_type!r} and must be empty")
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


def parking_cost_cents(tariff: ZoneTariff | None, arrival_s: int, departure_s: int, *, purpose: str,
                       parking_free: bool, resident_of_zone: bool) -> tuple[int, str]:
    """Parking cost of one car stay in integer euro cents, with the outcome that decided it (spec 3.2).

    ``tariff`` is the tariff of the zone the activity lies in, or None when it lies in no zone (Z1: free,
    outcome ``NO_ZONE``, decided before every check below, so also for the home purpose). ``arrival_s`` is
    the car arrival and ``departure_s`` the activity departure in simulation seconds (for a terminal
    activity use ``terminal_departure_s``); ``purpose`` is the MATSim activity type, ``parking_free`` the
    activity attribute ``parkingFree``, ``resident_of_zone`` whether the person lives in this zone (R1). The
    checks run in the order of spec 3.2; the first that applies decides:

    1. home purpose -> 0, ``HOME`` (H1);
    2. ``parking_free`` -> 0, ``EMPLOYER_FREE``;
    3. resident-exempt zone and a resident of it -> 0, ``RESIDENT_FREE`` (R1);
    4. no chargeable second -> 0, ``OUTSIDE_FEE_HOURS``;
    5. campus -> the member day product for work and education, else the guest day product (C1);
    6. chargeable minutes (seconds rounded up) within the free threshold -> 0, ``FREE_WITHIN_LIMIT``;
    7. chargeable minutes above the maximum stay -> the long-stay product, ``PAID_LONG_STAY`` (M1);
    8. metered: the first period is charged once in full when any part of it is used, the rest in started
       billing units at the hourly rate rounded half up to the cent, then capped by the daily cap, which
       applies once per stay as spec 3.2 states; 0 ct is ``FREE_WITHIN_LIMIT``, anything else ``PAID_METERED``.

    Raises ``ValueError`` for a departure before the arrival or negative times and ``TypeError`` for
    non-integer times or non-bool flags (a text "false" must never count as true). Pure function.
    """
    arrival_s = _time_s("arrival_s", arrival_s)
    departure_s = _time_s("departure_s", departure_s)
    if departure_s < arrival_s:
        raise ValueError(f"departure_s {departure_s} is before arrival_s {arrival_s}")
    if not isinstance(purpose, str) or not purpose:
        raise TypeError(f"purpose must be a non-empty activity type string, got {purpose!r}")
    for name, flag in (("parking_free", parking_free), ("resident_of_zone", resident_of_zone)):
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
    if tariff.resident_exempt and resident_of_zone:
        return 0, RESIDENT_FREE
    if chargeable_s == 0:
        return 0, OUTSIDE_FEE_HOURS
    if tariff.zone_type == CAMPUS:
        if purpose in CAMPUS_MEMBER_PURPOSES:
            return tariff.member_day_cents, PAID_CAMPUS_MEMBER
        return tariff.guest_day_cents, PAID_CAMPUS_GUEST

    # "is not None" equals the spec's truthiness test here: ZoneTariff rejects 0 for these fields.
    chargeable_min = _ceil_div(chargeable_s, SECONDS_PER_MINUTE)
    if tariff.free_if_stay_at_most_min is not None and chargeable_min <= tariff.free_if_stay_at_most_min:
        return 0, FREE_WITHIN_LIMIT
    if tariff.max_stay_min is not None and chargeable_min > tariff.max_stay_min:
        return tariff.long_stay_product_cents, PAID_LONG_STAY

    remaining_s = chargeable_s
    cents = 0
    if tariff.first_period_min is not None:
        cents += tariff.first_period_cents
        remaining_s -= min(remaining_s, tariff.first_period_min * SECONDS_PER_MINUTE)
    units = _ceil_div(remaining_s, tariff.billing_unit_min * SECONDS_PER_MINUTE)
    # Verbatim spec 3.2: units * minutes per unit * cents per hour is 60 times the price in cents; adding 30
    # before the integer division by 60 rounds half up to the cent.
    cents += (units * tariff.billing_unit_min * tariff.hourly_rate_cents + 30) // 60
    if tariff.daily_cap_cents is not None:
        cents = min(cents, tariff.daily_cap_cents)
    return cents, (FREE_WITHIN_LIMIT if cents == 0 else PAID_METERED)
