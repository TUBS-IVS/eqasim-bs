"""The golden parking-cost cases: the contract the Python reference and the Java port must both meet.

Each case is one car stay in a zone of the fixture tariff set (loaded by
``scripts/export_parking_golden_cases.py``; the set is inspired by the real tariff table but pins
arithmetic, not truth). The cases are hard-coded here ONCE and imported by ``tests/test_parking_cost.py``
and by the exporter, which checks them against ``braunschweig.parking.cost`` and writes them, together
with the fixture tariffs and the fixture garages in cents, to ``tests/fixtures/parking/parking_golden_cases.json``
for the Java ``ParkingCostCalculatorTest``.

Fields of a case (they are the keys of the JSON cases, too):

- ``id``: ``G01`` .. ``G38``, ``L01`` .. ``L08``, ``L01Z`` .. ``L08Z``, ``V01`` .. ``V23``, ``R01`` .. ``R13``,
  ``E01`` .. ``E26``.
- ``zone_id``: a zone of the fixture tariff set.
- ``arrival_s``, ``departure_s``: car arrival and activity departure in simulation seconds (may exceed
  86,400).
- ``terminal``: the destination is the last plan element. ``departure_s`` is then None and the
  implementation derives it from ``arrival_s`` with the terminal-stay rule (assumption T1,
  ``cost.terminal_departure_s``).
- ``purpose``: the MATSim activity type; ``parking_free``: the activity attribute ``parkingFree``;
  ``resident_of_zone``: whether the person lives in this zone (assumption R1); ``resident_of_district``: whether the
  activity lies in the resident parking district of the person's home (assumption R2, spec Amendment C3), false for
  every case but the R cases. The caller derives the flag by comparing the activity attribute ``parkingDistrict`` with
  the person attribute ``residentParkingDistrict`` (both set and equal); the calculator receives only the boolean, so
  a stay in another district and a stay in no district are the same input, ``false``. Whether the rule frees the stay
  also depends on the tariff of the zone: its ``resident_permits_valid`` (ASSUMPTION R2-a), a field of the fixture
  tariffs that the cases do not repeat.
- ``minimum_stay_min``: the minimum parked duration L of rule L1 (ADR-0139 decision 9) in whole minutes the stay
  is priced under. The evaluation applies T1 (terminal cases), then ``cost.minimum_stay_departure_s`` with
  ``60 * minimum_stay_min`` seconds, then ``cost.parking_cost_cents`` (spec amendment A3 of parking cost zones v2).
- ``destination_x_m``, ``destination_y_m``: the destination of the stay in EPSG:25832 metres (schema 4, spec
  Amendment E). None for every case of the families G, L, LZ, V and R: a case without a destination has no garage
  option, so it prices exactly as before schema 3. The garage options of a destination are the fixture garages within
  ``garage_max_distance_m`` of the golden document (``cost.garage_options_in_range``).
- ``garage_decay_m``: the decay length lambda in metres of the stay (ASSUMPTION G1; 0 switches the garage options off,
  spec E6). ``FIXTURE_GARAGE_DECAY_M`` in every case but E16, an illustrative value of the fixture, no calibration.
- ``expected_cents`` (integer euro cents) and ``expected_outcome`` (a ``cost.OUTCOMES`` name); both None
  when ``expected_error`` is true, i.e. the stay is invalid -- its departure lies before its arrival -- and
  the calculation must raise the ``ValueError`` of the stay check (``STAY_ERROR_PATTERN``), not any other.
- ``expected_garage_probability``: the sum of the garage probabilities of the stay (0.0 when no garage acted), a number
  hard-coded to ten decimals and compared with an absolute tolerance of ``PROBABILITY_TOLERANCE``; None for an error case.

The six families:

- G01..G38 (L = 0, the pricing before rule L1, on the v1 rows of the fixture set). G01..G26 are the cases of the
  implementation plan. G27..G38 pin what those leave open:

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

- L01..L08 (L = 15 min, the value of ADR-0139 decision 9) and their twins L01Z..L08Z (the same stays at L = 0):
  the minimum stay on the v1 rows.
- V01..V23 (L = 15 min): schema 2 of parking cost zones v2 (issue #436) on the ``fx_*_v2`` rows: the cheapest
  product the driver can use among the street, the garage and the commuter product (ASSUMPTION P1), ties going to
  the street, then the garage; the commuter product only for work and education (P2), also on campus rows (A4); a
  stay outside the STREET fee window stays free whatever the garage costs (rev-1 ruling, V07). V15..V23 pin the
  edge rules: both tie orders and the campus tie (V15..V17), the garage's own fee window with no first period for an
  unused garage and its 0 ct outcome (V18, V19), the garage without a day cap (V19, V20; spec amendment A6), the
  street unavailable above the maximum stay where its price would be the cheapest (V20) and in a resident zone (V22),
  and a terminal stay under L1 (T1 first, then L1, V21).
- R01..R13 (L = 0 except R10 and R11 at L = 15): the resident district rule R2 on the fixture rows, each with the
  result of the same stay without the rule in its comment (the existing G, L or V case). R01 frees a stay in a paid
  zone that exempts nobody and R02 is its twin with the flag false (a stay in another district or in none); R03 and
  R04 free a stay in a street zone without the exemption and in a resident zone for a person who is no resident of
  it, so the rule reads neither the zone type nor ``resident_exempt``; R05, R12 and R13 pin its scope (ASSUMPTION
  R2-a, the tariff field ``resident_permits_valid``): a campus honours no resident permit, so R05 pays the member day
  product although the flag is true, the street row ``fx_bga_v2`` of a separately operated car park (BgA) is marked
  not valid, so R12 pays the metered price, and R13 shows that the permit flag switches off R2 only: a person who
  lives in the resident zone itself is still exempt there (R1). R06 and R07 keep HOME and EMPLOYER_FREE ahead of the
  rule; R08 names a stay after the fee window RESIDENT_FREE, so the rule precedes the fee-window check; R09
  (terminal, T1) and R10 (L1, a zero-length stay) free the stay whatever interval would be priced; R11 frees a stay
  that would buy the cheapest schema-2 product, so the rule precedes the product minimum.

- E01..E26 (schema 3, spec Amendment E; the fixture garages ``tests/fixtures/parking/parking_garages_fixture.geojson``):
  the distance-weighted garage options priced as an expected cost (``cost.parking_cost_with_garages``), one case per
  rule: no garage in range (E01, the schema-2 result), one garage (E02), commuter month at a garage for work and for shop
  (E03, E04), two garages at different distances (E05), a garage at exactly 1000 m in and one at 1001 m out (E06), the
  street free (E07) and just not free (E08), the street unavailable (E09) and the zone garage family superseded (E10),
  the early rules before the mixture (E11..E14), a campus (E15), decay 0 (E16), a 0 ct garage option (E17), a tiered, a
  banded, a first-period and a grace-period garage inside the mixture (E18..E23), the zone commuter product as street
  option (E24), T1 (E25) and L1 (E26). Every expected number is hand-derived in the comment above its row and its
  unrounded expectation keeps at least ``ROUNDING_MARGIN_CENTS`` from a half cent (the generator asserts it), so that
  ``Math.exp`` of Java cannot flip a rounding.
- O01..O44 (``GOLDEN_GARAGE_OPTION_CASES``, the key ``garage_option_cases`` of the JSON): the price of ONE garage option
  without any mixture, pinning the three tariff structures (spec E10, E11, E12 and the amendments P6, P8, P9, P10) exactly:
  single window with a first period tied to a clock window, tiers (a stay across tier boundaries, a night stay, a unit that
  starts exactly at a boundary, times outside every tier, the day cap), a first period with its clock window, bands (the
  edge d = to and one second or one minute more, the jump to a total, the cap over a schedule), the grace period, the
  closed schedule repeated per started 24 h (1441 min) and the monthly product per working day.

The Java ``ParkingCostCalculatorTest`` evaluates every case of the JSON file with its ``minimum_stay_min`` and its
``resident_of_district`` against the file's own tariffs (``resident_permits_valid`` included), so a port that meets the
file meets these pins, too. That a stay outside every zone is ``NO_ZONE`` even for the home purpose, and even in the own
district, is not a golden case (every case lies in a fixture zone); ``tests/test_parking_cost.py`` pins both for the
Python reference.
"""
from __future__ import annotations

import math
import re
from typing import Mapping, Sequence

from braunschweig.parking import cost

CASE_FIELDS = ("id", "zone_id", "arrival_s", "departure_s", "purpose", "parking_free", "resident_of_zone",
               "resident_of_district", "terminal", "minimum_stay_min", "destination_x_m", "destination_y_m",
               "garage_decay_m", "expected_cents", "expected_outcome", "expected_garage_probability", "expected_error")
#: The keys of a garage option case (``GOLDEN_GARAGE_OPTION_CASES``).
OPTION_CASE_FIELDS = ("id", "garage_id", "arrival_s", "departure_s", "purpose", "expected_cents")

#: The decay length lambda of the fixture garages, metres: an illustrative value of the fixture, no calibration (the
#: calibrated value of a release is a config value, ``parking_garage_decay_m``).
FIXTURE_GARAGE_DECAY_M = 400.0
#: Absolute tolerance of the compared garage probabilities (the pinned numbers carry ten decimals).
PROBABILITY_TOLERANCE = 1e-9
#: The unrounded expectation of a pinned case keeps at least this distance (in cents) from a half cent, so that a different
#: ``exp`` implementation (Java ``Math.exp``) cannot flip the rounding.
ROUNDING_MARGIN_CENTS = 1e-6

#: The message of the stay check of ``cost.parking_cost_cents`` (and ``cost.chargeable_seconds``) for a
#: departure before the arrival: the only ValueError an ``expected_error`` case may raise. Any other ValueError
#: (an unknown zone id, a rejected tariff) means the case failed for a different reason than it pins.
STAY_ERROR_PATTERN = re.compile(r"departure_s -?\d+ is before arrival_s -?\d+")

#: L of the G cases and of the twins L01Z..L08Z: 0, the pricing before rule L1.
_WITHOUT_MINIMUM_STAY_MIN = 0
#: L of L01..L08 and V01..V14: 15 min, the minimum parked duration of ADR-0139 decision 9.
_MINIMUM_STAY_MIN = 15

# The rows of the G and V tables carry the CASE_FIELDS except minimum_stay_min, which the table fixes for all its
# rows, resident_of_district, which is false in all of them (the R rows below set it), and the garage fields of schema 4
# (no destination, the fixture decay, probability 0.0 -- None for an error case).
_GARAGE_FIELDS = ("destination_x_m", "destination_y_m", "garage_decay_m", "expected_garage_probability")
_ROW_FIELDS = tuple(field for field in CASE_FIELDS
                    if field not in ("minimum_stay_min", "resident_of_district", *_GARAGE_FIELDS))

# The comment above a row is its hand derivation. Times of day on day 0: 32400 = 09:00, 36000 = 10:00,
# 61200 = 17:00, 64800 = 18:00, 72000 = 20:00, 86400 = 24:00.
_G_CASE_ROWS = (
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

# Rule L1 (ADR-0139 decision 9): one row per twin pair, (id, zone_id, arrival_s, departure_s, purpose, result at
# L = 15 min, result at L = 0); every stay is non-terminal, without parkingFree and of a non-resident. With L = 15
# the stay is priced up to max(departure, arrival + 900 s); with L = 0 as before rule L1. Times of day:
# 36000 = 10:00, 40000 = 11:06:40, 71400 = 19:50, 72000 = 20:00 (the end of the fee window of fx_bs_ia and fx_bs_ib),
# 73000 = 20:16:40.
_MINIMUM_STAY_ROWS = (
    # A zero-length stay inside the fee window pays 15 min at 180 ct/h instead of nothing.
    ("L01", "fx_bs_ia",  36000, 36000, "shop", (45, "PAID_METERED"),        (0, "OUTSIDE_FEE_HOURS")),
    # 15 min lie within the free threshold of 30 min.
    ("L02", "fx_sz",     40000, 40000, "shop", (0, "FREE_WITHIN_LIMIT"),    (0, "OUTSIDE_FEE_HOURS")),
    # Only the 10 min before the end of the fee window are chargeable: 30 ct.
    ("L03", "fx_bs_ib",  71400, 71400, "shop", (30, "PAID_METERED"),        (0, "OUTSIDE_FEE_HOURS")),
    # Any use buys the first period (60 min, 110 ct) in full.
    ("L04", "fx_wob",    36000, 36000, "shop", (110, "PAID_METERED"),       (0, "OUTSIDE_FEE_HOURS")),
    # A 5-min stay pays 15 min: 45 ct instead of 15 ct.
    ("L05", "fx_bs_ia",  36000, 36300, "shop", (45, "PAID_METERED"),        (15, "PAID_METERED")),
    # A stay longer than the minimum is priced unchanged.
    ("L06", "fx_bs_ia",  36000, 39600, "shop", (180, "PAID_METERED"),       (180, "PAID_METERED")),
    # A zero-length work stay on campus pays the member day product.
    ("L07", "fx_campus", 36000, 36000, "work", (350, "PAID_CAMPUS_MEMBER"), (0, "OUTSIDE_FEE_HOURS")),
    # The extended stay still lies after the fee window: free.
    ("L08", "fx_bs_ib",  73000, 73000, "shop", (0, "OUTSIDE_FEE_HOURS"),    (0, "OUTSIDE_FEE_HOURS")),
)

# Schema 2 on the fx_*_v2 rows of the fixture set, priced at L = 15 min; the street product stays the v1 formula.
# fx_bs_ib_v2: street 180 ct/h in 1-min units, cap 900, 09:00-20:00; garage 120 ct per started hour after a first
#   hour of 120 ct, cap 960, all day; commuter 376 ct.
# fx_bs_ia_v2: street 180 ct/h in 1-min units, max stay 180 min WITHOUT a long-stay product, 09:00-20:00; garage
#   120 ct per started hour, cap 960, all day.
# fx_wob_v2: street first hour 110 ct, then 120 ct per started hour, cap 600, 06:00-24:00; garage 100 ct per started
#   hour, cap 500, all day.
# fx_campus_v2: member day 350 ct, guest day 900 ct, commuter 190 ct, all day.
# The edge rows of V15..V23:
# fx_garage_window_v2: street 180 ct/h in 1-min units, no cap, 09:00-20:00; garage first hour 120 ct, then 120 ct per
#   started hour, NO day cap, only 10:00-18:00; commuter 240 ct.
# fx_capped_street_v2: street 180 ct/h in 1-min units, cap 200, max stay 120 min WITHOUT a long-stay product,
#   09:00-20:00; garage 60 ct/h in 1-min units (1 ct a minute), NO day cap, all day.
# fx_campus_tie_v2: member day 350 ct, guest day 900 ct, commuter 350 ct, all day.
# fx_res_garage_v2: resident zone, rate 0 in 60-min units, max stay 120 min WITHOUT a long-stay product, all day;
#   garage 120 ct per started hour, cap 960, all day.
# Times of day: 28800 = 08:00, 37800 = 10:30, 37860 = 10:31, 38400 = 10:40, 41400 = 11:30, 43200 = 12:00,
# 46800 = 13:00, 50400 = 14:00, 64800 = 18:00, 68400 = 19:00, 71400 = 19:50, 73800 = 20:30, 79200 = 22:00.
_V_CASE_ROWS = (
    # 240 chargeable min > street max stay 180 without a long-stay product: the street is unavailable; the garage
    # charges 4 started hours x 120 ct = 480 ct.
    ("V01", "fx_bs_ia_v2",  32400, 46800, "shop",      False, False, False, 480, "PAID_GARAGE",       False),
    # Street 31 min x 3 ct = 93 ct < garage one started hour 120 ct.
    ("V02", "fx_bs_ia_v2",  36000, 37860, "shop",      False, False, False,  93, "PAID_METERED",      False),
    # Street 480 min = 1440 ct, capped at 900; garage 120 + 8 x 120 = 1080 ct, capped at 960.
    ("V03", "fx_bs_ib_v2",  28800, 61200, "shop",      False, False, False, 900, "PAID_METERED",      False),
    # Commuter 376 ct < street 900 ct < garage 960 ct.
    ("V04", "fx_bs_ib_v2",  28800, 61200, "work",      False, False, False, 376, "PAID_COMMUTER",     False),
    # The commuter product does not apply to leisure: street 900 ct.
    ("V05", "fx_bs_ib_v2",  28800, 61200, "leisure",   False, False, False, 900, "PAID_METERED",      False),
    # parkingFree precedes every product.
    ("V06", "fx_bs_ib_v2",  28800, 61200, "work",      True,  False, False,   0, "EMPLOYER_FREE",     False),
    # 20:30-22:00 lies outside the street fee window: a free street beats every garage (rev-1 ruling).
    ("V07", "fx_bs_ib_v2",  73800, 79200, "shop",      False, False, False,   0, "OUTSIDE_FEE_HOURS", False),
    # Street 110 + 9 x 120 = 1190 ct, capped at 600; garage 10 x 100 = 1000 ct, capped at 500.
    ("V08", "fx_wob_v2",    28800, 64800, "shop",      False, False, False, 500, "PAID_GARAGE",       False),
    # Street first period 110 ct; garage one started hour 100 ct.
    ("V09", "fx_wob_v2",    36000, 37800, "shop",      False, False, False, 100, "PAID_GARAGE",       False),
    # Education is a commuter purpose (P2): commuter 376 ct.
    ("V10", "fx_bs_ib_v2",  28800, 61200, "education", False, False, False, 376, "PAID_COMMUTER",     False),
    # L1 prices 36000-36900: street 15 min = 45 ct < garage one started hour 120 ct (A3).
    ("V11", "fx_bs_ia_v2",  36000, 36000, "shop",      False, False, False,  45, "PAID_METERED",      False),
    # L1 prices 36000-36900: street first period 110 ct > garage one started hour 100 ct (A3).
    ("V12", "fx_wob_v2",    36000, 36000, "shop",      False, False, False, 100, "PAID_GARAGE",       False),
    # Campus member day 350 ct > commuter 190 ct (A4).
    ("V13", "fx_campus_v2", 28800, 61200, "work",      False, False, False, 190, "PAID_COMMUTER",     False),
    # A guest pays the guest day product; the commuter product does not apply to shopping.
    ("V14", "fx_campus_v2", 28800, 61200, "shop",      False, False, False, 900, "PAID_CAMPUS_GUEST", False),
    # V15..V23 pin the edge rules of the product minimum that V01..V14 leave open (Task 2 review, rulings R-T2-a and
    # R-T2-b), each with the wrong result a port that breaks the rule would produce.
    # Tie: street 40 min x 3 ct = 120 ct = garage one started hour 120 ct; the street wins (garage first: PAID_GARAGE).
    ("V15", "fx_bs_ia_v2",  36000, 38400, "shop",      False, False, False, 120, "PAID_METERED",      False),
    # Tie: street 120 min = 360 ct; garage 10:00-12:00 inside its window 10:00-18:00 = first hour 120 + one started
    # hour 120 = 240 ct; commuter 240 ct; the garage wins (commuter first: PAID_COMMUTER).
    ("V16", "fx_garage_window_v2", 36000, 43200, "work", False, False, False, 240, "PAID_GARAGE",     False),
    # Tie on campus: member day 350 ct = commuter 350 ct; the member day product wins (commuter first: PAID_COMMUTER).
    ("V17", "fx_campus_tie_v2", 28800, 61200, "work",  False, False, False, 350, "PAID_CAMPUS_MEMBER", False),
    # 18:00-19:00: street 60 min = 180 ct, but not one second lies in the garage window 10:00-18:00, so the garage
    # charges nothing, not even its first period: 0 ct is FREE_WITHIN_LIMIT (first period charged: 120 PAID_GARAGE;
    # 0 ct named PAID_GARAGE: wrong outcome; garage metered in the street window: 120 PAID_GARAGE).
    ("V18", "fx_garage_window_v2", 64800, 68400, "shop", False, False, False,   0, "FREE_WITHIN_LIMIT", False),
    # 09:00-19:00: street 600 min = 1800 ct (no street cap); the garage window clips the garage to 10:00-18:00 = 8 h:
    # 120 + 7 x 120 = 960 ct, no garage cap (amendment A6) (garage metered over the whole stay: 1200; an empty cap
    # read as 0: 0 ct).
    ("V19", "fx_garage_window_v2", 32400, 68400, "shop", False, False, False, 960, "PAID_GARAGE",     False),
    # 240 chargeable min > max stay 120 without a long-stay product: the street is unavailable, although its price
    # would be the CHEAPEST (240 x 3 ct = 720, capped at 200 ct); the uncapped garage charges 240 min x 1 ct = 240 ct
    # (street kept available: 200 PAID_METERED).
    ("V20", "fx_capped_street_v2", 36000, 50400, "shop", False, False, False, 240, "PAID_GARAGE",     False),
    # Terminal at 19:50 under L1 (T1 first, then L1, A3): T1 takes the end of the STREET window, 72000; L1 extends to
    # 72300. Street: 10 chargeable min = 30 ct; garage (all day): 15 min x 1 ct = 15 ct (without L1: 10 ct; T1 from
    # the garage window, 86400: garage 250 ct, street 30 PAID_METERED).
    ("V21", "fx_capped_street_v2", 71400,  None, "shop", False, False, True,   15, "PAID_GARAGE",     False),
    # Resident zone without a long-stay product: a non-resident's 180 min > max stay 120 lose the street (whose disc
    # price would be 0 ct); the garage charges 3 started hours = 360 ct (street kept available: 0 FREE_WITHIN_LIMIT).
    ("V22", "fx_res_garage_v2", 36000, 46800, "shop",  False, False, False, 360, "PAID_GARAGE",       False),
    # 90 min <= max stay 120: disc parking at rate 0 is free and beats the garage (2 started hours = 240 ct).
    ("V23", "fx_res_garage_v2", 36000, 41400, "shop",  False, False, False,   0, "FREE_WITHIN_LIMIT", False),
)


# Rule R2 (spec Amendment C3): one row per case, (id, zone_id, arrival_s, departure_s, purpose, parking_free,
# resident_of_zone, resident_of_district, terminal, minimum_stay_min, expected_cents, expected_outcome). Times of day on
# day 0: 28800 = 08:00, 36000 = 10:00, 61200 = 17:00, 70200 = 19:30, 72000 = 20:00 (the end of the fee window of
# fx_bs_ia and fx_bs_ib), 73800 = 20:30, 79200 = 22:00. The comment above a row is its hand derivation, with the result
# of the same stay without the rule.
_R_CASE_ROWS = (
    # G01 stay, 31 chargeable min x 3 ct = 93 ct PAID_METERED without the rule; in the own district it is free.
    ("R01", "fx_bs_ia",   36000, 37860, "shop",    False, False, True,  False,  0,  0, "RESIDENT_FREE"),
    # The twin of R01 for a stay in another district or in no district: the flag is false, so G01's 93 ct stand.
    ("R02", "fx_bs_ia",   36000, 37860, "shop",    False, False, False, False,  0, 93, "PAID_METERED"),
    # fx_bs_ib exempts nobody (resident_exempt false): G03 pays 480 min = 1440 ct capped at 900 ct; R2 does not need
    # the exemption.
    ("R03", "fx_bs_ib",   28800, 61200, "work",    False, False, True,  False,  0,  0, "RESIDENT_FREE"),
    # A person who is no resident of the resident zone fx_res_a (R1 flag false) but lives in the district that holds
    # the stay: G18 pays the long-stay product 900 ct (540 min > max stay 120), R2 frees it.
    ("R04", "fx_res_a",   28800, 61200, "work",    False, False, True,  False,  0,  0, "RESIDENT_FREE"),
    # A campus honours no resident permit (R2-a, the default of resident_permits_valid there): the stay pays the member
    # day product 350 ct of G20 although the flag is true.
    ("R05", "fx_campus",  28800, 61200, "work",    False, False, True,  False,  0, 350, "PAID_CAMPUS_MEMBER"),
    # Home is free everywhere (H1) and comes first: HOME, not RESIDENT_FREE.
    ("R06", "fx_bs_ia",   36000, 39600, "home",    False, False, True,  False,  0,  0, "HOME"),
    # The employer's free parking comes before the district rule: EMPLOYER_FREE, not RESIDENT_FREE.
    ("R07", "fx_bs_ib",   28800, 61200, "work",    True,  False, True,  False,  0,  0, "EMPLOYER_FREE"),
    # 20:30-22:00 lies after the fee window 09:00-20:00 (G06: OUTSIDE_FEE_HOURS); the district rule precedes the
    # fee-window check, so the stay is RESIDENT_FREE.
    ("R08", "fx_bs_ia",   73800, 79200, "leisure", False, False, True,  False,  0,  0, "RESIDENT_FREE"),
    # Terminal stay (T1): departure max(70200, 72000) = 72000, 30 min x 3 ct = 90 ct without the rule (G07).
    ("R09", "fx_bs_ia",   70200,  None, "shop",    False, False, True,  True,   0,  0, "RESIDENT_FREE"),
    # L = 15: the zero-length stay would be priced for 15 min x 3 ct = 45 ct (L01); in the own district it is free.
    ("R10", "fx_bs_ia",   36000, 36000, "shop",    False, False, True,  False, 15,  0, "RESIDENT_FREE"),
    # L = 15 on a schema-2 row: the cheapest product would be the commuter product 376 ct (V04); R2 precedes the
    # product minimum.
    ("R11", "fx_bs_ib_v2", 28800, 61200, "work",    False, False, True,  False, 15,  0, "RESIDENT_FREE"),
    # fx_bga_v2 is a street row of a separately operated car park (BgA) with resident_permits_valid false (R2-a): the
    # stay of R01, 31 chargeable min x 3 ct = 93 ct, is priced although the flag is true.
    ("R12", "fx_bga_v2",   36000, 37860, "shop",    False, False, True,  False,  0, 93, "PAID_METERED"),
    # fx_res_nopermit_v2 is a resident zone that exempts its residents (R1) but does not honour district permits: a
    # person who lives in the zone and in the district is exempt by R1 (without R1 the 540 min > max stay 120 would pay
    # the long-stay product 900 ct, as G18). The permit flag switches off R2 only.
    ("R13", "fx_res_nopermit_v2", 28800, 61200, "work", False, True, True, False,  0,  0, "RESIDENT_FREE"),
)


def _without_garages(values: dict) -> dict:
    """The garage fields of a case that has no destination: no coordinates, the fixture decay (without a destination no garage
    can act) and the garage probability 0.0 (None for an error case, which prices nothing)."""
    return dict(values, destination_x_m=None, destination_y_m=None, garage_decay_m=FIXTURE_GARAGE_DECAY_M,
                expected_garage_probability=None if values["expected_error"] else 0.0)


def _case(row: Sequence, minimum_stay_min: int) -> dict:
    """One golden case with its keys in ``CASE_FIELDS`` order from a ``_ROW_FIELDS`` row and its L; the district flag is
    false (only the R cases set it) and there is no destination."""
    values = _without_garages(dict(zip(_ROW_FIELDS, row), minimum_stay_min=minimum_stay_min, resident_of_district=False))
    return {field: values[field] for field in CASE_FIELDS}


def _district_case(row: Sequence) -> dict:
    """One R case (``_R_CASE_ROWS``): the case id, the stay, the flags, its own L and the expectation."""
    (case_id, zone_id, arrival_s, departure_s, purpose, parking_free, resident_of_zone, resident_of_district, terminal,
     minimum_stay_min, expected_cents, expected_outcome) = row
    values = _without_garages({"id": case_id, "zone_id": zone_id, "arrival_s": arrival_s, "departure_s": departure_s,
                               "purpose": purpose, "parking_free": parking_free, "resident_of_zone": resident_of_zone,
                               "resident_of_district": resident_of_district, "terminal": terminal,
                               "minimum_stay_min": minimum_stay_min, "expected_cents": expected_cents,
                               "expected_outcome": expected_outcome, "expected_error": False})
    return {field: values[field] for field in CASE_FIELDS}


# Spec Amendment E: one row per case, (id, zone_id, arrival_s, departure_s, purpose, parking_free, resident_of_zone,
# resident_of_district, terminal, minimum_stay_min, destination_x_m, destination_y_m, garage_decay_m, expected_cents,
# expected_outcome, expected_garage_probability). Zones (fixture tariffs): fx_bs_ib street 180 ct/h in 1-min units, cap 900,
# 09:00-20:00; fx_sz free up to 30 min, first period 60 min 70 ct, 10:00-18:00; fx_bs_ia_v2 street max stay 180 min without a
# long-stay product (and the zone garage family 120 ct/h, cap 960, superseded by the garage options, E8); fx_bs_ib_v2 with the
# zone commuter product 376 ct. Destinations (EPSG:25832 metres) and fixture garages: D0 (570000, 5750000) has no garage;
# D1 (580000, 5750000): fx_g01_core 300 m east (200 ct per started hour, all day, monthly product 63.00 EUR = 300 ct per
# working day); D2 (590000, 5750000): fx_g02_a 300 m east (200 ct/h) and fx_g02_b 700 m north (100 ct/h); D3 (600000, 5750000):
# fx_g03_edge at exactly 1000 m (dx 600, dy 800) and fx_g03_far at 1001 m; D4 (610000, 5750000): fx_g04_core at 0 m; D6
# (620000, 5750000): fx_g06_window at 0 m (fee window 10:00-12:00, 100 ct/h); D7 (630000, 5750000): fx_g07_tiers at 0 m;
# D8 (640000, 5750000): fx_g08_bands at 0 m; D9 (580000, 5760000): fx_g09_fp at 0 m; D10 (590000, 5760000): fx_g10_grace at 0 m.
# lambda = 400 m: w(300 m) = exp(-0.75) = 0.4723665527, w(700 m) = exp(-1.75) = 0.1737739435, w(1000 m) = exp(-2.5) =
# 0.0820849986, w(0 m) = 1 (P = 0.5 each with one garage at the destination). expected = sum(w_i c_i) / sum(w_i) over the
# options (the street w 1, c = street product; a garage c = its option price), the result rounded half up once. Times of day
# on day 0: 19800 = 05:30, 28800 = 08:00, 34200 = 09:30, 36000 = 10:00, 39600 = 11:00, 43200 = 12:00, 46800 = 13:00,
# 50400 = 14:00, 54000 = 15:00, 57600 = 16:00, 61200 = 17:00, 70200 = 19:30, 72000 = 20:00, 73800 = 20:30, 79200 = 22:00.
_D0, _D1, _D2, _D3, _D4 = ((570000.0, 5750000.0), (580000.0, 5750000.0), (590000.0, 5750000.0), (600000.0, 5750000.0),
                           (610000.0, 5750000.0))
_D6, _D7, _D8, _D9, _D10 = ((620000.0, 5750000.0), (630000.0, 5750000.0), (640000.0, 5750000.0), (580000.0, 5760000.0),
                            (590000.0, 5760000.0))
_E_CASE_ROWS = (
    # D0 has no garage within 1000 m: the schema-2 price of G-like stays, 60 min x 3 ct = 180 ct, probability 0.
    ("E01", "fx_bs_ib", 36000, 39600, "shop", False, False, False, False, 0, *_D0, 400.0, 180, "PAID_METERED", 0.0),
    # street 180 ct, fx_g01_core 1 started hour 200 ct at 300 m: (180 + 0.4723665527 x 200) / 1.4723665527 = 186.416 -> 186;
    # P_garage = 0.4723665527 / 1.4723665527 = 0.3208213008.
    ("E02", "fx_bs_ib", 36000, 39600, "shop", False, False, False, False, 0, *_D1, 400.0, 186, "PAID_EXPECTED",
     0.3208213008),
    # work 08:00-16:00: street 420 chargeable min x 3 = 1260 -> cap 900; garage 8 started hours = 1600, its monthly product
    # 6300 / 21 = 300 ct is cheaper: option 300 ct. (900 + 0.4723665527 x 300) / 1.4723665527 = 707.507 -> 708.
    ("E03", "fx_bs_ib", 28800, 57600, "work", False, False, False, False, 0, *_D1, 400.0, 708, "PAID_EXPECTED",
     0.3208213008),
    # the same stay for shopping has no monthly product: garage 1600 ct: (900 + 0.4723665527 x 1600) / 1.4723665527 =
    # 1124.575 -> 1125.
    ("E04", "fx_bs_ib", 28800, 57600, "shop", False, False, False, False, 0, *_D1, 400.0, 1125, "PAID_EXPECTED",
     0.3208213008),
    # two garages: (180 + 0.4723665527 x 200 + 0.1737739435 x 100) / 1.6461404962 = 177.294 -> 177; P_garages =
    # 0.6461404962 / 1.6461404962 = 0.3925184379.
    ("E05", "fx_bs_ib", 36000, 39600, "shop", False, False, False, False, 0, *_D2, 400.0, 177, "PAID_EXPECTED",
     0.3925184379),
    # fx_g03_edge at exactly 1000 m is an option (w 0.0820849986, 200 ct), fx_g03_far at 1001 m is not:
    # (180 + 0.0820849986 x 200) / 1.0820849986 = 181.517 -> 182; P_garage = 0.0820849986 / 1.0820849986 = 0.0758581800.
    ("E06", "fx_bs_ib", 36000, 39600, "shop", False, False, False, False, 0, *_D3, 400.0, 182, "PAID_EXPECTED",
     0.0758581800),
    # E4: 25 min <= the free threshold of 30 min: the street costs 0, so the stay pays 0 although a garage lies at 0 m.
    ("E07", "fx_sz", 39600, 41100, "shop", False, False, False, False, 0, *_D4, 400.0, 0, "FREE_WITHIN_LIMIT", 0.0),
    # 31 min: street first period 70 ct (G10), garage 1 started hour 200 ct, w = 1: (70 + 200) / 2 = 135 exactly.
    ("E08", "fx_sz", 39600, 41460, "shop", False, False, False, False, 0, *_D4, 400.0, 135, "PAID_EXPECTED", 0.5),
    # E4: 240 chargeable min > max stay 180 without a long-stay product: no street option, the weights renormalise over the
    # garages: fx_g02_a 4 x 200 = 800 ct, fx_g02_b 4 x 100 = 400 ct: (0.4723665527 x 800 + 0.1737739435 x 400) /
    # 0.6461404962 = 692.423 -> 692; P_garages = 1 (the zone garage family 480 ct, PAID_GARAGE without options, is superseded).
    ("E09", "fx_bs_ia_v2", 32400, 46800, "shop", False, False, False, False, 0, *_D2, 400.0, 692, "PAID_EXPECTED", 1.0),
    # 60 min: street 180 ct (the zone garage family would give 120 ct without garage options; with them it is superseded):
    # the mixture of E05, 177 ct.
    ("E10", "fx_bs_ia_v2", 36000, 39600, "shop", False, False, False, False, 0, *_D2, 400.0, 177, "PAID_EXPECTED",
     0.3925184379),
    # parkingFree precedes the garages (V06-like): 0, EMPLOYER_FREE, probability 0.
    ("E11", "fx_bs_ib", 36000, 39600, "work", True, False, False, False, 0, *_D1, 400.0, 0, "EMPLOYER_FREE", 0.0),
    # R2 precedes the garages: 0, RESIDENT_FREE.
    ("E12", "fx_bs_ib", 36000, 39600, "shop", False, False, True, False, 0, *_D1, 400.0, 0, "RESIDENT_FREE", 0.0),
    # home is free everywhere (H1): 0, HOME.
    ("E13", "fx_bs_ib", 36000, 39600, "home", False, False, False, False, 0, *_D1, 400.0, 0, "HOME", 0.0),
    # 20:30-22:00 lies outside the street fee window: a free street beats every garage, 0, OUTSIDE_FEE_HOURS.
    ("E14", "fx_bs_ib", 73800, 79200, "shop", False, False, False, False, 0, *_D1, 400.0, 0, "OUTSIDE_FEE_HOURS", 0.0),
    # a campus has no garage option: work pays the member day product 350 ct (G20), probability 0.
    ("E15", "fx_campus", 28800, 61200, "work", False, False, False, False, 0, *_D1, 400.0, 350, "PAID_CAMPUS_MEMBER", 0.0),
    # decay 0 switches the garage options off: the street price of E02, 180 ct PAID_METERED, probability 0.
    ("E16", "fx_bs_ib", 36000, 39600, "shop", False, False, False, False, 0, *_D1, 0.0, 180, "PAID_METERED", 0.0),
    # fx_g06_window charges only 10:00-12:00 and the stay lies 14:00-15:00: its option costs 0 ct and keeps its weight 1:
    # (180 + 0) / 2 = 90, P_garage 0.5.
    ("E17", "fx_bs_ib", 50400, 54000, "shop", False, False, False, False, 0, *_D6, 400.0, 90, "PAID_EXPECTED", 0.5),
    # tiered garage (Rosenwall tiers), stay 09:30-10:44: street 74 min x 3 = 222 ct; units start 09:30 (30 ct), 10:00 (60 ct),
    # 10:30 (60 ct) = 150 ct: (222 + 150) / 2 = 186.
    ("E18", "fx_bs_ib", 34200, 38640, "shop", False, False, False, False, 0, *_D7, 400.0, 186, "PAID_EXPECTED", 0.5),
    # banded garage (Designer Outlets bands), stay 10:00-12:02 = 122 min: street 366 ct; band 120-240: 100 + 50 x 1 started
    # hour = 150 ct: (366 + 150) / 2 = 258.
    ("E19", "fx_bs_ib", 36000, 43320, "shop", False, False, False, False, 0, *_D8, 400.0, 258, "PAID_EXPECTED", 0.5),
    # first-period garage (tiers 00-06 0.50/h, 06-24 1.20/h, first period 60 min 1.10 EUR for an arrival 06:00-24:00, cap
    # 6.00 EUR), arrival 10:00 inside the window, stay 10:00-13:00: street 180 min x 3 = 540 ct; garage 110 + units at 11:00 and
    # 12:00 (120 ct each) = 350 ct: (540 + 350) / 2 = 445.
    ("E20", "fx_bs_ib", 36000, 46800, "shop", False, False, False, False, 0, *_D9, 400.0, 445, "PAID_EXPECTED", 0.5),
    # the same garage, arrival 05:30 outside the window, stay 05:30-12:00: street (09:00-12:00) 180 min x 3 = 540 ct; garage
    # no first period: unit 05:30 (50 ct) + 6 units 06:30..11:30 (120 ct) = 770 ct, capped at 600: (540 + 600) / 2 = 570.
    ("E21", "fx_bs_ib", 19800, 43200, "shop", False, False, False, False, 0, *_D9, 400.0, 570, "PAID_EXPECTED", 0.5),
    # grace period garage (0-15 free, 15-60 total 1.50 ...): a stay of 14 min: street 42 ct, garage 0 ct: (42 + 0) / 2 = 21.
    ("E22", "fx_bs_ib", 36000, 36840, "shop", False, False, False, False, 0, *_D10, 400.0, 21, "PAID_EXPECTED", 0.5),
    # 16 min: street 48 ct, garage 150 ct (the stay is billed from the arrival, P10): (48 + 150) / 2 = 99.
    ("E23", "fx_bs_ib", 36000, 36960, "shop", False, False, False, False, 0, *_D10, 400.0, 99, "PAID_EXPECTED", 0.5),
    # zone commuter product 376 ct is a street option product: work 08:00-17:00 street min(900 (cap), 376) = 376, garage
    # min(9 x 200 = 1800, 300) = 300 ct at 300 m: (376 + 0.4723665527 x 300) / 1.4723665527 = 351.618 -> 352.
    ("E24", "fx_bs_ib_v2", 28800, 61200, "work", False, False, False, False, 0, *_D1, 400.0, 352, "PAID_EXPECTED",
     0.3208213008),
    # terminal stay (T1): departure max(70200, 72000) = 72000, 30 min: street 90 ct, garage 1 started hour 200 ct at 300 m:
    # (90 + 0.4723665527 x 200) / 1.4723665527 = 125.290 -> 125.
    ("E25", "fx_bs_ib", 70200, None, "shop", False, False, False, True, 0, *_D1, 400.0, 125, "PAID_EXPECTED",
     0.3208213008),
    # L = 15: the zero-length stay is priced for 15 min: street 45 ct, garage 200 ct at 300 m:
    # (45 + 0.4723665527 x 200) / 1.4723665527 = 94.727 -> 95.
    ("E26", "fx_bs_ib", 36000, 36000, "shop", False, False, False, False, 15, *_D1, 400.0, 95, "PAID_EXPECTED",
     0.3208213008),
)

# Garage option cases (the price of ONE garage option, no mixture): (id, garage_id, arrival_s, departure_s, purpose,
# expected_cents). 600 = 10 min; times of day: 19800 = 05:30, 21600 = 06:00, 28800 = 08:00, 34200 = 09:30, 36000 = 10:00,
# 37800 = 10:30, 38700 = 10:45, 39600 = 11:00, 43200 = 12:00, 46800 = 13:00, 54000 = 15:00, 59400 = 16:30, 63000 = 17:30,
# 64800 = 18:00, 66600 = 18:30, 72000 = 20:00, 79200 = 22:00, 86400 = 24:00 (day 1 00:00).
_O_CASE_ROWS = (
    # fx_g01_core (200 ct per started hour, all day, monthly product 6300 ct): 90 min = 2 started hours = 400 ct.
    ("O01", "fx_g01_core", 36000, 41400, "shop", 400),
    # work 08:00-16:00 = 8 started hours = 1600 ct; the monthly product 6300 / 21 = 300 ct per working day is cheaper.
    ("O02", "fx_g01_core", 28800, 57600, "work", 300),
    ("O03", "fx_g01_core", 28800, 57600, "shop", 1600),
    # education 10:00-11:00 = 200 ct: the metered price is below the monthly 300 ct, so the minimum stays 200.
    ("O04", "fx_g01_core", 36000, 39600, "education", 200),
    # fx_g06_window (100 ct per started hour, fee window 10:00-12:00 only): 09:00-13:00 has 120 chargeable min = 200 ct.
    ("O05", "fx_g06_window", 32400, 46800, "shop", 200),
    # 14:00-15:00 lies outside the window: 0 ct (not even a first period).
    ("O06", "fx_g06_window", 50400, 54000, "shop", 0),
    # fx_g11_core_fp (200 ct/h in 60-min units, first period 60 min 100 ct for an arrival 06:00-10:00): arrival 09:00 inside:
    # 100 + (180 - 60 = 120 min = 2 started hours) 400 = 500 ct.
    ("O07", "fx_g11_core_fp", 32400, 43200, "shop", 500),
    # arrival 11:00 outside the window: 3 started hours = 600 ct.
    ("O08", "fx_g11_core_fp", 39600, 50400, "shop", 600),
    # the window start is inclusive: arrival 06:00, 60 min: the first period 100 ct covers the whole stay.
    ("O09", "fx_g11_core_fp", 21600, 25200, "shop", 100),
    # the window end is exclusive: arrival 10:00 is outside: 1 started hour = 200 ct.
    ("O10", "fx_g11_core_fp", 36000, 39600, "shop", 200),
    # fx_g07_tiers (08-10 0.30, 10-18 0.60, 18-23 0.30, 23-08 0.10 EUR per started 30 min): 09:30-10:45: units 09:30 (30 ct),
    # 10:00 (60 ct), 10:30 (60 ct) = 150 ct.
    ("O11", "fx_g07_tiers", 34200, 38700, "shop", 150),
    # 17:30-18:30: unit 17:30 (60 ct), unit 18:00 (30 ct: a unit that starts exactly at 18:00 takes the 18-23 tier) = 90 ct.
    ("O12", "fx_g07_tiers", 63000, 66600, "shop", 90),
    # 17:30-18:00: the unit that would start at 18:00 is not started (the departure is at the boundary): 60 ct.
    ("O13", "fx_g07_tiers", 63000, 64800, "shop", 60),
    # night 22:00-07:00 (next day): units 22:00 and 22:30 (30 ct each, 18-23 tier) = 60 ct, then the 16 units 23:00 .. 06:30 of
    # the tier 23-08 that crosses midnight (10 ct each) = 160 ct: 220 ct.
    ("O14", "fx_g07_tiers", 79200, 111600, "shop", 220),
    # 12:00-12:01: one started unit at 60 ct.
    ("O15", "fx_g07_tiers", 43200, 43260, "shop", 60),
    # a stay of no length costs 0.
    ("O16", "fx_g07_tiers", 43200, 43200, "shop", 0),
    # fx_g12_tiers_gap (one tier 08:00-10:00 0.30 per 30 min, day cap 1.00 EUR, free outside the tier): 12:00-13:00 starts
    # its units outside every tier: 0 ct.
    ("O17", "fx_g12_tiers_gap", 43200, 46800, "shop", 0),
    # 08:00-10:00: 4 units x 30 ct = 120 ct, capped at 100 ct.
    ("O18", "fx_g12_tiers_gap", 28800, 36000, "shop", 100),
    # fx_g09_fp (tiers 00-06 0.50 and 06-24 1.20 EUR per hour, first period 60 min 1.10 EUR for an arrival 06:00-24:00, cap
    # 6.00 EUR): arrival 10:00 inside, 3 h: 110 + units 11:00, 12:00 (120 ct each) = 350 ct.
    ("O19", "fx_g09_fp", 36000, 46800, "shop", 350),
    # arrival 05:30 outside the window, 3 h: no first period; units 05:30 (50 ct), 06:30, 07:30 (120 ct each) = 290 ct.
    ("O20", "fx_g09_fp", 19800, 30600, "shop", 290),
    # arrival exactly 06:00 (the window start is inclusive), 30 min: the first period covers it: 110 ct.
    ("O21", "fx_g09_fp", 21600, 23400, "shop", 110),
    # arrival 24:00 = 00:00 of day 1 is outside the window (the end is exclusive): units 00:00 and 01:00 (50 ct) = 100 ct.
    ("O22", "fx_g09_fp", 86400, 93600, "shop", 100),
    # arrival 23:30 inside the window: first period 110 ct to 00:30 (day 1); units 00:30 and 01:30 (50 ct each), the departure
    # is 02:00: 210 ct.
    ("O23", "fx_g09_fp", 84600, 93600, "shop", 210),
    # 06:00-20:00: 110 + 13 units (07:00 .. 19:00) x 120 = 1670 ct, capped at 600 ct.
    ("O24", "fx_g09_fp", 21600, 72000, "shop", 600),
    # fx_g08_bands (0-20 free; 20-120 total 1.00; 120-240 0.50/60; 240-420 1.50/60; 420- 5.00/60; cap 15.00 EUR): exactly 20 min
    # is still free, one second more is the total band, 120 min is still 1.00, 121 min the first 0.50 unit:
    ("O25", "fx_g08_bands", 36000, 37200, "shop", 0),
    ("O26", "fx_g08_bands", 36000, 37201, "shop", 100),
    ("O27", "fx_g08_bands", 36000, 43200, "shop", 100),
    ("O28", "fx_g08_bands", 36000, 43260, "shop", 150),
    # 240 min: 100 + 2 x 50 = 200 ct; 241 min: 200 + 150 = 350 ct; 420 min: 200 + 3 x 150 = 650 ct; 421 min: 650 + 500 = 1150 ct.
    ("O29", "fx_g08_bands", 36000, 50400, "shop", 200),
    ("O30", "fx_g08_bands", 36000, 50460, "shop", 350),
    ("O31", "fx_g08_bands", 36000, 61200, "shop", 650),
    ("O32", "fx_g08_bands", 36000, 61260, "shop", 1150),
    # 481 min: 650 + 2 x 500 = 1650 ct, capped at 1500 ct (the cap over a schedule).
    ("O33", "fx_g08_bands", 36000, 64860, "shop", 1500),
    # fx_g10_grace (0-15 free; 15-60 total 1.50; 60- 1.50/60): exactly 15 min costs 0, one second more 1.50 EUR (billed from
    # the arrival, P10), 60 min 150 ct, 61 min 150 + 150 = 300 ct.
    ("O34", "fx_g10_grace", 36000, 36900, "shop", 0),
    ("O35", "fx_g10_grace", 36000, 36901, "shop", 150),
    ("O36", "fx_g10_grace", 36000, 36960, "shop", 150),
    ("O37", "fx_g10_grace", 36000, 39600, "shop", 150),
    ("O38", "fx_g10_grace", 36000, 39660, "shop", 300),
    # fx_g13_closed (0-30 total 0.20; 30-60 total 0.80; 60-300 0.40/30; 300-1440 total 4.00): 1440 min is inside: 400 ct;
    # 1441 min (P9): one full period 400 ct + 1 min (20 ct) = 420 ct; 2880 min = 1 full period + the remainder 1440 min = 800 ct;
    # 2881 min = 2 full periods + 1 min = 820 ct.
    ("O39", "fx_g13_closed", 36000, 122400, "shop", 400),
    ("O40", "fx_g13_closed", 36000, 122460, "shop", 420),
    ("O41", "fx_g13_closed", 36000, 208800, "shop", 800),
    ("O42", "fx_g13_closed", 36000, 208860, "shop", 820),
    # fx_g14_closed_capped (the same schedule, day cap 3.00 EUR): 1441 min: every period capped: 300 + 20 = 320 ct; 1440 min:
    # min(400, 300) = 300 ct.
    ("O43", "fx_g14_closed_capped", 36000, 122460, "shop", 320),
    ("O44", "fx_g14_closed_capped", 36000, 122400, "shop", 300),
)


_E_ROW_FIELDS = ("id", "zone_id", "arrival_s", "departure_s", "purpose", "parking_free", "resident_of_zone",
                 "resident_of_district", "terminal", "minimum_stay_min", "destination_x_m", "destination_y_m",
                 "garage_decay_m", "expected_cents", "expected_outcome", "expected_garage_probability")


def _garage_case(row: Sequence) -> dict:
    """One E case (``_E_CASE_ROWS``): the stay, its destination, the decay and the expectation."""
    values = dict(zip(_E_ROW_FIELDS, row), expected_error=False)
    return {field: values[field] for field in CASE_FIELDS}


def _minimum_stay_case(row: Sequence, *, twin: bool) -> dict:
    """L0n at L = 15 min, or its twin L0nZ (``twin``) with the same stay at L = 0."""
    case_id, zone_id, arrival_s, departure_s, purpose, with_minimum, without_minimum = row
    cents, outcome = without_minimum if twin else with_minimum
    return _case((case_id + "Z" if twin else case_id, zone_id, arrival_s, departure_s, purpose, False, False, False,
                  cents, outcome, False), _WITHOUT_MINIMUM_STAY_MIN if twin else _MINIMUM_STAY_MIN)


GOLDEN_CASES: tuple[dict, ...] = (
    tuple(_case(row, _WITHOUT_MINIMUM_STAY_MIN) for row in _G_CASE_ROWS)
    + tuple(_minimum_stay_case(row, twin=False) for row in _MINIMUM_STAY_ROWS)
    + tuple(_minimum_stay_case(row, twin=True) for row in _MINIMUM_STAY_ROWS)
    + tuple(_case(row, _MINIMUM_STAY_MIN) for row in _V_CASE_ROWS)
    + tuple(_district_case(row) for row in _R_CASE_ROWS)
    + tuple(_garage_case(row) for row in _E_CASE_ROWS)
)

GOLDEN_GARAGE_OPTION_CASES: tuple[dict, ...] = tuple(
    {field: value for field, value in zip(OPTION_CASE_FIELDS, row)} for row in _O_CASE_ROWS)


def evaluate_case_detail(case: Mapping, tariffs_by_zone: Mapping[str, cost.ZoneTariff], garages: Sequence = (),
                         garage_max_distance_m: float = cost.GARAGE_MAX_DISTANCE_M) -> cost.GarageStayPrice:
    """Price, outcome, garage probability and unrounded expectation of one golden case, computed with the Python reference.

    A terminal case gets its departure from the terminal-stay rule (T1, from the STREET fee window); then the
    minimum stay of the case (``minimum_stay_min``, rule L1) extends the priced stay; then every product prices that
    same interval (spec amendment A3), unless the stay lies in the person's own resident district and the zone
    honours resident permits (R2: the flag ``resident_of_district`` of the case and the ``resident_permits_valid`` of the
    tariff free it before any interval is priced). The garage options (spec Amendment E) are those of ``garages``
    (``cost.GarageTariff`` objects) within ``garage_max_distance_m`` of the destination of the case; a case without a
    destination has none. Raises ``ValueError`` for an
    invalid stay (the stay check's error, ``STAY_ERROR_PATTERN``, is what an ``expected_error`` case expects) and for
    a zone id missing from ``tariffs_by_zone`` (the tariffs and the cases must come from the same fixture set).
    """
    zone_id = case["zone_id"]
    if zone_id not in tariffs_by_zone:
        raise ValueError(f"golden case {case['id']}: unknown zone id {zone_id!r}; known zones: "
                         f"{sorted(tariffs_by_zone)}")
    tariff = tariffs_by_zone[zone_id]
    arrival_s = case["arrival_s"]
    departure_s = cost.terminal_departure_s(arrival_s, tariff.fee_end_s) if case["terminal"] else case["departure_s"]
    priced_departure_s = cost.minimum_stay_departure_s(arrival_s, departure_s,
                                                       case["minimum_stay_min"] * cost.SECONDS_PER_MINUTE)
    options = ([] if case["destination_x_m"] is None else
               cost.garage_options_in_range(garages, case["destination_x_m"], case["destination_y_m"],
                                            garage_max_distance_m))
    return cost.parking_cost_with_garages_detail(
        tariff, arrival_s, priced_departure_s, purpose=case["purpose"], parking_free=case["parking_free"],
        resident_of_zone=case["resident_of_zone"], resident_of_district=case["resident_of_district"],
        garage_options=options, decay_m=case["garage_decay_m"])


def evaluate_case(case: Mapping, tariffs_by_zone: Mapping[str, cost.ZoneTariff], garages: Sequence = (),
                  garage_max_distance_m: float = cost.GARAGE_MAX_DISTANCE_M) -> tuple[int, str]:
    """Cost in integer cents and outcome of one golden case (``evaluate_case_detail`` without the probability)."""
    detail = evaluate_case_detail(case, tariffs_by_zone, garages, garage_max_distance_m)
    return detail.cents, detail.outcome


def golden_case_mismatches(tariffs_by_zone: Mapping[str, cost.ZoneTariff], cases: Sequence[Mapping] = GOLDEN_CASES,
                           garages: Sequence = (), garage_max_distance_m: float = cost.GARAGE_MAX_DISTANCE_M) -> list[str]:
    """One message per case whose evaluation differs from its expectation; empty when all cases hold.

    Compared: cents, outcome and the garage probability (within ``PROBABILITY_TOLERANCE``). A case whose expectation was
    rounded must keep its unrounded value ``ROUNDING_MARGIN_CENTS`` away from a half cent, so Java's ``Math.exp`` cannot
    flip a rounding; a case that does not is a mismatch. Only the stay check's ``ValueError`` (``STAY_ERROR_PATTERN``)
    counts as the expected error of an invalid
    stay: another ``ValueError``, e.g. for an unknown zone id, is a mismatch, and any other exception
    propagates, because it signals a defect rather than a rejected input.
    """
    problems = []
    for case in cases:
        try:
            detail = evaluate_case_detail(case, tariffs_by_zone, garages, garage_max_distance_m)
        except ValueError as error:
            if not case["expected_error"]:
                problems.append(f"{case['id']}: raised ValueError({error})")
            elif not STAY_ERROR_PATTERN.search(str(error)):
                problems.append(f"{case['id']}: expected the stay check's ValueError (departure before arrival), "
                                f"got ValueError({error})")
            continue
        if case["expected_error"]:
            problems.append(f"{case['id']}: expected a ValueError, got {(detail.cents, detail.outcome)}")
            continue
        if (detail.cents, detail.outcome) != (case["expected_cents"], case["expected_outcome"]):
            problems.append(f"{case['id']}: expected {(case['expected_cents'], case['expected_outcome'])}, "
                            f"got {(detail.cents, detail.outcome)}")
        if abs(detail.garage_probability - case["expected_garage_probability"]) > PROBABILITY_TOLERANCE:
            problems.append(f"{case['id']}: expected garage probability {case['expected_garage_probability']}, "
                            f"got {detail.garage_probability}")
        if detail.expected_cents is not None:
            fraction = detail.expected_cents - math.floor(detail.expected_cents)
            if abs(fraction - 0.5) < ROUNDING_MARGIN_CENTS:
                problems.append(f"{case['id']}: the unrounded expectation {detail.expected_cents!r} lies within "
                                f"{ROUNDING_MARGIN_CENTS} of a half cent; a different exp() could flip the rounding")
    return problems


def option_case_mismatches(garages: Sequence, cases: Sequence[Mapping] = GOLDEN_GARAGE_OPTION_CASES) -> list[str]:
    """One message per garage option case whose price (``cost.garage_option_cents``) differs from its expectation, or whose
    garage is not among ``garages`` (``cost.GarageTariff`` objects); empty when all cases hold."""
    by_id = {garage.garage_id: garage for garage in garages}
    problems = []
    for case in cases:
        garage = by_id.get(case["garage_id"])
        if garage is None:
            problems.append(f"{case['id']}: unknown garage id {case['garage_id']!r}; known garages: {sorted(by_id)}")
            continue
        cents = cost.garage_option_cents(garage, case["arrival_s"], case["departure_s"], purpose=case["purpose"])
        if cents != case["expected_cents"]:
            problems.append(f"{case['id']}: expected {case['expected_cents']} ct, got {cents} ct")
    return problems
