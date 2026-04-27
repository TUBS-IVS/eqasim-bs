---
goal: Replace the ZGB-aggregated household-size margin with per-Kreis joint Größe×Typ and Größe×Alter margins by activating the already-present Zensus 2022 tables 1000A-2081 and 1000A-3082 in the IPF; carry household type through the MiD-HTS seed so the IPF can balance against the new joint structure.
version: 1.0
date_created: 2026-04-27
last_updated: 2026-04-27
owner: BS calibration
status: 'Planned'
tags: [feature, ipf, braunschweig, zensus, household-type, household-size]
---

# Introduction

Phase-1 introduced `hh_size` as the 5th IPF margin (TASK-002). It works:
post-IPF mean HH-size 2.015 vs Bavaria's 2.017 baseline, max |Δ| 0.48 pp
against the ZGB-aggregated Zensus target. **However**, comparison
against the NS-Zensus aggregate shows a structural +3.5 pp 1-Person /
−1.9 pp 4-Person gap that Phase-2 (Dirichlet, outer-product) does **not**
close (|Δ| sum 7.7 pp vs 7.6 pp baseline, see `logs/compare_25pct.log`).

Diagnosis (see prior conversation analysis, 2026-04-27):

1. The current target **collapses 5000H-2001 to a single ZGB-wide
   vector** ([`braunschweig/data/census/household_size.py:96`](../braunschweig/data/census/household_size.py)).
   The 8 Kreise with strong urban-rural spread (SK BS 50.9 % 1P-HH
   vs LK Gifhorn 34.6 %) are not used as separate IPF cells.
2. Tables `1000A-2081` (Größe×Typ) and `1000A-3082` (Größe×Alter×Sex)
   are loaded but **inert** — only consumed by the validation
   harness, not by the IPF.
3. The MiD-HTS seed carries no `hh_type` column, so even if margin (2)
   were active, the IPF would have nothing to balance against on the
   type axis.

This plan keeps the existing 5th-margin scaffolding and adds three
incrementally larger cell sets, gated by config flags. **No new data
download is required** — all three Zensus tables are already in the
repo.

## 1. Requirements & Constraints

- **REQ-001**: Disaggregate the existing `hh_size` IPF margin from
  ZGB-aggregate to per-Kreis. Target table:
  `commune_id (ARS-12) × hh_size (1..6+) → households`.
  Source: existing 5000H-2001 loader, with grouping changed from
  `sum-over-scope` to `groupby(commune_id)`.

- **REQ-002**: Activate `1000A-2081` as a balanced IPF margin on
  the joint axis `(commune_id, hh_size, hh_type)` with five
  Familien-Typ classes (`single`, `couple`, `couple_with_children`,
  `single_parent`, `other_multi`). Margin is opt-in via
  `bavaria.ipf.use_household_type_margin: true`.

- **REQ-003**: Activate `1000A-3082` as a balanced IPF margin on
  the joint axis `(commune_id, sex, age_class, hh_size)`. Opt-in
  via `bavaria.ipf.use_household_age_size_margin: true`. Decouples
  age and HH-size from the current "identical distribution per
  age stratum" placeholder in
  [`braunschweig/data/census/household_size.py:124-130`](../braunschweig/data/census/household_size.py#L124).

- **REQ-004**: Add `hh_type ∈ {single, couple, couple_with_children,
  single_parent, other_multi}` to the MiD-HTS seed used by
  `bavaria.ipf.prepare`. Derive from MiD 2023 `H_TYP` (ID-Variable
  in `mid_haushalte.csv`). When `hh_type` cannot be derived, fall
  back to a default `other_multi` and log the share.

- **REQ-005**: Extend `bavaria.ipf.attributed.execute` so
  `hh_type` flows into the per-household formation pass alongside
  `hh_size`; rows with the same `(commune, hh_size, hh_type)` are
  shuffled and chunked into household groups of N.

- **REQ-006**: Surface a per-Kreis post-IPF deviation table for
  HH-size **and** HH-type:

  ```
  [bavaria.ipf.model] post-IPF margin check (hh_size × hh_type)
    SK Braunschweig 1×single        target=72500 achieved=72313 Δ=-0.26%
    SK Braunschweig 2×couple        target=21000 achieved=21115 Δ=+0.55%
    ...
  ```

  fail-hard threshold inherits `bavaria.ipf.margin_validation_tolerance`
  (default 1 %).

- **REQ-007**: All three flags default to `false` to preserve
  existing Bavaria/Braunschweig regression behaviour. Only the BS
  configs (`config_local_braunschweig.yml`,
  `config_local_braunschweig_10pct.yml`,
  `config_local_braunschweig_25pct.yml`) flip them on.

- **CON-001**: No new datasets — uses only `5000H-2001`,
  `1000A-2081`, `1000A-3082` already present in
  `eqasim-data/data/braunschweig/`.

- **CON-002**: No `eqasim-java/**` changes; HH-type stays a
  CSV/synth attribute.

- **CON-003**: Cell count grows from 6 (Phase-1) to up to
  8 Kreise × 6 sizes × 5 types ≈ 240 cells (margin 2) and
  8 × 2 sexes × 5 age classes × 6 sizes ≈ 480 cells (margin 3).
  Total IPF state ≤ 1.5 M rows — keep within current
  `bavaria.ipf.max_iterations: 1500`.

- **CON-004**: Dirichlet smoothing
  (`bavaria.ipf.dirichlet_prior_strength: 0.5`, TASK-011) MUST stay
  enabled — the new sparse type cells (e.g.
  `LK Goslar × 6+ × single_parent` ≈ < 50 households) would
  otherwise collapse.

- **CON-005**: No new conda dependencies; reuse pandas / numpy /
  zipfile.

- **CON-006**: Validation explicitly out of scope per user request
  ("validierung brauchen wir jetzt ersmla nicht"). Validation
  harness + post-deployment metrics tracked in a separate plan.

## 2. Implementation Steps

### Phase 1 — Per-Kreis HH-size margin (REQ-001)

1. Refactor `braunschweig/data/census/household_size.py`:
   - Replace `_load_region_distribution` (returns
     `dict[bin] -> count` summed over scope) with
     `_load_per_commune_distribution` returning a long DataFrame
     `commune_id × hh_size → weight` (no aggregation).
   - Drop the synthetic `(sex, age_strata)` cross-product —
     callers that need that cube will use `1000A-3082` via
     the new loader instead.
   - Output schema: `commune_id, hh_size, weight`.
2. Add a config flag
   `bavaria.ipf.use_per_commune_hh_size_margin: false` in
   `bavaria/ipf/model.py:configure`.
3. In `bavaria.ipf.model.execute`, when the flag is on, **replace**
   the current ZGB-wide hh_size constraint block with one
   `(commune_index, hh_size)` constraint per commune. Use the
   existing IPF-cell selector pattern at lines ~145-180.
4. Wire the new loader into `bavaria/ipf/prepare.py` alongside
   the existing one; pass the per-commune frame downstream when
   the flag is set.

### Phase 2 — Activate 1000A-2081 as joint Größe×Typ margin (REQ-002, REQ-004)

1. Promote `braunschweig/data/census/households_type.py` from
   validation-only to an IPF-ingested loader. Existing schema is
   already correct: `commune_id, hh_size, hh_type, weight`.
2. Add a new stage `braunschweig.data.census.households_type_target`
   that pivots to the long IPF format:
   `commune_id × hh_size × hh_type → households`.
3. Extend the MiD-HTS seed in `bavaria/ipf/prepare.py`:
   - Read the existing MiD2023 file
     (`bavaria.data.mid.persons` / `bavaria.data.mid.households`).
   - Map `H_TYP` → 5-class `hh_type` using the same code book
     as 1000A-2081 (`HSHTP1`).
   - Attach `hh_type` to every seed row via `household_id` join.
   - Log the per-class share post-mapping.
4. Add `bavaria.ipf.use_household_type_margin: false` flag.
5. In `bavaria.ipf.model`, when on:
   - Extend the seed `MultiIndex` with `hh_type ∈ HSHTP1_TYPE.values()`.
   - For each `(commune_index, hh_size, hh_type)` combination
     present in the target, append a `commune × size × type`
     constraint block.
   - Keep the existing per-commune `hh_size` block (REQ-001) —
     IPF tolerates redundant marginals.
6. In `bavaria/ipf/attributed.py` (the household-formation pass
   added in Phase-1, see
   [feature-bs-ipf-hhsize-1.md#L18](feature-bs-ipf-hhsize-1.md)):
   - Group by `(commune, hh_size, hh_type)` instead of
     `(commune, hh_size)`.
   - Within each bucket, shuffle deterministically with
     `random_seed`, chunk into household groups of size `n`, and
     assign `hh_type` to each group.

### Phase 3 — Activate 1000A-3082 as joint (Sex×Alter×Größe) margin (REQ-003)

1. Promote `braunschweig/data/census/households_size_age.py`
   to an IPF-ingested loader (currently used only for sanity
   checks).
2. Map Zensus age classes (`ALTKL2`, 11 bins) onto the existing
   `combined_age_classes` produced by `bavaria.ipf.model` (see
   [`bavaria/ipf/model.py:73-90`](../bavaria/ipf/model.py#L73)).
   For overlapping age cells, distribute the `1000A-3082`
   weight proportionally to the existing population marginal
   already present in the seed (preserves total person count).
3. Add `bavaria.ipf.use_household_age_size_margin: false` flag.
4. In `bavaria.ipf.model`, when on, append constraint block
   `(commune_index, sex, age_class, hh_size)` for every present
   target cell.
5. The existing `(age < minimum_one_person_age) ∩ (hh_size == 1)`
   hard-zero constraint stays — it's a physical impossibility,
   not a target.

### Phase 4 — Wiring & config flags

1. Update the three Braunschweig configs to enable all three
   flags:
   ```yaml
   bavaria.ipf.use_per_commune_hh_size_margin: true
   bavaria.ipf.use_household_type_margin: true
   bavaria.ipf.use_household_age_size_margin: true
   bavaria.ipf.dirichlet_prior_strength: 0.5  # already on
   ```
2. Leave Bavaria configs (`config_bavaria.yml`,
   `config_local_bavaria.yml`) untouched — flags default off.
3. Document the new flags in
   `plan/calibration-analysis-2025.md` next to the existing
   TASK-002/010/011 entries.

### Phase 5 — Cache invalidation & smoke-test runs

1. Delete `eqasim-data/cache_bs/`, `cache_bs_10pct/`,
   `cache_bs_25pct/` only for the affected stages
   (`bavaria.ipf.*`, `bavaria.synthesis.population.enriched`,
   `synthesis.population.sampled`, `synthesis.population.spatial.*`,
   `matsim.*`). synpp content-hashing handles the rest.
2. Smoke test: 1pct config (`config_local_braunschweig.yml`)
   end-to-end (≤ 30 min). Required signals in log:
   - `[bavaria.ipf.model] post-IPF margin check: ... cells, max Δ=` < 1 %.
   - `[braunschweig.data.census.household_size] Zensus 2022:
     <N> communes, mean size <m>` (per-commune log replaces
     ZGB-aggregate log).
   - `[bavaria.ipf.attributed] formed N households across
     M (commune × hh_size × hh_type) buckets`.
3. 10pct + 25pct runs only after 1pct succeeds.

## 3. Alternatives Considered

- **Single global type margin (no per-commune)**: rejected — gives
  no urban-rural correction, defeats the whole point.
- **Microdata sample from MiD only**: rejected — too small for
  reliable per-Kreis per-type cell sizes.
- **External Mikrozensus 2023 SUF**: rejected — requires manual
  acquisition, blocked on FDZ access; per-Kreis resolution
  insufficient for ZGB-8 anyway.

## 4. File Inventory

### Modified

- `braunschweig/data/census/household_size.py` (per-commune mode)
- `braunschweig/data/census/households_type.py` (loader → also IPF target)
- `braunschweig/data/census/households_size_age.py` (loader → also IPF target)
- `bavaria/ipf/prepare.py` (extend seed with `hh_type` from MiD)
- `bavaria/ipf/model.py` (3 new flags + 3 new constraint blocks)
- `bavaria/ipf/attributed.py` (HH-type-aware household formation)
- `config_local_braunschweig.yml`
- `config_local_braunschweig_10pct.yml`
- `config_local_braunschweig_25pct.yml`

### New

- `braunschweig/data/census/households_type_target.py` (pivots
  1000A-2081 to long IPF format)

### Untouched

- `eqasim-java/**` (CON-002)
- `bavaria/data/census/**` (Bavaria-specific loaders unchanged)
- All non-Braunschweig configs

## 5. Acceptance (functional only — no validation)

The plan is considered implemented when:

1. All three flags can be toggled independently and produce
   end-to-end-runnable pipelines on the 1pct config.
2. The post-IPF margin-check log emits one line per
   `(commune, hh_size, hh_type)` cell with |Δ| < 1 %.
3. The household-formation pass produces households whose
   `(hh_size, hh_type)` distribution matches the IPF target
   within the same tolerance.
4. No regression in the existing `tests/test_hh_size_margin.py`
   suite (extends, does not break).

## 6. Out of Scope

- Validation against MiD P12_1 / P13 / P17_1 (separate plan).
- Microcensus 2023 ingestion (separate plan if/when SUF acquired).
- Mode choice MNL (TASK-008/009 — separate plan).
- Gravity-decay re-calibration (TASK-001 extension — separate plan,
  larger expected MAE impact than this work).
