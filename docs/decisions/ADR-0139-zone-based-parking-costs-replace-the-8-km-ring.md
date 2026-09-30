# ADR-0139 · 2026-09-30 · Zone-based parking costs replace the 8 km ring

- **Status:** accepted pending owner review at the PR; production flag ON in the reviewed branch, the 25 % legacy-vs-zones A/B is pending
- **Numbering:** ADR-0139 is the next free id. Checked on 2026-09-29 and again on 2026-09-30 across all local and
  remote branches, the worktrees and the open pull requests: `origin/main` holds up to ADR-0136; ADR-0135,
  ADR-0137 and ADR-0138 exist only on the unmerged branches `fix/parallel-memory-robustness`,
  `feature/employment-grid-kreis-age-shape` and `chore/narrow-mid-trip-table` and stay reserved for them.
- **Issue:** #436 (the SrV parking inputs: #249)
- **Supersedes:** ADR-0039

## Context

The legacy parking cost (ADR-0039, flag `enable_urban_parking`) is a placeholder inherited from the Bavaria/IDF
code paths:

- The plans writer (`matsim.scenario.population.add_person`) writes the person attribute `isParis`, true for the
  residents of the city of Braunschweig (`is_urban_resident`, AGS 03101), and the activity attribute `isParis`,
  true for every activity within `_URBAN_RADIUS_M` = 8,000 m of Braunschweig Hbf (EPSG:25832), a disk of 201 km².
- The Java car cost model (`org.eqasim.braunschweig.mode_choice.costs.BraunschweigCarCostModel`,
  `calculateParkingCost_EUR`) charges a non-resident whose car trip ends at an `isParis` activity
  `BraunschweigCostParameters.parisParkingCost_EUR_h` = 3.0 EUR per started hour of the activity stay, at least
  one hour, whatever the purpose and the time of day; a trip to the plan's last activity is priced as an 8-hour
  stay (24 EUR). Residents of the city pay nothing anywhere. Neither the rate, the radius nor the 8-hour rule has
  a source; ADR-0039 records the ring as a realism switch.
- The parking cost is not a reporting quantity. eqasim's discrete mode choice evaluates the car cost model for
  every car alternative in every replanning iteration, and the cost enters the car utility through the existing
  income- and distance-elastic monetary coefficient. It is therefore a boundary condition of the mode choice and
  of its calibration (#23). The owner asked (2026-09-28) for parking prices that are roughly right as such a
  boundary condition, sourced and simple rather than a behavioural parking model.

## Decision

1. **Zones (D1).** A parking zone is a polygon with exactly one tariff row of type `street_paid` (on-street parking
   or municipal lots with one regime), `resident_zone` (resident parking, ticket or disc parking for everyone else)
   or `campus` (TU Braunschweig member and guest day products). Zones do not overlap (pairwise intersection at
   most 1 m²) and each carries the SrV `workplace_class` of its work and education destinations. The first release
   is the committed trio `parking_zones_2026`, `parking_tariffs_2026` and `parking_coverage_register_2026`; its
   zone inventory, sources, licences and limitations live in these data records. Tariffs are those published in
   September 2026, applied to the 2023-based population (the convention of the 2026 VRB prices, ADR-0133).
   `braunschweig.parking.zones_stage` loads and cross-validates the release as one unit and raises on any
   inconsistency.
2. **Cost of one stay (D2).** Deterministic, in integer euro cents and integer seconds
   (`braunschweig.parking.cost.parking_cost_cents`, ported to Java as `ParkingCostCalculator`). The chargeable
   duration is the overlap of the stay with the zone's daily fee window, repeated every 86,400 s. The first rule
   that applies decides: a home activity pays 0 (H1); an activity with `parkingFree` pays 0; a resident of a
   resident-exempt zone pays 0 there (R1); a stay without a chargeable second pays 0; a campus zone charges the
   member day product for work and education and the guest day product otherwise (C1); chargeable minutes within
   the free threshold pay 0; above the maximum stay the long-stay product is due (M1); otherwise the first period
   is charged once and the rest in started billing units at the hourly rate, rounded half up to the cent and capped
   by the day cap. A trip to the plan's last activity pays until the fee window of the arrival day ends (T1).
   Every priced stay lasts at least the minimum parked duration of decision 9 (L1), applied before these rules.
   Outside every zone parking is free (Z1); an unknown zone id raises.
3. **Free parking at work and education (D3).** `parkingFree` is drawn once per person for the person's work and
   education activities in `street_paid` and `resident_zone` zones, with P(free | workplace class) =
   `share_free_total` of `srv2023_commute_parking_by_workplace_class` (A1), from
   `RandomState(random_seed + 7371)` over the persons sorted by `person_id`
   (`braunschweig.parking.attach.draw_parking_free`). `parking_workplace_free_share_shift` shifts every class share
   for the sensitivity arm of A1. Campus zones are never drawn: members pay the day product (C1).
4. **Residents (D4).** `residentParkingZone` is the resident zone that contains the person's home (R1); the
   exemption applies in that zone only.
5. **Coupling (D5).** `org.eqasim.braunschweig.parking.ZoneParkingCarCostModel` returns the driving cost plus the
   parking cost behind the existing `@Named("car") CostModel` seam: no logsum, no second cost coefficient, no
   search, no cache, no plan mutation; car passengers pay nothing, as before. The preparation stage writes the MATSim
   module `braunschweigParking` (`enabled`, `tariffsPath`, `terminalStayRule`, `minimumStayMinutes`) next to the
   tariff model JSON, and `BraunschweigConfigurator.updateConfig` switches the car cost model name only when the
   module is enabled, so an absent module keeps the legacy names (the `vrbFare` pattern of ADR-0133).
6. **Configuration (D6).** Two explicit flags in `configs/base_bs.yml`, mutually exclusive:

   | `parking_zones_enabled` | `enable_urban_parking` | Behaviour |
   |---|---|---|
   | false | false | OFF: no parking attributes, driving cost only |
   | false | true | LEGACY: the ring, byte-identical to the plans before this decision |
   | true | false | ZONES: this decision, the canonical configuration of the reviewed branch |
   | true | true | configure-time error: ring fees and zone tariffs must never be combined |

   The eight parameters `parking_zones_path`, `parking_tariffs_path`, `parking_coverage_register_path`,
   `parking_workplace_shares_path`, `parking_tariff_snapshot_date`, `parking_workplace_free_share_shift`,
   `parking_terminal_stay_rule` and `parking_minimum_stay_min` are set explicitly in `configs/base_bs.yml`, which
   holds their values. The design had planned the flip to ZONES for after the 25 % A/B; it is part of the reviewed
   branch instead, and the owner decides it at PR review.
7. **Observability (D7).** The attach functions log their coverage as rates under `[parking]`, and zones present
   without a single activity inside raise (a broken join or CRS, not a population that never parks there). At
   controller start `ParkingPopulationCheck` fails the run for plans with unknown zone ids, legacy `isParis`
   attributes or mistyped parking attributes and logs the plans' zone coverage. During the run every pricing call is
   counted by outcome (`ITERS/it.N/N.parking_outcomes.csv` and one `[parking]` log line per iteration). Version 1
   reports the mix and has no failure threshold. `ParkingOutcomeReportListener` also logs the active minimum stay
   of decision 9 once at startup and, per iteration, how many priced stays in a zone it extended
   (`[parking] it N: minimum stay L min extended e of p priced stays (x %)`); like the outcome counts these are
   pricing calls of car alternatives, and a stay counts when it is shorter than L, zero-length or not, so this rate
   is a different quantity from the zero-length share of chosen car arrivals in decision 9. Because MATSim reads the
   module of a jar without the parking package as an untyped group, `matsim.simulation.run` refuses a run whose
   prepared config enables the module when the jar lacks `org.eqasim.braunschweig.parking` or when the last
   iteration wrote no outcome report (`braunschweig.parking.runtime_checks`).
8. **Assumptions (D8).** Z1 outside every zone parking is free; D1 the simulated day is an average weekday
   (Saturday and holiday windows are not modelled); T1 a terminal stay pays until the fee window of the arrival
   day ends; M1 the maximum stay compares the chargeable duration and a longer stay buys the long-stay product; A1
   the free share of current SrV car commuters applies to all workers and students of the class; C1 campus members
   pay the day product, passes are not modelled; R1 residence inside a resident zone equals permit possession; H1
   home activities are free everywhere; F1 fee windows without an ordinance or signage source are marked
   `assumption` in the tariff row; S1 each zone has one regime. Where each bites and its sensitivity:
   `braunschweig.parking.tariff_export.ASSUMPTIONS_REGISTER`, whose texts every tariff model JSON carries. L1, the
   minimum parked duration of decision 9, is a run parameter rather than a property of the tariffs: it travels in
   the `braunschweigParking` module and the parking inputs report, not in that register, and the tariff model JSON
   (schema 1) is unchanged by it, so a tariff file alone does not state the minimum stay it was priced with.
9. **Minimum parked duration (L1, owner decision 2026-09-30).** ASSUMPTION L1: every priced car stay lasts at
   least L minutes, L = `parking_minimum_stay_min` = 15 in `configs/base_bs.yml`. The priced interval is
   `[arrival_s, max(departure_s, arrival_s + 60 L))` (`braunschweig.parking.cost.minimum_stay_departure_s`, ported
   to Java as `ParkingCostCalculator.minimumStayDeparture_s`), computed before the rules of D2, so each of them
   prices the extended stay unchanged: fee window, free threshold, first period, maximum stay, day cap and the
   campus products; the home, employer-free and resident exemptions do not depend on the duration. A terminal stay
   gets the minimum after T1. L changes the price only, never the simulated timing, and L = 0 prices exactly as
   before this decision. The preparation writes L as the module parameter `minimumStayMinutes` in whole minutes
   and records it in `<prefix>parking_inputs_report.json`; `matsim.simulation.prepare` rejects at configure time
   anything but a whole number from 0 to 35,791,394 (the largest value whose seconds fit a Java int), and the Java
   side reads 0 when the parameter is absent, so a scenario prepared before L1 keeps its pricing. Rationale: the
   timing model stays exactly as in eqasim France (activity end times from the synthesis, eqasim's `GenerateConfig`
   sets `plans.tripDurationHandling = shiftActivityEndTimes`, discrete mode choice for 5 % of the agents per
   iteration and `KeepLastSelected` for the rest), so a car that arrives after the planned end of its activity has
   a zero-length stay, which the pricing before L1 left free (`OUTSIDE_FEE_HOURS`). Such stays are not rare, and
   they do not disappear when a run ends by the eqasim mode-share termination criterion (the mode shares
   stabilised, which is no validation): among the chosen car arrivals of the final iteration they are 11.9 % in the
   OFF arm of the local 1 % smoke (iteration 10), 6.1 % and 6.2 % in two local 25 % runs of April 2026 without
   parking costs and with the legacy ring (final iterations 91 and 94) and 10.3 % in the 100 % run of June 2026 (run
   manifest `100pct-2026-06-06`, final iteration 83); table and script:
   `docs/runs/artifacts/parking-zones-smoke-zgb-1pct-2026-09-30/zero_length_share_long_runs.csv` and
   `zero_length_share.py` in the same directory. In eqasim the MATSim score does not steer the mode choice, so
   nothing else penalises a car alternative that parks for free only because the car arrives late. The value
   15 min is the owner's choice, not an estimate: no committed source gives a minimum parked duration for the
   region. Sensitivity: the 25 % A/B adds the arms L = 0 (the pricing before L1) and L = 30 min.

## Rejected alternatives

- **A regional conditional-logit parking-choice model with a fixed-reference logsum (plan of 2026-09-24/28):**
  scientifically coherent but several times larger to build; its parameters (walking disutility, type constants,
  scale) are not identifiable from the SrV data, its home-parking part has almost no leverage under a zero home
  fee, and the goal here is a boundary condition, not a behavioural model. Kept as a blueprint for a later
  parking-policy question after #23.
- **Keeping the ring for non-commute activities:** a test placeholder is not a model; it charges every
  non-resident 24 EUR for an 8-hour stay anywhere in the 201 km² disk.
- **A regional default price outside the zones:** no source; Z1 with a coverage register is honest and cheap.
- **Tariff rule trees (a tariff DSL) with Python/Java parity:** ADR-0133 replaced a 15.6k-line fare engine that
  could not be activated; the flat tariff fields of the table cover every tariff of the first release.
- **An hourly tariff over the whole working day of paying commuters:** commuters who pay buy day products. At the
  Ia/Ib rate of 1.80 EUR per hour, 8 hours cost 14.40 EUR against the 9.00 EUR Ib day ticket of
  `parking_tariffs_2026`; the max-stay, long-stay and day-cap fields represent the products.
- **A universal commuter day rate (the earlier phase-A design):** not a regional observation; replaced by the zone
  tariff plus the SrV free-parking draw.
- **A MiD B1 home-parking model (earlier phase B):** under H1 it changes no mode-choice cost; it belongs to the
  vehicle-fleet work (#316) if ever needed.
- **Four-minute search and access penalties (earlier phase C):** transferred constants without local evidence; not
  before the ASC calibration (#23).
- **Tariffs as per-activity attributes:** a zone id plus one tariff table is leaner and diffable.
- **Removing the zero-length stays through the timing model instead of L1** (another `plans.tripDurationHandling` or
  activity duration interpretation): the owner decision of 2026-09-30 keeps the timing model of eqasim France, and a
  time interpretation that ends an activity before the car arrives makes `ZoneParkingCarCostModel` fail by design;
  L1 changes the price only.

## Consequences

- Scientific results change when the flag is on, which it is in the canonical configuration of the reviewed
  branch. The ring priced every destination of a non-resident within 8 km of the Hbf at 3 EUR per started hour
  and exempted every resident of the city everywhere; the zones price only the zoned areas, at their sourced
  tariffs, and nothing elsewhere (Z1), residents of the city included. By construction, car costs fall for
  non-residents at destinations inside the ring but outside the zones and rise for residents at paid destinations.
  How much the mode shares move is not established: the local 1 % smoke after 10 iterations (run manifest
  `parking-zones-smoke-zgb-1pct-2026-09-30`) is too small and too far from an equilibrium to read a direction or a
  size from, and the 25 % legacy-vs-zones A/B with frozen ASCs is pending.
- The minimum stay L1 (decision 9) changes results against the pricing before it: the stay of a late car arrival in
  a zone is priced for L minutes instead of none, so the car cost rises at such destinations unless those minutes
  lie outside the fee window or within a free threshold. The recorded 1 % smoke ran before L1 (its prices equal
  L = 0); the 25 % A/B runs L = 15 min with the sensitivity arms 0 and 30 min.
- Mode-choice parameters are not recalibrated by this decision; the calibration of #23 starts with the zones on.
- OFF and LEGACY stay reproducible: the plans writer's OFF and LEGACY output is pinned byte for byte against the
  writer before this feature, the prepared config is unchanged with the flag off, and the Java car cost model names
  are unchanged without the module.
- A scenario prepared with the zones on carries no `isParis` attributes. Do not switch `braunschweigParking.enabled`
  off on it (the legacy model would then price no parking at all); re-prepare with the flags of the wanted mode.
- Limitations to state with every result:
  - The resident permit districts A, B and C of Braunschweig overlap zones Ia and Ib but are not zoned as resident
    zones (only the resident concept zone 132 at the Stadthalle is), so a permit holder parking for a non-home
    activity inside Ia or Ib pays the street tariff. Which areas are zoned and which are not is recorded in the data
    record `parking_zones_2026` and the coverage register `parking_coverage_register_2026`.
  - ParkGO zone II is not zoned in v1 apart from the three Parkscheininseln, and neither is the part of zone Ia
    south of the city's overview map, so parking there is free (Z1). Zone II is the fee zone around the centre at
    1.00 EUR per hour (0.50 EUR per 30 min, the ParkGO amounts the Parkscheininsel tariff rows take over) and
    contains the Stadthalle quarter; it is the largest known gap in the Braunschweig street parking of the first
    release. Why neither was digitised (the ParkGO annex map could not be georeferenced precisely enough) is
    recorded in `parking_zones_2026`.
  - Several zone geometries are approximations, not sourced boundaries: the nine `centre_approximation` zones and
    the hulls of named streets or fee-tagged car parks that stand for the Salzgitter, Peine and Helmstedt zones.
    The list and the reason for each are in the limitations of the data record `parking_zones_2026`.
  - C1 charges campus members the day product and therefore overcharges holders of the monthly campus ticket; a
    commuter product is a lever of a second release.
  - A1 transfers the free share of an SrV workplace class to the work and education activities inside the paid
    zones of that class, although the class share also counts car commuters whose workplace lies outside every
    zone; it therefore likely overstates free parking inside the paid zones. The sensitivity arm
    `parking_workplace_free_share_shift` covers both directions.
  - The parked duration is approximated by the activity stay (from the car's arrival to the end of the activity),
    and a plan's first activity (no incoming car trip) is never priced; both are inherited from the legacy car cost
    model. A car that arrives after the planned end of its activity therefore has a zero-length stay, which pays for
    the minimum stay L of decision 9 instead of its actual duration; L is an assumption without a regional source.
    In the final iteration of the ZONES arm of the local 1 % smoke, which ran before L1 (its prices equal L = 0),
    11.8 % of the chosen car arrivals were such stays (run manifest `parking-zones-smoke-zgb-1pct-2026-09-30`).
  - Under T1 a car whose terminal stay begins after the fee window of its arrival day has ended counts as
    `OUTSIDE_FEE_HOURS` and pays nothing.
  - Cross-cordon in-commuters carry the parking attributes but keep their fixed modes
    (`braunschweig.matsim.simulation.cordon_subpopulation.add_incommuter_fixed_mode_strategy`), so the zones cannot
    change their mode.
  - The optional standalone mode choice inside `matsim.simulation.prepare` (`mode_choice: true`) runs before the
    `braunschweigParking` module is written and does not see it, like the `vrbFare` module (ADR-0133); `mode_choice`
    is off in every committed configuration.
  - The Java outcome counts are pricing calls, one per car alternative that mode choice evaluated, chosen or not;
    they are not counts of chosen trips or of persons.
  - The OSM-derived zone polygons are licensed under the ODbL 1.0 (share-alike on a public repository, attribution
    "(c) OpenStreetMap contributors"); the licence details are in `parking_zones_2026`.
- Follow-ups, not part of this decision: rule-based zone geometry instead of hand digitising, garage and commuter
  products, an SrV comparison loop for the zone scope, and a parking-search-time arm.

## Evidence

- Feature record `parking_cost_zones`; data records `parking_zones_2026`, `parking_tariffs_2026`,
  `parking_coverage_register_2026`, `srv2023_commute_parking_by_workplace_class` and
  `srv2023_city_center_parking`; contributor note `docs/codebase/notes/parking-cost-zones.md`; the Python tests
  listed in the feature record; the Java unit tests of eqasim-java-bs `org.eqasim.braunschweig.parking` (branch
  `feature/i249-parking-cost-zones`).
- Consistency of the two implementations: the Python reference and the Java calculator agree on the 38 shared
  golden cases (`tests/fixtures/parking/parking_golden_cases.json`, copied into the Java test resources) and, in
  two randomised differential tests of 60,000 cases each, with 0 mismatches and all nine calculator outcomes
  covered: one over the fixture tariffs during the implementation review, and one over the committed 24-zone
  release with seed 20260929, recorded in the run manifest `parking-zones-smoke-zgb-1pct-2026-09-30`
  (`python_java_differential.txt`). The differential scripts are committed under that run's artefact directory
  (`harness/gen_cases.py`, `harness/DiffCheck.java`). With the `braunschweigParking` module absent, the branch jar
  reproduced the jar of `origin/main` exactly on the June 1 % scenario (trips and scores byte-identical for OFF and
  LEGACY, legs identical as a row set), recorded in the same run manifest (`jar_parity_sha256.csv`). These are
  consistency and regression checks, not a validation.
- Comparison quantity for the A/B: the committed SrV 2023 table `srv2023_city_center_parking` reports that 70.8 %
  of the Braunschweig residents who drive to the city centre usually park in a garage or large lot and 83.3 % pay.
  In Braunschweig the first release prices the street zones and five BgA car parks, not the multi-storey garages,
  so garage products are the first lever of a second release. Comparing the modelled paid share of car arrivals in the centre zones with this table
  is a comparison with a universe caveat (SrV asks residents about their usual centre parking; the model counts all
  car arrivals), not a validation; the smoke's figures for it are in its run manifest.
- Recorded smoke: the local 1 % smoke of ZONES, OFF and LEGACY on the June 2026 scenario, 10 iterations (run
  manifest `parking-zones-smoke-zgb-1pct-2026-09-30`), shows the Java wiring working end to end and the jar parity
  above; its A/B is indicative only. A smoke, not a validation.
- Minimum parked duration (L1): the zero-length shares of decision 9 per travel mode, with the provenance of the
  four output directories in its header, are in
  `docs/runs/artifacts/parking-zones-smoke-zgb-1pct-2026-09-30/zero_length_share_long_runs.csv`, generated by
  `zero_length_share.py` in the same directory; descriptive measurements, not a validation. The Python helper and the
  Java calculator are pinned to the same eight cases L01 to L08 on the fixture tariffs, with L = 15 and with L = 0,
  which reproduces the pricing before L1 (`tests/test_parking_cost.py`; eqasim-java-bs `ParkingCostCalculatorTest`).
- Pending evidence: the 25 % legacy-vs-zones A/B on the server; its run manifest will be linked from the feature
  record.
