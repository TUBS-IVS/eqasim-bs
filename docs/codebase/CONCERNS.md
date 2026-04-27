# Codebase Concerns

> Verified hypotheses (H1..H4), known bugs (BUG-001..BUG-011), tech debt, and open questions.

## Core Sections (Required)

### 1) Top Risks (Prioritized)

| Severity | Concern | Evidence | Impact | Suggested action |
|----------|---------|----------|--------|------------------|
| **HIGH** | BUG-001: Residency flag mismatch in output | [synthesis/output.py](synthesis/output.py#L57) hardcoded `"is_munich_resident"` but Braunschweig enriched.py creates `"is_bs_resident"` | CSV writer outputs wrong/empty column for BS | Make column name config-driven (Decision D-5 deferred) |
| **HIGH** | BUG-002: Household member grouping corruption | [synthesis/population/sampled.py](synthesis/population/sampled.py#L37-L39) — split indices don't match replication count → persons wrongly grouped into households | 10+ person households may contain members from 2-3 original households | Fix: compute expanded sizes before split (Decision D-5 deferred) |
| **HIGH** | Bavaria coupling (CON-001) | Many core stages in [bavaria/](bavaria/) not customizable for BS without modifying Bavaria code | If we need to fix Bavaria bugs (e.g. BUG-002), must either patch upstream or override in BS | Refactor Phase 3 extracts region-neutral code to `eqasim_common/` |
| **MEDIUM** | BUG-003: commune_id leading-zero loss | [braunschweig/synthesis/spatial/commute_distance.py](braunschweig/synthesis/spatial/commute_distance.py#L78) — astype(str) loses leading zero → Kreis lookup fails silently → uses fallback distribution | Commute distances for Braunschweig residents are region-wide instead of Kreis-specific; ~5 km bias | Use `str.zfill(5)` before slice (Decision D-5 deferred) |
| **MEDIUM** | Cache invalidation cascade | If one input data file changes, all 62 downstream stages re-run (~4 hours on laptop) | High iteration cost; blocks rapid experimentation | Investigate partial re-run or cache partitioning (Phase 4+) |
| **MEDIUM** | Java/Python boundary fragility (CON-002) | Mode-choice parameters live in Java (`org.eqasim.bavaria.routing.Modes`); if we want to tune utilities for ZGB-8, must rebuild Java | −10 pp bike bias, +9.9 pp walk bias unsolved (BUG-E-001) | Document Java rebuild steps; defer mode-choice tuning to Phase 2 (Decision D-1c) |
| **MEDIUM** | RNG non-reproducibility (BUG-005) | Hardcoded seed offsets differ across modules (e.g. 91731 in enriched.py); if stage order changes, RNG state drifts | Two runs with same seed produce different vehicles/income if IPF output order changes | Use consistent offset or derive from stage name hash (Decision D-5 deferred) |
| **MEDIUM** | BUG-004: Silent NaN in household-income map | [bavaria/synthesis/population/enriched.py](bavaria/synthesis/population/enriched.py#L270) `.map()` returns NaN silently if household_size not in income_size_map | ~1% of 5-person households get NaN income; downstream sampling skips them | Pre-check with assertion before sampling (Decision D-5 deferred) |
| **LOW** | BUG-006: Encoding error in CSV read | [braunschweig/data/census/households_type.py](braunschweig/data/census/households_type.py#L69) no encoding param; defaults to system locale | If ZIP contains UTF-8 ü/ö/ä, corrupts ARS12 codes → merge fails silently → 0 households loaded from affected Gemeinden | Add `encoding="utf-8-sig"` to pd.read_csv() (Decision D-5 deferred) |

### 2) Technical Debt

List the most important debt items only.

| Debt item | Why it exists | Where | Risk if ignored | Suggested fix |
|-----------|---------------|-------|-----------------|--------------|
| BUG-007: INKAR merge NaN | If INKAR file malformed (all `"N.A."`), `.dropna()` returns empty → all income becomes NaN | [braunschweig/data/inkar/household_income.py](braunschweig/data/inkar/household_income.py#L84-L85) | Silent mode-choice bias (all HHs use baseline mode, not income-dependent) | Assert `len(df) > 0` after dropna with diagnostic message |
| BUG-008: Unsorted household formation | After stochastic shuffle, SORT is applied but sort order depends on category definition order (which varies with upstream data) | [bavaria/ipf/attributed.py](bavaria/ipf/attributed.py#L96-L98) | Two runs with same seed produce different household groupings if IPF output category order differs | Ensure `commune_id` sorted as string before category conversion |
| BUG-009: No post-IPF margin validation | IPF convergence only checks factor tolerance, not that final weights satisfy all margin targets | [bavaria/ipf/model.py](bavaria/ipf/model.py#L262) | Infeasible problems can spuriously "converge" to suboptimal distribution; margins violated silently | Post-IPF loop: assert `abs(sum(weights[f]) - target) < 0.1 * target` for all margins |
| BUG-010: Silent allocation failure in HH-size rescaling | No check before division; if `size_total` is 0 or NaN, produces inf/NaN | [bavaria/ipf/prepare.py](bavaria/ipf/prepare.py#L68) | NaN weights → IPF fails downstream | Assert `(size_total > 0).all()` before division with diagnostic message |
| BUG-011: Categorical bin mismatch in HH-size sampling | Maps integer 6 to "6+" but household_size is always string; mapping is dead code | [bavaria/synthesis/population/enriched.py](bavaria/synthesis/population/enriched.py#L20-L22) | If attributed.py changes, mixed int/string household_size → sampling skips unmapped values | Assert all bins present before sampling |
| Mixed German/English documentation | Docstrings, comments, config keys inconsistent; makes onboarding slow | All source files | High cognitive load for English-speaking contributors | Refactor Phase 3 standardizes on English (Decision D-5 target) |
| No linting/formatting rules enforced | Code style varies (line length, blank lines, docstring format) | All source | Inconsistent PRs; merge conflicts | Add `black`, `pylint` config; run in CI (Phase 3+) |
| No type hints | No static type checking; runtime errors catch type bugs late | All source | Harder to detect schema mismatches between stages | Add `mypy` config + type hints (Phase 3+) |

### 3) Security Concerns

| Risk | OWASP category (if applicable) | Evidence | Current mitigation | Gap |
|------|--------------------------------|----------|--------------------|-----|
| No input validation on CSV reads | A7:2021 — Cross-Site Scripting (data layer equivalent) | Data loaders read CSV without schema validation | Zensus/BA/MiD are trusted public sources | Add schema checks (expected column names + types); warn on unexpected rows |
| Hardcoded data paths | A6:2021 — Vulnerable and Outdated Components | [config_local_braunschweig.yml](config_local_braunschweig.yml) has `data_path: eqasim-data/data` | Relative path configurable via YAML | None (intended design) |
| No output sanitization | A3:2021 — Injection | CSV output includes user-derived fields (household_id, person_id) | Output is consumed by trusted MATSim code only (not web-facing) | None (low risk) |
| Credentials not stored | [Good] | No hardcoded API keys | Public data sources; no auth needed | None |

### 4) Performance and Scaling Concerns

| Concern | Evidence | Current symptom | Scaling risk | Suggested improvement |
|---------|----------|-----------------|-------------|-----------------------|
| 4-hour 10% run time | Synpp stage execution + cache I/O; gravity IPF is slowest step | Local laptop: ~4 hours for 1 run; 10% = 113k persons | 25% run = ~16 hours; 100% = 64 hours (extrapolated) | Profile gravity IPF; consider sparse matrix optimizations or GPU (Phase 4+) |
| In-memory cache for validation harness | [scripts/validate_bs_10pct/io.py](scripts/validate_bs_10pct/io.py) builds GeoDataFrame end-points lazily during metric computation | Validation run is slower than necessary | Doesn't scale beyond 25% (memory exhaustion on 100% population) | Cache GeoDataFrame to disk (.gpkg) during metric loop |
| Full DataFrame aggregation for IPF targets | [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L70) sums entire scope at once | Works fine for 1%; slow for 100% | Large scope aggregations become I/O bottleneck | Consider per-Kreis aggregation + parallel reduce (Phase 4+) |
| OSM parsing for every POI class | [braunschweig/data/osm.py](braunschweig/data/osm.py) iterates PBF for shops, schools, healthcare, etc. | ~10 min for 100m Niedersachsen PBF | PBF parsing not cached; re-download + reparse on every config change | Pre-cache POI layers as .gpkg; skip re-parse if unchanged |

### 5) Fragile/High-Churn Areas

| Area | Why fragile | Churn signal | Safe change strategy |
|------|-------------|-------------|----------------------|
| [braunschweig/gravity/model.py](braunschweig/gravity/model.py) | Core calibration logic; many edge cases (intra-Kreis synthesis, external commuter injection, NaN handling) | Edited 2026-04-25 for gravity_slope tuning | Add unit tests for `_synthesise_intra_kreis()` + `_calibrate()` before editing; test against BA flow totals |
| [bavaria/synthesis/population/enriched.py](bavaria/synthesis/population/enriched.py) | Household enrichment (vehicle, income, type sampling); HH-size bin mapping; division by zero on line 90, 132, 173 | Not touched in BS; Bavaria upstream | Do not edit directly (CON-001); override in braunschweig.synthesis.* if needed; add defensive assertions |
| [bavaria/ipf/attributed.py](bavaria/ipf/attributed.py) | Household formation from IPF output; stochastic rounding + sorting (BUG-008); non-deterministic sort order | Edited upstream frequently | Do not modify for BS; if reproducibility breaks, add assert for sorted commune_id before category conversion |
| [braunschweig/data/](braunschweig/data/) (all loaders) | Data source schemas change; Zensus suppression rules, BA file format updates | Data path refs scattered across 15 modules | Centralize data path config keys (Phase 3); document schema per source in README.md; add schema validation assertions in each loader |
| [tests/test_pipeline.py](tests/test_pipeline.py) | Pre-existing IDF tests; 11 fail (Decision D-5: no fix in refactor Phase 0) | Fails consistently | Do not modify for now; upstream sync after refactor complete (Phase 4) |

### 6) `[ASK USER]` Questions

1. **[ASK USER] Data source versioning**: Should we track Zensus/BA/MiD download dates and file hashes in a data manifest? This would help detect if input data was silently re-published with different values.

2. **[ASK USER] MiD 2023 infas sample 7555**: Is this the final/official ZGB regional sample, or will there be a newer version in 2024/2025? Should we plan for data updates mid-project?

3. **[ASK USER] GTFS feed source**: Currently unknown. Confirm: Which GTFS feed should be used (VBB or Braunschweig transport authority)? Is it updated regularly?

4. **[ASK USER] RegioStaR-7 integration**: The urban/rural classification is mentioned in config but I found no evidence it's used in synthesis. Should we remove the config key or implement region-type stratification in IPF/location sampling?

5. **[ASK USER] Phase 3 English translation**: Should we translate all docstrings + German variable names (e.g. `ars5`, `gemein de_id`) to English, or keep German abbreviations for Zensus compatibility?

6. **[ASK USER] Coverage targets**: What coverage threshold should we set for the refactored code? (Suggested: 80% braunschweig/*, 60% legacy bavaria/*)

### 7) Evidence

- Scan output: HIGH-CHURN FILES section, TODO/FIXME/HACK count
- Session memory: [/memories/session/ipf-braunschweig-analysis.md](/memories/session/ipf-braunschweig-analysis.md) — detailed bug audit
- File citations above (all line-linked)
- [plan/calibration-analysis-2025.md](plan/calibration-analysis-2025.md) — KPI analysis + hypotheses
- [eqasim-data/output_bs_10pct/validation/report.json](eqasim-data/output_bs_10pct/validation/report.json) — KPI baseline

---
