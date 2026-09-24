# ADR-0133 · 2026-09-24 · A compact VRB zone fare model replaces the parked tariff engine

- **Status:** accepted (maintainer decision "A", 2026-09-24); production flag OFF until the 1 % ON/OFF smoke is recorded
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
   (Data Registry `vrb_tariff_zone_polygons`; facility attribute `vrbTariffZone`). Zones 55/56 are 300 m buffers
   around the GTFS stops named Hämelerwald/Dedenhausen (ASSUMPTION; VRB terms 2026 §9.2). Overlap slivers go to
   the lower zone id; a facility on a shared border takes the lower id and is counted as a tie.
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
   `fernverkehr|flix` -> `long_distance` (Deutschlandticket invalid; priced with the fallback); everything else
   `regional`.
7. **External rides (D7).** When a leg touches an unzoned stop and the holder has no Germany-wide flat: rail legs
   -> Niedersachsentarif second-class single by band on the sum of ridden stop-to-stop distances x 1.0
   (ASSUMPTION; `docs/data/external-regional-rail-single-fares-2026.json`), child band for 6-14; no rail -> 370 ct
   local single (ASSUMPTION: GVH one-zone single 2026 for every external local operator). A mixed VRB/external
   journey is priced once as external; a VRB-network flat does not reduce it.
8. **Fallbacks (D8).** Every quote carries exactly one outcome label, counted per iteration
   (`ITERS/it.N/N.vrb_fare_outcomes.csv`, log marker `[vrb-fares]`). Fallback outcomes (`category_missing`,
   `category_unknown`, `line_scope_missing`, `long_distance_fallback`, `vrb_pair_undefined_fallback`,
   `external_rail_beyond_bands`) cost `unsupportedFallbackPriceCents` (default 370, ASSUMPTION) — except the two
   category outcomes, which keep the no-entitlement price — and the run fails after an iteration whose fallback
   share of the evaluated quotes exceeds `maximumUnsupportedShare` (default 0.05, ASSUMPTION for diagnostics).
   One ride never crashes the run.
9. **Day-ticket cap (D9).** The DMC is tour-based, so the earlier trips of the candidate tour and of the already
   selected tours are known. A person's VRB cash for the day is min(sum of VRB singles, day ticket of the highest
   price class used); the current trip pays the increase. The prefix-aware estimator seam passes DMC's previous
   trips; a prefix-aware tour estimator adds the selected earlier tours; `pt` is removed from the DMC cached modes.
   Earlier trips are re-priced without counting, and the informational label `day_ticket_cap_applied` is not
   part of the quote total. Without the cap two city singles already equal the city day ticket (7.20 EUR), so
   every further trip of the day would be over-priced.
10. **Inputs (D10).** Preparation writes `vrb_tariff_zones.shp`, `vrb_line_scopes.csv`, `vrb_fare_model_2026.json`,
    the `vrbFare` module and `vrb_fare_inputs_report.json` (line-scope coverage below 99 % fails the stage);
    `matsim.output` copies the three data files unprefixed next to the config, which references them.

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

## Consequences

- Scientific results change on ON: PT costs move from the ring approximation to sourced zone prices; flat holders
  keep 0 EUR inside their network but pay outside it. Mode-choice parameters are not recalibrated by this
  decision (#23); the legacy distance interaction on cost stays in the utility.
- The parked engine branches stay parked; only their data artefacts (prices, rail tables, polygon record, the
  DMC prefix seam) were carried over.
- `vrb_zone_fares_enabled` is OFF in `configs/base_bs.yml` until a 1 % smoke recorded in a run manifest shows a
  fallback share below 5 % and a plausible price distribution.
- Limitations to state with every result: no ticket reuse within validity windows, no six-ride packages, one flat
  single for every external local operator, ridden distance as tariff distance for rail, children under 14
  without their real school tickets (the population marks them `never_pt`), ticket categories from the 2023
  surveys, and the optional standalone mode choice inside `matsim.simulation.prepare` still uses the legacy model.

## Evidence

- Feature record `vrb_zone_fare_model`; data records `vrb_tariff_zone_polygons`, `vrb_price_stage_matrix_2022`,
  `vrb_dated_price_inputs_2026`, `external_regional_rail_single_fares_2026`.
- VRB Tarifbestimmungen 01.01.2026 (§2.2 price-stage matrix, §3.2 validity and short trip, §3.4 day ticket,
  §9 tariff transitions); VRB Tarifflyer 2026; Deutschlandticket scope (bahn.de); GVH prices 2026 (hannover.de).
- Java unit tests in eqasim-java-bs `org.eqasim.braunschweig.fares.zonal` and
  `org.eqasim.braunschweig.scenario.TariffZoneAssignerTest`; Python tests listed in the feature record.
