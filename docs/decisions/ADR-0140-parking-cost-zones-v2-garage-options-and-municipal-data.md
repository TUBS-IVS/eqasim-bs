# ADR-0140 · 2026-10-08 · Parking cost zones v2: product minimum, garage options, municipal zone data, resident districts, search time off

- **Status:** accepted pending owner review at the PR; production flag ON in the reviewed branch. No model run of v2 exists
  yet: the three-arm comparison with the SrV references and the calibration of the garage decay length (decision 7) are
  pending, so the garage options are OFF in the canonical configuration (`parking_garage_decay_m` 0) and v2 is unvalidated.
- **Numbering:** ADR-0140 is the next free id. Checked on 2026-10-08 across all local and remote branches and the open pull
  requests: `origin/main` holds up to ADR-0136; ADR-0135, ADR-0137 and ADR-0138 exist only on the unmerged branches
  `fix/parallel-memory-robustness`, `feature/employment-grid-kreis-age-shape` and `chore/narrow-mid-trip-table` and stay
  reserved for them; ADR-0139 exists on the parking branches; no branch or pull request holds ADR-0140.
- **Issue:** #436
- **Extends:** ADR-0139. **Supersedes in part:** ADR-0139 decision 3 (its sentence "Campus zones are never drawn"), the
  limitation of ADR-0139 that C1 overcharges campus members and its follow-up list (decision 2 and decision 8 below, and the
  section "Parts of ADR-0139 this record supersedes or closes").

## Context

ADR-0139 priced a car stay with one product per zone: the street tariff, the long-stay product above the maximum stay, the TU
campus day products. Its limitations named what a second release should fix: the resident permit districts were not zoned,
ParkGO zone II was unzoned, nine zone geometries were approximations, garage and commuter products were missing (in
Braunschweig most centre parkers use a garage or a large lot according to the SrV, which the first release did not price),
campus members were overcharged and the free-parking share of a Wolfsburg workplace was transferred from a population that
does not work in the zones. The v2 design (issue #436, design spec of 2026-09-29 with its amendments, local) proposed four
levers: zone geometry by rule instead of by hand, a second product family and a commuter product, a comparison loop with the
SrV quantities and a parking search-time term. While implementing it the owner supplied municipal data packages and decided
several points; this record fixes what was built, why, and what was tried and rejected. It states no result: v2 has not been
run (see Status).

## Decision

1. **Product minimum and commuter product (lever 2, ASSUMPTIONS P1 and P2).** A stay pays the cheapest product the driver
   can use (P1: drivers know the local products): the street product, a garage product and, for work and education only, a
   commuter product. The commuter product is the cheapest publicly purchasable monthly or 30-day product of the zone divided
   by 21 working days (P2, owner decision 2026-09-29: an effective daily cost for regulars, never a day tariff, rounded half
   up to the cent). Products restricted to a customer group the model cannot identify and permit offers limited to a small
   fixed number of places are recorded and not used; the TU member month ticket is the commuter product of the campus rows
   (members pay the cheaper of the member day product and the commuter product, which closes the ADR-0139 limitation C1).
   The early rules (no zone, home, `parkingFree`, resident, outside the fee window) and the campus rule keep their order in
   front of the minimum; every product prices the same minimum-stay interval of L1 (ADR-0139 decision 9). A stay above the
   street maximum stay loses the street product and takes the long-stay, garage or commuter product. The tariff table
   gained optional columns for this (the garage core all-or-none with an optional day cap, so no garage needs an invented
   cap; `commuter_day_eur`; `resident_permits_valid`); the new outcomes are `PAID_GARAGE`, `PAID_COMMUTER` and, for
   decision 6, `PAID_EXPECTED`. The tariff table and its values are in the data record `parking_tariffs_2026`; the
   production table carries no zone-level garage columns (decision 6, E8).
2. **Municipal zone data and the zone release v2 (Amendments C and D, owner decisions 2026-10-01 and 2026-10-07).** The zone
   geometry stays curated from municipal and sourced inputs; it is not derived by rule (see the rejected alternatives).
   Replaced or added against ADR-0139, each with its source in the data records `parking_zones_2026`,
   `parking_tariffs_2026` and `parking_coverage_register_2026`:
   - Braunschweig fee zones 1a and 1b of the city's maps replace the hand-digitised Ia and Ib polygons (the southern part of
     1a, reconstructed from the 2024 Amtsblatt annex, is flagged); the tariff rows stay; the BgA lots, the
     Parkscheininseln and the Stadthalle resident zone stay cut out of them. ParkGO zone II needs no area zone: the city's
     phone-parking map shows paid city parking outside the Okerumflut only at the three Parkscheininseln and at the BgA lot
     Willy-Brandt-Platz (D1).
   - BgA car parks (D1): Kannengiesserstrasse is removed (a pocket park since April 2026), the remaining lots take the geometry
     of the gazette annex maps, Willy-Brandt-Platz is added.
   - Wolfsburg: the three tariff zones of the city's phone-parking layer replace `wob_innenstadt`; a zone is the area within
     50 m of the street sections of its tariff (ASSUMPTION C-a: the access walk from the destination to a paid street
     section), the nearer section decides where two areas overlap (ties: the higher tariff). The billing unit of 30 min is
     taken from the city's superseded 2016 fee ordinance (ASSUMPTION C-b; the current ordinance link returned HTTP 404). Where
     a 2024 press report and the city's layer disagree on the zone 1 rate, the layer is used and the conflict is recorded in
     the tariff row. No daily cap and no maximum stay could be sourced for the street, so long street stays are priced
     uncapped; the Wolfsburg garage products compete with them only once the garage options act.
   - TU campus zones (D1 as revised, D6): a campus zone is the union of the campus grounds of the v1 outline (where the
     buildings are, hence where the activities lie) and the camera detection zones of the TU campus maps (the paid car
     parks, where the cars park); Bevenroder Strasse stays unzoned (no ticketing stated), Volkmaroder Strasse has no
     OSM outline and is the car park alone.
   - Single paid sites (D3): where a municipality has sourced paid parking but no zone map, each paid car park or street
     section is a zone of its own, the area within 50 m of it (C-a); a zone needs a sourced tariff, and a fee window or
     maximum stay without a source follows the labelled assumptions F1 and M1. Precedence: such a zone is cut out of an
     overlapping area zone. The coverage register states each municipality's status.
   - Fee windows with a source replace F1 at Helmstedt and in Goslar zone 1.
   - Licences: the TU maps, the Goslar service data and the Wolfsburg layer carry no verified open licence; the owner decided
     to use them. The sources and the licence status per source are recorded in the data records and the README, and the
     draft licence requests have not been sent.
3. **Resident districts (Amendment C3, owner decision 2026-10-01).** The resident parking districts of Braunschweig (A, B, C)
   and Goslar (A, B, C, F, G, H, J) are a layer of their own (`parking_resident_districts_2026`), independent of the fee
   zones, which they may overlap. The plans carry the district of every activity (`parkingDistrict`) and of every home
   (`residentParkingDistrict`). Rule R2 extends R1 (ASSUMPTION): a stay is free (`RESIDENT_FREE`) when the activity lies in
   the district of the person's home and the zone honours resident permits (ASSUMPTION R2-a: the tariff column
   `resident_permits_valid`, true by default on street and resident zones, never on a campus, false at the separately
   operated BgA car parks and at the Goslar car park at the ZOB, where no source says permits are valid); it acts after the
   home and employer-free rules and before the fee-window check, and a stay outside every fee zone stays free by Z1. The
   v1 resident zone (Stadthalle) is unchanged. Owner decision 2026-10-01: R2 stays on as implemented, without an off
   switch, although it touches only non-home stays inside the person's own district. The tariff model lists the district ids
   so that Java rejects an unknown district attribute.
4. **Free parking at work and education (Amendment D5 and D6).**
   - ASSUMPTION A1-b (owner decision 2026-10-07): for the persons of the workplace class of Wolfsburg (03103) the draw uses
     the SrV free share of the class `bs_zentrum` instead of the class's own share, which was measured on in-commuters only
     and is dominated by commuters to the Volkswagen plant, which has its own free parking and lies outside every paid zone,
     whereas the paid zones are a city centre with paid street parking like the Braunschweig centre. Salzgitter and the
     Landkreis towns keep their own SrV shares. The Volkswagen commuters stay free by Z1 whatever the draw: the plant lies
     outside every paid zone (a check of the local 1 % plans against the pinned OSM extract found no work activity on a
     Volkswagen-named site inside a paid Wolfsburg zone). Configuration `parking_free_share_proxy_classes`
     (`{"03103": "bs_zentrum"}` in `configs/base_bs.yml`); configuration overlays are deep-merged, so the empty mapping would
     leave the entry in place and the explicit null marker `{"03103": null}` is the "no proxy" sensitivity arm.
   - ASSUMPTION C2 (owner estimate, no source): a share of the persons who drive to a campus for work or education find a
     free place and pay nothing (outcome `EMPLOYER_FREE`, meaning `parkingFree`); the others pay the campus products; guests
     pay the guest product. The SrV cannot supply this share: it was surveyed in 2023, before the TU ticketing of the
     Parkordnung 2026. Configuration `parking_campus_free_share` (0.2; sensitivity arms 0.0 and 0.4); it is never shifted by
     `parking_workplace_free_share_shift`. The draw compares one uniform per person with the share of the zone kind of each
     activity, which keeps the draw of the earlier release unchanged at campus share 0.0 and keeps common random numbers across
     arms. This supersedes "Campus zones are never drawn" of ADR-0139.
5. **Parking search time stays OFF (Amendment D4, owner decision 2026-10-07).** The column `search_time_min` exists in the tariff
   model and is empty in every row; the Java car utility term `betaTravelTime_u_min * searchTimeFactor * search_time_min` has
   `searchTimeFactor` 0.0. The values 5 min and 2 min of the design (ASSUMPTION S2) are withdrawn and the sensitivity arm at
   factor 1.0 is dropped: no local measurement exists (the regional evidence found only an external benchmark of another
   city and a modelling assumption in a 2019 Braunschweig case study). A configured term that cannot act is a configuration
   error: the run fails at startup when the factor is above 0 and no zone carries a search time, or when
   `car.constantParkingSearchPenalty_min` is not 0 next to a factor above 0 (double counting); destinations of purpose home
   get no search time (H1, home parking is private and free). The Python preparation does not write the factor yet; it is a
   parameter of the MATSim module `braunschweigParking` in the Java reader.
6. **Off-street options by distance, priced as an expected cost (Amendment E, owner decision 2026-10-07 "gleich in v2").**
   - Garages and large lots are entities of their own, the dataset `parking_garages_2026` (points with the garage tariff
     forms of decision 8, source and date per row); the BgA car parks stay zones (no double role). A garage without a sourced
     tariff would be listed and not priced; the first release prices every listed garage by decision 8.
   - A car stay in a `street_paid` or `resident_zone` zone that passes the early rules has the options "street" (weight 1) and
     every priced garage within `D_max` = 1,000 m straight-line distance (ASSUMPTION G2); a garage has the weight
     exp(-d / lambda) (ASSUMPTION G1, a gravity-type choice by distance: the further away, the less likely). The price of the
     stay is the probability-weighted mean of the option costs, an expected cost with no random draw. The price does not enter
     the weights (one SrV share cannot identify a price coefficient); capacity and occupancy are not modelled. Each option
     costs its own cheapest product (P1 inside the option; the monthly product of a garage per working day, P2, for work and
     education only). Campus zones and every stay free by an early rule are unchanged.
   - E4: a stay whose street option costs 0 pays 0 (nobody pays a garage when the street is free). When the street product is
     unavailable (maximum stay exceeded without a long-stay or commuter product) the weights renormalise over the garages.
   - E8 reading: with garage options the zone-level garage family of schema 2 is superseded (it stays valid for fixture
     tariffs without garages, and the export warns about zone rows that still carry it while lambda is above 0); the zone
     commuter product stays a product of the street option. The renormalisation of E4 is implemented and pinned by golden
     cases but unreachable with the production tariffs: every production zone row with a maximum stay carries a long-stay
     product.
   - Tariff model schema 3 adds the priced garages, `garage_decay_m` and `garage_max_distance_m`; schema 1 and 2 files still load
     and price as before; `garage_decay_m` 0 (the value until the calibration exists) switches the options off; the
     export refuses a decay above 0 without a priced garage. Java computes the distances from the activity coordinates
     (no new plan attribute). The outcome report (CSV v3: `outcome,zone_id,purpose,count,share,garage_probability_sum,
     stays_with_garages_in_range`) shows the expected garage share per zone and purpose; its counts are pricing calls.
7. **Calibration of lambda (Amendment E5, ASSUMPTION G3).** One parameter, calibrated and not estimated: lambda is set so that
   the mean garage probability of the non-home, non-work, non-education stays at activities inside `bs_zone_ia` and
   `bs_zone_ib` (all modes, a destination universe, computed on the plans of the reference scenario without a MATSim run)
   equals the SrV share of garages and large lots among the Braunschweig residents who park on the street or in a garage when
   they drive to the city centre (`srv2023_city_center_parking`; the script reads the value from the table). The same lambda
   applies in every town (transfer assumption). Consequences for the role of that table: its garage share is now a
   calibration TARGET and no longer validates the model; its paid share and the commuter garage share of the class
   `bs_zentrum` (`srv2023_commute_parking_by_workplace_class`) stay independent comparison quantities, with the universe caveat
   that the SrV asks residents about their usual place whereas the model averages over destinations. The calibration is
   built (`scripts/parking/calibrate_garage_decay.py`) and tested on a synthetic fixture but has NOT been run: the local
   reference plans are no longer available (lost on 2026-10-07) and the plans of a server run are needed, so
   `parking_garage_decay_m` is 0 until the table `parking_garage_decay_calibration_2026.csv` exists (a test requires 0 until
   then and the table value afterwards). A calibration on the garages alone (`--facility-kinds garage`) is reported as a
   sensitivity number next to the release value on every kind of facility. The comparison script
   `scripts/parking/compare_parking_targets.py` (lever 3) compares the arms of a run with the SrV tables; it is a comparison,
   never a validation, and no knob is turned on a difference without a repeat arm.
8. **Garage tariff forms and evidence rules (Amendment E10 to E14, owner directions 2026-10-07).** A garage is priced only
   where its published structure maps exactly to one of three exclusive forms; there is no approximation:
   - the single window (fee window, first period, rate in started units, one day cap);
   - time-of-day tiers (E10, ASSUMPTION P6: units counted from the arrival, each started unit at the rate of the tier in force
     at its start; a first period tied to a clock window is charged once when the arrival lies inside the window and the tiers
     then run from its end);
   - duration bands (E11, ASSUMPTION P8: free, total and increment bands over the elapsed duration, an increment band adds to
     the price reached at its start, a total band sets the price).
   Further rules: P7 (one cap per garage, the day cap; a second published cap is not applied); P10 (a published free period is
   a grace period: a stay not longer than it costs 0, a longer stay is billed from the arrival; a tiered garage may carry
   exactly one closed free band for it, E14 amendment); P9 (a stay beyond a closed 24-hour schedule is priced per started
   24 h, counted and logged); P3 to P5 (a night tariff that is no per-unit rate is not modelled, a rate without stated
   rounding bills started units, a tariff without charging times charges 0 to 24 h); P11 (best available evidence: where no
   current operator tariff is published the garage is priced from the best secondary evidence for the same facility, newest
   and most detailed first, checked against the garages of the same town; source, missing date and missing operator
   confirmation are named per row, and where an official dated source and an undated directory differ the official source
   sets the value); P12 (municipal free default: a public car park of the Wolfsburg city layer outside every published
   municipal tariff area and without an operator tariff is free, because the municipal fee ordinance charges only inside its
   published tariff areas). Car parks inside a paid zone whose fee is the zone's municipal tariff are part of the zone's street
   product (no option); car parks reserved for a user group or free for customers only are no public option; a lot of
   long-term renters (Wolfenbuettel) and the DB BahnPark station car parks are no option. Owner overrides of
   recommendations in the supplied packages are named in the row notes: the owner directed to price every listed garage
   (the packages recommended leaving two Goslar garages unpriced and creating no 0 EUR rule for the unknown municipal points),
   and to integrate the Autostadt and Klinikum car parks although the sources contradict each other on access (named, not
   resolved). The data record `parking_garages_2026` holds the rules and the row counts per assumption.
9. **Cross-language contract.** The Python reference `braunschweig.parking.cost` and the Java `ParkingCostCalculator` agree
   through one shared golden file (`tests/fixtures/parking/parking_golden_cases.json`, schema_version 4, copied into the Java
   test resources): money in integer euro cents and times in integer seconds in the tariff model; the weights use `exp`
   (`StrictMath.exp` in Java, so the result does not depend on the platform); options are ordered street first and then the
   garages by ascending `garage_id`, the expectation is summed left to right in double precision and the distance is
   `sqrt(dx*dx + dy*dy)`; the price is rounded half up to the cent once, at the end; the golden generator asserts that every
   pinned expectation keeps a margin from a half cent so that no platform difference of `exp` can flip a rounding. Consumers
   of the outcome report key rows by outcome name, never by position: the Java enum starts with `NO_ZONE` while the Python
   `OUTCOMES` tuple starts with `HOME`.

## Rejected alternatives

- **Zone geometry by rule (lever 1).** Tried in three forms and not applied: (a) the design's erosion of the regulated-street
  buffer is empty in every real street grid and, with the corrected fill of enclosed blocks, still yields no zone in any audited
  town, because OSM maps most paid street parking as separate areas and most regulated street ways carry only bans;
  (b) the majority rule over the capacity-weighted parking supply within 250 m (Amendment B) failed its pre-registered check in
  Braunschweig with the default share threshold; the owner then set the threshold to 0.3 (a post hoc change, recorded as such),
  with which the Braunschweig check passed but the pre-registered holdout check at the other towns failed on pooled precision
  (the rule covers areas around large paid car parks that the references leave out; the references are not complete maps of
  paid parking, which the data record states); (c) the variants with street supply
  only and with payment evidence of ticket machines (information arms) failed the holdout check too. Nothing was applied.
  Measured values, bounds and the interpretations of the tags are in the data records `parking_zones_2026` and
  `parking_paid_share_2026`; the tooling and its QA tables stay as curation aid and evidence, and the classified supply cells
  are committed as an optional release file that no stage reads. The municipal data of decision 2 replaced the rule.
- **Withdrawn search-time values S2 (5 and 2 min):** transferred order-of-magnitude constants with no local measurement
  (decision 5), as ADR-0139 had already rejected four-minute search penalties.
- **A Monte Carlo choice of one garage per stay:** the car utility of an alternative would differ between evaluations of the
  same stay, which destabilises the discrete mode choice; the expected cost is deterministic and the choice weights cannot be
  identified from the SrV data anyway.
- **A deterministic catchment** (each paid zone split into nearest-garage cells, a garage product inside a radius) instead of
  distance weights: proposed as a simpler alternative and not chosen; the owner asked for a gravity-type choice calibrated on
  the SrV.
- **The zone-level garage family under schema 3** (garage columns per zone): superseded by the garage dataset and E8; a
  garage is a point with its own tariff, and a zone-wide family cannot express distance.
- **Approximating banded or tiered tariffs** (leaving them unpriced, or collapsing a time-of-day tariff into a day family):
  both were done first and withdrawn on the owner's direction that published information must be used; the three exact
  forms of decision 8 replaced them.
- **Leaving the unknown municipal car parks of the Wolfsburg layer unpriced, or giving them an invented price:** unpriced would
  drop a public option silently and an invented price is a made-up value; P12 states the default as a named assumption, with
  a row count, rather than hiding it.
- **Extracting the zone data of a payment app (EasyPark):** proprietary data, terms of use and database right, not
  reproducible; the legitimate route is the city or a data licence, which is an owner action.
- **Keeping the own class share of Wolfsburg for its paid zones:** that share was measured on in-commuters, dominated by the
  Volkswagen plant, and would make nearly every work stay in the centre free (A1-b).

## Consequences

- Scientific results change when the flag is on. Against ADR-0139 the prices change through the zone geometry (Wolfsburg
  zones, Braunschweig 1a/1b, TU campus unions, the single paid sites), the product minimum (cheaper long stays through
  commuter products), the resident districts, the Wolfsburg and campus free-parking shares and the BgA corrections. The
  garage options change prices only after lambda is calibrated: with `parking_garage_decay_m` 0, the state of this record,
  they are off. How much the mode shares move is not established: no v2 run exists, so no direction or size is stated here.
  The expected output change of each data update is stated once in its data record.
- v2 is unvalidated. A future run records its comparison with the SrV references in a run manifest (a comparison with a
  universe caveat; convergence of a run is not validation); the SrV garage share is a calibration target and counts as no
  validation of the garage options.
- The Java reader accepts the tariff model schemas 1, 2 and 3; the Python export writes schema 3 whenever the zones are on, so
  the Java package of the same branch must be in the jar before any run (the existing run checks of ADR-0139 enforce the
  package). The plans carry the new attributes `parkingDistrict` and `residentParkingDistrict`; the Java population check
  rejects a district id the tariff model does not list.
- Mode-choice parameters are not recalibrated; the calibration of #23 starts with the zones on.
- Limitations to state with every v2 result, additional to those of ADR-0139: the garage weights follow one lambda calibrated
  on the Braunschweig centre and transferred to every town (G3), with straight-line distances and without capacity or
  occupancy; many garage rows rest on a named assumption (P3 to P5, P9 to P12) and secondary evidence; the campus free share
  is an owner estimate; Wolfsburg street stays have neither a daily cap nor a maximum stay; the Braunschweig zone edges
  carry the working accuracy of the digitised city maps and several zone sources carry no verified open licence; the
  local (1 %) plans give the TU campus and the paid zones only a small exposure; and the outcome counts are pricing calls,
  not chosen trips (the paid share the comparison reports is an upper bound of the chosen-trip share).
- Parts of ADR-0139 this record supersedes or closes: decision 3, sentence "Campus zones are never drawn" (superseded by
  decision 4, C2); the limitation that C1 overcharges campus members and its commuter-product follow-up (closed by decision 1
  and the campus commuter product); the limitations that the resident districts are not zoned (decision 3) and that ParkGO
  zone II is unzoned (decision 2: it needs no area zone) and the Wolfsburg and Braunschweig geometries of decision 1 of
  ADR-0139 (decision 2); the follow-ups "rule-based zone geometry" (tried and rejected), "garage and commuter products"
  (decisions 1, 6 and 8) and "a parking-search-time arm" (decision 5, stays off). The first release as a trio of inputs
  (ADR-0139 decision 1) is now six inputs; the stage `braunschweig.parking.zones_stage` loads them as one unit.
- Follow-ups, not part of this decision: the server run with the lambda calibration and the three-arm comparison; the
  Java report of the chosen-trip paid share and of the mean parking cents per evaluated car candidate by zone (the
  comparison script cannot report either from the pricing-call counts); repeat arms or a MATSim noise band for parking
  deltas; official vector data of the city to replace the digitised zone maps.

## Evidence

- Feature record `parking_cost_zones`; stage records `braunschweig.parking.zones_stage`, `matsim.scenario.population` and
  `matsim.simulation.prepare`; data records `parking_zones_2026`, `parking_tariffs_2026`, `parking_coverage_register_2026`,
  `parking_resident_districts_2026`, `parking_garages_2026`, `parking_garage_decay_calibration_2026`,
  `parking_paid_share_2026`, `srv2023_commute_parking_by_workplace_class` and
  `srv2023_city_center_parking`; contributor notes `docs/codebase/notes/parking-cost-zones.md`,
  `parking-garage-options.md`, `parking-wolfsburg-lots.md` and `parking-target-comparison.md`; the Python tests listed in the
  feature record; the Java unit tests of eqasim-java-bs `org.eqasim.braunschweig.parking` (branch
  `feature/i436-parking-cost-zones-v2`, local at the time of writing).
- Consistency of the two implementations: the Python reference and the Java calculator agree on every case of the golden
  file (schema 4; the old families are unchanged prefixes of the new ones), and each Python and Java port of a rule
  was checked against hand-derived expectations. A differential run of Java against Python over 96,000 random stays in the
  review of the Java port found no mismatch; its harness is not committed. These are consistency and regression checks, not
  a validation.
- Design: the v2 design spec of 2026-09-29 with Amendments A to E (local, gitignored under `docs/superpowers/specs/`); the
  owner decisions quoted in this record are those recorded there.
- Pending evidence: the server run of the three arms (OFF, LEGACY, ZONES v2 with and without garages), the calibration table
  of lambda and the comparison table; their run manifest will be linked from the feature record. A smoke is not a validation.
