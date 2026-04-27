---
goal: Transfer the Bavaria synthetic-population pipeline to the Braunschweig region (former Regierungsbezirk Braunschweig, ARS prefix 031) in Lower Saxony, keeping ENTD 2008 as the HTS
version: 1.0
date_created: 2026-04-23
last_updated: 2026-04-23
owner: bienzeisler
status: 'In progress'
tags: [migration, data, feature, matsim, bavaria, braunschweig, niedersachsen]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

Port the Bavaria pipeline (`config_bavaria.yml` / `bavaria/**`) to the Braunschweig region (former Regierungsbezirk Braunschweig, ARS prefix `031`). ENTD 2008 stays as HTS. Bavaria code path remains frozen; Braunschweig lives in a sibling package `braunschweig/**`.

## 1. Requirements & Constraints

- **REQ-001**: End-to-end `synthesis.output` completes for Braunschweig at 1 % sampling.
- **REQ-002**: `matsim.output` produces a runnable MATSim scenario.
- **REQ-003**: IPF uses same five marginals (commune × sex × age × employed × license).
- **REQ-004**: ENTD 2008 remains the HTS; `bavaria.entd_codes` whitelist is reused.
- **REQ-005**: Bavaria setup untouched; existing Bavaria run must replay from cache.
- **CON-001**: No edit in `bavaria/**`; fork via thin re-exports in `braunschweig/**`.
- **CON-002**: `aliases:` mechanism identical; only targets rename `bavaria.*` → `braunschweig.*`.
- **CON-003**: Hard-coded `"09"` in `bavaria/data/census/population.py::construct_municipality_id` and `bavaria/data/census/employees.py` must be parametrised (`state_code`).
- **CON-004**: Hard-coded Bavaria `kreis_mapping` in `bavaria/data/census/licenses.py` must be rebuilt (likely empty) for LSN.
- **CON-005**: `bavaria/data/buildings.py` expects `*_Hausumringe.zip` with `hausumringe.shp`; LGLN HU-NI delivery differs.
- **CON-006**: CRS stays EPSG:25832 (Braunschweig region fully in UTM 32N).
- **CON-007**: `eqasim-data/data/braunschweig/**`, `output_bs/**`, `cache_bs/**` must be gitignored.
- **PAT-001**: Per-region config keys with generic defaults (pattern already used for `osm_path_bavaria`).

## 2. Implementation Steps

### Implementation Phase 1 — Region scoping & code scaffolding

- GOAL-001: Establish `braunschweig/**` scaffold, confirm ARS prefix, introduce `state_code` / `bundesland_label` parametrisation.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Verify ARS prefix `031` yields 9 Kreise (03101 BS, 03102 SZ, 03103 WOB, 03151 GF, 03153 GS, 03154 HE, 03155 NOM, 03157 PE, 03158 WF, 03159 GÖ) | | |
| TASK-002 | Create package skeleton `braunschweig/**` as thin re-exports from `bavaria.*` | | |
| TASK-003 | Parametrise state-code: `braunschweig.state_code: "03"`; fork `population.py` + `employees.py` | | |
| TASK-004 | Parametrise Bundesland label: `braunschweig.bundesland_label: "Niedersachsen"`; fork `licenses.py` | | |
| TASK-005 | Rebuild `kreis_mapping` for LSN (likely empty; document per-Kreis finding) | | |
| TASK-006 | Create `config_local_braunschweig.yml` (own cache/output dirs, new aliases) | | |
| TASK-007 | Extend `.gitignore` for `braunschweig` data/output paths | | |

### Implementation Phase 2 — Data acquisition

- GOAL-002: Source all Lower-Saxony datasets and place under `eqasim-data/data/**`.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | VG250 reuse (federal); verify `031` → 9 Kreise, ~400 Gemeinden | | |
| TASK-009 | GENESIS-Online `12111-0001` (Bevölkerung × Gemeinde × Sex × Alter, 2022, NI) | | |
| TASK-010 | GENESIS-Online `13111-0004` (Erwerbstätige × Kreis × Sex × Alter, 2022, NI) | | |
| TASK-011 | KBA `fe4_2024.xlsx` reuse — FE4.3 has Niedersachsen, FE4.4 has `031xx` rows | | |
| TASK-012 | LSN Pendlerstatistik (Gemeinde) or BA Pendleratlas (Kreis) fallback | | |
| TASK-013 | Hausumringe Niedersachsen from LGLN OpenGeoData | | |
| TASK-014 | Geofabrik `niedersachsen-latest.osm.pbf` | | |
| TASK-015 | Delfi Deutschlandweit GTFS filtered to 031 bbox | | |
| TASK-016 | ZGB tariff zones or empty stub for `braunschweig.data.mvg.zones` | | |
| TASK-017 | Household size / income — skipped for v1 (Bavaria doesn't consume them) | | |
| TASK-018 | `scripts/verify_braunschweig_inputs.py` | | |

### Implementation Phase 3 — Code adaptation

- GOAL-003: Fork only the three loaders that genuinely differ; everything else is a re-export.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-019 | Fork `braunschweig/data/census/population.py` (GENESIS XLSX layout) | | |
| TASK-020 | Fork `braunschweig/data/census/employment.py` (GENESIS XLSX layout) | | |
| TASK-021 | Fork `braunschweig/data/census/employees.py` (LSN Pendler format) | | |
| TASK-022 | Fork `braunschweig/data/buildings.py` (LGLN HU-NI format) | | |
| TASK-023 | Fork `braunschweig/data/census/licenses.py` (Bundesland + kreis_mapping) | | |
| TASK-024 | Fork `braunschweig/data/population/raw.py` (`braunschweig.political_prefix`) | | |
| TASK-025 | Thin re-exports for all other stages (spatial, entd_codes, homes, income, ipf, gravity, locations, matsim, synthesis, osm) | | |
| TASK-026 | Aliases block in `config_local_braunschweig.yml` → `braunschweig.*` | | |

### Implementation Phase 4 — First synthesis run

- GOAL-004: Successful `synthesis.output` run for Braunschweig.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-027 | Run `synthesis.output` only (matsim commented out) | | |
| TASK-028 | Fix stage failures iteratively | | |
| TASK-029 | Sanity-check outputs: ≈8 700 persons, homes inside 031 polygon | | |

### Implementation Phase 5 — MATSim output

- GOAL-005: Runnable Braunschweig MATSim scenario.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-030 | Enable `matsim.output` | | |
| TASK-031 | Re-run with `java_memory: 32G` | | |
| TASK-032 | Verify `run.jar`, `*.xml.gz`, `shutdown completed.` in log | | |

### Implementation Phase 6 — Documentation

- GOAL-006: Reproducible Braunschweig variant.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-033 | `DOWNLOAD_CHECKLIST_BS.md` with URLs | | |
| TASK-034 | README section "Braunschweig variant" | | |
| TASK-035 | Note gravity parameters as IDF-inherited (future work) | | |

## 3. Alternatives

- **ALT-001**: Inplace edit of `bavaria/**` driven by config — rejected (would require Bavaria regression testing).
- **ALT-002**: Switch HTS to MiD 2017/2022 — rejected per user preference; future work.
- **ALT-003**: OSM buildings instead of LGLN Hausumringe — rejected v1; fallback only.
- **ALT-004**: Scope only SK Braunschweig (03101) — rejected; ZGB region needs all 9 Kreise.
- **ALT-005**: GENESIS REST API — future improvement; v1 uses manual XLSX export.

## 4. Dependencies

- **DEP-001**: Conda env `eqasim` (installed).
- **DEP-002**: `osmconvert.exe` at `C:/Users/bienzeisler/tools/osmconvert/osmconvert.exe`.
- **DEP-003**: `osmosis.bat` at `C:/Users/bienzeisler/tools/osmosis/osmosis-0.49.2/bin/osmosis.bat`.
- **DEP-004**: Maven 3.9.15 + OpenJDK 25 in conda env.
- **DEP-005**: `git config --global core.longpaths true` (set).
- **DEP-006**: ENTD 2008 CSVs under `eqasim-data/data/entd_2008/**` (present).
- **DEP-007**: KBA `fe4_2024.xlsx`, VG250 under `eqasim-data/data/germany/**` (present).
- **DEP-008**: Network access to regionalstatistik.de, lgln.niedersachsen.de, geofabrik.de, opendata-oepnv.de.

## 5. Files

- **FILE-001**: `plan/migration-braunschweig-1.md` (this plan).
- **FILE-002**: `config_local_braunschweig.yml` (gitignored).
- **FILE-003**: `braunschweig/**` package.
- **FILE-004**: `braunschweig/data/census/{population,employment,employees,licenses}.py`.
- **FILE-005**: `braunschweig/data/buildings.py`.
- **FILE-006**: `braunschweig/data/population/raw.py`.
- **FILE-007**: `scripts/verify_braunschweig_inputs.py`.
- **FILE-008**: `eqasim-data/DOWNLOAD_CHECKLIST_BS.md`.
- **FILE-009**: `.gitignore` (modified).
- **FILE-010**: `bavaria/**` (untouched reference).

## 6. Testing

- **TEST-001**: `python -c "import braunschweig.data.census.population, braunschweig.data.census.employment, braunschweig.data.census.employees, braunschweig.data.census.licenses, braunschweig.data.buildings, braunschweig.data.population.raw"` succeeds.
- **TEST-002**: `python scripts/verify_braunschweig_inputs.py --matsim` all [OK].
- **TEST-003**: `python -m synpp config_local_braunschweig.yml` with only `synthesis.output` completes; `persons.csv` ≥1000 rows.
- **TEST-004**: Marginal fit vs. GENESIS 12111-0001 L1 error <5 %.
- **TEST-005**: End-to-end pipeline (`synthesis.output` + `matsim.output`) completes; `simulation_output/logfile.log` ends with `shutdown completed.`.
- **TEST-006**: All home points in `braunschweig_1pct_homes.gpkg` inside 031 polygon.
- **TEST-007**: Bavaria regression — re-run `config_local_bavaria.yml` synthesis.output → 100 % cache hits.

## 7. Risks & Assumptions

- **RISK-001**: GENESIS age bins differ from Bavaria's 13-bin schema → loader adaptation in TASK-019.
- **RISK-002**: LSN Pendler at Gemeinde level not public → Kreis-level fallback.
- **RISK-003**: HU-NI volume large → BBox filter to 031 before persistence.
- **RISK-004**: Gravity parameters IDF-inherited → likely suboptimal for ZGB; future work.
- **RISK-005**: ENTD cultural mismatch (FR shopping hours, school times) — explicitly accepted.
- **RISK-006**: Long Windows paths on `eqasim-java` clone in `cache_bs` — covered by `core.longpaths=true`.
- **RISK-007**: Deutschland-GTFS >1 GB → pre-filter by bbox.
- **ASSUMPTION-001**: 1 % @ 8 processes sufficient for first run (~8 700 persons).
- **ASSUMPTION-002**: `bavaria.entd_codes` whitelist region-agnostic.
- **ASSUMPTION-003**: All `031xx` Kreise present in KBA FE4.4 individually.

## 8. Related Specifications / Further Reading

- BKG VG250: https://gdz.bkg.bund.de
- GENESIS-Online / Regionaldatenbank: https://www.regionalstatistik.de
- LSN: https://www.statistik.niedersachsen.de
- LGLN OpenGeoData: https://opengeodata.lgln.niedersachsen.de
- Geofabrik NI: https://download.geofabrik.de/europe/germany/niedersachsen.html
- KBA FE4: https://www.kba.de
- Delfi GTFS: https://www.opendata-oepnv.de
- ZGB: https://www.zgb.de
- BA Pendleratlas: https://statistik.arbeitsagentur.de
