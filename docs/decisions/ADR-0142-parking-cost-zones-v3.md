# ADR-0142 · 2026-10-14 · Parking cost zones v3, Part A: stay feasibility, option prices, cost factor, EV exemption, report v4

- **Status:** accepted pending owner review at the PR (Part A, mechanics ON by default, no model run). The production flag
  `parking_zones_enabled` is unchanged; the three keys below have defaults that apply when the flag is on. At the time of
  writing (2026-10-14) no model run of v3 exists; the current state is held by the feature record `parking_cost_zones`,
  not by this record.
- **Numbering:** ADR-0142 is the next free id after ADR-0140 for this branch. The design spec and the plans said ADR-0141;
  on 2026-10-14, checked across all local and remote branches, ADR-0141 is taken by `feature/i442-portal-trips`
  (`ADR-0141-portal-trips-through-the-cordon-gates.md`, pushed), so the v3 record is ADR-0142 (ruling R-ADR). No other branch
  holds an id above ADR-0141.
- **Issue:** #436 (the owner decided on 2026-10-14 that v3 needs no issue of its own: all v3 commits carry #436).
- **Extends:** ADR-0140 (and through it ADR-0139). **Supersedes in part:** the v2 rule of ADR-0140 decision 1 that a zone
  with a maximum stay needs a long-stay product or a garage (assumption M1 stays for the zones that have the product), and the
  limitation of ADR-0140 decision 14 that the run comparison cannot split paid from free off-street options (closed for the
  two paid-only rows by decision 4 below).
- **Scope of this record:** Part A (model mechanics: Python reference, Java port, golden file, report). Part B (data: zone-table
  corrections, Wolfsburg area zones, resident districts, free-lot evidence, recalibration of the decay lengths) and Part C
  (runs) append their own sections when they are built. This record states NO model result.

## Context

The 2026-10-14 gap inventory and the owner's research packages showed three things the v2 model gets wrong or cannot say
(design spec of 2026-10-14, local, section 0): (1) a maximum stay is a rule, not a data field: in Braunschweig Ia (180 min,
no long-stay product), Salzgitter, Helmstedt and several others a commuter cannot park on the street at all, yet v2 let him,
at the price of a product that does not exist there (assumption M1 borrowed Ib's 9.00 EUR ticket for Ia); (2) the
behavioural response to the parking price is unexamined: the parking euro enters the car utility like the driving euro, the
garage choice ignores price, and one more lever was asked for; (3) the outcome report cannot separate paid from free
off-street options, and electric cars are exempt from the street fee in several towns. The owner selected the items on
2026-10-14 and decided each point quoted below. Every decision here is implemented in the Python reference
(`braunschweig.parking.*`) and in the Java port (`org.eqasim.braunschweig.parking`, eqasim-java-bs), which agree on the
shared golden file.

## Decision

1. **Maximum-stay feasibility (spec A1, ASSUMPTION M3; owner decision 2026-10-14, "1 ja").** A car stay whose chargeable
   duration inside the street fee window exceeds the maximum stay of its zone has no street option. With a long-stay product
   (`long_stay_product_eur`) it buys that product (M1 stays for exactly those zones); without one the street leaves the option
   set and the stay is priced over the remaining options: the garages and lots within the maximum distance, each with its own
   schedule and its own maximum stay where the dataset states one (a lot whose maximum is below the duration leaves the set too).
   Where NO option remains, the stay is priced as the street at its metered price for the duration, the maximum ignored so that
   the cost stays finite, and counted as the outcome `NO_FEASIBLE_OPTION`: the report makes the incompleteness of the option set
   visible instead of hiding it (a signal, never a silent fallback). `NO_FEASIBLE_OPTION` is a PAID outcome in the target
   comparison (it is priced at the street's metered cents). Unchanged and in front of the test: E4 (free street, everything
   free), the home, employer-free, campus and resident rules. Fee window and maximum stay stay separate fields; the maximum
   compares the chargeable duration inside the fee window.
   An asymmetry that is intended: the maximum stay of a garage or lot is compared with the ELAPSED stay (the car stands there
   all the time; `cost.option_feasible`, Java `GarageParkingCostCalculator`), the street maximum with the chargeable stay
   inside the fee window (M1); a lot that carries the zone's own maximum is therefore never less restrictive than its street,
   but it can be stricter.
   - **Validator relaxation (ruling R-A-6).** A street_paid zone may now state `max_stay_min` without a long-stay product and
     without garage columns (`braunschweig.parking.cost` ZoneTariff and `braunschweig.parking.zones.validate_tariffs`); a
     resident_zone keeps the v2 rule, and since the final review of Part A even a zone-level garage core no longer rescues
     it: with the garage options on that core is superseded, and a stay above the maximum would be priced at the disc's 0 ct
     as `NO_FEASIBLE_OPTION` and counted as paid. That check is in `zones.validate_tariffs` (the table gate); the marked
     synthetic fixture row `fx_res_garage_v2`, which pins the v2 zone-garage arithmetic in both repositories, is exempt, and
     the Java reader keeps accepting that combination for the same reason. The v2 test that pinned the refusal was replaced
     by its opposite, a deliberate behaviour change of the spec, stated in the commit. Part B needs it for Braunschweig Ia,
     Salzgitter, Helmstedt, Bad Harzburg and Seesen.
   - **Counters (fallback transparency).** The Python `cost.GarageOptionCounters` counts the stays priced over the garages
     alone because the street is unavailable (`street_unavailable`), the garage options left out for a maximum stay
     (`options_infeasible`) and the stays without any feasible option (`no_feasible_option`); the Java outcome report v4
     carries `street_infeasible_count` and `no_feasible_option_count` (decision 4). The model has no
     threshold for `NO_FEASIBLE_OPTION`: the planned Part C arm check flags a share of 1 % of the stays in Ia and Ib (ruling
     R-A-17), a plausibility flag and no scientific parameter.
   - **Calibration consequence (Part B, B5).** The calibration measure must use the same option set as the pricing: the street
     weight is 1 only where the street is feasible for the stay's duration, and a commuter activity without a feasible street
     contributes an off-street share of 1. Part B implements it (Task B5, the calibration prices every stay through the
     production code); the targets and universes do not change. A stay with NO feasible option at all contributes an
     off-street share of 0 (the production pricing gives it no option share; no share of 1 is invented), counted and reported
     as a rate per universe (data record `parking_garage_decay_calibration_2026`).
2. **Price in the option choice (spec A2, ASSUMPTION G1-d; owner item 5).** The weight of an option is
   `w_i = exp(-d_i / lambda - beta * c_i / 100)` for every option including the street (d_i the distance in metres, 0 for the
   street; c_i the option's cost in cents for THIS stay: duration, fee window, products, EV exemption, feasibility; lambda the
   decay length of the stay's purpose, ADR-0140 decisions 7 and 12). beta is the MAGNITUDE of the car cost coefficient of the
   mode choice, `BraunschweigModeParameters.betaCost_u_MU = -0.310998`, so beta = 0.310998 utility per EUR; the config key is
   `parking_option_price_beta_u_per_eur` (default and `configs/base_bs.yml` value 0.310998; written into the tariff model as
   `option_price_beta_u_per_eur`). ASSUMPTION G1-d: ONE money coefficient for the choice between parking options, without the
   income or distance elasticity of the person's whole utility (that term belongs to the whole trip, not to the comparison of
   two car parks). The expected cost is the probability-weighted mean as before. Python and Java compute the same weights with
   the same order of operations (the exponent is `(-d / lambda) - ((beta * c) / 100)`, `StrictMath.exp` in Java) and the same
   summation order; the golden cases pin them. beta 0 gives the price-independent weights of v2 bit for bit in the Python
   reference (tested against the v2 golden cases).
   - **Startup check.** The Java class `ParkingOptionPriceBetaCheck` refuses to start a run whose tariff model's coefficient
     differs from `Math.abs(betaCost_u_MU)` of the mode choice actually in force (after the mode parameter file and the
     command-line overrides) by more than 1e-9, naming both values and both ways out. The coefficient of the garage choice and
     the one of the mode choice cannot drift apart silently.
   - **beta 0 is refused in a schema-5 model (decision 2).** The coefficient is the mode-choice cost coefficient, not a
     free lever: a schema-5 run model with 0 fails the check like any other value that differs from the mode choice. A price-free
     comparison uses a schema-4 model (no coefficient, price-independent weights). The Python reference and the golden cases
     keep 0 valid as the v2 form (and the Part B recalibration reports it as a sensitivity, spec B5).
   - **All option weights underflow (review minor).** A stay whose weights all underflow to 0 raises a named
     `ValueError` in Python and the same refusal in Java; real tariffs cannot reach it (the street always carries a finite
     price), and v2 had the same hole for distances alone.
   - **State of the calibration.** The decay lengths lambda and lambda_c of ADR-0140 decision 14 were fitted WITHOUT the price
     term, on the price-independent weights, so with beta > 0 the garage shares they reproduced differed. They were
     recalibrated with the price term (beta 0.310998, fixed during the calibration) on 2026-10-14 in Part B (B5), with every
     stay priced and weighted by the production code (feasibility of decision 1, price term of this decision), on the final
     option set; see the calibration record `parking_garage_decay_calibration_2026` for the values, the superseded v2 values,
     the sensitivities (large-lot thresholds 30 and 100, beta 0) and the limitations. The preparation warns when a run's
     coefficient differs from the one the committed lengths were calibrated with (`matsim.simulation.prepare`, the constant
     `PARKING_GARAGE_DECAY_CALIBRATED_BETA_U_PER_EUR`, pinned to the table by a test).
3. **Parking cost factor (spec A3, ASSUMPTION CF; owner item 2).** The config key `parking_cost_utility_factor` (Java module
   parameter `costFactor` of `braunschweigParking`) multiplies the parking cost of a car trip before it is added to the car
   cost that enters the utility. Default 1.0, finite, in [0, 10] (ruling R-A-15: the spec's "finite, >= 0" gives the lower
   bound; the upper bound 10 is that of the Java `ParkingConfigGroup` and of the Python validation in
   `matsim.simulation.prepare`). 1.0 is the model and reproduces v2 bit for bit: the preparation writes `costFactor` only
   when the factor differs from 1.0 (the Java default applies, the config is byte-identical to v2), and the value is recorded
   in the inputs report and the log. The key is inert when `parking_zones_enabled` is false (no module, no parameter; tested).
   ASSUMPTION CF: the factor is a SENSITIVITY, never a calibrated value: no local price-response evidence exists; the Part C
   arms 0.5 and 2.0 report the spread of the mode-share effect as a range. The expected cost of the outcome report is the
   price before the factor (`parking-target-comparison.md`).
4. **Outcome report v4 (spec A4; owner item 6).** The CSV `ITERS/it.N/N.parking_outcomes.csv` of the Java
   `ParkingOutcomeReportListener` gains six columns after the seven of v3: `garage_probability_sum_paid`,
   `garage_probability_sum_free`, `street_infeasible_count`, `no_feasible_option_count`, `expected_cost_cents_sum` and
   `ev_exempt_count`; written per iteration, outcome, zone and purpose as before. The paid probability is accumulated directly
   per option (never as a total minus the free part, which can be -1e-17 and abort a run: review fix). The reader
   `scripts/parking/compare_parking_targets.py` reads v4 and older reports (the version follows from the header; rows are keyed by
   outcome name) and computes from v4 on the paid-only garage share of the two `*_paid_calls` rows (commuter group and
   Wolfsburg): `sum(garage_probability_sum_paid) / (calls - sum(garage_probability_sum_free))` over the PAID_* calls, labelled
   as paid-only. This closes the limitation of ADR-0140 decision 14 for those two rows only (ruling R-A-18): the
   `all_calls` and `without_employer_free` rows keep free calls by definition and stay labelled orientation rows. The row is a
   POOLED ratio over the stays, not the per-stay mean of the calibration (`calibrate_garage_decay.py` averages per-stay shares);
   the two are equal only when no free option is in range of any stay of the universe. A universe whose calls all lie on
   free options has no paying user: the row is empty with that reason. The paid share of the report counts `NO_FEASIBLE_OPTION`
   as paid and `EV_EXEMPT` as free. The mean expected cost per pricing call by zone group and purpose, the EV share and the
   share of stays without a feasible street are reported from v4 on (descriptive).
5. **EV exemption (spec A5, ASSUMPTIONS E1 and E2; owner decision 2026-10-14, "2 ja, einfach fuer alle E-Autos").** A person
   who owns a battery-electric (BEV) or plug-in-hybrid (PHEV) car of the synthetic fleet is exempt from the street fee in a
   zone that states an exemption: street cost 0 and outcome `EV_EXEMPT` when the chargeable duration is at most the zone's
   `ev_free_minutes` AND at most its maximum stay, and the tariff snapshot date is not after `ev_rule_valid_until`; a longer
   stay pays the full schedule (the exemption is no deduction). ASSUMPTION E1 (owner): every BEV/PHEV counts as an E-plate
   holder; no share, no sensitivity beyond the flag `parking_ev_exemption_enabled` (default true, in the base configuration;
   false writes no attribute and reproduces the v2 prices). ASSUMPTION E2: the exemption never extends a stay beyond the
   maximum stay (ruling R-A-10: Salzgitter's ordinance gives 240 min against a 150-min maximum and is silent on the relation;
   the exemption is clipped to the maximum, and the validator names every zone whose `ev_free_minutes` exceeds `max_stay_min`).
   The campus rule and a resident zone do not take the exemption (the columns exist on street_paid rows only).
   - **Attribute.** `braunschweig.parking.attach.attach_ev_exemption` adds the person column `parking_ev_exempt`, written by the
     plans writer as the person attribute `parkingEvExempt`. The powertrain is a per-VEHICLE column of the vehicles frame
     (`synthesis.vehicles.vehicles`, household and in-commuter fleet), not of the vehicle types frame, which carries only the
     HBEFA technology (ruling R-A-13, spec text corrected). A person is exempt iff at least one vehicle it owns with
     `mode == "car"` has a powertrain in `ELECTRIC_POWERTRAINS` (mirrored in `attach.py` with an equality pin test, ruling
     R-A-14, because importing `fleet_sampling_de` would put its import closure into two stage tokens). Placeholder routing cars
     (`default_car`, no powertrain) are never exempt; vehicles are attributed to their `owner_id` only.
   - **Several cars: stated limitation.** The plans tie no leg to a vehicle, so the rule is "any owned car is a BEV/PHEV" and can
     over-assign the exemption to the owner of a mixed fleet; the household fleet gives the cars beyond the licensed adults
     round-robin to the licensed adults (and all cars of a household without one to its oldest adult), so such owners exist. The
     log counts them and states the share of fleet-car owners next to the headline rate: the headline rate is per car owner
     including the owners of the placeholder car only, so it understates the rate among the owners of a fleet car.
   - **Data.** The two zone columns `ev_free_minutes` and `ev_rule_valid_until` are defined here (Python validation, export,
     Java reader); Part B (B1) fills their values from the 2026-10-14 packages and does not redefine them (ruling R-A-3).
     The export writes `ev_free_minutes` as null for a rule whose validity date is before the snapshot date and logs the zone.
6. **A commuter product with a single-stay maximum is not a commuter product (spec A6).** `commuter_day_eur` (ASSUMPTION P2) may
   only come from a product without a single-stay maximum (the Braunschweig Ib 30-day ticket, the TU member month, garage
   monthly products). A zone with a maximum stay AND a commuter product must name the unlimited-stay product in its `notes` with
   the literal text `unlimited-stay product:` (`zones.COMMUTER_UNLIMITED_STAY_MARKER`), else the validator stops. The check
   applies to every zone type, not only street_paid (spec wording; an earlier brief said street_paid; pinned by a test with a
   campus row, whose maximum stay is additionally forbidden by its own rule); this is a deliberate behaviour change of the
   validator with no effect on valid data (a campus row cannot carry a maximum stay, a resident row no commuter product). The
   annual tickets of Gifhorn, Bad Harzburg and
   Braunlage keep their 2 h or local maximum and are not entered (spec A6).
7. **Cross-language contract and run checks.** Tariff model schema 5 adds `option_price_beta_u_per_eur` (document),
   `max_stay_min` (every garage entry; null until the garage dataset carries it, Part B) and `ev_free_minutes` (every zone
   entry); the Java reader accepts schemas 1 to 5 with exact key sets per version, so a model of schema 4 or older prices as
   before. The golden file is schema 6 (the v2 cases are unchanged prefixes) and adds the families F (feasibility), P (price
   weights) and X (EV), plus the release band shapes carried from the v2 final review (increment to total, and so on; rulings
   R-FW-1 and R-FW-2: the register also lists a garage-dataset assumption only when a priced garage row names it, never the
   withdrawn P12), with per-case `beta`, `ev_exempt` and decay lengths. The LF SHA-256 of the two shared fixtures is pinned in
   BOTH repositories:
   `tests/fixtures/parking/parking_golden_cases.json` ac635f3b64a56fb7dd60e2abbb457a7efcdf14003efc02739679ed7b95bea080 and
   `tests/fixtures/parking/parking_tariffs_fixture.json` f51c7341a769c84c25d6dffd11c8ebe3ae2462c9b696eed56773f0ca5967a7cd.
   `matsim.simulation.run` requires, besides the parking package and `GarageOptionModel`, the class
   `org/eqasim/braunschweig/parking/ParkingOptionPriceBetaCheck.class` (`braunschweig.parking.runtime_checks`): the marker of a
   jar that implements the v3 mechanics, so a v2 jar, which would refuse the schema-5 model by its exact key sets or ignore
   the cost factor, is named before the run.

## Rejected alternatives

- **Keep assumption M1 for a zone without a long-stay product** (borrow Ib's 9.00 EUR ticket for Ia). The product does not exist
  there; the owner decided on 2026-10-14 that a stay above the maximum has no street option (decision 1).
- **Raise when no option is feasible** (the v2 behaviour of the cost function for a maximum stay without a product). A run
  cannot stop at a pricing call; the stay is priced at the street's metered cents and counted as `NO_FEASIBLE_OPTION`, so the
  incompleteness of the option set is reported and the cost stays finite.
- **Calibrate beta on the SrV garage share.** One share cannot identify two parameters (lambda and beta); beta is fixed to
  the mode-choice coefficient and lambda is calibrated (spec A2).
- **A separate parking price coefficient from the literature.** No local evidence; the mode-choice coefficient is the only
  committed money coefficient (spec A2).
- **The income and distance elasticity of the person's utility in beta.** That term is a property of the whole trip utility, not
  of the comparison between two car parks (ASSUMPTION G1-d).
- **Allow beta 0 in a schema-5 run model as a free sensitivity.** It would let the coefficient of the garage choice drift from
  the mode choice; a price-free comparison is a schema-4 model (decision 2).
- **Scope the commuter-product check to street_paid zones only** (the wording of an earlier brief): it would leave a
  gap if the per-type bans of the two columns were loosened; the spec says "a zone" (decision 6).
- **Enter the annual tickets of Gifhorn, Bad Harzburg and Braunlage as commuter products.** They keep a 2 h or local single-stay
  maximum, so they are no commuter product; spec A6 rejects it.
- **Ship no jar marker.** A v2 jar would then fail late, on the exact key sets of the schema-5 model, instead of before the run
  (decision 7).
- **Calibrate the cost factor.** No local price-response evidence exists; a factor in [0, 10] is a sensitivity (ASSUMPTION CF).
- **An E-plate share or an EV sensitivity arm.** The owner decided "einfach fuer alle E-Autos": every BEV/PHEV is an E-plate
  holder (E1); the only switch is the flag.
- **Read the powertrain from the vehicle types frame.** It has no such column (only the HBEFA technology): the spec text said so
  by mistake and was corrected (ruling R-A-13).
- **Import `ELECTRIC_POWERTRAINS` from `fleet_sampling_de`.** It would add that module's import closure to two stage tokens;
  the constant is mirrored with an equality test (ruling R-A-14).
- **Apply the exemption beyond the maximum stay** (E2) or as a deduction from a longer stay: the ordinance does not say so, and
  the spec rule is "exempt up to the limit, full schedule beyond".
- **Make the `all_calls` and `without_employer_free` rows exact** in report v4: they keep the free calls by definition and
  would duplicate the paid-only row (ruling R-A-18).

## Consequences

- Scientific results change when the zones are on. Against ADR-0140 the prices change through (1) the feasibility rule
  (stays above a maximum stay leave the street; the rule acts wherever Part B's data states a maximum stay: the spec names
  Braunschweig Ia, Salzgitter, Helmstedt, Bad Harzburg and Seesen, and a stay in Ia then no longer buys Ib's ticket), (2) the
  price term (cheaper options carry more weight; the effect depends on lambda, which was refitted in Part B, B5), (3) the EV
  exemption wherever a zone states one (which zones state one today: the validation note of the feature record
  `parking_cost_zones`), and (4) the cost
  factor, which at 1.0 changes nothing. How much the mode shares move is not established: no v3 run exists, so no direction
  or size is stated here.
- The garage decay lengths of the configuration (ADR-0140 decision 14) were fitted without the price term; they were
  recalibrated with the price term (beta 0.310998) on 2026-10-14, see the calibration record
  `parking_garage_decay_calibration_2026`. A calibration is no validation, and no v3 run exists.
- The jar must be a v3 build: a jar without `ParkingOptionPriceBetaCheck` is refused before the run, and a v2 jar would refuse the
  schema-5 tariff model by its exact key sets. The Java package of the same branch must be in the jar.
- The report v4 is read by v3 readers unchanged in its first seven columns; the compare script reads v1 to v4.
- The Python `street_feasible` of the reference reports the documented meaning (can the street serve the stay) on the decay-0
  path as well, so `street_infeasible_count` agrees across languages (ruling R-A-17, decisions 2 and 4). The golden file
  does not pin it.
- Limitations to state with every v3 result, additional to those of ADR-0140: the EV exemption is a rule for every BEV/PHEV
  owner (E1) and over-assigns it to owners of a mixed fleet; the marked-machine condition of Goslar and the charging-only
  regime of Wolfenbuettel are not modelled; the cost factor and beta are not estimated from any local price response; the
  price term uses the mode-choice coefficient without its income term; `NO_FEASIBLE_OPTION` stays are priced at the street's
  metered price although the street is not permitted there; option weights still ignore capacity; the paid-only rows of the
  comparison are pooled ratios, not the calibration's per-stay mean.
- v3 is unvalidated. A future run records its comparison with the SrV references in a run manifest; convergence of a run (the
  mode shares stop changing) is not validation, and the golden and unit tests are consistency and regression checks.
- Follow-ups, not part of this decision: Part B (data) and Part C (runs); a pulp occupancy series is the issue
  TUBS-IVS/eqasim-bs#448 (owner decision at spec review).

## Evidence

- Python commits of Part A on `feature/i436-v3-a-mechanics` (feasibility `c3e400b3`, `437552a8`, `123c91cc`; the comparison
  classification `abcc0229`; price weights `8d2e0001`; EV rule `c4ab1ee6`, `72b8ea98`; EV attribute `d0dad070`; cost factor and the
  commuter rule `b4084df3`; weight-sum guard `87e71507`; schema 5, golden schema 6 `49294b21`, `2f56ce2d`; report v4 `a1d8854a`,
  `bf129ce4`, `5d355f83`, `a463da1d`; the deferred minors `20c7d6e7`; the comment fix on beta 0 `b45922ec`). `20c7d6e7`,
  `b45922ec` and the later fix round of the records review change comments or code in hashed stage sources (`prepare.py`,
  `zones.py`, `attach.py`), so the stages that import them re-hash; that is expected for v3.
- Java commits of eqasim-java-bs, branch `feature/i436-v3-a-mechanics`, from `24b5fea42` to
  `ea74c1f26`: `24b5fea42` (schema 5 reader), `6c60ebedf` (feasibility, price weights, EV exemption), `381e3331e` (cost factor),
  `cf6c4989b` (report v4, EV flag in the car cost model, the beta check), `ea74c1f26` (direct paid accumulation, strict golden
  reads).
- Consistency of the two implementations: the Java port reproduces all cases of the shared golden file (151 golden cases and 94
  option cases in its review, per-case beta, EV flag and decay lengths) and the fixture hashes above are verified in both
  repositories. These are consistency and regression checks, not a validation.
- Records: feature record `parking_cost_zones`; stage records `matsim.simulation.prepare` and `matsim.scenario.population`; data
  record `parking_tariffs_2026` (columns); contributor notes `docs/codebase/notes/parking-garage-options.md`,
  `parking-ev-exemption.md`, `parking-cost-zones.md` and `parking-target-comparison.md`; ADR-0140.
- Design: the v3 design spec of 2026-10-14 (local, gitignored under `docs/superpowers/specs/`); the owner decisions quoted in
  this record are those recorded there. Results: none yet; the run manifest of Part C (to come) will be linked from the feature
  record.

## Ruling ids cited in the code and records

The ids are historical labels of a local working ledger; the sentences state the rule. R-A-3: the EV zone columns belong to
Part A. R-A-6: the validator relaxation for a street zone with a maximum stay and no product. R-A-10: the EV exemption is
clipped to the maximum stay (E2). R-A-13: the powertrain is a per-vehicle column. R-A-14: the mirrored constant with an
equality test. R-A-15: `costFactor` is written only when it differs from 1.0, range [0, 10]. R-A-17: beta 0 is refused in a
schema-5 run model, and `street_feasible` follows its documented meaning. R-A-18: only the two `*_paid_calls` rows become
exact in report v4. R-ADR: the record is ADR-0142.
