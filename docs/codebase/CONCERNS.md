# CONCERNS

> **Actionable items live as GitHub issues (2026-08-13, ADR-0077)** — notably
> the readiness-register findings #251–#255 (flags OFF despite ON-claims,
> simple_ipf_open-only enrichment features, simwrapper default contradiction,
> ADR path errors, uninstrumented employment-margin fallback). This file keeps
> fragile-area notes and dated audit narratives for context (the 2026-07-10 feature-wiring audit moved to
> `docs/archive/CONCERNS_feature_wiring_audit_2026-07-10.md`); treat every dated
> claim below as a snapshot, not current state. The ranked backlog is the open
> GitHub issues on `TUBS-IVS/eqasim-bs`; decisions are the ADRs in
> `docs/decisions/` (generated index `docs/generated/DECISIONS.md`). The retired
> `PROJECT_STATUS.md`, `PROJECT_BACKLOG.md`, `RUNS.md` and monolithic
> `DECISIONS.md` are pointer stubs (content in `docs/archive/`), never canonical.

Tech debt, risks, and divergences for `eqasim-bs` that are **verifiable from the
repository**. Bugs inherited from the upstream Bavaria/IDF pipeline (referred to
as BUG-001..011 in README/AGENTS.md) are tracked by the team but are not
re-derived here; this file records what the current branch actually shows.

## Data / provenance risks

- **`eqasim-data/` is gitignored with force-added exceptions.** `.gitignore` has
  `eqasim-data/*` and whitelists only `DOWNLOAD_CHECKLIST*.md`. the files
  tracked under `eqasim-data/` are listed by `git ls-files eqasim-data` — the MiD/Mikrozensus
  reference CSVs, school/kita/hochschule CSVs, and education-calibration outputs.
  These are committed reference data. Provenance/licence care is required: **MiD
  2023 is BMDV non-commercial**, and the extracted CSVs are derivative works that
  inherit those terms (licence per dataset: the Data Registry, `docs/registry/data/`). Anything committed must respect
  re-distribution rules; urbistat shares are explicitly non-redistributable.
- ~~**Legacy dead-config keys.**~~ **RESOLVED (issue #251).** Five keys were set across the
  committed configs while no live code could read them and have been DELETED, not documented:
  `home_location_sampling` and `osm_path_bavaria` (named by no source file at all), plus
  `braunschweig.population_path`, `braunschweig.work_flow_path` and
  `braunschweig.buildings_path` (read only by the inherited `eqasim_common.data.census.population`
  / `.employees` / `eqasim_common.data.buildings` loaders, which the Braunschweig forks replaced
  and which appear in no DAG snapshot and are no config's alias target). Two of them pointed at
  files that do not exist on disk, which is how they surfaced. Note the scope the earlier entry
  understated: the keys were in ten configs including `configs/base_bs.yml`, not one fixture.
- **ENTD 2008 (French HTS) donor.** Activity chains are still seeded from a 2008
  French survey; a German HTS replacement is open work (README "Known limitations").

## Environment / tooling

- **Broken BLAS/LAPACK in the local env.** NumPy linalg/SVD/GLM calls crash in
  the `eqasim` conda env (reference BLAS); the synpp pipeline runs, but GLM-based
  calibration scripts (`scripts/calibrate_gravity_per_rs7.py`) do not (user-memory
  `eqasim-env-lapack-broken.md`). Affects calibration reproducibility on that env.
- **Per-machine absolute Windows binary paths** baked into the config
  (`osmosis_binary`, `osmconvert_binary` under `C:/Users/<user>/tools/...`) —
  not portable to other contributors/CI without editing.

## Large files / performance

From the scan's CODE METRICS (largest tracked-or-cached files; most are under the
gitignored `eqasim-data/` cache and **not** in version control, but they dominate
disk/runtime):
- `eqasim-data/data/braunschweig/buildings/buildings_with_households_NI_260128.gpkg`
  (~4.0 GB) — largest input.
- `eqasim-data/data/braunschweig/landuse/FS_LN_03_NI_260101.gpkg` (~3.2 GB) and its zip.
- `…/osm/bayern-260421.osm.pbf` (~800 MB) — note a **Bayern** OSM PBF is present
  even though the region is Niedersachsen; likely a stale leftover. `[ASK USER]`.
- MATSim simulation outputs / events under `cache_*` reach 0.8–3.9 GB each.

These are cache/data artefacts, not source, but they signal heavy memory/disk and
long wall times (README: 25 % run ~10 h).

## Repository hygiene

- Loose top-level scratch artefacts are present and likely should not be tracked:
  `hh_gap.txt`, `zensus_hh_inspect.txt`, `pipeline_run.log`,
  `pipeline_10pct*.log`, `TODO_Bavaria`. `[ASK USER]` whether these belong in the
  repo or should be gitignored.
- The pre-existing **uncommitted** notebook `braunschweig/analysis/population_fit.ipynb`
  shows as modified (`git status` at session start); it is unrelated to this
  documentation task and is left untouched.

## Production code TODOs (from scan, source only)

- `braunschweig/synthesis/population/enriched/` — a `# TODO` on the `sex` filter of `df_persons`
- (All other scanned TODOs are inside the **cached Java** checkouts under
  `eqasim-data/cache_*`, which are third-party read-only sources, not this repo's code.)

## Test-only gaps (separated from production debt)

- The opt-in pipeline/simulation/determinism tests are skipped by default; the
  documented pass/fail baseline differs between sources (README 65 pass / 4 skip
  vs. AGENTS.md 53 pass / 11 fail). The "11 failing" set is inherited-IDF behaviour
  the team chose not to fix during the refactor (Decision D-5), not new regressions.

## Evidence

- `.gitignore` (`eqasim-data/*` + force-added exceptions); `git ls-files eqasim-data`
- `configs/fixtures/config_local_braunschweig.yml` (legacy keys, binary paths)
- `docs/registry/data/*.yml` (per-dataset licence and restrictions)
- `docs/codebase/.codebase-scan.txt` (CODE METRICS, production TODOs, high-churn files)
- user-memory `eqasim-env-lapack-broken.md`

---

## Cross-repo addendum: risks for the population-synthesis refactor (popsimprep)

Added 2026-06-08. Risks specific to folding popsimprep's PopulationSim workflow
into quaSIM as `popsim_open` / `popsim_mid`.

### Data-safety risks (highest priority — MiD)

1. **Uncommitted MiD-derived outputs are NOT gitignored in popsimprep.**
   `buildings_with_mid_data.gpkg` (per-building JSON of MiD household+person
   attributes), `buildings_with_assigned_households.gpkg`, `wege_temp_chunks/`
   (expanded MiD Wege with home coords), and `validation_results/` are produced at
   the repo root and are **not** in `.gitignore`. A stray `git add -A` would commit
   restricted microdata. **Must add ignore rules before generating these in any
   tracked location.** Evidence: `popsimprep/.gitignore`, notebook Steps 5/6.
2. **Raw MiD path is implicit / copy-step undocumented.** The notebook reads
   `inputs/MiD2023_*.csv` (repo root of `inputs/`), but the dataset package ships at
   `inputs/MiD2023/MiD2023_B1_Datensatzpaket/CSV/...` — a copy/symlink step is
   implied but not in code. Path must become config/env-driven, local-only, with a
   guard that fails clearly when absent (for `popsim_mid` only). Evidence: notebook
   Cell 2 vs `inputs/MiD2023/` listing.
3. **`popsim_open` must be provably MiD-free.** The notebook currently hard-wires
   MiD seeds; there is no open-seed path yet. Risk of accidental MiD leakage into
   the "open" workflow. Needs a test asserting no MiD path is read when
   `method != popsim_mid` (brief §10 "no silent fallback between workflows").

### Correctness / reproducibility risks

4. **Config is split across 4 sources of truth.** Notebook Cell-2 variables (live),
   `prep_config.json` (legacy BS, different control naming), and `settings.yaml` /
   `verification.yaml` (which are BOTH templates AND machine-mutated in place by
   Cell 6). Hidden notebook state + in-place YAML mutation make runs
   non-reproducible. The refactor must collapse these into one declarative config.
5. **The control set is a hand-edited CSV mid-pipeline.** Between Step 2 and Step 3
   the user manually fills `seed_table/importance/expression` in
   `_prep3_controls.csv`; only rows with a non-empty expression are used. This
   manual edit IS the de-facto control definition and must become declarative config.
6. **Hard-coded values buried in code cells** (must be parameterised): CRS literals
   `EPSG:3035` / `epsg25832`; the INSPIRE cell-id regex + 1000 m flooring
   (duplicated across Cells 4/6/8); `clean_col_name` (duplicated Cells 6/8); MiD CSV
   `sep=','` hard-coded but the config comment says semicolon (contradiction —
   verify the real delimiter); the `kernwo` "complete household" drop rule (a
   scientific assumption); geography names; `importance=1000`. Evidence: notebook
   manual-settings inventory.
7. **`verify.py` hard-codes a foreign absolute path** (`C:\Users\<developer>\...`) —
   will not run for this user; must be parameterised.
8. **Cross-batch ID safety is invariant-fragile.** Merge does NOT renumber `H_ID`;
   uniqueness relies entirely on cell-disjoint partitioning so `(ZENSUS100m, H_ID)`
   stays unique. Any change to batching that breaks cell-disjointness silently
   corrupts global IDs. Preserve + test this invariant.
9. **`census_100m_path` in Cell 2 points at a filename that differs from the file
   on disk** (`..._with_aggs_regiostar.parquet` vs the actual
   `...happyorphans.parquet`). The live config references an enriched variant that
   may not be the committed input — verify which parquet is canonical.

### Environment / performance risks

10. **BLAS/LAPACK collision.** PopulationSim's balancers need a working LAPACK; the
    eqasim conda env has a broken reference BLAS (see main CONCERNS above). Running
    PopulationSim inside the eqasim env will likely fail — a separate env /
    subprocess is probably required. `[ASK USER]` env strategy.
11. **PopulationSim does not scale in one run** (the entire reason for
    `batch_run_popsim.py`). At full ZGB the 100 m cell count is large; batch sizing
    (`--max-cells`, default 3000), worker count (default 3), and the 1 h per-batch
    timeout all need to be config-driven and validated. Big parquet (7.3 GB 100 m
    file) means careful streamed reads (the notebook already uses
    `iter_batches`).
12. **Python/dependency version gap** (3.11 + pandas≥2.2 vs 3.10 + pandas 1.5.3).
    A merged or bridged environment must satisfy both or stay split.

### Open questions for the refactor (consolidated, `[ASK USER]`)

- Env strategy: one merged env, or PopulationSim in its own env via subprocess?
- Open seed for `popsim_open`: which dataset, where, and what activity/trip source
  when MiD is absent?
- Provenance/reproducibility of the preprocessed cell parquets (backfill,
  gender-backfill, "happyorphans") — is the preprocessing script available?
- Should the three workflows produce byte-identical output schemas, or a shared
  superset with documented optional columns?

Evidence: `popsimprep/PopSimPrep-StartHere-v2.ipynb`, `popsimprep/batch_run_popsim.py`,
`popsimprep/.gitignore`, `popsimprep/scripts/verify.py`,
`popsimprep/popsim/configs/{settings.yaml,_prep3_controls.csv,prep_config.json}`,
`popsimprep/inputs/` listing, `popsimprep/docker/dev.Dockerfile`.

---

## Review 2026-06-10: branch state + popsim feature-parity gaps

Findings from the systematic review of the implemented three-workflow refactor
(worktree `popsim-g5`, branch `feature/population-method-workflows`, tip
`e6806b2`) against the legacy IPF path on main.

### Repository / branch state

1. **Main checkout is on a detached HEAD** (`fd7e335`, an ancestor of the popsim
   branch tip). Working-tree edits (CLAUDE.md, MiD status CSVs, simwrapper
   artefacts) sit on this detached HEAD — commits made here without re-attaching
   risk being orphaned. Re-attach to a branch before committing.
2. **The popsim branch does NOT contain the merged cordon feature.** Merge-base
   with main is `a9fd530`; all cordon commits (up to `4f68965`) landed on main
   after that. The popsim configs have no `cordon_enabled` key, and the cordon
   injection's terminal concat wrappers have never been tested against the
   popsim persons schema. Rebase/merge main into the popsim branch is required
   before any combined run.
3. Three worktrees exist (`popsim-g5`, `cordon-whole-region-gates` — fully merged,
   can likely be removed — and `simwrapper`).

### Feature-parity gaps: legacy IPF enrichment vs popsim paths

The popsim paths inherit core attributes from the donor (licence, PT ticket,
cars, bikes, income class — OK and intentional), but the following legacy
features are ABSENT or silently off in the popsim configs:

| # | Feature (legacy flag) | popsim_mid | popsim_open | Severity |
|---|---|---|---|---|
| P1 | `economic_status` | direct MiD `oek_status` (no hhtype Bayes — acceptable, donor value is real data) | **MISSING entirely** (ENTD has no status field; schema marks optional) | HIGH |
| P2 | `synthesise_housing_tenure` | missing (no flag, column never created) | missing | HIGH (if tenure used downstream) |
| P3 | `cars_income_aware` (P(cars\|hhtype,status,raumtyp)) | n/a — donor H_ANZAUTO is real joint data (BETTER than legacy resampling) | ENTD H_ANZAUTO (French car ownership!) | MEDIUM (popsim_open only) |
| P4 | `income_eur_from_distribution` (continuous EUR draw) | class-midpoint only (deterministic per class) | class-midpoint only | MEDIUM |
| P5 | `education_gravity_enabled` | **explicitly `false`** in config | explicitly `false` | MEDIUM — NDS school gravity OFF means OSM sampler |
| P6 | `enable_urban_parking` + `remode_carless_car_legs` | keys absent (default false) | absent | MEDIUM (parking scenario parity) |
| P7 | `vehicles_method: household` + fleet model (KBA/HSN-TSN) | key absent — falls back to eqasim default vehicles | absent | HIGH for emissions/fleet runs |
| P8 | `cordon_enabled` | absent (branch predates merge) | absent | HIGH for the 100% scenario |
| P9 | IPF household-formation features (joint age×size, age-aware composition, sex-aware couples) | structurally replaced by donor households (intentional — donor composition is real) | same | OK by design, document it |

Items P3/P9 are *improvements* in popsim_mid (real joint donor data instead of
synthetic coupling) — the table flags divergence, not necessarily defects. For
popsim_open the French donor attributes (car ownership, income bands mapped
ENTD→MiD labels) are documented approximations.

### Un-logged fallback defaults (CLAUDE.md no-silent-fallback violations)

- `braunschweig/popsim/trips.py` MODE_BY_HVM: MiD `hvm=9` (missing) -> `walk`
  default, not counted/logged per occurrence.
- `data/hts/entd/cleaned.py`: unmapped ENTD purpose -> `other`, unmapped mode ->
  `pt` — silent in the legacy path too (inherited upstream behaviour).

### Validation status (as of 2026-06-10)

- popsim_open mini smoke: PASSED (EXITCODE 0, 12/12 stages, 1,196 persons,
  secondary-location success 97.16 %) — `smoke_popsim_open_mini_final.log`.
- popsim_mid smoke harness exists (`scripts/popsim_mid_smoke.py`,
  `config_smoke_popsim_mid*.yml`); three-case comparability tests in
  `tests/test_three_case_comparability.py`.

Evidence: Explore-agent audit 2026-06-10; `config_popsim_{mid,open}_braunschweig.yml`
vs `config_local_braunschweig_25pct_allfeat.yml`; `braunschweig/popsim/assembly.py`,
`attributes.py`, `sources/entd.py`; `matsim/scenario/population.py` PERSON_FIELDS;
`git merge-base main e6806b2` -> `a9fd530`.

### Code-review findings 2026-06-10 (popsim branch diff a9fd530..e6806b2, verified)

Confirmed by adversarial verification against code + the real local MiD raw data
(references are by file; line numbers were dropped, CLAUDE.md shape rule):

1. `braunschweig/gravity/model.py` stages `braunschweig.ipf.attributed`
   DIRECTLY (not via the `data.census.filtered` alias) — every popsim run also
   executes the full legacy IPF synthesis, and gravity OD weights are calibrated
   on the IPF population while demand comes from popsim (smoke logs show 25
   `braunschweig.ipf` stage executions in the popsim_mid run).
2. `braunschweig/popsim/trips.py` converts raw `W_SZS/W_SZM/W_AZS/W_AZM` with
   no missing-code filtering: codes 99 (8,224 Wege) and 701 (107,368 Wege ≈ 9.9 %)
   become departure times up to ~29 days; no absolute time bound exists anywhere
   downstream (fix_trip_times/PlanValidator only check relative consistency).
3. `braunschweig/popsim/missing.py` + `attributes.py`: codes not
   enumerated in value_map/structural/NONRESPONSE are "valid" -> NaN ->
   `.astype(bool)` -> **True**. Real hit: `P_TAET=17` (4,043 persons) silently
   becomes `employed=True`.
4. `braunschweig/popsim/seed.py`: "complete household" checks day-filter
   completeness only, not member completeness — 16.9 % of kept seed households
   have fewer person rows than `H_GR` (verified on raw data) -> household size/
   composition biased low, unlogged.
5. `braunschweig/popsim/stage/` calls `assembly.build_persons`
   without `rng` -> all attribute imputation runs on hard-coded `RandomState(0)`;
   `random_seed` is not even declared in configure(), so seed changes neither
   change imputation nor invalidate the synpp cache.
6. `braunschweig/popsim/trips.py`: raw `hvm` used instead of the
   handbook-mandated `hvm_imp`; `hvm=9` -> walk silently (4,041 Wege, no rate log).
7. `braunschweig/popsim/trips.py` + `commute_distance.py`: `W_ZWECK=2`
   (dienstlich) maps to "work" and commute distance takes the per-person MAX ->
   one long business trip (wegkm_imp caps at 950 km) becomes the commute distance;
   legacy uses first home<->work trip only.
8. `braunschweig/popsim/expand.py`: HP_SEX 3 (614) / 9 (127 persons) ->
   `sex="unknown"` -> MATSim attribute `"u"`; code 9 should be imputed per the
   missing policy; legacy paths emit only male/female.
9. `braunschweig/popsim/mid/seed_loading.py` (`hhgr_gr`, `alter_gr1`)
   [path updated for the #267 package split; was `mid.py`] (content not
   re-verified; #267 path-only update): the conditioning columns (`alter_gr1`,
   `hhgr_gr`) for group-conditioned imputation are never loaded, so the
   documented within-age-band imputation silently degrades to the global pool.
10. `braunschweig/popsim/mid/seed_loading.py` [path updated for the #267
    package split; was `mid.py`] (content not re-verified; #267 path-only
    update): `day_filter_values or default` — the day filter cannot be
    disabled (None and () both fall back to (1,2,3)), contract inconsistent
    with `filter_complete_households`.
11. `matsim/writers.py` `long_or_string_type` decides the Java type PER VALUE:
    mixed Long/String for the same attribute in one file is possible; float
    contamination writes "123.0" as java.lang.Long (Java parse failure); legacy
    path is provably unaffected (inner merges, arange ids) — popsim-only risk.
    `tests/test_matsim_id_types.py` misses float/NaN/leading-zero cases.
12. `braunschweig/popsim/expand.py`: donor-person join `how="left"` with no
    validation — an unmatched H_ID yields a silent all-NaN ghost person whose
    attributes become True via finding 3.

Refuted candidates (checked, not bugs): HP_ALTER missing codes (real data is
0-85 top-coded), wegkm_imp sentinels (capped at 950), merge dtype-inference
duplicate miss (INSPIRE ids always strings), missing-batch tolerance "silent"
(warns + raises above 10 %).

Cleanup (valid, lower priority): `control_spec.py` is dead production code (the
real controls come from the hand-edited popsimprep CSV); third largest-remainder
implementation; INKAR scaling duplicated vs `enriched.py` (high_income rule
already diverges: label "5000+" vs eur>=5000); jitter formula + detour factor
duplicated; `mid_raw_path` required even for popsim_open; `num_workers=3` default
wastes the 64-core server; `missing.py` per-element map + `handoff.py`/
`assembly.py` Python loops will hurt at 100 %; `source_name=="entd"` branches
scattered across stage.py/trips_stage.py instead of the adapter Protocol;
mapper-contract sniffing (`isinstance(result, tuple)`) can silently produce an
empty pseudonym map.
