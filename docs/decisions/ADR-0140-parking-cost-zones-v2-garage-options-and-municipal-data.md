# ADR-0140 · 2026-10-08 · Parking cost zones v2: product minimum, garage options, municipal zone data, resident districts, search time off

- **Status:** accepted pending owner review at the PR; production flag ON in the reviewed branch. At the time of writing
  (2026-10-08) no model run of v2 exists; the garage decay length was calibrated on 2026-10-09 (decision 7, result)
  and the garage options are on; the current state is held by the feature record `parking_cost_zones`, not by this record.
- **Numbering:** ADR-0140 is the next free id. Checked on 2026-10-08 across all local and remote branches and the open pull
  requests: `origin/main` holds up to ADR-0136; ADR-0135, ADR-0137 and ADR-0138 exist only on the unmerged branches
  `fix/parallel-memory-robustness`, `feature/employment-grid-kreis-age-shape` and `chore/narrow-mid-trip-table` and stay
  reserved for them; ADR-0139 exists on the parking branches; no branch or pull request holds ADR-0140.
- **Issue:** #436
- **Extends:** ADR-0139. **Supersedes in part:** ADR-0139; the complete list of the superseded and closed parts is the
  Consequences bullet "Parts of ADR-0139 this record supersedes or closes".

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
   by 21 working days (P2, owner decision of the design, date not recorded: an effective daily cost for regulars, never a
   day tariff, rounded half up to the cent). Products restricted to a customer group the model cannot identify and permit
   offers limited to a small fixed number of places are recorded and not used; the TU member month ticket is the commuter
   product of the campus rows (members pay the cheaper of the member day product and the commuter product, which closes
   the ADR-0139 limitation C1).
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
3. **Resident districts (Amendment C3, owner decision, date not recorded).** The resident parking districts of Braunschweig
   (A, B, C) and Goslar (A, B, C, F, G, H, J) are a layer of their own (`parking_resident_districts_2026`), independent of the fee
   zones, which they may overlap. The plans carry the district of every activity (`parkingDistrict`) and of every home
   (`residentParkingDistrict`). Rule R2 extends R1 (ASSUMPTION): a stay is free (`RESIDENT_FREE`) when the activity lies in
   the district of the person's home and the zone honours resident permits (ASSUMPTION R2-a: the tariff column
   `resident_permits_valid`, true by default on street and resident zones, never on a campus, false at the separately
   operated BgA car parks and at the Goslar car park at the ZOB, where no source says permits are valid); it acts after the
   home and employer-free rules and before the fee-window check, and a stay outside every fee zone stays free by Z1. The
   v1 resident zone (Stadthalle) is unchanged. Owner decision (date not recorded): R2 stays on as implemented, without an off
   switch, although it touches only non-home stays inside the person's own district. The tariff model lists the district ids
   so that Java rejects an unknown district attribute.
4. **Free parking at work and education (Amendment D5 and D6).**
   - ASSUMPTION A1-b (owner decision 2026-10-07): for the persons of the workplace class of Wolfsburg (03103) the draw uses
     the SrV free share of the class `bs_zentrum` instead of the class's own share, which was measured on in-commuters only
     and is dominated by commuters to the Volkswagen plant, which has its own free parking and lies outside every paid zone,
     whereas the paid zones are a city centre with paid street parking like the Braunschweig centre. Salzgitter and the
     Landkreis towns keep their own SrV shares. The Volkswagen commuters stay free by Z1 whatever the draw: the plant lies
     outside every paid zone (the outcome report of a server run shows the work stays in the Wolfsburg zones). Configuration `parking_free_share_proxy_classes`
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
   get no search time (H1, home parking is private and free). The Python preparation has no configuration key for the factor
   (the lever is off by owner decision D4, and a key is added only with a measured search time); it is a parameter of the
   MATSim module `braunschweigParking` in the Java reader.
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
     and price as before; `garage_decay_m` 0 (the pre-calibration value and the sensitivity arm) switches the options off; the
     export refuses a decay above 0 without a priced garage. Java computes the distances from the activity coordinates
     (no new plan attribute). The outcome report (CSV v3: `outcome,zone_id,purpose,count,share,garage_probability_sum,
     stays_with_garages_in_range`) shows the expected garage share per zone and purpose; its counts are pricing calls.
7. **Calibration of lambda (Amendment E5, ASSUMPTION G3).** One parameter, calibrated and not estimated: lambda is set so that
   the mean garage probability of the non-home, non-work, non-education stays at activities inside `bs_zone_ia` and
   `bs_zone_ib` (all modes, a destination universe, computed on the plans of the reference scenario without a MATSim run)
   equals the SrV share of garages and large lots among the Braunschweig residents who park on the street or in a garage when
   they drive to the city centre (`srv2023_city_center_parking`; the script reads the value from the table). The same lambda
   applies in every town (transfer assumption). Consequences for the role of that table: its garage share is now a
   calibration TARGET and no longer validates the model; its paid share stays an independent comparison quantity, and so did
   the commuter garage share of the class `bs_zentrum` (`srv2023_commute_parking_by_workplace_class`) until decision 12
   (Amendment H, 2026-10-09), which makes that share the calibration TARGET of a second decay length for commuter stays and
   the Wolfsburg row `03103` the independent check (both in their paid-only form, decision 12); the universe caveat applies to both: the SrV asks residents about their
   usual place whereas the model averages over destinations.
   Pre-registered expectation for the commuter comparison (written before any v2 run; an expectation, not a target; the
   miss it predicted is what decision 12 answers):
   the model's commuter garage share for `bs_zentrum` exceeds the SrV 0.464, because the garage weights are purpose- and
   price-independent (G1) and no Braunschweig garage has a monthly product in the dataset, so a commuter's garage option is a
   day rate; lambda is NOT tuned to it. The baseline arm for work and education stays is `zones_v2_no_garages` (lambda 0).
   Limitation: E4 fires only for a stay that is free on the street as a whole, so mostly-evening stays still get the
   garage mixture.
   RESULT (calibration run 2026-10-09, issue #436 Task 5b; a calibration, no validation): on the plans of the 1 % reference
   run of 2026-10-08 (`parking_zones_enabled` true, garage options off, no MATSim run; plans SHA-256
   `a052270838a198c4450c992166133d371bc592e994e8047777555fdd1cfa02bc`, 12,567 persons) the script found lambda = 396.19 m
   (the committed table `parking_garage_decay_calibration_2026.csv`, which records every input hash, the search and the
   code state; `parking_garage_decay_m` of `configs/base_bs.yml` and the two popsim fixtures equals it, a test requires the
   equality). Universe: 340 destination activities of the 50,747 main activities, all with a priced garage within 1,000 m
   (340/340); target 0.736196, achieved mean 0.736279 after 12 halvings; the mean probability is 0.0055 at lambda 10 m and
   0.8911 at 5,000 m (the ends of the search). The universe is small (a 1 % sample), so lambda is a coarse estimate and its
   sampling uncertainty is not quantified. The pre-registered commuter comparison came out as the expected MISS: the model's
   commuter garage share for `bs_zentrum` is 0.7142 over 245 work and education activities against the SrV 0.4636 (a number
   and the expectation above, not a target; lambda was not tuned to it; this number belongs to the one-decay state and is
   superseded by decision 12, where the commuters get their own decay length). The option set of the release value is all 49
   priced options (36 garages, 13 surface lots, none of the latter within reach of the zones Ia and Ib; see below). The
   garage options are ON in the canonical configuration with this lambda; no v2 MATSim run exists yet and v2 stays
   unvalidated. Target and option set differ in kind: the target
   `garage_large_lot / (garage_large_lot + street)` contains large surface lots, while the Braunschweig option set holds 12
   garages and no surface lot (all 13 surface lots of the dataset are in Wolfsburg; the BgA lots are zones, R-E1), so lambda
   is pushed up to let the garages carry the large-lot share. A calibration on the garages alone (`--facility-kinds garage`)
   is therefore vacuous for the destination universe of the zones Ia and Ib (the same option set) and is NOT run as a
   sensitivity; the option stays in the script for datasets where surface lots lie within D_max of the universe.
   The comparison script
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
   long-term renters (Wolfenbuettel) are no option; the DB BahnPark station car parks are no option either, except the
   Wolfsburg Parkdeck Hauptbahnhof, a garage since decision 11 (the Braunschweig station car parks are zones). Owner overrides of
   recommendations in the supplied packages are named in the row notes: the owner directed to price every listed garage
   (the packages recommended leaving two Goslar garages unpriced and creating no 0 EUR rule for the unknown municipal points),
   and to integrate the Autostadt and Klinikum car parks although the sources of the Autostadt contradict each other on
   access and the sub-facility identity of the Klinikum is unconfirmed (named in the row notes, not resolved; the Klinikum
   rests on P11). The data record `parking_garages_2026` holds the rules and the row counts per assumption.
9. **Cross-language contract.** The Python reference `braunschweig.parking.cost` and the Java `ParkingCostCalculator` agree
   through one shared golden file (`tests/fixtures/parking/parking_golden_cases.json`, schema_version 5 since decision 12,
   copied into the Java test resources): money in integer euro cents and times in integer seconds in the tariff model; the weights use `exp`
   (`StrictMath.exp` in Java, so the result does not depend on the platform); options are ordered street first and then the
   garages by ascending `garage_id`, the expectation is summed left to right in double precision and the distance is
   `sqrt(dx*dx + dy*dy)`; the price is rounded half up to the cent once, at the end; the golden generator asserts that every
   pinned expectation keeps a margin from a half cent so that no platform difference of `exp` can flip a rounding. Consumers
   of the outcome report key rows by outcome name, never by position: the Java enum starts with `NO_ZONE` while the Python
   `OUTCOMES` tuple starts with `HOME`. The Java sources (eqasim-java-bs) cite the ruling ids R-4d-2, R-4d-4,
   R-4d-5, R-4e-3 and R-T3-a, which are defined in the last section of this record.
   The two fixtures the Java repository copies byte for byte are pinned by the SHA-256 of their LF blobs (a Windows checkout
   holds CRLF; hash the blob): `parking_golden_cases.json` `342bc4bcb5593e3601584ad2bfcc6c68db2b866867d97621a75200e41fae3b06`
   and `parking_tariffs_fixture.json` `9f7298bafe0d5f7feb6f2825ee07887e680134163a0ce63599d7fe18fd82f283` (the state of
   decision 12; before it, with one decay length for every purpose, they were `62cb9bc3c6bae7401c8130f54dee1349afaf6f39ec6f0db7c8371777e2e9d14b`
   and `c7d30141a04ef6c1652c5451d7e3e478bba463a73668ed1cb3bb4994d4472718`).
   `tests/test_parking_cost.py` pins the same two hashes, so a regenerated golden file without the Java copy update fails on
   the Python side too, and the next change of a fixture updates the test pin and this record together.

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
10. **Monthly products for commuters at garages (Amendment F, owner request 2026-10-08).** The final review of the branch
    found that work and education stays at a Braunschweig garage would pay the day rate, about three times today's cost,
    because no Braunschweig garage carried a monthly product. The owner supplied a research package
    (`Braunschweig_Monatstarife_2026-10-08.zip`) and approved a lookup of the public Contipark configurator; commuters pay
    the monthly product, which was already the pricing rule (P2: `min(metered or day price, monthly / 21)` for work and
    education), so the change is in the data and in one switch, not in the pricing code. Published products come first (F1):
    the cheapest publicly purchasable current product of a garage, read from the package and the capture and never typed
    (Steinstrasse Tarif A 100.00 EUR, Wallstrasse 114.95 EUR gross, i.e. 476 ct and 547 ct per working day; every other
    Braunschweig garage has a status row in the QA table: no price, sold out, price on request, billing period unconfirmed).
    ASSUMPTION P13 (F2) imputes a monthly product for a garage without a published one: the median of the published current
    garage monthly products of the SAME municipality (the dataset's garages and the recorded garages that are no option, e.g.
    Eves 80.00 and Fichtengrund 129.00 EUR), rounded half up to the cent, only with at least two such products, never for a
    surface lot, never from another municipality: Braunschweig 107.48 EUR (512 ct per working day, 10 garages), Wolfsburg 57.50
    EUR (274 ct, 5 garages; 60.00 EUR and 286 ct since decision 11 adds a fifth published product); the other
    municipalities impute nothing (counted and logged). The package's advice against
    imputing from other cities is respected by the same-municipality rule; the imputation itself is the owner's choice, an
    assumption of the model and not a finding. The config key `parking_garage_monthly_imputation` (default true) lets the export
    use the imputed products; false is the sensitivity arm `zones_v2_published_monthly_only` (published products only, so that
    only Steinstrasse and the Wallstrasse and the Wolfsburg garages with a product price a commuter below the day rate).
    Schema 3, the Java reader and both golden fixtures are unchanged: the model lists ASSUMPTION P13 in its register only when
    an imputed product is used. Expected effect, stated as an expectation and not as a result (no v2 run exists): the garage
    weights do not depend on price (E3), so the garage probability of a stay is unchanged by construction; commuter garage
    stays can change only through more or fewer car-commuter stays (the expected cost of a work or education stay with a
    garage option in range falls, which can raise the car's utility for commuters into the centre and so the number of their
    car stays); the size is not established, and
    the comparison with the SrV commuter quantities (`srv2023_commute_parking_by_workplace_class`, not a calibration target)
    belongs to the run manifest of the server arms, including the published-only arm. Limitations kept: the access window of
    Tarif A (Mo-Fr 06:30-21:00) is not modelled, 21 working days and the per-stay minimum stay named in P2, no capacity of
    monthly places (the sold-out garages Magni and Packhof, and the Eiermarkt, whose operator offers no monthly product at all
    (Contipark capture of 2026-10-08), still get the imputed product), the Fichtengrund offer has an unknown end date, and the Braunschweig P13 median mixes a Mo-Fr 06:30-21:00 product (Steinstrasse Tarif A, 100.00 EUR) with 24/7 products (Wallstrasse, Eves, Fichtengrund), so the imputed product prices a regular's access window that not every garage offers.

11. **Station car parks of DB BahnPark are public: zones in Braunschweig, a garage option in Wolfsburg (Amendment G, owner
    decision 2026-10-08).** The station car parks had been excluded as a "customer regime" (rulings R-4b-4 and R-4b-9); the
    owner decided that they are public (anyone may park, BahnCard and Pcard only give a discount) and declined a follow-up
    issue ("loes das mit den zonen und gut"), so both rulings are REVOKED for exactly four car parks:
    - **Braunschweig Hauptbahnhof P1 Nord, P2 Sued and P3 West are single paid-site zones** (D3, as the Goslar 1 EUR/h car parks
      and the BgA lots): `bs_hbf_p1_nord`, `bs_hbf_p2_sued`, `bs_hbf_p3_west`, each the area within 50 m of the OSM outline of the
      lot (ASSUMPTION C-a; OSM ways 25411279, 7874165, 236421384, Overpass response of 2026-10-08, ODbL 1.0), 29,619, 43,931 and
      21,623 m2 after the 0.5 m simplification and the cuts. P1 borders the BgA lot Willy-Brandt-Platz (the OSM lot 'Post', way
      26163572, which is not part of P1): its zone is cut against the BgA zone, which takes precedence (958 m2 removed; no zone
      of the release overlaps another). The tariff rows are READ, never typed, from the text of the Contipark location pages
      of the owner's package `Braunschweig_Monatstarife_2026-10-08.zip` (the line named by the specification: '1 Stunde' or
      '30 Minuten', '1 Tag', '1 Monat fuer Stellplatzmieter'), corroborated to the cent by the DB BahnPark sheet of 2026-05-29 and,
      for the monthly product, by the package's rules (the cheapest publicly purchasable one, rule D2): P1 2.50 EUR per started
      hour, day maximum 17.00 EUR, monthly product 120.00 EUR, so `commuter_day_eur` 5.71 (571 ct); P2 and P3 1.10 EUR per started
      30 min, day maximum 11.00 EUR, monthly product 74.00 EUR, so 3.52 (352 ct); fee window 0-24 h every day (read from the opening hours of an operator page, so fee_window_source is 'assumption' and the note names ASSUMPTION F1, ruling R-4g-2); resident permits
      not valid (ASSUMPTION R2-a); workplace class `bs_outer` as the BgA row at the forecourt (ASSUMPTION G-b, a reading of the
      location). ASSUMPTION G-a: the amount is billed per started unit and the published '1 Tag' amount is the daily maximum per
      stay (the pages state neither a rounding nor the day boundary).
    - **The Wolfsburg Parkdeck Hauptbahnhof P1 is a priced garage option** (`wob_hauptbahnhof`, inside `wob_tarifzone_1`; the
      regional curation reads the rules `WOB_HBF_P1_R01`, `WOB_HBF_P1_R02` and `WOB_HAUPTBAHNHOF_MONTHLY_1` by id): 1.70 EUR per
      started 60 min, the day tariff 9.00 EUR as the cap (its day definition is unspecified: read per stay), monthly product
      100.00 EUR (Dauerparken Mo-So 24 h), so 476 ct per working day; its capacity is not reported because the sources conflict
      (186 and 190 spaces). ASSUMPTION P13 for Wolfsburg is recomputed with this fifth published product: the median of 50.00,
      55.00, 60.00, 98.00 and 100.00 EUR is 60.00 EUR (286 ct per working day, was 57.50 EUR and 274 ct) at the five Wolfsburg
      garages without a published product.
    - **Named and NOT modelled:** the Kiss&Ride tariff of P1 (0.80 EUR for 15 min), its evening tariff (18:00 to 02:00, at most
      5.00 EUR on the first day), the BahnCard and Pcard discounts (also the Wolfsburg rule `WOB_HBF_P1_R03`, 7.00 EUR for a Pcard
      or digital BahnCard at the terminal), the 35 EUR product for public-transport customers at Wolfsburg (a restricted group),
      the weekly (55.00 EUR) and machine monthly (78.00 EUR) tickets of P2 and P3, the reserved-space products (160.00 and 130.00
      EUR) and the stated maximum parking durations. Free street parking west of the station stays unzoned (ASSUMPTION Z1).
    - **Records and mechanics.** The zones are added to the FINISHED zone release by `station_lots.py` (a step of the curation
      chain after `assemble_parking_zones.py`; it refuses to change any other zone and any package or Overpass file whose SHA-256
      differs from the pinned one); the garage row comes through the regional garage curation. The coverage register loses the
      excluded row of the Braunschweig station car parks (they are zones) and narrows the Wolfsburg row to the station car parks
      other than the deck. Schema 3, the Java reader and both golden fixtures are unchanged (zones and garages are data).
      Expected effect, an expectation and not a result (no v2 run exists): stays within 50 m of the three lots are now priced
      as paid parking at their rates (before: free by Z1 outside every zone), and commuters to the Hauptbahnhof pay the monthly
      share instead of the day rate; the size is not established. Limitations: the capacities of the sources differ (P1, P2, P3:
      OSM 148, 385, 72; Contipark 185, 379, 70; DB sheet 183, 380, 72) and no capacity enters the zones; the 50 m area is wider than the lots: they are 4.7, 4.6 and 9.2 times the area of the lots (P1, P2, P3); the OSM fee=no street-side parking west of the station lies 86 to 390 m outside the zones; the zones take in the station forecourt (P1) and private Siemens lots (P2, mostly free through the employer draw); the destination content is small (building potentials: P1 12 buildings, P2 4, P3 5); a bare outline without the 50 m area would leave the zones inert. The municipality containment check against the pipeline's VG250 polygons could not run (the cached polygons are not on the machine), so the review used a substitute: all three zones lie 100 % inside the OSM boundary of Braunschweig (relation 62531, read from the pinned Niedersachsen PBF), with a margin of 3.4 to 3.65 km to the boundary; assemble_parking_zones.check_municipality_containment stays the pipeline check and runs at the next full chain run. The deck point of the Wolfsburg garage lies inside wob_tarifzone_1 (checked).

12. **A separate decay length for commuter stays (Amendment H, owner decision 2026-10-09: "1 bitte umsetzen").** Reason: with the
    one decay length calibrated on the city-centre visitors (decision 7, lambda 396.19 m) the model's commuter garage share for
    `bs_zentrum` was 0.7142 against the SrV 0.4636 (a free-inclusive reference, see the revision below), the miss that decision
    7 had pre-registered. The SrV parking questions
    support exactly two groups: the usual place at work or education per workplace class (`V_*_PARKENAPL`, the table
    `srv2023_commute_parking_by_workplace_class`) and the usual place in the Braunschweig city centre
    (`V_BRAU_PARKENCITY`, `srv2023_city_center_parking`); they are person-level questions with no parking place per trip and no
    purpose split of the city-centre question, so a finer split of the weights by purpose is not supported by the data and is
    not made. The owner decided on two decay lengths, the commuter one calibrated on `bs_zentrum`, Wolfsburg as an independent check
    (both in the paid-only form after the revision below).
    - **Mechanism (ASSUMPTION G1-c).** The garage weights `exp(-d / decay)` use `garage_decay_commute_m` (lambda_c; config
      `parking_garage_decay_commute_m`) for a stay whose purpose is work or education (`cost.COMMUTER_PURPOSES`) and
      `garage_decay_m` (lambda) for every other purpose; one helper, `cost.garage_decay_m_for_purpose`, decides, and the commuter
      decay is never defaulted to lambda (required keyword in the pricing, `None` accepted by the export and the preparation only
      while lambda is 0). 0 switches the garage options off for the purposes the decay governs. Everything else of E2 to E4 is
      unchanged (street weight 1, D_max, expected cost, E4, renormalisation). The tariff model is schema 4 (schema 3 plus the
      top-level key `garage_decay_commute_m`; a schema-3 file has no commuter decay and every purpose uses `garage_decay_m`, as
      schema 3 always did: `tariff_export.garage_decays_from_model`, tested) and the golden file schema 5 (every case carries
      `garage_decay_commute_m`; every case before E33 carries the commuter decay equal to the decay, so all earlier cases are
      unchanged in content and price; E33 to E38 pin the two decays with a work, a shopping and an education stay at the same
      destination and the off switches). The Java reader and calculator follow in a separate task (Task 4i); until then the Python
      export writes a model the existing Java reader refuses (exact key sets).
    - **Calibration of lambda_c (H2, revised by ruling R-4h-1), the result of 2026-10-09 (a calibration, no validation).** The
      same script and the same plans as decision 7 (SHA-256 `a052270838a198c4450c992166133d371bc592e994e8047777555fdd1cfa02bc`,
      12,567 persons). Universe: the work and education main activities inside `bs_zone_ia` and `bs_zone_ib` that do NOT carry
      `parkingFree`: 54 of the 245 work and education activities there (191 carry `parkingFree`: the free-parking draw frees
      them, so the universe holds the payers only), all 54 with a priced garage within 1,000 m. Target: the PAID-only share
      `share_garage_large_lot_paid / (share_garage_large_lot_paid + share_street_paid)` of the row `bs_zentrum` = 0.0965 /
      (0.0965 + 0.0456) = 0.679099, READ by the script from the committed SrV table (which gained the per-place payment columns,
      see the revision bullet). Result: lambda_c = 359.64 m, achieved mean 0.678955 after 12 halvings (the mean is 0.0000 at
      10 m and 0.8861 at 5,000 m). lambda is unchanged (396.19 m, 340 activities, target 0.736196, achieved 0.736279). Both
      values are in the committed table `parking_garage_decay_calibration_2026.csv` (code state d459675e, clean tree); the
      config keys `parking_garage_decay_m` and `parking_garage_decay_commute_m` of `configs/base_bs.yml` and the two popsim
      fixtures equal its rows `decay_length_m` and `decay_commute_length_m` (a test requires both equalities). The city-centre
      target of lambda stays free-inclusive (`garage_large_lot / (garage_large_lot + street)`): its model universe, the
      destination universe of E5, applies no early rule, so it has no payers-only form and the question of ruling R-4h-1 does not
      arise there; that this asymmetry is acceptable is an assumption, not a finding.
    - **Revision of the target (ruling R-4h-1, review of Task 4h, 2026-10-09); the first H2 value is superseded.** The first
      version of H2 calibrated lambda_c on the free-inclusive share 0.1923 / (0.1923 + 0.2225) = 0.463597 and gave lambda_c =
      191.52 m (achieved 0.463645) and a Wolfsburg check of 0.348737 against 0.638740 (18 activities); that value was committed in
      the commit 160a4dc8 of the branch and is SUPERSEDED, not used by any config. Why it was wrong: the SrV denominator
      `share_garage_large_lot + share_street` contains the commuters who park FREE on the street or in a garage, whereas the
      model's universe (work and education activities without `parkingFree`) contains only payers, because the free-parking
      draw removes everyone who parks free (`share_free_total` of the class). Like-for-like is the paid-only share of the
      payment follow-up per place (`V_*_PARKENAPL<k>_ENTGELT`); a free-inclusive model quantity does not exist, so the
      free-inclusive reference was the wrong comparison, not a reference that a better model could meet. The commute table was
      re-extracted from the same raw files (identical SHA-256) and gained the eight per-place payment columns
      `share_<place>_<paid|free>`; its existing columns are unchanged in meaning and value (data record
      `srv2023_commute_parking_by_workplace_class`). The paid cells are small: bs_zentrum paid garage 29 and paid street 13
      respondents, 03103 22 and 5 (unweighted counts on the raw delivery), so the targets carry a wide sampling uncertainty that
      is not quantified; no bootstrap was computed.
    - **Targets and checks after this decision.** Calibration targets (no validation): the SrV garage share of the city centre,
      0.736196 (lambda), and the PAID-only SrV commuter garage share of `bs_zentrum`, 0.679099 (lambda_c); the garage shares of
      a run that the comparison script reports for these two universes are therefore not independent. Independent checks that
      remain, numbers and no validation: the paid share 0.8333 of `srv2023_city_center_parking` (computed on the outcomes of a
      run, not by the calibration), and the Wolfsburg commuter garage share (H3b) in its paid-only form: the row `03103` gives
      0.0225 / (0.0225 + 0.0080) = 0.737705 (READ from the table); the model value at lambda_c over the work and education
      activities without `parkingFree` inside `wob_tarifzone_1` to `wob_tarifzone_3` is 0.553517 over 18 activities (15 with a
      priced garage within 1,000 m), 0.184 below the reference. This check carries no pre-registered bound, so it is stated as
      a number and is neither a pass nor a fail; the universe is small (a 1 % sample), the SrV row covers the Kreis while the
      zones are three tariff areas, lambda_c is transferred from Braunschweig (the transfer assumption of G3 now also covers
      lambda_c), and the cause of the difference is not established. No decay is tuned to it. The free-parking draw of Wolfsburg
      uses the share of `bs_zentrum` as a proxy (A1-b), a fact that Wolfsburg is not independent of `bs_zentrum` in the draw, not
      a reason offered for the difference. In a run the like-for-like model quantity is the garage share among the PAID_*
      pricing calls of the work and education stays (rows `garage_share_*_work_education_paid_calls` of the comparison script);
      the rows that keep free calls in the denominator are shown for orientation only.
    - **Limits.** The commuter calibration rests on 54 activities of a 1 % sample and on paid cells of 29 and 13 respondents, so
      lambda_c is a coarse estimate and its sampling uncertainty is not quantified; the SrV share is about residents' usual
      place, the model averages over destinations (universe caveat); the effect on a run's commuter garage share is not
      established (no v2 run exists, and the comparison script labels the `bs_zentrum` rows as the target of lambda_c).

## Consequences

- Scientific results change when the flag is on. Against ADR-0139 the prices change through the zone geometry (Wolfsburg
  zones, Braunschweig 1a/1b, TU campus unions, the single paid sites), the product minimum (cheaper long stays through
  commuter products), the resident districts, the Wolfsburg and campus free-parking shares and the BgA corrections. The
  garage options change prices through the calibrated decay lengths (lambda 396.19 m, decision 7, and lambda_c 359.64 m for
  work and education stays, decision 12): with `parking_garage_decay_m` 396.19 and `parking_garage_decay_commute_m` 359.64,
  the state of this record, they are on. How much the mode shares move is not established: no v2 run exists,
  so no direction or size is stated here.
  The expected output change of each data update is stated once in its data record.
- v2 is unvalidated. A future run records its comparison with the SrV references in a run manifest (a comparison with a
  universe caveat; convergence of a run is not validation); the SrV garage share is a calibration target and counts as no
  validation of the garage options.
- The Java reader accepts the tariff model schemas 1, 2 and 3; the Python export writes schema 4 since decision 12 whenever
  the zones are on, which the existing Java reader refuses until Task 4i adds the key, so the Java package of the same branch
  must be in the jar before any run (the existing run checks of ADR-0139 enforce the
  package). The plans carry the new attributes `parkingDistrict` and `residentParkingDistrict`; the Java population check
  rejects a district id the tariff model does not list.
- Mode-choice parameters are not recalibrated; the calibration of #23 starts with the zones on.
- Owner decision 2026-10-08: the three geometry files (zone polygons, resident districts, garage dataset) rest on sources that
  are not cleared for redistribution, so they are no longer committed; they stay local like the other raw data and are
  available on request (GitHub issue in TUBS-IVS/eqasim-bs), their tests skip without them, and each data record states the
  SHA-256 to verify a copy.
- Limitations to state with every v2 result, additional to those of ADR-0139: the garage weights follow two decay lengths
  (lambda for every purpose but work and education, calibrated on the city-centre visitors; lambda_c for work and
  education, calibrated on the `bs_zentrum` commuters, G1-c) transferred to every town (G3), with straight-line distances and without capacity or
  occupancy; many garage rows rest on a named assumption (P3 to P5, P9 to P12) and secondary evidence; the campus free share
  is an owner estimate; Wolfsburg street stays have neither a daily cap nor a maximum stay; the Braunschweig zone edges
  carry the working accuracy of the digitised city maps and several zone sources carry no verified open licence; the
  local (1 %) plans give the TU campus and the paid zones only a small exposure; and the outcome counts are pricing calls,
  not chosen trips (the paid share the comparison reports is an upper bound of the chosen-trip share).
- Activity zone versus parking point: the zone of a stay is the zone of the activity coordinates (point in polygon), while
  the car parks at the end of its arrival link. One measurement of that agreement exists: on the 1 % plans of 2026-04-29
  (lost since; uncommitted and not reproducible from the repository) the median distance between the activity and the
  parking point was 34 m, 82 % of the zone-touching car arrivals lay in the same zone as their activity, and TU campus roads
  were missing in the network. It is a one-off measurement on lost plans, to be re-measured on the server plans; no other
  number from it is used anywhere.
- Parts of ADR-0139 this record supersedes or closes (the complete list): decision 3, sentence "Campus zones are never drawn"
  (superseded by decision 4, C2); the limitation that A1 likely overstates free parking inside the paid zones, for the Wolfsburg
  zones (superseded by decision 4, A1-b; it stands for every other class); the limitation that C1 overcharges campus members
  and its commuter-product follow-up (closed by decision 1 and the campus commuter product); the limitations that the
  resident districts are not zoned (decision 3) and that ParkGO zone II is unzoned (decision 2: it needs no area zone) and
  the Wolfsburg and Braunschweig geometries of decision 1 of ADR-0139 (decision 2); the follow-ups "rule-based zone
  geometry" (tried and rejected), "garage and commuter products" (decisions 1, 6 and 8) and "a parking-search-time arm"
  (decision 5, stays off); the follow-up "an SrV comparison loop for the zone scope" (closed in part: the comparison
  script is built, decision 7, and not yet run); the limitation that nine `centre_approximation` zones and street hulls are
  approximations (narrowed by decision 2: fewer approximated zones remain, see the data record `parking_zones_2026`; it
  stands for those). The first release as a trio of inputs (ADR-0139 decision 1) is now six inputs; the stage
  `braunschweig.parking.zones_stage` loads them as one unit.
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
  file (schema 5; the old families are unchanged prefixes of the new ones), and each Python and Java port of a rule
  was checked against hand-derived expectations. Randomised differential runs made during review are not committed and carry
  no weight here. These are consistency and regression checks, not a validation.
- Design: the v2 design spec of 2026-09-29 with Amendments A to G (local, gitignored under `docs/superpowers/specs/`); the
  owner decisions quoted in this record are those recorded there.
- Pending evidence: the server run of the three arms (OFF, LEGACY, ZONES v2 with and without garages), the calibration table
  of lambda and the comparison table; their run manifest will be linked from the feature record. A smoke is not a validation.

## Ruling ids cited in the code and records

Tracked code, tests, curation scripts, records and notes of the parking work cite controller ruling ids that were
defined in a local working ledger. This list defines every distinct id the search finds, one sentence each. Search used
on 2026-10-08, run in the eqasim-bs worktree:
`grep -rnoIE --exclude-dir=__pycache__ "\bR-[A-Za-z0-9]+(-[A-Za-z0-9]+)*\b" braunschweig scripts tests docs/registry
docs/codebase README.md`, keeping the parking ids (ids of the form R-<digit>..., R-T<digit>..., R-C1, R-D2-a, R-D3-a,
R-E1; the bare R-A, R-C, R-D, R-E, R-T of unrelated analysis reports and a test tag are no ruling ids). The same
search in the Java sources of eqasim-java-bs finds R-4d-2, R-4d-4, R-4d-5, R-4e-3 and R-T3-a.
The ids are historical labels: the sentences state the rule, which the code and the records apply.

- `R-4a-1`: Zone precedence: specific zones (BgA lots, single paid sites, Parkscheininseln, Stadthalle resident zone) are cut
  out of overlapping area zones, campus zones win over street zones, every cut is a QA row.
- `R-4a-2`: Single paid sites (D3) are the polygon, source point or street line plus 50 m; where two such areas overlap the
  nearer source decides, ties go to the higher tariff.
- `R-4a-3`: Tariff rows of the new single-site zones take their values from the preferred rules of the regional package and
  the ordinances; an unstated billing unit follows the labelled assumption D3-a (per started half hour for the three Goslar
  car parks).
- `R-4a-4`: A maximum stay needs a long-stay product in the loader; where a source gives a maximum stay but no long-stay
  product exists, the maximum stay stays empty and the note says that longer stays are priced metered.
- `R-4a-7`: Exposure check: the curation counts, per new or changed zone, the activities and car arrivals of a plans file
  with the production attach functions (an indicator, not a validation).
- `R-4a-8`: A TU campus zone is the union of the camera detection zones of the campus and the campus grounds of the v1
  outline (owner decision, variant C).
- `R-4g-2`: The fee window of the station rows, read from the opening hours of an operator page, is an assumption: `fee_window_source` is `assumption` and the note names ASSUMPTION F1 (the situation of P5 at the garages).
- `R-4b-1`: File form of the garage dataset: Point features in CRS84 on disk with the tariff fields, monthly price, reported
  capacity, provenance and a QA table.
- `R-4b-3`: A garage tariff is encoded only where its published structure maps exactly to the columns; nothing is
  approximated (extended by R-4b-10b and R-4b-11 for tiers and bands).
- `R-4b-4`: Large public car parks with a published tariff enter the dataset; BgA lots (they are zones) and customer-only
  regimes stay out and are recorded in the QA table. REVOKED for the DB BahnPark station car parks by decision 11 (Amendment G).
- `R-4b-8`: The package flag `preferred_for_current_use` is enforced for every value role of a garage row, without waivers; a
  rule that is not released never sets a value.
- `R-4b-9`: DB BahnPark station car parks are excluded in Braunschweig and Wolfsburg alike (QA candidates); shopping-centre
  garages stay garages. REVOKED by decision 11 (Amendment G) for the Braunschweig Hauptbahnhof car parks P1 to P3 (zones) and the
  Wolfsburg Parkdeck Hauptbahnhof P1 (garage); no other station car park is decided.
- `R-4b-10b`: Published time-of-day tiers are encoded exactly (column `tariff_tiers`, spec E10); they are not approximated by
  a day family.
- `R-4b-11`: Published duration schedules are encoded exactly as duration bands (column `tariff_duration_bands`, spec E11,
  ASSUMPTION P8).
- `R-4b-12`: A first period that its source ties to a clock window carries that window; it is charged once when the arrival
  lies inside it and the tiers then run from its end (P6 as amended).
- `R-4b2-0`: The garage curation reads the supplement and follow-up packages as optional inputs pinned by SHA-256 and byte-
  reproducible; a rule of such a package sets a value only where an owner decision releases it.
- `R-4b2-1`: Four garages of the supplement package enter the dataset with the entrance points of its entrance layer as
  positions (the entrance kind is named per row); the Helmstedt Groepern tariff is encoded from the brochure text with its
  quotation.
- `R-4b2-2`: The Forschungsflughafen garage is priced from the supplement (1.50 EUR per started 60 min, free period read as a
  grace period P10, day maximum 18 EUR); the owner decision of spec E12 on the GALERIA day maximum (15 EUR, 8 EUR recorded as
  a conflicting variant) is cited under the same id.
- `R-4b2-3`: The Suedkopf-Center garage is priced at 1.00 EUR per started 60 min with a 30 min grace period (P10) and no day
  maximum from the primary source.
- `R-4b2-4`: The GALERIA garage is priced at 1.50 EUR per started hour with the day maximum of R-4b2-2 and its monthly product.
- `R-4b2-5`: The Poststrasse garage replaces its night approximation (P3) by time-of-day tiers (night 21:00 to 06:30 at 1.00
  EUR per 60 min).
- `R-4b2-6`: Earlier plan to leave Achtermann and Charley-Jacob-Strasse unpriced; superseded by R-4b2-8.
- `R-4b2-8`: ASSUMPTION P11: every listed garage is priced, from the best available secondary evidence where no operator
  tariff is published (owner direction), replacing R-4b2-6.
- `R-4b2-9`: Where an official dated source and an undated directory differ, the official source sets the value and the
  directory is recorded as a conflicting variant (it may supply a product the official source does not state).
- `R-4b3`: Family of the rulings R-4b3-0 to R-4b3-4 on the Wolfsburg car parks (spec E14); cited without a suffix where the
  whole family applies.
- `R-4b3-0`: The garage dataset gains the column `facility_kind` (garage or surface_lot); the Wolfsburg lot package is a
  fourth optional curation input and every point gets one QA row.
- `R-4b3-1`: Classification of each Wolfsburg car park, decided from the package and the zone geometry, never from names:
  inside a paid zone (zone street product), own operator tariff (option), free for the public (option at cost 0), municipal
  free default P12, user-group or customer-only (no option).
- `R-4b3-3`: The committed Wolfsburg rows must load, validate, export (schema 3) and price through the garage-option code
  without special cases.
- `R-4b3-4`: Records and counts of the Wolfsburg lots (P12 in the data record, the contributor note, README and checklist impact).
- `R-4c-9`: The explicit null entry `{"03103": null}` of `parking_free_share_proxy_classes` survives the deep merge of
  configuration overlays and means "no proxy".
- `R-4d-2`: Garage options act only on street_paid and resident_zone zones, after the early rules and only when the street
  option costs more than 0; campus zones are priced as before and a stay without a garage in range prices as schema 2.
- `R-4d-4`: The expectation is summed over the options in the order street first, then garages by ascending id, in double
  precision with P_i = w_i / sum(w), and rounded half up to the cent once at the end; the golden generator keeps every pinned
  expectation away from a half cent.
- `R-4d-5`: The outcome PAID_EXPECTED is appended at the end of the outcome tuple; `garage_probability` is the sum of the
  garage probabilities of the stay.
- `R-4d-15`: `duration_band_price_eur` rejects a NaN duration; the loader's band rules (price never falls, only the first
  band free) are stricter than spec E11 and say so.
- `R-4e-3`: Java integration of the garage options in the car cost model: the activity coordinate is passed in, a spatial
  index or per-activity cache keeps the evaluation cheap, and with the decay 0 or absent no option code runs.
- `R-5-4`: The degenerate zone-level paid-share check of the decay calibration (blind to the fee window, 1.0 in Ia and Ib) is
  dropped; the time-aware paid share is compared on a run's own outcome report instead.
- `R-C1`: Controller ruling on the municipal packages: the Braunschweig, Wolfsburg and Goslar data enter the repository with
  their source recorded in neutral wording (open reuse licence not verified; used by owner decision), quoted verbatim in the
  provenance.
- `R-D2-a`: The commuter product excludes products for customer groups the model cannot identify and capacity-limited permit
  offers; the TU member month ticket is the commuter product of the campus rows.
- `R-D3-a`: The three Goslar car parks at 1 EUR per hour fall under D3 and become single-site zones.
- `R-E1`: The BgA car parks stay zones and are not garage options (no double role).
- `R-T1-e`: In the lever-1 supply inventory a street side mapped as "separate" carries no information about the street
  itself; the separately mapped parking area decides by its own class.
- `R-T1-f`: Lever-1 pieces in Braunschweig are assigned whole to the annex zone they overlap most (tariff choice), never
  clipped to it.
- `R-T1-g`: The lever-1 tooling refuses to overwrite derived outputs silently.
- `R-T1b-c`: The literal reading of the tag `parking:<side>=yes` in the supply rule stays the default although the other
  reading would pass the pre-registered check (switching because it passes would be selecting a passing combination); both
  results are recorded.
- `R-T1b-d`: A private=residents parking element counts as restricted, as the supply rule's text says; a correction, not tuning.
- `R-T1c-a`: Payment evidence (variant T) counts only parking facilities and parking payment devices; wallet payment keys and
  non-parking elements never count.
- `R-T1c-b`: The supply-share module is split into the rule, the variants and the QA/release I/O, behaviour-preserving.
- `R-T1d-a`: ASSUMPTION C-b: the Wolfsburg street billing unit is 30 min, taken from the city's superseded 2016 fee ordinance.
- `R-T1d-b`: Where the city layer (12/2024) and a 2024 press report disagree on a rate, the layer is used and the conflict is
  recorded in the row.
- `R-T1d-c`: No daily cap, maximum stay or long-stay product is invented for the Wolfsburg street zones; the tariff record
  states the expected output change.
- `R-T1e-a`: Rule R2 applies only where resident permits are valid: the optional tariff column `resident_permits_valid` (true
  by default on street and resident zones, never on a campus, false at the BgA car parks and the Goslar ZOB car park).
- `R-T2-a`: The garage core (rate, billing unit, fee window) is all-or-none; the day cap is optional and the first-period
  pair stays optional as a pair (spec Amendment A6).
- `R-T2-b`: The shared golden file pins every edge rule of the product minimum (cases V15 and later) so that the Java port
  needs no hand-ported copy.
- `R-T3-a`: Destinations of purpose home get no parking search time (rule H1: home parking is free and needs no search); one
  shared predicate serves the utility term and the population check.
