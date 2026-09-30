"""The Python parking cost reference (issue #249) against the golden contract shared with the Java port."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from braunschweig.parking import cost
from braunschweig.parking.golden_cases import GOLDEN_CASES, evaluate_case, golden_case_mismatches

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


def _tariff(base_type="street_paid", /, **overrides) -> cost.ZoneTariff:
    """A valid tariff of ``base_type`` with ``overrides`` applied (which may replace the zone_type itself)."""
    fields = {"zone_type": base_type, **dict.fromkeys(_OPTIONAL_FIELDS), **_VALID_FIELDS[base_type], **overrides}
    return cost.ZoneTariff(zone_id=f"test_{base_type}", **fields)


@pytest.fixture(scope="module")
def fixture_zones():
    # The fixture tariff table (euros, spec 5.3 columns) lives with its exporter; imported here so that
    # the pure unit tests below do not depend on it.
    from scripts.export_parking_golden_cases import fixture_zone_tariffs
    return fixture_zone_tariffs()


def test_the_contract_holds_the_38_cases():
    # G01..G26 are the cases of the plan; G27..G38 pin rounding, thresholds and the rule order.
    assert [case["id"] for case in GOLDEN_CASES] == [f"G{number:02d}" for number in range(1, 39)]
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
    assert document["schema_version"] == 1
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


def test_outcomes_are_ten_constants_named_as_their_values():
    assert cost.OUTCOMES == ("HOME", "EMPLOYER_FREE", "RESIDENT_FREE", "OUTSIDE_FEE_HOURS", "FREE_WITHIN_LIMIT",
                             "PAID_METERED", "PAID_LONG_STAY", "PAID_CAMPUS_MEMBER", "PAID_CAMPUS_GUEST", "NO_ZONE")
    assert all(getattr(cost, outcome) == outcome for outcome in cost.OUTCOMES)
    assert cost.CAMPUS_MEMBER_PURPOSES == frozenset({"work", "education"})


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
