"""The Python parking cost reference (issue #249) against the golden contract shared with the Java port."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from braunschweig.parking import cost
from braunschweig.parking.golden_cases import GOLDEN_CASES, STAY_ERROR_PATTERN, evaluate_case, golden_case_mismatches

GOLDEN_JSON = Path(__file__).resolve().parent / "fixtures" / "parking" / "parking_golden_cases.json"
REGENERATE_HINT = "regenerate with: python scripts/export_parking_golden_cases.py"

# Per zone type the fields a valid tariff carries; every other tariff field is None. Small synthetic
# tariffs for the unit tests, independent of the fixture tariff set.
_VALID_FIELDS = {
    "street_paid": {"hourly_rate_cents": 180, "billing_unit_min": 1, "fee_start_s": 32400, "fee_end_s": 72000,
                    "resident_exempt": False},
    "resident_zone": {"hourly_rate_cents": 0, "billing_unit_min": 60, "max_stay_min": 120,
                      "long_stay_product_cents": 900, "fee_start_s": 0, "fee_end_s": 86400, "resident_exempt": True},
    "campus": {"member_day_cents": 350, "guest_day_cents": 900, "fee_start_s": 0, "fee_end_s": 86400,
               "resident_exempt": False},
}
_OPTIONAL_FIELDS = ("hourly_rate_cents", "billing_unit_min", "free_if_stay_at_most_min", "first_period_min",
                    "first_period_cents", "daily_cap_cents", "max_stay_min", "long_stay_product_cents",
                    "member_day_cents", "guest_day_cents")
# A garage of schema 2: the core (120 ct per started hour, all day) plus the optional day cap of 960 ct; the first
# period is optional too.
_GARAGE = {"garage_hourly_rate_cents": 120, "garage_billing_unit_min": 60, "garage_daily_cap_cents": 960,
           "garage_fee_start_s": 0, "garage_fee_end_s": 86400}
_SHOP = {"purpose": "shop", "parking_free": False, "resident_of_zone": False}


def _tariff(base_type="street_paid", /, **overrides) -> cost.ZoneTariff:
    """A valid tariff of ``base_type`` with ``overrides`` applied (which may replace the zone_type itself).

    The schema-2 fields are left out unless overridden: a tariff without them is a schema-1 tariff.
    """
    fields = {"zone_type": base_type, **dict.fromkeys(_OPTIONAL_FIELDS), **_VALID_FIELDS[base_type], **overrides}
    return cost.ZoneTariff(zone_id=f"test_{base_type}", **fields)


@pytest.fixture(scope="module")
def fixture_zones():
    # The fixture tariff table (euros, spec 5.3 columns) lives with its exporter; imported here so that
    # the pure unit tests below do not depend on it.
    from scripts.export_parking_golden_cases import fixture_zone_tariffs
    return fixture_zone_tariffs()


def test_the_contract_holds_the_g_l_lz_and_v_cases_each_with_its_minimum_stay():
    # G01..G38 price as before rule L1 (L = 0); L01..L08 pin L1 at 15 min and their twins L01Z..L08Z the same stays at
    # L = 0 (ADR-0139 decision 9); V01..V23 pin the product minimum of schema 2 at L = 15 (issue #436), V15..V23 its
    # edge rules (ties, the garage's own fee window, the uncapped garage, the unavailable street, T1 before L1).
    families = (("G", "", range(1, 39), 0), ("L", "", range(1, 9), 15), ("L", "Z", range(1, 9), 0),
                ("V", "", range(1, 24), 15))
    expected = [(f"{prefix}{number:02d}{suffix}", minimum_stay_min)
                for prefix, suffix, numbers, minimum_stay_min in families for number in numbers]
    assert [(case["id"], case["minimum_stay_min"]) for case in GOLDEN_CASES] == expected
    assert all(case["expected_outcome"] in cost.OUTCOMES for case in GOLDEN_CASES if not case["expected_error"])
    assert [case["id"] for case in GOLDEN_CASES if case["expected_error"]] == ["G26"]


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[case["id"] for case in GOLDEN_CASES])
def test_golden_case(case, fixture_zones):
    if case["expected_error"]:
        with pytest.raises(ValueError, match="before arrival_s"):
            evaluate_case(case, fixture_zones)
    else:
        assert evaluate_case(case, fixture_zones) == (case["expected_cents"], case["expected_outcome"])


def test_the_committed_golden_json_is_in_sync(fixture_zones):
    document = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    assert document["schema_version"] == 2
    assert document["cases"] == [dict(case) for case in GOLDEN_CASES], REGENERATE_HINT
    zones = {zone_id: cost.ZoneTariff(zone_id=zone_id, **fields) for zone_id, fields in document["tariffs"].items()}
    assert zones == fixture_zones, REGENERATE_HINT
    # Re-evaluate every case of the FILE with the file's own tariffs, as the Java test does.
    assert golden_case_mismatches(zones, document["cases"]) == []


def test_an_error_case_must_fail_the_stay_check_not_just_any_check(fixture_zones):
    # G26 is invalid because its departure lies before its arrival. The same stay in a zone the tariffs do not
    # know raises a ValueError too -- but the lookup's, not the stay check's -- and must count as a mismatch.
    g26 = next(case for case in GOLDEN_CASES if case["id"] == "G26")
    assert golden_case_mismatches(fixture_zones, [g26]) == []
    [problem] = golden_case_mismatches(fixture_zones, [{**g26, "zone_id": "fx_unknown"}])
    assert problem.startswith("G26:") and "departure before arrival" in problem and "unknown zone id" in problem


def test_outcomes_are_twelve_constants_named_as_their_values_with_the_v2_products_appended():
    # Appended at the END, so the declaration order of the ten v1 outcomes (the order of the Java ParkingOutcome
    # enum and of the outcome report) stays as it was.
    assert cost.OUTCOMES == ("HOME", "EMPLOYER_FREE", "RESIDENT_FREE", "OUTSIDE_FEE_HOURS", "FREE_WITHIN_LIMIT",
                             "PAID_METERED", "PAID_LONG_STAY", "PAID_CAMPUS_MEMBER", "PAID_CAMPUS_GUEST", "NO_ZONE",
                             "PAID_GARAGE", "PAID_COMMUTER")
    assert all(getattr(cost, outcome) == outcome for outcome in cost.OUTCOMES)
    assert cost.CAMPUS_MEMBER_PURPOSES == frozenset({"work", "education"})
    assert cost.COMMUTER_PURPOSES == frozenset({"work", "education"})


def test_chargeable_seconds_over_three_days_with_a_whole_day_window_is_the_full_duration():
    arrival_s = 3 * 3600
    departure_s = arrival_s + 3 * 86400
    assert cost.chargeable_seconds(arrival_s, departure_s, 0, 86400) == 3 * 86400


def test_chargeable_seconds_of_a_stay_between_two_fee_windows_is_zero():
    # 20:00 on day 0 to 09:00 on day 1 against a 09:00-20:00 window: both edges touch, nothing overlaps.
    assert cost.chargeable_seconds(72000, 118800, 32400, 72000) == 0
    assert cost.chargeable_seconds(36000, 36000, 32400, 72000) == 0


def test_terminal_departure_is_the_end_of_the_fee_window_of_the_arrival_day():
    assert cost.terminal_departure_s(75600, 72000) == 75600
    assert cost.terminal_departure_s(70200, 72000) == 72000
    assert cost.terminal_departure_s(86400 + 70200, 72000) == 86400 + 72000


def test_departure_before_arrival_raises():
    with pytest.raises(ValueError, match="before arrival_s"):
        cost.parking_cost_cents(_tariff(), 37860, 36000, purpose="shop", parking_free=False, resident_of_zone=False)
    with pytest.raises(ValueError, match="before arrival_s"):
        cost.chargeable_seconds(37860, 36000, 32400, 72000)


def test_street_paid_with_max_stay_but_without_long_stay_product_is_rejected_at_construction():
    with pytest.raises(ValueError, match="long_stay_product_cents"):
        _tariff(max_stay_min=180)


@pytest.mark.parametrize("base_type, overrides, message", [
    ("street_paid", {"zone_type": "garage"}, "unknown zone_type"),
    ("street_paid", {"first_period_min": 60}, "first_period_cents"),
    ("street_paid", {"billing_unit_min": None}, "billing_unit_min is required"),
    ("street_paid", {"daily_cap_cents": 0}, "daily_cap_cents must be at least 1"),
    ("street_paid", {"hourly_rate_cents": -1}, "hourly_rate_cents must be at least 0"),
    ("street_paid", {"hourly_rate_cents": 1.8}, "hourly_rate_cents must be an integer"),
    ("street_paid", {"fee_start_s": 72000, "fee_end_s": 32400}, "fee window"),
    # A zero-length window is no fee window either.
    ("street_paid", {"fee_start_s": 32400, "fee_end_s": 32400}, "fee window"),
    ("street_paid", {"member_day_cents": 350}, "member_day_cents does not apply"),
    ("resident_zone", {"hourly_rate_cents": 100}, "hourly_rate_cents must be 0"),
    ("resident_zone", {"resident_exempt": False}, "resident_exempt must be true"),
    # Spec 3.1 does not list it for resident zones, but the metered step divides by it for every non-campus zone.
    ("resident_zone", {"billing_unit_min": None}, "billing_unit_min is required for zone_type 'resident_zone'"),
    ("campus", {"hourly_rate_cents": 180}, "hourly_rate_cents does not apply"),
    ("campus", {"resident_exempt": "false"}, "resident_exempt must be a bool"),
    # Schema 2 (spec amendment A6): the garage core (rate, unit, fee window) is all-or-none; the day cap is optional
    # (empty = no cap) and the first period comes as a pair, both only with the core.
    ("street_paid", {**_GARAGE, "garage_billing_unit_min": None}, r"garage core .*garage_billing_unit_min"),
    ("street_paid", {**_GARAGE, "garage_fee_end_s": None}, r"garage core .*garage_fee_end_s"),
    ("street_paid", {"garage_daily_cap_cents": 960}, r"garage_daily_cap_cents .*garage core"),
    ("street_paid", {"garage_first_period_min": 60, "garage_first_period_cents": 120}, "garage core"),
    ("street_paid", {**_GARAGE, "garage_first_period_min": 60}, "garage_first_period_cents"),
    ("street_paid", {**_GARAGE, "garage_fee_start_s": 72000, "garage_fee_end_s": 32400}, "garage fee window"),
    ("street_paid", {**_GARAGE, "garage_billing_unit_min": 0}, "garage_billing_unit_min must be at least 1"),
    ("street_paid", {**_GARAGE, "garage_daily_cap_cents": 0}, "garage_daily_cap_cents must be at least 1"),
    ("street_paid", {**_GARAGE, "garage_first_period_min": 60, "garage_first_period_cents": 1000},
     "garage_daily_cap_cents 960 is below garage_first_period_cents 1000"),
    ("street_paid", {"commuter_day_cents": -1}, "commuter_day_cents must be at least 0"),
    ("street_paid", {"search_time_min": -1}, "search_time_min must be at least 0"),
    ("street_paid", {"search_time_min": 2.5}, "search_time_min must be an integer"),
    # Without a long-stay product only a garage can price a stay above the maximum stay; v1 resident zones keep
    # their long-stay product unless they have a garage.
    ("resident_zone", {"long_stay_product_cents": None}, "long_stay_product_cents"),
])
def test_inconsistent_tariffs_are_rejected_at_construction(base_type, overrides, message):
    with pytest.raises(ValueError, match=message):
        _tariff(base_type, **overrides)


# Written out here, independently of cost._NOT_APPLICABLE_FIELDS: per zone type every field it does not have
# (a pair is set together, so the pair rule passes and the applicability rule decides).
@pytest.mark.parametrize("base_type, overrides, field", [
    ("street_paid", {"member_day_cents": 350}, "member_day_cents"),
    ("street_paid", {"guest_day_cents": 900}, "guest_day_cents"),
    ("resident_zone", {"free_if_stay_at_most_min": 30}, "free_if_stay_at_most_min"),
    ("resident_zone", {"first_period_min": 60, "first_period_cents": 70}, "first_period_min"),
    ("resident_zone", {"daily_cap_cents": 900}, "daily_cap_cents"),
    ("resident_zone", {"member_day_cents": 350}, "member_day_cents"),
    ("resident_zone", {"guest_day_cents": 900}, "guest_day_cents"),
    ("campus", {"hourly_rate_cents": 180}, "hourly_rate_cents"),
    ("campus", {"billing_unit_min": 1}, "billing_unit_min"),
    ("campus", {"free_if_stay_at_most_min": 30}, "free_if_stay_at_most_min"),
    ("campus", {"first_period_min": 60, "first_period_cents": 70}, "first_period_min"),
    ("campus", {"daily_cap_cents": 900}, "daily_cap_cents"),
    ("campus", {"max_stay_min": 180, "long_stay_product_cents": 900}, "max_stay_min"),
    # Schema 2: a campus stay pays a day product, never a garage; the commuter product is not one of the resident
    # zone's products (A4 allows it on street_paid and campus rows only).
    ("campus", dict(_GARAGE), "garage_hourly_rate_cents"),
    ("resident_zone", {"commuter_day_cents": 376}, "commuter_day_cents"),
])
def test_a_field_the_zone_type_does_not_have_must_stay_empty(base_type, overrides, field):
    with pytest.raises(ValueError, match=f"{field} does not apply to zone_type '{base_type}' and must be empty"):
        _tariff(base_type, **overrides)


@pytest.mark.parametrize("overrides, field", [
    ({"billing_unit_min": 0}, "billing_unit_min"),
    ({"free_if_stay_at_most_min": 0}, "free_if_stay_at_most_min"),
    ({"first_period_min": 0, "first_period_cents": 70}, "first_period_min"),
    ({"daily_cap_cents": 0}, "daily_cap_cents"),
    ({"max_stay_min": 0, "long_stay_product_cents": 900}, "max_stay_min"),
])
def test_zero_is_rejected_in_every_field_the_cost_rule_tests_for_truthiness(overrides, field):
    # Spec 3.2 reads these fields as "is set" by truthiness (and divides by the billing unit), so a 0 would
    # silently mean "not set"; ZoneTariff therefore requires at least 1 when a value is given.
    with pytest.raises(ValueError, match=f"{field} must be at least 1, got 0"):
        _tariff(**overrides)


def test_a_daily_cap_below_the_first_period_price_is_a_contradictory_tariff():
    # The first period is charged in full for any use, so a lower cap would replace its price on every metered
    # stay: the tariff contradicts itself. The error names the zone and both values.
    with pytest.raises(ValueError) as error:
        _tariff(first_period_min=60, first_period_cents=110, daily_cap_cents=100)
    message = str(error.value)
    assert "'test_street_paid'" in message and "daily_cap_cents 100" in message and "first_period_cents 110" in message
    # A cap equal to the first-period price is consistent, and so is either field alone.
    _tariff(first_period_min=60, first_period_cents=110, daily_cap_cents=110)
    _tariff(first_period_min=60, first_period_cents=110)
    _tariff(daily_cap_cents=100)


def test_residents_park_free_only_where_the_zone_exempts_them():
    # A resident of a paid zone without the exemption pays like everybody else (golden G31 on the fixture
    # set; here on a synthetic tariff, both ways).
    flags = {"purpose": "shop", "parking_free": False, "resident_of_zone": True}
    assert cost.parking_cost_cents(_tariff(), 36000, 37860, **flags) == (93, cost.PAID_METERED)
    assert cost.parking_cost_cents(_tariff(resident_exempt=True), 36000, 37860, **flags) == (0, cost.RESIDENT_FREE)


def test_a_stay_without_a_zone_is_free():
    assert cost.parking_cost_cents(None, 36000, 37860, purpose="shop", parking_free=False,
                                   resident_of_zone=False) == (0, cost.NO_ZONE)


def test_no_zone_precedes_the_home_rule():
    # Rule order: a stay outside every zone is NO_ZONE even for the home purpose, so the outcome counts
    # separate "outside the zones" from "home inside a zone". Not a golden case: the golden cases price stays
    # inside fixture zones, and the Java port decides NO_ZONE in its cost model before the calculator.
    assert cost.parking_cost_cents(None, 36000, 39600, purpose="home", parking_free=False,
                                   resident_of_zone=False) == (0, "NO_ZONE")


def test_flags_must_be_booleans_so_a_text_false_is_never_truthy():
    with pytest.raises(TypeError, match="parking_free"):
        cost.parking_cost_cents(_tariff(), 36000, 37860, purpose="shop", parking_free="false",
                                resident_of_zone=False)
    with pytest.raises(TypeError, match="arrival_s"):
        cost.parking_cost_cents(_tariff(), 36000.0, 37860, purpose="shop", parking_free=False,
                                resident_of_zone=False)


def test_thresholds_compare_the_chargeable_minutes_rounded_up():
    # One second past a threshold is a started minute (spec 3.2: chargeable_min = ceil(chargeable_s / 60)).
    # Golden G32..G34 pin the same on the fixture set; this pins both sides of both thresholds on one tariff.
    free_limit = _tariff(hourly_rate_cents=100, billing_unit_min=6, free_if_stay_at_most_min=30,
                         first_period_min=60, first_period_cents=70)
    flags = {"purpose": "shop", "parking_free": False, "resident_of_zone": False}
    assert cost.parking_cost_cents(free_limit, 36000, 36000 + 1800, **flags) == (0, cost.FREE_WITHIN_LIMIT)
    assert cost.parking_cost_cents(free_limit, 36000, 36000 + 1801, **flags) == (70, cost.PAID_METERED)
    max_stay = _tariff(max_stay_min=180, long_stay_product_cents=900)
    assert cost.parking_cost_cents(max_stay, 36000, 36000 + 10800, **flags) == (540, cost.PAID_METERED)
    assert cost.parking_cost_cents(max_stay, 36000, 36000 + 10801, **flags) == (900, cost.PAID_LONG_STAY)


def test_the_first_period_is_charged_in_full_for_any_use_and_the_rest_rounds_half_up():
    tariff = _tariff(hourly_rate_cents=100, billing_unit_min=6, first_period_min=60, first_period_cents=70)
    # 1 s of chargeable time already buys the whole first period.
    assert cost.parking_cost_cents(tariff, 36000, 36001, purpose="shop", parking_free=False,
                                   resident_of_zone=False) == (70, cost.PAID_METERED)
    # One minute at 50 / 30 / 29 ct/h is 0.83 / 0.50 / 0.48 ct: half up gives 1 / 1 / 0 ct, and 0 ct is free.
    for rate_cents, expected in ((50, (1, cost.PAID_METERED)), (30, (1, cost.PAID_METERED)),
                                 (29, (0, cost.FREE_WITHIN_LIMIT))):
        per_minute = _tariff(hourly_rate_cents=rate_cents, billing_unit_min=1)
        assert cost.parking_cost_cents(per_minute, 36000, 36060, purpose="shop", parking_free=False,
                                       resident_of_zone=False) == expected


@pytest.mark.parametrize("overrides, arrival_s, departure_s, purpose, expected", [
    # 60 min: street 60 x 2 ct = 120 ct and garage one started hour = 120 ct; a tie goes to the street.
    ({"hourly_rate_cents": 120, **_GARAGE}, 36000, 39600, "shop", (120, cost.PAID_METERED)),
    # Street 600 ct; garage 120 ct and commuter 120 ct tie; the garage comes before the commuter product.
    ({"hourly_rate_cents": 600, **_GARAGE, "commuter_day_cents": 120}, 36000, 39600, "work", (120, cost.PAID_GARAGE)),
    # The commuter product competes only for the commuter purposes.
    ({"hourly_rate_cents": 600, **_GARAGE, "commuter_day_cents": 100}, 36000, 39600, "work", (100, cost.PAID_COMMUTER)),
    ({"hourly_rate_cents": 600, **_GARAGE, "commuter_day_cents": 100}, 36000, 39600, "other", (120, cost.PAID_GARAGE)),
    # 19:00-20:00 lies inside the street window 09:00-20:00 (180 ct) but after the garage window 07:00-19:00: no garage
    # second is used, so not even its first period is charged, and the garage costs 0 ct (FREE_WITHIN_LIMIT, as a
    # metered 0 ct stay).
    ({**_GARAGE, "garage_fee_start_s": 25200, "garage_fee_end_s": 68400, "garage_first_period_min": 60,
      "garage_first_period_cents": 120}, 68400, 72000, "shop", (0, cost.FREE_WITHIN_LIMIT)),
], ids=["street_beats_garage_on_a_tie", "garage_beats_commuter_on_a_tie", "commuter_for_work",
        "no_commuter_for_other_purposes", "garage_unused_outside_its_window"])
def test_the_cheapest_product_wins_and_ties_go_to_street_then_garage_then_commuter(overrides, arrival_s, departure_s,
                                                                                  purpose, expected):
    flags = {**_SHOP, "purpose": purpose}
    assert cost.parking_cost_cents(_tariff(**overrides), arrival_s, departure_s, **flags) == expected


def test_a_resident_zone_without_long_stay_product_prices_a_long_stay_with_its_garage():
    # "long_stay_product_eur becomes optional when a garage family exists" holds for resident zones too: above the
    # maximum stay of 120 min the street is unavailable and the garage prices 180 min as 3 started hours; within the
    # maximum stay disc parking at rate 0 stays free and beats the garage.
    tariff = _tariff("resident_zone", long_stay_product_cents=None, **_GARAGE)
    assert cost.parking_cost_cents(tariff, 36000, 46800, **_SHOP) == (360, cost.PAID_GARAGE)
    assert cost.parking_cost_cents(tariff, 36000, 41400, **_SHOP) == (0, cost.FREE_WITHIN_LIMIT)
    # The residents of the zone stay exempt (R1), before any product.
    assert cost.parking_cost_cents(tariff, 36000, 46800, **{**_SHOP, "resident_of_zone": True}) == (
        0, cost.RESIDENT_FREE)


def test_a_campus_commuter_product_wins_only_when_cheaper_than_the_member_day_product():
    # A4: member purposes pay min(member day, commuter); a tie keeps the member day product, guests pay the guest day.
    for commuter_cents, expected in ((349, (349, cost.PAID_COMMUTER)), (350, (350, cost.PAID_CAMPUS_MEMBER))):
        tariff = _tariff("campus", commuter_day_cents=commuter_cents)
        assert cost.parking_cost_cents(tariff, 28800, 61200, **{**_SHOP, "purpose": "education"}) == expected
        assert cost.parking_cost_cents(tariff, 28800, 61200, **_SHOP) == (900, cost.PAID_CAMPUS_GUEST)


# Rule L1 (ADR-0139 decision 9): every priced car stay lasts at least L minutes, applied to the stay before the tariff
# rules. The eight cases L01..L08 (L = 15 min) and their twins L01Z..L08Z (L = 0, the pricing before L1) are golden
# cases now, exported with the others and read by the Java ParkingCostCalculatorTest from the golden JSON. This table
# pins them against the decision's numbers, independently of braunschweig.parking.golden_cases, so that an export with
# a wrong stay, minimum or expectation cannot pass silently. Times of day: 36000 = 10:00, 40000 = 11:06:40,
# 71400 = 19:50, 72000 = 20:00 (the end of the fee window of fx_bs_ia and fx_bs_ib), 73000 = 20:16:40.
_MINIMUM_STAY_S = 900
_MINIMUM_STAY_PINS = {
    # id: (zone, purpose, arrival_s, departure_s, priced departure_s with L = 15, result with L = 15, with L = 0).
    # A zero-length stay inside the fee window pays 15 min at 180 ct/h instead of nothing.
    "L01": ("fx_bs_ia", "shop", 36000, 36000, 36900, (45, "PAID_METERED"), (0, "OUTSIDE_FEE_HOURS")),
    # 15 min lie within the free threshold of 30 min.
    "L02": ("fx_sz", "shop", 40000, 40000, 40900, (0, "FREE_WITHIN_LIMIT"), (0, "OUTSIDE_FEE_HOURS")),
    # Only the 10 min before the end of the fee window are chargeable: 30 ct.
    "L03": ("fx_bs_ib", "shop", 71400, 71400, 72300, (30, "PAID_METERED"), (0, "OUTSIDE_FEE_HOURS")),
    # Any use buys the first period (60 min, 110 ct) in full.
    "L04": ("fx_wob", "shop", 36000, 36000, 36900, (110, "PAID_METERED"), (0, "OUTSIDE_FEE_HOURS")),
    # A 5-min stay pays 15 min: 45 ct instead of 15 ct.
    "L05": ("fx_bs_ia", "shop", 36000, 36300, 36900, (45, "PAID_METERED"), (15, "PAID_METERED")),
    # A stay longer than the minimum is priced unchanged.
    "L06": ("fx_bs_ia", "shop", 36000, 39600, 39600, (180, "PAID_METERED"), (180, "PAID_METERED")),
    # A zero-length work stay on campus pays the member day product.
    "L07": ("fx_campus", "work", 36000, 36000, 36900, (350, "PAID_CAMPUS_MEMBER"), (0, "OUTSIDE_FEE_HOURS")),
    # The extended stay still lies after the fee window: free.
    "L08": ("fx_bs_ib", "shop", 73000, 73000, 73900, (0, "OUTSIDE_FEE_HOURS"), (0, "OUTSIDE_FEE_HOURS")),
}


def test_the_exported_minimum_stay_cases_keep_the_numbers_of_adr_0139_decision_9():
    cases = {case["id"]: case for case in GOLDEN_CASES}
    for case_id, (zone_id, purpose, arrival_s, departure_s, priced_s, with_minimum, without_minimum) in (
            _MINIMUM_STAY_PINS.items()):
        assert cost.minimum_stay_departure_s(arrival_s, departure_s, _MINIMUM_STAY_S) == priced_s, case_id
        for twin_id, minimum_stay_min, expected in ((case_id, 15, with_minimum), (case_id + "Z", 0, without_minimum)):
            case = cases[twin_id]
            assert (case["zone_id"], case["purpose"], case["arrival_s"], case["departure_s"], case["minimum_stay_min"],
                    case["parking_free"], case["resident_of_zone"], case["terminal"], case["expected_error"]) == (
                zone_id, purpose, arrival_s, departure_s, minimum_stay_min, False, False, False, False), twin_id
            assert (case["expected_cents"], case["expected_outcome"]) == expected, twin_id


def test_the_minimum_stay_rejects_invalid_input_instead_of_pricing_it():
    # The max() would quietly turn an invalid stay into a valid one, so the helper validates first, like the Java
    # ParkingCostCalculator.minimumStayDeparture_s. A departure before the arrival stays the stay check's error
    # (golden G26) instead of becoming the stay [37860, 38760).
    with pytest.raises(ValueError, match=STAY_ERROR_PATTERN):
        cost.minimum_stay_departure_s(37860, 36000, _MINIMUM_STAY_S)
    with pytest.raises(ValueError, match="arrival_s must be non-negative"):
        cost.minimum_stay_departure_s(-600, 36000, _MINIMUM_STAY_S)
    # Integer seconds only, as everywhere in the reference: a non-finite time is not a time.
    with pytest.raises(TypeError, match="departure_s must be an integer"):
        cost.minimum_stay_departure_s(36000, math.inf, _MINIMUM_STAY_S)
    # A negative or non-integer minimum is a configuration error (the Java config group rejects it at load time).
    with pytest.raises(ValueError, match="minimum_stay_s must be non-negative"):
        cost.minimum_stay_departure_s(36000, 36000, -60)
    for minimum_stay_s in (900.0, True):
        with pytest.raises(TypeError, match="minimum_stay_s must be an integer"):
            cost.minimum_stay_departure_s(36000, 36000, minimum_stay_s)
