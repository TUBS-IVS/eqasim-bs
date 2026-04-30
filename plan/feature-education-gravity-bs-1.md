---
goal: Replace shared work/education gravity with a Braunschweig-specific education gravity model based on open-source education-capacity data (LSN, DESTATIS Hochschulstatistik, OSM) and MiD 2023 distance/sub-purpose tables
version: 1.0
date_created: 2026-04-29
last_updated: 2026-04-29
owner: eqasim-bs / Braunschweig modelling
status: 'Planned'
tags: [feature, gravity, education, calibration, mid2023, post-refactor]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

Today `braunschweig.gravity.model` returns the same OD matrix for both
`work` and `education` (`return df_matrix, df_matrix`). Education trips
are therefore distributed proportional to the *number of employees* per
Gemeinde and decay with the same Île-de-France-derived friction as work
commutes. Bavaria does this identically — there is no upstream template
for a separate education gravity. This plan replaces the shared model
with a dedicated `df_education_od` driven by per-Gemeinde **education
capacity** (Schülerzahlen, Studierendenzahlen) and calibrated against
**MiD 2023 W12** (Weglänge × Wegezweck `Ausbildung`) for ZGB-8.

The plan strictly post-dates the current refactor (Decision D-5: no
behaviour changes during Phase 0..4). It is implemented on a feature
branch off the post-refactor main and gated behind the config flag
`gravity_education_separate` (default `false`) so existing baselines are
not invalidated implicitly.

## 1. Requirements & Constraints

- **REQ-001**: A new synpp stage `braunschweig.data.education.capacity` produces a per-Gemeinde DataFrame with columns `commune_id, capacity_primary, capacity_secondary, capacity_tertiary, capacity_total` (float, head counts).
- **REQ-002**: A new synpp stage `braunschweig.gravity.education_model` produces `df_education_od` with the same schema as the existing work OD: `origin_id (8-digit AGS), destination_id, weight (row-normalised P(d|o))`.
- **REQ-003**: The existing stage `braunschweig.gravity.model` continues to produce a tuple `(df_work_od, df_education_od)`; when `gravity_education_separate=true` the second element is loaded from `braunschweig.gravity.education_model`, otherwise it remains the legacy work-OD copy.
- **REQ-004**: Education gravity uses its own `gravity_education_slope` config (default empirically calibrated, expected magnitude `-0.20 … -0.40`, steeper than work).
- **REQ-005**: Calibration target is the MiD 2023 W12 distance distribution for trip purpose `Ausbildung`, ZGB total. Acceptance: synth distribution L1 distance ≤ 0.10 vs. MiD W12 bins.
- **REQ-006**: Mode-share for `education` trips must remain ≥ 99 % unchanged versus the legacy run when `gravity_education_separate=false` (i.e. flag is fully reversible).
- **REQ-007**: Education capacity per commune must be reproducible from publicly downloadable, license-compatible sources only. No paid datasets, no datasets requiring NDA.
- **DAT-001**: Open-source data sources — strict allow-list:
  - Niedersächsisches Landesamt für Statistik (LSN) GENESIS-Online — Schulstatistik per Gemeinde (Tabellen K3300101, K3320101 — Schülerinnen und Schüler nach Schulform und Gemeinde). Annual, free, Datenlizenz Deutschland 2.0 Namensnennung.
  - DESTATIS Hochschulstatistik — Studierende pro Hochschulort (Tabelle 21311-0001). Free, official statistics.
  - Hochschulkompass (HRK) — list of higher-education institutions including ZGB locations (TU Braunschweig, HBK Braunschweig, Ostfalia HaW Wolfenbüttel/Salzgitter/Wolfsburg/Suderburg).
  - OpenStreetMap — `amenity in {school, kindergarten, university, college}` as cross-check for spatial coverage. ODbL 1.0.
- **DAT-002**: MiD 2023 — Großraum Braunschweig, infas project 7555. Tables W6 (Detailwegezwecke, p245-250), W12 (Weglänge, p275-276). Already in repo as PDF.
- **CON-001**: ZGB-8 scope (8 Kreise, AGS prefixes 03101..03158). External Kreise (Niedersachsen + outliers) are NOT modelled — students residing in ZGB but studying outside (e.g. Hannover, Magdeburg) are folded into a single `external` destination weighted by DESTATIS `Studierende nach Herkunfts-Kreis`. Out of scope for v1: detailed per-Kreis external destinations.
- **CON-002**: No bug fixes during the refactor (D-5). This plan begins after the refactor branch lands on main.
- **CON-003**: Behaviour-preserving default: feature flag off ⇒ identical numerical output. Verified by 1 % smoke baseline regression test.
- **CON-004**: All comments / docstrings English (CLAUDE.md). Reference table CSV column names snake_case.
- **GUD-001**: All numerical reference values from MiD live in CSV files under `eqasim-data/data/braunschweig/mid/`, not Python literals. Loader pattern via `braunschweig.data.mid.reference_tables` (existing).
- **GUD-002**: New stages follow existing dotted-module naming and are wired through `config_local_braunschweig*.yml` aliases.
- **PAT-001**: Reuse `eqasim_common.gravity.distance_matrix` and `evaluate_gravity()` — no parallel implementation.
- **PAT-002**: Reuse `_build_origin_slope_vector()` for per-RegioStaR-7 differentiation of education slope (younger pupils ⇒ even shorter trips than urban workers).

## 2. Implementation Steps

### Implementation Phase 0 — Acquire & freeze open-source education data

- GOAL-000: Download, version-pin and document all open-source education-capacity inputs for ZGB-8 + Niedersachsen so subsequent stages have a stable, reproducible base. No model code written in this phase.

| Task     | Description | Completed | Date |
|----------|-------------|-----------|------|
| TASK-001 | Add `scripts/download_lsn_schulen.py`. Pulls LSN GENESIS-Online table `K3300101` (Schüler je Schule, Schulform × Gemeinde) and `K3320101` (allgemeinbildende Schulen) via the open SOAP/REST API. Writes raw JSON + flattened CSV to `eqasim-data/data/braunschweig/lsn/lsn_schulen_<jahr>.csv`. Year defaults to latest available (2023/24 at time of writing). |  |  |
| TASK-002 | Add `scripts/download_destatis_hochschulen.py`. Pulls DESTATIS Hochschulstatistik table `21311-0001` (Studierende nach Hochschulort × Semester). Filters to Niedersachsen Hochschulen. Writes `eqasim-data/data/braunschweig/destatis/hochschulen_<jahr>.csv`. |  |  |
| TASK-003 | Manually compile (one-off) `eqasim-data/data/braunschweig/education/hochschul_orte_zgb.csv` mapping each ZGB Hochschule to commune_id (8-digit AGS): TU BS → 03101000, HBK BS → 03101000, Ostfalia Wolfenbüttel → 03158021, Ostfalia Wolfsburg → 03103000, Ostfalia Salzgitter → 03102000. Source: Hochschulkompass HRK (cite URL in CSV header). |  |  |
| TASK-004 | Extend `scripts/preprocess_osm_pois.py` to also emit `eqasim-data/data/braunschweig/osm/osm_education_pois.gpkg` (amenity ∈ {kindergarten, school, college, university}). Used as fallback / cross-check for Phase 1 capacity allocation, not as primary signal. |  |  |
| TASK-005 | Add provenance section to `eqasim-data/DOWNLOAD_CHECKLIST_BS.md` listing all four new datasets, retrieval URLs/API endpoints, license, file hashes (SHA-256). |  |  |

### Implementation Phase 1 — `braunschweig.data.education.capacity` stage

- GOAL-001: Build the per-Gemeinde education-capacity table that replaces `df_employees` as the destination-mass vector for the education gravity model.

| Task     | Description | Completed | Date |
|----------|-------------|-----------|------|
| TASK-006 | Create `braunschweig/data/education/__init__.py` (empty marker). |  |  |
| TASK-007 | Create `braunschweig/data/education/capacity.py` with synpp stage. Configure: `context.config("data_path")`, `context.stage("eqasim_common.spatial.zoning")`. |  |  |
| TASK-008 | In `capacity.py`, implement `_load_lsn_schulen(data_path)` returning long DataFrame `[commune_id, schulform, schueler]` from `lsn/lsn_schulen_*.csv`. Validate schema; raise `ValueError` if `schulform` not in expected set. |  |  |
| TASK-009 | Implement `_load_hochschulen(data_path)` returning `[commune_id, hochschule, studierende]` joined from `destatis/hochschulen_*.csv` × `education/hochschul_orte_zgb.csv`. |  |  |
| TASK-010 | Implement `_aggregate_to_capacity(df_lsn, df_hs)` returning per-commune `[commune_id, capacity_primary, capacity_secondary, capacity_tertiary, capacity_total]`. Schulform mapping: `Grundschule, Förderschule (Klasse 1-4)` → primary; `Hauptschule, Realschule, Oberschule, Gymnasium, Gesamtschule, Sek I/II Förderschule, Berufsschule` → secondary; `Studierende` → tertiary. Document mapping in module docstring with citation to LSN Schulformsystematik. |  |  |
| TASK-011 | Implement `execute(context)` that returns the aggregated DataFrame. Add log line summing `capacity_total` per Kreis and asserting `≥ 100 000` for ZGB-8 total (sanity check; ZGB has ~150k Schüler + ~40k Studierende). |  |  |
| TASK-012 | Add `tests/test_education_capacity.py` with synthetic LSN/Hochschulen fixtures and assertions on schulform mapping, totals per Kreis, and absence of negative values. |  |  |

### Implementation Phase 2 — MiD W12 + W6 reference tables

- GOAL-002: Extract empirical distance distributions per trip purpose from MiD 2023 to use as calibration target for the education slope. Follow the existing CSV-first pattern (CLAUDE.md GUD-001).

| Task     | Description | Completed | Date |
|----------|-------------|-----------|------|
| TASK-013 | Extend `scripts/extract_mid_tables.py` `SPECS` list with `TableSpec("W12", 275, 276, [<distance_class_columns>])`. Distance classes per MiD W12 header: `<0.5, 0.5-1, 1-2, 2-5, 5-10, 10-20, 20-50, 50-100, ≥100` (km). Run extractor → produces `eqasim-data/data/braunschweig/mid/mid2023_W12.csv` keyed by `kreis, ars5, n_weighted, n_unweighted, <bins>` with one Gesamt row + per-purpose rows. |  |  |
| TASK-014 | Add `TableSpec("W6_1", 245, 246, [...])` etc. for the 3 sub-pages of W6 → `mid2023_W6.csv` (Detailwegezwecke). Used to split education into school-pupil vs. higher-education (informational; not yet wired into gravity in v1). |  |  |
| TASK-015 | Add `EDUCATION_W12_DISTRIBUTION` loader in `braunschweig.data.mid.reference_tables`. Returns dict `{distance_class: share}` for W12 row `Hauptwegezweck = Ausbildung, Gesamt`. Tested against pinned values via `tests/test_mid_reference_tables.py`. |  |  |
| TASK-016 | Document W6/W12 entries in `CLAUDE.md` reference-table block (existing pattern). |  |  |

### Implementation Phase 3 — `braunschweig.gravity.education_model` stage

- GOAL-003: Implement the dedicated education gravity model and gate it behind a config flag.

| Task     | Description | Completed | Date |
|----------|-------------|-----------|------|
| TASK-017 | Create `braunschweig/gravity/education_model.py`. Configure stages: `eqasim_common.gravity.distance_matrix`, `braunschweig.ipf.attributed`, `braunschweig.data.education.capacity`, `braunschweig.data.bbsr.regiostar`. Configure params: `gravity_education_slope` (default `-0.30`), `gravity_education_constant` (default `-2.4`), `gravity_education_diagonal` (default `1.0`), `gravity_education_slope_by_regiostar7` (default `{}`). |  |  |
| TASK-018 | Implement `execute(context)` that mirrors `_execute_gravity_base` but with `population` weights restricted to persons with `age < 30` proxy (or `is_student` if available in `df_population`), and `attractors` = `capacity_total` from Phase 1. Reuse `evaluate_gravity()` and `_build_origin_slope_vector()` from `braunschweig.gravity.model`. |  |  |
| TASK-019 | Modify `braunschweig.gravity.model.execute(context)` to add `context.config("gravity_education_separate", False)`. When true: `df_education_od = context.stage("braunschweig.gravity.education_model")`. When false: keep current behaviour (`return df_work_extended, df_education_od` where the second is the legacy copy). |  |  |
| TASK-020 | Wire stage into `config_local_braunschweig.yml`, `_10pct.yml`, `_25pct.yml` aliases section (commented-out by default with a `# enable for education-aware runs` note). Add to `config_gravity_only_braunschweig.yml` for fast iteration. |  |  |
| TASK-021 | Add `tests/test_education_model.py`: synthetic 3-Gemeinde scenario, assert row-normalised weights sum to 1.0, assert education slope ≠ work slope when separate flag is on, assert flag-off identity vs. legacy. |  |  |

### Implementation Phase 4 — Calibration & validation harness

- GOAL-004: Calibrate `gravity_education_slope` against MiD W12 and surface education-specific KPIs in the validation harness.

| Task     | Description | Completed | Date |
|----------|-------------|-----------|------|
| TASK-022 | Create `scripts/calibrate_education_slope.py` analogous to `scripts/calibrate_gravity_decay.py`. Sweeps slope in `{-0.10, -0.15, -0.20, -0.25, -0.30, -0.35, -0.40}`, runs `config_gravity_only_braunschweig.yml` with `gravity_education_separate=true` for each, computes synth education-trip distance distribution, picks slope minimising L1 distance to `EDUCATION_W12_DISTRIBUTION`. Writes report to `plan/baselines/education_slope_calibration.md`. |  |  |
| TASK-023 | Pin chosen slope back into module default (`DEFAULT_EDUCATION_SLOPE = ...`) and into all 1pct/10pct/25pct configs. Add commit message convention `Calibrate education slope to <value> against MiD W12`. |  |  |
| TASK-024 | Extend `scripts/validate_bs_10pct/metrics.py`: new function `education_distance_distribution()` that filters `df_trips["following_purpose"] == "education"` and computes the same MiD W12 bins. Compare against `EDUCATION_W12_DISTRIBUTION`. Add to `report.py` section 5.5. |  |  |
| TASK-025 | Extend `scripts/validate_bs_10pct/metrics.py`: new function `purpose_mix_no_home()` already exists — verify the `education` row deviation tightens after the change (acceptance: |Δ| ≤ 2 pp post-calibration). |  |  |
| TASK-026 | Re-run 1 % smoke and 10 % validation with flag on/off; record both baselines under `plan/baselines/edu_gravity_on.txt` and `edu_gravity_off.txt`; verify off-state matches the existing pre-feature baseline byte-for-byte (or document any noise from RNG). |  |  |

## 3. Alternatives

- **ALT-001**: Keep the shared work/education gravity (status quo). Rejected — empirically the synth education share is +4.3 pp above MiD W1 and the distance distribution is biased toward employment centres rather than schools.
- **ALT-002**: Use OSM `amenity=school|university` POI counts as the only attractor signal. Rejected — POI counts do not reflect *capacity* (Stadtgymnasium with 1500 pupils ≠ Dorfschule with 80). LSN GENESIS provides actual head counts and is authoritative.
- **ALT-003**: Use the MATSim secondary-locations module (existing) to relocate education activities post-hoc. Rejected — that module operates after the gravity step and inherits its bias; fixing the destination distribution at gravity time is more principled.
- **ALT-004**: Borrow the Île-de-France education gravity parameters directly from upstream eqasim. Rejected — same concern as ALT-001 (region-mismatched), and IDF parameters were never split between work and education there either.
- **ALT-005**: Build a full agent-based school-assignment (assign each pupil to nearest school respecting capacity). Rejected for v1 — disproportionate complexity for a regional-scale travel-demand model. Could be a future v2.

## 4. Dependencies

- **DEP-001**: LSN GENESIS-Online API (https://www1.nls.niedersachsen.de/statistik/) — public, requires only registration for higher quotas.
- **DEP-002**: DESTATIS GENESIS-Online — public REST API for table 21311-0001.
- **DEP-003**: Existing stages `eqasim_common.gravity.distance_matrix`, `braunschweig.ipf.attributed`, `braunschweig.data.bbsr.regiostar`.
- **DEP-004**: MiD 2023 PDF (already vendored under `eqasim-data/data/braunschweig/`).
- **DEP-005**: Python packages already in `environment.yml`: `pandas`, `numpy`, `pdfplumber`, `requests`, `geopandas` (for OSM cross-check). No new dependencies.
- **DEP-006**: This plan must NOT start before the eqasim-bs refactor branch (`refactor/braunschweig-clean-fork`) is merged to main. Ref: AGENTS.md D-5.

## 5. Files

- **FILE-001**: `scripts/download_lsn_schulen.py` (new) — LSN GENESIS downloader.
- **FILE-002**: `scripts/download_destatis_hochschulen.py` (new) — DESTATIS Hochschulstatistik downloader.
- **FILE-003**: `eqasim-data/data/braunschweig/lsn/lsn_schulen_<year>.csv` (new, downloaded).
- **FILE-004**: `eqasim-data/data/braunschweig/destatis/hochschulen_<year>.csv` (new, downloaded).
- **FILE-005**: `eqasim-data/data/braunschweig/education/hochschul_orte_zgb.csv` (new, manually curated).
- **FILE-006**: `eqasim-data/data/braunschweig/osm/osm_education_pois.gpkg` (new, derived).
- **FILE-007**: `eqasim-data/data/braunschweig/mid/mid2023_W12.csv` (new, extracted).
- **FILE-008**: `eqasim-data/data/braunschweig/mid/mid2023_W6.csv` (new, extracted).
- **FILE-009**: `braunschweig/data/education/__init__.py` (new, marker).
- **FILE-010**: `braunschweig/data/education/capacity.py` (new) — synpp stage.
- **FILE-011**: `braunschweig/data/mid/reference_tables.py` (modified) — add W12 / W6 loaders.
- **FILE-012**: `braunschweig/gravity/education_model.py` (new) — new synpp stage.
- **FILE-013**: `braunschweig/gravity/model.py` (modified) — feature flag wiring.
- **FILE-014**: `config_local_braunschweig.yml`, `_10pct.yml`, `_25pct.yml`, `config_gravity_only_braunschweig.yml` (modified) — new params + alias.
- **FILE-015**: `scripts/extract_mid_tables.py` (modified) — W12 / W6 specs.
- **FILE-016**: `scripts/calibrate_education_slope.py` (new) — calibration sweep.
- **FILE-017**: `scripts/validate_bs_10pct/metrics.py`, `report.py` (modified) — education distance KPI.
- **FILE-018**: `tests/test_education_capacity.py` (new).
- **FILE-019**: `tests/test_education_model.py` (new).
- **FILE-020**: `tests/test_mid_reference_tables.py` (modified) — assert W12 loader values.
- **FILE-021**: `plan/baselines/education_slope_calibration.md` (new) — calibration log.
- **FILE-022**: `eqasim-data/DOWNLOAD_CHECKLIST_BS.md` (modified) — provenance.
- **FILE-023**: `CLAUDE.md` (modified) — reference-table doc block extension.

## 6. Testing

- **TEST-001**: `tests/test_education_capacity.py::test_schulform_mapping` — synthetic LSN row per schulform asserts correct primary/secondary bucket.
- **TEST-002**: `tests/test_education_capacity.py::test_zgb_total_sane` — real LSN data, assert `100000 ≤ capacity_total[ZGB] ≤ 250000`.
- **TEST-003**: `tests/test_education_capacity.py::test_no_negative_capacity` — invariant.
- **TEST-004**: `tests/test_education_model.py::test_row_normalised` — output `weight` sums to 1.0 per `origin_id` ± 1e-9.
- **TEST-005**: `tests/test_education_model.py::test_flag_off_identity` — when `gravity_education_separate=false`, `df_education_od` is byte-identical to `df_work_od`.
- **TEST-006**: `tests/test_education_model.py::test_separate_slope_changes_distribution` — when on with steeper slope, mean travel distance of synth education trips strictly decreases vs. flag-off (sanity).
- **TEST-007**: `tests/test_mid_reference_tables.py::test_w12_education_share_loader` — pinned values (parsed from MiD W12 Gesamt × Ausbildung row).
- **TEST-008**: Smoke regression: 1 % run with flag off must reproduce `plan/baselines/smoke_1pct_baseline.txt` exactly (decision D-1c on byte-equality already in force).
- **TEST-009**: 10 % validation with flag on: `purpose_mix_no_home()` `education` row deviation ≤ 2 pp; `education_distance_distribution()` L1 distance to W12 ≤ 0.10.

## 7. Risks & Assumptions

- **RISK-001**: LSN GENESIS API may rate-limit or change endpoints. Mitigation: cache CSV under `eqasim-data/data/braunschweig/lsn/` and check in only the final aggregate (file size ~50 KB), not raw JSON.
- **RISK-002**: DESTATIS Hochschulstatistik tables only count students at study location, not residence. Out-commuting from ZGB to Hannover/Magdeburg therefore needs a separate margin (in-scope as `external` destination only).
- **RISK-003**: MiD W12 sample size for `Ausbildung` rows in ZGB is small (~935 weighted Wege according to W2 — see `mid2023_W2.csv`). Calibration noise floor ≈ ±0.05 in L1. Acceptance threshold 0.10 is set with that in mind.
- **RISK-004**: Per-RegioStaR slope override may overfit (only 8 Kreise × few RS7 codes). Mitigation: keep `gravity_education_slope_by_regiostar7={}` empty by default and only enable after sensitivity testing.
- **RISK-005**: Schools are biased toward residential areas while employees are biased toward CBDs. The gravity model with capacity-weighted destinations should reproduce this — but if `df_population` (which currently sums over *all* persons at origin) dominates, intra-Kreis trips may still be over-distributed. Mitigation: TASK-018 restricts the production vector to plausible education-eligible cohorts (age < 30 proxy).
- **RISK-006**: Integer rounding in MiD W12 percentages limits achievable L1 below ≈ 0.04 (independent rounding per row). Documented in TASK-022 acceptance.
- **ASSUMPTION-001**: LSN K3300101 Schülerzahlen are reported per Gemeinde (8-digit AGS) with no suppression issues for ZGB-8 (verified informally; LSN suppresses only n < 3 cells).
- **ASSUMPTION-002**: Hochschulkompass identifies all material ZGB Hochschulen — confirmed: TU BS, HBK BS, Ostfalia (4 standorte), HAW Wolfenbüttel, plus a handful of Berufsakademien with negligible enrolment.
- **ASSUMPTION-003**: MiD W1/W2 column "Ausbildung" includes all education-related main purposes (Schule, Berufsschule, Studium, Fortbildung) — verified via MiD 2023 codebook.
- **ASSUMPTION-004**: The current `population` term in `_execute_gravity_base` (sum of `df_population.weight`) approximates "potential education-trip producers" closely enough at the Gemeinde level once filtered to age < 30 (TASK-018). Proper alternative would be `df_population[df_population["status"]=="student"]` once that field is reliable downstream.

## 8. Related Specifications / Further Reading

- `plan/refactor-eqasim-bs.md` — refactor that must complete before this plan starts (D-5).
- `plan/improve-commute-calibration-1.md` — sister plan for the work-OD slope calibration; shares the per-RegioStaR-7 mechanism.
- `eqasim-data/data/braunschweig/Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf` — pages 245-250 (W6), 275-276 (W12).
- `braunschweig/gravity/model.py` — current implementation including the `return df_matrix, df_matrix` line that this plan removes for the education branch.
- LSN GENESIS-Online: https://www1.nls.niedersachsen.de/statistik/
- DESTATIS GENESIS-Online table 21311-0001 (Studierende): https://www-genesis.destatis.de/
- Hochschulkompass: https://www.hochschulkompass.de/
- OSM amenity tagging: https://wiki.openstreetmap.org/wiki/Key:amenity
- Bavaria upstream `bavaria/gravity/model.py` — confirms the shared work/education matrix is an upstream limitation, not a Braunschweig-specific bug.
