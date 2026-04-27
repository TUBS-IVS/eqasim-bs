# Codebase Concerns

> Verified hypotheses (H1..H4), known bugs (BUG-001..BUG-011), tech debt, and open questions.
>
> **Status (2026-04-27, post-bavaria-removal)**: BUG-001, -004, -009, -010, -011 are
> resolved as part of the BS-merged code base. BUG-003, -006, -007, -008 fixed in
> the bug-sweep commit on branch `refactor/braunschweig-clean-fork`. BUG-002, -005
> remain documented (BUG-002 not reproducible in current code, BUG-005 RNG-offset
> consistency is cosmetic and deferred).

## Core Sections (Required)

### 1) Top Risks (Prioritized)

| Severity | Concern | Evidence | Status | Suggested action |
|----------|---------|----------|--------|------------------|
| **RESOLVED** | BUG-001: Residency flag mismatch in output | [synthesis/output.py](../../synthesis/output.py#L57) | Auto-detects fork-specific `is_*_resident` column; falls back to `is_munich_resident=False`. | None — fixed in current code. |
| **RESOLVED** | BUG-004: Silent NaN in household-income map | [braunschweig/synthesis/population/enriched.py](../../braunschweig/synthesis/population/enriched.py) | `_build_income_size_map` raises `RuntimeError` on unresolved bins; post-sampling `n_missing_income > 0` raises. | None — fixed in current code. |
| **RESOLVED** | BUG-009: No post-IPF margin validation | [braunschweig/ipf/model.py](../../braunschweig/ipf/model.py) | Hard post-IPF check on `margin_validation_tolerance` (default 1 %) plus zero-target violation guard. | None — fixed in current code. |
| **RESOLVED** | BUG-010: Silent allocation failure in HH-size rescaling | [braunschweig/ipf/prepare.py](../../braunschweig/ipf/prepare.py) | Explicit `if not (df["size_total"] > 0).all(): raise RuntimeError(...)`. | None — fixed in current code. |
| **RESOLVED** | BUG-011: Categorical bin mismatch in HH-size sampling | [braunschweig/synthesis/population/enriched.py](../../braunschweig/synthesis/population/enriched.py) | `_build_income_size_map` validates the bin schema and raises on unknown labels. | None — fixed in current code. |
| **RESOLVED (this commit)** | BUG-003: commune_id leading-zero loss | [braunschweig/synthesis/spatial/commute_distance.py](../../braunschweig/synthesis/spatial/commute_distance.py) | `astype(str).str.zfill(8)` before slicing the 5-digit Kreis prefix. | None. |
| **RESOLVED (this commit)** | BUG-006: Encoding error in CSV read | [braunschweig/data/census/households_type.py](../../braunschweig/data/census/households_type.py) | `encoding="utf-8-sig"` added to `pd.read_csv`. | None. |
| **RESOLVED (this commit)** | BUG-007: INKAR merge NaN | [braunschweig/data/inkar/household_income.py](../../braunschweig/data/inkar/household_income.py) | `RuntimeError` when `dropna` empties the frame, with diagnostic showing row counts. | None. |
| **RESOLVED (this commit)** | BUG-008: Unsorted household formation | [braunschweig/ipf/attributed.py](../../braunschweig/ipf/attributed.py) | `commune_id` cast to `str` before sort/factorize; `kind="mergesort"` for stability. | None. |
| **MEDIUM** | Cache invalidation cascade | If one input data file changes, all 62 downstream stages re-run (~4 hours on laptop) | Open. | Investigate partial re-run or cache partitioning (Phase 4+). |
| **MEDIUM** | Java/Python boundary fragility (CON-002) | Mode-choice parameters live in Java (`org.eqasim.bavaria.routing.Modes`); if we want to tune utilities for ZGB-8, must rebuild Java | Open per Decision D-1c (Java rename out of scope). | Document Java rebuild steps; defer mode-choice tuning. |
| **MEDIUM** | BUG-005: RNG seed offsets inconsistent across modules | Hardcoded offsets like `+ 8572` in `enriched.py`; if stage order changes, RNG state drifts | Documented; not reproducible failure today. | Cosmetic — derive from stage-name hash in a separate refactor. |
| **LOW** | BUG-002: Household member grouping (claimed) | [synthesis/population/sampled.py](../../synthesis/population/sampled.py) — split + repeat sequence | Re-audited 2026-04-27: no defect reproducible. `np.split + filter + repeat` correctly preserves household groupings; `household_size` totals match `len(df_census)` by construction (one row per person). | Keep documented; reopen if validation harness ever shows mismatched HH groupings. |
| **CLOSED** | Bavaria coupling (CON-001) | `bavaria/` folder deleted in commit `0b1d01d`; region-neutral code lives in `eqasim_common/`, BS-specific in `braunschweig/`. | None — done. |

### 2) Technical Debt

| Debt item | Where | Status | Suggested fix |
|-----------|-------|--------|--------------|
| Mixed German/English documentation | All source files | Open. | Standardize on English in a future refactor; German Zensus/BA/MiD field names stay for traceability. |
| No linting/formatting rules enforced | All source | Open. | Add `black`, `ruff` config; run in CI (Phase 4+). |
| No type hints | All source | Open. | Add `mypy` config + type hints (Phase 4+). |
| Stage-cache fingerprinting cascade | `eqasim-data/cache_bs/` | Open. | Single input change invalidates ~62 downstream stages; investigate per-stage hash partitioning. |

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
