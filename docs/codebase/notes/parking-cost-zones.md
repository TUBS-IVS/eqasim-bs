# Zone-based parking costs

One note for the mechanism that prices car parking when `parking_zones_enabled` is on (ADR-0139, issue #436). The
feature state lives in the feature record `parking_cost_zones`, the data in the data records `parking_zones_2026`,
`parking_tariffs_2026`, `parking_coverage_register_2026` and `srv2023_commute_parking_by_workplace_class`.

## Data flow

1. `braunschweig.parking.zones_stage` (synpp stage, declared only when the flag is on) reads the release relative
   to `data_path` (`RELEASE_INPUTS`: `parking_zones_path`, `parking_tariffs_path`, `parking_coverage_register_path`,
   `parking_workplace_shares_path`). `braunschweig.parking.zones` loads and validates the polygons
   (`load_zone_polygons`: EPSG:25832, repaired rings counted, overlap at most `OVERLAP_TOLERANCE_M2`), the tariffs
   (`load_tariffs`, `validate_tariffs`), one row per polygon (`cross_validate`) and the register
   (`validate_coverage_register`); every tariff row passes `braunschweig.parking.tariff_export.tariff_row_to_zone`,
   and every workplace class needs an SrV class row (`braunschweig.parking.attach.free_share_by_class`,
   `check_workplace_classes`). Output: `zones`, `tariffs`, `workplace_shares`, `coverage_register` and `sources`
   (dataset id, relative path, LF-normalised sha256 per input).
2. `braunschweig.matsim.scenario.population` (the plans-writer wrapper) calls, after the cordon in-commuter merge,
   `attach_parking_zones` (activity column `parking_zone`, point in polygon by `zones.assign_zones`),
   `attach_resident_zones` (person column `resident_parking_zone`) and `draw_parking_free` (activity column
   `parking_free`, one draw per person from `RandomState(random_seed + PARKING_FREE_SEED_OFFSET)`, shifted by
   `parking_workplace_free_share_shift`). The vendored writer `matsim.scenario.population` emits them through
   `OPTIONAL_ACTIVITY_FIELDS` and `OPTIONAL_PERSON_FIELDS` as the activity attributes `parkingZone`
   (`java.lang.String`) and `parkingFree` (`java.lang.Boolean`, written only when true) and the person attribute
   `residentParkingZone` (`java.lang.String`); a missing value writes no attribute, never `"nan"`. A very small
   smoke (a single Kreis at 0.1 %) can place no activity inside any of the small zone polygons and then stops with
   the zero-coverage error of `attach_parking_zones`, the broken-join guard: widen the region or the sample of such
   a smoke instead of weakening the guard.
3. `braunschweig.matsim.simulation.prepare._write_parking_inputs`, the last step of the preparation: the release
   tariffs become the tariff model `<prefix>parking_tariffs_<parking_tariff_snapshot_date>.json`
   (`tariff_export.build_tariff_model`: integer cents and seconds, the rendered `ASSUMPTIONS_REGISTER`, the release
   `sources`), `braunschweig.matsim.config_modules.write_module` adds the module `braunschweigParking` (`enabled`,
   `tariffsPath` relative to the config, `terminalStayRule`) to the final config, and
   `<prefix>parking_inputs_report.json` lists the files. `matsim.output.copy_parking_inputs` copies the listed files
   next to the exported config and raises for a missing one.
4. Java (eqasim-java-bs, `org.eqasim.braunschweig.parking`): `BraunschweigConfigurator.updateConfig` switches the
   car cost model to `ZoneParkingCarCostModel` when the module is enabled. `ParkingModule` reads the model into
   `ParkingTariffs` and binds `ParkingPopulationCheck`, which fails the run at controller start for an unknown zone
   id, a resident zone of another type, `parkingFree` without a zone, any legacy `isParis` attribute or a mistyped
   parking attribute, and logs the coverage in one `[parking] population:` line. `ZoneParkingCarCostModel` prices
   the stay at each car trip's destination with `ParkingCostCalculator` and adds it to the driving cost;
   `ParkingOutcomeCounter` counts one `ParkingOutcome` per pricing call and `ParkingOutcomeReportListener` writes
   `ITERS/it.N/N.parking_outcomes.csv` (columns `outcome`, `count`, `share`; one row per outcome, zero counts
   included) and one `[parking]` log line per iteration. The counts are pricing calls, one per car alternative that
   mode choice priced, chosen or not: not distinct trips or persons.
5. `matsim.simulation.run` checks around the Java run (`braunschweig.parking.runtime_checks`), because MATSim reads
   the module of a jar without the parking package as an untyped group and would price nothing: when the prepared
   config enables `braunschweigParking`, `require_parking_package` refuses a jar without
   `org/eqasim/braunschweig/parking/ParkingConfigGroup.class` before MATSim starts, and `require_parking_outcomes`
   refuses a run whose last iteration (the highest `ITERS/it.N`; the eqasim termination criterion can end a run
   before `lastIteration`) wrote no `N.parking_outcomes.csv`; one `[parking] run check:` INFO line reports that both
   passed. With the module absent or disabled neither check runs.

## Adding or changing a zone

1. Digitise the polygon into `parking_zones_2026.geojson` with every `ZONE_PROVENANCE_COLUMNS` field; cut it out of
   any zone it overlaps (a paid island inside a resident zone is its own polygon).
2. Add its row to `parking_tariffs_2026.csv` (`TARIFF_COLUMNS`; the required and forbidden fields per zone type are
   `REQUIRED_FIELDS_BY_TYPE` and `FORBIDDEN_FIELDS_BY_TYPE`), with `source_url`, `source_date`, `valid_from` and a
   `fee_window_source` (`assumption` where no ordinance or signage gives the window, F1).
3. Set the municipality to `zoned` in `parking_coverage_register_2026.csv`; its workplace class must have a class row
   in `srv2023_commute_parking_by_workplace_class.csv`.
4. Run `python scripts/validate_parking_zones.py --data-path eqasim-data/data` and `tests/test_parking_zones.py`,
   force-add the changed files (`git add -f`), and update the validator summary and limitations in the three data
   records.

## Rules maintainers must keep

- One outcome per priced stay: a new pricing branch adds its name to `braunschweig.parking.cost.OUTCOMES` and to the
  Java `ParkingOutcome` enum, and a golden case. Never price silently; an unknown zone id raises in Python and in
  Java.
- `braunschweig.parking.cost` is the reference; the Java `ParkingCostCalculator` must agree with it on the golden
  cases. After changing the fixture table, the golden cases or the export, re-run
  `python scripts/export_parking_golden_cases.py` and copy `parking_golden_cases.json` and
  `parking_tariffs_fixture.json` into the Java test resources (eqasim-java-bs
  `braunschweig/src/test/resources/parking/`).
- Every tariff number enters through the tariff table and the tariff model JSON; Java holds no tariff constants and
  the `braunschweigParking` module carries no prices.
- The two parking flags stay mutually exclusive (`braunschweig.matsim.scenario.population.configure` raises;
  `tests/test_configs_composed.py` pins the ring off). The OFF path must stay byte-identical
  (`tests/test_population_parking_attributes.py`, `tests/test_parking_prepare_wiring.py`).
- A scenario prepared with the zones on has no `isParis` attributes: never switch `braunschweigParking.enabled` off
  on it (the legacy model would then price no parking and no check runs), re-prepare with the flags of the wanted
  mode instead. The reverse, ring plans with the module on, fails at controller start (`ParkingPopulationCheck`).
- The seed offset `PARKING_FREE_SEED_OFFSET` (7371) must stay unused by every other seeded stream.
- Assumption parameters live in `configs/base_bs.yml` under `parking_*`; a change of an assumption needs an ADR
  amendment and an update of `ASSUMPTIONS_REGISTER`.

## Known limitations

The limitations to state with every result are listed once, in ADR-0139 (Consequences). The zone inventory and
what is known but not zoned live in the data record `parking_zones_2026` and the coverage register.
