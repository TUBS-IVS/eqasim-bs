# Parking garage options (distance-weighted, priced as an expected cost)

Mechanism of spec Amendment E of the parking cost zones v2 design (issue #436, Task 4d). The decisions and the rejected
alternatives live in the design spec and the ADR of the feature; this note says where the code is and which rules a
maintainer must keep. The zone-based pricing around it is in [parking-cost-zones.md](parking-cost-zones.md).

## What it does

A car stay in a `street_paid` or `resident_zone` zone that passes the early rules (no zone, home, `parkingFree`,
resident, outside the street fee window) has the options "street" (weight 1) and every priced garage within
`garage_max_distance_m` (1000 m, ASSUMPTION G2) of the destination, weight `exp(-d / lambda)` (ASSUMPTION G1,
`garage_decay_m`). The price is the probability-weighted mean of the option costs, rounded half up to the cent once
(`braunschweig.parking.cost.parking_cost_with_garages`, the Python reference the Java port of Task 4e reproduces). A stay
whose street option costs 0 pays 0 (E4); an unavailable street renormalises the weights over the garages; a campus zone
and every early-rule stay price exactly as before. The new outcome `PAID_EXPECTED` is the LAST entry of `cost.OUTCOMES`.

## Where things are

| Concern | Place |
| --- | --- |
| Pricing of one stay, the three tariff structures, counters | `braunschweig/parking/cost.py` (`GarageTariff`, `garage_metered_cents`, `garage_option_cents`, `garage_options_in_range`, `parking_cost_with_garages`, `GarageOptionCounters`) |
| Band evaluation (reference, called not copied) | `braunschweig.parking.garages.duration_band_price_eur` |
| Dataset to model entries, schema 3, assumptions register | `braunschweig/parking/tariff_export.py` (`garage_entries`, `build_tariff_model`, `ASSUMPTIONS_REGISTER` G1 to G3 and P3 to P11) |
| Release input, config keys | `braunschweig/parking/zones_stage.py` (`parking_garages_path`), `braunschweig/matsim/simulation/prepare.py` (`parking_garage_decay_m`, `parking_garage_max_distance_m`) |
| Golden cases (Java contract) | `braunschweig/parking/golden_cases.py` families E01..E28 and O01..O44, `scripts/export_parking_golden_cases.py`, `tests/fixtures/parking/parking_golden_cases.json` (schema 4) |
| Calibration of lambda | `scripts/parking/calibrate_garage_decay.py` |

## Rules maintainers must keep

- **Order of operations is a contract.** Options are the street first, then the garages by ascending `garage_id`;
  `P_i = w_i / sum(w)`, the expectation is summed left to right in double precision, the distance is `sqrt(dx*dx +
  dy*dy)` (never `hypot`), the price is `floor(expected + 0.5)`. The golden generator asserts that every pinned
  expectation keeps 1e-6 cents from a half cent, so `Math.exp` of Java cannot flip a rounding; a new case must satisfy it.
- **Bands are integer arithmetic in Java.** Python calls `duration_band_price_eur` on `chargeable_s / 60`; the port uses
  the band of `seconds <= 60 * to_min` and `ceil((seconds - 60 * from_min) / (60 * unit_min))` units, which
  `tests/test_parking_garage_options.py` proves equal for every second near the band edges (also for a band whose length
  is no multiple of its unit). The price reached at the start of the next band counts started units, also at the band
  end: `ceil((to_min - from_min) / unit_min)` units of the band, never the floor.
- **One unit length per tiered garage.** The tier parser and `GarageTariff` require all tiers of a garage to share one
  unit length (the units are counted from the arrival, P6); this is stricter than spec E10 and is stated in the P6 text of
  the assumptions register of the model.
- **Tiers (P6):** units are counted from the arrival (or from the end of the first period); a unit costs the tier in force
  at its start (start inclusive, end exclusive, a tier may cross midnight); a start in no tier is free; the first period
  is charged only for an arrival inside its clock window; the day cap is applied once per stay.
- **A stay longer than a closed schedule (P9)** is priced per started 24 h and counted (`closed_schedule_repeats`); a
  closed schedule that does not end at 1440 min raises instead of guessing.
- **E8:** with garage options the zone-level garage family of a fixture zone is superseded (production zone rows have none);
  the street option is the street or long-stay product or the zone commuter product, or unavailable.
- **No silent fallback:** `GarageOptionCounters` reports how many stays had garages in range, how many paid 0 by E4, how
  many were priced over the garages alone and how many repeated a closed schedule; callers log `counters.summary()`. The
  Python reference prices nothing at run time, so no pipeline stage logs the counters; the Java port logs these rates
  during the run (Task 4e). `build_tariff_model` warns, with the count and the zone ids, when a zone row still carries
  zone-level garage columns while `garage_decay_m` is above 0 (E8: they are superseded; production rows have none).
- **The model is schema 3.** Every key set is exact (`cost.GARAGE_FIELDS_JSON`, `GARAGE_TIER_FIELDS`,
  `GARAGE_BAND_FIELDS`); `garage_decay_m` 0 switches the options off, and the export refuses `garage_decay_m > 0` without
  a priced garage.

## Calibration and its state

`garage_decay_m` is a release value with its calibration table `parking_garage_decay_calibration_2026.csv`, written by
`scripts/parking/calibrate_garage_decay.py` on the plans of the reference scenario (target: the SrV garage share
`0.708 / (0.708 + 0.2537)`, READ from `srv2023_city_center_parking`, never typed). The script is built and tested on a
synthetic fixture (`tests/fixtures/parking/calibration_plans_fixture.xml`). The reference plans were lost on 2026-10-07, so
the calibration has NOT been run: `parking_garage_decay_m` is 0 in `configs/base_bs.yml` and the two popsim fixture
configs, and `tests/test_parking_garage_decay_config.py` requires 0 until the table exists and the table value once it does.
The independent checks the table reports (commuter garage share against 0.464, a zone-level street-paid share against the
SrV paid share 0.8333) are numbers and never validation. Setting the value is part of the server run of Task 5.

## Known limitations

- The garage share is a calibration target, so it no longer validates the model; the universe caveat of E5 (SrV asks
  residents about their usual place, the model averages over destinations) is stated in the table header.
- 34 of the 35 committed garages rest on at least one assumption (P4 or P5 for 30); the loader warns at every load.
- The effect of the garage options on exposure and expected cost per town is not reported yet: it needs plans, and the
  reference plans are lost (Task 5).
