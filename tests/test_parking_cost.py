"""The Python parking cost reference (issue #249) against the golden contract shared with the Java port."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from braunschweig.parking import cost
from braunschweig.parking.golden_cases import (GOLDEN_CASES, GOLDEN_GARAGE_OPTION_CASES, STAY_ERROR_PATTERN, evaluate_case,
                                               golden_case_mismatches, option_case_mismatches)

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


@pytest.fixture(scope="module")
def fixture_garages():
    # The fixture garages (dataset -> tariff model -> GarageTariff), as the Java side reads them.
    from scripts.export_parking_golden_cases import fixture_garage_tariffs
    return fixture_garage_tariffs()


def test_the_contract_holds_the_g_l_lz_v_r_e_f_p_and_x_cases_each_with_its_minimum_stay():
    # G01..G38 price as before rule L1 (L = 0); L01..L08 pin L1 at 15 min and their twins L01Z..L08Z the same stays at
    # L = 0 (ADR-0139 decision 9); V01..V23 pin the product minimum of schema 2 at L = 15 (issue #436), V15..V23 its
    # edge rules (ties, the garage's own fee window, the uncapped garage, the unavailable street, T1 before L1);
    # R01..R13 pin the resident district rule R2 (spec Amendment C3) and its scope R2-a (resident_permits_valid), at L = 0
    # except R10 and R11 at L = 15; E01..E32 pin the garage options of spec Amendment E at L = 0 except E26 at L = 15
    # (E29..E32 the free options of E14: a free garage lowers the expected cost, never raises a street-free stay);
    # E33..E38 pin the separate commuter decay length of spec Amendment H (work and education against every other purpose);
    # schema 6 (spec v3) appends F01..F08 (maximum-stay feasibility), P01..P06 (the price in the option weights) and
    # X01..X09 (the EV exemption), all at L = 0.
    families = (("G", "", range(1, 39), 0), ("L", "", range(1, 9), 15), ("L", "Z", range(1, 9), 0),
                ("V", "", range(1, 24), 15))
    expected = [(f"{prefix}{number:02d}{suffix}", minimum_stay_min)
                for prefix, suffix, numbers, minimum_stay_min in families for number in numbers]
    expected += [(f"R{number:02d}", 15 if number in (10, 11) else 0) for number in range(1, 14)]
    expected += [(f"E{number:02d}", 15 if number == 26 else 0) for number in range(1, 39)]
    expected += [(f"{prefix}{number:02d}", 0) for prefix, count in (("F", 8), ("P", 6), ("X", 9))
                 for number in range(1, count + 1)]
    assert [(case["id"], case["minimum_stay_min"]) for case in GOLDEN_CASES] == expected
    # only the R cases and E12 (R2 before the garages) set the district flag, and the flag is a key of every case
    assert all("resident_of_district" in case for case in GOLDEN_CASES)
    assert [case["id"] for case in GOLDEN_CASES if case["resident_of_district"]] == [
        "R01", "R03", "R04", "R05", "R06", "R07", "R08", "R09", "R10", "R11", "R12", "R13", "E12"]
    assert all(case["expected_outcome"] in cost.OUTCOMES for case in GOLDEN_CASES if not case["expected_error"])
    assert [case["id"] for case in GOLDEN_CASES if case["expected_error"]] == ["G26"]


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[case["id"] for case in GOLDEN_CASES])
def test_golden_case(case, fixture_zones, fixture_garages):
    if case["expected_error"]:
        with pytest.raises(ValueError, match="before arrival_s"):
            evaluate_case(case, fixture_zones, fixture_garages)
    else:
        assert evaluate_case(case, fixture_zones, fixture_garages) == (case["expected_cents"], case["expected_outcome"])


@pytest.mark.parametrize("case", GOLDEN_GARAGE_OPTION_CASES, ids=[case["id"] for case in GOLDEN_GARAGE_OPTION_CASES])
def test_golden_garage_option_case(case, fixture_garages):
    assert option_case_mismatches(fixture_garages, [case]) == []


def test_the_garage_option_cases_are_named_o01_to_o69_then_s01_to_s25_and_name_known_fixture_garages(fixture_garages):
    # S01..S25 (spec v3 R-FW-2) are the release band shapes the fixture lacked, appended after O69
    assert [case["id"] for case in GOLDEN_GARAGE_OPTION_CASES] == (
        [f"O{number:02d}" for number in range(1, 70)] + [f"S{number:02d}" for number in range(1, 26)])
    assert {case["garage_id"] for case in GOLDEN_GARAGE_OPTION_CASES} <= {garage.garage_id for garage in fixture_garages}
    assert option_case_mismatches(fixture_garages, [{**GOLDEN_GARAGE_OPTION_CASES[0], "garage_id": "fx_unknown"}])


_E14_GARAGE_IDS = ("fx_g15_tier_grace", "fx_g16_tier_grace_cap", "fx_g17_free", "fx_g18_grace_total")
_E14_SEPARATION_MARGIN_M = 1000.0


def test_the_e14_fixture_garages_leave_every_earlier_garage_option_set_unchanged(fixture_garages):
    # The Java contract copies the golden file byte for byte, so the cases written before the E14 garages (E01..E28) must
    # keep their option sets: no destination of those cases may see an E14 garage, and the E14 cases (E29..E32) see
    # only fx_g17_free. The margin keeps a clear distance beyond D_max, so no rounding of coordinates can change this.
    earlier_cases = [case for case in GOLDEN_CASES if case["id"][0] == "E" and case["destination_x_m"] is not None
                     and int(case["id"][1:]) <= 28]
    e14_cases = [case for case in GOLDEN_CASES if case["id"] in ("E29", "E30", "E31", "E32")]
    assert len(earlier_cases) == 28 and len(e14_cases) == 4
    assert {garage.garage_id for garage in fixture_garages} >= set(_E14_GARAGE_IDS)
    for case in earlier_cases + e14_cases:
        in_range = {garage.garage_id for garage, _ in cost.garage_options_in_range(
            fixture_garages, case["destination_x_m"], case["destination_y_m"],
            cost.GARAGE_MAX_DISTANCE_M + _E14_SEPARATION_MARGIN_M)}
        if case in earlier_cases:
            assert not in_range & set(_E14_GARAGE_IDS), case["id"]
        else:
            assert in_range == {"fx_g17_free"}, case["id"]


def test_the_cases_of_amendment_h_pin_a_work_a_shopping_and_an_education_stay_with_two_decay_lengths(fixture_zones,
                                                                                                    fixture_garages):
    # Every case before E33 carries ONE decay for every purpose (the commuter decay equals the visitor decay, as before
    # Amendment H), so their pricing is unchanged; E33..E38 carry the decays of the fixture model (lambda 400 m, lambda_c
    # 250 m) or a switched-off one. The hand derivations are in the comments of braunschweig.parking.golden_cases.
    by_id = {case["id"]: case for case in GOLDEN_CASES}
    older = [case for case in GOLDEN_CASES if case["id"][0] in "GLVR" or (case["id"][0] == "E" and case["id"] < "E33")]
    assert len(older) == len(GOLDEN_CASES) - 6 - 23
    assert all(case["garage_decay_commute_m"] == case["garage_decay_m"] for case in older)
    from braunschweig.parking.golden_cases import FIXTURE_GARAGE_DECAY_COMMUTE_M, FIXTURE_GARAGE_DECAY_M
    assert (FIXTURE_GARAGE_DECAY_M, FIXTURE_GARAGE_DECAY_COMMUTE_M) == (400.0, 250.0)
    pinned = {  # id: (purpose, decay, commute decay, cents, outcome, garage probability)
        "E33": ("work", 400.0, 250.0, 761, "PAID_EXPECTED", 0.2314752165),
        "E34": ("shop", 400.0, 250.0, 1125, "PAID_EXPECTED", 0.3208213008),
        "E35": ("education", 400.0, 250.0, 185, "PAID_EXPECTED", 0.2314752165),
        "E36": ("work", 400.0, 0.0, 900, "PAID_METERED", 0.0),
        "E37": ("work", 0.0, 250.0, 761, "PAID_EXPECTED", 0.2314752165),
        "E38": ("shop", 0.0, 250.0, 900, "PAID_METERED", 0.0)}
    for case_id, (purpose, decay, commute, cents, outcome, probability) in pinned.items():
        case = by_id[case_id]
        assert (case["purpose"], case["garage_decay_m"], case["garage_decay_commute_m"]) == (purpose, decay, commute)
        assert (case["expected_cents"], case["expected_outcome"]) == (cents, outcome), case_id
        assert case["expected_garage_probability"] == pytest.approx(probability, abs=1e-9), case_id
    # the work stay and the shopping stay of E33 and E34 have the same destination and the same times (E04 is E34 with one decay)
    for key in ("zone_id", "arrival_s", "departure_s", "destination_x_m", "destination_y_m"):
        assert by_id["E33"][key] == by_id["E34"][key] == by_id["E04"][key]
    # without the commuter decay the work stay would be priced by lambda: E03 (708 ct, probability 0.3208) is the same work
    # stay with one decay of 400 m, and E33 differs from it only by the commuter decay
    assert (by_id["E03"]["expected_cents"], by_id["E33"]["expected_cents"]) == (708, 761)


def test_the_cases_without_a_destination_price_exactly_as_before_the_garage_options(fixture_zones):
    # Families G, L, LZ, V and R carry no destination: their numbers must be those of the schema-2 reference
    # parking_cost_cents, whatever garages exist and whatever the decay. (The cases of F, P and X that have none are pinned in
    # tests/test_parking_golden_cases.py: they price the v3 rules, which this schema-2 comparison does not know.)
    without_destination = [case for case in GOLDEN_CASES if case["destination_x_m"] is None and case["id"][0] in "GLVR"]
    assert [case["id"] for case in without_destination] == [case["id"] for case in GOLDEN_CASES if case["id"][0] in "GLVR"]
    assert len(without_destination) == 90
    for case in without_destination:
        if case["expected_error"]:
            continue
        zone = fixture_zones[case["zone_id"]]
        departure_s = (cost.terminal_departure_s(case["arrival_s"], zone.fee_end_s) if case["terminal"]
                       else case["departure_s"])
        departure_s = cost.minimum_stay_departure_s(case["arrival_s"], departure_s, case["minimum_stay_min"] * 60)
        assert cost.parking_cost_cents(zone, case["arrival_s"], departure_s, purpose=case["purpose"],
                                       parking_free=case["parking_free"], resident_of_zone=case["resident_of_zone"],
                                       resident_of_district=case["resident_of_district"]) == (
            case["expected_cents"], case["expected_outcome"]), case["id"]
        assert case["expected_garage_probability"] == 0.0 and case["garage_decay_m"] == 400.0
        assert case["garage_decay_commute_m"] == 400.0


def test_every_pinned_expectation_keeps_a_rounding_margin_and_a_mismatch_is_reported(fixture_zones, fixture_garages):
    # E03's unrounded expectation 707.507 is the closest to a half cent (0.007 cents): Math.exp cannot flip it. A
    # case whose hand-pinned number is wrong is reported, and so is a wrong garage probability.
    assert golden_case_mismatches(fixture_zones, GOLDEN_CASES, fixture_garages) == []
    e02 = next(case for case in GOLDEN_CASES if case["id"] == "E02")
    wrong_cents = golden_case_mismatches(fixture_zones, [{**e02, "expected_cents": 187}], fixture_garages)
    assert len(wrong_cents) == 1 and wrong_cents[0].startswith("E02: expected (187, 'PAID_EXPECTED')")
    wrong_probability = golden_case_mismatches(fixture_zones, [{**e02, "expected_garage_probability": 0.5}], fixture_garages)
    assert len(wrong_probability) == 1 and "garage probability" in wrong_probability[0]


def test_the_committed_golden_json_is_in_sync(fixture_zones, fixture_garages):
    from scripts.export_parking_golden_cases import GOLDEN_SCHEMA_VERSION

    document = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    # 6 since the v3 mechanics (spec v3 A1, A2, A5): ev_exempt and option_price_beta_u_per_eur of every case and the document key
    # option_price_beta_u_per_eur of the fixture model; 5 since the separate commuter decay (spec Amendment H):
    # garage_decay_commute_m of every case; 4 since the garage options (spec Amendment E): garages, garage_max_distance_m,
    # garage_option_cases and the four garage keys of every case (3: resident_of_district of the rule R2; 2: minimum_stay_min)
    assert document["schema_version"] == GOLDEN_SCHEMA_VERSION == 6, REGENERATE_HINT
    assert sorted(document) == ["cases", "garage_max_distance_m", "garage_option_cases", "garages",
                                "option_price_beta_u_per_eur", "schema_version", "tariffs"], REGENERATE_HINT
    assert document["option_price_beta_u_per_eur"] == 0.310998, REGENERATE_HINT
    assert document["cases"] == [dict(case) for case in GOLDEN_CASES], REGENERATE_HINT
    assert document["garage_option_cases"] == [dict(case) for case in GOLDEN_GARAGE_OPTION_CASES], REGENERATE_HINT
    zones = {zone_id: cost.ZoneTariff(zone_id=zone_id, **fields) for zone_id, fields in document["tariffs"].items()}
    assert zones == fixture_zones, REGENERATE_HINT
    garages = [cost.GarageTariff.from_json(entry) for entry in document["garages"]]
    assert garages == fixture_garages, REGENERATE_HINT
    assert [garage.garage_id for garage in garages] == sorted(garage.garage_id for garage in garages)
    assert document["garage_max_distance_m"] == cost.GARAGE_MAX_DISTANCE_M == 1000.0
    # Re-evaluate every case of the FILE with the file's own tariffs and garages, as the Java test does.
    assert golden_case_mismatches(zones, document["cases"], garages, document["garage_max_distance_m"]) == []
    assert option_case_mismatches(garages, document["garage_option_cases"]) == []


def test_the_golden_cases_carry_exactly_the_documented_keys():
    from braunschweig.parking.golden_cases import CASE_FIELDS, OPTION_CASE_FIELDS
    assert all(list(case) == list(CASE_FIELDS) for case in GOLDEN_CASES)
    assert all(list(case) == list(OPTION_CASE_FIELDS) for case in GOLDEN_GARAGE_OPTION_CASES)
    assert len(CASE_FIELDS) == 20 and len(OPTION_CASE_FIELDS) == 6


def test_an_error_case_must_fail_the_stay_check_not_just_any_check(fixture_zones):
    # G26 is invalid because its departure lies before its arrival. The same stay in a zone the tariffs do not
    # know raises a ValueError too -- but the lookup's, not the stay check's -- and must count as a mismatch.
    g26 = next(case for case in GOLDEN_CASES if case["id"] == "G26")
    assert golden_case_mismatches(fixture_zones, [g26]) == []
    [problem] = golden_case_mismatches(fixture_zones, [{**g26, "zone_id": "fx_unknown"}])
    assert problem.startswith("G26:") and "departure before arrival" in problem and "unknown zone id" in problem


def test_outcomes_are_fifteen_constants_named_as_their_values_with_the_v2_products_appended():
    # Appended at the END, so the position of the ten v1 outcomes in this tuple stays as it was; PAID_EXPECTED (schema 3,
    # the garage expectation) follows them and NO_FEASIBLE_OPTION (spec v3 A1) comes last. The Java ParkingOutcome enum
    # has another order (NO_ZONE first), so the tuple
    # order is no contract with Java: consumers of the outcome report key the rows by outcome name.
    assert cost.OUTCOMES == ("HOME", "EMPLOYER_FREE", "RESIDENT_FREE", "OUTSIDE_FEE_HOURS", "FREE_WITHIN_LIMIT",
                             "PAID_METERED", "PAID_LONG_STAY", "PAID_CAMPUS_MEMBER", "PAID_CAMPUS_GUEST", "NO_ZONE",
                             "PAID_GARAGE", "PAID_COMMUTER", "PAID_EXPECTED", "NO_FEASIBLE_OPTION", "EV_EXEMPT")
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


def test_street_paid_with_max_stay_but_without_long_stay_product_or_garage_is_a_valid_zone():
    # Spec v3 A1 (deliberate change of the v2 rule): a street zone may state a maximum stay without selling a long-stay
    # product and without a zone-level garage; a stay above the maximum then has no street product (street_feasible).
    zone = _tariff(max_stay_min=180)
    assert zone.long_stay_product_cents is None and not zone.has_garage
    assert cost.street_feasible(zone, 180 * 60) and not cost.street_feasible(zone, 180 * 60 + 1)
    # within the maximum the street meters as always: 180 min x 180 ct/h = 540 ct
    assert cost.parking_cost_cents(zone, 36000, 36000 + 10800, **_SHOP) == (540, cost.PAID_METERED)
    # above it no product exists: the stay is priced as the street's metered price with the maximum ignored and named
    assert cost.parking_cost_cents(zone, 36000, 36000 + 10860, **_SHOP) == (543, cost.NO_FEASIBLE_OPTION)
    assert cost.parking_cost_cents(zone, 36000, 36000 + 36000, **_SHOP) == (1800, cost.NO_FEASIBLE_OPTION)
    # the early rules still precede it
    assert cost.parking_cost_cents(zone, 36000, 72000, **{**_SHOP, "parking_free": True}) == (0, cost.EMPLOYER_FREE)
    assert cost.parking_cost_cents(zone, 36000, 72000, **{**_SHOP, "purpose": "home"}) == (0, cost.HOME)
    # a zone with a long-stay product (Ib) is unchanged: the stay buys it
    with_product = _tariff(max_stay_min=180, long_stay_product_cents=900)
    assert cost.parking_cost_cents(with_product, 36000, 36000 + 10860, **_SHOP) == (900, cost.PAID_LONG_STAY)
    # the free threshold is served first, as before
    free_first = _tariff(max_stay_min=180, free_if_stay_at_most_min=30)
    assert cost.parking_cost_cents(free_first, 36000, 36000 + 1500, **_SHOP) == (0, cost.FREE_WITHIN_LIMIT)
    # a resident zone keeps the rule: its maximum stay needs a long-stay product or a garage
    with pytest.raises(ValueError, match="long_stay_product_cents"):
        _tariff("resident_zone", long_stay_product_cents=None)
    # and a long-stay product without a maximum stay is still never sold
    with pytest.raises(ValueError, match="needs max_stay_min"):
        _tariff(long_stay_product_cents=900)


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


def test_no_zone_precedes_the_district_rule():
    # Z1 is decided before every check, the resident district included (spec Amendment C3, ruling of the controller):
    # a district is a second layer that only acts inside a fee zone. Not a golden case, like the home case above.
    assert cost.parking_cost_cents(None, 36000, 37860, purpose="shop", parking_free=False, resident_of_zone=False,
                                   resident_of_district=True) == (0, cost.NO_ZONE)


# Rule R2 (spec Amendment C3, ASSUMPTION, extends R1): a stay is free when the activity lies in the district of the
# person's home and the zone honours resident permits (ASSUMPTION R2-a: resident_permits_valid). The caller compares the
# district ids of the activity and of the person's home and passes the boolean.
@pytest.mark.parametrize("zone_type, expected_without_the_rule, expected_in_the_own_district", [
    ("street_paid", (93, cost.PAID_METERED), (0, cost.RESIDENT_FREE)),
    ("resident_zone", (0, cost.FREE_WITHIN_LIMIT), (0, cost.RESIDENT_FREE)),
    # A campus honours no resident permit (the default of resident_permits_valid there): the stay pays as before.
    ("campus", (350, cost.PAID_CAMPUS_MEMBER), (350, cost.PAID_CAMPUS_MEMBER)),
])
def test_the_district_rule_frees_a_stay_where_the_zone_honours_resident_permits_whatever_it_exempts(
        zone_type, expected_without_the_rule, expected_in_the_own_district):
    # The street zone exempts nobody (resident_exempt False) and the resident zone exempts its own residents only, yet
    # both free a resident of the district; the flag alone changes the result.
    tariff = _tariff(zone_type)
    arrival_s, departure_s = (36000, 37860) if zone_type != "campus" else (28800, 61200)
    purpose = "work" if zone_type == "campus" else "shop"
    flags = {"purpose": purpose, "parking_free": False, "resident_of_zone": False}
    assert cost.parking_cost_cents(tariff, arrival_s, departure_s, **flags) == expected_without_the_rule
    assert cost.parking_cost_cents(tariff, arrival_s, departure_s, **flags, resident_of_district=True) == (
        expected_in_the_own_district)


def test_a_zone_that_does_not_honour_resident_permits_prices_the_stay_in_the_own_district_like_any_other():
    # ASSUMPTION R2-a: a separately operated car park (BgA) is a street_paid row with resident_permits_valid false; the
    # same stay in an otherwise equal zone that honours permits is free.
    own_district = {**_SHOP, "resident_of_district": True}
    assert cost.parking_cost_cents(_tariff(), 36000, 37860, **own_district) == (0, cost.RESIDENT_FREE)
    assert cost.parking_cost_cents(_tariff(resident_permits_valid=False), 36000, 37860, **own_district) == (
        93, cost.PAID_METERED)


def test_the_permit_flag_switches_off_only_the_district_rule_never_the_residents_of_the_zone_itself():
    # R1 reads resident_exempt and resident_of_zone only: a resident zone that does not honour district permits still
    # exempts its own residents (a person living in the zone and in the district), and prices a stranger to it.
    tariff = _tariff("resident_zone", resident_permits_valid=False)
    stay = {"purpose": "work", "parking_free": False}
    assert cost.parking_cost_cents(tariff, 28800, 61200, resident_of_zone=True, resident_of_district=True,
                                   **stay) == (0, cost.RESIDENT_FREE)
    assert cost.parking_cost_cents(tariff, 28800, 61200, resident_of_zone=False, resident_of_district=True,
                                   **stay) == (900, cost.PAID_LONG_STAY)


@pytest.mark.parametrize("base_type, expected", [("street_paid", True), ("resident_zone", True), ("campus", False)])
def test_resident_permits_are_valid_by_default_on_street_and_resident_zones_and_never_on_a_campus(base_type, expected):
    # The default is part of the tariff, so the table, the model JSON and Java read the same value: an unset field is
    # resolved at construction, an explicit value that equals the default is the same tariff.
    assert _tariff(base_type).resident_permits_valid is expected
    assert _tariff(base_type, resident_permits_valid=expected) == _tariff(base_type)
    assert _tariff(base_type).resident_permits_valid is not None


def test_a_street_or_resident_zone_may_state_that_resident_permits_are_not_valid():
    for base_type in ("street_paid", "resident_zone"):
        tariff = _tariff(base_type, resident_permits_valid=False)
        assert tariff.resident_permits_valid is False and tariff != _tariff(base_type)


@pytest.mark.parametrize("base_type, value, message", [
    # A campus car park sells no resident permits (no source states the opposite): true is a configuration error.
    ("campus", True, "resident_permits_valid must be false for zone_type 'campus'"),
    ("street_paid", "false", "resident_permits_valid must be a bool"),
    ("street_paid", 0, "resident_permits_valid must be a bool"),
], ids=["true_on_campus", "text", "number"])
def test_resident_permits_valid_is_validated_when_the_tariff_is_built(base_type, value, message):
    with pytest.raises(ValueError, match=message):
        _tariff(base_type, resident_permits_valid=value)


def test_the_district_rule_comes_after_home_and_employer_free_and_before_the_fee_hours_and_the_products():
    tariff = _tariff(**_GARAGE, commuter_day_cents=376)
    district = {"resident_of_zone": False, "resident_of_district": True}
    # home first (H1), then the employer's free parking, then the district, then the fee window and the products
    assert cost.parking_cost_cents(tariff, 36000, 37860, purpose="home", parking_free=True, **district) == (0, cost.HOME)
    assert cost.parking_cost_cents(tariff, 36000, 37860, purpose="work", parking_free=True, **district) == (
        0, cost.EMPLOYER_FREE)
    assert cost.parking_cost_cents(tariff, 36000, 37860, purpose="work", parking_free=False, **district) == (
        0, cost.RESIDENT_FREE)
    # 20:30-22:00 lies after the street fee window: the district rule still names the stay (R2 precedes the fee hours)
    assert cost.parking_cost_cents(tariff, 73800, 79200, purpose="leisure", parking_free=False, **district) == (
        0, cost.RESIDENT_FREE)
    assert cost.parking_cost_cents(tariff, 73800, 79200, purpose="leisure", parking_free=False,
                                   resident_of_zone=False) == (0, cost.OUTSIDE_FEE_HOURS)


def test_either_resident_rule_alone_frees_the_stay_with_the_one_outcome():
    # R1 (the zone's own residents) and R2 (the district) are separate inputs of one outcome, RESIDENT_FREE
    tariff = _tariff(resident_exempt=True)
    stay = {"purpose": "shop", "parking_free": False}
    assert cost.parking_cost_cents(tariff, 36000, 37860, resident_of_zone=True, resident_of_district=False,
                                   **stay) == (0, cost.RESIDENT_FREE)
    assert cost.parking_cost_cents(tariff, 36000, 37860, resident_of_zone=False, resident_of_district=True,
                                   **stay) == (0, cost.RESIDENT_FREE)
    assert cost.parking_cost_cents(tariff, 36000, 37860, resident_of_zone=False, resident_of_district=False,
                                   **stay) == (93, cost.PAID_METERED)


def test_the_district_flag_defaults_to_false_so_that_every_existing_call_prices_as_before():
    flags = {"purpose": "shop", "parking_free": False, "resident_of_zone": False}
    assert cost.parking_cost_cents(_tariff(), 36000, 37860, **flags) == cost.parking_cost_cents(
        _tariff(), 36000, 37860, **flags, resident_of_district=False) == (93, cost.PAID_METERED)


def test_flags_must_be_booleans_so_a_text_false_is_never_truthy():
    with pytest.raises(TypeError, match="resident_of_district must be a bool"):
        cost.parking_cost_cents(_tariff(), 36000, 37860, purpose="shop", parking_free=False, resident_of_zone=False,
                                resident_of_district="false")
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


# The resident district rule R2 (spec Amendment C3) and its scope R2-a. This table pins the exported R cases against the
# numbers derived by hand here, independently of braunschweig.parking.golden_cases, so that an export with a wrong flag,
# stay or expectation cannot pass silently. Each entry names the result of the same stay WITHOUT the rule (the existing
# golden case, or for R02, R05, R12 and R13 the same result: there the rule moves nothing), the evidence that the rule,
# and nothing else, changes the result. Times of day: 28800 = 08:00, 36000 = 10:00, 61200 = 17:00, 70200 = 19:30,
# 72000 = 20:00 (end of the fee window of fx_bs_ia and fx_bs_ib), 73800 = 20:30.
_DISTRICT_PINS = {
    # id: (zone, purpose, arrival_s, departure_s, free, of_zone, of_district, terminal, L, (cents, outcome), same stay without R2)
    # 31 min x 3 ct = 93 ct (G01) -> free.
    "R01": ("fx_bs_ia", "shop", 36000, 37860, False, False, True, False, 0, (0, "RESIDENT_FREE"), (93, "PAID_METERED")),
    # The flag is false for a stay in another district or in none: 93 ct as G01.
    "R02": ("fx_bs_ia", "shop", 36000, 37860, False, False, False, False, 0, (93, "PAID_METERED"), (93, "PAID_METERED")),
    # fx_bs_ib exempts nobody: 480 min = 1440 ct capped at 900 ct (G03) -> free.
    "R03": ("fx_bs_ib", "work", 28800, 61200, False, False, True, False, 0, (0, "RESIDENT_FREE"), (900, "PAID_METERED")),
    # Not a resident of the resident zone fx_res_a (R1 flag false), but the stay lies in the person's own district:
    # 540 min > max stay 120 -> long-stay product 900 ct (G18) -> free.
    "R04": ("fx_res_a", "work", 28800, 61200, False, False, True, False, 0, (0, "RESIDENT_FREE"), (900, "PAID_LONG_STAY")),
    # A campus honours no resident permit (R2-a): the member day product 350 ct (G20) stays although the flag is true.
    "R05": ("fx_campus", "work", 28800, 61200, False, False, True, False, 0, (350, "PAID_CAMPUS_MEMBER"),
            (350, "PAID_CAMPUS_MEMBER")),
    # Home first (H1): HOME, not RESIDENT_FREE (G05 without the flag is HOME as well).
    "R06": ("fx_bs_ia", "home", 36000, 39600, False, False, True, False, 0, (0, "HOME"), (0, "HOME")),
    # The employer's free parking comes before the district: EMPLOYER_FREE (G04 without the flag).
    "R07": ("fx_bs_ib", "work", 28800, 61200, True, False, True, False, 0, (0, "EMPLOYER_FREE"), (0, "EMPLOYER_FREE")),
    # 20:30-22:00 lies after the fee window 09:00-20:00: the district rule comes first, RESIDENT_FREE instead of
    # OUTSIDE_FEE_HOURS (G06).
    "R08": ("fx_bs_ia", "leisure", 73800, 79200, False, False, True, False, 0, (0, "RESIDENT_FREE"), (0, "OUTSIDE_FEE_HOURS")),
    # Terminal (T1): the departure would be max(70200, 72000) = 72000, 30 min x 3 ct = 90 ct (G07) -> free.
    "R09": ("fx_bs_ia", "shop", 70200, None, False, False, True, True, 0, (0, "RESIDENT_FREE"), (90, "PAID_METERED")),
    # L = 15: the zero-length stay would be priced for 15 min x 3 ct = 45 ct (L01) -> free.
    "R10": ("fx_bs_ia", "shop", 36000, 36000, False, False, True, False, 15, (0, "RESIDENT_FREE"), (45, "PAID_METERED")),
    # L = 15 on a schema-2 row: the cheapest product would be the commuter product 376 ct (V04) -> free.
    "R11": ("fx_bs_ib_v2", "work", 28800, 61200, False, False, True, False, 15, (0, "RESIDENT_FREE"), (376, "PAID_COMMUTER")),
    # fx_bga_v2 does not honour resident permits (R2-a): 31 min x 3 ct = 93 ct as in R01/G01 although the flag is true.
    "R12": ("fx_bga_v2", "shop", 36000, 37860, False, False, True, False, 0, (93, "PAID_METERED"),
            (93, "PAID_METERED")),
    # fx_res_nopermit_v2 does not honour district permits, but a resident of the zone is exempt by R1 (G19): the flag
    # switches off R2 only. Without R1 the 540 min > max stay 120 would pay the long-stay product 900 ct (G18).
    "R13": ("fx_res_nopermit_v2", "work", 28800, 61200, False, True, True, False, 0, (0, "RESIDENT_FREE"),
            (0, "RESIDENT_FREE")),
}


def test_the_exported_district_cases_keep_the_hand_derived_numbers(fixture_zones):
    cases = {case["id"]: case for case in GOLDEN_CASES}
    assert [case_id for case_id in cases if case_id.startswith("R")] == list(_DISTRICT_PINS)
    for case_id, (zone_id, purpose, arrival_s, departure_s, free, of_zone, of_district, terminal, minimum_stay_min,
                  expected, without_the_rule) in _DISTRICT_PINS.items():
        case = cases[case_id]
        assert (case["zone_id"], case["purpose"], case["arrival_s"], case["departure_s"], case["parking_free"],
                case["resident_of_zone"], case["resident_of_district"], case["terminal"], case["minimum_stay_min"],
                case["expected_error"]) == (zone_id, purpose, arrival_s, departure_s, free, of_zone, of_district,
                                            terminal, minimum_stay_min, False), case_id
        assert (case["expected_cents"], case["expected_outcome"]) == expected, case_id
        # The same stay with the flag off gives the result of the existing golden case: the rule alone moved the result
        # (and moved nothing where R2-a switches it off or R1 frees the stay anyway).
        assert evaluate_case({**case, "resident_of_district": False}, fixture_zones) == without_the_rule, case_id


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


# The Java test resources hold byte copies of these two fixtures (eqasim-java-bs, braunschweig/src/test/resources/parking).
# The hashes of the LF blobs are recorded in the ADR of the contract (ADR-0140 up to golden schema 5, ADR-0141 from tariff model
# schema 5 and golden schema 6, spec v3), so regenerating one fixture here without copying it to the Java repository
# fails this test as well as the ADR record, instead of leaving the two sides apart.
PINNED_FIXTURE_SHA256 = {
    "parking_golden_cases.json": "ac635f3b64a56fb7dd60e2abbb457a7efcdf14003efc02739679ed7b95bea080",
    "parking_tariffs_fixture.json": "f51c7341a769c84c25d6dffd11c8ebe3ae2462c9b696eed56773f0ca5967a7cd",
}


@pytest.mark.parametrize("name", sorted(PINNED_FIXTURE_SHA256))
def test_the_fixtures_copied_to_the_java_repository_match_their_pinned_lf_hash(name):
    from braunschweig.parking.tariff_export import content_sha256

    actual = content_sha256(GOLDEN_JSON.parent / name)   # LF-normalised, as git stores the blob
    assert actual == PINNED_FIXTURE_SHA256[name], (
        f"{name} changed (sha256 {actual}): copy it byte for byte to eqasim-java-bs "
        "braunschweig/src/test/resources/parking, then update this pin and the hashes in the ADR of the contract (ADR-0141)")


# --------------------------------------------------------------------- maximum-stay feasibility (spec A1, issue #436)
# A zone with a maximum stay and no long-stay product needs its zone-level garage family to be a valid ZoneTariff (the
# rule of _check_tariff); with garage OPTIONS that family is superseded (E8), so these tests price over the options.
_MAX_STAY_ZONE = {"max_stay_min": 180, **_GARAGE}
_DECAY_M = 400.0


def _option_garage(garage_id="g1", **overrides) -> cost.GarageTariff:
    """A single-window garage: 200 ct per started hour, all day, no cap, no monthly product."""
    fields = {"garage_id": garage_id, "x_m": 0.0, "y_m": 0.0, "hourly_rate_cents": 200, "billing_unit_min": 60,
              "fee_start_s": 0, "fee_end_s": 86400, **overrides}
    return cost.GarageTariff(**fields)


def _detail(zone, minutes, options, *, purpose="shop", counters=None, **flags):
    flags = {"parking_free": False, "resident_of_zone": False, **flags}
    return cost.parking_cost_with_garages_detail(zone, 32400, 32400 + minutes * 60, purpose=purpose,
                                                 garage_options=options, decay_m=_DECAY_M, decay_commute_m=_DECAY_M,
                                                 counters=counters, **flags)


def test_the_no_feasible_option_outcome_is_appended_after_paid_expected():
    assert cost.NO_FEASIBLE_OPTION == "NO_FEASIBLE_OPTION"
    assert cost.OUTCOMES[-2] == cost.NO_FEASIBLE_OPTION and cost.OUTCOMES[-3] == cost.PAID_EXPECTED
    assert len(cost.OUTCOMES) == len(set(cost.OUTCOMES)) == 15


def test_street_is_infeasible_above_the_maximum_stay_without_a_product():
    zone = _tariff(**_MAX_STAY_ZONE)
    assert cost.street_feasible(zone, 180 * 60) is True
    assert cost.street_feasible(zone, 180 * 60 + 1) is False      # 181 started minutes: chargeable minutes round up
    assert cost.street_feasible(zone, 200 * 60) is False
    # with the 9.00 EUR long-stay product the stay buys it, as before (M1)
    with_product = _tariff(max_stay_min=180, long_stay_product_cents=900)
    assert cost.street_feasible(with_product, 200 * 60) is True
    assert cost.parking_cost_cents(with_product, 32400, 32400 + 200 * 60, **_SHOP) == (900, cost.PAID_LONG_STAY)
    # no maximum: always feasible
    assert cost.street_feasible(_tariff(), 600 * 60) is True


def test_a_garage_with_a_maximum_stay_leaves_the_option_set():
    zone = _tariff(**_MAX_STAY_ZONE)
    short_stay_only = _option_garage("g_short", max_stay_min=120)
    unlimited = _option_garage("g_open")
    assert cost.option_feasible(short_stay_only, 120 * 60) is True
    assert cost.option_feasible(short_stay_only, 120 * 60 + 1) is False    # the ELAPSED stay rounds up to 121 min
    assert cost.option_feasible(unlimited, 10 ** 6) is True
    detail = _detail(zone, 150, [(short_stay_only, 100.0), (unlimited, 100.0)])
    assert detail.feasible_option_ids == ("g_open",) and detail.street_feasible is True
    # 150 min: the street meters 450 ct; the only garage priced is g_open (3 started hours = 600 ct), so the weights are
    # the street's 1 and g_open's exp(-100 / 400)
    weight = math.exp(-100 / _DECAY_M)
    assert detail.expected_cents == pytest.approx((450 + weight * 600) / (1 + weight), abs=1e-9)
    # a stay within the garage maximum keeps both garages
    assert _detail(zone, 100, [(short_stay_only, 100.0), (unlimited, 100.0)]).feasible_option_ids == ("g_open", "g_short")


def test_the_garage_maximum_compares_the_elapsed_stay_not_the_chargeable_minutes():
    # The garage fee window ends at 10:00: a stay 09:00-13:00 has 60 chargeable minutes but 240 elapsed minutes; the
    # garage maximum of 180 min is exceeded by the elapsed stay.
    garage = _option_garage(fee_end_s=36000, max_stay_min=180)
    assert cost.option_feasible(garage, 240 * 60) is False
    assert cost.option_feasible(garage, 180 * 60) is True


def test_a_stay_with_no_feasible_option_is_priced_as_the_street_and_named():
    zone = _tariff(**_MAX_STAY_ZONE)
    garage = _option_garage("g_short", max_stay_min=60)
    counters = cost.GarageOptionCounters()
    detail = _detail(zone, 300, [(garage, 100.0)], counters=counters)
    # 300 chargeable min x 180 ct/h = 900 ct: the street's metered price, the maximum ignored
    assert (detail.cents, detail.outcome) == (900, cost.NO_FEASIBLE_OPTION)
    assert detail.feasible_option_ids == () and detail.street_feasible is False
    assert detail.garage_probability == 0.0 and detail.expected_cents is None
    assert counters.no_feasible_option == 1 and counters.street_unavailable == 1 and counters.options_infeasible == 1
    assert "(NO_FEASIBLE_OPTION) 1/1 (100.0 %)" in counters.summary()
    assert cost.parking_cost_with_garages(zone, 32400, 32400 + 300 * 60, purpose="shop", parking_free=False,
                                          resident_of_zone=False, garage_options=[(garage, 100.0)], decay_m=_DECAY_M,
                                          decay_commute_m=_DECAY_M) == (900, cost.NO_FEASIBLE_OPTION, 0.0)


def test_an_infeasible_street_with_a_feasible_garage_is_priced_over_the_garage_alone():
    zone = _tariff(**_MAX_STAY_ZONE)
    detail = _detail(zone, 300, [(_option_garage("g_short", max_stay_min=60), 100.0), (_option_garage("g_open"), 700.0)])
    assert detail.street_feasible is False and detail.feasible_option_ids == ("g_open",)
    # 300 min = 5 started hours = 1000 ct, the only option
    assert (detail.cents, detail.outcome, detail.garage_probability) == (1000, cost.PAID_EXPECTED, 1.0)


def test_a_street_alone_after_the_garages_leave_the_set_prices_as_the_street():
    zone = _tariff(**_MAX_STAY_ZONE)
    detail = _detail(zone, 150, [(_option_garage("g_short", max_stay_min=60), 100.0)])
    assert (detail.cents, detail.outcome, detail.garage_probability) == (450, cost.PAID_METERED, 0.0)
    assert detail.street_feasible is True and detail.feasible_option_ids == ()


def test_feasibility_precedes_the_mixture_but_follows_the_early_rules():
    zone = _tariff(**_MAX_STAY_ZONE)
    options = [(_option_garage("g_short", max_stay_min=60), 100.0)]
    # 300 min exceed the zone and the garage maximum; every early rule still decides first and prices 0 as without it
    for flags, outcome in (({"purpose": "home"}, cost.HOME), ({"parking_free": True}, cost.EMPLOYER_FREE),
                           ({"resident_of_district": True}, cost.RESIDENT_FREE)):
        detail = _detail(zone, 300, options, **flags)
        assert (detail.cents, detail.outcome) == (0, outcome)
    # outside the street fee window (21:00-23:00) nothing is chargeable
    outside = cost.parking_cost_with_garages_detail(zone, 75600, 75600 + 7200, purpose="shop", parking_free=False,
                                                    resident_of_zone=False, garage_options=options, decay_m=_DECAY_M,
                                                    decay_commute_m=_DECAY_M)
    assert (outside.cents, outside.outcome) == (0, cost.OUTSIDE_FEE_HOURS)
    # a campus stay is priced as before whatever the garage maximum is
    assert _detail(_tariff("campus"), 300, options).outcome == cost.PAID_CAMPUS_GUEST
    # no zone: free
    assert cost.parking_cost_with_garages_detail(None, 32400, 50400, purpose="shop", parking_free=False,
                                                 resident_of_zone=False, garage_options=options, decay_m=_DECAY_M,
                                                 decay_commute_m=_DECAY_M).outcome == cost.NO_ZONE


def test_a_free_street_still_pays_nothing_whatever_the_garage_maximum_e4():
    zone = _tariff(free_if_stay_at_most_min=30)
    detail = _detail(zone, 25, [(_option_garage(max_stay_min=10), 0.0)])
    assert (detail.cents, detail.outcome) == (0, cost.FREE_WITHIN_LIMIT)


def test_a_model_with_no_maximum_stay_prices_as_before_and_lists_every_garage():
    # the numbers of the two-garage case of test_parking_garage_options: street 180 ct, garages 200 ct (300 m) and 100 ct
    # (700 m) at lambda 400 m give 177 ct
    zone = _tariff()
    options = [(_option_garage("g_b"), 300.0), (_option_garage("g_a", hourly_rate_cents=100), 700.0)]
    detail = _detail(zone, 60, options)
    assert (detail.cents, detail.outcome) == (177, cost.PAID_EXPECTED)
    assert detail.street_feasible is True and detail.feasible_option_ids == ("g_a", "g_b")


def test_a_garage_maximum_stay_is_exported_by_the_schema_5_entry_and_read_back():
    # tariff model schema 5 (spec v3 A1): max_stay_min is the last key of the garage entry, null = no maximum; a maximum is
    # never dropped (the A1 interim guard that refused to export one is gone) and the strict reader accepts exactly this key set
    assert cost.GARAGE_FIELDS_JSON[-1] == "max_stay_min"
    assert list(_option_garage().to_json()) == list(cost.GARAGE_FIELDS_JSON)
    assert _option_garage().to_json()["max_stay_min"] is None
    entry = _option_garage("g_short", max_stay_min=120).to_json()
    assert entry["max_stay_min"] == 120
    assert cost.GarageTariff.from_json(entry) == _option_garage("g_short", max_stay_min=120)
    with pytest.raises(ValueError, match="differ from the documented"):
        cost.GarageTariff.from_json({key: value for key, value in entry.items() if key != "max_stay_min"})


def test_a_garage_maximum_stay_is_validated():
    for bad in (0, -5, 1.5, True):
        with pytest.raises(ValueError, match="max_stay_min"):
            _option_garage(max_stay_min=bad)


def test_street_feasible_is_exactly_the_condition_under_which_the_street_product_exists():
    zones = (_tariff(**_MAX_STAY_ZONE), _tariff(max_stay_min=180, long_stay_product_cents=900), _tariff(),
             _tariff(free_if_stay_at_most_min=30, **_MAX_STAY_ZONE))
    for zone in zones:
        for chargeable_min in (1, 30, 31, 179, 180, 181, 600):
            chargeable_s = chargeable_min * 60
            assert cost.street_feasible(zone, chargeable_s) == (cost._street_product(zone, chargeable_s) is not None)


def test_a_stay_without_a_feasible_option_and_without_a_garage_in_range_is_counted_too():
    # Zone maximum 180 min, no long-stay product, no zone-level garage, no garage in range: 300 min have no option at all.
    zone = _tariff(max_stay_min=180)
    counters = cost.GarageOptionCounters()
    detail = _detail(zone, 300, [], counters=counters)
    assert (detail.cents, detail.outcome, detail.street_feasible) == (900, cost.NO_FEASIBLE_OPTION, False)
    assert counters.no_feasible_option == 1 and counters.eligible == 1 and counters.with_garages_in_range == 0
    # the rate is over the eligible stays, so these stays are inside its denominator
    assert "(NO_FEASIBLE_OPTION) 1/1 (100.0 %)" in counters.summary()
    # a stay within the maximum is no such case; a zone whose garage family serves the stay is none either
    assert _detail(zone, 150, [], counters=counters).outcome == cost.PAID_METERED
    assert counters.no_feasible_option == 1 and counters.eligible == 2
    family = _tariff(**_MAX_STAY_ZONE)
    assert _detail(family, 300, [], counters=counters).outcome == cost.PAID_GARAGE
    assert counters.no_feasible_option == 1


# ------------------------------------------------------------- price in the option weights (spec v3 A2, issue #436)
# w_i = exp(-d_i / lambda - beta * c_i / 100), c_i in cents, beta in utility per EUR (the magnitude of the mode-choice
# money coefficient, ASSUMPTION G1-d); the street is the option at distance 0 with its own price. beta 0 is the v2 form.
_BETA_U_PER_EUR = 0.31


def _priced_detail(zone, minutes, options, *, beta, purpose="shop", **flags):
    flags = {"parking_free": False, "resident_of_zone": False, **flags}
    return cost.parking_cost_with_garages_detail(zone, 32400, 32400 + minutes * 60, purpose=purpose,
                                                 garage_options=options, decay_m=_DECAY_M, decay_commute_m=_DECAY_M,
                                                 option_price_beta_u_per_eur=beta, **flags)


def test_option_weights_fall_with_price_at_equal_distance():
    zone = _tariff()
    dear = _option_garage("g_dear", hourly_rate_cents=200)      # 1 h stay: 200 ct = 2.00 EUR
    cheap = _option_garage("g_cheap", hourly_rate_cents=100)    # 1 h stay: 100 ct = 1.00 EUR
    detail = _priced_detail(zone, 60, [(dear, 300.0), (cheap, 300.0)], beta=_BETA_U_PER_EUR)
    assert detail.feasible_option_ids == ("g_cheap", "g_dear")
    weight_cheap, weight_dear = detail.option_weights
    assert weight_cheap > weight_dear
    assert weight_cheap / weight_dear == pytest.approx(math.exp(_BETA_U_PER_EUR * 1.0), rel=1e-12)
    # hand-derived absolute values: exp(-300 / 400 - 0.31 * EUR)
    assert weight_cheap == pytest.approx(math.exp(-0.75 - 0.31 * 1.0), rel=1e-12)
    assert weight_dear == pytest.approx(math.exp(-0.75 - 0.31 * 2.0), rel=1e-12)


def test_the_street_weight_carries_its_own_price():
    # 180 ct per hour, 1-minute billing: 09:00-12:00 costs 540 ct = 5.40 EUR on the street
    zone = _tariff()
    garage = _option_garage("g1", hourly_rate_cents=200)         # 3 h: 600 ct
    detail = _priced_detail(zone, 180, [(garage, 100.0)], beta=_BETA_U_PER_EUR)
    assert detail.street_weight == pytest.approx(math.exp(-0.31 * 5.4), rel=1e-12)
    garage_weight = math.exp(-100 / 400 - 0.31 * 6.0)
    assert detail.option_weights == pytest.approx((garage_weight,), rel=1e-12)
    expected = (540 * detail.street_weight + 600 * garage_weight) / (detail.street_weight + garage_weight)
    assert detail.expected_cents == pytest.approx(expected, rel=1e-12)
    assert detail.cents == int(math.floor(expected + 0.5)) and detail.outcome == cost.PAID_EXPECTED
    # a free street costs 0 ct and keeps the weight 1 (the weight function, the street at distance 0)
    assert cost.option_weight(0.0, _DECAY_M, 0, _BETA_U_PER_EUR) == 1.0
    assert cost.option_weight(0.0, _DECAY_M, 540, _BETA_U_PER_EUR) == pytest.approx(math.exp(-0.31 * 5.4), rel=1e-12)


def test_a_free_option_has_weight_exp_minus_distance_only():
    assert cost.option_weight(200.0, 400.0, 0, _BETA_U_PER_EUR) == math.exp(-200.0 / 400.0)
    free_garage = _option_garage("g_free", fee_start_s=0, fee_end_s=3600)    # the stay lies outside its fee window
    detail = _priced_detail(_tariff(), 60, [(free_garage, 200.0)], beta=_BETA_U_PER_EUR)
    assert detail.option_weights == (math.exp(-200.0 / 400.0),)


def test_beta_zero_is_the_v2_weight_bit_for_bit():
    for distance_m, decay_m, cents in ((0.0, 400.0, 0), (123.456, 400.0, 540), (999.9, 250.0, 12345), (1e-9, 3.0, 1)):
        assert cost.option_weight(distance_m, decay_m, cents, 0.0) == math.exp(-distance_m / decay_m)
    zone = _tariff()
    options = [(_option_garage("g_a", hourly_rate_cents=200), 120.0), (_option_garage("g_b", hourly_rate_cents=100), 640.0)]
    detail = _priced_detail(zone, 180, options, beta=0.0)
    assert detail.street_weight == 1.0
    assert detail.option_weights == (math.exp(-120.0 / 400.0), math.exp(-640.0 / 400.0))
    # the default (no keyword) is beta 0
    assert _detail(zone, 180, options) == detail


def test_beta_zero_reproduces_the_schema_5_golden_cases(monkeypatch):
    # Every case of the schema-5 families (G, L, V, R, E) of the COMMITTED golden file (schema 6) carries beta 0 and no EV
    # exemption and is reproduced with beta 0 passed explicitly through the new path: the price term changes no v2 value (the
    # keyword is really used: the wrapper records every call). Their content is unchanged since schema 5 (the prefix digests
    # in tests/test_parking_golden_cases.py).
    document = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    assert document["schema_version"] == 6
    zones = {zone_id: cost.ZoneTariff(zone_id=zone_id, **fields) for zone_id, fields in document["tariffs"].items()}
    garages = [cost.GarageTariff.from_json(entry) for entry in document["garages"]]
    schema_5_cases = [case for case in document["cases"] if case["id"][0] in "GLVRE"]
    assert len(schema_5_cases) == 128
    assert all(case["option_price_beta_u_per_eur"] == 0.0 and case["ev_exempt"] is False for case in schema_5_cases)
    original = cost.parking_cost_with_garages_detail
    betas = []

    def with_explicit_beta_zero(*args, **kwargs):
        betas.append(kwargs["option_price_beta_u_per_eur"])
        return original(*args, **kwargs)

    monkeypatch.setattr(cost, "parking_cost_with_garages_detail", with_explicit_beta_zero)
    assert golden_case_mismatches(zones, schema_5_cases, garages, document["garage_max_distance_m"]) == []
    assert len(betas) == len(schema_5_cases) - 1 and set(betas) == {0.0}      # G26 raises before pricing
    # the golden cases that form a mixture are among them (the weights did act)
    assert any(case["expected_outcome"] == cost.PAID_EXPECTED for case in schema_5_cases)


def test_a_positive_beta_moves_probability_to_the_cheaper_option():
    zone = _tariff()
    options = [(_option_garage("g_a", hourly_rate_cents=100), 300.0)]       # 3 h: 300 ct against the street's 540 ct
    cheap_garage_share = {}
    for beta in (0.0, 0.31, 1.0):
        cheap_garage_share[beta] = _priced_detail(zone, 180, options, beta=beta).garage_probability
    assert cheap_garage_share[0.0] < cheap_garage_share[0.31] < cheap_garage_share[1.0]
    # an expensive garage loses probability instead
    dear = [(_option_garage("g_a", hourly_rate_cents=400), 300.0)]           # 3 h: 1200 ct
    with_price = _priced_detail(zone, 180, dear, beta=0.31).garage_probability
    assert with_price < _priced_detail(zone, 180, dear, beta=0.0).garage_probability


def test_an_infeasible_street_is_not_weighted_and_an_infeasible_garage_is_dropped():
    zone = _tariff(**_MAX_STAY_ZONE)       # maximum stay 180 min, no long-stay product: the street is infeasible at 200
    short = _option_garage("g_short", max_stay_min=120)
    open_garage = _option_garage("g_open", hourly_rate_cents=200)
    detail = _priced_detail(zone, 200, [(short, 50.0), (open_garage, 100.0)], beta=_BETA_U_PER_EUR)
    assert detail.street_feasible is False and detail.feasible_option_ids == ("g_open",)
    assert detail.street_weight == 0.0                   # no street option: no weight, no probability
    assert detail.option_weights == (math.exp(-100 / 400 - 0.31 * 8.0),)        # 200 min = 4 started hours: 800 ct
    assert detail.garage_probability == pytest.approx(1.0, rel=1e-12)
    assert detail.expected_cents == pytest.approx(800.0, rel=1e-12)


def test_the_weights_are_formed_only_where_a_mixture_is_formed():
    # a street free of charge (E4) and a stay decided before the options act form no mixture: no weights are reported
    free_street = _tariff(free_if_stay_at_most_min=30)
    detail = _priced_detail(free_street, 20, [(_option_garage("g1"), 100.0)], beta=_BETA_U_PER_EUR)
    assert detail.expected_cents is None and detail.street_weight == 0.0 and detail.option_weights == ()
    early = cost.parking_cost_with_garages_detail(None, 32400, 36000, purpose="shop", parking_free=False,
                                                  resident_of_zone=False, garage_options=[], decay_m=_DECAY_M,
                                                  decay_commute_m=_DECAY_M, option_price_beta_u_per_eur=0.31)
    assert early.option_weights == () and early.street_weight == 0.0


def test_the_price_coefficient_is_validated():
    zone, options = _tariff(), [(_option_garage("g1"), 100.0)]
    for bad in (-0.1, float("nan"), float("inf"), -float("inf"), True, "0.31", None):
        with pytest.raises(ValueError, match="option_price_beta_u_per_eur"):
            _priced_detail(zone, 60, options, beta=bad)
        with pytest.raises(ValueError, match="option_price_beta_u_per_eur"):
            cost.parking_cost_with_garages(zone, 32400, 36000, purpose="shop", parking_free=False,
                                           resident_of_zone=False, garage_options=options, decay_m=_DECAY_M,
                                           decay_commute_m=_DECAY_M, option_price_beta_u_per_eur=bad)
    # an int is a number; 0 is the v2 form
    assert (_priced_detail(zone, 60, options, beta=0).option_weights
            == _priced_detail(zone, 60, options, beta=0.0).option_weights)
    # the validation does not depend on the stay reaching the weights: an early rule rejects a bad coefficient too
    with pytest.raises(ValueError, match="option_price_beta_u_per_eur"):
        cost.parking_cost_with_garages_detail(None, 32400, 36000, purpose="shop", parking_free=False,
                                              resident_of_zone=False, decay_m=_DECAY_M, decay_commute_m=_DECAY_M,
                                              option_price_beta_u_per_eur=-1.0)


def test_the_wrapper_passes_the_price_coefficient_through():
    zone = _tariff()
    options = [(_option_garage("g_a", hourly_rate_cents=100), 300.0)]
    for beta in (0.0, 0.31):
        detail = _priced_detail(zone, 180, options, beta=beta)
        wrapped = cost.parking_cost_with_garages(zone, 32400, 32400 + 180 * 60, purpose="shop", parking_free=False,
                                                 resident_of_zone=False, garage_options=options, decay_m=_DECAY_M,
                                                 decay_commute_m=_DECAY_M, option_price_beta_u_per_eur=beta)
        assert wrapped == (detail.cents, detail.outcome, detail.garage_probability)


# ------------------------------------------------------------------------------- EV exemption (spec v3 A5, issue #436)
# ASSUMPTION E1 (owner): every BEV/PHEV counts as an E-plate holder; ASSUMPTION E2: the exemption applies within the maximum
# stay of the zone. The street zone of _tariff() charges 180 ct per hour in 1-minute units (3 ct per minute) from 09:00
# to 20:00, so a stay from 09:00 of m minutes costs 3 * m ct.
_EV_STAY_START_S = 32400


def _ev_price(zone, minutes, *, ev_exempt=True, **flags):
    flags = {**_SHOP, **flags}
    return cost.parking_cost_cents(zone, _EV_STAY_START_S, _EV_STAY_START_S + minutes * 60, ev_exempt=ev_exempt, **flags)


def test_ev_exempt_is_a_free_outcome_appended_at_the_end_of_the_outcomes():
    assert cost.EV_EXEMPT == "EV_EXEMPT"
    assert cost.OUTCOMES[-1] == cost.EV_EXEMPT and cost.OUTCOMES[-2] == cost.NO_FEASIBLE_OPTION
    assert len(cost.OUTCOMES) == len(set(cost.OUTCOMES)) == 15


def test_ev_exempt_stay_within_the_limit_costs_nothing():
    zone = _tariff(ev_free_minutes=240)
    assert _ev_price(zone, 90) == (0, cost.EV_EXEMPT)
    assert _ev_price(zone, 240) == (0, cost.EV_EXEMPT)          # the limit itself is inside
    assert _ev_price(zone, 1) == (0, cost.EV_EXEMPT)
    # chargeable minutes, seconds rounded up: 240 min and one second is 241 started minutes
    assert cost.parking_cost_cents(zone, _EV_STAY_START_S, _EV_STAY_START_S + 240 * 60 + 1, ev_exempt=True,
                                   **_SHOP) == (723, cost.PAID_METERED)


def test_ev_exemption_is_not_a_deduction():
    zone = _tariff(ev_free_minutes=240)
    # 241 min: the full metered price (3 ct per minute), not the price of the minutes above the limit
    assert _ev_price(zone, 241) == (723, cost.PAID_METERED)
    assert _ev_price(zone, 241) == _ev_price(zone, 241, ev_exempt=False)


def test_a_non_exempt_person_is_unchanged():
    with_rule, without_rule = _tariff(ev_free_minutes=240), _tariff()
    assert _ev_price(with_rule, 90, ev_exempt=False) == (270, cost.PAID_METERED)
    # an exempt person in a zone without the rule pays as everybody does; so does the default of the keyword
    assert _ev_price(without_rule, 90, ev_exempt=True) == (270, cost.PAID_METERED)
    assert cost.parking_cost_cents(with_rule, _EV_STAY_START_S, _EV_STAY_START_S + 90 * 60,
                                   **_SHOP) == (270, cost.PAID_METERED)


def test_the_ev_exemption_comes_after_the_early_rules_and_before_the_street_product():
    zone = _tariff(ev_free_minutes=240, free_if_stay_at_most_min=30, resident_exempt=True)
    assert _ev_price(zone, 20) == (0, cost.EV_EXEMPT)                                   # before the free threshold
    assert _ev_price(zone, 20, ev_exempt=False) == (0, cost.FREE_WITHIN_LIMIT)
    assert _ev_price(zone, 90, parking_free=True) == (0, cost.EMPLOYER_FREE)
    assert _ev_price(zone, 90, resident_of_zone=True) == (0, cost.RESIDENT_FREE)
    assert _ev_price(zone, 90, resident_of_district=True) == (0, cost.RESIDENT_FREE)           # the R2 district path
    assert _ev_price(zone, 90, purpose="home") == (0, cost.HOME)
    assert _ev_price(None, 90) == (0, cost.NO_ZONE)
    # no chargeable second in the street fee window: the early rule names it, not the exemption
    assert cost.parking_cost_cents(zone, 3600, 7200, ev_exempt=True, **_SHOP) == (0, cost.OUTSIDE_FEE_HOURS)


def test_the_ev_exemption_applies_within_the_maximum_stay_only_assumption_e2():
    # Salzgitter: the exemption (240 min) exceeds the maximum stay (150 min); ASSUMPTION E2 limits it to the maximum.
    zone = _tariff(ev_free_minutes=240, max_stay_min=150, long_stay_product_cents=900)
    assert _ev_price(zone, 150) == (0, cost.EV_EXEMPT)
    assert _ev_price(zone, 151) == (900, cost.PAID_LONG_STAY)
    assert _ev_price(zone, 200) == _ev_price(zone, 200, ev_exempt=False) == (900, cost.PAID_LONG_STAY)


def test_ev_exempt_must_be_a_bool_and_ev_free_minutes_a_positive_integer_on_a_street_zone():
    zone = _tariff(ev_free_minutes=240)
    for bad in ("true", 1, None):
        with pytest.raises(TypeError, match="ev_exempt must be a bool"):
            _ev_price(zone, 90, ev_exempt=bad)
    for bad in (0, -5, 2.5, True, "240"):
        with pytest.raises(ValueError, match="ev_free_minutes"):
            _tariff(ev_free_minutes=bad)
    for zone_type in ("resident_zone", "campus"):
        with pytest.raises(ValueError, match="ev_free_minutes does not apply to zone_type"):
            _tariff(zone_type, ev_free_minutes=240)
    assert _tariff().ev_free_minutes is None


def test_the_ev_exemption_is_free_on_the_street_side_with_garage_options_and_counts_like_e4():
    zone = _tariff(ev_free_minutes=240)
    options = [(_option_garage("g_a", hourly_rate_cents=100), 300.0)]
    counters = cost.GarageOptionCounters()
    detail = _detail(zone, 200, options, counters=counters, ev_exempt=True)
    assert (detail.cents, detail.outcome, detail.garage_probability) == (0, cost.EV_EXEMPT, 0.0)
    assert detail.expected_cents is None and detail.street_feasible is True
    assert detail.feasible_option_ids == ("g_a",)
    assert counters.street_free == 1 and counters.expected == 0
    # a longer stay prices as today, as an expectation over the street and the garage
    longer = _detail(zone, 241, options, ev_exempt=True)
    assert longer == _detail(zone, 241, options, ev_exempt=False) and longer.outcome == cost.PAID_EXPECTED
    # the person who is not exempt pays the expectation also within the limit
    assert _detail(zone, 200, options, ev_exempt=False).outcome == cost.PAID_EXPECTED
    wrapped = cost.parking_cost_with_garages(zone, _EV_STAY_START_S, _EV_STAY_START_S + 200 * 60, purpose="shop",
                                             parking_free=False, resident_of_zone=False, garage_options=options,
                                             decay_m=_DECAY_M, decay_commute_m=_DECAY_M, ev_exempt=True)
    assert wrapped == (0, cost.EV_EXEMPT, 0.0)


def test_the_ev_exemption_without_garage_options_and_with_decay_zero_prices_as_the_street_function():
    zone = _tariff(ev_free_minutes=240)
    assert _detail(zone, 200, [], ev_exempt=True)[:2] == (0, cost.EV_EXEMPT)
    detail = cost.parking_cost_with_garages_detail(zone, _EV_STAY_START_S, _EV_STAY_START_S + 200 * 60, purpose="shop",
                                                   parking_free=False, resident_of_zone=False, decay_m=0.0,
                                                   decay_commute_m=0.0, ev_exempt=True)
    assert (detail.cents, detail.outcome) == (0, cost.EV_EXEMPT)
    with pytest.raises(TypeError, match="ev_exempt must be a bool"):
        _detail(zone, 200, [], ev_exempt="yes")


def test_a_stay_whose_option_weights_all_underflow_raises_a_named_error_not_a_zero_division():
    # Spec v3 A2 (deferred minor of the A2 review): with the street infeasible (maximum stay 180 min, no product) the weights
    # of the garages alone are summed, and a decay of 1 m at 1000 m gives exp(-1000) = 0.0 in double precision, so the sum is
    # 0.0 and the probabilities w / sum(w) are undefined. The pricing refuses the stay and names the garages and the stay;
    # a bare ZeroDivisionError told nothing, and v2 had the same hole for the distance-only weights.
    zone = _tariff(**_MAX_STAY_ZONE)
    far_garage = _option_garage("g_far")
    assert math.exp(-1000.0 / 1.0) == 0.0
    with pytest.raises(ValueError, match=r"all option weights underflow.*g_far.*decay"):
        cost.parking_cost_with_garages_detail(zone, 32400, 32400 + 200 * 60, purpose="shop", parking_free=False,
                                              resident_of_zone=False, garage_options=[(far_garage, 1000.0)], decay_m=1.0,
                                              decay_commute_m=1.0)
    # the same garage with a usable decay length prices normally (the guard is not a fallback)
    detail = cost.parking_cost_with_garages_detail(zone, 32400, 32400 + 200 * 60, purpose="shop", parking_free=False,
                                                   resident_of_zone=False, garage_options=[(far_garage, 1000.0)],
                                                   decay_m=400.0, decay_commute_m=400.0)
    assert detail.outcome == cost.PAID_EXPECTED


# ------------------------------------------------------------- street_feasible on the decay 0 path (parity with Java, A8)


def _decay_zero_detail(zone, minutes, *, purpose="shop", **flags):
    """The detail of a stay priced with both decay lengths 0 (the garage options off) and no garage in range."""
    flags = {"parking_free": False, "resident_of_zone": False, **flags}
    return cost.parking_cost_with_garages_detail(zone, 32400, 32400 + minutes * 60, purpose=purpose, decay_m=0.0,
                                                 decay_commute_m=0.0, **flags)


def test_street_feasible_means_the_street_side_can_serve_the_stay_also_with_the_garage_options_off():
    # Documented meaning (GarageStayPrice, Java GarageParkingCostCalculator.Result): the street product, the long-stay
    # product, the zone commuter product or the EV exemption can serve the stay. It must not depend on the decay length.
    no_product = _tariff(max_stay_min=180)
    detail = _decay_zero_detail(no_product, 300)
    assert detail.street_feasible is False and detail.outcome == cost.NO_FEASIBLE_OPTION and detail.cents == 900
    # within the maximum stay the street serves it
    within = _decay_zero_detail(no_product, 150)
    assert within.street_feasible is True and (within.cents, within.outcome) == (450, cost.PAID_METERED)
    # without a maximum stay the street always serves it
    assert _decay_zero_detail(_tariff(), 600).street_feasible is True


def test_the_long_stay_product_commuter_product_and_ev_exemption_keep_the_street_side_alive_with_the_options_off():
    long_stay = _tariff(max_stay_min=180, long_stay_product_cents=900)
    detail = _decay_zero_detail(long_stay, 300)
    assert detail.street_feasible is True and (detail.cents, detail.outcome) == (900, cost.PAID_LONG_STAY)
    commuter = _tariff(max_stay_min=180, commuter_day_cents=700)
    work = _decay_zero_detail(commuter, 300, purpose="work")
    assert work.street_feasible is True and (work.cents, work.outcome) == (700, cost.PAID_COMMUTER)
    # the same zone for a purpose without the commuter product has no street side above the maximum
    assert _decay_zero_detail(commuter, 300).street_feasible is False
    ev = _decay_zero_detail(_tariff(ev_free_minutes=240, max_stay_min=150), 90, ev_exempt=True)
    assert ev.street_feasible is True and (ev.cents, ev.outcome) == (0, cost.EV_EXEMPT)


def test_a_zone_level_garage_product_prices_the_stay_but_is_no_street_side_with_the_options_off():
    # _MAX_STAY_ZONE sells a zone-level garage product above the maximum stay: the stay is priced PAID_GARAGE, but the
    # street side cannot serve it (the garage family is superseded by the options, spec Amendment E8), as in Java.
    detail = _decay_zero_detail(_tariff(**_MAX_STAY_ZONE), 300)
    assert detail.outcome == cost.PAID_GARAGE and detail.street_feasible is False


def test_street_feasible_is_the_same_with_the_options_off_as_with_an_unused_option_set():
    # The flag describes the street side, so switching the options on without a garage in range must not change it.
    zones = [_tariff(max_stay_min=180), _tariff(max_stay_min=180, long_stay_product_cents=900), _tariff(),
             _tariff(max_stay_min=180, commuter_day_cents=700), _tariff(**_MAX_STAY_ZONE)]
    for zone in zones:
        for minutes in (60, 180, 181, 300):
            for purpose in ("shop", "work"):
                off = _decay_zero_detail(zone, minutes, purpose=purpose)
                on = cost.parking_cost_with_garages_detail(zone, 32400, 32400 + minutes * 60, purpose=purpose,
                                                           parking_free=False, resident_of_zone=False, decay_m=_DECAY_M,
                                                           decay_commute_m=_DECAY_M)
                assert off.street_feasible is on.street_feasible, (zone, minutes, purpose)
                assert (off.cents, off.outcome) == (on.cents, on.outcome)
