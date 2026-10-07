# Zone-based parking costs

One note for the mechanism that prices car parking when `parking_zones_enabled` is on (ADR-0139, issue #436). The
feature state lives in the feature record `parking_cost_zones`, the data in the data records `parking_zones_2026`,
`parking_tariffs_2026`, `parking_coverage_register_2026`, `srv2023_commute_parking_by_workplace_class`,
`parking_resident_districts_2026` and `parking_garages_2026`.

## Data flow

1. `braunschweig.parking.zones_stage` (synpp stage, declared only when the flag is on) reads the release relative
   to `data_path` (`RELEASE_INPUTS`: `parking_zones_path`, `parking_tariffs_path`, `parking_coverage_register_path`,
   `parking_workplace_shares_path`, `parking_resident_districts_path`, `parking_garages_path`).
   `braunschweig.parking.zones` loads and
   validates the polygons (`load_zone_polygons`: EPSG:25832, repaired rings counted, overlap at most
   `OVERLAP_TOLERANCE_M2`), the tariffs (`load_tariffs`, `validate_tariffs`), one row per polygon (`cross_validate`),
   the register (`validate_coverage_register`) and the resident districts (`load_resident_districts`: valid as stored,
   never repaired, unique ids, no overlap; `validate_district_municipalities`: a register status row per district
   municipality); every tariff row passes `braunschweig.parking.tariff_export.tariff_row_to_zone`, and every workplace
   class needs an SrV class row (`braunschweig.parking.attach.free_share_by_class`, `check_workplace_classes`).
   Output: `zones`, `tariffs`, `workplace_shares`, `coverage_register`, `districts` and `sources` (dataset id,
   relative path, LF-normalised sha256 per input).
2. `braunschweig.matsim.scenario.population` (the plans-writer wrapper) calls, after the cordon in-commuter merge,
   `attach_parking_zones` (activity column `parking_zone`, point in polygon by `zones.assign_zones`),
   `attach_resident_zones` (person column `resident_parking_zone`) and `draw_parking_free` (activity column
   `parking_free`, one draw per person from `RandomState(random_seed + PARKING_FREE_SEED_OFFSET)`, shifted by
   `parking_workplace_free_share_shift`, with the two options of the rule below). The vendored writer `matsim.scenario.population` emits them through
   `OPTIONAL_ACTIVITY_FIELDS` and `OPTIONAL_PERSON_FIELDS` as the activity attributes `parkingZone`
   (`java.lang.String`) and `parkingFree` (`java.lang.Boolean`, written only when true) and the person attribute
   `residentParkingZone` (`java.lang.String`); a missing value writes no attribute, never `"nan"`. A very small
   smoke (a single Kreis at 0.1 %) can place no activity inside any of the small zone polygons and then stops with
   the zero-coverage error of `attach_parking_zones`, the broken-join guard: widen the region or the sample of such
   a smoke instead of weakening the guard. The resident parking districts (spec Amendment C3) are a second layer
   on the same merged frames: `attach_parking_districts` (activity column `parking_district`, written as
   `parkingDistrict`) and `attach_resident_districts` (person column `resident_parking_district`, the district of
   the home, written as `residentParkingDistrict`), each only where set. The two layers are independent: an activity
   can lie in a district and in no zone and the other way round, and neither attachment reads the other. The wrapper
   logs one more coverage line with the own-district stays, the exposure of assumption R2.
3. `braunschweig.matsim.simulation.prepare._write_parking_inputs`, the last step of the preparation: the release
   tariffs become the tariff model `<prefix>parking_tariffs_<parking_tariff_snapshot_date>.json`
   (`tariff_export.build_tariff_model`: integer cents and seconds, the permit flag `resident_permits_valid` of every
   zone, the rendered `ASSUMPTIONS_REGISTER`, the release `sources` and the ids of the release's resident districts as
   `resident_districts`, the ids a plan may carry), `braunschweig.matsim.config_modules.write_module` adds the module
   `braunschweigParking` (`enabled`,
   `tariffsPath` relative to the config, `terminalStayRule`, and `minimumStayMinutes` from
   `parking_minimum_stay_min` in plain digits) to the final config, and `<prefix>parking_inputs_report.json` lists
   the files and records the terminal-stay rule and the minimum stay. `_check_parking_parameters` rejects at
   configure time a minimum stay that is not a whole number in `[0, PARKING_MINIMUM_STAY_MAXIMUM_MIN]` (its seconds
   must fit a Java int), a bool included. `matsim.output.copy_parking_inputs` copies the listed files next to the
   exported config and raises for a missing one.
4. Java (eqasim-java-bs, `org.eqasim.braunschweig.parking`): `BraunschweigConfigurator.updateConfig` switches the
   car cost model to `ZoneParkingCarCostModel` when the module is enabled. `ParkingModule` reads the model into
   `ParkingTariffs` and binds `ParkingPopulationCheck`, which fails the run at controller start for an unknown zone
   id, a resident zone of another type, `parkingFree` without a zone, any legacy `isParis` attribute or a mistyped
   parking attribute, and logs the coverage in one `[parking] population:` line. `ZoneParkingCarCostModel` extends
   the stay at each car trip's destination to the minimum stay (`ParkingCostCalculator.minimumStayDeparture_s`,
   after the terminal-stay rule for a terminal stay; 0 min when the module has no `minimumStayMinutes`), prices it
   with `ParkingCostCalculator` and adds it to the driving cost; `ParkingOutcomeCounter` counts one `ParkingOutcome`
   per pricing call and the stays the minimum extended, and `ParkingOutcomeReportListener` writes
   `ITERS/it.N/N.parking_outcomes.csv` (columns `outcome`, `count`, `share`; one row per outcome, zero counts
   included) and one `[parking]` log line per iteration, logs the active minimum stay once at startup and, per
   iteration, how many priced stays in a zone the minimum extended. The counts are pricing calls, one per car
   alternative that mode choice priced, chosen or not: not distinct trips or persons.
5. `matsim.simulation.run` checks around the Java run (`braunschweig.parking.runtime_checks`), because MATSim reads
   the module of a jar without the parking package as an untyped group and would price nothing: when the prepared
   config enables `braunschweigParking`, `require_parking_package` refuses a jar without
   `org/eqasim/braunschweig/parking/ParkingConfigGroup.class` before MATSim starts, and `require_parking_outcomes`
   refuses a run whose last iteration (the highest `ITERS/it.N`; the eqasim termination criterion can end a run
   before `lastIteration`) wrote no `N.parking_outcomes.csv`; one `[parking] run check:` INFO line reports that both
   passed. With the module absent or disabled neither check runs.

## Adding or changing a zone

1. Digitise the polygon into `parking_zones_2026.geojson` with every `ZONE_PROVENANCE_COLUMNS` field; cut it out of
   any zone it overlaps (a paid island inside a resident zone is its own polygon). The committed file is the output of
   the one-off curation chain `scripts/curation/parking_zones_2026/assemble_parking_zones.py` (its steps
   `municipal_zones.py` for `--municipal-dir` and `regional_zones.py` for `--regional-dir`; the raw inputs are
   gitignored), so a change is made in the curation and the file regenerated, never edited by hand; the data record
   `parking_zones_2026` holds the command line and the inputs. The geometry source is one of `GEOMETRY_SOURCES`; a
   source that implies a zone type (`GEOMETRY_SOURCE_ZONE_TYPES`: `campus_outline_and_detection_zones` and
   `campus_detection_zones` are campus zones, `single_site_buffered` a street zone) must match the tariff row
   (`validate_geometry_source_zone_types`), and the municipal QA table needs rows for every
   `municipal_street_sections_buffered`, `campus_outline_and_detection_zones`, `campus_detection_zones` and
   `single_site_buffered` zone (`municipal_zone_qa.municipal_zone_ids`). A TU campus zone is the union of its campus
   grounds (the v1 release polygon of the OSM university outline: the destination area, where the buildings are) and
   its camera detection zones (the paid car parks); the detection zones do not contain the buildings.
2. Add its row to `parking_tariffs_2026.csv` (`TARIFF_COLUMNS`; the required and forbidden fields per zone type are
   `REQUIRED_FIELDS_BY_TYPE` and `FORBIDDEN_FIELDS_BY_TYPE`), with `source_url`, `source_date`, `valid_from` and a
   `fee_window_source` (`assumption` where no ordinance or signage gives the window, F1). A value no source gives
   stays empty or is a named assumption in the row's notes; the curation checks the rows of a regional package
   against the package's tariff rules (`--regional-tariffs`).
3. Set the municipality to `zoned` in `parking_coverage_register_2026.csv`; its workplace class must have a class row
   in `srv2023_commute_parking_by_workplace_class.csv`.
4. Run `python scripts/validate_parking_zones.py --data-path eqasim-data/data` and `tests/test_parking_zones.py`,
   force-add the changed files (`git add -f`), and update the validator summary and limitations in the three data
   records. To see what a release change touches, run `scripts/curation/parking_zones_2026/count_zone_exposure.py` on
   the earlier and the new zone file (activities and car arrivals of a plans file per added, removed or changed
   zone; an exposure indicator, not a validation).

## Rules maintainers must keep

- The free-parking draw (`braunschweig.parking.attach.draw_parking_free`; semantics, the single seeded uniform and
  the rule for a person with paid-zone and campus activities are in its docstring) has two options, validated by
  `braunschweig.parking.free_draw_options` at configure time (the plans-writer wrapper) and again in the draw:
  `parking_free_share_proxy_classes` (ASSUMPTION A1-b, why Wolfsburg borrows another class: data record
  `srv2023_commute_parking_by_workplace_class`) and `parking_campus_free_share` (ASSUMPTION C2, an owner estimate).
  Their production values live in `configs/base_bs.yml` and, equal, in the two popsim fixture configurations
  (`tests/test_popsim_config_parity.py`); the code defaults are OFF. A config overlay is deep-merged, so the sensitivity
  arm without the proxy is `{"03103": null}`, never `{}`, which leaves the base entry in place. Do not add a third
  source of free parking without a record: the draw is the only writer of `parkingFree`.
- The resident districts are data of their own, not zones: `parking_resident_districts_2026.geojson` is built by
  the curation step `scripts/curation/parking_zones_2026/resident_districts.py` from the owner's packages (ids
  prefixed per town, the Goslar feature without a code joined to C, an overlap cut from the later district), must
  stay valid as stored (`load_resident_districts` raises, it never repairs) and is validated with
  `scripts/validate_parking_zones.py`. Rule R2 (`parking_cost_cents(..., resident_of_district=...)`, outcome
  `RESIDENT_FREE`) takes only the boolean "the activity lies in the district of the person's home"; the caller
  compares `parkingDistrict` with `residentParkingDistrict`. It acts inside a fee zone only (Z1 first) and only where
  the zone honours resident permits (ASSUMPTION R2-a: the tariff column and `ZoneTariff` field
  `resident_permits_valid`, valid by default on street and resident zones, never on a campus, false at the five BgA
  car parks and at the Goslar car park at the ZOB, `gs_parkplatz_klubgartenstrasse_zob`), after the home and
  employer-free rules and before the fee-window check. The flag switches off R2 only:
  the zone's own residents (R1) stay exempt. The golden cases R01 to R13 pin it, and the golden JSON has
  `schema_version` 3 since.
- The garage dataset is data of its own, not zones (spec Amendment E1): `parking_garages_2026.geojson` (points, WGS84
  on disk, EPSG:25832 after `braunschweig.parking.garages.load_garages`) with its QA table
  `parking_garages_2026_qa.csv` (`braunschweig.parking.garage_qa`) is built by
  `scripts/curation/parking_zones_2026/regional_garages.py` from the owner's regional package and its two optional
  supplement packages (`--supplement-zip`, `--followup-zip`, read by `garage_supplement.py`), so a change is made in
  `regional_garage_specs.py` (which package rule plays which role) and the two files are regenerated, never edited by
  hand. `braunschweig.parking.zones_stage` loads it as the sixth release input and the tariff model (schema 3) lists its
  priced garages; the zone-level garage columns of the tariff table stay empty (Amendment E8) and garages enter the
  pricing only through the distance-weighted garage options ([parking-garage-options.md](parking-garage-options.md)). A garage is priced only where the published structure maps
  exactly to ONE of three exclusive forms (one tariff structure per garage; no approximation, `not_priced_reason` says
  why when none fits): the single window (first period, hourly rate in started units, day cap), the time-of-day tiers
  of the column `tariff_tiers` or the duration bands of the column `tariff_duration_bands` (grammar `0-20 free; 20-120
  total 1.00; 120-240 0.50/60; 420- 5.00/60`, ASSUMPTION P8: a total band sets the price, an increment band adds per
  started unit from the band's start, a cap or 24-hour price is the day cap; `garages.duration_band_price_eur` is the
  pure reference evaluation that task 4d reproduces, it is wired nowhere yet and raises for a stay beyond a closed
  schedule). A first period carries the clock window its rule states in `garage_first_period_start_h` and
  `garage_first_period_end_h` (empty where the source ties it to none; P6 as amended: charged once when the arrival
  lies inside the window, the tiers then run per started unit from its end, an arrival outside the window pays the
  tiers from the arrival). Only rules that the package marks `preferred_for_current_use` set a value: a specification
  that names another rule for a role stops the step; a rule of a supplement package never sets a value by itself (the
  packages mark every rule `full_cost_calculation_ready=false`): `SUPPLEMENT_RELEASED` and `FOLLOWUP_RELEASED` name the owner
  decision that releases it, and only while the package's field decision still has the status the decision relied on. A
  row that a package touches cites its SHA-256 after the regional one in `package_sha256`; a facility is matched by its
  `facility_id` or a verified `legacy_id`, never `BS_None` (airport: legacy key `BS_SOURCE_4781...`) and never
  `HE_GROEPERN_STRASSE` for the Groepern garage (`HE_GROEPERN_TG_118`). A free period is read as a grace period (P10: a
  stay not longer than it costs 0, a longer stay is billed from the arrival, a free first band; the specification of every
  P10 row names the supplement's field decision and the status its ruling relied on, `p10_decisions`, and the build checks
  it) and a garage without an operator tariff is priced from the best secondary evidence (P11; at Groepern the monthly
  product only: the brochure tariff stays the official one and the undated directory entry, which confirms the unit and
  conflicts at 2 h, is recorded as a variant), each with its basis in the notes. A rest tier (a day rate without
  charging times beside a night tier) refuses a rule that states charging times. The assumptions P3
  to P8, P10 and P11 are named in the row and counted at every run and by `scripts/validate_parking_zones.py` (with the union rates, which warn above
  `garages.UNION_WARNING_SHARE`), a station car park of DB BahnPark or a lot of long-term renters is a candidate QA
  row, not a garage, and a new garage needs a QA row (the validator refuses a dataset without its QA table). The
  package's facility BS_None is named 'Parkhaus Forschungsflughafen' although its rules are the Ring-Center's
  (checked, see the garage's notes); never join a garage to the package by a facility name. The data record
  `parking_garages_2026` holds the full rules.
- A commuter product is data of the tariff table (`commuter_day_eur`, the cheapest monthly or 30-day product over 21
  working days, ASSUMPTION P2): the QA table holds the record of every monthly product, used or not, and the validator
  checks the table against the used product.
- Overlaps of the release are settled by an explicit precedence in the curation (`PRECEDENCE_REGIONAL` in
  `assemble_parking_zones.py`: the campus zones first, then the BgA lots and the single paid sites, then the older
  zones), never by the order of the file. Every cut that involves a zone of the regional step is a QA row
  `<loser>_cut_by_<winner>` of `parking_zones_2026_municipal_qa.csv`, and after the 0.5 m simplification the winner
  is subtracted once more (`enforce_exact_cuts`): the simplification of a cut zone can move its hole ring back across
  the winner. The release must load with `load_zone_polygons(..., max_repairs=0)`, nothing is repaired.
- A zone that lies outside its own municipality of the pipeline's polygons fails the containment check of the
  assembly unless it is declared in `regional_zones.CONTAINMENT_EXCEPTIONS` with its reason; the declared exception
  still has to lie in its own and the declared municipality together.
- The tariff model JSON is schema 3 since the garage options (Amendment E added `garages`, `garage_decay_m` and
  `garage_max_distance_m`; Amendment C3 had added the per-zone bool
  `resident_permits_valid`, never null, and the top-level list `resident_districts` without a new version), and the Java `ParkingTariffs`
  reader requires the exact key sets of the document and of every zone entry (`DOCUMENT_FIELDS`, `ZONE_FIELDS`): a key
  added to the model needs the reader's field list in the same change set.
- One outcome per priced stay: a new pricing branch adds its name to `braunschweig.parking.cost.OUTCOMES` and to the
  Java `ParkingOutcome` enum, and a golden case. Never price silently; an unknown zone id raises in Python and in
  Java.
- `braunschweig.parking.cost` is the reference; the Java `ParkingCostCalculator` must agree with it on the golden
  cases and on the eight minimum-stay cases L01 to L08, which both sides hard-code on the fixture tariffs
  (`tests/test_parking_cost.py`, Java `ParkingCostCalculatorTest`) instead of reading them from the golden JSON.
  After changing the fixture table, the golden cases or the export, re-run
  `python scripts/export_parking_golden_cases.py` and copy `parking_golden_cases.json` and
  `parking_tariffs_fixture.json` into the Java test resources (eqasim-java-bs
  `braunschweig/src/test/resources/parking/`).
- Every tariff number enters through the tariff table and the tariff model JSON; Java holds no tariff constants and
  the `braunschweigParking` module carries no prices.
- The two parking flags stay mutually exclusive (`braunschweig.matsim.scenario.population.configure` raises;
  `tests/test_configs_composed.py` pins the ring off). The OFF path must stay byte-identical
  (`tests/test_population_parking_attributes.py`, `tests/test_parking_prepare_wiring.py`).
- The zones need the plans-writer wrapper: a configuration that runs them aliases `matsim.scenario.population` to
  `braunschweig.matsim.scenario.population`. The plain writer attaches no parking column, so its `configure`
  rejects `parking_zones_enabled`; the wrapper calls `declare_writer_inputs` instead of that `configure`, and must
  keep doing so.
- A scenario prepared with the zones on has no `isParis` attributes: never switch `braunschweigParking.enabled` off
  on it (the legacy model would then price no parking and no check runs), re-prepare with the flags of the wanted
  mode instead. The reverse, ring plans with the module on, fails at controller start (`ParkingPopulationCheck`).
- The seed offset `PARKING_FREE_SEED_OFFSET` (7371) must stay unused by every other seeded stream.
- Assumption parameters live in `configs/base_bs.yml` under `parking_*`; the two popsim fixture configurations
  carry the same block, pinned equal by `tests/test_popsim_config_parity.py`. A change of an assumption needs an
  ADR amendment and an update of `ASSUMPTIONS_REGISTER`, except the minimum stay L1 (`parking_minimum_stay_min`):
  a run parameter that travels in the `braunschweigParking` module and the inputs report, not in the register, so
  the tariff model JSON does not change with it.

## Known limitations

The limitations to state with every result are listed once, in ADR-0139 (Consequences). The zone inventory and
what is known but not zoned live in the data record `parking_zones_2026` and the coverage register.
