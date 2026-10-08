"""Distance-weighted garage options priced as an expected cost (parking cost zones v2, spec Amendment E, Task 4d).

Covered: the expectation over the street and the garages (E2, E3), E4 (a free street pays 0; an unavailable street
renormalises the weights over the garages), the early rules and campus zones that the garages never touch, the garages
beyond the maximum distance, the monthly product at a garage for work and education only (P2), half-up rounding once at the
end, the three garage tariff structures (time-of-day tiers with the clock window of the first period, duration bands with
the grace period, the cap over a schedule, the closed schedule repeated per started 24 h) and the validation of a garage.
Every expected number is derived by hand in the comment above it; nothing is read back from the code under test.
"""
from __future__ import annotations

import math

import pytest

from braunschweig.parking import cost
from braunschweig.parking.cost import GarageOptionCounters, GarageTariff, ZoneTariff
from braunschweig.parking.tariff_export import garage_bands_from_text, garage_tiers_from_text
from tests.restricted_parking_data import committed_parking_path

HOUR_S = 3600
DECAY_M = 400.0


def _street(**overrides) -> ZoneTariff:
    """A street zone: 180 ct/h in 1-min units, cap 900 ct, fee window 09:00-20:00 (the fixture zone fx_bs_ib)."""
    fields = dict(zone_id="test_street", zone_type="street_paid", hourly_rate_cents=180, billing_unit_min=1,
                  free_if_stay_at_most_min=None, first_period_min=None, first_period_cents=None, daily_cap_cents=900,
                  max_stay_min=None, long_stay_product_cents=None, member_day_cents=None, guest_day_cents=None,
                  fee_start_s=32400, fee_end_s=72000, resident_exempt=False)
    fields.update(overrides)
    return ZoneTariff(**fields)


def _garage(garage_id="g1", **overrides) -> GarageTariff:
    """A single-window garage: 200 ct per started hour, all day, no cap (monthly product 6300 ct = 300 ct per day)."""
    fields = dict(garage_id=garage_id, x_m=0.0, y_m=0.0, hourly_rate_cents=200, billing_unit_min=60, fee_start_s=0,
                  fee_end_s=86400, monthly_cents=6300)
    fields.update(overrides)
    return GarageTariff(**fields)


def _price(tariff, arrival_s, departure_s, options, *, purpose="shop", decay_m=DECAY_M, **flags):
    flags = {"parking_free": False, "resident_of_zone": False, **flags}
    return cost.parking_cost_with_garages(tariff, arrival_s, departure_s, purpose=purpose, garage_options=options,
                                          decay_m=decay_m, **flags)


def test_the_outcome_is_appended_at_the_end_so_the_declaration_order_stays():
    assert cost.OUTCOMES[-1] == "PAID_EXPECTED" == cost.PAID_EXPECTED
    assert cost.OUTCOMES[:12] == ("HOME", "EMPLOYER_FREE", "RESIDENT_FREE", "OUTSIDE_FEE_HOURS", "FREE_WITHIN_LIMIT",
                                  "PAID_METERED", "PAID_LONG_STAY", "PAID_CAMPUS_MEMBER", "PAID_CAMPUS_GUEST", "NO_ZONE",
                                  "PAID_GARAGE", "PAID_COMMUTER")
    assert len(cost.OUTCOMES) == 13


# ----------------------------------------------------------------------------------------------- the expectation


def test_without_a_garage_in_range_or_with_the_decay_off_the_stay_prices_exactly_as_before():
    street = _street()
    base = cost.parking_cost_cents(street, 36000, 39600, purpose="shop", parking_free=False, resident_of_zone=False)
    assert base == (180, cost.PAID_METERED)
    assert _price(street, 36000, 39600, []) == (180, cost.PAID_METERED, 0.0)
    assert _price(street, 36000, 39600, [(_garage(), 0.0)], decay_m=0.0) == (180, cost.PAID_METERED, 0.0)


def test_one_garage_prices_the_probability_weighted_mean_of_the_street_and_the_garage_option():
    # Stay 10:00-11:00 = 60 chargeable min: street 60 x 3 ct = 180 ct; garage one started hour = 200 ct.
    # d = 300 m, lambda = 400 m: w = exp(-0.75) = 0.4723665527; P_garage = w / (1 + w) = 0.3208213008;
    # expected = (180 + 0.4723665527 x 200) / 1.4723665527 = 186.416426 -> 186 ct.
    cents, outcome, probability = _price(_street(), 36000, 39600, [(_garage(), 300.0)])
    assert (cents, outcome) == (186, cost.PAID_EXPECTED)
    assert probability == pytest.approx(0.3208213008, abs=1e-9)
    detail = cost.parking_cost_with_garages_detail(_street(), 36000, 39600, purpose="shop", parking_free=False,
                                                   resident_of_zone=False, garage_options=[(_garage(), 300.0)],
                                                   decay_m=DECAY_M)
    assert detail.expected_cents == pytest.approx(186.416426, abs=1e-6)


def test_two_garages_at_different_distances_are_ordered_by_id_and_weighted_by_their_own_distance():
    # g_a: 300 m (w 0.4723665527), 200 ct; g_b: 700 m (w = exp(-1.75) = 0.1737739435), 100 ct per started hour.
    # expected = (180 + 0.4723665527 x 200 + 0.1737739435 x 100) / 1.6461404962 = 177.293922 -> 177 ct;
    # P_garages = (0.4723665527 + 0.1737739435) / 1.6461404962 = 0.3925184379.
    first = _garage("g_a")
    second = _garage("g_b", hourly_rate_cents=100)
    expected = (177, cost.PAID_EXPECTED)
    for options in ([(first, 300.0), (second, 700.0)], [(second, 700.0), (first, 300.0)]):
        cents, outcome, probability = _price(_street(), 36000, 39600, options)
        assert (cents, outcome) == expected
        assert probability == pytest.approx(0.3925184379, abs=1e-9)


def test_a_street_that_costs_nothing_pays_nothing_whatever_garages_are_near_e4():
    # A free threshold of 30 min: 25 chargeable min cost 0 ct (FREE_WITHIN_LIMIT); nobody pays a garage then.
    street = _street(free_if_stay_at_most_min=30)
    assert _price(street, 39600, 41100, [(_garage(), 0.0)]) == (0, cost.FREE_WITHIN_LIMIT, 0.0)
    # 31 min lie above the threshold, so the mixture applies again: street 93 ct, garage 200 ct -> (93 + 200) / 2 = 146.5.
    cents, outcome, _ = _price(street, 39600, 39600 + 31 * 60, [(_garage(), 0.0)])
    assert (cents, outcome) == (147, cost.PAID_EXPECTED)


def test_an_unavailable_street_renormalises_the_weights_over_the_garages_e4():
    # Maximum stay 180 min without a long-stay product: the zone garage family (120 ct/h, the only way a schema-2 zone can
    # hold such a maximum stay) is superseded by the garage options (E8). 240 chargeable min: the street is unavailable;
    # g_a 4 started hours x 200 = 800 ct (w 0.4723665527), g_b 4 x 100 = 400 ct (w 0.1737739435), the street has no weight:
    # expected = (0.4723665527 x 800 + 0.1737739435 x 400) / (0.4723665527 + 0.1737739435) = 447.4028 / 0.6461405 = 692.4.
    zone = _street(max_stay_min=180, daily_cap_cents=None, garage_hourly_rate_cents=120, garage_billing_unit_min=60,
                   garage_fee_start_s=0, garage_fee_end_s=86400)
    arrival_s, departure_s = 32400, 32400 + 4 * HOUR_S
    assert cost.parking_cost_cents(zone, arrival_s, departure_s, purpose="shop", parking_free=False,
                                   resident_of_zone=False) == (480, cost.PAID_GARAGE)
    first, second = _garage("g_a"), _garage("g_b", hourly_rate_cents=100)
    weight_a, weight_b = math.exp(-300 / DECAY_M), math.exp(-700 / DECAY_M)
    expected = (weight_a * 800 + weight_b * 400) / (weight_a + weight_b)
    assert expected == pytest.approx(692.4, abs=0.1)
    cents, outcome, probability = _price(zone, arrival_s, departure_s, [(first, 300.0), (second, 700.0)])
    assert (cents, outcome) == (692, cost.PAID_EXPECTED)
    assert probability == pytest.approx(1.0, abs=1e-12)
    # one garage alone: probability 1, the price is exactly the option cost
    assert _price(zone, arrival_s, departure_s, [(first, 900.0)]) == (800, cost.PAID_EXPECTED, 1.0)


def test_the_early_rules_and_a_campus_precede_the_garages():
    street = _street()
    options = [(_garage(), 0.0)]
    assert _price(street, 36000, 39600, options, parking_free=True) == (0, cost.EMPLOYER_FREE, 0.0)
    assert _price(street, 36000, 39600, options, purpose="home") == (0, cost.HOME, 0.0)
    assert _price(street, 36000, 39600, options, resident_of_district=True) == (0, cost.RESIDENT_FREE, 0.0)
    assert _price(street, 73800, 79200, options) == (0, cost.OUTSIDE_FEE_HOURS, 0.0)
    assert _price(None, 36000, 39600, options) == (0, cost.NO_ZONE, 0.0)
    campus = ZoneTariff(zone_id="test_campus", zone_type="campus", hourly_rate_cents=None, billing_unit_min=None,
                        free_if_stay_at_most_min=None, first_period_min=None, first_period_cents=None,
                        daily_cap_cents=None, max_stay_min=None, long_stay_product_cents=None, member_day_cents=350,
                        guest_day_cents=900, fee_start_s=0, fee_end_s=86400, resident_exempt=False)
    assert _price(campus, 36000, 39600, options, purpose="work") == (350, cost.PAID_CAMPUS_MEMBER, 0.0)
    assert _price(campus, 36000, 39600, options) == (900, cost.PAID_CAMPUS_GUEST, 0.0)


def _resident_zone(**overrides) -> ZoneTariff:
    """A resident zone (disc parking, rate 0 per 60 min, all day) with a maximum stay of 120 min and NO long-stay product:
    it is only constructible with the zone-level garage family (120 ct per started hour, cap 960 ct), which the garage
    options supersede (E8)."""
    fields = dict(zone_id="test_resident", zone_type="resident_zone", hourly_rate_cents=0, billing_unit_min=60,
                  free_if_stay_at_most_min=None, first_period_min=None, first_period_cents=None, daily_cap_cents=None,
                  max_stay_min=120, long_stay_product_cents=None, member_day_cents=None, guest_day_cents=None,
                  fee_start_s=0, fee_end_s=86400, resident_exempt=True, garage_hourly_rate_cents=120,
                  garage_billing_unit_min=60, garage_daily_cap_cents=960, garage_fee_start_s=0, garage_fee_end_s=86400)
    fields.update(overrides)
    return ZoneTariff(**fields)


def test_garage_options_act_on_a_resident_zone_too_r_4d_2():
    zone = _resident_zone()
    options = [(_garage(), 300.0)]
    # 90 min <= the maximum stay 120: disc parking at rate 0 is free, so the street option costs 0 and the stay pays 0 (E4)
    # although a garage lies 300 m away.
    assert _price(zone, 36000, 41400, options) == (0, cost.FREE_WITHIN_LIMIT, 0.0)
    # 180 min > 120 without a long-stay product: the street is unavailable (the zone garage family 3 x 120 = 360 ct is
    # superseded); the weights renormalise over the garage: 3 started hours x 200 ct = 600 ct, probability 1.
    assert _price(zone, 36000, 46800, options) == (600, cost.PAID_EXPECTED, 1.0)
    # a resident of the zone is exempt before any garage acts (R1)
    assert _price(zone, 36000, 46800, options, resident_of_zone=True) == (0, cost.RESIDENT_FREE, 0.0)


def test_the_zone_commuter_product_stays_a_street_option_product():
    # Zone commuter product 150 ct: a work stay's street option is min(street 900 (cap), commuter 150) = 150 ct.
    street = _street(commuter_day_cents=150)
    # 8 h stay: street min(1440, 900) = 900; garage 8 started hours x 200 = 1600, monthly 300 per day -> option 300.
    # d = 0: P = 0.5 each: expected = (150 + 300) / 2 = 225 ct.
    assert _price(street, 28800, 28800 + 8 * HOUR_S, [(_garage(), 0.0)], purpose="work")[:2] == (225, cost.PAID_EXPECTED)


def test_the_monthly_product_per_working_day_serves_work_and_education_only_p2():
    street = _street()
    eight_hours = (28800, 28800 + 8 * HOUR_S)
    # street 8 h x 180 ct/h = 1440 ct, capped at 900. Garage 8 x 200 = 1600 ct; monthly 6300 / 21 = 300 ct.
    # d = 0 (P = 0.5): work (900 + 300) / 2 = 600; education the same; shop (900 + 1600) / 2 = 1250.
    for purpose, expected in (("work", 600), ("education", 600), ("shop", 1250), ("leisure", 1250)):
        assert _price(street, *eight_hours, [(_garage(), 0.0)], purpose=purpose)[:2] == (expected, cost.PAID_EXPECTED)


@pytest.mark.parametrize("monthly_cents, per_day_cents", [(6300, 300), (6400, 305), (6310, 300), (6320, 301), (2100, 100),
                                                          (1, 0), (11, 1), (10, 0)])
def test_the_monthly_product_per_day_is_rounded_half_up_to_the_cent(monthly_cents, per_day_cents):
    # 6400 / 21 = 304.76 -> 305; 6310 / 21 = 300.48 -> 300; 6320 / 21 = 300.95 -> 301; 11 / 21 = 0.52 -> 1; 10 / 21 = 0.48 -> 0
    assert cost.garage_monthly_day_cents(monthly_cents) == per_day_cents


def test_a_garage_option_that_costs_nothing_is_a_valid_option_with_its_weight():
    # Garage fee window 10:00-12:00 only; the stay 14:00-15:00 has no chargeable second there: the option costs 0 ct, the
    # street costs 180 ct. d = 0: expected = (180 + 0) / 2 = 90 ct, probability 0.5 (the weight stays).
    window = _garage(fee_start_s=36000, fee_end_s=43200)
    assert _price(_street(), 50400, 54000, [(window, 0.0)]) == (90, cost.PAID_EXPECTED, 0.5)


def test_the_expectation_is_rounded_half_up_once_at_the_end():
    # 31 min: street 31 x 3 = 93 ct, garage 200 ct, d = 0: (93 + 200) / 2 = 146.5 exactly -> 147 (banker's rounding: 146).
    detail = cost.parking_cost_with_garages_detail(_street(), 36000, 36000 + 31 * 60, purpose="shop", parking_free=False,
                                                   resident_of_zone=False, garage_options=[(_garage(), 0.0)],
                                                   decay_m=DECAY_M)
    assert detail.expected_cents == 146.5 and detail.cents == 147


def test_the_garage_options_of_a_destination_are_the_garages_within_the_maximum_distance_ordered_by_id():
    garages = [_garage("g_c", x_m=600.0, y_m=800.0), _garage("g_a", x_m=300.0, y_m=0.0),
               _garage("g_b", x_m=1001.0, y_m=0.0)]
    options = cost.garage_options_in_range(garages, 0.0, 0.0, 1000.0)
    # g_c lies at exactly 1000 m (a 3-4-5 triangle: sqrt is exact) and is IN; g_b at 1001 m is out.
    assert [(garage.garage_id, distance_m) for garage, distance_m in options] == [("g_a", 300.0), ("g_c", 1000.0)]
    assert cost.garage_options_in_range(garages, 0.0, 0.0) == options  # the default maximum distance is 1000 m
    with pytest.raises(ValueError, match="duplicate garage_id"):
        cost.garage_options_in_range([_garage("g_a"), _garage("g_a")], 0.0, 0.0)
    with pytest.raises(ValueError, match="max_distance_m must be positive"):
        cost.garage_options_in_range(garages, 0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="finite"):
        cost.garage_options_in_range(garages, float("nan"), 0.0)


@pytest.mark.parametrize("decay_m, message", [(-1.0, "decay_m"), (float("nan"), "decay_m"), (float("inf"), "decay_m"),
                                              (True, "decay_m")])
def test_an_invalid_decay_is_refused(decay_m, message):
    with pytest.raises(ValueError, match=message):
        _price(_street(), 36000, 39600, [(_garage(), 0.0)], decay_m=decay_m)


def test_invalid_garage_options_are_refused():
    with pytest.raises(ValueError, match="distance_m"):
        _price(_street(), 36000, 39600, [(_garage(), -1.0)])
    with pytest.raises(ValueError, match="distance_m"):
        _price(_street(), 36000, 39600, [(_garage(), float("nan"))])
    with pytest.raises(TypeError, match="GarageTariff"):
        _price(_street(), 36000, 39600, [("g1", 10.0)])
    with pytest.raises(ValueError, match="duplicate garage_id"):
        _price(_street(), 36000, 39600, [(_garage(), 10.0), (_garage(), 20.0)])


def test_the_counters_say_how_many_stays_had_garages_in_range_and_how_they_were_priced():
    counters = GarageOptionCounters()
    zone = _street(free_if_stay_at_most_min=30)
    one = [(_garage(), 0.0)]
    _price(zone, 39600, 41100, one, counters=counters)                      # street free (E4)
    _price(zone, 36000, 39600, one, counters=counters)                      # expectation
    _price(zone, 36000, 39600, [], counters=counters)                       # eligible, no garage in range
    _price(zone, 36000, 39600, one, parking_free=True, counters=counters)   # early rule
    assert (counters.stays, counters.eligible, counters.with_garages_in_range) == (4, 3, 2)
    assert (counters.street_free, counters.expected, counters.street_unavailable) == (1, 1, 0)
    assert counters.option_evaluations == 1
    assert "garages in range 2/3 (66.7 %)" in counters.summary()


# ----------------------------------------------------------------------------------------- the tariff structures

ROSENWALL = "08:00-10:00 0.30/30; 10:00-18:00 0.60/30; 18:00-23:00 0.30/30; 23:00-08:00 0.10/30"
RATHAUS = "00:00-06:00 0.50/60; 06:00-24:00 1.20/60"
OUTLETS = "0-20 free; 20-120 total 1.00; 120-240 0.50/60; 240-420 1.50/60; 420- 5.00/60"
PEINE = "0-30 total 0.20; 30-60 total 0.80; 60-300 0.40/30; 300-1440 total 4.00"
GRACE = "0-15 free; 15-60 total 1.50; 60- 1.50/60"


def _tiered(text=ROSENWALL, **overrides) -> GarageTariff:
    fields = dict(garage_id="g_tiers", x_m=0.0, y_m=0.0, tiers=garage_tiers_from_text(text))
    fields.update(overrides)
    return GarageTariff(**fields)


def _banded(text, **overrides) -> GarageTariff:
    fields = dict(garage_id="g_bands", x_m=0.0, y_m=0.0, fee_start_s=0, fee_end_s=86400, bands=garage_bands_from_text(text))
    fields.update(overrides)
    return GarageTariff(**fields)


def _hms(hours: int, minutes: int = 0) -> int:
    return hours * 3600 + minutes * 60


@pytest.mark.parametrize("arrival_s, departure_s, expected_cents", [
    # 09:30-10:45 = 75 min, units of 30 min start 09:30 (08-10 tier 30 ct), 10:00 (60 ct), 10:30 (60 ct) = 150 ct
    (_hms(9, 30), _hms(10, 45), 150),
    # 17:30-18:30: units start 17:30 (60 ct) and 18:00 (30 ct, a unit that starts exactly at 18:00 takes the 18-23 tier)
    (_hms(17, 30), _hms(18, 30), 90),
    # 17:30-18:00: the unit that would start at 18:00 is not started (departure at the boundary): one unit, 60 ct
    (_hms(17, 30), _hms(18), 60),
    # 22:00-07:00 night stay (9 h): units 22:00, 22:30 at 30 ct (18-23 tier) = 60 ct, then 23:00 to 06:30 = 16 units
    # in the tier 23-08 crossing midnight at 10 ct = 160 ct: 220 ct
    (_hms(22), 86400 + _hms(7), 220),
    # 12:00-12:01: one started unit, 60 ct
    (_hms(12), _hms(12) + 60, 60),
    # no stay, no price
    (_hms(12), _hms(12), 0),
])
def test_a_tiered_garage_prices_each_started_unit_at_the_tier_in_force_at_its_start_p6(arrival_s, departure_s, expected_cents):
    assert cost.garage_metered_cents(_tiered(), arrival_s, departure_s) == expected_cents


def test_a_time_of_day_outside_every_tier_is_free_and_the_day_cap_limits_the_stay_once():
    morning_only = _tiered("08:00-10:00 0.30/30")
    assert cost.garage_metered_cents(morning_only, _hms(12), _hms(13)) == 0
    # 08:00-10:00: 4 units x 30 ct = 120 ct; a cap of 100 ct limits it
    assert cost.garage_metered_cents(morning_only, _hms(8), _hms(10)) == 120
    assert cost.garage_metered_cents(_tiered("08:00-10:00 0.30/30", daily_cap_cents=100), _hms(8), _hms(10)) == 100


@pytest.mark.parametrize("arrival_s, departure_s, expected_cents", [
    # arrival 10:00 inside the window 06:00-24:00: first period 110 ct covers 10:00-11:00; units start 11:00 and 12:00
    # at 120 ct each: 110 + 240 = 350 ct
    (_hms(10), _hms(13), 350),
    # arrival 05:30 outside the window: no first period; units start 05:30 (00-06 tier 50 ct), 06:30 and 07:30 (120 ct)
    (_hms(5, 30), _hms(8, 30), 290),
    # the window start is inclusive: arrival 06:00, 30 min stay: the first period only (the next unit would start 07:00)
    (_hms(6), _hms(6, 30), 110),
    # the window end is exclusive: arrival 24:00 = 00:00 of the next day lies outside: units 00:00, 01:00 at 50 ct
    (86400, 86400 + _hms(2), 100),
    # arrival 23:30 inside the window; the first period ends at 00:30; units start 00:30 and 01:30 at 50 ct (departure 02:00)
    (_hms(23, 30), 86400 + _hms(2), 210),
    # the day cap of 600 ct limits a long stay: 06:00-20:00 = 110 + 13 x 120 = 1670 -> 600
    (_hms(6), _hms(20), 600),
])
def test_a_first_period_tied_to_a_clock_window_is_charged_only_for_an_arrival_inside_it_p6_amended(arrival_s, departure_s,
                                                                                                    expected_cents):
    garage = _tiered(RATHAUS, first_period_min=60, first_period_cents=110, first_period_start_s=_hms(6),
                     first_period_end_s=86400, daily_cap_cents=600)
    assert cost.garage_metered_cents(garage, arrival_s, departure_s) == expected_cents


def test_a_first_period_without_a_window_is_charged_for_every_arrival_and_the_single_window_form_honours_a_window():
    always = _tiered(RATHAUS, first_period_min=60, first_period_cents=110)
    # arrival 05:30 with a first period of 60 min from 05:30: 110 ct, units from 06:30: 06:30 and 07:30 at 120 ct = 350 ct
    assert cost.garage_metered_cents(always, _hms(5, 30), _hms(8, 30)) == 350
    # single-window garage: first period 60 min 100 ct tied to 06:00-10:00, then 200 ct/h in 60 min units, all day.
    core = _garage(first_period_min=60, first_period_cents=100, first_period_start_s=_hms(6), first_period_end_s=_hms(10))
    assert cost.garage_metered_cents(core, _hms(9), _hms(12)) == 100 + 2 * 200   # inside: 100 + (180-60 = 120 min -> 2 h)
    assert cost.garage_metered_cents(core, _hms(11), _hms(14)) == 3 * 200        # outside: no first period, 3 started hours


@pytest.mark.parametrize("duration_min, expected_cents", [
    # Designer Outlets: 0-20 free, 20-120 total 1.00, 120-240 0.50/60, 240-420 1.50/60, 420- 5.00/60, cap 15.00
    (20, 0), (21, 100), (120, 100), (121, 150), (180, 150), (181, 200), (240, 200), (241, 350), (420, 650), (421, 1150),
    (481, 1500),
])
def test_a_banded_garage_prices_the_band_edges_the_jump_to_a_total_and_the_cap_over_the_schedule_p8(duration_min,
                                                                                                    expected_cents):
    # 421 min: 200 ct at 240 + 3 x 150 = 650 ct at 420, then one started hour at 500 ct = 1150; 481 min: 1650 -> cap 1500.
    garage = _banded(OUTLETS, daily_cap_cents=1500)
    assert cost.garage_metered_cents(garage, _hms(10), _hms(10) + duration_min * 60) == expected_cents
    # one second past the edge is the next band
    if duration_min in (20, 120, 240, 420):
        assert cost.garage_metered_cents(garage, _hms(10), _hms(10) + duration_min * 60 + 1) > expected_cents


@pytest.mark.parametrize("duration_min, expected_cents", [(15, 0), (16, 150), (60, 150), (61, 300)])
def test_a_grace_period_garage_prices_the_free_minutes_as_zero_and_bills_the_longer_stay_from_the_arrival_p10(duration_min,
                                                                                                             expected_cents):
    # "0-15 free; 15-60 total 1.50; 60- 1.50/60": 15 min cost 0, 16 min the first unit's total 1.50, 61 min 1.50 + 1.50.
    assert cost.garage_metered_cents(_banded(GRACE), _hms(10), _hms(10) + duration_min * 60) == expected_cents


def test_a_closed_schedule_is_repeated_per_started_24_hours_and_counted_p9():
    garage = _banded(PEINE)
    counters = GarageOptionCounters()
    # a stay of exactly 1440 min is inside the schedule: 400 ct
    assert cost.garage_metered_cents(garage, 0, 1440 * 60, counters) == 400
    assert counters.closed_schedule_repeats == 0
    # 1441 min: one full period 400 ct + the remainder of 1 min (0.20) = 420 ct
    assert cost.garage_metered_cents(garage, 0, 1441 * 60, counters) == 420
    # 2881 min: two full periods 800 ct + 1 min 20 ct = 820 ct
    assert cost.garage_metered_cents(garage, 0, 2881 * 60, counters) == 820
    # exactly two periods (2880 min): k = (172800 - 1) // 86400 = 1 full period + the remainder 1440 min = 400 + 400
    assert cost.garage_metered_cents(garage, 0, 2880 * 60, counters) == 800
    assert counters.closed_schedule_repeats == 3
    # every period is capped: a 300 ct day cap gives 300 + 20 for 1441 min
    assert cost.garage_metered_cents(_banded(PEINE, daily_cap_cents=300), 0, 1441 * 60) == 320


def test_a_closed_schedule_that_does_not_end_at_24_hours_states_no_rule_for_a_longer_stay():
    with pytest.raises(ValueError, match="P9"):
        cost.garage_metered_cents(_banded("0-30 total 0.20; 30-600 total 1.00"), 0, 601 * 60)


def test_a_banded_garage_charges_only_the_minutes_inside_its_fee_window():
    # fee window 10:00-12:00; a stay 09:00-13:00 has 120 chargeable min: "0-20 free; 20-120 total 1.00" -> 100 ct
    garage = _banded(OUTLETS, fee_start_s=_hms(10), fee_end_s=_hms(12))
    assert cost.garage_metered_cents(garage, _hms(9), _hms(13)) == 100
    assert cost.garage_metered_cents(garage, _hms(14), _hms(15)) == 0


def test_the_integer_band_arithmetic_equals_the_reference_evaluation_for_every_second_near_the_edges():
    # The Java port prices a band in integer seconds: band by seconds <= 60 x to_min, started units
    # ceil((seconds - 60 x from_min) / (60 x unit_min)). This is that arithmetic, against the reference evaluation.
    for text, edges in ((OUTLETS, (0, 20, 120, 180, 240, 420, 480)),
                        # a band whose length (80 min) is not a multiple of its unit (30 min): 3 started units at its end
                        ("0-20 free; 20-100 1.00/30; 100-145 2.00/45; 145- 3.00/60", (0, 20, 50, 100, 145, 205))):
        _check_integer_band_arithmetic(_banded(text), edges)


def _check_integer_band_arithmetic(garage, edges) -> None:
    def integer_price(seconds: int) -> int:
        price_at_start = 0
        for band in garage.bands:
            if band.to_min is None or seconds <= band.to_min * 60:
                if band.kind == "free":
                    return 0
                if band.kind == "total":
                    return band.price_cents
                return price_at_start + band.price_cents * -(-(seconds - band.from_min * 60) // (band.unit_min * 60))
            if band.kind == "total":
                price_at_start = band.price_cents
            elif band.kind == "increment":
                # started units, also at the band end: ceil((to - from) / unit), the reference's rule
                price_at_start += band.price_cents * -(-(band.to_min - band.from_min) // band.unit_min)
        raise AssertionError

    for edge_min in edges:
        for delta_s in range(-130, 131):
            seconds = edge_min * 60 + delta_s
            if seconds > 0:
                assert integer_price(seconds) == cost.garage_metered_cents(garage, 0, seconds), seconds


def test_a_garage_option_takes_the_cheaper_of_the_metered_and_the_monthly_product_for_commuters_only():
    garage = _garage(monthly_cents=6300)
    day = (28800, 28800 + 8 * HOUR_S)   # 8 started hours x 200 = 1600 ct
    assert cost.garage_option_cents(garage, *day, purpose="work") == 300
    assert cost.garage_option_cents(garage, *day, purpose="shop") == 1600
    # a short stay: the metered price 200 ct is below the monthly 300 ct per day
    assert cost.garage_option_cents(garage, 28800, 28800 + HOUR_S, purpose="work") == 200
    assert cost.garage_option_cents(_garage(monthly_cents=None), *day, purpose="work") == 1600


# ------------------------------------------------------------------- the grace period of a tiered garage (Task 4b3)

AUTOSTADT = "06:00-18:00 1.00/60; 18:00-06:00 0.50/60"


def _graced_tiers(text=AUTOSTADT, grace="0-30 free", **overrides) -> GarageTariff:
    """The shape of the Wolfsburg Autostadt P2 row: day and night tier per started 60 min and a grace period of 30 min."""
    return _tiered(text, bands=garage_bands_from_text(grace), **overrides)


@pytest.mark.parametrize("arrival_s, departure_s, expected_cents", [
    # a stay of exactly the grace period is free, one second more is billed from the arrival (the minutes are not deducted):
    # 10:00-10:30 = 0 ct; 10:00-10:30:01 = one started 60 min unit at the day tier = 100 ct
    (_hms(10), _hms(10, 30), 0),
    (_hms(10), _hms(10, 30) + 1, 100),
    (_hms(10), _hms(10, 31), 100),
    # 10:00-11:00 is one unit (100 ct); 10:00-11:01 starts a second unit at 11:00 (day tier): 200 ct
    (_hms(10), _hms(11), 100),
    (_hms(10), _hms(11, 1), 200),
    # the day/night boundary: 17:45-18:15 is a stay of 30 min, free whatever tiers it crosses
    (_hms(17, 45), _hms(18, 15), 0),
    # 17:45-18:16 is billed from the arrival: one unit that starts at 17:45 in the day tier = 100 ct
    (_hms(17, 45), _hms(18, 16), 100),
    # 17:30-19:00 = 90 min: units start 17:30 (day, 100 ct) and 18:30 (night, 50 ct) = 150 ct
    (_hms(17, 30), _hms(19), 150),
    # a unit that starts exactly at 18:00 takes the night tier: 17:00-19:01 = units 17:00 (100), 18:00 (50), 19:00 (50)
    (_hms(17), _hms(19, 1), 200),
    # a night stay 22:00-06:00 = 8 units of 50 ct = 400 ct; the unit that would start at 06:00 is not started
    (_hms(22), 86400 + _hms(6), 400),
    # no stay, no price
    (_hms(10), _hms(10), 0),
])
def test_a_tiered_garage_with_a_grace_period_is_free_up_to_it_and_billed_from_the_arrival_beyond_it_p10(arrival_s,
                                                                                                          departure_s,
                                                                                                          expected_cents):
    assert cost.garage_metered_cents(_graced_tiers(), arrival_s, departure_s) == expected_cents


def test_the_grace_period_of_a_tiered_garage_comes_before_the_day_cap_and_never_adds_a_price():
    capped = _graced_tiers(daily_cap_cents=600)
    # 10:00-18:00 = 8 units x 100 ct = 800 ct, capped once at 600 ct; the grace period does not change the cap
    assert cost.garage_metered_cents(capped, _hms(10), _hms(18)) == 600
    assert cost.garage_metered_cents(capped, _hms(10), _hms(10, 29)) == 0
    # without the grace band the same garage charges the first unit of a 29 min stay: the band is what makes it free
    assert cost.garage_metered_cents(_tiered(AUTOSTADT, daily_cap_cents=600), _hms(10), _hms(10, 29)) == 100


def test_a_tiered_garage_with_a_grace_period_is_an_option_like_any_other_and_round_trips_through_its_json():
    garage = _graced_tiers(daily_cap_cents=600)
    assert garage.form == "tiers"
    entry = garage.to_json()
    assert list(entry) == list(cost.GARAGE_FIELDS_JSON) and entry["bands"] == [
        {"from_min": 0, "to_min": 30, "kind": "free", "price_cents": 0, "unit_min": None}]
    assert GarageTariff.from_json(entry) == garage
    # a work stay of 20 min at 10:00 next to the street: street 20 x 3 = 60 ct, the garage option is free (grace period),
    # d = 0: expected = 60 / 2 = 30 ct, probability 0.5
    counters = GarageOptionCounters()
    cents, outcome, probability = _price(_street(), _hms(10), _hms(10, 20), [(garage, 0.0)], counters=counters)
    assert (cents, outcome, probability) == (30, cost.PAID_EXPECTED, 0.5) and counters.option_evaluations == 1


@pytest.mark.parametrize("overrides, message", [
    ({"grace": "0-30 free; 30-60 total 1.00"}, "only as a grace period"),
    ({"grace": "0- free"}, "only as a grace period"),
    ({"grace": "0-30 total 1.00"}, "only as a grace period"),
    ({"fee_start_s": 21600, "fee_end_s": 64800}, "leaves the single-window core empty"),
    ({"first_period_min": 60, "first_period_cents": 100}, "no first period"),
])
def test_a_tiered_garage_accepts_nothing_but_a_closed_free_band_next_to_its_tiers(overrides, message):
    grace = overrides.pop("grace", "0-30 free")
    with pytest.raises(ValueError, match=message):
        _graced_tiers(grace=grace, **overrides)


# ----------------------------------------------------------------------- a car park that is free (Task 4b3, spec E14)


def _free_garage(garage_id="g_free", **overrides) -> GarageTariff:
    """The shape of a free Wolfsburg car park: the free schedule '0- free' with the formal fee window 0 to 24 h."""
    return _banded("0- free", garage_id=garage_id, **overrides)


@pytest.mark.parametrize("duration_s", [0, 1, 1800, 36000, 86400, 3 * 86400])
def test_a_free_car_park_costs_nothing_for_every_stay_even_beyond_a_day(duration_s):
    assert cost.garage_metered_cents(_free_garage(), 36000, 36000 + duration_s) == 0
    assert cost.garage_option_cents(_free_garage(), 36000, 36000 + duration_s, purpose="work") == 0


def test_a_free_option_lowers_the_expected_cost_of_a_paid_street_stay_within_the_maximum_distance():
    # Stay 10:00-11:00: street 60 x 3 = 180 ct. A free garage at d = 300 m, lambda = 400 m: w = exp(-0.75) = 0.4723665527,
    # P_garage = w / (1 + w) = 0.3208213008; expected = 180 / 1.4723665527 = 122.2522 -> 122 ct (below the 180 ct of the
    # street alone).
    street_only = _price(_street(), 36000, 39600, [])
    assert street_only == (180, cost.PAID_METERED, 0.0)
    cents, outcome, probability = _price(_street(), 36000, 39600, [(_free_garage(), 300.0)])
    assert (cents, outcome) == (122, cost.PAID_EXPECTED)
    assert probability == pytest.approx(0.3208213008, abs=1e-9)
    detail = cost.parking_cost_with_garages_detail(_street(), 36000, 39600, purpose="shop", parking_free=False,
                                                   resident_of_zone=False, garage_options=[(_free_garage(), 300.0)],
                                                   decay_m=DECAY_M)
    assert detail.expected_cents == pytest.approx(122.2522, abs=1e-4)
    # nearer is cheaper: d = 100 m: w = exp(-0.25) = 0.7788007831, expected = 180 / 1.7788007831 = 101.1918 -> 101 ct
    assert _price(_street(), 36000, 39600, [(_free_garage(), 100.0)])[0] == 101
    # a free garage next to a paid one (200 ct for the started hour), both at 300 m: the free one adds weight at cost 0 and so
    # pulls the mean down: expected = (180 + 0.4723665527 x 0 + 0.4723665527 x 200) / 1.9447331054 = 141.1368 -> 141 ct,
    # P_garages = 0.9447331054 / 1.9447331054 = 0.4857906223 (the options are ordered by id: g_free, g_paid)
    mixed = _price(_street(), 36000, 39600, [(_garage("g_paid"), 300.0), (_free_garage(), 300.0)])
    assert mixed[:2] == (141, cost.PAID_EXPECTED) and mixed[2] == pytest.approx(0.4857906223, abs=1e-9)


def test_a_free_option_alone_never_makes_a_street_free_stay_cost_anything_e4():
    # a free threshold of 30 min: 25 chargeable min cost 0 ct on the street and nobody takes a detour (E4)
    street = _street(free_if_stay_at_most_min=30)
    assert _price(street, 39600, 41100, [(_free_garage(), 0.0)]) == (0, cost.FREE_WITHIN_LIMIT, 0.0)
    # outside the street fee window (09:00-20:00) the street is free as well
    assert _price(_street(), 25200, 28800, [(_free_garage(), 0.0)]) == (0, cost.OUTSIDE_FEE_HOURS, 0.0)
    # the early rules precede the options: a home stay or an employer-free stay pays 0 without any garage
    assert _price(_street(), 36000, 39600, [(_free_garage(), 0.0)], parking_free=True)[0] == 0


def test_a_free_option_alone_prices_a_stay_whose_street_is_unavailable_at_zero():
    zone = _street(max_stay_min=180, daily_cap_cents=None, garage_hourly_rate_cents=120, garage_billing_unit_min=60,
                   garage_fee_start_s=0, garage_fee_end_s=86400)
    arrival_s, departure_s = 32400, 32400 + 4 * HOUR_S
    assert _price(zone, arrival_s, departure_s, [(_free_garage(), 900.0)]) == (0, cost.PAID_EXPECTED, 1.0)


# --------------------------------------------------------------------------------------------- validation


def test_a_garage_round_trips_through_its_json_entry_with_exactly_the_documented_keys():
    for garage in (_garage(), _tiered(), _tiered(RATHAUS, first_period_min=60, first_period_cents=110,
                                                 first_period_start_s=_hms(6), first_period_end_s=86400,
                                                 daily_cap_cents=600, monthly_cents=4200),
                   _banded(OUTLETS, daily_cap_cents=1500)):
        entry = garage.to_json()
        assert list(entry) == list(cost.GARAGE_FIELDS_JSON)
        assert GarageTariff.from_json(entry) == garage
    with pytest.raises(ValueError, match="keys"):
        GarageTariff.from_json({**_garage().to_json(), "extra": 1})
    with pytest.raises(ValueError, match="tiers item"):
        GarageTariff.from_json({**_tiered().to_json(), "tiers": [{"start_s": 0}]})


@pytest.mark.parametrize("overrides, message", [
    ({"hourly_rate_cents": None}, "exactly one tariff structure"),
    ({"billing_unit_min": 0}, "billing_unit_min must be at least 1"),
    ({"hourly_rate_cents": 1.5}, "hourly_rate_cents must be an integer"),
    ({"fee_start_s": 72000, "fee_end_s": 32400}, "fee window"),
    ({"x_m": float("nan")}, "x_m must be a finite number"),
    ({"first_period_min": 60}, "first_period_min and first_period_cents"),
    ({"first_period_start_s": 0, "first_period_end_s": 3600}, "needs a first period"),
    ({"first_period_min": 60, "first_period_cents": 100, "first_period_start_s": 0}, "first_period_start_s and"),
    ({"first_period_min": 60, "first_period_cents": 100, "daily_cap_cents": 50}, "below first_period_cents"),
    ({"monthly_cents": 0}, "monthly_cents must be at least 1"),
    ({"tiers": garage_tiers_from_text(ROSENWALL)}, "leaves the single-window core empty"),
    ({"bands": garage_bands_from_text(OUTLETS)}, "tiers and bands|rate and the billing unit"),
])
def test_an_inconsistent_garage_is_rejected_at_construction(overrides, message):
    with pytest.raises(ValueError, match=message):
        _garage(**overrides)


def test_inconsistent_tiers_and_bands_are_rejected_at_construction():
    # the text parser rejects these as well; the garage re-validates what it is built from, so the tiers are built directly
    mixed_units = (cost.GarageTier(start_s=_hms(8), end_s=_hms(10), unit_min=30, price_cents=30),
                   cost.GarageTier(start_s=_hms(18), end_s=_hms(20), unit_min=60, price_cents=30))
    with pytest.raises(ValueError, match="one unit length"):
        GarageTariff(garage_id="x", x_m=0.0, y_m=0.0, tiers=mixed_units)
    overlapping = (cost.GarageTier(start_s=_hms(8), end_s=_hms(10), unit_min=30, price_cents=30),
                   cost.GarageTier(start_s=_hms(9), end_s=_hms(11), unit_min=30, price_cents=30))
    with pytest.raises(ValueError, match="overlap"):
        GarageTariff(garage_id="x", x_m=0.0, y_m=0.0, tiers=overlapping)
    with pytest.raises(ValueError, match="needs its fee window"):
        GarageTariff(garage_id="x", x_m=0.0, y_m=0.0, bands=garage_bands_from_text(OUTLETS))
    with pytest.raises(ValueError, match="no first period"):
        _banded(OUTLETS, first_period_min=60, first_period_cents=100)
    gap = (cost.GarageBand(from_min=0, to_min=60, kind="total", price_cents=100, unit_min=None),
           cost.GarageBand(from_min=90, to_min=None, kind="increment", price_cents=100, unit_min=60))
    with pytest.raises(ValueError, match="contiguous"):
        _banded(OUTLETS, bands=gap)


# ---------------------------------------------------------------------------------------- spec Amendment F: committed dataset

def _committed_garage_option(garage_id: str, arrival_s: int, departure_s: int, purpose: str, **options) -> int:
    """The garage option of a stay at a garage of the committed dataset, exported as the release does (``garage_entries``)."""
    from braunschweig.parking import garages as parking_garages
    from braunschweig.parking.tariff_export import garage_row_to_tariff

    path = committed_parking_path("parking_garages_2026.geojson")
    frame = parking_garages.load_garages(path).set_index("garage_id", drop=False)
    return cost.garage_option_cents(garage_row_to_tariff(frame.loc[garage_id], **options), arrival_s, departure_s,
                                    purpose=purpose)


def test_a_commuter_at_the_wallstrasse_pays_the_published_monthly_product_per_working_day():
    # Parkhaus Wallstrasse: 2.90 EUR per started hour, 19.00 EUR day cap (metered 8 h: 8 x 290 = 2320 -> cap 1900 ct); the
    # published monthly product 114.95 EUR (configurator) / 21 working days = 547.38 -> 547 ct (P2, half up). Work and education
    # pay min(1900, 547) = 547 ct, a shopper pays the metered 1900 ct; a 1 h stay is cheaper metered (290 ct) than the day share.
    eight_hours = (28800, 28800 + 8 * HOUR_S)
    assert _committed_garage_option("bs_wallstrasse", *eight_hours, "work") == 547
    assert _committed_garage_option("bs_wallstrasse", *eight_hours, "education") == 547
    assert _committed_garage_option("bs_wallstrasse", *eight_hours, "shop") == 1900
    assert _committed_garage_option("bs_wallstrasse", 28800, 28800 + HOUR_S, "work") == 290
    assert cost.garage_monthly_day_cents(11495) == 547


def test_a_commuter_at_a_braunschweig_garage_without_a_published_product_pays_the_imputed_median_per_working_day():
    # Parkhaus Magni (no published product, sold out): 1.20 EUR per started hour, 9.60 EUR day cap (metered 8 h 960 ct); the
    # imputed product of ASSUMPTION P13 is the Braunschweig median 107.48 EUR (80.00, 100.00, 114.95, 129.00) -> 10748 ct / 21 =
    # 511.8 -> 512 ct. Work: min(960, 512) = 512 ct; shop 960 ct. The sensitivity arm 'published only' prices the metered day.
    eight_hours = (28800, 28800 + 8 * HOUR_S)
    assert _committed_garage_option("bs_magni", *eight_hours, "work") == 512
    assert cost.garage_monthly_day_cents(10748) == 512
    assert _committed_garage_option("bs_magni", *eight_hours, "shop") == 960
    assert _committed_garage_option("bs_magni", *eight_hours, "work", monthly_imputation=False) == 960
    # a Wolfsburg garage without a published product: Congresspark 1.00 EUR per started hour, 6.00 EUR cap; the Wolfsburg median
    # is 60.00 EUR since the Parkdeck Hauptbahnhof (100.00 EUR, spec Amendment G2) is a fifth published product (50.00, 55.00,
    # 60.00, 98.00, 100.00): 6000 ct / 21 = 285.7 -> 286 ct; min(600, 286) = 286 ct for work
    assert _committed_garage_option("wob_congresspark", *eight_hours, "work") == 286
    assert cost.garage_monthly_day_cents(6000) == 286
    # Steinstrasse (published Tarif A 100.00 EUR / 21 = 476.2 -> 476 ct; metered 8 x 180 = 1440 -> cap 1800 stays 1440)
    assert _committed_garage_option("bs_steinstrasse", *eight_hours, "work") == 476


def test_the_wolfsburg_station_deck_is_a_priced_garage_option_with_its_published_monthly_product():
    # Parkdeck Hauptbahnhof P1 (spec Amendment G2): 1.70 EUR per started hour, the day tariff 9.00 EUR as the cap, the monthly
    # product Dauerparken Mo-So 24 h 100.00 EUR / 21 working days = 476.2 -> 476 ct (P2). Calculated by hand: 1 h 170 ct; 8 h
    # metered 8 x 170 = 1360 -> cap 900; work and education pay min(900, 476) = 476 ct, a shopper the metered 900 ct; 2 h metered
    # 340 ct is cheaper than the day share for a commuter as well
    eight_hours = (28800, 28800 + 8 * HOUR_S)
    assert _committed_garage_option("wob_hauptbahnhof", 28800, 28800 + HOUR_S, "shop") == 170
    assert _committed_garage_option("wob_hauptbahnhof", *eight_hours, "shop") == 900
    assert _committed_garage_option("wob_hauptbahnhof", *eight_hours, "work") == 476
    assert _committed_garage_option("wob_hauptbahnhof", *eight_hours, "education") == 476
    assert _committed_garage_option("wob_hauptbahnhof", 28800, 28800 + 2 * HOUR_S, "work") == 340
    assert cost.garage_monthly_day_cents(10000) == 476
    # the published product comes first: switching the imputation off does not change the deck, it has its own product
    assert _committed_garage_option("wob_hauptbahnhof", *eight_hours, "work", monthly_imputation=False) == 476
