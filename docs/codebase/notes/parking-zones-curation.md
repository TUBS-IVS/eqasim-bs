# Curating the parking v3 data (Part B of issue #436)

How the data of parking cost zones v3 is produced and kept consistent: the corrected tariff table, the Wolfsburg area
zones, the resident districts of four more towns, the free lots, and the recalibration that closes the chain. The decisions
and the rejected alternatives live in ADR-0142 (Part B); every number, hash and source of a file lives in its data record
(`parking_tariffs_2026`, `parking_zones_2026`, `parking_resident_districts_2026`, `parking_garages_2026`,
`parking_coverage_register_2026`, `parking_garage_decay_calibration_2026`; the raw packages `parking_package_*`). This note
says where the code is and which rules a maintainer must keep. The zone mechanics around it are in
[parking-cost-zones.md](parking-cost-zones.md), the lots in [parking-large-lots.md](parking-large-lots.md) and
[parking-garage-options.md](parking-garage-options.md).

## The chain and its order

All steps are curation run by a person (`scripts/curation/parking_zones_2026/`, `scripts/parking/`), never pipeline stages;
their raw inputs (the owner packages of `raw_sources/`) are local and gitignored. The order matters because each step
changes a file that a later step pins:

1. **Tariff table and zone polygons.** The table `parking_tariffs_2026.csv` is edited by hand and enforced by the curation
   checks (`municipal_zones.check_tariff_rows`, `regional_zones.check_tariff_rows`, `station_lots.check_tariff_rows`), which
   compare every corrected cell with the evidence of its package on each chain run. The area zones are added by
   `wolfsburg_area_release.carve_release` (a finished release) or by the chain with `--municipal-tariffs`.
2. **Garage dataset.** `regional_garages.py` re-derives the lots from the zone and tariff files; a lot inside a changed zone
   changes zone, tariff and maximum stay with it (two Wolfsburg lots did in B2).
3. **Resident districts.** `resident_districts.py` with `--research-package` and `--osm-extract` (independent of 1 and 2).
4. **Calibration.** `scripts/parking/calibrate_garage_decay.py` last: its table names the garage dataset, the zone file and the
   tariff table by SHA-256, and a test ties each pin to the data record (`tests/test_parking_garage_decay_config.py`). Any
   change to steps 1 and 2 makes the table stale on purpose; it must be regenerated, never edited.

## Mechanisms and rules

- **Source ids and separate columns.** A corrected tariff cell cites a source id of a pinned package in the row's `notes`, or
  an ASSUMPTION id of the header (M3, W-max, E2, R3, CF). Fee windows (`fee_start_h`, `fee_end_h`, `fee_window_source`) and
  the maximum stay (`max_stay_min`) are separate columns; nothing is inferred from the other. `fee_window_source` takes a
  value of `zones.FEE_WINDOW_SOURCES`: a page of the operator is `operator_page`, never `municipal_page`.
- **Wolfsburg area zones** (`wolfsburg_area_zones`): `recorded_maximum_areas` selects the areas with a recorded maximum stay
  (the count is checked), `carve_area_zones` cuts each from its parent zone as the part of the parent's buffered section
  region nearer to the area's section than to any other section of the parent (the split `municipal_zones` already uses between
  tariff zones, ASSUMPTION C-a for the 50 m buffer), `area_tariff_rows` copies the parent's values and adds the area's own
  maximum stay and the compared paid-hours text, `check_area_tariff_rows` compares the committed rows with the layer. A
  zone id is `wob_area_<source id>`. The parents lose the areas, so zones never overlap. The 58 areas without a recorded
  maximum are not zones (ASSUMPTION W-max).
- **District builders** (`resident_districts_from_streets`, `resident_districts_from_maps`): street names go through ONE
  folding function (`normalise_street_name`); an unmatched street stops the step with the list; a house-number range is cut
  at the OSM house numbers; the buffer is `MatchSettings.buffer_m` (default `DEFAULT_BUFFER_M`, ASSUMPTION R3); overlaps between
  zones are
  resolved by `resolve_claims`. Every fallback (a qualifier that cannot be applied, a range without a side, the area-boundary
  match of a square) is counted per town and logged as a rate; the rates are in the district record. The map towns build from
  OSM street geometry (`build_ring_district`, `build_street_map_district`), never from hand-set vertices; a constant that
  changes the area (`HELMSTEDT_RING_HALF_WIDTH_M`) carries its reason in its comment and the record.
- **Free lots** (`large_lots.load_package_free_evidence`): a lot is free only on an archived page that holds the quotation, read
  in place from the package with its hash checked; the claim scope of the package is quoted in the dataset note, an extension
  to the lot by owner decision is named as such. A site without a position in its package stays a QA row (reason
  `lot_no_position`); no point is invented.
- **A lot with a zone product and a maximum stay** (`large_lots.zone_product_rules`) carries the zone's maximum stay as the
  garage column `max_stay_min` (compared with the ELAPSED stay, ADR-0142 decision 1); the Java reader takes the value from the
  exported JSON, the fixture keeps its own sidecar for the maximum stay
  (`tests/fixtures/parking/parking_garages_fixture_max_stay_min.json`) so that the Java-pinned fixture JSON stays byte-identical.
- **Calibration** prices every stay with `cost.parking_cost_with_garages_detail` (one weight implementation); the commuter
  and Wolfsburg measures are payer-conditional (`is_payer`), the Wolfsburg zones are selected by municipality
  (`wolfsburg_zone_ids`); a target that the search cannot reach raises `TargetUnreachable`. The configured beta must equal the
  one the table was calibrated with (`prepare.PARKING_GARAGE_DECAY_CALIBRATED_BETA_U_PER_EUR`, tested).

## Rules maintainers must keep

- Regenerate, then re-pin: after any chain step regenerate the file with the command of its data record, recompute the LF
  SHA-256 and state it once in the record (`storage.notes`); the committed tables (tariffs, coverage register, QA tables, the
  calibration table) are force-added, the three geometry files stay local (never `git add` them).
- Never edit a derived file by hand, and never loosen a tie test to make a stale calibration pass; run the calibration.
- Read the packages from their zips (SHA-256 checked by the code, the pin tied to the data record by
  `tests/test_parking_raw_package_records.py`); never extract them into the repository.
- The curation-time dependency `pdfplumber` (reads the Peine list) is pinned in `environment.yml` and the explicit locks
  (0.11.9); the real-list test still skips where it is absent. Do not import it from a stage.
- Fallback transparency applies to every step above: a rate is logged for each fallback, and a high rate is a failure signal.
