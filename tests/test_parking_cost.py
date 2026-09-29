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


def test_the_contract_holds_the_26_cases_of_the_plan():
    assert [case["id"] for case in GOLDEN_CASES] == [f"G{number:02d}" for number in range(1, 27)]
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
    ("street_paid", {"member_day_cents": 350}, "member_day_cents does not apply"),
    ("resident_zone", {"hourly_rate_cents": 100}, "hourly_rate_cents must be 0"),
    ("resident_zone", {"resident_exempt": False}, "resident_exempt must be true"),
    ("campus", {"hourly_rate_cents": 180}, "hourly_rate_cents does not apply"),
    ("campus", {"resident_exempt": "false"}, "resident_exempt must be a bool"),
])
def test_inconsistent_tariffs_are_rejected_at_construction(base_type, overrides, message):
    with pytest.raises(ValueError, match=message):
        _tariff(base_type, **overrides)


def test_residents_park_free_only_where_the_zone_exempts_them():
    # The golden cases only put a resident into a resident-exempt zone (G19); a resident of a paid zone
    # without the exemption pays like everybody else.
    flags = {"purpose": "shop", "parking_free": False, "resident_of_zone": True}
    assert cost.parking_cost_cents(_tariff(), 36000, 37860, **flags) == (93, cost.PAID_METERED)
    assert cost.parking_cost_cents(_tariff(resident_exempt=True), 36000, 37860, **flags) == (0, cost.RESIDENT_FREE)


def test_a_stay_without_a_zone_is_free():
    assert cost.parking_cost_cents(None, 36000, 37860, purpose="shop", parking_free=False,
                                   resident_of_zone=False) == (0, cost.NO_ZONE)


def test_flags_must_be_booleans_so_a_text_false_is_never_truthy():
    with pytest.raises(TypeError, match="parking_free"):
        cost.parking_cost_cents(_tariff(), 36000, 37860, purpose="shop", parking_free="false",
                                resident_of_zone=False)
    with pytest.raises(TypeError, match="arrival_s"):
        cost.parking_cost_cents(_tariff(), 36000.0, 37860, purpose="shop", parking_free=False,
                                resident_of_zone=False)


def test_thresholds_compare_the_chargeable_minutes_rounded_up():
    # One second past a threshold is a started minute (spec 3.2: chargeable_min = ceil(chargeable_s / 60)).
    # The golden cases only use whole minutes, so this pins the rounding for both thresholds.
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
