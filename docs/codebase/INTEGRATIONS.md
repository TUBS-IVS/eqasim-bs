# INTEGRATIONS

> **Staleness note (2026-06-26):** reflects the 2026-06-08 state. Added since:
> building-activity-potentials parquet (from the external `TUBS-IVS/Activities-and-
> Potentials-Calculation-Pipeline` repo), and the `cache_share` shared stage store.
> External run server: a Linux box (connection details in memory `server-deployment.md`, not committed).

External dependencies of `eqasim-bs`. This is a **file-based, offline pipeline**:
there are no live application APIs or databases at runtime. Integrations are
(1) statistical/geospatial **input datasets** downloaded once into
`eqasim-data/data/`, and (2) the **eqasim/MATSim Java toolchain** invoked for the
`matsim.output` stage. The authoritative inventory with target paths, source URLs
and licences is `eqasim-data/DOWNLOAD_CHECKLIST_BS.md`; this file summarises it.

## Subprocess toolchains

- **eqasim/MATSim Java** (`matsim.output` stage) — unchanged.
- **PopulationSim via `uv`** (popsim branch): `braunschweig/popsim/batch.py`
  spawns `uv run populationsim -w <batch-folder>` per 1-km-atomic batch in a
  thread pool; inputs/outputs are plain CSV/YAML folders. Config keys:
  `popsimprep_dir`, `uv_path`, `controls_path`, `settings_path`, `logging_path`.
- **Restricted local-only inputs** (never committed): MiD 2023 raw CSVs
  (`mid_raw_path`, required ONLY for `popsim_mid`), Zensus-2022 100 m/1 km cell
  parquets (`cells_100m_path`, `cells_1km_path`).

## No live services

- No databases (no `psycopg`, `mysql`, `mongoose`, `prisma`, etc. in
  `environment.yml`). On-disk formats only: CSV, XLSX/XLS, Parquet, GeoPackage,
  Shapefile, OSM PBF, GTFS zip.
- No auth/secrets: no `.env.example`/`.env.template` exists (scan). A few
  `scripts/download_*.py` fetch open data over HTTP (`requests`), and the
  school geocoder uses OSM **Nominatim** (1 req/s, cached) per
  `eqasim-data/data/braunschweig/schools/README.md`. These run during one-off
  preprocessing, not in the synpp DAG.

## Input data sources (from DOWNLOAD_CHECKLIST_BS.md)

### A. Federal (shared)
- **BKG VG250-EW** administrative boundaries with population (dl-de/by-2-0).
- **KBA Fahrerlaubnisbestand FE4** driving licences by Bundesland.
- **ENTD 2008** French HTS — reused as the activity-chain/travel-pattern donor
  (no German HTS replacement yet; a known limitation).

### B. Niedersachsen / Braunschweig statistical
- **DESTATIS / GENESIS** 12411-0018 population; 13111-06-02-4 (employees by
  residence); 13111-01-03-5 (employees at workplace).
- **urbistat.com** scraped Gemeinde-level age shares (non-redistributable terms).
- **BA (Bundesagentur für Arbeit)** gemband-dlk (employees by Wirtschaftsabteilung)
  and **BA Pendleratlas 2025** Ein-/Auspendler CSVs (gravity calibration target).
- **Zensus 2022** households 5000H-2001 (Gemeinde × size × type) and the
  **100 m population grid** (auto-downloaded via `scripts/download_zensus_grid.py`,
  source `github.com/JsLth/z22data`).
- **BBSR INKAR** Haushaltseinkommen (Kreis × year) + optional full panel.
- **MiD 2023 Großraum Braunschweig** regional table volume (PDF, BMDV
  **non-commercial**); extracted CSVs via `scripts/extract_mid_tables.py`.
- **BMV/BBSR RegioStaR-7** reference (auto-downloaded via
  `scripts/download_regiostar.py`).
- **Mikrozensus 2024** (DESTATIS 12251-* tables) — school-distance benchmark for
  the BBS education level.

### C. Preprocessed geospatial (LGLN + OSM)
- **ALKIS Hausumringe Niedersachsen** (LGLN, dl-de/zero-2-0) -> `alkis_buildings.parquet`
  via `scripts/preprocess_alkis_landuse.py`.
- **ATKIS Basis-DLM landuse** (LGLN) -> `landuse.parquet`.
- **OSM Niedersachsen PBF** (Geofabrik, ODbL 1.0) -> `osm_pois.parquet` via
  `scripts/preprocess_osm_pois.py`.

### D. MATSim-only
- **OSM Niedersachsen PBF** (same file as C3) — network build.
- **GTFS Deutschland (Delfi) or ZGB feeds** — transit schedule (pre-clip to ZGB bbox).
- **VRB tariff zones** (`vrb/stations.json`, built by
  `scripts/build_vrb_stations_json.py`) consumed by `braunschweig.data.vrb.zones`
  (analogue of the upstream MVG zone stage) and the Java fare module.

### E. Education capacity (only when `education_gravity_enabled` / calibrating)
- **LSN Schulverzeichnis** (allgemeinbildende + berufsbildende Schulen) ->
  `nds_schools_zgb.csv` via `scripts/extract_nds_schools.py`.
- **LSN Kindertageseinrichtungen** Plätze -> `nds_kitas_zgb.csv`.
- **LSN + Magdeburg Hochschule** enrollment -> `nds_hochschulen.csv`
  (`scripts/seed_nds_hochschulen.py`); national target from **Destatis MZ 2024
  Hochschule** and DESTATIS Hochschulstatistik 21311-0007.

## eqasim / MATSim Java toolchain

The `matsim.output` stage builds a MATSim scenario using the **eqasim Java**
project and **pt2matsim** (HAFAS/GTFS converter). synpp downloads and caches both
as nested git checkouts under `eqasim-data/cache_*/matsim.runtime.*` (gitignored;
AGENTS.md). CI provisions Java 17 (Corretto) + Maven + osmosis
(`.github/workflows/tests.yml`). Local runs require `osmosis_binary` and
`osmconvert_binary` (configured per-machine in the run config). The Java package
namespace stays `org.eqasim.bavaria.*` and is read-only from this repo (AGENTS.md
D-1c).

## Licences (re-distribution constraints)

dl-de/by-2-0, dl-de/zero-2-0 (LGLN), ODbL 1.0 (OSM), BA terms, urbistat terms
(non-redistributable), and **BMDV non-commercial** for MiD 2023 — the most
restrictive: any output depending on MiD 2023 inherits non-commercial use
(README "License"; DOWNLOAD_CHECKLIST_BS.md notes).

## Evidence

- `eqasim-data/DOWNLOAD_CHECKLIST_BS.md` (sections A–E, bounding box, licence notes)
- `configs/fixtures/config_local_braunschweig.yml` (input paths, `vrb_stations_path`, `osmosis_binary`)
- `environment.yml` (no DB/HTTP-service client libs)
- `.github/workflows/tests.yml` (Java 17 + Maven + osmosis)
- `eqasim-data/data/braunschweig/schools/README.md` (Nominatim geocoding)
- `README.md` ("Input data", "License")

---

## Cross-repo addendum: PopulationSim + popsimprep inputs

Added 2026-06-08. The `popsim_open`/`popsim_mid` workflows add one external tool
and three new input datasets.

### New tool integration: PopulationSim (subprocess)

- **PopulationSim ≥ 0.10.0** invoked as a CLI: `uv run populationsim -w <folder>`
  per batch, wrapped by `popsimprep/batch_run_popsim.py` (thread pool, default 3
  workers, 1 h timeout per batch). It reads a PopSim folder
  (`data/ configs/ output/`) and writes `output/final_expanded_household_ids.csv`
  + synthetic_households/persons. Mirrors eqasim-bs's existing pattern of shelling
  out to an external toolchain (Java/osmosis). Evidence:
  `popsimprep/batch_run_popsim.py:285`, `popsimprep/popsim/configs/settings.yaml`.

### New input datasets

1. **Zensus 2022 cell grids (preprocessed, local-only, ~7.7 GB total)** —
   `popsimprep/inputs/cells_1km_with_binneds.parquet` (212,758 × 348) and
   `cells_100m_with_gender_backf_binneds_happyorphans.parquet` (3,148,482 × 536).
   German INSPIRE grid, **EPSG:3035**, no geometry column (coords encoded in the
   `CRS3035RES…N…E…` id). Carry the binned control marginals (age×sex, HH size,
   nationality, heating, dwellings, …) + a gender backfill + `scale`/`_adj`
   rescale columns + `is_orphan` flag. These become the **PopulationSim control
   totals** and the spatial backbone. Open-data provenance (Zensus 2022) but the
   files are large and gitignored. `[ASK USER]`: exact provenance + whether the
   "backfill/happyorphans" preprocessing is documented/reproducible.
2. **MiD 2023 RAW microdata (RESTRICTED — `popsim_mid` only)** —
   `popsimprep/inputs/MiD2023/` (B1 Standard dataset package: Haushalte, Personen,
   Wege, Etappen, Autos, Reisen, Tagesreisen as CSV + SPSS). **BMDV scientific-use
   file — must never be copied into the repo or committed.** The notebook consumes
   `MiD2023_Haushalte.csv`, `MiD2023_Personen.csv`, `MiD2023_Wege.csv` as the
   PopulationSim **seed** (households/persons) and the trip source. This is a
   stronger dependency than eqasim-bs's current use: eqasim-bs uses only **derived
   MiD aggregate CSVs** (committed reference tables); `popsim_mid` uses the **raw
   row-level survey**. The path must be config/env-driven and local-only.
3. **Open seed for `popsim_open`** — per the brief, the open French data is the
   public PopulationSim seed so external users can run the workflow without MiD.
   `[TODO]`: identify which open seed file(s) and where they live; not present in
   popsimprep inputs today (the notebook currently hard-wires MiD seeds).

### Data-safety status (popsimprep `.gitignore`)

`popsimprep/.gitignore` ignores `inputs/` (so MiD raw + both parquets are safe),
plus `popsim/data/`, `popsim/output/`, `popsim_regiostar_*/`, `popsim_combined/`.
**GAPS:** the Step 5/6 outputs `buildings_with_assigned_households.gpkg`,
`buildings_with_mid_data.gpkg` (contains per-building JSON blobs of MiD household +
person attributes — MiD-derived microdata), `wege_temp_chunks/`, and
`validation_results/` are **NOT ignored** and would be committed if `git add`-ed.
`popsimprep/scripts/verify.py` also hard-codes a foreign absolute path
(`C:\Users\<developer>\...`). See CONCERNS.md.

### Licence escalation

`popsim_mid` outputs inherit **MiD 2023 BMDV scientific-use terms** (more
restrictive than the non-commercial aggregate tables eqasim-bs already uses). The
open workflows (`simple_ipf_open`, `popsim_open`) must remain free of raw-MiD
provenance so they can be shared.

Evidence: `popsimprep/.gitignore`, `popsimprep/inputs/` listing (names/sizes only),
`popsimprep/PopSimPrep-StartHere-v2.ipynb` (Steps 3 & 6 read MiD CSVs),
`popsimprep/popsim/configs/settings.yaml`, `popsimprep/scripts/verify.py`.
