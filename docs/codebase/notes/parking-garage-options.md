# Parking garage options (distance-weighted, priced as an expected cost)

Mechanism of spec Amendment E of the parking cost zones v2 design (issue #436, Task 4d). The decisions and the rejected
alternatives live in ADR-0140; this note says where the code is and which rules a
maintainer must keep. The zone-based pricing around it is in [parking-cost-zones.md](parking-cost-zones.md).

## What it does

A car stay in a `street_paid` or `resident_zone` zone that passes the early rules (no zone, home, `parkingFree`,
resident, outside the street fee window) has the options "street" and every priced garage within
`garage_max_distance_m` (1000 m, ASSUMPTION G2) of the destination that the duration of the stay allows, with the weight
`exp(-d / lambda - beta * c / 100)` (ASSUMPTION G1, `garage_decay_m`; lambda_c, `garage_decay_commute_m`, ASSUMPTION G1-c, for
a stay whose purpose is work or education: the helper `cost.garage_decay_m_for_purpose` is the single place that picks the
decay of a stay; d is 0 for the street, c the cost in cents of the option for this stay, beta the price coefficient of
ASSUMPTION G1-d, see "Feasibility and the price term" below). The price is the probability-weighted mean of the option costs,
rounded half up to the cent once (`braunschweig.parking.cost.parking_cost_with_garages`, the Python reference the Java port
reproduces). A stay whose street option costs 0 pays 0 (E4); an infeasible street renormalises the weights over the garages; a
campus zone and every early-rule stay price exactly as before. The new outcome `PAID_EXPECTED` is the LAST entry of
`cost.OUTCOMES` (since v3 followed by `NO_FEASIBLE_OPTION` and `EV_EXEMPT`).

## Where things are

| Concern | Place |
| --- | --- |
| Pricing of one stay, the three tariff structures, counters | `braunschweig/parking/cost.py` (`GarageTariff`, `garage_metered_cents`, `garage_option_cents`, `garage_options_in_range`, `parking_cost_with_garages`, `parking_cost_with_garages_detail`, `option_weight`, `street_feasible`, `garage_decay_m_for_purpose`, `GarageOptionCounters`) |
| Band evaluation (reference, called not copied) | `braunschweig.parking.garages.duration_band_price_eur` |
| Dataset to model entries, schema 5, assumptions register | `braunschweig/parking/tariff_export.py` (`garage_entries`, `build_tariff_model`, `garage_decays_from_model`, `ASSUMPTIONS_REGISTER` present only where a priced row names them: P3 to P8, P10, P11, P13, P14; always present: P9, G1 to G3 (with G1-c, G1-d) and M3; E1 and E2 only with an EV exemption in the model; P12 is withdrawn and never listed) |
| Release input, config keys | `braunschweig/parking/zones_stage.py` (`parking_garages_path`), `braunschweig/matsim/simulation/prepare.py` (`parking_garage_decay_m`, `parking_garage_decay_commute_m`, `parking_garage_max_distance_m`) |
| Golden cases (Java contract) | `braunschweig/parking/golden_cases.py` (E01..E38, O01..O69 and the v3 families F, P and X), `scripts/export_parking_golden_cases.py`, `tests/fixtures/parking/parking_golden_cases.json` (schema 6) |
| Calibration of lambda and lambda_c | `scripts/parking/calibrate_garage_decay.py` |

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
  to 24 h (`garages.is_free_schedule`); it is a banded garage like any other for `garage_metered_cents`, rests on no P8 and
  on no assumption: a free lot states its free status by a source (ASSUMPTION P12, the municipal default, is withdrawn since
  spec Amendment I5a and no row rests on it).
- **A stay longer than a closed schedule (P9)** is priced per started 24 h and counted (`closed_schedule_repeats`); a
  closed schedule that does not end at 1440 min raises instead of guessing.
- **E8:** with garage options the zone-level garage family of a fixture zone is superseded (production zone rows have none);
  the street option is the street or long-stay product or the zone commuter product, or unavailable.
- **No silent fallback:** `GarageOptionCounters` reports how many stays had garages in range, how many paid 0 by E4, how
  many were priced over the garages alone and how many repeated a closed schedule; callers log `counters.summary()`. The
  Python reference prices nothing at run time, so no pipeline stage logs the counters; the Java listener logs these rates
  per iteration while the garage options are on. `build_tariff_model` warns, with the count and the zone ids, when a zone row still carries
  zone-level garage columns while `garage_decay_m` is above 0 (E8: they are superseded; production rows have none).
- **The model is schema 5.** Every key set is exact (`cost.GARAGE_FIELDS_JSON`, `GARAGE_TIER_FIELDS`,
  `GARAGE_BAND_FIELDS`); `garage_decay_m` 0 switches the options off for the purposes it governs, `garage_decay_commute_m`
  (schema 4, spec Amendment H) for work and education, and the export refuses either decay above 0 without a priced garage;
  schema 5 adds `option_price_beta_u_per_eur` and the garage field `max_stay_min` (spec v3 A1, A2).
- **Two decay lengths, one helper (ASSUMPTION G1-c).** `cost.garage_decay_m_for_purpose` returns lambda_c for the purposes
  of `cost.COMMUTER_PURPOSES` and lambda for every other purpose; the pricing, the golden cases and the Java port take the
  decay of a stay from this rule only. The commuter decay is never defaulted to lambda: `parking_cost_with_garages` requires
  it (keyword), `build_tariff_model` and the preparation accept `None` only while lambda is 0 and refuse it otherwise. A
  schema-3 model has no commuter decay and every purpose uses `garage_decay_m` (`tariff_export.garage_decays_from_model`;
  the Java reader does the same). Since schema 5 of the golden file every case carries `garage_decay_commute_m`, equal to
  `garage_decay_m` in every case before E33 (so those cases keep their content and price), and E33 to E38 pin the two
  decays (lambda 400 m, lambda_c 250 m of the fixture model, `FIXTURE_GARAGE_DECAY_COMMUTE_M`) with a work, a shopping and
  an education stay at the same destination and the two off switches.

## Feasibility and the price term (spec v3 A1 and A2, ADR-0142)

- **Feasible options.** A stay whose chargeable duration (street fee window, seconds rounded up to minutes) exceeds the
  maximum stay of its zone has no street option unless the zone has a long-stay product (ASSUMPTION M3; the detail flag
  `GarageStayPrice.street_feasible`, which counts street OR zone commuter product, not the free function `street_feasible`,
  decides the street weight). A garage or lot whose own `max_stay_min` is below the duration leaves the set too.
  The two maxima are compared on different durations, on purpose: a garage or lot maximum with the ELAPSED stay (the car
  stands there all the time; `cost.option_feasible`, Java `GarageParkingCostCalculator`), the street maximum with the
  chargeable stay inside the fee window (M1), so a lot that carries the zone's own maximum is never less restrictive than
  its street but can be stricter. A stay with no feasible option is priced as the street at its
  metered cents, the maximum ignored, and named `NO_FEASIBLE_OPTION`; the
  Python counters `street_unavailable` (a street-infeasible stay), `options_infeasible` and `no_feasible_option` of
  `cost.GarageOptionCounters` and the Java report columns `street_infeasible_count` and `no_feasible_option_count` make the
  case visible. An error is never raised for it; the arm check of Part C flags a high rate.
- **Price term.** `cost.option_weight` is `exp(-distance_m / decay_m - beta_u_per_eur * cost_cents / 100)` with the exponent
  evaluated as `(-d / lambda) - ((beta * c) / 100)` in this order (Java: `StrictMath.exp`; the order is a contract like the
  summation order of the expectation). beta is the config key `parking_option_price_beta_u_per_eur` (0.310998, the magnitude of
  `betaCost_u_MU`), written into the model as `option_price_beta_u_per_eur`; the Java class `ParkingOptionPriceBetaCheck`
  refuses a run whose model value differs from the mode choice by more than 1e-9, also 0 in a schema-5 model (ADR-0142 decision 2).
  The Python reference accepts beta 0 (finite and >= 0 is validated) and then reproduces the v2 weights bit for bit; the v2
  golden cases are pinned that way. beta is no free parameter: it is not calibrated (one share cannot identify it together
  with lambda).
- **All weights underflow.** `parking_cost_with_garages_detail` raises a named `ValueError` when the weights sum to 0 (the Java
  calculator refuses the same stay) instead of a bare division error.
- **The decay lengths and the price term.** lambda and lambda_c of `configs/base_bs.yml` were first fitted on the price-free
  weights. Since Part B (B5) `scripts/parking/calibrate_garage_decay.py` prices and weights every stay through
  `cost.parking_cost_with_garages_detail` (feasibility of spec A1, the weights `cost.option_weight` of spec A2, beta from the
  config), so the lengths are calibrated for the weights the run uses; see the section "Calibration and its state" below. The
  calibration has no weight formula of its own (a test fails a mutant with the v2 closed form when beta > 0).

## Calibration and its state

`garage_decay_m` (lambda, every purpose but work and education) and `garage_decay_commute_m` (lambda_c, work and education)
are release values with their calibration table `parking_garage_decay_calibration_2026.csv`, written by
`scripts/parking/calibrate_garage_decay.py` on the plans of the reference scenario. Since Task B5 (spec v3) the script routes
every stay of its universes through the production pricing: `cost.parking_cost_with_garages_detail` with the tariff row of the
zone, the activity times of the plans (arrival `start_time`, departure `end_time`, rule T1 for a missing end, minimum stay L1)
and the weights `cost.option_weight` at the config's option price coefficient; the share of a stay is read from the detail
object (`garage_probability`, or `W_paid / (W_paid + w_street)` for the paid-only measure). A stay without any feasible option
(NO FEASIBLE OPTION) counts an off-street share of 0, is counted and logged as a rate per universe, and a stay whose street is
infeasible but that has a feasible option is forced off-street (share 1). The beta of the table is pinned by tests to
`PARKING_DEFAULTS`, every config and the module constant `PARKING_GARAGE_DECAY_CALIBRATED_BETA_U_PER_EUR`; `--beta` and
`--sensitivity-beta` give the beta sensitivities (0 is the v2 form). The values, the old values, the sensitivities and the
limitations (no `parkingEvExempt` in the reference plans, the arrival is the activity start) are in the data record. Targets, all READ from the committed SrV
tables and never typed: lambda against the SrV garage share `0.708 / (0.708 + 0.2537)` of `srv2023_city_center_parking`
(E5, the destination universe outside home, work and education in Ia and Ib), lambda_c against the PAID-only commuter
garage share `share_garage_large_lot_paid / (share_garage_large_lot_paid + share_street_paid)` of the row `bs_zentrum` of
`srv2023_commute_parking_by_workplace_class` (H2 as revised by ruling R-4h-1: the work and education activities in Ia and Ib
that do NOT carry `parkingFree` are the payers only, because the free-parking draw frees everyone who parks free, so the
reference must be the paid-only share; the free-inclusive share has free parkers in its denominator and is not used; the plans
reader `count_zone_exposure.read_main_activities` provides the attribute as the column `parking_free`). Both are CALIBRATION TARGETS
and no validation. The independent check the table reports (H3b) is the paid-only Wolfsburg commuter garage share: the mean garage
share at lambda_c of the work and education activities without `parkingFree` in the Wolfsburg zones (every zone of the tariff
table with `municipality_ags` 03103000)
against the row `03103`, a number with its universe size and never validation. The script is tested on a synthetic fixture
(`tests/fixtures/parking/calibration_plans_fixture.xml`) and was run on 2026-10-09 on the plans of the 1 % server run of
2026-10-08 and again, on the 25 % reference plans and the enlarged option set, on 2026-10-14 (the table is committed and the two
config keys equal its rows `decay_length_m` and `decay_commute_length_m` in
`configs/base_bs.yml` and the two popsim fixture configs; `tests/test_parking_garage_decay_config.py` requires both
equalities and that the header pins of the garage dataset, the zone file and the two SrV tables are the pins of their data
records and the committed files; the results, universes and provenance are in ADR-0140 decision 14 and the data record
`parking_garage_decay_calibration_2026`). The table checks no paid share (the zone-level one was blind to the fee window and
read 1.0 in Ia and Ib, ruling R-5-4): a run's time-aware paid share, garage share and free shares are compared with the SrV
by `scripts/parking/compare_parking_targets.py` on the run's own outcome report (see the next section). The reader of the
table refuses a table without `decay_commute_length_m` (written before Amendment H).

The measure of each target has the universe of its SrV quantity (spec Amendment I6): lambda counts EVERY option
(payment-inclusive, the SrV shares contain free and paying parkers), lambda_c and the Wolfsburg check count the PAID options only
(a free option, the free schedule `0- free` read from the schedule of the row and never from a name or a rate, is a free
parker in SrV terms and leaves numerator and denominator: `W_paid / (W_paid + w_street)`, which is `sum(w_paid) / (1 +
sum(w_paid))` at beta 0, derived in the script documentation). The table header and rows name the measure of each target and the free options within D_max per universe (by id,
with the activities reached), and the freight agents of the plans (ids `freight_*`, no residents) are excluded. A universe that
the options barely reach (fewer than the share of `--minimum-reach-share-warning`, default one half, of its activities have an
option of its measure within D_max; a log-only plausibility threshold and no scientific parameter) is warned about.
`--sensitivity-garages LABEL=PATH` runs the same calibration on another garage dataset (the lot thresholds 30 and 100) and
writes the two decay lengths as header lines, labelled sensitivity and never a release value; the search stops at the first
bisection midpoint within the tolerance, so differences of that order between option sets are not resolved. The table belongs
to the garage dataset, the zone file and the tariff table named in its header exactly: any change of the option set, a fee
window, a maximum stay or a long-stay product needs a new calibration.

## Comparison with the SrV references

How a run's paid share, garage share and free shares are compared with the committed SrV tables
(`scripts/parking/compare_parking_targets.py`), with the universe caveats, is in
[parking-target-comparison.md](parking-target-comparison.md); which of its commuter and Wolfsburg rows are paid-only depends on
the version of the outcome report (v4 splits the paid and the free probability, ADR-0142). The calibration script takes
`--facility-kinds` (default every kind): the release value is the calibration on every kind, and a run on `garage` only reports
lambda without the surface lots as a sensitivity number (the table records the filter).

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

## Large surface lots (spec Amendment I, Task 4j)

The option set is the garages and the large public surface lots of the research package (`facility_kind` surface_lot, the
size threshold of ASSUMPTION L50; the data record `parking_garages_2026` states the counts), priced by the same code without
a special case: a lot is one more point with a schedule
(single window, duration bands or the free schedule). The reading of the package, the policy, the tariff roles, the median
product of P14 and the evidence gate are in [parking-large-lots.md](parking-large-lots.md); the decisions in ADR-0140 decision 13.
Three consequences for this mechanism: a lot that carries the street product of its zone has the same price as the street
there for every stay of 1 to 720 min (the range of the exactness sweep: arrivals 06:00, 09:00, 14:00 and 19:00, purposes work
and shopping; the data record `parking_garages_2026` states the sweep, no claim is made outside it), because the maximum stay of the zone is written as bands and the lot row alone reproduces the street
path (owner decision A and ruling R-4j-6, so the option changes the weights and not the price at the same spot); many lots
are free (cost 0 at every duration, E4 unchanged); and the decay lengths were recalibrated on the enlarged set (spec I6,
ADR-0140 decision 14), where the commuter decay and the Wolfsburg check count paid options only, so a free option leaves them.

## Known limitations

- **Option weights ignore capacity.** A small lot pulls as much as the largest garage at the same distance (E3: capacity and
  occupancy are not modelled); the dataset's `capacity_reported` is information only.
- The garage shares of the city centre and of `bs_zentrum` are calibration targets, so they no longer validate the model;
  the universe caveat of E5 and H2 (SrV asks residents about their usual place, the model averages over destinations) is
  stated in the table header. The universes are samples whose sizes the table states, and the independent Wolfsburg check is
  a number without a bound that differs from the SrV row (ADR-0140 decisions 12 and 14; the cause is not established).
- Most committed rows rest on at least one assumption; the counts per assumption and the warning share are in the data record `parking_garages_2026` (one fact, one file), and the loader warns at every load for the first share.
- The effect of the garage options on exposure and expected cost per town is not reported yet (no v2 run exists; task 5b of
  issue #436).
- Monthly products (Amendment F): Tarif A of the Steinstrasse is limited to Mo-Fr 06:30-21:00 and the limit is not modelled;
  the imputed products rest on the published products of their own municipality (counts in the data record `parking_garages_2026`) and no capacity of monthly places is modelled (the sold-out
  garages Magni and Packhof, and the Eiermarkt, whose operator offers no monthly product at all (Contipark capture of
  2026-10-08), still carry the imputed product); the Braunschweig P13 median mixes a Mo-Fr 06:30-21:00 product (Steinstrasse Tarif A, 100.00 EUR) with 24/7 products (Wallstrasse, Eves, Fichtengrund), so the imputed product prices a regular's access window that not every garage offers; the sensitivity arm `zones_v2_published_monthly_only` (imputation false) bounds the
  effect, and no v2 run exists yet, so the effect on the commuter garage share is an expectation (ADR-0140, decision 10).
