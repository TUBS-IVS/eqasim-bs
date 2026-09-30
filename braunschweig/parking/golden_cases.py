"""The 38 golden parking-cost cases: the contract the Python reference and the Java port must both meet.

Each case is one car stay in a zone of the fixture tariff set (loaded by
``scripts/export_parking_golden_cases.py``; the set is inspired by the real tariff table but pins
arithmetic, not truth). The cases are hard-coded here ONCE and imported by ``tests/test_parking_cost.py``
and by the exporter, which checks them against ``braunschweig.parking.cost`` and writes them, together
with the fixture tariffs in cents, to ``tests/fixtures/parking/parking_golden_cases.json`` for the Java
``ParkingCostCalculatorTest``.

Fields of a case (they are the keys of the JSON cases, too):

- ``id``: ``G01`` .. ``G38``.
- ``zone_id``: a zone of the fixture tariff set.
- ``arrival_s``, ``departure_s``: car arrival and activity departure in simulation seconds (may exceed
  86,400).
- ``terminal``: the destination is the last plan element. ``departure_s`` is then None and the
  implementation derives it from ``arrival_s`` with the terminal-stay rule (assumption T1,
  ``cost.terminal_departure_s``).
- ``purpose``: the MATSim activity type; ``parking_free``: the activity attribute ``parkingFree``;
  ``resident_of_zone``: whether the person lives in this zone (assumption R1).
- ``expected_cents`` (integer euro cents) and ``expected_outcome`` (a ``cost.OUTCOMES`` name); both None
  when ``expected_error`` is true, i.e. the stay is invalid -- its departure lies before its arrival -- and
  the calculation must raise the ``ValueError`` of the stay check (``STAY_ERROR_PATTERN``), not any other.

G01..G26 are the cases of the implementation plan. G27..G38 pin what those leave open:

- the half-up rounding of a metered price to the cent, on the fixture zone ``fx_frac`` (175 ct/h in 1-min
  units, i.e. 175/60 ct a minute): G27 rounds 2.917 ct up to 3 ct, G28 rounds the exact half 52.5 ct up to
  53 ct (banker's rounding would give 52 ct), G29 rounds 20.417 ct down to 20 ct, and G30 caps 1050 ct at
  the daily cap of 700 ct;
- the resident-exemption condition: a resident of a zone WITHOUT the exemption pays like everybody else (G31);
- the minute ceiling of the thresholds: one second past the maximum stay buys the long-stay product (G32),
  exactly the maximum does not (G33), and one second past the free threshold pays the first period (G34);
- a started billing unit: one second inside the fee window is one charged minute (G35);
- the order of the early rules: the chargeable-time check precedes the campus day products (G36), the home
  rule precedes the employer-free rule (G37), and the resident exemption precedes the chargeable-time check
  (G38).

The Java ``ParkingCostCalculatorTest`` evaluates every case of the JSON file, so a port that meets the file
meets these pins, too. That a stay outside every zone is ``NO_ZONE`` even for the home purpose is not a golden
case (every case lies in a fixture zone); ``tests/test_parking_cost.py`` pins it for the Python reference.
"""
from __future__ import annotations

import re
from typing import Mapping, Sequence

from braunschweig.parking import cost

CASE_FIELDS = ("id", "zone_id", "arrival_s", "departure_s", "purpose", "parking_free", "resident_of_zone",
               "terminal", "expected_cents", "expected_outcome", "expected_error")

#: The message of the stay check of ``cost.parking_cost_cents`` (and ``cost.chargeable_seconds``) for a
#: departure before the arrival: the only ValueError an ``expected_error`` case may raise. Any other ValueError
#: (an unknown zone id, a rejected tariff) means the case failed for a different reason than it pins.
STAY_ERROR_PATTERN = re.compile(r"departure_s -?\d+ is before arrival_s -?\d+")

# Columns in CASE_FIELDS order; the comment above a row is its hand derivation. Times of day on day 0:
# 32400 = 09:00, 36000 = 10:00, 61200 = 17:00, 64800 = 18:00, 72000 = 20:00, 86400 = 24:00.
_CASE_ROWS = (
    # 31 chargeable min in 1-min units at 180 ct/h = 93 ct.
    ("G01", "fx_bs_ia",  36000,  37860, "shop",      False, False, False,   93, "PAID_METERED",       False),
    # 240 chargeable min > max stay 180 -> long-stay product (M1).
    ("G02", "fx_bs_ia",  32400,  46800, "shop",      False, False, False,  900, "PAID_LONG_STAY",     False),
    # 480 chargeable min (09:00-17:00) = 1440 ct, capped at 900.
    ("G03", "fx_bs_ib",  28800,  61200, "work",      False, False, False,  900, "PAID_METERED",       False),
    # parkingFree at the workplace.
    ("G04", "fx_bs_ib",  28800,  61200, "work",      True,  False, False,    0, "EMPLOYER_FREE",      False),
    # Home is free everywhere (H1).
    ("G05", "fx_bs_ib",  28800,  61200, "home",      False, False, False,    0, "HOME",               False),
    # 20:30-22:00 lies after the 09:00-20:00 fee window.
    ("G06", "fx_bs_ia",  73800,  79200, "leisure",   False, False, False,    0, "OUTSIDE_FEE_HOURS",  False),
    # Terminal (T1): departure = max(70200, 0 + 72000) = 72000 -> 30 min -> 90 ct.
    ("G07", "fx_bs_ia",  70200,   None, "shop",      False, False, True,    90, "PAID_METERED",       False),
    # Terminal arrival after 20:00: departure = max(75600, 72000) = arrival, nothing is chargeable.
    ("G08", "fx_bs_ia",  75600,   None, "shop",      False, False, True,     0, "OUTSIDE_FEE_HOURS",  False),
    # 25 chargeable min <= 30 free threshold.
    ("G09", "fx_sz",     39600,  41100, "shop",      False, False, False,    0, "FREE_WITHIN_LIMIT",  False),
    # 31 min > 30: the first period (60 min, 70 ct) is charged once and covers everything.
    ("G10", "fx_sz",     39600,  41460, "shop",      False, False, False,   70, "PAID_METERED",       False),
    # 140 min: 70 ct + 80 min in 6-min units = 14 units x 10 ct = 140 ct -> 210 ct.
    ("G11", "fx_sz",     39600,  48000, "shop",      False, False, False,  210, "PAID_METERED",       False),
    # Only 17:30-18:00 = 30 min is chargeable, <= 30 free threshold.
    ("G12", "fx_sz",     63000,  68400, "shop",      False, False, False,    0, "FREE_WITHIN_LIMIT",  False),
    # Crosses midnight: the day-0 window ends at 86400 -> 60 min -> first period 110 ct.
    ("G13", "fx_wob",    82800,  91800, "leisure",   False, False, False,  110, "PAID_METERED",       False),
    # 600 min: 110 ct + 9 started hours x 120 ct = 1190 ct, capped at 600.
    ("G14", "fx_wob",    28800,  64800, "work",      False, False, False,  600, "PAID_METERED",       False),
    # 45 min in 30-min units = 2 units x 50 ct.
    ("G15", "fx_pe",     36000,  38700, "shop",      False, False, False,  100, "PAID_METERED",       False),
    # 240 chargeable min (10:00-14:00) > max stay 180 -> long-stay product.
    ("G16", "fx_pe",     36000,  50400, "shop",      False, False, False,  500, "PAID_LONG_STAY",     False),
    # 90 min <= max stay 120 at rate 0 -> 0 ct (disc parking for non-residents).
    ("G17", "fx_res_a",  36000,  41400, "shop",      False, False, False,    0, "FREE_WITHIN_LIMIT",  False),
    # 540 min > max stay 120 for a non-resident -> long-stay product.
    ("G18", "fx_res_a",  28800,  61200, "work",      False, False, False,  900, "PAID_LONG_STAY",     False),
    # Resident of the zone (R1).
    ("G19", "fx_res_a",  28800,  61200, "work",      False, True,  False,    0, "RESIDENT_FREE",      False),
    # Members are exactly the work and education purposes (C1); everybody else pays the guest product.
    ("G20", "fx_campus", 28800,  61200, "work",      False, False, False,  350, "PAID_CAMPUS_MEMBER", False),
    ("G21", "fx_campus", 28800,  61200, "education", False, False, False,  350, "PAID_CAMPUS_MEMBER", False),
    ("G22", "fx_campus", 50400,  57600, "leisure",   False, False, False,  900, "PAID_CAMPUS_GUEST",  False),
    # parkingFree is checked before the campus day products.
    ("G23", "fx_campus", 28800,  61200, "work",      True,  False, False,    0, "EMPLOYER_FREE",      False),
    # Day-1 window [118800, 158400) overlaps [100800, 122400) by 3600 s -> 60 min -> 180 ct.
    ("G24", "fx_bs_ia", 100800, 122400, "shop",      False, False, False,  180, "PAID_METERED",       False),
    # Day 0: 68400..72000 = 3600 s; the day-1 window starts at the departure -> 60 min -> 180 ct (below cap).
    ("G25", "fx_bs_ib",  68400, 118800, "shop",      False, False, False,  180, "PAID_METERED",       False),
    # Departure before arrival is invalid input and raises.
    ("G26", "fx_bs_ia",  37860,  36000, "shop",      False, False, False, None, None,                 True),
    # G27..G30: fx_frac meters 175 ct/h in 1-min units (08:00-18:00, cap 700 ct), i.e. 175/60 ct a minute.
    # 1 min: (1 * 175 + 30) // 60 = 3 (2.917 ct rounds half up).
    ("G27", "fx_frac",   36000,  36060, "shop",      False, False, False,    3, "PAID_METERED",       False),
    # 18 min: (18 * 175 + 30) // 60 = 53 (the exact half 52.5 ct rounds up; banker's rounding would give 52).
    ("G28", "fx_frac",   36000,  37080, "shop",      False, False, False,   53, "PAID_METERED",       False),
    # 7 min: (7 * 175 + 30) // 60 = 20 (20.417 ct rounds down).
    ("G29", "fx_frac",   36000,  36420, "shop",      False, False, False,   20, "PAID_METERED",       False),
    # 360 min: (360 * 175 + 30) // 60 = 1050 ct, capped at 700.
    ("G30", "fx_frac",   36000,  57600, "shop",      False, False, False,  700, "PAID_METERED",       False),
    # fx_bs_ib is not resident-exempt: a resident pays 31 min at 3 ct = 93 ct like everybody else (R1).
    ("G31", "fx_bs_ib",  36000,  37860, "shop",      False, True,  False,   93, "PAID_METERED",       False),
    # 180 min + 1 s: ceil(10801 / 60) = 181 chargeable min > max stay 180 -> long-stay product (M1).
    ("G32", "fx_bs_ia",  36000,  46801, "shop",      False, False, False,  900, "PAID_LONG_STAY",     False),
    # Exactly 180 min is not above the max stay: 180 min at 3 ct = 540 ct.
    ("G33", "fx_bs_ia",  36000,  46800, "shop",      False, False, False,  540, "PAID_METERED",       False),
    # 30 min + 1 s: ceil 31 > free 30; the first period (60 min, 70 ct) is charged in full, no remainder.
    ("G34", "fx_sz",     36000,  37801, "shop",      False, False, False,   70, "PAID_METERED",       False),
    # Only 71999..72000 (19:59:59-20:00) lies in the 09:00-20:00 window: 1 s is one started minute = 3 ct.
    ("G35", "fx_bs_ib",  71999,  75600, "shop",      False, False, False,    3, "PAID_METERED",       False),
    # Zero-length stay: the chargeable-time check precedes the campus day products.
    ("G36", "fx_campus", 36000,  36000, "work",      False, False, False,    0, "OUTSIDE_FEE_HOURS",  False),
    # Home with parkingFree: the home rule (H1) precedes the employer-free rule.
    ("G37", "fx_bs_ia",  36000,  39600, "home",      True,  False, False,    0, "HOME",               False),
    # Zero-length stay of a resident: the resident exemption (R1) precedes the chargeable-time check.
    ("G38", "fx_res_a",  36000,  36000, "shop",      False, True,  False,    0, "RESIDENT_FREE",      False),
)

GOLDEN_CASES: tuple[dict, ...] = tuple(dict(zip(CASE_FIELDS, row)) for row in _CASE_ROWS)


def evaluate_case(case: Mapping, tariffs_by_zone: Mapping[str, cost.ZoneTariff]) -> tuple[int, str]:
    """Cost in integer cents and outcome of one golden case, computed with the Python reference.

    A terminal case gets its departure from the terminal-stay rule (T1). Raises ``ValueError`` for an
    invalid stay (the stay check's error, ``STAY_ERROR_PATTERN``, is what an ``expected_error`` case expects)
    and for a zone id missing from ``tariffs_by_zone`` (the tariffs and the cases must come from the same
    fixture set).
    """
    zone_id = case["zone_id"]
    if zone_id not in tariffs_by_zone:
        raise ValueError(f"golden case {case['id']}: unknown zone id {zone_id!r}; known zones: "
                         f"{sorted(tariffs_by_zone)}")
    tariff = tariffs_by_zone[zone_id]
    arrival_s = case["arrival_s"]
    departure_s = cost.terminal_departure_s(arrival_s, tariff.fee_end_s) if case["terminal"] else case["departure_s"]
    return cost.parking_cost_cents(tariff, arrival_s, departure_s, purpose=case["purpose"],
                                   parking_free=case["parking_free"], resident_of_zone=case["resident_of_zone"])


def golden_case_mismatches(tariffs_by_zone: Mapping[str, cost.ZoneTariff],
                           cases: Sequence[Mapping] = GOLDEN_CASES) -> list[str]:
    """One message per case whose evaluation differs from its expectation; empty when all cases hold.

    Only the stay check's ``ValueError`` (``STAY_ERROR_PATTERN``) counts as the expected error of an invalid
    stay: another ``ValueError``, e.g. for an unknown zone id, is a mismatch, and any other exception
    propagates, because it signals a defect rather than a rejected input.
    """
    problems = []
    for case in cases:
        try:
            result = evaluate_case(case, tariffs_by_zone)
        except ValueError as error:
            if not case["expected_error"]:
                problems.append(f"{case['id']}: raised ValueError({error})")
            elif not STAY_ERROR_PATTERN.search(str(error)):
                problems.append(f"{case['id']}: expected the stay check's ValueError (departure before arrival), "
                                f"got ValueError({error})")
            continue
        if case["expected_error"]:
            problems.append(f"{case['id']}: expected a ValueError, got {result}")
        elif result != (case["expected_cents"], case["expected_outcome"]):
            problems.append(f"{case['id']}: expected {(case['expected_cents'], case['expected_outcome'])}, "
                            f"got {result}")
    return problems
