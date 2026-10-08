# Parking garage options (distance-weighted, priced as an expected cost)

Mechanism of spec Amendment E of the parking cost zones v2 design (issue #436, Task 4d). The decisions and the rejected
alternatives live in ADR-0140; this note says where the code is and which rules a
maintainer must keep. The zone-based pricing around it is in [parking-cost-zones.md](parking-cost-zones.md).

## What it does

A car stay in a `street_paid` or `resident_zone` zone that passes the early rules (no zone, home, `parkingFree`,
resident, outside the street fee window) has the options "street" (weight 1) and every priced garage within
`garage_max_distance_m` (1000 m, ASSUMPTION G2) of the destination, weight `exp(-d / lambda)` (ASSUMPTION G1,
`garage_decay_m`). The price is the probability-weighted mean of the option costs, rounded half up to the cent once
(`braunschweig.parking.cost.parking_cost_with_garages`, the Python reference the Java port reproduces). A stay
whose street option costs 0 pays 0 (E4); an unavailable street renormalises the weights over the garages; a campus zone
and every early-rule stay price exactly as before. The new outcome `PAID_EXPECTED` is the LAST entry of `cost.OUTCOMES`.

## Where things are

| Concern | Place |
| --- | --- |
| Pricing of one stay, the three tariff structures, counters | `braunschweig/parking/cost.py` (`GarageTariff`, `garage_metered_cents`, `garage_option_cents`, `garage_options_in_range`, `parking_cost_with_garages`, `GarageOptionCounters`) |
| Band evaluation (reference, called not copied) | `braunschweig.parking.garages.duration_band_price_eur` |
| Dataset to model entries, schema 3, assumptions register | `braunschweig/parking/tariff_export.py` (`garage_entries`, `build_tariff_model`, `ASSUMPTIONS_REGISTER` G1 to G3 and P3 to P12) |
| Release input, config keys | `braunschweig/parking/zones_stage.py` (`parking_garages_path`), `braunschweig/matsim/simulation/prepare.py` (`parking_garage_decay_m`, `parking_garage_max_distance_m`) |
| Golden cases (Java contract) | `braunschweig/parking/golden_cases.py` (E01..E32, O01..O69), `scripts/export_parking_golden_cases.py`, `tests/fixtures/parking/parking_golden_cases.json` (schema 4) |
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
  at its start (start inclusive, end exclusive, a tier may cross midnight); a start in no tier is free; the first period is
  charged only for an arrival inside its clock window; the day cap is applied once per stay. A tiered garage may carry ONE
  closed free band in `bands` as its grace period (P10 for tiers, `GarageTariff` refuses any other band next to tiers): a stay
  not longer than the band (elapsed, the tiered form has no fee window) costs 0, a longer stay is priced by the tiers from its
  arrival. The schema 3 keys do not change, and the Java reader accepts tiers and bands together:
  `GarageTariff.form` returns "tiers" for a tiered garage with a grace band, so the port branches on the presence of both
  `tiers` and `bands`, never on the form name alone. Spec E14 amendment (1) narrows E11's "bands exclude tiers" to "no other
  band may stand next to tiers". The golden contract pins the combination (`fx_g15_tier_grace`, `fx_g16_tier_grace_cap`:
  O45..O59), the free schedule `0- free` (`fx_g17_free`: O60..O65, E29..E32) and a grace period equal to the billing unit
  (`fx_g18_grace_total`: O66..O69); their fixture garages lie 10 km north of every earlier destination and garage, so the
  option sets of E01..E28 and every earlier golden entry are unchanged.
- **Free schedule:** a car park that is free for every stay is the one open free band `0- free` with the formal fee window 0
  to 24 h (`garages.is_free_schedule`); it is a banded garage like any other for `garage_metered_cents`, rests on no P8 and,
  where it is only a municipal default (the Wolfsburg points without fee evidence), on P12.
- **A stay longer than a closed schedule (P9)** is priced per started 24 h and counted (`closed_schedule_repeats`); a
  closed schedule that does not end at 1440 min raises instead of guessing.
- **E8:** with garage options the zone-level garage family of a fixture zone is superseded (production zone rows have none);
  the street option is the street or long-stay product or the zone commuter product, or unavailable.
- **No silent fallback:** `GarageOptionCounters` reports how many stays had garages in range, how many paid 0 by E4, how
  many were priced over the garages alone and how many repeated a closed schedule; callers log `counters.summary()`. The
  Python reference prices nothing at run time, so no pipeline stage logs the counters; the Java listener logs these rates
  per iteration while the garage options are on. `build_tariff_model` warns, with the count and the zone ids, when a zone row still carries
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
The independent check the table reports (commuter garage share against 0.464) is a number and never validation. The table
checks no paid share (the zone-level one was blind to the fee window and read 1.0 in Ia and Ib, ruling R-5-4): a run's
time-aware paid share, garage share and free shares are compared with the SrV by `scripts/parking/compare_parking_targets.py`
on the run's own outcome report (see the next section). Setting the value is part of the server run (task 5b of issue #436).

## Comparison with the SrV references

How a run's paid share, garage share and free shares are compared with the committed SrV tables
(`scripts/parking/compare_parking_targets.py`), with the universe caveats, is in
[parking-target-comparison.md](parking-target-comparison.md). The calibration script takes `--facility-kinds` (default every
kind): the release value is the calibration on every kind, and a run on `garage` only reports lambda without the surface lots
as a sensitivity number (the table records the filter).

## Monthly products at garages (spec Amendment F, Task 4f)

- Work and education stays pay `min(metered or day price, monthly_cents / 21)` at a garage (P2); the export writes
  `monthly_cents` as the published `monthly_eur` or, with the config key `parking_garage_monthly_imputation` true (the
  default, declared in the `PARKING_DEFAULTS` of `matsim.simulation.prepare`, in `configs/base_bs.yml` and in both popsim
  fixtures), the imputed `monthly_imputed_eur`. Nothing in `braunschweig.parking.cost` and nothing in Java changed: no schema,
  key set or golden fixture; ASSUMPTION P13 is listed in the model's register only when an imputed product is used
  (`tariff_export.assumption_texts(monthly_imputed=True)`), which keeps the committed fixture model byte-identical.
- The values are read, never typed: `scripts/curation/parking_zones_2026/bs_monthly_products.py` reads the cheapest
  current product of a Braunschweig garage from the owner's package `Braunschweig_Monatstarife_2026-10-08.zip` (rule named
  in `regional_garage_specs.BS_MONTHLY_SPECS`, checked to be the cheapest) and the Wallstrasse price from the 'weitere Monate
  ... Brutto' line of the Contipark configurator capture; every Braunschweig garage has a status row in the QA table (no_price,
  sold_out, price_on_request, period_unconfirmed) with the package's sentence, and `BS_RECORDED_SPECS` lists the facilities that
  are no garage of the dataset (Eves and Fichtengrund count for P13; the DB BahnPark lots, whose products are USED products of their zones since spec Amendment G1 (`decision` used, `zone_ids`), and the APCOA lots never do). The Wolfsburg Parkdeck Hauptbahnhof is a garage since Amendment G2 (`GARAGE_SPECS`, its monthly product 100.00 EUR is one of the five Wolfsburg values of P13).
- ASSUMPTION P13 is computed once, by `regional_garages.apply_monthly_imputation`, with the single implementation
  `garage_qa.expected_imputed_monthly` (median in integer cents, half up) that `validate_garage_qa` checks the committed
  table against: a withdrawn or added published product makes the committed imputed values fail the validator until the
  curation is rerun. Imputation is per municipality, needs at least `garages.MINIMUM_PUBLISHED_MONTHLY_PRODUCTS` (2) products
  and never touches a surface lot or a garage with a published product. `garages.monthly_summary` is the one count that the
  export log, the preparation report and `scripts/validate_parking_zones.py` print (published, imputed, none, median).
- A dataset file without the column `monthly_imputed_eur` is refused by `garages.load_garages` (`LegacyColumnsError`, naming
  the column and the curation command). Only `allow_legacy_columns=True` (the test fixtures; `zones_stage` only when every row
  carries the test-set marker) reads it with the column empty and a warning; the committed dataset always has it.
- The curation step and the tariff export warn per municipality when more than `garages.MONTHLY_IMPUTATION_WARNING_SHARE` (50 %)
  of its priced garages carry an imputed instead of a published product (`garages.monthly_imputation_warnings`; Braunschweig
  10 of 12 and Wolfsburg 5 of 9 do).

## Known limitations

- The garage share is a calibration target, so it no longer validates the model; the universe caveat of E5 (SrV asks
  residents about their usual place, the model averages over destinations) is stated in the table header.
- Most committed rows rest on at least one assumption; the counts per assumption and the warning share are in the data record `parking_garages_2026` (one fact, one file), and the loader warns at every load for the first share.
- The effect of the garage options on exposure and expected cost per town is not reported yet: it needs plans, and the
  reference plans are lost (task 5b of issue #436).
- Monthly products (Amendment F): Tarif A of the Steinstrasse is limited to Mo-Fr 06:30-21:00 and the limit is not modelled;
  the imputed products rest on the published products of their own municipality (counts in the data record `parking_garages_2026`) and no capacity of monthly places is modelled (the sold-out
  garages Magni and Packhof, and the Eiermarkt, whose operator offers no monthly product at all (Contipark capture of
  2026-10-08), still carry the imputed product); the Braunschweig P13 median mixes a Mo-Fr 06:30-21:00 product (Steinstrasse Tarif A, 100.00 EUR) with 24/7 products (Wallstrasse, Eves, Fichtengrund), so the imputed product prices a regular's access window that not every garage offers; the sensitivity arm `zones_v2_published_monthly_only` (imputation false) bounds the
  effect, and no v2 run exists yet, so the effect on the commuter garage share is an expectation (ADR-0140, decision 10).
