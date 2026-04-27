# External Integrations

## Core Sections (Required)

### 1) Integration Inventory

| System | Type | Purpose | Auth model | Criticality | Evidence |
|--------|------|---------|------------|-------------|----------|
| **Zensus 2022 (5000H-2001, 1000A-2081, 1000A-3082, 100m grid)** | File API / Direct download | Household-size, employment, population, household-type marginals by Gemeinde + 100m grid counts | Public (no auth) | **HIGH** | [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L50-L60), [braunschweig/data/census/employment.py](braunschweig/data/census/employment.py) |
| **BA Pendleratlas 2025** | Excel / CSV | Kreis-pair SvB commuter flows (inter-Kreis OD reference for gravity calibration) | Public (no auth) | **HIGH** | [braunschweig/data/ba/pendler_detailed.py](braunschweig/data/ba/pendler_detailed.py) |
| **MiD 2023 Großraum Braunschweig (infas sample 7555)** | CSV export from infas database | Trip chains, commute distance distribution (P13), mode share, trip purpose, activity duration (used in post-synthesis override) | Restricted (infas sample 7555 = Großraum BS only; larger samples available via purchase) | **HIGH** | [braunschweig/data/mid/data.py](braunschweig/data/mid/data.py), [braunschweig/synthesis/spatial/commute_distance.py](braunschweig/synthesis/spatial/commute_distance.py#L20) |
| **INKAR full panel (Indikatoren und Karten zur Raum- und Stadtentwicklung)** | Excel XLS | Household income by Gemeinde (linked to household-size bins via income-size map) | Public (no auth, downloadable via BBSR) | **MEDIUM** | [braunschweig/data/inkar/household_income.py](braunschweig/data/inkar/household_income.py#L77-L80) |
| **BBSR RegioStaR-7** | CSV / API | Regional classification (urban/rural) for Gemeinden | Public (no auth) | **MEDIUM** | [TODO] confirm if used; search codebase for RegioStaR |
| **ALKIS Hausumringe (LGLN Niedersachsen)** | GeoPackage / Shapefile | Building polygons + residential/commercial flags for location sampling | Public (no auth, downloadable from Geobasis Niedersachsen) | **HIGH** | [braunschweig/data/buildings.py](braunschweig/data/buildings.py) |
| **ATKIS Landuse / INSPIRE FS_LN_03** | GeoPackage | Landuse categories (agricultural, forest, urban) for POI filtering and activity context | Public (no auth) | **MEDIUM** | [braunschweig/data/landuse.py](braunschweig/data/landuse.py) |
| **OSM Niedersachsen PBF** | PBF (OsmPBF format) | POI (shops, schools, workplaces, healthcare, leisure) for location candidates and employment distribution | Public (no auth, downloadable from OSM Geofabrik) | **HIGH** | [braunschweig/data/osm.py](braunschweig/data/osm.py) |
| **GTFS feed (Braunschweig transit)** | GTFS zip | Transit schedule (routes, stops, timetables) — used by MATSim for mode-choice routing. **Read-only in Python; consumed by Java MATSim.** | Public (no auth, available from VBB or Braunschweig transport authority) | **MEDIUM** | [TODO] confirm GTFS path in config; search for gtfs in codebase |
| **MATSim simulation engine (eqasim-java classes, org.eqasim.bavaria.*)** | Java subprocess via `subprocess.run()` | Agent-based transport simulation; scenario preparation, population routing, mode-choice. **Read-only in this cycle.** | Internal (compiled JAR in cache) | **HIGH** | [matsim/scenario/population.py](matsim/scenario/population.py), [matsim/simulation/prepare.py](matsim/simulation/prepare.py) |

### 2) Data Stores

| Store | Role | Access layer | Key risk | Evidence |
|-------|------|--------------|----------|----------|
| **eqasim-data/data/ (local file tree)** | Input data staging (CSVs, GeoPackages, Excel, 7z archives). Synpp reads from here. | Each loader module directly reads files via `pd.read_csv()`, `gpd.read_file()`, etc. | File encoding errors (BUG-006: UTF-8 not specified in ZIP reads), missing data (Zensus suppressed cells → `-` → NaN), malformed numbers (INKAR `"N.A."` → coerce errors per BUG-007) | [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L50-L65) |
| **eqasim-data/cache_bs[_10pct,_25pct]/ (content-hashed DAG cache)** | Synpp-managed stage outputs. Keyed by content hash of input data + config + stage code. Re-runs only if upstream changes. | Synpp reads/writes via pickle + directory structure `stage_name.hash/output.cache` | Cache invalidation cascade (if one input file changes, all downstream stages re-run; 10% run = ~4 hours on laptop). Cache corruption if power-loss during write. | [config_local_braunschweig.yml](config_local_braunschweig.yml#L3) `working_directory: eqasim-data/cache_bs` |
| **eqasim-data/output_bs[_10pct,_25pct]/ (synthesis output)** | Final CSV + GeoPackage + MATSim XML. Written once, not updated by subsequent runs. | Python stage `matsim.output` writes `population.xml.gz`, `synthesis.output` writes CSV. | Disk space (10% output ≈ 1 GB; 25% output ≈ 4 GB). Directory not cleaned between runs if re-running at same sampling rate. | [config_local_braunschweig.yml](config_local_braunschweig.yml#L4) `output_path: eqasim-data/output_bs` |

### 3) Secrets and Credentials Handling

- **Credential sources**: None required for public data sources (Zensus, BA Pendler, ALKIS, OSM, GTFS all public). MiD 2023 access is via local CSV export (not API-based).
- **Hardcoding checks**: No API keys, passwords, or tokens in code. All data sources are file-based (CSV/GeoPackage) or public downloads.
- **Rotation or lifecycle notes**: Not applicable (no credentials to rotate).

### 4) Reliability and Failure Behavior

- **Retry/backoff behavior**: Not implemented. All data sources are local files (no network I/O). If file missing, stage raises error immediately.
- **Timeout policy**: Not applicable (no network calls).
- **Circuit-breaker or fallback behavior**: 
  - If Zensus HH-size row is missing for a Gemeinde, [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L70) raises `RuntimeError` (no fallback; data is mandatory).
  - If external workplace file missing, [braunschweig/data/external_workplaces.py](braunschweig/data/external_workplaces.py) — [TODO] verify behavior (return empty DataFrame or raise?).
  - If INKAR income file is malformed (all values `"N.A."`), [braunschweig/data/inkar/household_income.py](braunschweig/data/inkar/household_income.py#L77-L80) silently returns empty DataFrame → downstream `.map()` produces all-NaN income (BUG-007). Fix: add post-dropna assertion.

### 5) Observability for Integrations

- **Logging around external calls**: None. Stage functions print() for diagnostics (disabled by default in synpp runs).
- **Metrics/tracing coverage**: None. No APM or tracer instrumentation.
- **Missing visibility gaps**: 
  - No audit log of which data sources were loaded + version/date stamps. Suggested: log file hash + row count at stage entry.
  - No performance metrics for file reads (I/O time, parse time).
  - No validation metrics (row count before/after filtering, % of cells with missing data).

### 6) Evidence

- [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L50) — Zensus 5000H-2001 loader
- [braunschweig/data/ba/pendler_detailed.py](braunschweig/data/ba/pendler_detailed.py) — BA Pendleratlas loader
- [braunschweig/data/mid/data.py](braunschweig/data/mid/data.py) — MiD CSV loader
- [braunschweig/data/inkar/household_income.py](braunschweig/data/inkar/household_income.py) — INKAR loader
- [braunschweig/data/buildings.py](braunschweig/data/buildings.py) — ALKIS loader
- [braunschweig/data/osm.py](braunschweig/data/osm.py) — OSM PBF loader
- [config_local_braunschweig.yml](config_local_braunschweig.yml) — data_path / output_path config

## Extended Sections (Optional)

### Data Source Checklist (Refactor Phase 3 Target)

Each source should be documented in README.md with:
- **Name**: Official name + acronym
- **URL**: Download link or data portal
- **Version/date**: Current snapshot (e.g. "Zensus 2022", "BA Pendleratlas 2025")
- **License**: Reuse rights (e.g. "Public Domain (dl-de/by-2-0)")
- **Preprocessing**: Any transformation steps before use (e.g. "unzip, filter ARS to ZGB-8, drop NaN rows")
- **Cache location**: Where it lives in eqasim-data/data/

---
