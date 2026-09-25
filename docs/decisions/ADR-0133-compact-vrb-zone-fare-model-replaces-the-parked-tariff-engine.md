# ADR-0133 · 2026-09-24 · A compact VRB zone fare model replaces the parked tariff engine

- **Status:** accepted (maintainer decision "A", 2026-09-24); production flag ON since the 1 % ON/OFF smoke
  `vrb-zone-fares-smoke-1pct-2026-09-25`
- **Numbering:** ADR-0133 is the next free id. Checked on 2026-09-24 across all local and remote branches: `main`
  holds up to ADR-0128; ADR-0129 to ADR-0132 exist only on the parked branch `codex/regional-pt-fares` and stay
  reserved for it.
- **Issue:** #430

## Context

The production PT cost model (`org.eqasim.braunschweig.mode_choice.costs.BraunschweigPtCostModel`) prices trips
with placeholder rings around Braunschweig Hbf without a source: 3.90-25.50 EUR by ring difference, 8 EUR per
begun hour outside the rings, 0 EUR for every subscription holder. A two-week attempt to implement the full VRB
tariff law (branches `codex/regional-pt-fares` in eqasim-bs, 84 commits, and in eqasim-java-bs, 113 commits,
15.6k lines of Java) could not be activated:

- its price-stage matrix (`zone_pairs`) is validated but never consulted when a product is chosen
  (`FareCurrentJourneyQuote` iterates all declared products and filters only by sales opportunity, time and
  coverage kind; `RegionalFareSpatialLaw` knows city zones, fixed zone sets, corridors and networks, no
  origin-destination price class), so several single-ticket products would always yield the cheapest one;
- an unpriced ride throws inside mode choice;
- external services need one manifest entry per GTFS trip;
- geometry-based stop memberships are rejected in strict mode.

Its tests price a single synthetic city ticket (360 ct) only.

## Decision

1. **Seam (D1).** Price the routed PT trip in `org.eqasim.braunschweig.fares.zonal.VrbZoneFareCostModel` behind
   the existing eqasim `@Named("pt") CostModel` selection (eqasim France pattern), switched by the MATSim module
   `vrbFare`. Routing, DMC model type, selector and utility formula stay unchanged (D11).
2. **Zones (D2).** Zone membership of a stop facility is the official Regionalverband polygon covering it
   (Data Registry `vrb_tariff_zone_polygons`; facility attribute `vrbTariffZone`). Zones 55/56 are point zones
   around the station complexes Hämelerwald and Dedenhausen (VRB terms 2026 §9.2): the rail-served cleaned-GTFS
   stops carrying the station name plus every stop sharing their GTFS parent station (the forecourt bus bays),
   each buffered by 50 m (ASSUMPTION: a tolerance for coordinate rounding), refused when the complex spreads
   more than 300 m around its centroid (ASSUMPTION). The other stops of the villages that carry the place name
   are not part of the zone; the first implementation buffered all stops with the name and was corrected in the
   final review. Overlap slivers above 1 m² (ASSUMPTION: smaller ones are digitisation noise) go to the lower zone
   id; a facility on a shared border takes the lower id and is counted as a tie. Every zone id on a stop facility
   must be a zone of the fare model, checked when the cost model is built.
3. **Price class (D3).** The cell (first boarding zone, last alighting zone) of the matrix printed 01.01.2022,
   applied to 2026 as an ASSUMPTION: VRB still publishes 48 zones, and the 2026 selector agreed with the matrix
   on 2026-09-23 for 40->70 (stage 2), 40->20 (stage 3) and 40->40 (city tariff). `ST` -> city, `1..4` -> ps1..ps4.
4. **Prices (D4).** Snapshot 2026-06-20 (`docs/data/vrb-dated-price-inputs.json`): single adult
   360/390/560/770/1230 ct, child 6-14 210/230/330/460/740 ct, short trip 200 ct (one bus/tram ride of at most
   three stop intervals), day ticket 720/780/1120/1540/2460 ct. Ticket validity windows are NOT modelled; every
   trip is priced on its own (ASSUMPTION).
5. **Holders (D5).** From `ptSubscriptionType`: `deutschlandticket` and `job_or_semester_ticket` -> Germany-wide
   flat (0 ct on every regional service); `monthly_or_annual_subscription` and `weekly_monthly_no_subscription`
   -> VRB-network flat; all other categories -> no entitlement. Under 6 free, 6-14 child price. Basis: in the SrV
   2023 BS+RGB microdata (probe of 2026-09-24, not yet a committed table; #308) zone-bound adult passes
   (`V_OEV_FK_VRB` codes 7 and 9) are about 1.2 % of persons 14+; the Deutschlandsemesterticket exists since
   WS 2024/25; the 2026 Plus-Abo (73.50 EUR city) costs more than the Deutschlandticket (63 EUR).
6. **Line scope (D6).** From the cleaned GTFS: extended route types 101/102 or an agency matching
   `fernverkehr|flix` -> `long_distance`; everything else `regional`. In the cleaned DELFI feed long-distance
   trains carry the basic route type 2, so the agency rule is the effective classifier. A journey with a
   long-distance ride costs the fare model's `long_distance.single_cents` (pipeline key
   `vrb_fare_long_distance_single_cents`, default 2190 ct) for every traveller of 6 or older, whatever the ticket
   category (maintainer decision 2026-09-24): neither the Deutschlandticket nor a VRB pass is valid on DB
   Fernverkehr or Flix. The value is the DB Sparpreis entry price 2026 (21.90 EUR, unchanged at the December 2025
   timetable change; DB press release). ASSUMPTION: an advertised minimum, not a mean, applied to every relation
   and to children of 6-14 alike. On the relations of the cut timetable it agrees with a distance rule built on
   the Bundesnetzagentur's average long-distance revenue of 12.6 ct per passenger-kilometre (Marktuntersuchung
   Eisenbahnen 2025, reporting year 2024) with the entry price as floor, because 21.90 EUR / 12.6 ct is about
   174 km and the long-distance relations inside the timetable are shorter; the flat price keeps one parameter
   instead of two. The outcome label is `long_distance_flat`; it is no fallback outcome and no VRB cash, so it
   neither enters the run guard nor the day-ticket cap. The router sees the price too (maintainer decision
   2026-09-25, `vrbFare.longDistanceRoutingSurchargeEnabled`, pipeline key
   `vrb_fare_long_distance_routing_surcharge_enabled`, default on): `LongDistanceFareRaptorCostCalculator` replaces
   SwissRailRaptor's default in-vehicle cost and adds, to every ride on a long-distance vehicle, the flat price
   converted into equivalent in-vehicle seconds with the value of time the Braunschweig PT utility applies to this
   person and trip (maintainer decision 2026-09-25): PT in-vehicle time utility over cost utility times the
   estimator's own distance and income interaction terms, with the trip's straight-line distance and the
   person's household income (reference income when missing, as in the estimator). With the default parameters
   21.90 EUR equal about 267 min for the reference person at 4.4 km, about 163 min at 30 km and 136 min at
   60 km at the reference income, and about 92 / 77 min at twice that income. `LongDistanceSurchargeStopFinder`,
   wrapping SwissRailRaptor's default stop finder, is the first component of a request that sees origin,
   destination and person; it values the surcharge and hands it to the in-vehicle cost through a per-thread
   context bound to the person object. A ride without a recorded valuation uses the reference person and is
   counted and logged. The surcharge is valued at the ride's marginal utility of travel time; SwissRailRaptorCore
   calls the calculator once per ride, so it counts once; under-6s get none. ASSUMPTION: the full flat price as
   the difference to the regional alternative, exact for pass holders and an upper bound for travellers who
   would pay a VRB single on the regional train. The router cannot price the VRB zone fare itself: it depends on
   the first and last zone of the whole journey and is not additive per ride.
7. **External rides (D7).** When a leg touches an unzoned stop and the holder has no Germany-wide flat: rail legs
   -> Niedersachsentarif second-class single by band on the sum of ridden stop-to-stop distances x 1.0
   (ASSUMPTION; `docs/data/external-regional-rail-single-fares-2026.json`), child band for 6-14; no rail -> 370 ct
   local single (ASSUMPTION: GVH one-zone single 2026 for every external local operator). A mixed VRB/external
   journey is priced once as external; a VRB-network flat does not reduce it.
8. **Fallbacks (D8).** Every quote carries exactly one outcome label, counted per iteration
   (`ITERS/it.N/N.vrb_fare_outcomes.csv`, log marker `[vrb-fares]`). Fallback outcomes (`category_missing`,
   `category_unknown`, `line_scope_missing`, `vrb_pair_undefined_fallback`, `external_rail_beyond_bands`) cost the fare model's `fallback.unsupported_ride_cents` (pipeline key
   `vrb_fare_unsupported_fallback_cents`, default 370, ASSUMPTION; the JSON is the only home of every price and
   the `vrbFare` module carries none) — except the two category outcomes, which keep the no-entitlement price and,
   priced as a VRB single, take part in the day-ticket cap — and the run fails after an iteration whose fallback
   share of the evaluated quotes exceeds `maximumUnsupportedShare` (default 0.05, ASSUMPTION for diagnostics).
   One ride never crashes the run; a person without an age attribute does, because the child rules need it.
9. **Day-ticket cap (D9).** The DMC is tour-based, so the earlier trips of the candidate tour and of the already
   selected tours are known. A person's VRB cash for the day is the cheapest of all VRB singles or, for every
   price class k, one day ticket of class k plus the singles of the trips above k (a second day ticket never
   helps); the current trip pays the increase, which lies between 0 and its own single. The first formulation
   min(sum of singles, day ticket of the highest class) over-priced a higher-class trip after capped city trips
   (three city trips then ps3: 8.20 instead of 7.70 EUR) and was replaced in the final review. The PT utility is
   estimated at the trip's own single fare and stays in the DMC estimate cache; `DayTicketCapTourEstimator` (the
   native cumulative estimator with the selected earlier tours added to the prefix) adds the cap correction per
   mode chain as the monetary utility term at the marginal fare minus the one at the single, which is exact
   because the monetary term is the only fare-dependent term. Earlier trips are re-priced without counting; the
   informational label `day_ticket_cap_applied` counts the chain evaluations where the cap binds and is not part
   of the quote total. The cap requires the tour-based DMC model with the cumulative tour estimator and fails
   loudly otherwise. ASSUMPTION: a day ticket of class k covers every VRB trip of class k or lower wherever it
   runs. The VRB price table has no child day ticket (the day ticket is priced by party size only), so children
   are capped by the same one-person day ticket. Without the cap two city singles already equal the city day
   ticket (7.20 EUR), so every further trip of the day would be over-priced.
10. **Inputs (D10).** Preparation writes `vrb_tariff_zones.shp`, `<prefix>vrb_line_scopes.csv`,
    `<prefix>vrb_fare_model_<snapshot>.json` (carrying the sha256 of every committed source table), the `vrbFare`
    module and `<prefix>vrb_fare_inputs_report.json`, whose coverage is counted on the final (cut) schedule the run
    prices with. Two guards fail the stage: line-scope coverage below 99 % (ASSUMPTION; the GTFS route ids and the
    schedule line ids diverged) and stop-facility zone coverage below 10 % (ASSUMPTION; the zone attribution broke,
    which the run guard cannot see because external pricing is not a fallback outcome). The committed price,
    matrix and rail tables are part of the stage's cache token. `matsim.output` copies the files the report lists
    next to the config, which references them by these names.

## Rejected alternatives

- **Completing the parked engine (option B):** needs a new coverage kind in the law engine, three further
  contract changes and a 13-sidecar package generated per final schedule; weeks of work inside code nobody on
  the team has read, and each inspection so far revealed a further gap.
- **Keeping the rings:** no source, wrong geometry (VRB zone ids are non-ordinal), ticket types not distinguished.
- **Zone-bound passes with usual work/education anchor zones:** 163,705 of 535,757 employed persons have no
  assigned work location in the 100 % cache (`docs/runs/regional-fare-usual-anchor-source-audit-100pct-2026-09-23.yml`
  on the parked branch), and the affected pass group is about 1 % of persons; deferred.
- **A purely myopic purchase rule (parked ADR-0130 item 4):** over-prices every PT trip beyond the second one of
  a day; replaced by the cap above.
- **Removing `pt` from the DMC estimate cache for the cap (first implementation of D9):** re-routes the PT trip
  once per enumerated mode chain of a tour and multiplies the outcome counts by the chain count; replaced by the
  cached estimate at the single fare plus the per-chain utility correction, which needs no eqasim-core change.
- **A configurable fallback price in the `vrbFare` module:** the cost model never read it (the price comes from
  the fare model JSON); one fact in two homes, removed in the final review.
- **Long-distance rides at the 370 ct fallback (first implementation of D6):** far below any DB long-distance
  ticket and counted as unsupported, so it both under-priced these rides and inflated the fallback share.
- **Long-distance prices from DB relation fares:** the Flexpreis is set per relation and published only per
  query on bahn.de; automated queries through the unofficial clients are rate-limited and blocked
  (db-vendo-client documentation), and the only public price monitoring (vzbv, 2024-2026) covers the five
  largest cities. Not reproducible for a committed input.
- **The average revenue per passenger-kilometre alone (12.6 ct):** a mean over BahnCard, saver and season
  tickets that prices a 60 km ride at about 7.60 EUR, below every ticket DB sells.
- **Deutschlandticket holders at 0 EUR on long-distance rides, assuming they take the parallel regional train:**
  proposed and rejected by the maintainer on 2026-09-24; the priced ride is the routed one.
- **Best of two PT routes inside mode choice (the fastest route and, when it contains a long-distance ride, a
  regional-only route, each valued with the person's own fare; the better one becomes the PT alternative):**
  the price-aware fix for the routing limitation below, preferred over a second PT mode (two near-identical
  logit alternatives would inflate the PT share). Rejected by the maintainer on 2026-09-24 as too much work for
  this feature; it needs a second router on a schedule without long-distance lines and a wrapper around the PT
  trip estimator. The routing surcharge of D6 was built instead.
- **A per-person, per-mode travel-time utility for long-distance trains (`RaptorParametersForPerson`):** needs
  the long-distance routes re-tagged with an own transport mode first (they are `rail` like regional trains in
  the prepared schedule) and turns a fixed fare into a cost per minute; the per-ride in-vehicle cost prices the
  fare as what it is.

## Consequences

- Scientific results change on ON: PT costs move from the ring approximation to sourced zone prices; flat holders
  keep 0 EUR inside their network but pay outside it. Mode-choice parameters are not recalibrated by this
  decision (#23); the legacy distance interaction on cost stays in the utility.
- The parked engine branches stay parked; only their data artefacts (prices, rail tables, polygon record, the
  DMC prefix seam) were carried over.
- `vrb_zone_fares_enabled` was OFF in `configs/base_bs.yml` until a 1 % smoke recorded in a run manifest showed a
  fallback share below 5 % and a plausible price distribution; it is ON since the smoke
  `vrb-zone-fares-smoke-1pct-2026-09-25` (fallback share 0 in every iteration). A smoke is not a validation: the
  feature stays unvalidated until a run compares it with an observed reference.
- Long-distance services stay in the routed timetable (maintainer decision 2026-09-24, issue #431 closed as not
  planned): an ICE or IC between Wolfsburg, Braunschweig and Hanover is a real option for these trips, and with
  the flat price it is no longer priced as a fallback. The router adds the flat price as in-vehicle cost (D6), so
  an ICE is only chosen when its time saving is worth the fare at the mode choice's value of time. In the 1 %
  smoke, which ran before the routing surcharge existed, none of 2,637 final-plan PT trips contained a
  long-distance ride; rerun with the surcharge on the same prepared scenario
  (`vrb-zone-fares-routing-surcharge-1pct-2026-09-25`), the router offered mode choice no long-distance route at
  all (51 of about 50,000 PT candidates before, 0 after).
- A scenario prepared with the flag ON cannot be run with `vrbFare.enabled=false` or a command-line override:
  its schedule carries `vrbTariffZone` instead of the ring attributes the legacy cost model reads. Re-prepare
  with the flag OFF instead.
- Limitations to state with every result: no ticket reuse within validity windows, no six-ride packages, one flat
  single for every external local operator, ridden distance as tariff distance for rail, children under 14
  without their real school tickets (the population marks them `never_pt`), ticket categories from the 2023
  surveys, and the optional standalone mode choice inside `matsim.simulation.prepare` still uses the legacy model.

## Evidence

- Feature record `vrb_zone_fare_model`; data records `vrb_tariff_zone_polygons`, `vrb_price_stage_matrix_2022`,
  `vrb_dated_price_inputs_2026`, `external_regional_rail_single_fares_2026`.
- VRB Tarifbestimmungen 01.01.2026 (§2.2 price-stage matrix, §3.2 validity and short trip, §3.4 day ticket,
  §9 tariff transitions); VRB Tarifflyer 2026; Deutschlandticket scope (bahn.de); GVH prices 2026 (hannover.de).
- Long-distance price (D6): DB press release "Ab Dezember: Super Sparpreise weiterhin ab 17,90 Euro erhältlich"
  (Sparpreis from 21.90 EUR); Bundesnetzagentur, Marktuntersuchung Eisenbahnen 2025, section 2.4.1 (long-distance
  revenue 12.6 ct per passenger-kilometre in 2024); DB Fernverkehr AG annual report 2025 (6,407 million EUR
  revenue, 45.2 billion passenger-kilometres); vzbv DB price monitoring 2024-2026 (cheapest daytime fares above
  the advertised minimum on 76 % of the observed days).
- Java unit tests in eqasim-java-bs `org.eqasim.braunschweig.fares.zonal` and
  `org.eqasim.braunschweig.scenario.TariffZoneAssignerTest`; Python tests listed in the feature record.
