# VRB zone fare model (compact)

One note for the mechanism that prices PT trips when `vrb_zone_fares_enabled` is on (ADR-0133).

## Data flow

1. `braunschweig.data.vrb.zone_polygons` (Python stage): official polygons -> repaired, de-overlapped,
   plus point zones 55/56 -> `GeoDataFrame[zone_id, geometry]` and `zone_polygons_report.json`.
2. `braunschweig.matsim.simulation.prepare._attach_vrb_tariff_zones`: shapefile -> Java
   `org.eqasim.braunschweig.scenario.AddVrbTariffZoneInformation` -> facility attribute `vrbTariffZone`
   (string) plus `vrb_tariff_zone_report.json`.
3. `braunschweig.matsim.simulation.prepare._write_vrb_fare_inputs` (after the cordon cut and freight
   injection): `braunschweig.data.vrb.line_scopes` -> `vrb_line_scopes.csv`;
   `braunschweig.data.vrb.fare_model_export` -> `vrb_fare_model_2026.json`;
   `braunschweig.data.vrb.fare_config_xml` -> module `vrbFare` in the config; coverage report
   `vrb_fare_inputs_report.json` (line scope coverage below `vrb_fare_minimum_line_scope_coverage`
   fails the stage). `matsim.output` copies the three files unprefixed next to the config.
4. Java (eqasim-java-bs, `org.eqasim.braunschweig.fares.zonal`): `BraunschweigConfigurator.updateConfig`
   promotes the module, switches the pt cost model and estimator names and, with the cap on, removes `pt`
   from the DMC cache and sets `PrefixAwareTourEstimator`. `VrbZoneFareCostModel` prices,
   `FareOutcomeCounter` counts, `FareOutcomeReportListener` writes `N.vrb_fare_outcomes.csv` and enforces
   the threshold, `VrbZoneFarePtUtilityEstimator` applies the day-ticket cap over the DMC prefix.

## Rules maintainers must keep

- One outcome label per quote; a new pricing branch adds a constant to `FareQuote` and, if it is a
  fallback, to `FareQuote.FALLBACK_OUTCOMES`. Never price silently.
- Re-pricing for the cap uses `FareQuoteSource.quoteWithoutCounting`; only the current trip is counted.
- Every tariff number enters through `vrb_fare_model_2026.json`; Java holds no tariff constants. When the
  JSON contract changes, regenerate the Java test resource `vrb-zone-fares/vrb_fare_model_2026.json`.
- The OFF path must stay byte-identical (`tests/test_vrb_zone_fares_prepare_wiring.py`).
- Assumption parameters live in `configs/base_bs.yml` under `vrb_fare_*`; a change needs an ADR amendment.

## Known limitations

No ticket reuse within validity windows, no six-ride packages, one flat local single for every external
operator, ridden distance as tariff distance for rail, children under 14 without school tickets, 2023
survey ticket categories, and the optional standalone mode choice inside `matsim.simulation.prepare`
prices with the legacy model. The full tariff-law engine is parked on the branches
`codex/regional-pt-fares` (eqasim-bs 8ea3c55b, eqasim-java-bs 18e111771).
