"""The calibration of the garage decay lengths (``scripts/parking/calibrate_garage_decay.py``, spec Amendments E5 and H2).

Built and tested on a synthetic universe only: the reference plans of the earlier exposure checks were lost on 2026-10-07
and the real calibration runs on the server plans of Task 5 of issue #436. Covered: the target is READ from the committed
SrV table (0.708 / (0.708 + 0.2537) = 0.7362), the mean garage probability, the bisection (a known solution, an unreachable
target, the maximum distance), the destination universe of E5, the commuter universe of H2 (work and education without
parkingFree) with the commuter target READ from the bs_zentrum row, the Wolfsburg check of H3b, the written table with its
provenance and its reader, and the refusal to replace a table silently. Every expected number is derived in its comment.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import math
import re
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.parking import cost, garages as pg, tariff_export

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "parking"
COMMITTED = REPO / "eqasim-data" / "data" / "braunschweig"
SCRIPT = REPO / "scripts" / "parking" / "calibrate_garage_decay.py"


_SCRIPT_MODULE = []


def _script():
    """The calibration script as a module, loaded once (the helpers below build its ``Stay`` objects outside a fixture)."""
    if not _SCRIPT_MODULE:
        spec = importlib.util.spec_from_file_location("calibrate_garage_decay_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _SCRIPT_MODULE.append(module)
    return _SCRIPT_MODULE[0]


@pytest.fixture(scope="module")
def cal():
    return _script()


def _probability(distance_m: float, decay_m: float) -> float:
    weight = math.exp(-distance_m / decay_m)
    return weight / (1.0 + weight)


# ------------------------------------------------------------------------------------------------ the targets


def test_the_target_is_read_from_the_committed_city_center_table_and_is_0_736(cal):
    target = cal.read_city_center_target(COMMITTED / "srv" / "srv2023_city_center_parking.csv")
    # rows garage_large_lot 0.708 and street 0.2537: 0.708 / 0.9617 = 0.73620
    assert (target.numerator, target.other) == (0.708, 0.2537)
    assert target.value == pytest.approx(0.708 / (0.708 + 0.2537), abs=1e-12)
    assert target.value == pytest.approx(0.7362, abs=5e-5)


def test_the_commuter_and_paid_references_are_read_from_the_committed_tables(cal):
    # the paid reference is no longer used by the calibration; the comparison script reads it through this reader
    commute = COMMITTED / "srv" / "srv2023_commute_parking_by_workplace_class.csv"
    commuters = cal.read_commuter_reference(commute)
    # class bs_zentrum (the target of H2, ruling R-4h-1: the PAID-only shares, like-for-like with a universe of payers): paid
    # street 0.0456, paid garage or large lot 0.0965: 0.0965 / 0.1421 = 0.67910 (the free-inclusive 0.1923 / 0.4148 = 0.4636
    # is not used: free parkers are in its denominator)
    assert cal.COMMUTER_CLASS == "bs_zentrum"
    assert (commuters.numerator_name, commuters.other_name) == ("share_garage_large_lot_paid", "share_street_paid")
    assert (commuters.numerator, commuters.other) == (0.0965, 0.0456)
    assert commuters.value == pytest.approx(0.0965 / (0.0965 + 0.0456), abs=1e-12)
    assert cal.read_commuter_reference(commute, cal.COMMUTER_CLASS) == commuters
    # row 03103 (Wolfsburg, the check of H3b): paid street 0.0080, paid garage or large lot 0.0225: 0.0225 / 0.0305 = 0.73770
    assert cal.WOLFSBURG_CLASS == "03103"
    wolfsburg = cal.read_commuter_reference(commute, cal.WOLFSBURG_CLASS)
    assert (wolfsburg.numerator, wolfsburg.other) == (0.0225, 0.0080)
    assert wolfsburg.value == pytest.approx(0.0225 / (0.0225 + 0.0080), abs=1e-12)
    with pytest.raises(ValueError, match="expected exactly one class row 'no_such_class'"):
        cal.read_commuter_reference(commute, "no_such_class")


def test_a_commute_table_without_the_payment_per_place_columns_is_refused_with_the_remedy(cal, tmp_path):
    # a table written before the revision of H2 (ruling R-4h-1) has no paid-only shares: it must not fall back to the
    # free-inclusive share
    path = tmp_path / "old_commute.csv"
    path.write_text("# old\nworkplace_class,level,n_unweighted,n_eff,share_employer_lot,share_street,share_garage_large_lot,"
                    "share_other,share_paid_total,share_free_total\nbs_zentrum,class,221,104.4,0.571,0.2225,0.1923,0.0141,"
                    "0.2062,0.7938\n", encoding="utf-8")
    with pytest.raises(ValueError, match="share_garage_large_lot_paid.*extract_srv_commute_parking.py"):
        cal.read_commuter_reference(path)
    assert cal.read_paid_share_reference(COMMITTED / "srv" / "srv2023_city_center_parking.csv") == 0.8333


@pytest.mark.parametrize("rows, message", [
    ("employer_lot,0.02,40\nstreet,0.25,574\n", "exactly one row 'garage_large_lot'"),
    ("garage_large_lot,0.7,1\ngarage_large_lot,0.1,1\nstreet,0.25,574\n", "exactly one row 'garage_large_lot'"),
    ("garage_large_lot,0.0,1\nstreet,0.25,574\n", "share in"),
    ("garage_large_lot,0.7,1\nstreet,1.5,574\n", "share in"),
])
def test_a_city_center_table_without_a_usable_target_row_is_refused(cal, tmp_path, rows, message):
    path = tmp_path / "srv.csv"
    path.write_text("# header\nparking_type,share,n_unweighted\n" + rows, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        cal.read_city_center_target(path)


# --------------------------------------------------------------------------- the stays, the production weights, the bisection


def _zone(**overrides) -> cost.ZoneTariff:
    """A street zone: 1.80 EUR per started minute-unit hour rate (180 ct/h, billing unit 1 min), fee window 06:00-22:00."""
    fields = dict(zone_id="z", zone_type="street_paid", hourly_rate_cents=180, billing_unit_min=1,
                  free_if_stay_at_most_min=None, first_period_min=None, first_period_cents=None, daily_cap_cents=None,
                  max_stay_min=None, long_stay_product_cents=None, member_day_cents=None, guest_day_cents=None,
                  fee_start_s=6 * 3600, fee_end_s=22 * 3600, resident_exempt=False)
    fields.update(overrides)
    return cost.ZoneTariff(**fields)


def _garage_tariff(garage_id: str) -> cost.GarageTariff:
    """The ``GarageTariff`` of a priced row of the garage fixture: fx_g01_core 2.00 EUR per started hour, all day, monthly
    product 63.00 EUR (3.00 EUR per working day, P2); fx_g02_a the same without a monthly product; fx_g17_free the free
    schedule."""
    frame = pg.load_garages(FIXTURES / "parking_garages_fixture.geojson", allow_legacy_columns=True)
    return tariff_export.garage_row_to_tariff(frame[frame["garage_id"] == garage_id].iloc[0])


def _stay(options, *, zone=None, purpose="shop", arrival_s=9 * 3600, departure_s=10 * 3600):
    """A stay of the given zone with ``options`` = ``[(garage_id, distance_m)]`` (a one-hour stay at 09:00 by default)."""
    zone = zone or _zone()
    return _script().Stay(zone_id=zone.zone_id, tariff=zone, purpose=purpose, arrival_s=arrival_s,
                          departure_s=departure_s,
                          options=tuple((_garage_tariff(garage_id), distance_m) for garage_id, distance_m in options))


def _priced_share(distance_m, decay_m, beta, street_cents, option_cents):
    """The share of ONE garage next to the street with the weights exp(-d / lambda - beta * c / 100) (spec v3 A2), derived
    here and not read from the code: street weight exp(-beta * c_street / 100), garage weight exp(-d / lambda - beta *
    c_garage / 100), share w / (w + w_street)."""
    street = math.exp(-beta * street_cents / 100.0)
    garage = math.exp(-distance_m / decay_m - beta * option_cents / 100.0)
    return garage / (garage + street)


ALL_PAID = {"fx_g01_core": True, "fx_g02_a": True, "fx_g08_bands": True, "fx_g17_free": False}


def test_the_mean_option_share_is_the_choice_share_of_the_options_and_the_street_g1(cal):
    # beta 0 is the price-independent weight of v2: one destination, garages at 300 m and 700 m, lambda 400:
    # w = 0.4723665527 and 0.1737739435; P = 0.6461404962 / 1.6461404962 = 0.39251844
    one = _stay([("fx_g01_core", 300.0), ("fx_g02_a", 700.0)])
    assert cal.mean_option_share([one], 400.0, 0.0, cal.MEASURE_ALL_OPTIONS, ALL_PAID) == pytest.approx(0.3925184379, abs=1e-9)
    # two destinations average: (0.3925184379 + 0.3208213008) / 2 (the second sees only the 300 m garage)
    two = _stay([("fx_g01_core", 300.0)])
    assert cal.mean_option_share([one, two], 400.0, 0.0, cal.MEASURE_ALL_OPTIONS, ALL_PAID) == pytest.approx(
        (0.3925184379 + 0.3208213008) / 2, abs=1e-9)
    # a stay without an option in range has the share 0 (it parks on the street)
    assert cal.mean_option_share([_stay([])], 400.0, 0.0, cal.MEASURE_ALL_OPTIONS, ALL_PAID) == 0.0


def test_an_empty_universe_and_a_bad_decay_are_refused(cal):
    with pytest.raises(ValueError, match="universe is empty"):
        cal.mean_option_share([], 400.0, 0.0, cal.MEASURE_ALL_OPTIONS, ALL_PAID)
    for decay in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="decay_m"):
            cal.mean_option_share([_stay([("fx_g01_core", 300.0)])], decay, 0.0, cal.MEASURE_ALL_OPTIONS, ALL_PAID)
    with pytest.raises(ValueError, match="measure"):
        cal.mean_option_share([_stay([("fx_g01_core", 300.0)])], 400.0, 0.0, "free_only", ALL_PAID)


def test_every_stay_is_priced_and_weighted_by_the_production_code_in_the_order_street_then_garages(cal, monkeypatch):
    # Part A final review, B5 amendment 1: ONE weight implementation. The calibration calls cost.option_weight (through
    # cost.parking_cost_with_garages_detail) with the street (distance 0, its cents for THIS stay) first and the garages by id.
    calls = []
    original = cost.option_weight

    def spy(distance_m, decay_m, cost_cents, beta_u_per_eur):
        calls.append((distance_m, decay_m, cost_cents, beta_u_per_eur))
        return original(distance_m, decay_m, cost_cents, beta_u_per_eur)

    monkeypatch.setattr(cost, "option_weight", spy)
    # a one-hour stay at 09:00: the street costs 180 ct (1.80 EUR per hour, minute units), fx_g01_core 200 ct (2.00 per hour)
    stay = _stay([("fx_g01_core", 300.0)])
    share = cal.mean_option_share([stay], 400.0, 0.310998, cal.MEASURE_ALL_OPTIONS, ALL_PAID)
    assert calls == [(0.0, 400.0, 180, 0.310998), (300.0, 400.0, 200, 0.310998)]
    assert share == pytest.approx(_priced_share(300.0, 400.0, 0.310998, 180, 200), abs=1e-12)


def test_a_mutant_with_the_old_closed_form_weight_gives_another_share_when_beta_is_above_zero(cal, monkeypatch):
    # the closed form exp(-d / lambda) with street weight 1 (v2) is the mutant: with beta > 0 and a garage that costs more than
    # the street it differs from the production weight, so a calibration that kept it would calibrate another quantity
    stay = _stay([("fx_g01_core", 300.0)])
    production = cal.mean_option_share([stay], 400.0, 0.310998, cal.MEASURE_ALL_OPTIONS, ALL_PAID)
    # independent value: street 180 ct, garage 200 ct: w / w_street = exp(-300 / 400 - 0.310998 * 20 / 100)
    ratio = math.exp(-300.0 / 400.0 - 0.310998 * 20 / 100.0)
    assert production == pytest.approx(ratio / (1.0 + ratio), abs=1e-12)
    monkeypatch.setattr(cost, "option_weight", lambda distance_m, decay_m, cost_cents, beta: math.exp(-distance_m / decay_m))
    mutant = cal.mean_option_share([stay], 400.0, 0.310998, cal.MEASURE_ALL_OPTIONS, ALL_PAID)
    assert mutant == pytest.approx(_probability(300.0, 400.0), abs=1e-12)
    assert abs(mutant - production) > 0.01          # the test sees the mutant
    # with beta 0 the production weight IS the old formula, bit for bit: there the mutant cannot be told apart
    assert cal.mean_option_share([stay], 400.0, 0.0, cal.MEASURE_ALL_OPTIONS, ALL_PAID) == mutant


def test_a_cheaper_option_gets_more_weight_and_the_free_street_pays_no_option_e4(cal):
    # price in the choice (spec v3 A2): the 300 m garage costs 200 ct, the street 180 ct; at a street that costs 600 ct the
    # same garage is the cheap option, so its share rises
    dear_street = _zone(hourly_rate_cents=600)
    cheap = cal.mean_option_share([_stay([("fx_g01_core", 300.0)])], 400.0, 0.310998, cal.MEASURE_ALL_OPTIONS, ALL_PAID)
    dear = cal.mean_option_share([_stay([("fx_g01_core", 300.0)], zone=dear_street)], 400.0, 0.310998,
                                 cal.MEASURE_ALL_OPTIONS, ALL_PAID)
    assert dear > cheap
    assert dear == pytest.approx(_priced_share(300.0, 400.0, 0.310998, 600, 200), abs=1e-12)
    # E4 (production rule): a stay within the free threshold has a free street, nobody pays a garage: share 0
    free_street = _zone(free_if_stay_at_most_min=90)
    assert cal.mean_option_share([_stay([("fx_g01_core", 300.0)], zone=free_street)], 400.0, 0.310998,
                                 cal.MEASURE_ALL_OPTIONS, ALL_PAID) == 0.0


def test_the_paid_only_share_leaves_the_free_option_out_but_keeps_its_weight_in_the_choice(cal):
    # One destination; paid options at 300 m (fx_g01_core, 200 ct) and 700 m (fx_g02_a, 200 ct) and a FREE option at 200 m
    # (fx_g17_free, 0 ct); lambda 400 m, beta 0 (price-independent weights, street 1):
    # w = exp(-0.75) = 0.4723665527 (paid), exp(-1.75) = 0.1737739435 (paid), exp(-0.5) = 0.6065306597 (free);
    # the model's probabilities: street 1 / (1 + W), option i w_i / (1 + W), W = 1.2526711559
    stay = _stay([("fx_g01_core", 300.0), ("fx_g02_a", 700.0), ("fx_g17_free", 200.0)])
    weights = np.exp(-np.array([300.0, 700.0, 200.0]) / 400.0)
    p_street, p_option = 1.0 / (1.0 + weights.sum()), weights / (1.0 + weights.sum())
    # the SrV quantity: paid garage / (paid garage + paid street); the free option is a free parker and leaves both
    from_probabilities = p_option[:2].sum() / (p_option[:2].sum() + p_street)
    assert from_probabilities == pytest.approx(0.6461404962 / 1.6461404962, abs=1e-9)
    paid_only = cal.mean_option_share([stay], 400.0, 0.0, cal.MEASURE_PAID_OPTIONS, ALL_PAID)
    assert paid_only == pytest.approx(from_probabilities, abs=1e-12) and paid_only == pytest.approx(0.39251844, abs=1e-8)
    # the payment-inclusive measure counts every option: W / (1 + W) = 1.2526711559 / 2.2526711559 = 0.55610
    every = cal.mean_option_share([stay], 400.0, 0.0, cal.MEASURE_ALL_OPTIONS, ALL_PAID)
    assert every == pytest.approx(1.2526711559 / 2.2526711559, abs=1e-9) and every > paid_only
    # with the price term the free option (0 ct) outweighs the paid ones and the street (180 ct): the paid-only share is
    # W_paid / (W_paid + w_street) with the production weights, derived here: street exp(-b * 1.80), paid exp(-d / 400 - b * 2.00)
    beta = 0.310998
    street, paid_weight = math.exp(-beta * 1.80), math.exp(-0.75 - beta * 2.0) + math.exp(-1.75 - beta * 2.0)
    assert cal.mean_option_share([stay], 400.0, beta, cal.MEASURE_PAID_OPTIONS, ALL_PAID) == pytest.approx(
        paid_weight / (paid_weight + street), abs=1e-12)


def test_the_paid_only_share_of_a_stay_without_a_paid_option_or_without_a_street_is_defined_not_silent(cal):
    free_only = _stay([("fx_g17_free", 200.0)])
    # only a free option: no paying parker exists next to the paid street, the paid-only share is 0 and no target above 0 is
    # reachable (the measure cannot be silently replaced by the payment-inclusive one)
    assert cal.mean_option_share([free_only], 400.0, 0.0, cal.MEASURE_PAID_OPTIONS, ALL_PAID) == 0.0
    assert cal.mean_option_share([free_only], 400.0, 0.0, cal.MEASURE_ALL_OPTIONS, ALL_PAID) > 0.0
    # a street that cannot serve the stay (maximum stay, no long-stay product): the garages carry the whole share, 1 when a
    # paid option is feasible (spec v3 A1: an off-street share of 1), 0 when only a free one is
    no_street = _zone(max_stay_min=30)
    paid_stay = _stay([("fx_g01_core", 300.0)], zone=no_street, purpose="work", arrival_s=8 * 3600, departure_s=16 * 3600)
    assert cal.mean_option_share([paid_stay], 400.0, 0.310998, cal.MEASURE_PAID_OPTIONS, ALL_PAID) == pytest.approx(1.0, abs=1e-12)
    assert cal.mean_option_share([paid_stay], 400.0, 0.310998, cal.MEASURE_ALL_OPTIONS, ALL_PAID) == pytest.approx(1.0, abs=1e-12)
    free_stay = _stay([("fx_g17_free", 300.0)], zone=no_street, purpose="work", arrival_s=8 * 3600, departure_s=16 * 3600)
    assert cal.mean_option_share([free_stay], 400.0, 0.310998, cal.MEASURE_ALL_OPTIONS, ALL_PAID) == pytest.approx(1.0, abs=1e-12)
    assert cal.mean_option_share([free_stay], 400.0, 0.310998, cal.MEASURE_PAID_OPTIONS, ALL_PAID) == 0.0
    # no option can serve the stay at all (NO_FEASIBLE_OPTION): share 0, never an invented 1
    nothing = _stay([], zone=no_street, purpose="work", arrival_s=8 * 3600, departure_s=16 * 3600)
    assert cal.mean_option_share([nothing], 400.0, 0.310998, cal.MEASURE_ALL_OPTIONS, ALL_PAID) == 0.0


def test_the_bisection_finds_the_known_lambda_of_one_destination(cal):
    # one destination 300 m from one garage: P = w / (1 + w) = 0.25 needs w = 1/3, i.e. lambda = 300 / ln 3 = 273.06 m.
    # The slope there is 0.00075 per metre, so the tolerance 0.0005 allows a lambda within about 0.7 m.
    def mean(decay):
        return _probability(300.0, decay)

    result = cal.calibrate_decay(mean, 0.25)
    assert result.decay_m == pytest.approx(300.0 / math.log(3.0), abs=1.0)
    assert abs(result.achieved - 0.25) <= 0.0005
    assert abs(_probability(300.0, result.decay_m) - result.achieved) < 1e-12   # the mean at the rounded lambda
    assert result.decay_m == round(result.decay_m, 2)
    assert result.low_end_mean < 0.25 < result.high_end_mean and result.iterations > 1
    # a tighter tolerance gives a closer lambda (down to the rounding of lambda to 0.01 m: 0.005 m x 0.00075 = 4e-6)
    tight = cal.calibrate_decay(mean, 0.25, tolerance=1e-5)
    assert tight.decay_m == pytest.approx(300.0 / math.log(3.0), abs=0.02)
    with pytest.raises(ValueError, match="rounded to"):
        cal.calibrate_decay(mean, 0.25, tolerance=1e-8)


def test_the_bisection_is_monotone_a_higher_target_needs_a_longer_decay(cal):
    def mean(decay):
        return float(np.mean([_probability(distance, decay) for distance in (200.0, 650.0, 450.0, 900.0)]))

    lambdas = [cal.calibrate_decay(mean, target).decay_m for target in (0.1, 0.2, 0.3)]
    assert lambdas == sorted(lambdas) and len(set(lambdas)) == 3


@pytest.mark.parametrize("distance_m, target", [(300.0, 0.9), (1.0, 0.1)])
def test_an_unreachable_target_fails_instead_of_returning_the_end_of_the_range(cal, distance_m, target):
    # a garage at 300 m gives 0.485 at lambda 5000 m (below 0.9); a garage at 1 m already gives 0.475 at lambda 10 m
    # (above 0.1): neither target lies between the two ends
    with pytest.raises(ValueError, match="unreachable") as error:
        cal.calibrate_decay(lambda decay: _probability(distance_m, decay), target)
    assert "0.4" in str(error.value)   # the message names the mean at the two ends of the range


def test_a_universe_without_an_option_in_range_cannot_reach_a_positive_target(cal):
    with pytest.raises(ValueError, match="unreachable"):
        cal.calibrate_decay(lambda decay: 0.0, 0.3)


def test_the_search_range_and_target_are_validated(cal):
    with pytest.raises(ValueError, match="target"):
        cal.calibrate_decay(lambda decay: 0.5, 1.0)
    with pytest.raises(ValueError, match="lambda_min_m"):
        cal.calibrate_decay(lambda decay: 0.5, 0.25, lambda_min_m=100.0, lambda_max_m=50.0)


# --------------------------------------------------------------- the times of a stay (spec v3 A1: the activity duration, L1, T1)


def test_a_stay_is_priced_from_the_activity_start_to_its_end_with_the_minimum_stay_and_the_terminal_rule(cal):
    hour = 3600
    # the planned stay, longer than the minimum: priced as it is
    assert cal.priced_stay_times_s(8 * hour, 9 * hour, fee_end_s=22 * hour, minimum_stay_s=900) == (
        8 * hour, 9 * hour, "planned")
    # L1: a stay shorter than the minimum is priced as the minimum (the priced interval changes, never the plan)
    assert cal.priced_stay_times_s(8 * hour, 8 * hour + 300, fee_end_s=22 * hour, minimum_stay_s=900) == (
        8 * hour, 8 * hour + 900, "planned")
    # T1: a terminal stay (no end_time) pays until the fee window of the arrival day ends
    assert cal.priced_stay_times_s(8 * hour, float("nan"), fee_end_s=22 * hour, minimum_stay_s=900) == (
        8 * hour, 22 * hour, "terminal_stay_rule")
    # a plan that runs past midnight: the arrival day is the day of the arrival (T1 uses the arrival day)
    assert cal.priced_stay_times_s(25 * hour, float("nan"), fee_end_s=22 * hour, minimum_stay_s=0) == (
        25 * hour, 46 * hour, "terminal_stay_rule")
    # an end before the start (the planned end precedes the arrival): the minimum stay prices it, and it is labelled
    assert cal.priced_stay_times_s(10 * hour, 9 * hour, fee_end_s=22 * hour, minimum_stay_s=900) == (
        10 * hour, 10 * hour + 900, "planned_end_before_arrival")
    # seconds are whole numbers: a read float second is rounded, not truncated
    assert cal.priced_stay_times_s(28800.0, 32400.4, fee_end_s=22 * hour, minimum_stay_s=0)[:2] == (28800, 32400)
    # a stay without its start is never priced from midnight
    with pytest.raises(ValueError, match="start_time"):
        cal.priced_stay_times_s(float("nan"), 9 * hour, fee_end_s=22 * hour, minimum_stay_s=900)


def test_the_stays_of_a_universe_hold_the_options_within_the_maximum_distance_from_the_production_function(cal):
    near, edge, beyond = _garage_tariff("fx_g01_core"), _garage_tariff("fx_g02_a"), _garage_tariff("fx_g08_bands")
    garages = [dataclasses.replace(near, x_m=300.0, y_m=0.0), dataclasses.replace(edge, x_m=0.0, y_m=1000.0),
               dataclasses.replace(beyond, x_m=1000.0, y_m=1.0)]
    activities = pd.DataFrame({"purpose": ["shop", "work"], "x": 0.0, "y": 0.0, "start_time_s": [8 * 3600.0, 8 * 3600.0],
                               "end_time_s": [9 * 3600.0, float("nan")]})
    zone = _zone(zone_id="a")
    stays, notes = cal.build_stays(activities, pd.Series(["a", "a"]), {"a": zone}, garages, max_distance_m=1000.0,
                                   minimum_stay_s=900)
    assert [stay.zone_id for stay in stays] == ["a", "a"] and [stay.purpose for stay in stays] == ["shop", "work"]
    # exactly at D_max counts (1,000.0 m), 1,000.0005 m does not; the options are ordered by id (the production order)
    assert [(garage.garage_id, distance) for garage, distance in stays[0].options] == [
        ("fx_g01_core", 300.0), ("fx_g02_a", 1000.0)]
    assert (stays[0].arrival_s, stays[0].departure_s) == (8 * 3600, 9 * 3600)
    assert (stays[1].arrival_s, stays[1].departure_s) == (8 * 3600, 22 * 3600)   # the terminal stay
    assert notes.terminal_stays == 1 and notes.planned_end_before_arrival == 0


def test_a_stay_in_a_zone_without_a_tariff_row_or_without_a_start_time_is_refused_naming_it(cal):
    activities = pd.DataFrame({"purpose": ["shop"], "x": 0.0, "y": 0.0, "start_time_s": [8 * 3600.0],
                               "end_time_s": [9 * 3600.0]})
    with pytest.raises(ValueError, match="no tariff row for the zone 'ghost'"):
        cal.build_stays(activities, pd.Series(["ghost"]), {"a": _zone(zone_id="a")}, [], max_distance_m=1000.0,
                        minimum_stay_s=900)
    activities["start_time_s"] = float("nan")
    with pytest.raises(ValueError, match="start_time"):
        cal.build_stays(activities, pd.Series(["a"]), {"a": _zone(zone_id="a")}, [], max_distance_m=1000.0,
                        minimum_stay_s=900)


def test_the_outcomes_of_the_stays_are_classified_and_sum_to_the_stays(cal):
    work = dict(purpose="work", arrival_s=8 * 3600, departure_s=16 * 3600)
    capped = _zone(max_stay_min=180)
    stays = [
        _stay([("fx_g01_core", 300.0)]),                                           # an expectation over street and option
        _stay([("fx_g01_core", 300.0)], zone=capped, **work),                      # the street is no option: garages only
        _stay([], zone=capped, **work),                                            # nothing can serve the stay
        _stay([], **work),                                                         # no option in range: priced as the street
        _stay([("fx_g01_core", 300.0)], zone=_zone(free_if_stay_at_most_min=90)),  # E4: the street is free
        _stay([("fx_g01_core", 300.0)], arrival_s=23 * 3600, departure_s=24 * 3600),   # outside the fee window 06:00-22:00
    ]
    outcomes = cal.stay_outcomes(stays, 400.0, 0.310998)
    assert outcomes.stays == 6 and outcomes.expectation == 2 and outcomes.street_infeasible == 1
    assert outcomes.no_feasible_option == 1 and outcomes.street_only == 1 and outcomes.street_free == 1
    assert outcomes.outside_fee_hours == 1
    assert (outcomes.expectation + outcomes.street_only + outcomes.street_free + outcomes.outside_fee_hours
            + outcomes.no_feasible_option) == outcomes.stays


# ------------------------------------------------------------------------------------ universe, checks, end to end


def test_the_destination_universe_leaves_out_home_work_education_and_everything_outside_the_zones(cal):
    activities = pd.DataFrame({"purpose": ["shop", "leisure", "other", "home", "work", "education", "shop", "work"],
                               "x": 0.0, "y": 0.0, "parking_free": False})
    zone_id = pd.Series(["bs_zone_ia", "bs_zone_ib", "bs_zone_ia", "bs_zone_ia", "bs_zone_ia", "bs_zone_ib", np.nan,
                         "other_zone"])
    masks = cal.universe_masks(activities, zone_id, WOLFSBURG_ZONES)
    assert masks.universe.tolist() == [True, True, True, False, False, False, False, False]
    assert masks.commuters.tolist() == [False, False, False, False, True, True, False, False]
    assert not masks.wolfsburg_commuters.any()


def test_the_commuter_universe_of_h2_leaves_out_parking_free_activities_and_the_wolfsburg_check_has_its_own_zones(cal):
    # work and education inside Ia and Ib WITHOUT parkingFree (H2): the employer-lot and free-parking users leave the
    # denominator because the SrV share is among those who park on the street or in a garage; parkingFree does not change
    # the E5 universe (it has no work and education); the Wolfsburg check (H3b) is work and education without parkingFree
    # inside the Wolfsburg zones of the tariff table (the three tariff zones and the area zones, spec v3 B2)
    activities = pd.DataFrame({
        "purpose": ["work", "work", "education", "education", "shop", "work", "education", "work", "work", "home"],
        "parking_free": [False, True, False, True, True, False, False, True, False, False], "x": 0.0, "y": 0.0})
    zone_id = pd.Series(["bs_zone_ia", "bs_zone_ia", "bs_zone_ib", "bs_zone_ib", "bs_zone_ia", "wob_tarifzone_1",
                         "wob_tarifzone_3", "wob_tarifzone_2", "wob_tarifzone_4", "wob_tarifzone_1"])
    masks = cal.universe_masks(activities, zone_id, WOLFSBURG_ZONES)
    assert masks.commuters.tolist() == [True, False, True, False, False, False, False, False, False, False]
    assert masks.universe.tolist() == [False, False, False, False, True, False, False, False, False, False]
    assert masks.wolfsburg_commuters.tolist() == [False, False, False, False, False, True, True, False, False, False]
    assert masks.commuters_with_parking_free == 2   # the two parkingFree work/education activities inside Ia and Ib


def test_a_frame_without_the_parking_free_column_is_refused_instead_of_assuming_nobody_is_free(cal):
    activities = pd.DataFrame({"purpose": ["work"], "x": 0.0, "y": 0.0})
    with pytest.raises(ValueError, match="parking_free"):
        cal.universe_masks(activities, pd.Series(["bs_zone_ia"]), WOLFSBURG_ZONES)


#: The Wolfsburg zones of the synthetic cases (the tariff zones 1 to 3).
WOLFSBURG_ZONES = ("wob_tarifzone_1", "wob_tarifzone_2", "wob_tarifzone_3")


def _tariff_frame(*rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["zone_id", "municipality_ags"])


def test_the_wolfsburg_zones_are_selected_by_municipality_and_an_area_zone_counts(cal, caplog):
    # spec v3 B2: the Wolfsburg area zones wob_area_<id> are zones of Wolfsburg like the tariff zones; a hand-kept list of the
    # three tariff zones silently dropped them, so the selection is by municipality_ags of the tariff table
    tariffs = _tariff_frame(("bs_zone_ia", "03101000"), ("wob_tarifzone_2", "03103000"), ("wob_area_668", "03103000"),
                            ("wob_tarifzone_1", "03103000"), ("sz_lebenstedt", "03102000"), ("wob_area_723", "03103000"))
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        ids = cal.wolfsburg_zone_ids(tariffs)
    assert ids == ("wob_area_668", "wob_area_723", "wob_tarifzone_1", "wob_tarifzone_2")
    assert "4 of the tariff table (2 tariff or other zones, 2 area zones)" in caplog.text
    assert not hasattr(cal, "WOLFSBURG_ZONE_IDS")
    # an activity inside an area zone is a member of the Wolfsburg check universe
    activities = pd.DataFrame({"purpose": ["work", "education", "work", "shop"], "parking_free": False, "x": 0.0, "y": 0.0})
    zone_id = pd.Series(["wob_area_668", "wob_tarifzone_1", "wob_area_999", "wob_area_723"])
    masks = cal.universe_masks(activities, zone_id, ids)
    assert masks.wolfsburg_commuters.tolist() == [True, True, False, False]
    assert "4 Wolfsburg zones (municipality_ags 03103000" in cal.wolfsburg_zone_label(ids)


def test_an_empty_wolfsburg_selection_fails_loudly(cal):
    with pytest.raises(ValueError, match="no zone with municipality_ags 03103000"):
        cal.wolfsburg_zone_ids(_tariff_frame(("bs_zone_ia", "03101000"), ("sz_lebenstedt", "03102000")))
    with pytest.raises(ValueError, match="no Wolfsburg zone is given"):
        cal.universe_masks(pd.DataFrame({"purpose": ["work"], "parking_free": False, "x": 0.0, "y": 0.0}),
                           pd.Series(["bs_zone_ia"]), ())


def test_the_zone_level_paid_share_check_is_gone_the_time_aware_one_is_the_comparison_scripts(cal):
    # R-5-4 (issue #436, Task 5): the zone-level street-paid share was blind to the fee window, the free threshold and the
    # maximum stay and read 1.0 in the zones Ia and Ib, so it carried no information. The paid share of a run is the
    # time-aware one of scripts/parking/compare_parking_targets.py, computed from the outcomes the Java side priced.
    assert not hasattr(cal, "street_paid_share")
    text = SCRIPT.read_text(encoding="utf-8")
    assert "street_paid_share" not in text and "check_street_paid_share_zone_level" not in text
    assert "compare_parking_targets.py" in text


def _tariff_columns() -> list:
    header = next(line for line in (FIXTURES / "parking_tariffs_fixture.csv").read_text(encoding="utf-8").splitlines()
                  if line.startswith("zone_id,"))
    return header.split(",")


def _write_tariffs(path, *, ia=None, ib=None, wob=None) -> Path:
    """The synthetic tariff table of the calibration tests: bs_zone_ia and bs_zone_ib (street, 1.80 EUR per hour in minute
    units, fee window 06:00-22:00, no maximum stay unless ``ia`` / ``ib`` set columns) and wob_tarifzone_1 (the Wolfsburg
    fixture row: 1.20 EUR per started hour, first hour 1.10 EUR, day cap 6.00 EUR, window 06:00-24:00)."""
    base = {"name": "Synthetic zone", "municipality_ags": "03101000", "zone_type": "street_paid", "workplace_class": "bs_zentrum",
            "hourly_rate_eur": "1.80", "billing_unit_min": "1", "fee_start_h": "6.0", "fee_end_h": "22.0",
            "resident_exempt": "false", "source_url": "fixture", "source_date": "2026-09-28", "valid_from": "2026-01-01",
            "fee_window_source": "fixture", "notes": "synthetic: pins arithmetic not truth"}
    wolfsburg = {**base, "municipality_ags": "03103000", "workplace_class": "03103", "hourly_rate_eur": "1.20",
                 "billing_unit_min": "60", "first_period_min": "60", "first_period_eur": "1.10", "daily_cap_eur": "6.00",
                 "fee_end_h": "24.0"}
    rows = {"bs_zone_ia": {**base, **(ia or {})}, "bs_zone_ib": {**base, **(ib or {})},
            "wob_tarifzone_1": {**wolfsburg, **(wob or {})}}
    columns = _tariff_columns()
    lines = [",".join(columns)] + [",".join(str({**{"zone_id": zone_id}, **row}.get(column, "")) for column in columns)
                                   for zone_id, row in rows.items()]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(path)


@pytest.fixture
def inputs(tmp_path):
    """The synthetic universe of tests/fixtures/parking/calibration_plans_fixture.xml: zones, garages, SrV tables."""
    plans = FIXTURES / "calibration_plans_fixture.xml"
    zones = gpd.GeoDataFrame(
        {"zone_id": ["bs_zone_ia", "bs_zone_ib", "wob_tarifzone_1"], "geometry_source": "centre_approximation",
         "source_url": "https://example.org/zones", "source_date": "2026-10-07", "digitised_on": "2026-10-07",
         "digitising_note": "synthetic zone of the calibration test"},
        crs="EPSG:25832", geometry=[box(603000, 5790000, 603200, 5790200), box(603400, 5790000, 603600, 5790200),
                                    box(603700, 5790000, 603900, 5790200)])
    zones_path = tmp_path / "zones.geojson"
    zones.to_crs("EPSG:4326").to_file(zones_path, driver="GeoJSON")
    # two fixture garages moved next to the zones: 300 m and 500 m from the two activity locations; one out of range
    frame = pg.load_garages(FIXTURES / "parking_garages_fixture.geojson", allow_legacy_columns=True).iloc[:2].copy()
    frame["geometry"] = [Point(603100.0, 5790400.0), Point(603100.0, 5792000.0)]
    garages_path = tmp_path / "garages.geojson"
    pg.write_garages(gpd.GeoDataFrame(frame, geometry="geometry", crs="EPSG:25832"), garages_path)
    city_center = tmp_path / "srv2023_city_center_parking.csv"
    # target 0.2718 / (0.2718 + 0.7282) = 0.2718; the mean at lambda 400 m is 0.27176 (derived in the test below)
    city_center.write_text("# synthetic\nparking_type,share,n_unweighted\nemployer_lot,0.01,1\nstreet,0.7282,1\n"
                           "garage_large_lot,0.2718,1\nother,0.01,1\npaid_share_overall,0.8,1\n", encoding="utf-8")
    # synthetic commute table: bs_zentrum paid garage 0.04 / (0.04 + paid street 0.06) = 0.4 (the target of lambda_c), 03103 paid
    # 0.06 / (0.06 + 0.04) = 0.6 (the check); the free-inclusive columns are a decoy (0.5 / 0.6 = 0.833 must not be read)
    commute = tmp_path / "srv2023_commute_parking_by_workplace_class.csv"
    commute.write_text(
        "# synthetic\nworkplace_class,level,n_unweighted,n_eff,share_employer_lot,share_street,share_garage_large_lot,share_other,"
        "share_paid_total,share_free_total,share_garage_large_lot_paid,share_garage_large_lot_free,share_street_paid,"
        "share_street_free\n"
        "bs_zentrum,class,221,104.4,0.1,0.1,0.5,0.0,0.1,0.9,0.04,0.46,0.06,0.04\n"
        "03103,class,691,162.7,0.1,0.1,0.5,0.0,0.1,0.9,0.06,0.44,0.04,0.06\n", encoding="utf-8")
    # the tariff table next to the zone polygons: the Wolfsburg zones of the check universe are selected from it by municipality,
    # and since spec v3 B5 every calibration stay is priced with the tariff of its zone
    _write_tariffs(tmp_path / "parking_tariffs_2026.csv")
    # beta 0 is the price-independent weight of v2: the synthetic numbers below are the closed forms exp(-d / lambda), so the
    # synthetic config says beta 0; the tests of the price term pass their own beta
    config = tmp_path / "config_beta_zero.yml"
    config.write_text("config:\n  parking_garage_max_distance_m: 1000.0\n  parking_option_price_beta_u_per_eur: 0.0\n",
                      encoding="utf-8")
    return {"plans": plans, "zones_path": zones_path, "garages_path": garages_path,
            "city_center_path": city_center, "commute_path": commute, "config_path": config}


def test_the_check_universe_of_h3b_counts_an_area_zone_and_logs_zones_and_activities_per_universe(cal, inputs, tmp_path, caplog):
    # the polygon of wob_tarifzone_1 is renamed wob_area_9 and the tariff table holds the area zone only: the same activities
    # are the Wolfsburg check universe, so an area zone counts
    base = cal.run(out_path=None, **inputs)
    assert base["wolfsburg_activities"] > 0
    zones = gpd.read_file(inputs["zones_path"])
    zones["zone_id"] = zones["zone_id"].replace({"wob_tarifzone_1": "wob_area_9"})
    zones.to_file(inputs["zones_path"], driver="GeoJSON")
    tariffs_path = tmp_path / "parking_tariffs_2026.csv"
    tariffs_path.write_text(tariffs_path.read_text(encoding="utf-8").replace("\nwob_tarifzone_1,", "\nwob_area_9,"),
                            encoding="utf-8")
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        result = cal.run(out_path=None, **inputs)
    assert result["wolfsburg_activities"] == base["wolfsburg_activities"] and result["wolfsburg_mean"] == base["wolfsburg_mean"]
    assert "zones and activities per universe: destination" in caplog.text
    assert f"Wolfsburg check {base['wolfsburg_activities']} activities in 1 of 1 Wolfsburg zones" in caplog.text
    # without a Wolfsburg zone in the tariff table the run stops instead of computing an empty check
    tariffs_path.write_text("\n".join(line for line in tariffs_path.read_text(encoding="utf-8").splitlines()
                                      if ",03103000," not in line) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="municipality_ags 03103000"):
        cal.run(out_path=None, **inputs)


def test_the_calibration_recovers_its_lambda_on_the_synthetic_universe_and_writes_the_table(cal, inputs, tmp_path, caplog):
    # Universe: p01 shop and p02 leisure at 300 m from the garage, p03 other and p04 shop at 500 m (four activities).
    # At lambda 400 m: P(300) = 0.4723665527 / 1.4723665527 = 0.3208213, P(500) = 0.2865047969 / 1.2865047969 = 0.2227
    # mean = (0.3208213 + 0.2227) / 2 = 0.27176; the target 0.2718 is that mean, so lambda is 400 m within the tolerance.
    out = tmp_path / "parking_garage_decay_calibration_2026.csv"
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        result = cal.run(out_path=out, generated_on="2026-10-07", **inputs)
    assert result["universe_activities"] == 4 and result["with_garage_in_range"] == 4
    expected_mean_at_400 = (2 * _probability(300.0, 400.0) + 2 * _probability(500.0, 400.0)) / 4
    assert expected_mean_at_400 == pytest.approx(0.27176, abs=1e-5)
    assert result["target"] == pytest.approx(0.2718, abs=1e-12)
    assert abs(result["decay_length_m"] - 400.0) <= 2.0
    assert abs(result["achieved"] - 0.2718) <= 0.0005
    # H2: the commuter decay on the same plans: p05 work at 300 m and p06 education at 500 m without parkingFree (p14 carries it
    # and is excluded), target 0.04 / (0.04 + 0.06) = 0.4 READ from the paid columns of the bs_zentrum row; the mean at
    # lambda_c equals it
    at_lambda_c = result["decay_commute_length_m"]
    assert result["commuter_universe_activities"] == 2 and result["commuter_activities_excluded_parking_free"] == 1
    assert result["commuter_target"] == pytest.approx(0.4, abs=1e-12)
    mean_at_lambda_c = (_probability(300.0, at_lambda_c) + _probability(500.0, at_lambda_c)) / 2
    assert mean_at_lambda_c == pytest.approx(result["commuter_target"], abs=0.0005)
    # the garages are stored as WGS84 with 7 decimals (about 1 cm), which moves the distances by about 1e-6 in the mean
    assert result["commuter_achieved"] == pytest.approx(mean_at_lambda_c, abs=1e-5)
    assert at_lambda_c == round(at_lambda_c, 2) and at_lambda_c != result["decay_length_m"]
    # the Wolfsburg check (H3b) with lambda_c: p11 work at 761.58 m and p12 education at 743.30 m from the near garage
    # (p13 carries parkingFree and is excluded); a number and no validation, reference 0.06 / (0.06 + 0.04) = 0.6
    assert result["wolfsburg_activities"] == 2 and result["wolfsburg_with_garage_in_range"] == 2
    assert result["wolfsburg_mean"] == pytest.approx(
        (_probability(math.sqrt(700.0 ** 2 + 300.0 ** 2), at_lambda_c)
         + _probability(math.sqrt(700.0 ** 2 + 250.0 ** 2), at_lambda_c)) / 2, abs=5e-5)   # garages at 1 cm WGS84 precision
    assert result["wolfsburg_reference"] == pytest.approx(0.6, abs=1e-12)
    assert "commuter_mean" not in result   # the bs_zentrum share is a target now, not a check
    assert "street_paid_share" not in result and "paid_reference" not in result   # R-5-4: dropped, see the comparison script
    # the table: ASCII, provenance header, the rows, readable by the reader that the config test uses
    text = out.read_bytes().decode("ascii")
    assert text == result["text"] and "\r" not in text
    assert "paid_share" not in text and "compare_parking_targets.py" in text
    for needle in ("calibrate_garage_decay.py on 2026-10-07", "sha256=", "ASSUMPTIONS G1 to G3 and G1-c", "Universe caveat",
                   "bs_zone_ia, bs_zone_ib", "4 activities, 4 of them with at least one priced garage",
                   "garage_large_lot / (garage_large_lot + street) = 0.2718 / (0.2718 + 0.7282)", "no validation",
                   "Commuter universe: the 2 work and education activities", "without parkingFree",
                   "1 more carry parkingFree and are left out",
                   "share_garage_large_lot_paid / (share_garage_large_lot_paid + share_street_paid) = 0.04 / (0.04 + 0.06)",
                   "the payers only", "like-for-like",
                   "class bs_zentrum", "Independent check", "class 03103",
                   "the 1 Wolfsburg zones (municipality_ags 03103000 of the tariff table)",
                   "2 work and education activities", "calibration target"):
        assert needle in text, needle
    assert len(re.findall(r"sha256=[0-9a-f]{64}", text)) == 6   # plans, zones, tariffs, garages and the two SrV tables
    # the two release values are named in the header with the config keys and the test that requires their equality
    assert "parking_garage_decay_m" in text and "parking_garage_decay_commute_m" in text
    assert "decay_commute_length_m" in text
    values = cal.read_calibration_table(out)
    assert values["decay_length_m"] == result["decay_length_m"] and values["universe_activities"] == 4
    assert values["decay_commute_length_m"] == result["decay_commute_length_m"]
    assert values["commuter_universe_activities"] == 2 and values["commuter_activities_excluded_parking_free"] == 1
    assert values["target_garage_probability"] == pytest.approx(0.2718, abs=1e-6)
    assert values["target_commuter_garage_probability"] == pytest.approx(0.4, abs=1e-5)
    assert values["check_wolfsburg_commuter_activities"] == 2
    assert values["check_wolfsburg_commuter_reference_share"] == pytest.approx(0.6, abs=1e-5)
    assert not [name for name in values if name.startswith("check_commuter")]   # the bs_zentrum share is a target now
    assert any("with a priced garage within 1000 m: 4/4" in message for message in caplog.messages)
    assert any("commuter universe 2" in message and "excluded as parkingFree 1" in message for message in caplog.messages)
    assert any("Wolfsburg check" in message and "no validation" in message for message in caplog.messages)


def test_an_existing_table_is_not_replaced_without_overwrite(cal, inputs, tmp_path):
    out = tmp_path / "table.csv"
    out.write_text("# an earlier calibration\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="--overwrite"):
        cal.run(out_path=out, **inputs)
    assert out.read_text(encoding="utf-8") == "# an earlier calibration\n"
    cal.run(out_path=out, overwrite=True, **inputs)
    assert cal.read_calibration_table(out)["decay_length_m"] > 0


def test_a_target_the_synthetic_garages_cannot_reach_fails_and_writes_nothing(cal, inputs, tmp_path):
    inputs["city_center_path"].write_text("parking_type,share,n_unweighted\nstreet,0.01,1\ngarage_large_lot,0.99,1\n"
                                          "paid_share_overall,0.8,1\n", encoding="utf-8")
    out = tmp_path / "table.csv"
    with pytest.raises(ValueError, match="unreachable"):
        cal.run(out_path=out, **inputs)
    assert not out.exists()


def test_a_commuter_universe_without_activities_or_a_wolfsburg_check_without_activities_fails_and_writes_nothing(
        cal, inputs, tmp_path):
    # only free commuters in Ia/Ib: the H2 universe is empty (every commuter leaves the denominator)
    plans = tmp_path / "free_commuters.xml"
    plans.write_text(
        '<population><person id="a"><plan selected="yes"><activity type="home" x="590000" y="5780000"/><leg mode="car"/>'
        '<activity type="shop" x="603100" y="5790100"/><leg mode="car"/>'
        '<activity type="work" x="603100" y="5790100"><attributes><attribute name="parkingFree" '
        'class="java.lang.Boolean">true</attribute></attributes></activity></plan></person></population>', encoding="utf-8")
    out = tmp_path / "table.csv"
    with pytest.raises(ValueError, match="commuter universe is empty"):
        cal.run(**{**inputs, "plans": plans}, out_path=out)
    assert not out.exists()
    # a commuter in Ia but nobody in the Wolfsburg zones: the check has no universe, which is refused, not skipped
    plans.write_text(
        '<population><person id="a"><plan selected="yes"><activity type="home" x="590000" y="5780000"/><leg mode="car"/>'
        '<activity type="shop" x="603100" y="5790100"/><leg mode="car"/>'
        '<activity type="work" x="603100" y="5790100"/></plan></person></population>', encoding="utf-8")
    with pytest.raises(ValueError, match="Wolfsburg check has no universe"):
        cal.run(**{**inputs, "plans": plans}, out_path=out)
    assert not out.exists()


def test_a_plans_file_without_a_destination_of_the_universe_fails(cal, inputs, tmp_path):
    # the only activity is a home inside zone Ia: it lies in a zone, but it is no destination of the universe
    plans = tmp_path / "plans.xml"
    plans.write_text('<population><person id="a"><plan selected="yes"><activity type="home" x="603100" y="5790100"/>'
                     "</plan></person></population>", encoding="utf-8")
    with pytest.raises(ValueError, match="no destination of the universe"):
        cal.run(**{**inputs, "plans": plans}, out_path=None)


def test_the_calibration_table_reader_refuses_a_malformed_table(cal, tmp_path):
    path = tmp_path / "table.csv"
    with pytest.raises(FileNotFoundError):
        cal.read_calibration_table(path)
    path.write_text("# x\nquantity,value\ndecay_length_m,300\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns"):
        cal.read_calibration_table(path)
    path.write_text("quantity,value,unit,note\ntolerance,0.0005,share,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="decay_length_m"):
        cal.read_calibration_table(path)
    # a table written before spec Amendment H has no commuter decay: refused with the name of the missing row
    path.write_text("quantity,value,unit,note\ndecay_length_m,300,m,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="decay_commute_length_m"):
        cal.read_calibration_table(path)
    path.write_text("quantity,value,unit,note\ndecay_length_m,300,m,x\ndecay_commute_length_m,0,m,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="decay_commute_length_m"):
        cal.read_calibration_table(path)
    path.write_text("quantity,value,unit,note\ndecay_length_m,300,m,x\ndecay_length_m,301,m,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        cal.read_calibration_table(path)


def test_the_script_does_not_hard_code_the_target(cal):
    # the SrV shares are read from the table: neither 0.708 nor 0.2537 nor 0.736 appears as a number in the script
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    for number in ("0.708", "0.2537", "0.736", "0.7362", "0.464", "0.4636", "0.1923", "0.2225", "0.1075", "0.0608", "0.639",
                   "0.0965", "0.0456", "0.0225", "0.0080", "0.679", "0.738",
                   "0.6387"):
        assert number not in code


# ------------------------------------------------------------------------------------------ the facility-kind filter (C1)


def _with_kinds(inputs, tmp_path, kinds):
    """The synthetic garages of ``inputs`` with the given ``facility_kind`` per garage (near garage first)."""
    frame = pg.load_garages(inputs["garages_path"]).copy()
    frame["facility_kind"] = kinds
    path = tmp_path / "garages_kinds.geojson"
    pg.write_garages(gpd.GeoDataFrame(frame, geometry="geometry", crs="EPSG:25832"), path)
    return {**inputs, "garages_path": path}


def test_the_facility_kind_filter_keeps_the_selected_kinds_and_the_table_records_it(cal, inputs, tmp_path, caplog):
    # near garage (300 m and 500 m from the universe) is a surface lot, the far one (out of range) a garage: selecting
    # surface lots only leaves the near garage, so lambda stays 400 m; the table records the filter and the garages used
    mixed = _with_kinds(inputs, tmp_path, ["surface_lot", "garage"])
    everything = cal.run(out_path=None, **mixed)
    out = tmp_path / "surface_only.csv"
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        result = cal.run(out_path=out, facility_kinds=("surface_lot",), **mixed)
    assert result["decay_length_m"] == everything["decay_length_m"] and abs(result["decay_length_m"] - 400.0) <= 2.0
    text = out.read_text(encoding="ascii")
    assert "facility kinds: surface_lot" in text and "1 priced of 2 listed" in text
    values = cal.read_calibration_table(out)
    assert values["garage_facility_filter_active"] == 1 and values["priced_garages_used"] == 1
    all_out = tmp_path / "all_kinds.csv"
    cal.run(out_path=all_out, **mixed)
    all_values = cal.read_calibration_table(all_out)
    assert all_values["garage_facility_filter_active"] == 0 and all_values["priced_garages_used"] == 2
    assert "facility kinds: all" in all_out.read_text(encoding="ascii")
    assert any("facility kinds surface_lot" in message for message in caplog.messages)


def test_a_facility_kind_filter_that_leaves_no_reachable_garage_or_names_an_unknown_kind_fails(cal, inputs, tmp_path):
    mixed = _with_kinds(inputs, tmp_path, ["surface_lot", "garage"])
    # only the far garage (out of range of every destination) remains: the target is unreachable, nothing is returned
    with pytest.raises(ValueError, match="unreachable"):
        cal.run(out_path=None, facility_kinds=("garage",), **mixed)
    with pytest.raises(ValueError, match="unknown facility kind 'parkade'"):
        cal.run(out_path=None, facility_kinds=("parkade",), **mixed)
    # a known kind that no priced garage has
    only_garages = _with_kinds(inputs, tmp_path, ["garage", "garage"])
    with pytest.raises(ValueError, match="no priced garage of the facility kind"):
        cal.run(out_path=None, facility_kinds=("surface_lot",), **only_garages)


def test_the_command_line_parses_the_facility_kinds(cal):
    assert cal.parse_facility_kinds("garage") == ("garage",)
    assert cal.parse_facility_kinds("garage,surface_lot") == ("garage", "surface_lot")
    assert cal.parse_facility_kinds("all") is None


# ----------------------------------------------------------------------- release-path guard of a filtered run (G1)


def test_run_refuses_a_filtered_calibration_under_the_release_table_name(cal, inputs, tmp_path):
    mixed = _with_kinds(inputs, tmp_path, ["surface_lot", "garage"])
    release_name = Path(cal.TABLE_RELATIVE).name
    with pytest.raises(ValueError, match="sensitivity number"):
        cal.run(out_path=tmp_path / release_name, facility_kinds=("surface_lot",), **mixed)
    assert not (tmp_path / release_name).exists()
    # an unfiltered run may write the release name, a filtered one another name
    cal.run(out_path=tmp_path / release_name, **mixed)
    cal.run(out_path=tmp_path / "garage_only_sensitivity.csv", facility_kinds=("surface_lot",), **mixed)


def test_the_command_line_refuses_a_filtered_run_without_out_or_onto_the_release_path(cal, tmp_path):
    data = tmp_path / "data"
    release = data / cal.TABLE_RELATIVE
    release.parent.mkdir(parents=True)
    base = ["--plans", str(tmp_path / "plans.xml"), "--data-path", str(data), "--facility-kinds", "surface_lot"]
    with pytest.raises(SystemExit) as no_out:   # --out omitted: the default is the release table
        cal.main(base)
    assert no_out.value.code == 2
    with pytest.raises(SystemExit) as onto_release:
        cal.main(base + ["--out", str(release)])
    assert onto_release.value.code == 2
    with pytest.raises(SystemExit):   # the same path spelled differently
        cal.main(base + ["--out", str(release.parent / ".." / release.parent.name / release.name)])
    assert not release.exists()


# ----------------------------------------------- the maximum garage distance comes from the config, not the code (M6)


def _config_with_distance(tmp_path, text):
    path = tmp_path / "config.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_configured_maximum_distance_is_read_from_the_base_config(cal):
    assert cal.read_configured_max_distance_m() == 1000.0   # configs/base_bs.yml parking_garage_max_distance_m


def test_the_configured_maximum_distance_is_the_value_of_the_config_file_not_the_code_constant(cal, tmp_path):
    config = _config_with_distance(tmp_path, "config:\n  parking_garage_max_distance_m: 750.0\n")
    assert cal.read_configured_max_distance_m(config) == 750.0


@pytest.mark.parametrize("text", [
    "config:\n  parking_zones_enabled: true\n",                        # key missing
    "config:\n  parking_garage_max_distance_m: 0\n",                   # not positive
    "config:\n  parking_garage_max_distance_m: yes\n",                 # a boolean is no distance
    "config:\n  parking_garage_max_distance_m: far\n",                 # not a number
    "stages: []\n",                                                    # no config section
], ids=["missing", "zero", "boolean", "text", "no-config-section"])
def test_an_unusable_configured_maximum_distance_fails_naming_the_key(cal, tmp_path, text):
    with pytest.raises(ValueError, match="parking_garage_max_distance_m"):
        cal.read_configured_max_distance_m(_config_with_distance(tmp_path, text))


def test_run_takes_the_maximum_distance_of_the_config_by_default_and_records_it(cal, inputs, tmp_path):
    # 750 m is another value than the code constant (1000 m); the garage 300 m and 500 m away stays in range
    config = _config_with_distance(tmp_path, "config:\n  parking_garage_max_distance_m: 750.0\n"
                                             "  parking_option_price_beta_u_per_eur: 0.0\n")
    inputs = {**inputs, "config_path": config}
    result = cal.run(out_path=None, generated_on="2026-10-07", **inputs)
    assert "garage_max_distance_m,750,m,ASSUMPTION G2" in result["text"]
    assert "within 750 m" in result["text"]
    explicit = cal.run(out_path=None, max_distance_m=900.0, generated_on="2026-10-07", **inputs)
    assert "garage_max_distance_m,900,m,ASSUMPTION G2" in explicit["text"]   # an explicit value still wins


def test_the_command_line_default_of_the_maximum_distance_is_the_config_key(cal, monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cal, "run", lambda **options: captured.update(options))
    cal.main(["--plans", str(tmp_path / "plans.xml"), "--data-path", str(tmp_path / "data")])
    assert captured["max_distance_m"] is None   # run() resolves it from the config key; no code constant is the default
    parser_help = SCRIPT.read_text(encoding="utf-8")
    assert "parking_garage_max_distance_m" in parser_help
    assert 'default=cost.GARAGE_MAX_DISTANCE_M' not in parser_help.split("def main", 1)[1]


# ------------------------------------------- the target contains surface lots, the Braunschweig option set has none (F3)


def test_the_table_header_names_the_option_set_and_that_the_target_contains_surface_lots(cal, inputs, tmp_path):
    mixed = _with_kinds(inputs, tmp_path, ["surface_lot", "garage"])
    text = cal.run(out_path=None, **mixed)["text"]
    header = [line for line in text.splitlines() if line.startswith("#")]
    option_line = next(line for line in header if line.startswith("# Option set:"))
    assert "1 garage, 1 surface_lot" in option_line
    assert "large surface lots" in option_line and "lambda up" in option_line
    # an option set without a surface lot is called out as such (the Braunschweig dataset has no surface lot)
    garages_only = cal.run(out_path=None, **_with_kinds(inputs, tmp_path, ["garage", "garage"]))["text"]
    assert "2 garage, 0 surface_lot" in garages_only and "holds no surface lot" in garages_only


def test_the_facility_kind_option_says_it_matters_only_where_surface_lots_lie_within_the_maximum_distance(cal):
    text = SCRIPT.read_text(encoding="utf-8")
    assert "matters only where surface lots lie within" in text.split("def main", 1)[1]
    documentation = text.split("def main", 1)[0]
    # before Task 4j the Braunschweig option set held no surface lot, so the garages-only run was vacuous there; since
    # Task 4j (spec Amendment I) it holds the large lots, which the text must not deny any more
    assert "vacuous" in documentation and "since Task 4j" in documentation
    assert "holds 12 garages and no surface lot" not in documentation
    assert "'garage' is vacuous there" not in text.split("def main", 1)[1]


# ------------------------------------------------------------------ the free option and the paid-only measure (I6)


def _fixture_garages():
    """The 19 rows of the garage fixture of the golden parking cases (one of them, fx_g17_free, is the free schedule)."""
    return pg.load_garages(FIXTURES / "parking_garages_fixture.geojson", allow_legacy_columns=True)


@pytest.mark.parametrize("tiers, bands, expected", [
    (None, "0- free", True),                                     # the free schedule: one open free band, no tiers
    (None, None, False),                                         # the single-window form
    (None, "0-20 free; 20-120 total 1.00; 120- 5.00/60", False),    # a free first band that something follows
    (None, "0-30 free", False),                                  # a closed free band is a grace period, not a car park
    ("06:00-18:00 1.00/60", "0-30 free", False),                 # tiers with a grace period
    ("06:00-18:00 1.00/60", "0- free", False),                   # tiers are never free (and the validator refuses this)
    (None, "", False), (float("nan"), None, False),
    (float("nan"), "0- free", True), ("", "0- free", True),      # an empty tiers cell is no tiers
    # the emptiness rule is the one of the garage module (zones._is_set, as garages.coverage counts priced_free): a blank
    # string is a value there, so a blank tiers cell is tiers (the loader turns a blank cell into None before this point)
    ("   ", "0- free", False),
])
def test_a_free_option_is_the_free_schedule_and_nothing_else(cal, tiers, bands, expected):
    assert cal.is_free_option(tiers, bands) is expected


def test_the_free_marker_reads_the_schedule_of_the_rows_and_never_the_name_or_the_rate(cal):
    frame = _fixture_garages()
    paid = cal.paid_option_flags(frame)
    assert paid.dtype == bool and len(paid) == len(frame) == 38
    # of the 38 fixture rows only fx_g17_free and fx_g22_free (golden P02, spec v3) are the free schedule; the grace periods, the
    # free first band of fx_g08_bands and the tiers with a grace period are PAID options
    assert frame.loc[~paid, "garage_id"].tolist() == ["fx_g17_free", "fx_g22_free"]
    # a name that says free does not free a paid option; a name that says paid does not charge a free one
    renamed = frame.copy()
    renamed.loc[renamed["garage_id"] == "fx_g01_core", "name"] = "Kostenlos free gratis"
    renamed.loc[renamed["garage_id"] == "fx_g17_free", "name"] = "Teures Parkhaus 99 EUR"
    assert cal.paid_option_flags(renamed).tolist() == paid.tolist()
    # a rate of zero does not free an option either: only the schedule does (the validator refuses a zero rate anyway)
    zero_rate = frame.copy()
    zero_rate.loc[zero_rate["garage_id"] == "fx_g01_core", "garage_hourly_rate_eur"] = 0.0
    assert cal.paid_option_flags(zero_rate).tolist() == paid.tolist()
    # the id of a row does not decide either (a marker by id text would follow the ids): swap the ids of the free row and a paid
    # row, the flags stay with the schedules and the free row now carries the paid row's id
    ids = frame["garage_id"].tolist()
    free_position, paid_position = ids.index("fx_g17_free"), ids.index("fx_g01_core")
    ids[free_position], ids[paid_position] = ids[paid_position], ids[free_position]
    rotated = frame.copy()
    rotated["garage_id"] = ids
    assert cal.paid_option_flags(rotated).tolist() == paid.tolist()
    assert rotated.loc[~cal.paid_option_flags(rotated), "garage_id"].tolist() == ["fx_g01_core", "fx_g22_free"]


def test_the_free_marker_is_the_one_the_garage_module_counts_as_priced_free(cal):
    # garages.coverage counts priced_free with is_free_schedule(parse_duration_bands(...)): the script marks the same rows
    frame = _fixture_garages()
    assert int((~cal.paid_option_flags(frame)).sum()) == pg.coverage(frame)["priced_free"] == 2


def test_the_option_reach_counts_the_free_options_within_the_maximum_distance_of_a_universe(cal):
    # the options of a stay are already those within D_max (build_stays); g01 and g02 are paid, g17 is free
    stays = [_stay([("fx_g01_core", 300.0), ("fx_g17_free", 900.0)]),      # a paid and the free option in range
             _stay([]),                                                      # nothing in range
             _stay([("fx_g02_a", 400.0)]),                                   # one paid option in range
             _stay([("fx_g17_free", 500.0)]),                                # only the free option in range
             _stay([("fx_g01_core", 1000.0)])]                               # exactly at D_max counts (build_stays keeps it)
    reach = cal.option_reach(stays, ALL_PAID)
    assert reach.activities == 5
    assert reach.with_option_in_range == 4            # stays 0, 2, 3, 4
    assert reach.with_paid_option_in_range == 3       # stays 0, 2, 4
    assert reach.free_options_in_range == 1           # the one free option reaches stays 0 and 3
    assert reach.with_free_option_in_range == 2
    assert reach.free_option_ids == ("fx_g17_free",)
    # no free option at all and an empty universe are zeros, not errors
    none_free = cal.option_reach([stays[0], stays[2]], {"fx_g01_core": True, "fx_g02_a": True, "fx_g17_free": True})
    assert (none_free.free_options_in_range, none_free.with_free_option_in_range) == (0, 0)
    assert none_free.free_option_ids == () and none_free.with_paid_option_in_range == 2
    empty = cal.option_reach([], ALL_PAID)
    assert (empty.activities, empty.with_option_in_range, empty.free_options_in_range) == (0, 0, 0)


def test_the_option_reach_names_the_free_options_in_range_sorted_by_id(cal):
    # two free options (fx_g17_free and a copy named fx_g00_free), each within reach of one stay
    free_copy = dataclasses.replace(_garage_tariff("fx_g17_free"), garage_id="fx_g00_free")
    stays = [_stay([("fx_g01_core", 300.0)]), _stay([("fx_g17_free", 700.0)]),
             cal.Stay(zone_id="z", tariff=_zone(), purpose="shop", arrival_s=0, departure_s=3600,
                      options=((free_copy, 400.0),))]
    reach = cal.option_reach(stays, {**ALL_PAID, "fx_g00_free": False})
    assert reach.free_options_in_range == 2 and reach.free_option_ids == ("fx_g00_free", "fx_g17_free")


def _option_set(inputs, tmp_path, rows, points, kinds=None, name="options.geojson"):
    """The synthetic garages of ``inputs`` replaced by the given rows of the garage fixture, moved to ``points``."""
    frame = _fixture_garages().iloc[rows].copy()
    frame["geometry"] = [Point(*point) for point in points]
    if kinds is not None:
        frame["facility_kind"] = kinds
    path = tmp_path / name
    pg.write_garages(gpd.GeoDataFrame(frame, geometry="geometry", crs="EPSG:25832"), path)
    return {**inputs, "garages_path": path}


#: The near paid garage (300 m from the Ia activities, 500 m from the Ib ones), the far paid garage (out of range of every
#: activity) and a FREE lot 200 m south of the Ia activities (fixture rows 0, 1 and 17).
NEAR_GARAGE, FAR_GARAGE, FREE_LOT = (603100.0, 5790400.0), (603100.0, 5792000.0), (603100.0, 5789900.0)


def _share(distances_m, decay_m):
    """W / (1 + W) with W the sum of exp(-d / decay) over the distances of one activity (all options counted)."""
    weights = sum(math.exp(-distance / decay_m) for distance in distances_m)
    return weights / (1.0 + weights)


def test_a_free_option_changes_lambda_but_not_lambda_c_nor_the_wolfsburg_check(cal, inputs, tmp_path, caplog):
    plain = cal.run(out_path=None, **inputs)
    with_free = _option_set(inputs, tmp_path, [0, 1, 17], [NEAR_GARAGE, FAR_GARAGE, FREE_LOT], ["garage", "garage",
                                                                                              "surface_lot"])
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        result = cal.run(out_path=None, **with_free)
    # E5 (payment-inclusive): the free lot is one more option, so the same target needs a SHORTER decay. Independent check at
    # the result: Ia activities 300 m (near) and 200 m (free lot) away, Ib activities 500 m and sqrt(400^2 + 200^2) = 447.21 m;
    # the mean over the four activities (two each) is the target 0.2718 within the tolerance
    assert result["decay_length_m"] < plain["decay_length_m"] - 20.0
    decay = result["decay_length_m"]
    mean = 0.5 * _share([300.0, 200.0], decay) + 0.5 * _share([500.0, math.hypot(400.0, 200.0)], decay)
    assert mean == pytest.approx(0.2718, abs=0.0006)
    # H2 and H3b (paid-only): the free option leaves numerator and denominator, so nothing changes
    assert result["decay_commute_length_m"] == plain["decay_commute_length_m"]
    assert result["commuter_achieved"] == plain["commuter_achieved"]
    assert result["wolfsburg_mean"] == plain["wolfsburg_mean"]
    # the free lot lies within D_max of every universe and is counted per universe (fallback transparency)
    assert (result["free_options_used"], result["priced_options_used"]) == (1, 3)
    assert result["universe_free_option_ids"] == result["commuter_free_option_ids"] == ("fx_g17_free",)
    assert result["wolfsburg_free_option_ids"] == ("fx_g17_free",)
    assert (result["universe_free_options_in_range"], result["universe_activities_with_free_option"]) == (1, 4)
    assert (result["commuter_free_options_in_range"], result["commuter_activities_with_free_option"]) == (1, 2)
    assert (result["wolfsburg_free_options_in_range"], result["wolfsburg_activities_with_free_option"]) == (1, 2)
    assert (result["commuter_with_paid_option_in_range"], result["wolfsburg_with_paid_option_in_range"]) == (2, 2)
    assert (plain["free_options_used"], plain["universe_free_options_in_range"]) == (0, 0)
    messages = " | ".join(caplog.messages)
    assert "free options within 1000 m: 1 distinct" in messages and "reach 4/4" in messages
    assert "paid options within 1000 m" in messages


def test_the_paid_only_measure_is_not_vacuous_a_paid_option_at_the_same_place_does_change_lambda_c(cal, inputs, tmp_path):
    plain = cal.run(out_path=None, **inputs)
    # fixture row 8 is a PAID banded schedule (it starts with a free band, which is no free car park)
    control = _option_set(inputs, tmp_path, [0, 1, 8], [NEAR_GARAGE, FAR_GARAGE, FREE_LOT])
    result = cal.run(out_path=None, **control)
    assert result["free_options_used"] == 0 and result["universe_free_options_in_range"] == 0
    assert result["decay_commute_length_m"] < plain["decay_commute_length_m"] - 20.0
    assert result["decay_length_m"] < plain["decay_length_m"] - 20.0
    # independent check of the paid-only measure at the result: both options of the commuters count (300 m and 200 m for the
    # Ia commuter, 500 m and 447.21 m for the Ib commuter), the mean equals the target 0.4 (read from the paid columns)
    decay = result["decay_commute_length_m"]
    mean = 0.5 * _share([300.0, 200.0], decay) + 0.5 * _share([500.0, math.hypot(400.0, 200.0)], decay)
    assert mean == pytest.approx(0.4, abs=0.0006)


def test_a_commuter_universe_without_any_paid_option_fails_naming_the_measure(cal, inputs, tmp_path):
    only_free = _option_set(inputs, tmp_path, [17], [FREE_LOT], ["surface_lot"])
    with pytest.raises(ValueError, match="lambda_c.*PAID-only.*unreachable"):
        cal.run(out_path=None, **only_free)


def test_a_commuter_without_a_paid_option_in_range_counts_zero_and_is_reported_separately(cal, inputs, tmp_path):
    # a paid garage 950 m north of the Ia activities (1,030.8 m from the Ib ones, out of range) and the free lot in reach of
    # both: the Ib commuter has a free option in range but no PAID one, which the paid-only measure counts as probability 0.
    # One commuter in reach of one option reaches a mean of at most 0.25, so the commuter target of this test is lowered to
    # 0.01 / (0.01 + 0.09) = 0.1 (the share of the Ia commuter alone: 0.5 * w / (1 + w) = 0.1 for w = 0.25).
    inputs["commute_path"].write_text(
        "# synthetic\nworkplace_class,level,n_unweighted,n_eff,share_garage_large_lot_paid,share_street_paid\n"
        "bs_zentrum,class,221,104.4,0.01,0.09\n03103,class,691,162.7,0.06,0.04\n", encoding="utf-8")
    options = _option_set(inputs, tmp_path, [0, 17], [(603100.0, 5791050.0), FREE_LOT], ["garage", "surface_lot"])
    result = cal.run(out_path=None, **options)
    assert result["commuter_target"] == pytest.approx(0.1, abs=1e-12)
    assert result["commuter_universe_activities"] == 2
    assert result["commuter_with_paid_option_in_range"] == 1       # only the Ia commuter has a paid option in range
    assert result["commuter_with_garage_in_range"] == 2            # but both have an option (the free lot)
    assert result["commuter_activities_with_free_option"] == 2
    # the paid-only mean is 0.5 * w / (1 + w) with w = exp(-950 / lambda_c): independent check at the result
    weight = math.exp(-950.0 / result["decay_commute_length_m"])
    assert 0.5 * weight / (1.0 + weight) == pytest.approx(0.1, abs=0.0006)
    # w = 0.25 at 685.3 m; the tolerance 0.0005 of the mean allows about 3 m at the slope of 0.00016 per metre
    assert result["decay_commute_length_m"] == pytest.approx(950.0 / math.log(4.0), abs=4.0)
    # the Wolfsburg activities (1,180 m and 1,140 m from the paid garage, 728 m and 743 m from the free lot) have no paid
    # option in range: the paid-only check reads 0, a number and no error (the check is no calibration)
    assert result["wolfsburg_with_paid_option_in_range"] == 0 and result["wolfsburg_mean"] == 0.0
    assert result["wolfsburg_activities_with_free_option"] == 2


# ----------------------------------------------------------------- the table says which measure each target uses (I6)


def test_the_table_header_and_rows_say_which_measure_each_target_uses_and_count_the_free_options(cal, inputs, tmp_path):
    with_free = _option_set(inputs, tmp_path, [0, 1, 17], [NEAR_GARAGE, FAR_GARAGE, FREE_LOT], ["garage", "garage",
                                                                                              "surface_lot"])
    out = tmp_path / "parking_garage_decay_calibration_2026.csv"
    result = cal.run(out_path=out, generated_on="2026-10-14", **with_free)
    text = out.read_bytes().decode("ascii")
    header = [line for line in text.splitlines() if line.startswith("#")]
    measures = next(line for line in header if line.startswith("# Measures"))
    for needle in ("spec Amendment I6", "lambda: payment-inclusive", "counts EVERY off-street option",
                   "sum(w) / (w_street + sum(w))", "sum(w) / (1 + sum(w))",
                   "lambda_c and the Wolfsburg check: PAID-only", "sum(w_paid) / (w_street + sum(w_paid))",
                   "sum(w_paid) / (1 + sum(w_paid))", "free option", "free schedule", "cost 0 at every stay duration",
                   "free parker", "leaves numerator and denominator", "the factor W cancels",
                   "W_paid / (W_paid + w_street)", "never a name or a rate"):
        assert needle in measures, needle
    assert "I6" in header[1]
    # every target line names its measure
    target_lambda = next(line for line in header if line.startswith("# Target of lambda:"))
    target_commuter = next(line for line in header if line.startswith("# Target of lambda_c:"))
    assert "payment-inclusive measure (every option counts)" in target_lambda
    assert "PAID-only measure (the free option leaves numerator and denominator)" in target_commuter
    wolfsburg = next(line for line in header if "Wolfsburg: payer-conditional mean" in line)
    assert "PAID-only measure" in wolfsburg
    # the free options within D_max are stated per universe
    universe_line = next(line for line in header if line.startswith("# Universe of lambda:"))
    commuter_line = next(line for line in header if line.startswith("# Commuter universe:"))
    assert "Free options within 1000 m of this universe: 1 distinct (fx_g17_free), reaching 4 of its 4 activities" in universe_line
    assert "Free options within 1000 m of this universe: 1 distinct (fx_g17_free), reaching 2 of its 2 activities" in commuter_line
    # the composition of every universe by purpose is stated (a purpose that does not belong there would be visible)
    assert "By purpose: leisure 1, other 1, shop 2." in universe_line
    assert "By purpose: education 1, work 1." in commuter_line
    assert "2 of 2 activities with at least one PAID option within 1000 m" in commuter_line
    assert "reaching 2 of its 2 activities" in wolfsburg and "1 distinct (fx_g17_free)" in wolfsburg
    assert "By purpose: education 1, work 1." in wolfsburg
    option_line = next(line for line in header if line.startswith("# Option set:"))
    assert "2 garage, 1 surface_lot" in option_line and "1 of the 3 options is free" in option_line
    values = cal.read_calibration_table(out)
    assert values["priced_free_options_used"] == 1 and values["priced_garages_used"] == 3
    assert values["universe_free_options_within_d_max"] == 1 and values["universe_activities_with_free_option_in_range"] == 4
    assert values["commuter_universe_free_options_within_d_max"] == 1
    assert values["commuter_universe_activities_with_free_option_in_range"] == 2
    assert values["commuter_universe_activities_with_paid_option_in_range"] == 2
    assert values["check_wolfsburg_free_options_within_d_max"] == 1
    assert values["check_wolfsburg_activities_with_free_option_in_range"] == 2
    assert values["check_wolfsburg_commuter_activities_with_paid_option_in_range"] == 2
    # the rows of the earlier tables keep their name and meaning (all options)
    assert values["universe_activities_with_garage_in_range"] == 4 == result["with_garage_in_range"]
    assert values["commuter_universe_activities_with_garage_in_range"] == 2
    notes = {line.split(",")[0]: line for line in text.splitlines() if not line.startswith("#")}
    assert "every option" in notes["target_garage_probability"]
    assert "paid-only" in notes["target_commuter_garage_probability"]
    assert "paid-only" in notes["achieved_mean_commuter_garage_probability"]
    assert "paid-only" in notes["check_wolfsburg_commuter_mean_garage_probability"]


def test_the_table_states_that_the_decays_are_bisection_results_within_the_tolerance_and_no_exact_roots(cal, inputs):
    # the search stops at the first midpoint whose mean is within the tolerance of the target, so a decay is not the exact root
    # and option-set differences of the order of the tolerance (divided by the local slope of the mean) are not resolved,
    # the sensitivities included: ONE sentence in the header, stating the rule and quoting no number of this scenario
    text = cal.run(out_path=None, generated_on="2026-10-14", **inputs)["text"]
    header = [line for line in text.splitlines() if line.startswith("#")]
    search = next(line for line in header if line.startswith("# Search for lambda:"))
    for needle in ("first bisection midpoint whose mean lies within the tolerance of the target", "not the exact root",
                   "of the order of the tolerance divided by the local slope of the mean", "not resolved",
                   "the sensitivities below"):
        assert needle in search, needle
    assert text.count("not the exact root") == 1 and text.count("first bisection midpoint") == 1
    assert "0.7 m" not in text and "0.4 m" not in text   # the rule, not the local slopes of one scenario


def test_a_universe_that_the_options_barely_reach_is_warned_about_and_a_covered_one_is_not(cal, inputs, tmp_path, caplog):
    # primary-method coverage (fallback transparency): a universe in which fewer than MINIMUM_REACH_SHARE_WARNING of the
    # activities have an option of the measure within D_max warns, with its name and the numbers
    assert cal.MINIMUM_REACH_SHARE_WARNING == 0.5
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        cal.run(out_path=None, **inputs)   # the near garage reaches every activity of every universe
    assert not [message for message in caplog.messages if "within reach" in message]
    # a garage 400 m west of the Ia activities: 800 m from the Ib ones, 1,100 m from the Wolfsburg activities (out of range),
    # so the Wolfsburg universe (2 activities) has no paid option in reach, while the calibrated universes are covered
    west = _option_set(inputs, tmp_path, [0, 1], [(602700.0, 5790100.0), FAR_GARAGE], name="west.geojson")
    caplog.clear()
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        result = cal.run(out_path=None, **west)
    warned = [message for message in caplog.messages if "within reach" in message]
    assert len(warned) == 1 and "Wolfsburg" in warned[0] and "0/2" in warned[0] and "50 %" in warned[0]
    assert result["wolfsburg_with_paid_option_in_range"] == 0


# ------------------------------------------------------- freight agents are no residents (the person-travel convention)


FREIGHT_PERSON = (
    '<person id="freight_internal_1"><plan selected="yes">'
    '<activity type="freight_start" x="603100" y="5790100" end_time="08:00:00"/><leg mode="truck"/>'
    '<activity type="freight_end" x="603500" y="5790100"/></plan></person>')


def test_freight_agents_are_left_out_of_the_universes_and_counted_in_the_header(cal, inputs, tmp_path, caplog):
    # braunschweig.analysis.freight_filter: freight agents (ids freight_*) are trip creators, not residents, and the SrV
    # shares are about residents. A freight stop inside Ia or Ib would be a destination "other than home, work and education"
    # of the E5 universe (two extra activities here: 4 -> 6) without the exclusion.
    plans = tmp_path / "plans_with_freight.xml"
    fixture = (FIXTURES / "calibration_plans_fixture.xml").read_text(encoding="utf-8")
    plans.write_text(fixture.replace("</population>", FREIGHT_PERSON + "</population>"), encoding="utf-8")
    plain = cal.run(out_path=None, generated_on="2026-10-14", **inputs)
    with caplog.at_level("INFO"):
        result = cal.run(out_path=None, generated_on="2026-10-14", **{**inputs, "plans": plans})
    assert result["universe_activities"] == plain["universe_activities"] == 4
    assert result["freight_agents_excluded"] == 1 and plain["freight_agents_excluded"] == 0
    assert result["persons"] == plain["persons"] == 14      # the 14 persons of the fixture, the freight agent not counted
    assert result["decay_length_m"] == plain["decay_length_m"]
    assert "(14 persons; 1 freight agents with ids freight_* excluded" in result["text"]
    assert "(14 persons; 0 freight agents with ids freight_* excluded" in plain["text"]
    # the shared filter logs what it counts: the two main activities (rows) of the one freight agent
    assert any("excluded 2 rows of 1 freight agents" in message for message in caplog.messages)
    assert any("1 freight agents" in message and "14 persons" in message for message in caplog.messages)


def test_the_reach_warning_threshold_is_a_log_only_option_with_a_documented_default(cal, inputs, tmp_path, caplog):
    # a universe with fewer than the threshold of its activities in reach of an option of its measure is warned about: 0 never
    # warns, the default 0.5 warns below half, a higher threshold also warns for a half-covered universe. Only the log changes.
    west = _option_set(inputs, tmp_path, [0, 1], [(602700.0, 5790100.0), FAR_GARAGE], name="west_option.geojson")
    messages = {}
    for threshold in (0.0, 0.5, 1.0):
        caplog.clear()
        with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
            result = cal.run(out_path=None, minimum_reach_share_warning=threshold, **west)
        messages[threshold] = [m for m in caplog.messages if "within reach" in m]
        assert result["wolfsburg_with_paid_option_in_range"] == 0   # the numbers do not depend on the threshold
    assert messages[0.0] == []
    assert len(messages[0.5]) == 1 and "Wolfsburg" in messages[0.5][0] and "50 %" in messages[0.5][0]
    assert len(messages[1.0]) == 1 and "100 %" in messages[1.0][0]   # the covered universes reach 100 %, not below it
    # the default is the documented constant
    caplog.clear()
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        cal.run(out_path=None, **west)
    assert len([m for m in caplog.messages if "within reach" in m]) == 1 and cal.MINIMUM_REACH_SHARE_WARNING == 0.5
    for bad in (-0.1, 1.5, float("nan"), True, "half"):
        with pytest.raises(ValueError, match="minimum_reach_share_warning"):
            cal.run(out_path=None, minimum_reach_share_warning=bad, **inputs)


def test_the_reach_warning_threshold_applies_to_the_sensitivities_too_with_their_label(cal, inputs, tmp_path, caplog):
    # the release dataset covers every universe; the sensitivity option set barely reaches the Wolfsburg universe: its warning
    # carries the label and follows the threshold that the run was given
    west = _option_set(inputs, tmp_path, [0, 1], [(602700.0, 5790100.0), FAR_GARAGE],
                       name="west_sensitivity.geojson")["garages_path"]
    caplog.clear()
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        cal.run(out_path=None, sensitivity_garages={"west_sensitivity": west}, **inputs)
    warned = [m for m in caplog.messages if "within reach" in m]
    assert len(warned) == 1 and warned[0].startswith("[garage-decay] sensitivity west_sensitivity: Wolfsburg check universe")
    caplog.clear()
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        cal.run(out_path=None, sensitivity_garages={"west_sensitivity": west}, minimum_reach_share_warning=0.0, **inputs)
    assert [m for m in caplog.messages if "within reach" in m] == []


def test_the_command_line_takes_the_reach_warning_threshold_and_refuses_a_value_outside_zero_to_one(cal, monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cal, "run", lambda **options: captured.update(options))
    base = ["--plans", str(tmp_path / "plans.xml"), "--data-path", str(tmp_path / "data")]
    cal.main(base)
    assert captured["minimum_reach_share_warning"] == cal.MINIMUM_REACH_SHARE_WARNING == 0.5
    cal.main(base + ["--minimum-reach-share-warning", "0.2"])
    assert captured["minimum_reach_share_warning"] == 0.2
    for bad in ("1.5", "-0.1", "nan", "half"):
        with pytest.raises(SystemExit) as refused:
            cal.main(base + ["--minimum-reach-share-warning", bad])
        assert refused.value.code == 2
    text = SCRIPT.read_text(encoding="utf-8")
    help_text = text.split("def main", 1)[1]
    assert "--minimum-reach-share-warning" in help_text and "not a scientific parameter" in help_text   # said in the help


# I2: the exact condition of the equality of the per-activity mean and the pooled ratio
def test_the_documentation_states_the_exact_condition_for_the_equality_of_the_mean_and_the_pooled_ratio(cal):
    documentation = " ".join(SCRIPT.read_text(encoding="utf-8").split("def main", 1)[0].split())
    assert "when no free option is in range of any activity of the universe" in documentation
    assert "or where every activity sees the same options" not in documentation
    # the equality it states, checked numerically: without a free option in range the per-activity mean of W / (1 + W)
    # equals sum(p_paid) / sum(p_paid + p_street); with one free option in reach of part of the universe the two differ
    weights = np.array([0.5, 1.5, 3.0])                      # W of three activities, all options paid: W_paid = W
    p_paid, p_street = weights / (1.0 + weights), 1.0 / (1.0 + weights)
    pooled = p_paid.sum() / (p_paid + p_street).sum()
    assert pooled == pytest.approx(np.mean(weights / (1.0 + weights)), abs=1e-12)
    paid_weights = np.array([0.5, 1.5, 1.0])                 # the third activity has a free option of weight 2 in reach
    total = np.array([0.5, 1.5, 3.0])
    pooled_with_free = (paid_weights / (1.0 + total)).sum() / ((paid_weights + 1.0) / (1.0 + total)).sum()
    assert abs(pooled_with_free - np.mean(paid_weights / (1.0 + paid_weights))) > 1e-3


# ---------------------------------------------------------------- the sensitivities of the option set (I6, no release value)


def _dataset_files(inputs, tmp_path):
    """The release dataset of ``inputs`` and two other option sets: with a FREE lot (3 options) and with a PAID lot."""
    free_lot = _option_set(inputs, tmp_path, [0, 1, 17], [NEAR_GARAGE, FAR_GARAGE, FREE_LOT],
                           ["garage", "garage", "surface_lot"], name="with_free_lot.geojson")["garages_path"]
    paid_lot = _option_set(inputs, tmp_path, [0, 1, 8], [NEAR_GARAGE, FAR_GARAGE, FREE_LOT],
                           ["garage", "garage", "surface_lot"], name="with_paid_lot.geojson")["garages_path"]
    return free_lot, paid_lot


def test_the_sensitivities_run_the_same_calibration_on_another_option_set_and_go_into_the_header_only(cal, inputs, tmp_path):
    free_lot, paid_lot = _dataset_files(inputs, tmp_path)
    out = tmp_path / "parking_garage_decay_calibration_2026.csv"
    result = cal.run(out_path=out, generated_on="2026-10-14",
                     sensitivity_garages={"lot_threshold_30": free_lot, "lot_threshold_100": paid_lot}, **inputs)
    release = cal.run(out_path=None, **inputs)
    # the release values are those of the release dataset, whatever the sensitivities say
    assert result["decay_length_m"] == release["decay_length_m"]
    assert result["decay_commute_length_m"] == release["decay_commute_length_m"]
    # each sensitivity equals the direct calibration on its dataset (same plans, targets, D_max and tolerance)
    by_label = {entry["label"]: entry for entry in result["sensitivities"]}
    assert list(by_label) == ["lot_threshold_30", "lot_threshold_100"]
    for label, path in (("lot_threshold_30", free_lot), ("lot_threshold_100", paid_lot)):
        direct = cal.run(out_path=None, **{**inputs, "garages_path": path})
        entry = by_label[label]
        assert entry["decay_length_m"] == direct["decay_length_m"]
        assert entry["decay_commute_length_m"] == direct["decay_commute_length_m"]
        assert entry["achieved"] == direct["achieved"] and entry["commuter_achieved"] == direct["commuter_achieved"]
        assert entry["options"] == 3 and entry["sha256"] == cal.file_sha256(path)
    assert by_label["lot_threshold_30"]["free_options"] == 1 and by_label["lot_threshold_100"]["free_options"] == 0
    assert by_label["lot_threshold_30"]["decay_length_m"] != release["decay_length_m"]
    # the table: header lines labelled sensitivity (never a release value) with the dataset hash; the rows are the release's
    text = out.read_bytes().decode("ascii")
    lines = [line for line in text.splitlines() if line.startswith("# Sensitivity")]
    assert len(lines) == 2
    for line, label, path in zip(lines, ("lot_threshold_30", "lot_threshold_100"), (free_lot, paid_lot)):
        entry = by_label[label]
        assert f"# Sensitivity of the option set (spec Amendment I6; NOT a release value) {label}:" in line
        assert f"sha256={cal.file_sha256(path)}" in line
        assert f"lambda = {entry['decay_length_m']:.2f} m" in line and f"lambda_c = {entry['decay_commute_length_m']:.2f} m" in line
        assert "3 options (2 garage + 1 surface_lot" in line
    assert "1 free" in lines[0] and "0 free" in lines[1]
    assert len(re.findall(r"sha256=[0-9a-f]{64}", text)) == 8   # the six inputs and the two sensitivity datasets
    values = cal.read_calibration_table(out)
    assert values["decay_length_m"] == release["decay_length_m"]
    assert not [name for name in values if "sensitivity" in name or "threshold" in name]
    assert "Sensitivity" not in "\n".join(line for line in text.splitlines() if not line.startswith("#"))


def test_a_run_without_sensitivities_writes_no_sensitivity_line_and_keeps_its_bytes(cal, inputs, tmp_path):
    first = cal.run(out_path=None, generated_on="2026-10-14", **inputs)
    again = cal.run(out_path=None, generated_on="2026-10-14", sensitivity_garages={}, **inputs)
    assert first["text"] == again["text"] and "# Sensitivity" not in first["text"] and first["sensitivities"] == []


def test_a_sensitivity_that_is_the_release_dataset_or_is_badly_named_or_missing_is_refused(cal, inputs, tmp_path):
    release = inputs["garages_path"]
    copy = tmp_path / "copy_of_release.geojson"
    copy.write_bytes(release.read_bytes())
    free_lot, _ = _dataset_files(inputs, tmp_path)
    with pytest.raises(ValueError, match="same SHA-256 as the release dataset"):
        cal.run(out_path=None, sensitivity_garages={"same": copy}, **inputs)
    with pytest.raises(ValueError, match="label"):
        cal.run(out_path=None, sensitivity_garages={"two words": free_lot}, **inputs)
    with pytest.raises(ValueError, match="label"):
        cal.run(out_path=None, sensitivity_garages={"": free_lot}, **inputs)
    with pytest.raises(FileNotFoundError, match="sensitivity dataset lot_threshold_30"):
        cal.run(out_path=None, sensitivity_garages={"lot_threshold_30": tmp_path / "nope.geojson"}, **inputs)
    out = tmp_path / "table.csv"
    with pytest.raises(ValueError):
        cal.run(out_path=out, sensitivity_garages={"same": copy}, **inputs)
    assert not out.exists()   # nothing is written when a sensitivity fails


def test_a_bad_sensitivity_is_refused_before_the_plans_are_read(cal, inputs, tmp_path):
    # the plans (the slow step) are read only after every sensitivity passed its checks: with a plans file that does not exist
    # a bad sensitivity raises ITS error, and the control with a good sensitivity goes on to the plans and raises THEIR error
    options = {**inputs, "plans": tmp_path / "missing_plans.xml"}
    copy = tmp_path / "copy_of_release.geojson"
    copy.write_bytes(inputs["garages_path"].read_bytes())
    free_lot, _ = _dataset_files(inputs, tmp_path)
    with pytest.raises(ValueError, match="same SHA-256 as the release dataset"):
        cal.run(out_path=None, sensitivity_garages={"same": copy}, **options)
    with pytest.raises(ValueError, match="label"):
        cal.run(out_path=None, sensitivity_garages={"two words": free_lot}, **options)
    with pytest.raises(FileNotFoundError, match="sensitivity dataset lot_threshold_30"):
        cal.run(out_path=None, sensitivity_garages={"lot_threshold_30": tmp_path / "nope.geojson"}, **options)
    with pytest.raises(FileNotFoundError, match="missing_plans.xml"):   # the control: the sensitivity is fine, the plans are not
        cal.run(out_path=None, sensitivity_garages={"lot_threshold_30": free_lot}, **options)


def test_a_sensitivity_whose_target_is_unreachable_fails_naming_its_label(cal, inputs, tmp_path):
    # a sensitivity option set with the far garage only: nothing within D_max of any activity
    far = _option_set(inputs, tmp_path, [1], [FAR_GARAGE], name="far_only.geojson")["garages_path"]
    with pytest.raises(ValueError, match="sensitivity far_only.*unreachable"):
        cal.run(out_path=None, sensitivity_garages={"far_only": far}, **inputs)


def test_the_command_line_takes_labelled_sensitivity_datasets(cal, monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cal, "run", lambda **options: captured.update(options))
    base = ["--plans", str(tmp_path / "plans.xml"), "--data-path", str(tmp_path / "data")]
    cal.main(base)
    assert captured["sensitivity_garages"] is None   # no sensitivity unless asked for
    cal.main(base + ["--sensitivity-garages", f"lot_threshold_30={tmp_path / 'a.geojson'}",
                     "--sensitivity-garages", f"lot_threshold_100={tmp_path / 'b.geojson'}"])
    assert captured["sensitivity_garages"] == {"lot_threshold_30": tmp_path / "a.geojson",
                                               "lot_threshold_100": tmp_path / "b.geojson"}
    for bad in (["--sensitivity-garages", "no_equals_sign"], ["--sensitivity-garages", "=path.geojson"],
                ["--sensitivity-garages", "label="],
                ["--sensitivity-garages", f"a={tmp_path / 'a'}", "--sensitivity-garages", f"a={tmp_path / 'b'}"]):
        with pytest.raises(SystemExit) as refused:
            cal.main(base + bad)
        assert refused.value.code == 2


def test_the_release_path_guard_of_a_filtered_run_stays_with_sensitivities(cal, inputs, tmp_path):
    free_lot, _ = _dataset_files(inputs, tmp_path)
    mixed = _with_kinds(inputs, tmp_path, ["surface_lot", "garage"])
    release_name = Path(cal.TABLE_RELATIVE).name
    with pytest.raises(ValueError, match="sensitivity number"):
        cal.run(out_path=tmp_path / release_name, facility_kinds=("surface_lot",), sensitivity_garages={"x": free_lot}, **mixed)
    assert not (tmp_path / release_name).exists()


# ------------------------------------------------------ spec v3 B5: feasibility, the price term, the production pricing (#436)


def _set_tariffs(inputs, **columns):
    """Rewrite the synthetic tariff table next to the zone polygons of ``inputs`` (see ``_write_tariffs``)."""
    return _write_tariffs(Path(inputs["zones_path"]).with_name("parking_tariffs_2026.csv"), **columns)


def _set_commute_target(inputs, garage_paid, street_paid):
    """The commuter targets: bs_zentrum paid garage / (paid garage + paid street); the Wolfsburg row stays 0.06 / 0.10."""
    inputs["commute_path"].write_text(
        "# synthetic\nworkplace_class,level,n_unweighted,n_eff,share_garage_large_lot_paid,share_street_paid\n"
        f"bs_zentrum,class,221,104.4,{garage_paid},{street_paid}\n03103,class,691,162.7,0.06,0.04\n", encoding="utf-8")


def _closed_form(distance_m, decay_m):
    """beta 0: the street weighs 1, a garage exp(-d / lambda); the share w / (1 + w) (the v2 form, derived here)."""
    return _probability(distance_m, decay_m)


BETA = 0.310998   # the money coefficient of the option weights (ASSUMPTION G1-d), the value of the config


def test_the_calibration_weights_every_stay_by_the_price_term_and_a_mutant_with_the_old_formula_fails(cal, inputs, monkeypatch):
    # Universe of the synthetic plans (activity times in tests/fixtures/parking/calibration_plans_fixture.xml; street 1.80 EUR
    # per hour in minute units; the near garage fx_g01_core 2.00 EUR per started hour, monthly product 63.00 EUR = 3.00 EUR
    # per working day for work and education): visitors p01 (1 h, Ia, 300 m), p02 (2 h, Ia, 300 m), p03 and p04 (1 h, Ib,
    # 500 m): street 180 / 360 / 180 / 180 ct, garage 200 / 400 / 200 / 200 ct. Commuters p05 (8 h, Ia, 300 m) and p06 (7 h,
    # Ib, 500 m): street 1,440 and 1,260 ct, garage min(1,600; 300) = 300 ct (the monthly product per day).
    inputs = {**inputs, "option_price_beta_u_per_eur": BETA}
    result = cal.run(out_path=None, **inputs)

    def visitor_mean(decay):
        stays = ((300.0, 180, 200), (300.0, 360, 400), (500.0, 180, 200), (500.0, 180, 200))
        return sum(_priced_share(d, decay, BETA, street, garage) for d, street, garage in stays) / 4

    def commuter_mean(decay):
        return (_priced_share(300.0, decay, BETA, 1440, 300) + _priced_share(500.0, decay, BETA, 1260, 300)) / 2

    def wolfsburg_mean(decay):   # street: first hour 1.10 EUR + started hours at 1.20, capped at 6.00 EUR; garage 300 ct
        return (_priced_share(math.hypot(700.0, 300.0), decay, BETA, 600, 300)
                + _priced_share(math.hypot(700.0, 250.0), decay, BETA, 600, 300)) / 2

    # the results satisfy the independent price-weighted means (the targets are 0.2718 and 0.4 of the synthetic tables)
    assert visitor_mean(result["decay_length_m"]) == pytest.approx(0.2718, abs=0.0007)
    assert commuter_mean(result["decay_commute_length_m"]) == pytest.approx(0.4, abs=0.0007)
    assert result["wolfsburg_mean"] == pytest.approx(wolfsburg_mean(result["decay_commute_length_m"]), abs=1e-5)
    assert result["option_price_beta_u_per_eur"] == BETA
    # the price changes the answer: the v2 lambdas (beta 0) are another pair
    v2 = cal.run(out_path=None, **{key: value for key, value in inputs.items() if key != "option_price_beta_u_per_eur"})
    assert abs(v2["decay_length_m"] - result["decay_length_m"]) > 10.0
    assert abs(v2["decay_commute_length_m"] - result["decay_commute_length_m"]) > 100.0
    assert abs(v2["decay_length_m"] - 400.0) <= 2.0
    # the mutant: the old closed form exp(-d / lambda) with street weight 1 in place of the production weight. With beta > 0 it
    # calibrates another quantity, and the independent price-weighted mean misses the target at its lambdas
    monkeypatch.setattr(cost, "option_weight", lambda distance_m, decay_m, cost_cents, beta: math.exp(-distance_m / decay_m))
    mutant = cal.run(out_path=None, **inputs)
    assert abs(visitor_mean(mutant["decay_length_m"]) - 0.2718) > 0.005
    assert abs(commuter_mean(mutant["decay_commute_length_m"]) - 0.4) > 0.05
    assert mutant["decay_length_m"] == pytest.approx(v2["decay_length_m"], abs=1e-9)   # it IS the v2 calibration


def test_beta_zero_reproduces_the_v2_closed_form_values_on_the_synthetic_set(cal, inputs):
    # the v2 table values were the closed form exp(-d / lambda), street weight 1: with beta 0 the production weights give
    # them again (lambda 400 m on the four visitors, 4 x 0.27176 = the target 0.2718; lambda_c from the two commuters)
    result = cal.run(out_path=None, **inputs)
    assert result["option_price_beta_u_per_eur"] == 0.0   # the synthetic config says beta 0
    mean = (2 * _closed_form(300.0, result["decay_length_m"]) + 2 * _closed_form(500.0, result["decay_length_m"])) / 4
    assert mean == pytest.approx(0.2718, abs=0.0006) and abs(result["decay_length_m"] - 400.0) <= 2.0
    commuters = (_closed_form(300.0, result["decay_commute_length_m"]) + _closed_form(500.0, result["decay_commute_length_m"])) / 2
    assert commuters == pytest.approx(0.4, abs=0.0006)


def test_a_commuter_stay_above_the_maximum_stay_of_its_zone_is_forced_off_street(cal, inputs, caplog):
    # spec v3 A1 / B5: zone Ia has a maximum stay of 180 min and no long-stay product, so the 8 h commuter p05 has NO street
    # option: its off-street share is 1 (the garage is feasible, no maximum stay) however long lambda_c is. The Ib commuter
    # p06 (7 h, no maximum stay) is a normal choice. The target 0.07 / (0.07 + 0.03) = 0.7 is reached with the Ib share
    # 0.4: mean = (1 + P) / 2 = 0.7 -> P = 0.4 -> w = 2/3 -> 500 / lambda_c = ln 1.5 -> lambda_c = 1,233.2 m (beta 0)
    _set_tariffs(inputs, ia={"max_stay_min": "180"})
    _set_commute_target(inputs, 0.07, 0.03)
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        result = cal.run(out_path=None, **inputs)
    assert result["decay_commute_length_m"] == pytest.approx(500.0 / math.log(1.5), abs=25.0)
    assert (1.0 + _closed_form(500.0, result["decay_commute_length_m"])) / 2 == pytest.approx(0.7, abs=0.0006)
    outcomes = result["commuter_outcomes"]
    assert (outcomes.stays, outcomes.expectation, outcomes.street_infeasible, outcomes.no_feasible_option) == (2, 2, 1, 0)
    assert result["commuter_no_feasible_option"] == 0
    assert any("street infeasible 1" in message and "forced off-street" in message for message in caplog.messages)
    # the visitors (1 h and 2 h stays) are within the maximum stay of the zone: none is forced
    assert result["universe_outcomes"].street_infeasible == 0


def test_a_commuter_with_no_feasible_option_counts_zero_is_reported_as_a_rate_and_warned_about(cal, inputs, tmp_path, caplog):
    # the only garage lies 900 m from the Ib activities and 1,300 m from the Ia ones: the Ia commuter p05 has no street option
    # (maximum stay 180 min, no long-stay product) and no garage in range, i.e. NO FEASIBLE OPTION. The production pricing
    # prices it as the street; the calibration gives it NO off-street share (0, never an invented 1), counts it and logs the
    # rate. The target 0.02 / (0.02 + 0.08) = 0.2 is reached with the Ib share 0.4: mean = (0 + P) / 2 = 0.2, w = 2/3,
    # 900 / lambda_c = ln 1.5 -> lambda_c = 2,219.6 m (beta 0)
    _set_tariffs(inputs, ia={"max_stay_min": "180"})
    _set_commute_target(inputs, 0.02, 0.08)
    east = _option_set(inputs, tmp_path, [0, 1], [(604400.0, 5790100.0), FAR_GARAGE], name="east.geojson")
    # the east garage reaches the two Ib visitors only (900 m): the visitor target is lowered to 0.1 / (0.1 + 0.9) = 0.1
    inputs["city_center_path"].write_text("parking_type,share,n_unweighted\ngarage_large_lot,0.1,1\nstreet,0.9,1\n"
                                          "paid_share_overall,0.8,1\n", encoding="utf-8")
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        result = cal.run(out_path=None, generated_on="2026-10-14", **east)
    assert result["commuter_no_feasible_option"] == 1 and result["commuter_universe_activities"] == 2
    assert 0.5 * _closed_form(900.0, result["decay_commute_length_m"]) == pytest.approx(0.2, abs=0.0006)
    assert result["commuter_outcomes"].no_feasible_option == 1 and result["commuter_outcomes"].expectation == 1
    assert any("commuter universe" in message and "no feasible option 1/2 (50.0 %)" in message for message in caplog.messages)
    warned = [message for message in caplog.messages if "no feasible option" in message and "above the warning share" in message]
    assert len(warned) == 1 and "commuter universe" in warned[0] and "1/2" in warned[0]
    # the threshold is log-only: a higher one stays silent, the result does not change
    caplog.clear()
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        quiet = cal.run(out_path=None, maximum_no_feasible_option_share_warning=0.6, **east)
    assert not [message for message in caplog.messages if "above the warning share" in message]
    assert quiet["decay_commute_length_m"] == result["decay_commute_length_m"]
    for bad in (-0.1, 1.5, float("nan"), True):
        with pytest.raises(ValueError, match="maximum_no_feasible_option_share_warning"):
            cal.run(out_path=None, maximum_no_feasible_option_share_warning=bad, **inputs)
    # the table states the count and the definition
    text = result["text"]
    assert "NO FEASIBLE OPTION" in text and "contributes an off-street share of 0" in text and "never an invented" in text
    out = tmp_path / "no_feasible.csv"
    cal.run(out_path=out, generated_on="2026-10-14", **east)
    assert cal.read_calibration_table(out)["commuter_universe_stays_no_feasible_option"] == 1


def test_a_stay_with_a_free_street_or_outside_the_fee_window_has_no_off_street_share_and_is_counted(cal, inputs):
    # production rules that now enter the calibration through the pricing code: E4 (a free street: nobody pays an option)
    # and the fee window (outside it the street is free). Zone Ia: stays of at most 90 min are free, so the 1 h visitor p01
    # is a free-street stay; its 2 h neighbour p02 pays. The two Ib visitors are normal choices (beta 0, closed form).
    _set_tariffs(inputs, ia={"free_if_stay_at_most_min": "90"})
    result = cal.run(out_path=None, **inputs)
    outcomes = result["universe_outcomes"]
    assert (outcomes.stays, outcomes.street_free, outcomes.expectation, outcomes.outside_fee_hours) == (4, 1, 3, 0)
    decay = result["decay_length_m"]
    mean = (0.0 + _closed_form(300.0, decay) + 2 * _closed_form(500.0, decay)) / 4
    assert mean == pytest.approx(0.2718, abs=0.0006)
    # the fee window of zone Ia is 08:00-18:00: the 19:00-21:00 stay of p02 lies outside it (the street is free, early rule)
    _set_tariffs(inputs, ia={"fee_start_h": "8.0", "fee_end_h": "18.0"})
    window = cal.run(out_path=None, **inputs)
    assert (window["universe_outcomes"].outside_fee_hours, window["universe_outcomes"].expectation) == (1, 3)


def test_a_universe_activity_without_a_start_time_fails_before_a_table_is_written(cal, inputs, tmp_path):
    plans = tmp_path / "no_times.xml"
    plans.write_text(
        '<population><person id="a"><plan selected="yes"><activity type="home" x="590000" y="5780000"/><leg mode="car"/>'
        '<activity type="shop" x="603100" y="5790100" end_time="09:00:00"/><leg mode="car"/>'
        '<activity type="work" x="603100" y="5790100" start_time="08:00:00" end_time="16:00:00"/>'
        '<activity type="work" x="603800" y="5790100" start_time="08:00:00" end_time="16:00:00"/></plan></person>'
        "</population>", encoding="utf-8")
    out = tmp_path / "table.csv"
    with pytest.raises(ValueError, match="start_time"):
        cal.run(**{**inputs, "plans": plans}, out_path=out)
    assert not out.exists()


def test_the_table_states_the_price_coefficient_the_pricing_parameters_and_the_outcomes_of_every_universe(cal, inputs, tmp_path):
    out = tmp_path / "calibration_beta_0.310998.csv"   # a beta other than the config's: not the release name
    result = cal.run(out_path=out, generated_on="2026-10-14", **{**inputs, "option_price_beta_u_per_eur": BETA})
    text = out.read_bytes().decode("ascii")
    header = [line for line in text.splitlines() if line.startswith("#")]
    beta_line = next(line for line in header if line.startswith("# Option price coefficient"))
    for needle in ("ASSUMPTION G1-d", "option_price_beta_u_per_eur 0.310998", "exp(-d / lambda - beta * c / 100)",
                   "cost.option_weight", "parking_cost_with_garages_detail", "fixed during the calibration"):
        assert needle in beta_line, needle
    stays_line = next(line for line in header if line.startswith("# Stays:"))
    for needle in ("start_time", "end_time", "minimum stay of 15 min", "terminal-stay rule", "parkingEvExempt",
                   "ASSUMPTION", "not applied", "limitation"):
        assert needle in stays_line, needle
    values = cal.read_calibration_table(out)
    assert values["option_price_beta_u_per_eur"] == BETA and values["parking_minimum_stay_min"] == 15
    assert values["universe_stays_expectation"] == 4 and values["universe_stays_no_feasible_option"] == 0
    assert values["commuter_universe_stays_expectation"] == 2 and values["check_wolfsburg_stays_expectation"] == 2
    for prefix in ("universe", "commuter_universe", "check_wolfsburg"):
        names = ("expectation", "street_only", "street_free", "outside_fee_hours", "no_feasible_option")
        assert sum(values[f"{prefix}_stays_{name}"] for name in names) == values[
            {"universe": "universe_activities", "commuter_universe": "commuter_universe_activities",
             "check_wolfsburg": "check_wolfsburg_commuter_activities"}[prefix]]
        assert f"{prefix}_stays_street_infeasible" in values
    # the inputs: the tariff table joins the plans, zones, garages and the two SrV tables as a pinned input
    assert re.search(r"#   tariffs: \S+parking_tariffs_2026.csv sha256=[0-9a-f]{64}", text)
    assert len(re.findall(r"sha256=[0-9a-f]{64}", text)) == 6
    assert result["text"] == text


def test_the_beta_sensitivity_runs_the_same_calibration_with_another_coefficient_and_is_no_release_value(cal, inputs, tmp_path):
    released = {**inputs, "option_price_beta_u_per_eur": BETA}
    out = tmp_path / "calibration_beta_0.310998.csv"
    result = cal.run(out_path=out, generated_on="2026-10-14", sensitivity_betas=[0.0], **released)
    direct = cal.run(out_path=None, **inputs)   # the synthetic config says beta 0
    [entry] = result["beta_sensitivities"]
    assert entry["label"] == "beta_0" and entry["option_price_beta_u_per_eur"] == 0.0
    assert entry["decay_length_m"] == direct["decay_length_m"]
    assert entry["decay_commute_length_m"] == direct["decay_commute_length_m"]
    assert entry["decay_length_m"] != result["decay_length_m"]
    release = cal.run(out_path=None, **released)
    assert result["decay_length_m"] == release["decay_length_m"]   # the release values do not move with a sensitivity
    lines = out.read_text(encoding="ascii").splitlines()
    line = next(line for line in lines if line.startswith("# Sensitivity of the option price"))
    assert "NOT a release value" in line and "beta_0" in line and "option_price_beta_u_per_eur 0" in line
    assert f"lambda = {entry['decay_length_m']:.2f} m" in line and f"lambda_c = {entry['decay_commute_length_m']:.2f} m" in line
    assert "the price-independent weights of v2" in line
    values = cal.read_calibration_table(out)
    assert values["option_price_beta_u_per_eur"] == BETA and values["decay_length_m"] == release["decay_length_m"]
    # a sensitivity with the release coefficient is no sensitivity; a negative or non-numeric one is refused
    with pytest.raises(ValueError, match="same as the release"):
        cal.run(out_path=None, sensitivity_betas=[BETA], **released)
    for bad in (-0.1, float("nan"), True, "zero"):
        with pytest.raises(ValueError, match="option_price_beta_u_per_eur"):
            cal.run(out_path=None, sensitivity_betas=[bad], **released)


def test_a_run_with_another_beta_than_the_config_must_not_overwrite_the_release_table(cal, inputs, tmp_path):
    config = tmp_path / "config.yml"
    config.write_text("config:\n  parking_garage_max_distance_m: 1000.0\n  parking_option_price_beta_u_per_eur: 0.310998\n",
                      encoding="utf-8")
    release_name = Path(cal.TABLE_RELATIVE).name
    options = {key: value for key, value in inputs.items() if key != "config_path"}
    with pytest.raises(ValueError, match="sensitivity number"):
        cal.run(out_path=tmp_path / release_name, config_path=config, option_price_beta_u_per_eur=0.0, **options)
    assert not (tmp_path / release_name).exists()
    cal.run(out_path=tmp_path / "beta_zero_sensitivity.csv", config_path=config, option_price_beta_u_per_eur=0.0, **options)
    # without an override the beta of the config is the release value and may be written under the release name
    default = cal.run(out_path=tmp_path / release_name, config_path=config, **options)
    assert default["option_price_beta_u_per_eur"] == 0.310998


def test_the_pricing_parameters_come_from_the_config_with_the_pipeline_defaults_and_beta_defaults_to_the_config_key(cal, tmp_path):
    from braunschweig.matsim.simulation import prepare

    defaults = prepare.PARKING_DEFAULTS
    base = cal.read_pricing_config(REPO / "configs" / "base_bs.yml")
    assert base.option_price_beta_u_per_eur == defaults["parking_option_price_beta_u_per_eur"] == BETA
    assert base.minimum_stay_min == 15 and base.max_distance_m == 1000.0 and base.monthly_imputation is True
    assert base.tariff_snapshot_date == defaults["parking_tariff_snapshot_date"]
    assert base.terminal_stay_rule == "until_fee_end"
    # a config that carries the keys wins over the pipeline defaults; D_max stays mandatory
    config = tmp_path / "config.yml"
    config.write_text("config:\n  parking_garage_max_distance_m: 750.0\n  parking_option_price_beta_u_per_eur: 0.5\n"
                      "  parking_minimum_stay_min: 30\n  parking_garage_monthly_imputation: false\n"
                      "  parking_tariff_snapshot_date: '2026-10-01'\n", encoding="utf-8")
    custom = cal.read_pricing_config(config)
    assert (custom.max_distance_m, custom.option_price_beta_u_per_eur, custom.minimum_stay_min) == (750.0, 0.5, 30)
    assert custom.monthly_imputation is False and custom.tariff_snapshot_date == "2026-10-01"
    # an explicit beta (a sensitivity) overrides the config; a config without the key falls back to the default
    assert cal.read_pricing_config(config, option_price_beta_u_per_eur=0.0).option_price_beta_u_per_eur == 0.0
    bare = tmp_path / "bare.yml"
    bare.write_text("config:\n  parking_garage_max_distance_m: 750.0\n", encoding="utf-8")
    assert cal.read_pricing_config(bare).option_price_beta_u_per_eur == BETA
    assert cal.read_pricing_config(bare).minimum_stay_min == defaults["parking_minimum_stay_min"]
    # unusable values fail naming the key
    for text, key in (("parking_option_price_beta_u_per_eur: -1", "parking_option_price_beta_u_per_eur"),
                      ("parking_minimum_stay_min: -5", "parking_minimum_stay_min"),
                      ("parking_garage_monthly_imputation: maybe", "parking_garage_monthly_imputation"),
                      ("parking_terminal_stay_rule: never", "parking_terminal_stay_rule")):
        broken = tmp_path / "broken.yml"
        broken.write_text("config:\n  parking_garage_max_distance_m: 750.0\n  " + text + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match=key):
            cal.read_pricing_config(broken)


def test_the_command_line_takes_the_beta_and_the_beta_sensitivities(cal, monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cal, "run", lambda **options: captured.update(options))
    base = ["--plans", str(tmp_path / "plans.xml"), "--data-path", str(tmp_path / "data")]
    cal.main(base)
    assert captured["option_price_beta_u_per_eur"] is None and captured["sensitivity_betas"] is None   # the config decides
    assert captured["maximum_no_feasible_option_share_warning"] == cal.MAXIMUM_NO_FEASIBLE_OPTION_SHARE_WARNING
    cal.main(base + ["--beta", "0", "--out", str(tmp_path / "beta_zero_sensitivity.csv")])
    assert captured["option_price_beta_u_per_eur"] == 0.0
    cal.main(base + ["--sensitivity-beta", "0", "--sensitivity-beta", "0.5"])
    assert captured["sensitivity_betas"] == [0.0, 0.5]
    cal.main(base + ["--maximum-no-feasible-option-share-warning", "0.25"])
    assert captured["maximum_no_feasible_option_share_warning"] == 0.25
    for bad in (["--beta", "-1"], ["--beta", "nan"], ["--sensitivity-beta", "x"], ["--sensitivity-beta", "-0.2"],
                ["--maximum-no-feasible-option-share-warning", "1.5"]):
        with pytest.raises(SystemExit) as refused:
            cal.main(base + bad)
        assert refused.value.code == 2
    # a beta that differs from the config is a sensitivity number: it must not be written onto the release table
    release = tmp_path / "data" / cal.TABLE_RELATIVE
    release.parent.mkdir(parents=True)
    with pytest.raises(SystemExit) as onto_release:
        cal.main(base + ["--beta", "0", "--out", str(release)])
    assert onto_release.value.code == 2


def test_the_module_documentation_says_the_calibration_routes_through_the_production_pricing(cal):
    documentation = " ".join(SCRIPT.read_text(encoding="utf-8").split("def main", 1)[0].split())
    for needle in ("cost.parking_cost_with_garages_detail", "cost.option_weight", "NO FEASIBLE OPTION", "off-street share of 0",
                   "parkingEvExempt", "ASSUMPTION", "price term", "feasible"):
        assert needle in documentation, needle
    # no second implementation of the weight: the closed form of v2 is gone from the code
    code = "\n".join(line for line in SCRIPT.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    assert "math.exp" not in code and "np.exp" not in code and not re.search(r"\bmean_garage_probability\(", code)


# ------------------------------------------- B5 fix round 1 (ruling R-B-16): the payer-conditional commuter measure, sensitivities


def test_a_zero_priced_stay_leaves_the_payer_conditional_measure_is_counted_and_the_visitor_measure_is_unchanged(cal):
    # R-B-16: the lambda_c target is the PAID-only SrV share among payers (R-4h-1), so a stay that the model prices at 0 ct is no
    # payer and leaves numerator AND denominator: here a free street (E4), a stay outside the street fee window and a stay whose
    # street is infeasible and whose only feasible option is free (expected cost exactly 0)
    normal = _stay([("fx_g01_core", 300.0)])
    free_street = _stay([("fx_g01_core", 300.0)], zone=_zone(free_if_stay_at_most_min=90))
    outside_window = _stay([("fx_g01_core", 300.0)], arrival_s=23 * 3600, departure_s=24 * 3600)
    only_free = _stay([("fx_g17_free", 300.0)], zone=_zone(max_stay_min=30), purpose="work", arrival_s=8 * 3600,
                      departure_s=16 * 3600)
    stays = [normal, free_street, outside_window, only_free]
    share = _probability(300.0, 400.0)          # beta 0: the street weighs 1, the garage exp(-0.75)
    paid = cal.MEASURE_PAID_OPTIONS
    # the payer-conditional mean is the share of the only payer; the payer-inclusive mean divides by all four stays
    assert cal.mean_option_share(stays, 400.0, 0.0, paid, ALL_PAID, payers_only=True) == pytest.approx(share, abs=1e-12)
    assert cal.mean_option_share(stays, 400.0, 0.0, paid, ALL_PAID) == pytest.approx(share / 4, abs=1e-12)
    # the visitor measure (payment-inclusive, every stay) is unchanged: the free-only stay is a full off-street stay there
    every = cal.MEASURE_ALL_OPTIONS
    assert cal.mean_option_share(stays, 400.0, 0.0, every, ALL_PAID) == pytest.approx((share + 1.0) / 4, abs=1e-12)
    # the zero-priced stays are counted
    outcomes = cal.stay_outcomes(stays, 400.0, 0.0)
    assert outcomes.stays == 4 and outcomes.non_payers == 3
    assert (outcomes.street_free, outcomes.outside_fee_hours, outcomes.expectation) == (1, 1, 2)
    assert cal.stay_outcomes([normal], 400.0, 0.0).non_payers == 0
    # a universe without any payer cannot be averaged: refused, never read as 0
    with pytest.raises(ValueError, match="no payer"):
        cal.mean_option_share([free_street, outside_window], 400.0, 0.0, paid, ALL_PAID, payers_only=True)


def test_a_mutant_that_keeps_the_garage_price_term_but_gives_the_street_weight_one_gives_another_share(cal, monkeypatch):
    # the second mutant: the street weight 1 (the v2 street) with the price term kept on the garages. With beta > 0 and a street
    # that costs something it differs from the production weight exp(-beta * c_street / 100)
    stay = _stay([("fx_g01_core", 300.0)])
    production = cal.mean_option_share([stay], 400.0, BETA, cal.MEASURE_ALL_OPTIONS, ALL_PAID)
    original = cost.option_weight
    monkeypatch.setattr(cost, "option_weight", lambda d, decay, cents, beta: 1.0 if d == 0.0 else original(d, decay, cents, beta))
    mutant = cal.mean_option_share([stay], 400.0, BETA, cal.MEASURE_ALL_OPTIONS, ALL_PAID)
    # street 180 ct: production street weight exp(-0.310998 * 1.8) = 0.5714, the mutant 1
    assert abs(mutant - production) > 0.02
    assert mutant == pytest.approx(math.exp(-0.75 - BETA * 2.0) / (1.0 + math.exp(-0.75 - BETA * 2.0)), abs=1e-12)


def _payer_scenario(inputs):
    """Zone Ib charges only from 16:00 to 22:00, so the Ib stays (p03 and p04 visitors, p06 commuter, all before 16:00) lie outside
    the street fee window; the Ia stays (p01, p02 visitors, p05 commuter) pay. The visitor target is lowered to 0.1 (the two
    paying visitors alone reach at most 0.5 * 0.5 = 0.25 of four stays)."""
    _set_tariffs(inputs, ib={"fee_start_h": "16.0", "fee_end_h": "22.0"})
    inputs["city_center_path"].write_text("parking_type,share,n_unweighted\ngarage_large_lot,0.1,1\nstreet,0.9,1\n"
                                          "paid_share_overall,0.8,1\n", encoding="utf-8")
    return inputs


def test_the_commuter_measure_leaves_the_non_payers_out_of_numerator_and_denominator_but_lambda_keeps_every_stay(
        cal, inputs, caplog, tmp_path):
    # beta 0 closed forms. Commuters: p05 (Ia, 300 m) pays, p06 (Ib, outside the window) is a non-payer and leaves: the mean is
    # P(300) alone, target 0.04 / (0.04 + 0.06) = 0.4 -> w = 2/3 -> lambda_c = 300 / ln 1.5 = 739.9 m. With p06 counted as 0 the
    # mean would be P(300) / 2 <= 0.25 and the target unreachable. Visitors: p01 and p02 pay, p03 and p04 are outside the window
    # but STAY in the mean (the SrV street category contains free parkers): 2 P / 4 = 0.1 -> P = 0.2 -> lambda = 300 / ln 4.
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        result = cal.run(out_path=None, generated_on="2026-10-14", **_payer_scenario(inputs))
    assert result["decay_commute_length_m"] == pytest.approx(300.0 / math.log(1.5), abs=8.0)
    assert _closed_form(300.0, result["decay_commute_length_m"]) == pytest.approx(0.4, abs=0.0006)
    assert result["decay_length_m"] == pytest.approx(300.0 / math.log(4.0), abs=2.0)
    assert 2 * _closed_form(300.0, result["decay_length_m"]) / 4 == pytest.approx(0.1, abs=0.0006)   # all four visitors count
    commuters, visitors = result["commuter_outcomes"], result["universe_outcomes"]
    assert (commuters.stays, commuters.non_payers, commuters.outside_fee_hours) == (2, 1, 1)
    assert (visitors.stays, visitors.non_payers, visitors.outside_fee_hours) == (4, 2, 2)
    messages = [message for message in caplog.messages if "non-payers" in message]
    assert any("commuter universe" in message and "non-payers (priced 0 ct) 1/2 (50.0 %)" in message and "payer-conditional" in message
               for message in messages)
    assert any("destination universe" in message and "non-payers (priced 0 ct) 2/4 (50.0 %)" in message and "payment-inclusive" in message
               for message in messages)
    table = tmp_path / "payer_table.csv"
    cal.run(out_path=table, generated_on="2026-10-14", **inputs)
    rows = cal.read_calibration_table(table)
    assert rows["commuter_universe_stays_non_payer"] == 1 and rows["universe_stays_non_payer"] == 2
    assert rows["commuter_universe_payer_stays"] == 1 and rows["check_wolfsburg_payer_stays"] == 2
    for needle in ("payer-conditional", "non-payers", "leave numerator and denominator", "the SrV street category contains free "
                   "parkers"):
        assert needle in result["text"], needle


def test_the_wolfsburg_check_is_payer_conditional_too(cal, inputs, tmp_path):
    # the Wolfsburg universe of the fixture: p11 work and p12 education pay (window 06:00-24:00). A window that ends before them
    # (06:00-07:00) makes both non-payers: the check has no payer and is refused instead of reading 0 or 1
    _set_tariffs(inputs, wob={"fee_start_h": "6.0", "fee_end_h": "7.0"})
    with pytest.raises(ValueError, match="no payer"):
        cal.run(out_path=None, **inputs)
    _set_tariffs(inputs)
    result = cal.run(out_path=None, **inputs)
    assert result["wolfsburg_outcomes"].non_payers == 0 and result["wolfsburg_payers"] == result["wolfsburg_activities"] == 2


def test_the_visitor_sensitivity_over_in_window_stays_only_is_a_header_line_and_no_release_value(cal, inputs, tmp_path):
    # in the payer scenario the two in-window visitors p01 and p02 alone must reach 0.1: P = 0.1 -> w = 1/9 -> lambda = 300 /
    # ln 9 = 136.5 m against the release lambda = 300 / ln 4 = 216.4 m
    scenario = _payer_scenario(inputs)
    out = tmp_path / "table.csv"
    result = cal.run(out_path=out, generated_on="2026-10-14", sensitivity_visitors_in_window=True, **scenario)
    entry = result["visitor_window_sensitivity"]
    assert entry["label"] == "visitors_in_window"
    assert entry["decay_length_m"] == pytest.approx(300.0 / math.log(9.0), abs=1.5)
    assert _closed_form(300.0, entry["decay_length_m"]) == pytest.approx(0.1, abs=0.0006)
    assert result["decay_length_m"] == pytest.approx(300.0 / math.log(4.0), abs=2.0)   # the release value did not move
    assert entry["payer_stays"] == 2 and entry["stays"] == 4
    text = out.read_text(encoding="ascii")
    line = next(line for line in text.splitlines() if line.startswith("# Sensitivity of lambda over in-window stays only"))
    assert "NOT a release value" in line and "visitors_in_window" in line and f"lambda = {entry['decay_length_m']:.2f} m" in line
    assert "2 of 4 stays" in line
    # without the flag there is no such line and no entry
    plain = cal.run(out_path=None, **scenario)
    assert plain["visitor_window_sensitivity"] is None


def test_the_commuter_target_uncertainty_table_reports_lambda_c_at_shifted_targets_and_where_it_is_unreachable(
        cal, inputs, tmp_path):
    # target 0.4 with n = 100 paid respondents: SE = sqrt(0.4 * 0.6 / 100) = 0.04899 (a lower bound: the design effect is ignored).
    # Both commuters pay, beta 0: the mean is (P(300) + P(500)) / 2 at every lambda_c, the shifted targets are met by closed form.
    out = tmp_path / "table.csv"
    result = cal.run(out_path=out, generated_on="2026-10-14", commuter_target_respondents=100,
                     sensitivity_commuter_target_se=[-1.0, 0.5, 1.0], **inputs)
    entries = result["commuter_target_sensitivities"]
    assert [entry["label"] for entry in entries] == ["commuter_target_minus_1_se", "commuter_target_plus_0.5_se",
                                                     "commuter_target_plus_1_se"]
    se = math.sqrt(0.4 * 0.6 / 100)
    assert entries[0]["standard_error"] == pytest.approx(se, abs=1e-6)
    for entry, multiple in zip(entries, (-1.0, 0.5, 1.0)):
        assert entry["target"] == pytest.approx(0.4 + multiple * se, abs=1e-6) and not entry["unreachable"]
        mean = (_closed_form(300.0, entry["decay_commute_length_m"]) + _closed_form(500.0, entry["decay_commute_length_m"])) / 2
        assert mean == pytest.approx(entry["target"], abs=0.0006)
    lambdas = [entry["decay_commute_length_m"] for entry in entries]
    assert lambdas == sorted(lambdas) and len(set(lambdas)) == 3 and lambdas[0] < result["decay_commute_length_m"] < lambdas[1]
    text = out.read_text(encoding="ascii")
    lines = [line for line in text.splitlines() if line.startswith("# Sensitivity of the lambda_c target")]
    assert len(lines) == 3 and "NOT a release value" in lines[0] and "n = 100" in lines[0] and "design effect" in lines[0]
    # a target that the options cannot reach is reported as unreachable with the floor, never as a value
    wide = cal.run(out_path=None, commuter_target_respondents=2, sensitivity_commuter_target_se=[1.0], **inputs)
    [unreachable] = wide["commuter_target_sensitivities"]
    assert unreachable["unreachable"] and unreachable["decay_commute_length_m"] is None
    assert unreachable["low_end_mean"] < 0.01 and unreachable["high_end_mean"] < 0.5
    # the shifts need the respondents, and the respondents must be a positive whole number
    with pytest.raises(ValueError, match="commuter_target_respondents"):
        cal.run(out_path=None, sensitivity_commuter_target_se=[1.0], **inputs)
    for bad in (0, -3, 2.5, True):
        with pytest.raises(ValueError, match="commuter_target_respondents"):
            cal.run(out_path=None, commuter_target_respondents=bad, sensitivity_commuter_target_se=[1.0], **inputs)
    for bad in (0.0, float("nan"), "one"):
        with pytest.raises(ValueError, match="sensitivity_commuter_target_se"):
            cal.run(out_path=None, commuter_target_respondents=100, sensitivity_commuter_target_se=[bad], **inputs)


def test_the_header_lists_every_sensitivity_names_the_zone_split_of_the_commuters_and_the_code_state_rule(cal, inputs, tmp_path):
    free_lot, paid_lot = _dataset_files(inputs, tmp_path)
    result = cal.run(out_path=None, generated_on="2026-10-14", sensitivity_garages={"lot_threshold_30": free_lot},
                     sensitivity_betas=[0.5], sensitivity_visitors_in_window=True, commuter_target_respondents=100,
                     sensitivity_commuter_target_se=[-1.0], **inputs)
    header = [line for line in result["text"].splitlines() if line.startswith("#")]
    summary = next(line for line in header if line.startswith("# Sensitivities in this table"))
    for label in ("lot_threshold_30", "beta_0.5", "visitors_in_window", "commuter_target_minus_1_se"):
        assert label in summary, label
    assert "none is a release value" in summary
    labelled = [line for line in header if "NOT a release value" in line]
    assert len(labelled) == 4          # every listed sensitivity has its own line
    note = next(line for line in header if "code state" in line and "names the commit of the producing code" in line)
    assert "changes only that line" in note
    zones = next(line for line in header if line.startswith("# Commuter universe:"))
    assert "By zone: bs_zone_ia 1 (50.0 %), bs_zone_ib 1 (50.0 %)" in zones
    # without any optional sensitivity the summary says so
    plain = cal.run(out_path=None, **inputs)["text"]
    assert "# Sensitivities in this table (none is a release value): none." in plain
    assert result["commuter_zone_counts"] == {"bs_zone_ia": 1, "bs_zone_ib": 1}


def test_the_command_line_takes_the_new_sensitivity_options(cal, monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cal, "run", lambda **options: captured.update(options))
    base = ["--plans", str(tmp_path / "plans.xml"), "--data-path", str(tmp_path / "data")]
    cal.main(base)
    assert captured["sensitivity_visitors_in_window"] is False and captured["commuter_target_respondents"] is None
    assert captured["sensitivity_commuter_target_se"] is None
    cal.main(base + ["--sensitivity-visitors-in-window", "--commuter-target-respondents", "42",
                     "--sensitivity-commuter-target-se", "-1", "--sensitivity-commuter-target-se", "0.5"])
    assert captured["sensitivity_visitors_in_window"] is True and captured["commuter_target_respondents"] == 42
    assert captured["sensitivity_commuter_target_se"] == [-1.0, 0.5]
    with pytest.raises(SystemExit) as refused:
        cal.main(base + ["--sensitivity-commuter-target-se", "1"])      # the respondents are missing
    assert refused.value.code == 2
    with pytest.raises(SystemExit) as refused:
        cal.main(base + ["--commuter-target-respondents", "0", "--sensitivity-commuter-target-se", "1"])
    assert refused.value.code == 2
