# VRB zone fare model (compact)

One note for the mechanism that prices PT trips when `vrb_zone_fares_enabled` is on (ADR-0133).

## Data flow

1. `braunschweig.data.vrb.zone_polygons` (Python stage): official polygons -> repaired, de-overlapped,
   plus point zones 55/56 around the rail station complexes (rail-served cleaned-GTFS stops carrying the
   station name plus the stops sharing their parent station; `line_scopes.mode_class` decides "rail") ->
   `GeoDataFrame[zone_id, geometry]` and `zone_polygons_report.json`, which names the matched stops.
2. `braunschweig.matsim.simulation.prepare._attach_vrb_tariff_zones`: shapefile -> Java
   `org.eqasim.braunschweig.scenario.AddVrbTariffZoneInformation` -> facility attribute `vrbTariffZone`
   (string) plus `vrb_tariff_zone_report.json`.
3. `braunschweig.matsim.simulation.prepare._write_vrb_fare_inputs` (after the cordon cut and freight
   injection): `braunschweig.data.vrb.line_scopes` -> `<prefix>vrb_line_scopes.csv`;
   `braunschweig.data.vrb.fare_model_export` -> `<prefix>vrb_fare_model_<snapshot>.json` (its `sources`
   carry the sha256 of every committed table); `braunschweig.data.vrb.fare_config_xml` -> module `vrbFare`
   in the config; `<prefix>vrb_fare_inputs_report.json` with the coverage of the FINAL schedule, read in
   one streaming pass (`_schedule_summary`). Two guards fail the stage: line scope coverage below
   `vrb_fare_minimum_line_scope_coverage` and stop-facility zone coverage below
   `vrb_fare_minimum_facility_zone_coverage`. `matsim.output.copy_vrb_fare_inputs` copies the files the
   report lists next to the config.
4. Java (eqasim-java-bs, `org.eqasim.braunschweig.fares.zonal`): `BraunschweigConfigurator.updateConfig`
   switches the pt cost model and estimator names and, with the cap on, sets `DayTicketCapTourEstimator`
   (it requires the tour-based model with the cumulative tour estimator). `VrbZoneFareCostModel` prices
   and checks at construction that every stop zone id is a zone of the fare model, `FareOutcomeCounter`
   counts, `FareOutcomeReportListener` writes `N.vrb_fare_outcomes.csv` and enforces the threshold,
   `VrbZoneFarePtUtilityEstimator` estimates the utility at the single fare (cached by DMC) and, as the
   `DayTicketCapAdjustment`, supplies the per-chain cap correction computed by `DayTicketCap`.

## Rules maintainers must keep

- One outcome label per quote; a new pricing branch adds a constant to `FareQuote` and, if it is a
  fallback, to `FareQuote.FALLBACK_OUTCOMES`. Never price silently.
- Re-pricing for the cap uses `FareQuoteSource.quoteWithoutCounting`; only the current trip is counted.
- Every tariff number, including the fallback price, enters through the fare model JSON; Java holds no
  tariff constants and the `vrbFare` module carries no prices. When the JSON contract changes, regenerate
  the Java test resource `vrb-zone-fares/vrb_fare_model_2026.json`.
- The fare input file names have one owner, `fare_config_xml.fare_input_file_names`; the prepare stage,
  the vrbFare module and `matsim.output` all read them from there (via the report).
- The cap correction must stay a pure function of the monetary term: `DayTicketCapTourEstimator` adds it
  to a cached utility, so any new fare-dependent utility term breaks the exactness argument of ADR-0133 D9.
- The routing surcharge reads the same cost interaction terms as `BraunschweigPtUtilityEstimator`
  (`ModeChoiceValueOfTime`); a change of the PT cost term must change both.
- The OFF path must stay byte-identical (`tests/test_vrb_zone_fares_prepare_wiring.py`).
- A scenario prepared with the flag ON has no ring attributes: never switch `vrbFare.enabled` off on it,
  re-prepare with the flag off instead.
- Assumption parameters live in `configs/base_bs.yml` under `vrb_fare_*`; a change needs an ADR amendment.

## Known limitations

No ticket reuse within validity windows, no six-ride packages, one flat local single for every external
operator, one flat long-distance price for every relation and ticket (the router adds it as in-vehicle cost
at the value of time the mode choice applies to the person and trip, `LongDistanceFareRaptorCostCalculator`
with `LongDistanceSurchargeStopFinder`; long-distance services stay routed, ADR-0133 D6), ridden distance as tariff distance for rail, children under 14 without school tickets, 2023
survey ticket categories, and the optional standalone mode choice inside `matsim.simulation.prepare`
prices with the legacy model. The full tariff-law engine is parked on the branches
`codex/regional-pt-fares` (eqasim-bs 8ea3c55b, eqasim-java-bs 18e111771).
