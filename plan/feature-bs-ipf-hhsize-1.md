---
goal: Add household-size as a 5th IPF margin (commune × sex × age × HH-size) to fix the −16…−33 pp 1-Person-Haushalt bias in the Braunschweig synthetic population by integrating the new Zensus 2022 1000A-* tables into the Bavaria IPF model
version: 1.0
date_created: 2026-04-26
last_updated: 2026-04-25
owner: BS calibration
status: 'Implemented'
tags: [feature, ipf, braunschweig, bavaria, household-size, zensus]
---

# Introduction

![Status: Implemented](https://img.shields.io/badge/status-Implemented-brightgreen)

> **2026-04-25 update — end-to-end validated.** All five phases shipped.
> Pivot from 1000A-3082 (4-way cube, ZGB coverage too sparse) to
> 1000A-2081 (commune × hh_size × hh_type, 1.135 M ZGB persons). The IPF
> margin alone was insufficient because `synthesis.population.sampled`
> requires integer `household_size` with consecutive rows per
> `household_id`; a deterministic **household-formation pass** was added
> at the end of `bavaria/ipf/attributed.py` (stochastic round → shuffle
> within `(commune, hh_size)` buckets → chunk into groups of N). On
> `config_local_braunschweig.yml`, ZGB-wide synth vs Zensus
> household-share fit:
>
> | bin | Zensus | Synth (new) | Δ (pp) |
> |-----|--------|-------------|--------|
> | 1   | 43.2 % | 43.1 %      | −0.07  |
> | 2   | 31.5 % | 31.9 %      | +0.35  |
> | 3   | 12.4 % | 12.4 %      |  0.00  |
> | 4   |  8.7 % |  8.3 %      | −0.48  |
> | 5   |  2.7 % |  2.6 %      | −0.14  |
> | 6+  |  1.4 % |  1.7 %      | +0.34  |
>
> **max |Δ| = 0.48 pp**, mean 0.23 pp — well inside the 5 pp acceptance
> threshold (REQ-002). Children (age < 16) in 1P-HH = 0 exactly. 14 new
> tests pass (`tests/test_hh_size_margin.py`); 23 existing BS tests pass.

The 10 % Braunschweig validation report exposes a systematic
under-representation of single-person households (1P-HH) of −16 to −33 pp
per Kreis (worst: SK Braunschweig at 18.5 % synth vs. 51.2 % Zensus).
Root cause: `household_size` is **not** an IPF margin. It is sampled
post-IPF in [bavaria/synthesis/population/enriched.py](bavaria/synthesis/population/enriched.py#L199)
from a regions-aggregated `bavaria.data.census.household_size`
distribution, which:

1. uses Bavaria-wide age × sex shares (no per-Gemeinde / per-Kreis
   resolution),
2. cannot reflect the strong urban-rural split in ZGB-8 (SK Braunschweig
   ≈ 51 % 1P vs. LK Wolfenbüttel ≈ 35 % 1P).

This plan adopts **Option B** (clean solution): we wire the four
newly-downloaded Zensus 2022 ZIPs (`1000A-3082` Gemeinde × Alter ×
Geschlecht × HH-Größe; `1000A-2081` Gemeinde × Größe × HH-Typ-Familien;
`1000A-2087` Gemeinde × Größe × Lebensform; `1000A-2095` Gemeinde × Größe
× Kernfamilie) into the Bavaria IPF stage and add HH-size as a fifth
balanced margin. The result is a per-(commune, sex, age, hh_size) joint
weight that the synthesis stage consumes deterministically — no more
random sampling from a regions-mean.

**User has explicitly relaxed CON-001** (bavaria/** read-only) for this
work because Bavaria's IPF is the only place where a per-commune
HH-size margin can be enforced consistently.

## 1. Requirements & Constraints

- **REQ-001**: Add `hh_size` ∈ {1, 2, 3, 4, 5, 6+} as the 5th IPF margin in `bavaria.ipf.model` such that the IPF balances against a `commune × sex × age_class × hh_size` cell from `1000A-3082`.
- **REQ-002**: Ingest `1000A-3082` (4-way Gemeinde × ALTKL2 × GESCH1 × HSHGR2) as the primary HH-size constraint; treat `value_q='e'` (suppressed) cells as 0.
- **REQ-003**: Ingest `1000A-2081` (Gemeinde × HSHGR2 × HSHTP1) as auxiliary margin — used only for **post-IPF consistency check** and reporting, not as a balanced margin (avoids further cell explosion).
- **REQ-004**: Per-Kreis 1P-share deviation in the validation report drops to < 5 pp (target < 3 pp for SK BS).
- **REQ-005**: Total population per commune unchanged within ±0.5 % after IPF (balance is preserved by adding a constraint, not by replacing population).
- **REQ-006**: The new IPF margin is opt-in via a config switch `bavaria.ipf.use_household_size_margin` (default `True` for Braunschweig configs, `False` for legacy Bavaria configs to keep regression behaviour).
- **REQ-007**: ALTKL2 11-Klassen (`ALT000B005, ALT005B009, ALT010B014, ALT015B019, ALT020B024, ALT025B029, ALT030B039, ALT040B049, ALT050B059, ALT060B074, ALT075UM`) are mapped onto Bavaria's existing `age_class` scheme (5-year groups + 75+) using the lower-bound of the Zensus interval; HH-size cell gets distributed across overlapping Bavaria age_classes proportional to commune-level age population already present in the IPF.
- **REQ-008**: The new stages live in `braunschweig/data/census/` so they are local to the BS scope and do not pollute Bavaria's census loaders, but Bavaria's IPF reads them through `context.stage(...)` only when the new config flag is set.
- **CON-001 (RELAXED)**: User has granted permission to modify files in `bavaria/**` strictly for adding the optional HH-size margin; existing behaviour for legacy Bavaria configs MUST be preserved when `use_household_size_margin=False`.
- **CON-002**: `eqasim-java/**` remains read-only (no MATSim XML schema changes are needed; `household_size` is a CSV/synth-only attribute).
- **CON-003**: All new logic stays inside synpp stages (no ad-hoc scripts in the build path).
- **CON-004**: No new conda dependencies; reuse pandas 1.5.3, numpy 1.23.5, stdlib `zipfile`, `io`.
- **CON-005**: Data ingestion code MUST NOT inline-parse CSVs in stages other than the dedicated `braunschweig.data.census.*` loaders.
- **CON-006**: IPF iteration count stays bounded (≤ 2000); convergence threshold remains `< 1e-2` factor deviation.
- **CON-007**: Cell explosion is contained: existing index has dims `(commune ≈ 791) × sex(2) × age_class(~16) × employed(2) × license(2) ≈ 162 K` cells. Adding `hh_size(6)` → ≈ 970 K cells. We accept this; `numpy.float64` weights → ~7 MiB. NO sparse storage required.
- **GUD-001**: One synpp stage per Zensus table; pure functions, no side-effects beyond `return df`.
- **GUD-002**: ARS-5 → Kreis filter happens inside each loader (filter to ZGB-8 prefixes), keeping downstream maths small.
- **GUD-003**: All new code uses English comments, German variable labels only inside docstrings explaining Zensus columns.
- **PAT-001**: Follow the existing pattern in [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py) for ZIP discovery, CSV reading via `zipfile.ZipFile + pd.read_csv(sep=';', dtype=str)` and ARS-5 filtering.
- **PAT-002**: Follow the IPF selector/target idiom in [bavaria/ipf/model.py](bavaria/ipf/model.py#L60) — append a new selectors-list block guarded by the config flag.

## 2. Implementation Steps

### Implementation Phase 1 — Data ingestion (new synpp stages)

- GOAL-001: Provide clean, pre-aggregated DataFrames keyed on commune/sex/age/hh_size that the IPF can consume directly.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Create `braunschweig/data/census/households_size_age.py` with `configure(context)` declaring `data_path` config and `braunschweig.households_size_age_path` (default `braunschweig/1000A-3082_de_flat.zip`). `execute(context)` opens the ZIP, reads the single CSV with `dtype=str`, filters `1_variable_attribute_code.str[:5].isin(ZGB8_KREISE)` (constant `('03101','03102','03103','03151','03153','03154','03157','03158')`), drops rows where `2_variable_attribute_code` or `3_variable_attribute_code` or `4_variable_attribute_code` is missing/Insgesamt, parses `value` (replace `-`/`e`-flagged with `0`), maps HSHGR2 codes (`PERSON01..PERSON05` → `"1".."5"`, `PERSON06UM` → `"6+"`), maps GESCH1 (`GESM`→`male`, `GESF`→`female`), maps ALTKL2 to `(lower_age, upper_age)` tuple via fixed dict (`ALT000B005`→(0,5), …, `ALT075UM`→(75,150)). Returns `DataFrame[commune_id, sex, lower_age, upper_age, hh_size, weight]`. |  |  |
| TASK-002 | Create `braunschweig/data/census/households_type.py` with the same pattern for `1000A-2081_de_flat.zip` (3-way Gemeinde × HSHGR2 × HSHTP1). Map HSHTP1 codes `HSH-EIN→single`, `HSH-PAAR-KINDX→couple`, `HSH-PAAR-KIND→couple_with_children`, `HSH-ALLEIN→single_parent`, `HSH-MEHR→other_multi`. Drop `Insgesamt`. Returns `DataFrame[commune_id, hh_size, hh_type, weight]`. |  |  |
| TASK-003 | (Optional, descriptive only) Create `braunschweig/data/census/households_lifeform.py` for `1000A-2087` and `households_corefamily.py` for `1000A-2095`. Same loader pattern. These are NOT consumed by IPF; they exist so the validation report can cross-check household typology. Mark as "low priority — implement only if Phase 4 reveals additional bias dimensions". |  |  |
| TASK-004 | Add `validate(context)` to all new loaders that asserts the ZIP exists at `data_path/<path>` (mirrors the existing `bavaria/data/census/household_size.py::validate` pattern). |  |  |
| TASK-005 | Update `eqasim-data/DOWNLOAD_CHECKLIST_BS.md` with checkboxes for `1000A-3082_de_flat.zip` and `1000A-2081_de_flat.zip` and a one-liner pointing at https://ergebnisse.zensus2022.de. |  |  |

### Implementation Phase 2 — IPF integration

- GOAL-002: Make Bavaria's IPF balance against the new HH-size margin when the config flag is set, while preserving exact legacy behaviour otherwise.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-006 | Modify [bavaria/ipf/prepare.py](bavaria/ipf/prepare.py): add config `bavaria.ipf.use_household_size_margin` (default `False`). When `True`, also `context.stage("braunschweig.data.census.households_size_age")` and emit `df_household_size_margin` with columns `[commune_index, sex, age_class, hh_size_index, weight]` where `age_class` is derived from `(lower_age, upper_age)` by *expanding* each Zensus row to all overlapping Bavaria age_classes weighted by current commune-level age population (taken from `df_population`). Algorithm: for each row in raw 4-way table, list Bavaria age_classes that fall inside `[lower_age, upper_age)`, distribute the row's weight proportionally to `df_population` shares of those age_classes within the same commune+sex. Returns the existing 4-tuple plus the new DataFrame as a 5-tuple. |  |  |
| TASK-007 | Modify [bavaria/ipf/model.py](bavaria/ipf/model.py): extend the MultiIndex to `(commune_index, sex_index, age_class, employed, license, hh_size_index)` with `hh_size_index ∈ {0,1,2,3,4,5}` mapping `1..6+`. Initialise `weight=1.0`. When `use_household_size_margin=True`, append a new selector block: for each (commune, sex, age_class, hh_size) cell with `target>0`, build a boolean mask via `df_model.set_index([...]).index.isin([(c,s,a,*,*,h)])` (use `pd.MultiIndex.get_locs` with slicers for unconstrained dims) and rescale `weights[mask] *= target / sum(weights[mask])`. Increase max iterations to 2000. |  |  |
| TASK-008 | Add a regression-safe early exit: if `use_household_size_margin=False`, the model's MultiIndex collapses `hh_size_index` to a singleton (i.e. behaves exactly like the legacy 5-dim index) and the new selector block is skipped. Verified by running an existing Bavaria config and asserting bit-identical IPF output. |  |  |
| TASK-009 | Output schema of `bavaria.ipf.model` gains a `household_size` column (string `"1".."5","6+"`) only when the flag is on; downstream stages must tolerate both shapes. |  |  |
| TASK-010 | Add iteration logging: print `iter, max_factor, min_factor, max_hh_factor` every 100 iterations to spot non-convergence early. |  |  |

### Implementation Phase 3 — Downstream wiring

- GOAL-003: Make `bavaria/synthesis/population/enriched.py` consume the IPF-assigned `household_size` directly when present, falling back to the existing post-hoc sampling otherwise.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | In [bavaria/synthesis/population/enriched.py](bavaria/synthesis/population/enriched.py#L199), wrap the existing `df_household_size` sampling block in `if "household_size" not in df_persons.columns:`. The IPF-derived assignment, if present, is propagated through `bavaria.ipf.sampled` (the stage that draws individuals from IPF weights) and arrives in `df_persons` as a categorical column. |  |  |
| TASK-012 | Modify `bavaria/ipf/sampled.py` (locate via `grep_search` first) so the sampler carries `household_size` from the IPF cell to each drawn agent. If the column already flows through, this is a no-op. |  |  |
| TASK-013 | Verify [bavaria/data/census/household_income.py](bavaria/data/census/household_income.py) still works: it joins on `household_size`, which is the same key — no change expected. Add a unit test that the join produces no NaN income shares. |  |  |
| TASK-014 | Wire the new config flag into [config_local_braunschweig_10pct.yml](config_local_braunschweig_10pct.yml), [config_local_braunschweig.yml](config_local_braunschweig.yml), [config_dryrun_braunschweig.yml](config_dryrun_braunschweig.yml) and [config_gravity_only_braunschweig.yml](config_gravity_only_braunschweig.yml). Set `bavaria.ipf.use_household_size_margin: true`. Leave Bavaria configs (`config_bavaria.yml`, `config_local_bavaria.yml`, `config_tum.yml`) untouched (flag stays default `False`). |  |  |

### Implementation Phase 4 — Validation & cache invalidation

- GOAL-004: Re-run the BS 10 % pipeline and confirm the per-Kreis HH-size deviation is < 5 pp.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-015 | Clear caches for affected stages: `bavaria.ipf.*`, `bavaria.synthesis.population.*`, `braunschweig.synthesis.*`, `matsim.*`, `braunschweig.data.census.households_*`. Use synpp's content-hashed cache directory `eqasim-data/cache_bs_10pct/`. Document cache hashes that change in `plan/feature-bs-ipf-hhsize-1.md` after first run. |  |  |
| TASK-016 | Run the 10 % pipeline: `python -u -m synpp config_local_braunschweig_10pct.yml`. Capture stdout to `logs/ipf_hhsize_run_<timestamp>.log`. Wall-clock budget: < 25 min. |  |  |
| TASK-017 | Run `python -m scripts.validate_bs_10pct`; inspect `validation_report.html` chapter "Haushaltsgröße". Confirm 1P-share deviation per Kreis < 5 pp (TEST-001) and 2..6+ shares within ±3 pp. |  |  |
| TASK-018 | Run `python -m scripts.inspect_hh_gap` (existing diagnostic) and confirm the delta matrix turns green (|delta| < 5 pp in every cell). |  |  |
| TASK-019 | Spot-check IPF convergence: number of iterations reported in stage log < 2000, final `max_factor - 1 < 1e-2`. |  |  |

### Implementation Phase 5 — Tests

- GOAL-005: Lock in the new behaviour with deterministic unit tests.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-020 | Add `tests/test_braunschweig_data.py::test_households_size_age_loader`: instantiate the stage with the real ZIP, assert columns `[commune_id, sex, lower_age, upper_age, hh_size, weight]`, assert `weight.sum()` matches Zensus 2022 ZGB-8 total population within 1 %, assert all `commune_id` start with one of the 8 ZGB-8 prefixes. |  |  |
| TASK-021 | Add `tests/test_braunschweig_data.py::test_households_type_loader`: same pattern for `1000A-2081`. Assert sum of `value` for commune SK BS (`031010000000`) matches the 136 611 households figure (within 0.1 %). |  |  |
| TASK-022 | Add `tests/test_pipeline.py::test_ipf_hh_size_margin_balanced` (slow, marked `@pytest.mark.slow`): run the IPF stage on a fixture commune-subset, assert that for every (commune, sex, age_class, hh_size) cell the IPF output marginal matches the target within 2 %. |  |  |
| TASK-023 | Add `tests/test_determinism.py::test_hhsize_margin_deterministic`: run the pipeline twice with `random_seed=42`; assert `df_persons.household_size` is identical. |  |  |
| TASK-024 | Run `pytest tests/ -k "households or ipf_hh_size or hhsize_margin"` and document results in the validation report. |  |  |

## 3. Alternatives

- **ALT-001**: Option A — BS-only override stage that overwrites `household_size` post Bavaria-enriched using a per-commune Zensus draw. **Rejected**: it doesn't fix the IPF marginal consistency (the joint distribution with sex/age remains broken) and creates two sources of truth for `household_size`.
- **ALT-002**: Option C — per-Kreis sampling without IPF integration (replace the regions-aggregated draw in `enriched.py` with a per-Kreis draw using `1000A-3082`). **Rejected** because, while it fixes the marginal, it does not enforce consistency with the (sex, age) margins balanced by IPF — small Gemeinden could end up over-represented in 1P-HH because a 25-year-old male is preferentially drawn for a 1P bin but might already be balanced as employed in another commune.
- **ALT-003**: Use 5000H-2001 (existing `braunschweig.data.census.household_size` loader) at Kreis-level only. **Rejected**: 5000H-2001 has no commune-level resolution we can use, and no sex/age cross-tab — exactly what 1000A-3082 fixes.
- **ALT-004**: Add `hh_type` (HSHTP1) as an additional IPF margin. **Rejected for v1**: doubles cell count again (≈ 5 M cells) for marginal benefit; better validated post-IPF as a reporting check (REQ-003).

## 4. Dependencies

- **DEP-001**: pandas 1.5.3, numpy 1.23.5, stdlib `zipfile`, `io` (already in conda `eqasim` env).
- **DEP-002**: synpp ≥ 1.5 (already pinned).
- **DEP-003**: Existing 5000H-2001 loader at [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py) — kept for backwards compatibility but no longer wired into the IPF after this change.
- **DEP-004**: Validation harness at [scripts/validate_bs_10pct/](scripts/validate_bs_10pct/) — used as acceptance gate.
- **DEP-005**: Diagnostic at [scripts/inspect_hh_gap.py](scripts/inspect_hh_gap.py) — used as smoke test.
- **DEP-006**: Bavaria IPF infrastructure ([bavaria/ipf/prepare.py](bavaria/ipf/prepare.py), [bavaria/ipf/model.py](bavaria/ipf/model.py), `bavaria/ipf/sampled.py`).

## 5. Files

- **FILE-001** (input, present): `eqasim-data/data/braunschweig/1000A-3082_de_flat.zip` (61 MB).
- **FILE-002** (input, present): `eqasim-data/data/braunschweig/1000A-2081_de_flat.zip` (9.6 MB).
- **FILE-003** (input, optional): `eqasim-data/data/braunschweig/1000A-2087_de_flat.zip` (12.8 MB).
- **FILE-004** (input, optional): `eqasim-data/data/braunschweig/1000A-2095_de_flat.zip` (6.4 MB).
- **FILE-005** (new): `braunschweig/data/census/households_size_age.py` — 4-way primary IPF margin loader.
- **FILE-006** (new): `braunschweig/data/census/households_type.py` — Familien-HH-Typ loader.
- **FILE-007** (new, optional): `braunschweig/data/census/households_lifeform.py`, `households_corefamily.py`.
- **FILE-008** (modify): [bavaria/ipf/prepare.py](bavaria/ipf/prepare.py) — emit new margin DataFrame when flag is set.
- **FILE-009** (modify): [bavaria/ipf/model.py](bavaria/ipf/model.py) — add `hh_size_index` dim & selector block.
- **FILE-010** (modify): `bavaria/ipf/sampled.py` — propagate `household_size` to drawn agents.
- **FILE-011** (modify): [bavaria/synthesis/population/enriched.py](bavaria/synthesis/population/enriched.py) — guard legacy sampling behind column-presence check.
- **FILE-012** (modify): [config_local_braunschweig_10pct.yml](config_local_braunschweig_10pct.yml) and the three sister BS configs — set `bavaria.ipf.use_household_size_margin: true`.
- **FILE-013** (modify): [eqasim-data/DOWNLOAD_CHECKLIST_BS.md](eqasim-data/DOWNLOAD_CHECKLIST_BS.md) — document the new ZIPs.
- **FILE-014** (modify): [tests/test_braunschweig_data.py](tests/test_braunschweig_data.py) — new loader tests.
- **FILE-015** (modify): [tests/test_pipeline.py](tests/test_pipeline.py) — IPF margin balance test.
- **FILE-016** (modify): [tests/test_determinism.py](tests/test_determinism.py) — determinism guard.

## 6. Testing

- **TEST-001**: Per-Kreis 1P-share deviation < 5 pp (target < 3 pp for SK BS) — verified via `scripts.validate_bs_10pct` and `scripts.inspect_hh_gap`.
- **TEST-002**: Per-Kreis 2..6+ shares deviation < 3 pp.
- **TEST-003**: Total population per commune unchanged within ±0.5 % (regression on existing `tests/test_braunschweig_data.py::test_population_kreis_totals` if present, else add).
- **TEST-004**: IPF converges in < 2000 iterations with `max_factor - 1 < 1e-2`.
- **TEST-005**: With `use_household_size_margin=False`, the Bavaria pipeline output is bit-identical to the pre-change pipeline (regression test on `bavaria/ipf/model` cache hash).
- **TEST-006**: Determinism — two consecutive 10 % BS runs with `random_seed=42` yield identical `df_persons.household_size`.
- **TEST-007**: HH-Typ-Familien aggregate (post-IPF) matches `1000A-2081` per Kreis within 5 pp on the dominant types (Single ≡ 1P-HH, Paare-mit-Kindern, Alleinerziehend) — descriptive check, not a hard gate.

## 7. Risks & Assumptions

- **RISK-001**: IPF cell-count grows ~6× (≈ 162 K → 970 K cells). Memory ≈ 7 MiB for weights only, but selector boolean masks for ~970 K cells × ~5 K margin targets (commune × sex × age × size = 791 × 2 × 16 × 6 ≈ 152 K) could become slow. **Mitigation**: precompute `pd.Index.get_locs` once per selector, cache as `np.ndarray[int]` integer indices instead of boolean masks.
- **RISK-002**: `1000A-3082` has many suppressed cells (`value_q='e'`) at small Gemeinden — treating them as 0 may produce IPF infeasibility (target=0 with df_model rows present). **Mitigation**: wherever `target<1` for a (commune, sex, age, hh_size) cell, smooth by adding a tiny epsilon (`eps=0.01`) before normalisation; alternatively, aggregate the HH-size margin to Kreis-level for Gemeinden with > 30 % suppressed cells.
- **RISK-003**: ALTKL2 11-Klassen don't align with Bavaria's 5-year age_classes for `(30..39, 40..49, 50..59, 60..74, 75+)`. Distributing Zensus 11-class weight across Bavaria 5-year groups by commune-level age population is heuristic. **Mitigation**: validated empirically in TASK-017; if bias remains, switch Bavaria's IPF age_class scheme for Braunschweig configs to the 11-Klassen scheme directly (config flag `bavaria.ipf.age_class_scheme: zensus11`).
- **RISK-004**: Modifying Bavaria IPF risks regressing the legacy Bavaria configs. **Mitigation**: REQ-006 (config-flag-gated) + TEST-005 (bit-identical regression).
- **RISK-005**: Iteration count may need to grow beyond 2000 for the harder feasibility region. **Mitigation**: monitor in TASK-019; if non-converging, fall back to relaxation (target = 0.7·observed + 0.3·model after 1500 iters).
- **ASSUMPTION-001**: ALTKL2 lower-bound mapping is acceptable for the IPF (REQ-007). Verified post-hoc against per-age-class population marginals.
- **ASSUMPTION-002**: `1000A-3082` Gemeinde codes (`1_variable_attribute_code`, 12-digit ARS) correspond 1:1 with Bavaria's `commune_id` (which is AGS-8 in BS configs). The first 8 chars of the 12-digit ARS equal the AGS-8 — verified for SK BS (`031010000000` → `03101000`).
- **ASSUMPTION-003**: 1P-HH ≡ "Einpersonenhaushalte (Singlehaushalte)" exactly (1:1 in `1000A-2081` cross-tab confirmed via `scripts/inspect_zensus_hh.py`).
- **ASSUMPTION-004**: Six bins (1, 2, 3, 4, 5, 6+) are the right granularity; the existing post-IPF sampler already uses this binning and INKAR Haushaltseinkommen joins on the same bins.

## 8. Related Specifications

- [plan/feature-bs-validation-10pct-1.md](plan/feature-bs-validation-10pct-1.md) — the validation harness whose `Haushaltsgröße` chapter is the acceptance gate for this work.
- [plan/calibration-analysis-2025.md](plan/calibration-analysis-2025.md) — calibration narrative; this work resolves the "1P-HH bias" entry.
- [plan/migration-braunschweig-1.md](plan/migration-braunschweig-1.md) — original BS migration plan; CON-001 is relaxed only for this feature.
- Zensus 2022 cube documentation: https://ergebnisse.zensus2022.de — variable codebooks for `GEOGM4`, `ALTKL2`, `GESCH1`, `HSHGR2`, `HSHTP1`, `HSHTP2`, `FAMTP1`.
- Bavaria IPF reference: [bavaria/ipf/model.py](bavaria/ipf/model.py) selector/target loop pattern.
