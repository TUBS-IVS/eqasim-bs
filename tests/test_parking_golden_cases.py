"""Schema 6 of the golden parking cases (spec v3 A1, A2, A5, issue #436, Task A6): the families F (maximum-stay feasibility),
P (the price in the option weights), X (the EV exemption) and S (the release band shapes), and the proof that the schema-5
cases did not change.

The hand derivations are in the comments of ``braunschweig.parking.golden_cases``; the numbers below are typed in a second time,
independently, together with the inputs of every case (the double implementation of ``test_parking_garage_calibration``), and the
mixtures are recomputed here with plain ``math`` so that neither the rows nor ``cost.py`` is the only witness of a number.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from braunschweig.parking import cost
from braunschweig.parking import golden_cases as gc

GOLDEN_JSON = Path(__file__).resolve().parent / "fixtures" / "parking" / "parking_golden_cases.json"
FIXTURE_JSON = GOLDEN_JSON.with_name("parking_tariffs_fixture.json")
FIXTURE_CSV = GOLDEN_JSON.with_name("parking_tariffs_fixture.csv")
BETA = 0.310998     # the magnitude of BraunschweigModeParameters.betaCost_u_MU (eqasim-java-bs)
LAMBDA_M, LAMBDA_COMMUTE_M = 400.0, 250.0

# Destinations (EPSG:25832 metres) of the schema-6 cases, written out again.
D20, D21, D22, D23, D24 = ((580000.0, 5780000.0), (590000.0, 5780000.0), (600000.0, 5780000.0), (610000.0, 5780000.0),
                           (620000.0, 5780000.0))
D26, D27, D28, D30 = (630000.0, 5780000.0), (640000.0, 5780000.0), (580000.0, 5790000.0), (590000.0, 5790000.0)

# id: (zone, purpose, arrival_s, departure_s, parking_free, ev_exempt, destination, beta, cents, outcome, garage probability)
V3_CASES = {
    "F01": ("fx_ia_nolong_v3", "shop", 36000, 43200, False, False, None, 0.0, 360, "PAID_METERED", 0.0),
    "F02": ("fx_ia_nolong_v3", "shop", 34200, 48600, False, False, None, 0.0, 720, "NO_FEASIBLE_OPTION", 0.0),
    "F03": ("fx_bs_ia", "work", 36000, 48600, False, False, None, 0.0, 900, "PAID_LONG_STAY", 0.0),
    "F04": ("fx_bs_ib", "shop", 36000, 43200, False, False, D20, 0.0, 341, "PAID_EXPECTED", 0.3208213008),
    "F05": ("fx_bs_ib", "shop", 36000, 43201, False, False, D20, 0.0, 363, "PAID_METERED", 0.0),
    "F06": ("fx_ia_nolong_v3", "shop", 34200, 48600, False, False, D28, 0.0, 720, "NO_FEASIBLE_OPTION", 0.0),
    "F07": ("fx_ia_nolong_v3", "work", 34200, 48600, True, False, D28, 0.0, 0, "EMPLOYER_FREE", 0.0),
    "F08": ("fx_ia_nolong_v3", "shop", 34200, 48600, False, False, D30, 0.0, 400, "PAID_EXPECTED", 1.0),
    "P01": ("fx_bs_ib", "shop", 36000, 39600, False, False, D21, BETA, 175, "PAID_EXPECTED", 0.4821444544),
    "P02": ("fx_bs_ib", "shop", 36000, 43200, False, False, D22, BETA, 160, "PAID_EXPECTED", 0.6898336753),
    "P03": ("fx_ia_nolong_v3", "shop", 34200, 48600, False, False, D23, BETA, 576, "PAID_EXPECTED", 1.0),
    "P04": ("fx_bs_ib", "shop", 36000, 39600, False, False, D21, 0.0, 190, "PAID_EXPECTED", 0.4857906223),
    "P05": ("fx_bs_ib", "work", 28800, 57600, False, False, D24, BETA, 504, "PAID_EXPECTED", 0.6606042240),
    "P06": ("fx_bs_ib_v2", "work", 28800, 57600, False, False, D26, BETA, 384, "PAID_EXPECTED", 0.0066490138),
    "X01": ("fx_ev_street_v3", "shop", 36000, 46800, False, True, None, 0.0, 0, "EV_EXEMPT", 0.0),
    "X02": ("fx_ev_street_v3", "shop", 36000, 50400, False, True, None, 0.0, 0, "EV_EXEMPT", 0.0),
    "X03": ("fx_ev_street_v3", "shop", 36000, 50460, False, True, None, 0.0, 723, "PAID_METERED", 0.0),
    "X04": ("fx_ev_street_v3", "shop", 36000, 46800, False, False, None, 0.0, 540, "PAID_METERED", 0.0),
    "X05": ("fx_ev_clip_v3", "shop", 36000, 45000, False, True, None, 0.0, 0, "EV_EXEMPT", 0.0),
    "X06": ("fx_ev_clip_v3", "shop", 36000, 48000, False, True, None, 0.0, 600, "NO_FEASIBLE_OPTION", 0.0),
    "X07": ("fx_ev_street_v3", "shop", 36000, 43200, False, True, D27, BETA, 0, "EV_EXEMPT", 0.0),
    "X08": ("fx_ev_street_v3", "shop", 36000, 50460, False, True, D27, BETA, 769, "PAID_EXPECTED", 0.1663853918),
    "X09": ("fx_ev_expired_v3", "shop", 36000, 39600, False, True, None, 0.0, 180, "PAID_METERED", 0.0),
}

# id: (garage, arrival_s, departure_s, cents); the schedule of every garage is in S_GARAGES
S_CASES = {
    "S01": ("fx_g40_inc_total_open", 36000, 38700, 120), "S02": ("fx_g40_inc_total_open", 36000, 43200, 240),
    "S03": ("fx_g40_inc_total_open", 36000, 43260, 600),
    "S04": ("fx_g41_total_inc_total_open", 36000, 37200, 80), "S05": ("fx_g41_total_inc_total_open", 36000, 40500, 160),
    "S06": ("fx_g41_total_inc_total_open", 36000, 43200, 200), "S07": ("fx_g41_total_inc_total_open", 36000, 43260, 400),
    "S08": ("fx_g42_inc_total_total_open", 36000, 38700, 100), "S09": ("fx_g42_inc_total_total_open", 36000, 39660, 250),
    "S10": ("fx_g42_inc_total_total_open", 36000, 50400, 250), "S11": ("fx_g42_inc_total_total_open", 36000, 50460, 500),
    "S12": ("fx_g43_inc_inc_total_inc_open", 36000, 41400, 140), "S13": ("fx_g43_inc_inc_total_inc_open", 36000, 46800, 260),
    "S14": ("fx_g43_inc_inc_total_inc_open", 36000, 48000, 300), "S15": ("fx_g43_inc_inc_total_inc_open", 36000, 55800, 330),
    "S16": ("fx_g43_inc_inc_total_inc_open", 36000, 55860, 360),
    "S17": ("fx_g44_inc_inc_open", 36000, 41400, 100), "S18": ("fx_g44_inc_inc_open", 36000, 43200, 100),
    "S19": ("fx_g44_inc_inc_open", 36000, 45000, 120), "S20": ("fx_g44_inc_inc_open", 36000, 45060, 140),
    "S21": ("fx_g45_window_bands", 28800, 39600, 360), "S22": ("fx_g45_window_bands", 32400, 43200, 540),
    "S23": ("fx_g45_window_bands", 32400, 43260, 900), "S24": ("fx_g45_window_bands", 68400, 75600, 180),
    "S25": ("fx_g45_window_bands", 73800, 79200, 0),
}
# garage: the sequence of band kinds and whether the last band is open (the shape of the release dataset it stands for)
S_GARAGES = {
    "fx_g40_inc_total_open": (("increment", "total"), True),
    "fx_g41_total_inc_total_open": (("total", "increment", "total"), True),
    "fx_g42_inc_total_total_open": (("increment", "total", "total"), True),
    "fx_g43_inc_inc_total_inc_open": (("increment", "increment", "total", "increment"), True),
    "fx_g44_inc_inc_open": (("increment", "increment"), True),
    "fx_g45_window_bands": (("increment", "total"), True),
}


@pytest.fixture(scope="module")
def fixture_zones():
    from scripts.export_parking_golden_cases import fixture_zone_tariffs
    return fixture_zone_tariffs()


@pytest.fixture(scope="module")
def fixture_garages():
    from scripts.export_parking_golden_cases import fixture_garage_tariffs
    return fixture_garage_tariffs()


@pytest.fixture(scope="module")
def cases():
    return {case["id"]: case for case in gc.GOLDEN_CASES}


def test_the_schema_6_cases_are_appended_after_the_schema_5_cases_in_the_order_f_p_x():
    ids = [case["id"] for case in gc.GOLDEN_CASES]
    assert ids[127] == "E38"
    assert ids[128:] == [f"F{n:02d}" for n in range(1, 9)] + [f"P{n:02d}" for n in range(1, 7)] + [
        f"X{n:02d}" for n in range(1, 10)]
    assert list(V3_CASES) == ids[128:]
    assert gc.SCHEMA_6_CASE_FIELDS == ("ev_exempt", "option_price_beta_u_per_eur")
    assert all(field in gc.CASE_FIELDS for field in gc.SCHEMA_6_CASE_FIELDS)
    assert len(gc.CASE_FIELDS) == 20


@pytest.mark.parametrize("case_id", list(V3_CASES))
def test_the_inputs_and_hand_derived_expectations_of_the_schema_6_cases(cases, case_id):
    zone, purpose, arrival_s, departure_s, parking_free, ev_exempt, destination, beta, cents, outcome, probability = (
        V3_CASES[case_id])
    case = cases[case_id]
    assert (case["zone_id"], case["purpose"], case["arrival_s"], case["departure_s"], case["parking_free"],
            case["ev_exempt"], case["option_price_beta_u_per_eur"]) == (zone, purpose, arrival_s, departure_s, parking_free,
                                                                         ev_exempt, beta)
    assert (case["destination_x_m"], case["destination_y_m"]) == (destination if destination else (None, None))
    assert (case["expected_cents"], case["expected_outcome"]) == (cents, outcome)
    assert case["expected_garage_probability"] == pytest.approx(probability, abs=1e-9)
    # the stay is a plain interval at L = 0: no terminal rule, no resident rule, no error
    assert (case["terminal"], case["minimum_stay_min"], case["resident_of_zone"], case["resident_of_district"],
            case["expected_error"]) == (False, 0, False, False, False)
    # the decays of the fixture model with a destination (a garage may act), the visitor decay twice without one
    assert (case["garage_decay_m"], case["garage_decay_commute_m"]) == (
        (LAMBDA_M, LAMBDA_COMMUTE_M) if destination else (LAMBDA_M, LAMBDA_M))


@pytest.mark.parametrize("case_id", list(V3_CASES))
def test_the_reference_reproduces_each_schema_6_case_with_a_clear_rounding_margin(fixture_zones, fixture_garages, cases,
                                                                                 case_id):
    detail = gc.evaluate_case_detail(cases[case_id], fixture_zones, fixture_garages)
    assert (detail.cents, detail.outcome) == (cases[case_id]["expected_cents"], cases[case_id]["expected_outcome"])
    assert detail.garage_probability == pytest.approx(cases[case_id]["expected_garage_probability"], abs=1e-9)
    if detail.expected_cents is not None:
        # these hand derivations stay at least 0.1 cent from a half cent, far more than gc.ROUNDING_MARGIN_CENTS
        assert abs((detail.expected_cents - math.floor(detail.expected_cents)) - 0.5) > 0.1


def _mixture(options, beta, decay_m):
    """The expectation sum(w_i c_i) / sum(w_i) of ``options`` = [(distance_m, cents)], recomputed with plain math."""
    weights = [math.exp(-distance_m / decay_m - beta * cents / 100.0) for distance_m, cents in options]
    total = sum(weights)
    return sum(weight * cents for weight, (_, cents) in zip(weights, options)) / total, weights, total


@pytest.mark.parametrize("case_id, options, decay_m, first_garage", [
    # the options are (distance in metres, cents of THIS stay): street first (distance 0) where the street is an option
    ("F04", [(0, 360), (300, 300)], LAMBDA_M, 1),
    ("F08", [(700, 400)], LAMBDA_M, 0),
    ("P01", [(0, 180), (300, 100), (300, 300)], LAMBDA_M, 1),
    ("P02", [(0, 360), (300, 0), (300, 200)], LAMBDA_M, 1),
    ("P03", [(300, 800), (700, 400)], LAMBDA_M, 0),
    ("P04", [(0, 180), (300, 100), (300, 300)], LAMBDA_M, 1),
    ("P05", [(0, 900), (300, 300)], LAMBDA_COMMUTE_M, 1),
    ("P06", [(0, 376), (300, 1600)], LAMBDA_COMMUTE_M, 1),
    ("X08", [(0, 723), (300, 1000)], LAMBDA_M, 1),
])
def test_the_mixture_cases_are_recomputed_with_plain_math_and_agree_with_the_reference(
        fixture_zones, fixture_garages, cases, case_id, options, decay_m, first_garage):
    case = cases[case_id]
    expected, weights, total = _mixture(options, case["option_price_beta_u_per_eur"], decay_m)
    assert int(math.floor(expected + 0.5)) == case["expected_cents"]
    assert sum(weights[first_garage:]) / total == pytest.approx(case["expected_garage_probability"], abs=1e-9)
    detail = gc.evaluate_case_detail(case, fixture_zones, fixture_garages)
    assert detail.expected_cents == pytest.approx(expected, rel=1e-12)
    # the unnormalised weights the reference gave the street and the garages are those of the formula
    assert detail.option_weights == pytest.approx(tuple(weights[first_garage:]), rel=1e-12)
    assert detail.street_weight == pytest.approx(weights[0] if first_garage else 0.0, rel=1e-12)


def test_the_price_weights_move_the_expectation_toward_the_cheap_option_and_beta_zero_is_the_v2_value(cases, fixture_zones,
                                                                                                    fixture_garages):
    # P01 and P04 are the same stay; with beta the cheap garage (100 ct) gains and the dear one (300 ct) loses against the
    # street (180 ct): the expectation falls from 190 ct (v2 weights, a flat 0.4724 for both garages) to 175 ct.
    assert (cases["P01"]["expected_cents"], cases["P04"]["expected_cents"]) == (175, 190)
    assert cases["P04"]["option_price_beta_u_per_eur"] == 0.0
    v2 = (180 + math.exp(-0.75) * 100 + math.exp(-0.75) * 300) / (1 + 2 * math.exp(-0.75))
    assert gc.evaluate_case_detail(cases["P04"], fixture_zones, fixture_garages).expected_cents == pytest.approx(v2, rel=1e-12)
    assert cases["P04"]["expected_cents"] == int(math.floor(v2 + 0.5))
    # P05 weighs the garage by its MONTHLY product (300 ct per working day), not by its metered 1600 ct
    detail = gc.evaluate_case_detail(cases["P05"], fixture_zones, fixture_garages)
    assert detail.option_weights == pytest.approx((math.exp(-300 / LAMBDA_COMMUTE_M - BETA * 3.0),), rel=1e-12)
    # P06 weighs the street by its commuter product (376 ct), not by the capped metered price (900 ct)
    assert gc.evaluate_case_detail(cases["P06"], fixture_zones, fixture_garages).street_weight == pytest.approx(
        math.exp(-BETA * 3.76), rel=1e-12)


def test_the_feasibility_cases_pin_each_branch_of_the_rule(cases, fixture_zones, fixture_garages):
    detail = {case_id: gc.evaluate_case_detail(cases[case_id], fixture_zones, fixture_garages) for case_id in V3_CASES}
    # F04: the garage maximum (120 min) equals the stay: both options; F05: one second more drops the garage
    assert detail["F04"].feasible_option_ids == ("fx_g20_max120",) and detail["F04"].street_feasible is True
    assert detail["F05"].feasible_option_ids == () and detail["F05"].outcome == "PAID_METERED"
    # F06: street and the only garage infeasible -> priced as the street at its metered cents, named
    assert detail["F06"].feasible_option_ids == () and detail["F06"].street_feasible is False
    assert detail["F06"].cents == detail["F02"].cents == 720
    # F07: an early rule precedes the feasibility test
    assert detail["F07"].outcome == "EMPLOYER_FREE"
    # F08: the street is infeasible, the garage with the maximum drops out, the open garage alone carries probability 1
    assert detail["F08"].feasible_option_ids == ("fx_g30b_open",) and detail["F08"].street_feasible is False
    assert detail["F08"].garage_probability == pytest.approx(1.0, abs=1e-12) and detail["F08"].street_weight == 0.0
    # the zone without a long-stay product has a maximum; the zone of F03 has the product
    assert fixture_zones["fx_ia_nolong_v3"].max_stay_min == 180
    assert fixture_zones["fx_ia_nolong_v3"].long_stay_product_cents is None and not fixture_zones["fx_ia_nolong_v3"].has_garage
    assert fixture_zones["fx_bs_ia"].long_stay_product_cents == 900


def test_the_ev_cases_pin_the_limit_the_clipping_and_the_expired_rule(cases, fixture_zones, fixture_garages):
    # the rule in force: 240 min, the boundary day of the snapshot (2026-09-28) is not what this row tests, its date lies later
    assert fixture_zones["fx_ev_street_v3"].ev_free_minutes == 240 and fixture_zones["fx_ev_street_v3"].max_stay_min is None
    assert fixture_zones["fx_ev_clip_v3"].ev_free_minutes == 240 and fixture_zones["fx_ev_clip_v3"].max_stay_min == 150
    # the expired rule is exported as no exemption: the exempt person of X09 pays the metered 60 min (E2 and the date switch)
    assert fixture_zones["fx_ev_expired_v3"].ev_free_minutes is None
    stated = [line for line in FIXTURE_CSV.read_text(encoding="utf-8").splitlines() if line.startswith("fx_ev_expired_v3,")]
    assert len(stated) == 1 and ",180,2026-09-27" in stated[0].replace(", ", ",")
    # X02 is exempt exactly at the limit, X03 pays the full schedule one minute above it (no deduction), X04 is a non-exempt
    # person in the same zone for the same minutes as X01
    assert (cases["X02"]["expected_outcome"], cases["X03"]["expected_cents"]) == ("EV_EXEMPT", 723)
    assert cases["X04"]["expected_cents"] == 540 and cases["X04"]["ev_exempt"] is False
    # X05 is exempt exactly at the maximum stay, X06 is not exempt within the exemption but above the maximum (E2) and then
    # has no feasible option
    assert (cases["X05"]["expected_outcome"], cases["X06"]["expected_outcome"]) == ("EV_EXEMPT", "NO_FEASIBLE_OPTION")
    # X07: a free street (exempt) means nobody pays a garage (E4); X08: above the limit the mixture forms as for everybody
    assert gc.evaluate_case_detail(cases["X07"], fixture_zones, fixture_garages).feasible_option_ids == ("fx_g27_core",)
    assert gc.evaluate_case_detail(cases["X07"], fixture_zones, fixture_garages).garage_probability == 0.0
    assert gc.evaluate_case_detail(cases["X08"], fixture_zones, fixture_garages).outcome == "PAID_EXPECTED"
    # every exempt case is unchanged by the exemption where it prices a stay that is no exemption: X03 with the flag off
    assert gc.evaluate_case({**cases["X03"], "ev_exempt": False}, fixture_zones, fixture_garages) == (723, "PAID_METERED")


def test_the_schema_6_cases_carry_the_model_coefficient_or_zero_and_the_model_names_the_same_value():
    document = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    model = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert document["option_price_beta_u_per_eur"] == model["option_price_beta_u_per_eur"] == gc.FIXTURE_OPTION_PRICE_BETA_U_PER_EUR
    assert gc.FIXTURE_OPTION_PRICE_BETA_U_PER_EUR == BETA
    assert {case["option_price_beta_u_per_eur"] for case in document["cases"]} == {0.0, BETA}
    # every case of the schema-5 families, the whole F family and P04 are priced with the weights of v2
    zero = {case["id"] for case in document["cases"] if case["option_price_beta_u_per_eur"] == 0.0}
    assert {case["id"] for case in document["cases"][:128]} <= zero and {f"F{n:02d}" for n in range(1, 9)} <= zero
    assert {case["id"] for case in document["cases"]} - zero == {"P01", "P02", "P03", "P05", "P06", "X07", "X08"}
    assert all(case["ev_exempt"] is (case["id"] in ("X01", "X02", "X03", "X05", "X06", "X07", "X08", "X09"))
               for case in document["cases"])


# --------------------------------------------------------------------------------------------------- the fixture garages


def test_the_schema_6_fixture_garages_carry_their_maximum_stay_and_the_rest_of_the_set_has_none(fixture_garages):
    by_id = {garage.garage_id: garage for garage in fixture_garages}
    assert {garage_id: garage.max_stay_min for garage_id, garage in by_id.items() if garage.max_stay_min is not None} == {
        "fx_g20_max120": 120, "fx_g28_max90": 90, "fx_g30a_max120": 120}
    # the fixture geojson keeps a legacy layout without the column (the dataset has it since spec v3 B), so the exporter sets it
    # from
    # the sidecar tests/fixtures/parking/parking_garages_fixture_max_stay_min.json: the maximum travels in the model
    model = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert {entry["garage_id"] for entry in model["garages"] if entry["max_stay_min"] is not None} == {
        "fx_g20_max120", "fx_g28_max90", "fx_g30a_max120"}


OLD_GARAGE_IDS = frozenset({
    "fx_g01_core", "fx_g02_a", "fx_g02_b", "fx_g03_edge", "fx_g03_far", "fx_g04_core", "fx_g06_window", "fx_g07_tiers",
    "fx_g08_bands", "fx_g09_fp", "fx_g10_grace", "fx_g11_core_fp", "fx_g12_tiers_gap", "fx_g13_closed", "fx_g14_closed_capped",
    "fx_g15_tier_grace", "fx_g16_tier_grace_cap", "fx_g17_free", "fx_g18_grace_total"})
# the garages in range (D_max = 1000 m) of every destination of the schema-6 cases
OPTION_SETS = {D20: {"fx_g20_max120"}, D21: {"fx_g21_cheap", "fx_g21_dear"}, D22: {"fx_g22_free", "fx_g22_paid"},
               D23: {"fx_g23_a", "fx_g23_b"}, D24: {"fx_g24_monthly"}, D26: {"fx_g26_dear"}, D27: {"fx_g27_core"},
               D28: {"fx_g28_max90"}, D30: {"fx_g30a_max120", "fx_g30b_open"}}


def test_the_schema_6_destinations_see_exactly_their_own_garages_and_no_schema_5_case_sees_a_new_one(fixture_garages, cases):
    new_ids = {garage.garage_id for garage in fixture_garages} - OLD_GARAGE_IDS
    assert len(OLD_GARAGE_IDS) == 19 and OLD_GARAGE_IDS <= {garage.garage_id for garage in fixture_garages}
    for destination, expected in OPTION_SETS.items():
        in_range = {garage.garage_id for garage, _ in cost.garage_options_in_range(
            fixture_garages, destination[0], destination[1], cost.GARAGE_MAX_DISTANCE_M)}
        assert in_range == expected, destination
    # the schema-5 cases must not see a new garage, even with a margin of 1000 m beyond D_max: their option sets are part of
    # the unchanged prefix
    for case in list(cases.values())[:128]:
        if case["destination_x_m"] is None:
            continue
        in_range = {garage.garage_id for garage, _ in cost.garage_options_in_range(
            fixture_garages, case["destination_x_m"], case["destination_y_m"], cost.GARAGE_MAX_DISTANCE_M + 1000.0)}
        assert not in_range & new_ids, case["id"]
    # the new garages that no destination uses (the S shapes) lie where no case destination is within D_max
    shaped = {garage_id for garage_id in new_ids if garage_id.startswith("fx_g4")}
    assert shaped == set(S_GARAGES)
    for garage in fixture_garages:
        if garage.garage_id in shaped:
            assert all(math.hypot(garage.x_m - case["destination_x_m"], garage.y_m - case["destination_y_m"]) >
                       cost.GARAGE_MAX_DISTANCE_M for case in cases.values() if case["destination_x_m"] is not None)


# ----------------------------------------------------------------------------------------- S: the release band shapes


@pytest.mark.parametrize("garage_id, shape", sorted(S_GARAGES.items()))
def test_each_s_garage_has_the_band_shape_of_the_release_dataset_it_stands_for(garage_id, shape):
    kinds, open_last = shape
    model = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    entry = next(entry for entry in model["garages"] if entry["garage_id"] == garage_id)
    assert tuple(band["kind"] for band in entry["bands"]) == kinds
    assert (entry["bands"][-1]["to_min"] is None) == open_last
    assert entry["tiers"] is None and entry["hourly_rate_cents"] is None


def test_the_window_garage_has_a_9_to_20_h_fee_window_and_the_zone_ia_lot_bands(fixture_garages):
    # '0-180 0.03/1; 180- total 9.00' inside 09:00-20:00 (the zone-Ia lots of the release)
    garage = next(garage for garage in fixture_garages if garage.garage_id == "fx_g45_window_bands")
    assert (garage.fee_start_s, garage.fee_end_s) == (32400, 72000)
    assert [(band.from_min, band.to_min, band.kind, band.price_cents, band.unit_min) for band in garage.bands] == [
        (0, 180, "increment", 3, 1), (180, None, "total", 900, None)]


@pytest.mark.parametrize("case_id", list(S_CASES))
def test_the_s_cases_name_their_stay_and_hand_derived_price(fixture_garages, case_id):
    garage_id, arrival_s, departure_s, cents = S_CASES[case_id]
    case = next(case for case in gc.GOLDEN_GARAGE_OPTION_CASES if case["id"] == case_id)
    assert (case["garage_id"], case["arrival_s"], case["departure_s"], case["purpose"], case["expected_cents"]) == (
        garage_id, arrival_s, departure_s, "shop", cents)
    garage = next(garage for garage in fixture_garages if garage.garage_id == garage_id)
    assert cost.garage_option_cents(garage, arrival_s, departure_s, purpose="shop") == cents
    assert gc.option_case_mismatches(fixture_garages, [case]) == []


# ------------------------------------------------------------------- the schema-5 content is unchanged (prefix digests)

OLD_CASE_FIELDS = ("id", "zone_id", "arrival_s", "departure_s", "purpose", "parking_free", "resident_of_zone",
                   "resident_of_district", "terminal", "minimum_stay_min", "destination_x_m", "destination_y_m",
                   "garage_decay_m", "garage_decay_commute_m", "expected_cents", "expected_outcome",
                   "expected_garage_probability", "expected_error")
# SHA-256 of the sorted, compact JSON of the schema-5 content of the COMMITTED golden file before Task A6 (the file whose LF
# SHA-256 was 342bc4bc...), computed from that file with the projections below: the first 128 cases with the 18 schema-5 fields,
# the first 69 option cases, the 18 schema-5 zone entries without ev_free_minutes and the 19 schema-5 garage entries without
# max_stay_min. A change of any schema-5 case, expectation, tariff or garage fails here, which is the proof that the schema-6
# case lists are exact prefixes in content of the schema-5 lists.
SCHEMA_5_DIGESTS = {
    "cases": "9bbf8d29d6371a9f8edac8dfbfb7494490912b6a0f46f624b94fecb2cf1b1cff",
    "options": "e319fdd7087ea75421e1a43d3b79e61ecb1c56a3f4601c03223c3379cd411221",
    "tariffs": "d0dc922497bf3652fbc1e85bf458eb854912cd294977b775a403c61296890a75",
    "garages": "86ef5ba965dd93d24a99508015576191c8094ca4b3506585d27cfded0c21d159",
}
SCHEMA_5_ZONE_COUNT, SCHEMA_5_GARAGE_COUNT = 18, 19


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def test_the_schema_5_cases_options_tariffs_and_garages_are_unchanged_in_the_schema_6_file():
    document = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    cases = [{key: case[key] for key in OLD_CASE_FIELDS} for case in document["cases"][:128]]
    options = document["garage_option_cases"][:69]
    old_zone_ids = {"fx_bs_ia", "fx_bs_ib", "fx_sz", "fx_wob", "fx_pe", "fx_res_a", "fx_campus", "fx_frac", "fx_bs_ib_v2",
                    "fx_bs_ia_v2", "fx_wob_v2", "fx_campus_v2", "fx_garage_window_v2", "fx_capped_street_v2",
                    "fx_campus_tie_v2", "fx_res_garage_v2", "fx_bga_v2", "fx_res_nopermit_v2"}
    tariffs = {zone_id: {key: value for key, value in fields.items() if key != "ev_free_minutes"}
               for zone_id, fields in document["tariffs"].items() if zone_id in old_zone_ids}
    garages = [{key: value for key, value in entry.items() if key != "max_stay_min"} for entry in document["garages"]
               if entry["garage_id"] in OLD_GARAGE_IDS]
    assert len(tariffs) == SCHEMA_5_ZONE_COUNT and len(garages) == SCHEMA_5_GARAGE_COUNT
    assert (_digest(cases), _digest(options), _digest(tariffs), _digest(garages)) == (
        SCHEMA_5_DIGESTS["cases"], SCHEMA_5_DIGESTS["options"], SCHEMA_5_DIGESTS["tariffs"], SCHEMA_5_DIGESTS["garages"])
