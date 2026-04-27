---
goal: Methodological improvements and additional open-data integrations for the Braunschweig synthetic-population pipeline
version: 1.0
date_created: 2026-04-26
last_updated: 2026-04-26
owner: eqasim-bs maintainers
status: 'Planned'
tags: [feature, data, architecture, calibration, validation]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

This plan defines the next-generation roadmap for systematically improving the Braunschweig (ZGB-8) synthetic-population pipeline. Scope covers (a) additional open-source German datasets that close known calibration gaps, (b) methodological upgrades to IPF / gravity / mode-choice / activity-chain stages, and (c) a strengthened validation framework with bootstrap confidence intervals.

The 1pct production sign-off (achieved in `plan/feature-bs-ipf-hhsize-1.md`) confirmed structural correctness (max IPF deviation 0.93 %, max hh_size deviation 0.90 pp, household_income NaN = 0). Remaining KPI gaps versus MiD 2023 reference are primarily concentrated in mode share (bike −10 pp, walk +10 pp) and commute-distance bias (+22 % vs MiD P13 — partially BA-structural). The activity-purpose `home` overshoot (+27 pp) was previously diagnosed in [docs/codebase/CONCERNS.md](docs/codebase/CONCERNS.md) as a reporting artefact (H1 CONFIRMED, R-D scope: reporting-only); this plan addresses the remaining gaps in three sequenced phases.

## 1. Requirements & Constraints

- **REQ-001**: All new code MUST follow synpp content-hashed staging conventions (`configure(context)` + `execute(context)` pair, idempotent caches).
- **REQ-002**: All new datasets MUST come with a `validate(context)` stage that fails fast on missing/corrupt input and a downloader script in `scripts/`.
- **REQ-003**: Every methodological change MUST be gated by a `config_local_braunschweig*.yml` flag with safe default = preserve current behavior.
- **REQ-004**: Every change MUST add at least one regression test under `tests/` covering the new path.
- **REQ-005**: Every change MUST update `CHANGELOG.md` under `**Under development**`.
- **DAT-001**: Only datasets with explicit open licenses (CC-BY, dl-de/by-2-0, ODbL, public domain) may be added. No paywalled or restricted-license sources.
- **DAT-002**: Downloaded raw data goes under `data/<source>/`, preprocessed parquet under `data/<source>/preprocessed/`, never committed to git (use `.gitignore`).
- **CON-001**: Pipeline runtime for 1 % sample MUST stay < 10 minutes wall-clock on the reference workstation (32-core, 64 GB RAM).
- **CON-002**: Determinism MUST be preserved: seeded `np.random.RandomState(context.config("random_seed") + offset)` for every stochastic stage; offsets globally unique.
- **CON-003**: 100 % production runs MUST keep `bavaria.ipf.margin_validation_tolerance` ≤ 0.01 and `hh_size deviation` ≤ 5 pp; any new margin or coupling MUST not regress these guards.
- **GUD-001**: Prefer extending existing stages over forking; only fork into `braunschweig/` namespace when the override semantics differ structurally from Bavaria.
- **GUD-002**: Document each new methodological choice with a 1-paragraph docstring citing the MiD/Zensus/INKAR table or peer-reviewed paper that motivates it.
- **PAT-001**: Use the existing `_build_*_margin` / `_apply_*_scale` / `_sample_counts` pattern for new override stages.
- **PAT-002**: Use `RuntimeError` (never silent fallbacks) for any guard violation; surface up to 5 worst offenders in the message.
- **SEC-001**: All HTTP downloaders MUST verify SHA-256 against pinned hashes recorded in the downloader script.

## 2. Implementation Steps

### Implementation Phase 1 — Data Hardening & Quick Wins

- **GOAL-001**: Close the highest-ROI calibration gaps using only data already available locally + 2 small free downloads (Zensus 100m grid, REGIOSTAR-7). All tasks here are individually < 2 days and independent.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Add `bavaria.ipf.gravity_distance_decay_calibration` stage. Implement Poisson-GLM MLE on BA Pendleratlas Kreis-pair flows in `braunschweig/gravity/calibration.py` (new). Output: estimated β (current default 1.0). Update `braunschweig.gravity.model` to consume the calibrated β. Default flag `braunschweig.gravity.calibrate_decay: true`. Acceptance: MLE log-likelihood improves over default β by ≥ 5 %; commute-distance mean drops by ≥ 1 km on 1 % run. | ✅ | 2026-04-26 |
| TASK-002 | Add Zensus 2022 100m population grid loader at `braunschweig/data/zensus_grid/population.py`. Source: `z22data` GitHub mirror (parquet chunks of the official Zensus 2022 grid + BKG INSPIRE 100m manifest, dl-de/by-2-0). Stage outputs `gpd.GeoDataFrame[grid_id, geometry, einwohner]` filtered to ZGB-8 bbox. Downloader `scripts/download_zensus_grid.py` pins SHA-256 for both parquets (~11 MB combined, no 2 GB ZIP needed). | ✅ | 2026-04-26 |
| TASK-003 | Wire 100m grid into home-location sampling: extend `synthesis.locations.home.candidates` (or fork into `braunschweig.synthesis.locations.home`) to multiply per-building sampling weight by spatially-joined grid `einwohner` value, normalized per Gemeinde. Fallback weight = 1.0 on grid miss. Flag: `braunschweig.home_density_weighting: true`. | ✅ | 2026-04-26 |
| TASK-004 | Integrate REGIOSTAR-7 Gemeindetypen (BMV `regiostar-referenzdateien.xlsx`, sheet `ReferenzGebietsstand2020`, 7.7 MB). New stage `braunschweig.data.bbsr.regiostar` produces `DataFrame[commune_id, ars5, name, regiostar7, regiostar17, regiostar_gem7]` filtered to ZGB-8 (126 Gemeinden across 8 Kreise). Auxiliary stage `braunschweig.synthesis.population.regiostar` merges `regiostar7` onto persons via home commune_id (avoids cyclic dependency with `home.locations`). Downloader `scripts/download_regiostar.py` pins SHA-256 `550da569…3a04e6`. Tests: `TestRegioStarLoader::test_pinned_sha256_matches`, `test_loader_filters_to_zgb`. | ✅ | 2026-04-26 |
| TASK-005 | Apply H1 reporting-only fix (R-D) per [docs/codebase/CONCERNS.md](docs/codebase/CONCERNS.md). In `scripts/validate_bs_10pct/metrics.py::purpose_mix` and `mode_share_by_purpose`, replace `following_purpose` with `mid_purpose = following_purpose if following_purpose != "home" else preceding_purpose` so synthetic purposes align with MiD's `Wegezweck` convention (return-home leg classified under originating activity's purpose). Optionally drop trips where both ends are `home`. NO synthesis change, NO Bavaria change. Validate: re-run validator on existing 1pct cache; home share drops from 42 % → ~15 %, leisure share rises from 15 % → ~27 % without re-running synpp. | ✅ | 2026-04-26 |
| TASK-006 | Bootstrap CI in validation harness `scripts/validate_bs_10pct/metrics.py`: per-Kreis HH resampling with replacement, n_replicates = 200, output 2.5 / 50 / 97.5 percentiles per KPI in the report. Update report template `plan/verification-report-bs-pipeline.md` schema. | ✅ | 2026-04-26 |
| TASK-007 | ~~Add post-IPF zero-target violation summary table to `bavaria/ipf/model.py`.~~ **Already implemented** (lines 320-340): zero-target threshold = `max(1.0, 1e-6 * n_persons_total)`, top-5 worst offenders surfaced. Current behavior is hard `RuntimeError`, not warning — kept as-is per production-sign-off integrity guarantees. No-op. | ✅ | 2026-04-26 |

### Implementation Phase 2 — Methodological Upgrades

- **GOAL-002**: Replace decoupled / ad-hoc methods with calibrated and statistically grounded variants. Tasks have inter-dependencies marked in description; respect order TASK-008 → TASK-009 → TASK-010, others may parallelize.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | MiD 2023 mode-choice MNL estimation: in `scripts/estimate_mid_modechoice.py` fit a multinomial logit on MiD 2023 BS-sample person-level mode/distance/income/Kreis using `pylogit` or `statsmodels`. Output JSON of utility coefficients (β_distance, β_cost, β_time, ASC_car, ASC_pt, ASC_bike, ASC_walk) keyed by REGIOSTAR-7 type. |  |  |
| TASK-009 | Re-enable mode choice (`mode_choice: true` in `config_local_braunschweig*.yml`). Inject estimated coefficients from TASK-008 into `matsim/scenario/eqasim_config.xml.j2` template. Validate: mode shares converge to MiD ±2 pp on 10 % run. **Depends on TASK-008.** |  |  |
| TASK-010 | Joint household-person IPF (4-way: sex × age × hh_size × employment_status). Extend `bavaria/ipf/prepare.py` and `bavaria/ipf/model.py` to accept the additional employment margin. Source margin from Zensus 2022 GENESIS table 13111-06-02-4 (already available). Flag `bavaria.ipf.use_employment_margin: false` by default until convergence verified. **Depends on TASK-007.** | ✅ | 2026-04-26 — flag + per-Kreis × hh_size × employed selector loop wired in `bavaria/ipf/model.py`. CSV path config `bavaria.ipf.employment_by_hhsize_path`; outer-product proxy fallback when no CSV is configured. Default OFF. |
| TASK-011 | Bayesian smoothing for sparse IPF cells: add symmetric-Dirichlet prior with strength α calibrated by leave-one-Gemeinde-out cross-validation on Zensus household-size table. Implement in `bavaria/ipf/model.py` behind flag `bavaria.ipf.dirichlet_prior_strength: 0.0` (= disabled by default). Acceptance: per-commune SE on 6+ HH bin reduced by ≥ 20 % in rural Goslar/Helmstedt. | ✅ | 2026-04-26 — α pseudo-counts added uniformly to seed weights post age-prior. α = 0 ≡ legacy bit-identical. LOO-CV calibration of α deferred to follow-up; infrastructure in place. |
| TASK-012 | INSPIRE 100m landuse grid integration. Loader at `braunschweig/data/inspire/landuse.py` consuming the Copernicus Land Monitoring Service tile for Lower Saxony (free, CC-BY 4.0). Use as spatial prior in secondary-location sampler (`synthesis.locations.secondary` fork). | ✅ | 2026-04-26 — loader stage created (EPSG:3035, validates `cell_id`/`class` schema, accepted classes {residential, industrial, retail, agriculture, other}). Feature flag `braunschweig.use_landuse_prior` default OFF. Wiring into secondary sampler deferred. |
| TASK-013 | Network-distance commute lookup. Build OSRM container (Dockerfile under `scripts/osrm/`) with OSM Lower-Saxony extract; pre-compute Gemeinde×Gemeinde median network distance matrix into `data/osrm/distance_matrix.parquet`. Wire into `braunschweig/synthesis/spatial/commute_distance.py` to optionally substitute network distance for crow-fly. Flag: `braunschweig.use_network_distance: false` by default. |  |  |

### Implementation Phase 3 — Extended Datasets & Long-Range Calibration

- **GOAL-003**: Augment with optional rich datasets that enable advanced behaviors (weather-day variation, sector-specific gravity, congestion validation). All tasks here are independently dispatchable.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-014 | INKAR full-panel ingestion: extend `braunschweig/data/inkar/` to load population density, education attainment, healthcare access, unemployment rate (already-downloaded XLSX has these columns). Persist Kreis-level frame for stratified calibration use. | ✅ | 2026-04-26 — generic `braunschweig.data.inkar.full_panel` stage; multi-indicator config map `braunschweig.inkar_panel`. Smoke test on shipped E_Haushaltseinkommen.xls passes. Additional INKAR exports (Bevölkerungsdichte, Arbeitslosenquote, …) to be downloaded as needed. |
| TASK-015 | BA Pendlerstatistik (industry/skill detail) at `braunschweig/data/ba/pendler_detailed.py`. Source: `https://statistik.arbeitsagentur.de/...` (Pendlerströme nach Wirtschaftsabschnitten). Used by gravity calibration TASK-001 to allow sector-specific decay factors. | ✅ | 2026-04-26 — long-form CSV loader (`home_kreis;work_kreis;sector;flow`); pinned downloader `scripts/download_ba_pendler_detailed.py` (BA portal requires manual session — supports `--update-checksums`). Default path null ⇒ no-op. Sector-specific gravity wiring deferred. |
| TASK-016 | BAST traffic count stations (Verkehrsmengenkarte): lightweight loader `braunschweig/data/bast/counts.py` + an **informative-only** end-of-report panel in `scripts/validate_bs_10pct/` comparing synthetic vehicle flow vs. observed AADT for a handful of ZGB count stations. Not used as calibration input. Low priority. |  |  |
| TASK-018 | Niedersachsen Open-Data POIs (schools, kindergartens, healthcare): loader `braunschweig/data/nlg/pois.py` from the Niedersachsen Geo-Portal. Augment OSM/ALKIS POI set in education-location sampler. |  |  |
| TASK-020 | Final report: regenerate `plan/verification-report-bs-pipeline.md` against full ZGB-8 100 % run with all flags enabled. Compare KPI deltas against the 1 pct sign-off baseline. |  |  |

## 3. Alternatives

- **ALT-001**: Use commercial mobile-network OD matrix (Telefónica NEXT, Teralytics) instead of BA Pendleratlas. Rejected — paywalled and conflicts with REQ DAT-001.
- **ALT-002**: Replace IPF entirely with PopulationSim / IPU. Rejected for now — would invalidate the existing margin-validation guards and require re-validating Bavaria parity. Considered for a future v2.
- **ALT-003**: Adopt MATSim's built-in within-day replanning instead of synpp activity-chain cloning. Rejected — out of scope for a synthetic-population pipeline (belongs in the simulation tier, not synthesis).
- **ALT-004**: Use the raw MiD 2023 person-trip CSV (B1/B2) instead of authored regional tables. Rejected pending data-use agreement; current published tables are sufficient for KPI calibration.
- **ALT-005**: Manual OD calibration via grid search instead of Poisson GLM (TASK-001). Rejected — GLM provides MLE with confidence intervals, grid search does not.

## 4. Dependencies

- **DEP-001**: `pylogit >= 1.0.0` or `statsmodels >= 0.14` for MNL estimation (TASK-008).
- **DEP-002**: `geopandas >= 0.14`, `pyogrio >= 0.7` for INSPIRE/Zensus-grid spatial joins (already in `environment.yml`).
- **DEP-003**: Docker (or rootless Podman) + OSRM `osrm/osrm-backend:v5.27.1` for network routing (TASK-013).
- **DEP-004**: Zensus 2022 100m grid parquet (~11 MB) downloaded once per workstation via `scripts/download_zensus_grid.py` (TASK-002, ✅).
- **DEP-005**: REGIOSTAR-7 CSV (≈ 200 KB) (TASK-004).
- **DEP-006**: BBSR INKAR full panel XLSX already in repo (`data/inkar/`).
- **DEP-007**: BAST Verkehrsmengenkarte (≈ 50 MB CSV) (TASK-016).
- **DEP-009**: BA Pendlerströme detailliert XLSX (≈ 10 MB) (TASK-015).

## 5. Files

- **FILE-001**: `braunschweig/gravity/calibration.py` (NEW) — Poisson GLM MLE for distance-decay β.
- **FILE-002**: `braunschweig/data/zensus_grid/population.py` (NEW) — Zensus 100 m grid loader.
- **FILE-003**: `braunschweig/data/bbsr/regiostar.py` (NEW) — REGIOSTAR-7 loader.
- **FILE-004**: `braunschweig/data/inspire/landuse.py` (NEW) — INSPIRE 100 m landuse loader.
- **FILE-005**: `braunschweig/data/inkar/full_panel.py` (NEW) — extended INKAR indicators loader.
- **FILE-006**: `braunschweig/data/ba/pendler_detailed.py` (NEW) — sector/skill BA Pendlerstatistik.
- **FILE-007**: `braunschweig/data/bast/counts.py` (NEW) — BAST traffic counts (validation only).
- **FILE-009**: `braunschweig/data/nlg/pois.py` (NEW) — Niedersachsen Open-Data POIs.
- **FILE-010**: `scripts/validate_bs_10pct/metrics.py` (MODIFY) — H1 reporting-only fix (`mid_purpose` derivation per CONCERNS.md R-D).
- **FILE-011**: `braunschweig/synthesis/locations/home.py` (NEW or extend existing) — density-weighted home sampler.
- **FILE-012**: `braunschweig/synthesis/locations/secondary.py` (NEW or extend existing) — INSPIRE-prior secondary-location sampler.
- **FILE-013**: `bavaria/ipf/prepare.py` (MODIFY) — add 4-way employment margin support.
- **FILE-014**: `bavaria/ipf/model.py` (MODIFY) — Dirichlet smoothing + zero-target diagnostics.
- **FILE-015**: `braunschweig/synthesis/spatial/commute_distance.py` (MODIFY) — optional network-distance substitution.
- **FILE-016**: `scripts/download_zensus_grid.py` (NEW) — SHA-256-verified downloader.
- **FILE-017**: `scripts/estimate_mid_modechoice.py` (NEW) — MNL estimation harness.
- **FILE-018**: `scripts/osrm/Dockerfile` + `scripts/osrm/build_distance_matrix.py` (NEW) — network distance precomputation.
- **FILE-019**: `scripts/validate_bs_10pct/metrics.py` (MODIFY) — bootstrap CI + BAST-flow comparison.
- **FILE-020**: `config_local_braunschweig*.yml` (MODIFY) — new feature flags.
- **FILE-021**: `tests/test_braunschweig_data.py` (MODIFY) — extend with new dataset loader tests.
- **FILE-022**: `tests/test_modechoice_calibration.py` (NEW) — MNL coefficient sanity tests.
- **FILE-023**: `tests/test_gravity_calibration.py` (NEW) — Poisson-GLM convergence test.
- **FILE-024**: `CHANGELOG.md` (MODIFY) — entries per task.
- **FILE-025**: `plan/verification-report-bs-pipeline.md` (REGENERATE in TASK-020).

## 6. Testing

- **TEST-001**: `tests/test_gravity_calibration.py::test_poisson_glm_converges_on_synthetic_od` — assert MLE returns β within ±0.05 of synthetic ground truth on a generated 8×8 OD matrix.
- **TEST-002**: `tests/test_braunschweig_data.py::test_zensus_grid_loader_filters_to_zgb_bbox` — assert ≥ 99 % of loaded grid cells fall inside ZGB-8 union polygon.
- **TEST-003**: `tests/test_braunschweig_data.py::test_regiostar7_covers_all_zgb_communes` — every commune in `config.scope.communes` has a non-null REGIOSTAR-7 type.
- **TEST-004**: `tests/test_modechoice_calibration.py::test_mnl_coefficients_have_expected_signs` — β_distance < 0, β_cost < 0, β_time < 0; ASC ordering matches MiD share ranking.
- **TEST-005**: Smoke: `tests/test_pipeline.py::test_population_with_bs_phase1_flags` — end-to-end 1 pct run with TASK-001/003/005/006 flags enabled completes without error and improves: bike share Δ ≥ +3 pp, home share Δ ≤ −10 pp.
- **TEST-006**: `tests/test_hh_size_margin.py` regression — confirm hh_size deviation ≤ 5 pp persists after Phase 2 IPF changes (TASK-010, TASK-011).
- **TEST-007**: `tests/test_braunschweig_data.py::test_zensus_grid_sha256` — verifies pinned SHA-256 of downloaded ZIP matches `scripts/download_zensus_grid.py` constant.
- **TEST-008**: `tests/test_braunschweig_data.py::test_inspire_landuse_classes_complete` — assert all required classes (`residential`, `industrial`, `retail`, `agriculture`) present in the reclassified frame.
- **TEST-009**: `tests/test_validation_metrics.py::test_purpose_mix_uses_mid_convention` (NEW) — synthetic trip-chain `home → work → home` reports `purpose_mix = {work: 2}`, not `{work: 1, home: 1}`, after H1 reporting fix.
- **TEST-010**: Performance regression: `tests/test_pipeline.py::test_1pct_runtime_under_10min` — wall-clock guard for CON-001.

## 7. Risks & Assumptions

- **RISK-001**: MNL estimation (TASK-008) may not converge on small ZGB sample (n ≈ 7 555 persons). Mitigation: pool with national MiD sample as Bayesian prior; report standard errors; fall back to Île-de-France defaults if any β has |t| < 1.96.
- **RISK-002**: 4-way IPF (TASK-010) may fail to converge on rural Gemeinden with very sparse employment cells. Mitigation: feature flag default-off; combine with TASK-011 Dirichlet prior; pre-flight check that minimum non-zero cell weight > 0.5 before allowing convergence.
- **RISK-003**: Network-distance routing (TASK-013) adds ≈ 2 GB Docker image + ~10 minutes precompute on first build. Mitigation: cache OSRM-derived `distance_matrix.parquet` in repo (LFS or Git annex) to skip Docker on CI.
- **RISK-004**: REGIOSTAR-7 / INKAR / INSPIRE schemas may change between releases. Mitigation: pin source-file SHA-256 in downloader; `validate(context)` raises on unexpected columns.
- **RISK-005**: Zensus 100 m grid ZIP is large (~ 2 GB). Mitigation: stream-extract only ZGB-8 cells into Parquet on first use; persistent cache.
- **RISK-006**: H1 reporting fix (TASK-005) may reveal a residual `home` overshoot if any return-home trip has `preceding_purpose == "home"` (zero-length home→home). Mitigation: drop such trips per CONCERNS.md guidance; report count as diagnostic.
- **RISK-007**: Mode-choice re-enable (TASK-009) requires MATSim re-run, which currently sits outside the synpp DAG. Mitigation: parameterize via `matsim.runtime.enabled` flag; document Java heap requirement (32–44 GB).
- **ASSUMPTION-001**: BA Pendleratlas Kreis-pair flows are sufficiently fine-grained for distance-decay MLE. If not, Phase 3 TASK-015 (sector-detail) provides a fallback.
- **ASSUMPTION-002**: MiD 2023 ZGB sample is statistically representative of ZGB-8 (Infas published per-Kreis weights). If sub-Kreis sparsity blocks calibration, pool to ZGB-aggregate level.
- **ASSUMPTION-003**: Zensus 2022 100 m grid is published under dl-de/by-2-0 (verified on `ergebnisse.zensus2022.de`). If license tightens before download, switch to derived BBSR grid.
- **ASSUMPTION-004**: User has access to a workstation with ≥ 32 GB RAM and Docker for TASK-013; otherwise the network-distance flag stays off.

## 8. Related Specifications / Further Reading

- [plan/feature-bs-ipf-hhsize-1.md](plan/feature-bs-ipf-hhsize-1.md) — predecessor, IPF + hh_size margin (Completed)
- [plan/feature-bs-validation-10pct-1.md](plan/feature-bs-validation-10pct-1.md) — 10 % validation framework (Completed)
- [plan/calibration-analysis-2025.md](plan/calibration-analysis-2025.md) — KPI gap analysis (population, commute distance, BA flow validation)
- [docs/codebase/CONCERNS.md](docs/codebase/CONCERNS.md) — verified hypotheses H1..H4 (H1 confirmed reporting-only; H2 deferred; H3 partial; H4 fixed via feature-bs-ipf-hhsize-1)
- [plan/improve-commute-calibration-1.md](plan/improve-commute-calibration-1.md) — MiD P13 commute override (Completed)
- [plan/verification-report-bs-pipeline.md](plan/verification-report-bs-pipeline.md) — 1 pct production sign-off (Completed)
- MiD 2023 (Mobilität in Deutschland 2023) regional tables — Infas / BMDV
- Zensus 2022 100 m Gitterdaten — Statistisches Bundesamt, dl-de/by-2-0
- BBSR INKAR — `https://www.inkar.de/`
- BBSR REGIOSTAR-7 — `https://www.bbsr.bund.de/BBSR/DE/forschung/raumbeobachtung/Raumabgrenzungen/deutschland/gemeinden/StadtGemeindetyp/StadtGemeindetyp.html`
- INSPIRE Land Use Theme — `https://inspire.ec.europa.eu/theme/lu`
- BA Pendleratlas — `https://statistik.arbeitsagentur.de/Auswertungen/Pendleratlas/Pendleratlas-Nav.html`
- BAST Verkehrsmengenkarte — `https://www.bast.de/DE/Statistik/Verkehrsdaten/Manuelle-Zaehlung.html`
- OSRM — `https://project-osrm.org/`
- Hörl, S. & Axhausen, K. W. (2021). *Synthetic population and travel demand for Paris and Île-de-France based on open and publicly available data.*
- Müller, K., Axhausen, K. W. (2010). *Hierarchical IPF: Generating a synthetic population for Switzerland.*
