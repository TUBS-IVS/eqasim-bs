# STRUCTURE

> **Staleness note (2026-06-26):** reflects the 2026-06-08 layout. New since then:
> `braunschweig/calibration/` (calibration corner — on `worktree-calibration-corner`),
> `braunschweig/data/building_potentials.py`, `braunschweig/popsim/{distance_distributions,
> shop_subtype}.py`, `braunschweig/analysis/popsim_validation/`. Current feature
> state: see ARCHITECTURE.md banner; open work: see CONCERNS.md banner.

Directory layout and entry points for `eqasim-bs`. Verified from the directory
tree (`docs/codebase/.codebase-scan.txt`) and direct listings.

## Entry point

```powershell
python scripts/run_synpp.py configs/fixtures/config_local_braunschweig.yml      # 1 % smoke run

# Composed production / all-features runs (config-composition cleanup, #230):
# a fixed base + a per-scale overlay, deep-merged and persisted as
# <working_directory>/.merged_config.yml (see configs/base_bs.yml header).
python scripts/run_synpp.py configs/base_bs.yml configs/overlays/test_25pct.yml
```

synpp reads a YAML config that declares the requested terminal stages under
`run:` and a stage-alias map under `aliases:`. Two config families exist side by
side:

- `configs/base_bs.yml` + `configs/overlays/{test,test_1pct,test_25pct,test_100pct,test_matsim}.yml`
  — the composed, all-features popsim_mid set. Everything fixed across scales
  lives once in the base; only `sampling_rate`, `working_directory`, output
  paths and worker counts differ per overlay. This is the current
  production/server path.
- `configs/fixtures/*.yml` — standalone (pre-composition), single-file configs
  kept as pytest fixtures and lightweight local entry points:

  | Config | Rate | Purpose |
  |--------|------|---------|
  | `configs/fixtures/config_local_braunschweig.yml` | 0.01 | Smoke / dev |
  | `configs/fixtures/config_local_braunschweig_10pct.yml` | 0.10 | Validation harness |
  | `configs/fixtures/config_local_braunschweig_25pct.yml` | 0.25 | Pre-release / calibration |
  | `configs/fixtures/config_dryrun_braunschweig.yml` | small | CI / plan-only sanity |

`CLAUDE.md` is the authoritative module guide for the calibrated subsystems
(MiD reference tables, gravity model, education gravity); read it alongside this
file.

## Top-level layout

```
braunschweig/        Regional fork modules (the active pipeline for ZGB-8)
eqasim_common/       Region-neutral building blocks shared by the fork
data/                Upstream eqasim (Île-de-France/France) data stages — donor + inherited
synthesis/           Upstream eqasim synthesis stages (output.py, population, locations, vehicles)
matsim/              Upstream eqasim MATSim scenario build (output.py, scenario, runtime, writers)
scripts/             One-off CLIs: preprocessing, calibration, seeding, verification, download
tests/               pytest suite (see TESTING.md)
eqasim-data/         Data root — gitignored except force-added reference CSVs and checklists
docs/                Project docs (this docs/codebase set + population.md / simulation.md)
plan/ quality/       Refactor plan + quality playbooks
config_*.yml         synpp run + alias configs
environment.yml      Pinned conda environment
README.md AGENTS.md CLAUDE.md   Intent / bootstrap / module guide
```

Note: the `bavaria/` directory described in README.md, AGENTS.md and CLAUDE.md
**does not exist on the current branch** (`feature/education-gravity-bs`).
Verified by `ls bavaria` -> "No such file or directory" and by `git ls-files`.
The config aliases that those docs describe as `bavaria.*` overrides now point to
`eqasim_common.*` and `braunschweig.*` instead. See CONCERNS.md (Intent-vs-Reality).

## `braunschweig/` (the regional fork)

```
braunschweig/
  data/            Input loaders, one subpackage per source:
    census/        DESTATIS/GENESIS population, employment, pendler, licenses, household_*
    mid/           MiD 2023 reference tables, references, school_distance, zones
    mikrozensus/   Mikrozensus 2024 school-distance reference
    schools/       NDS school / kita / university facilities, typing, readers
    bbsr/          RegioStaR-7 assignment (regiostar.py)
    inkar/         BBSR INKAR household income / full panel
    ba/            BA Pendleratlas detailed loader
    inspire/ zensus_grid/ vrb/ gtfs/   landuse, 100 m grid population, VRB zones, GTFS
    alkis.py buildings.py landuse.py locations.py osm.py external_workplaces.py
  ipf/             Iterative Proportional Fitting: model, prepare, attributed
  gravity/         model.py — work/education distance-decay gravity (per-RS7 slope)
  synthesis/
    population/    enriched.py (PT-ticket/licence IPF), regiostar.py
    locations/     education_gravity.py, education_gravity_model.py, secondary_chainsolvers/ (stage package: __init__ = synpp stage + re-exports; submodules distance_sampling, candidates, srv_candidates, plans, fallback, results, parallel_solving, deciders, srv_location_types, reporting, escort, activity_types, candidate_columns)
    spatial/       commute_distance.py (MiD P13 override), home_zones.py
    income.py
  locations/       home.py, work.py, secondary.py,
                   synthesis/replacement_education_gravity.py (flag-gated drop-in)
  matsim/          simulation/prepare.py (MATSim prepare override)
  analysis/        run_mid_validation.py, run_full_analysis.py,
                   run_education_validation.py, dashboard/, *.ipynb
  REGION.md        ZGB_KREIS_IDS single source of truth
```

## `eqasim_common/` (region-neutral)

```
eqasim_common/
  data/       buildings.py
  gravity/    distance_matrix.py
  locations/  education.py (the OSM radius sampler — legacy education default)
  spatial/    codes.py, entd_codes.py
  analysis/   bootstrapping, chains, marginals, statistics
```

## `data/`, `synthesis/`, `matsim/` (upstream eqasim)

`data/` holds the upstream eqasim France/Île-de-France stages (`hts/`, `census/`,
`od/`, `osm/`, `gtfs/`, `income/`, `bpe/`, `sirene/`, `ban/`, `bdtopo/`,
`external/`). The ENTD 2008 HTS donor (`data/hts/entd/`) is still consumed by the
BS activity-chain step. `synthesis/output.py` and `matsim/output.py` are the
upstream terminal stages aliased/extended by the fork.

## Key files

- `configs/fixtures/config_local_braunschweig.yml` — the canonical run+alias map (the
  other fixtures mirror it); `configs/base_bs.yml` is the composed-run equivalent.
- `CLAUDE.md` — authoritative description of the MiD/gravity/education subsystems.
- `braunschweig/REGION.md` — the eight ZGB Kreis ARS-5 codes; "always store as strings".
- `eqasim-data/DOWNLOAD_CHECKLIST_BS.md` — input inventory with paths + licences.

## Evidence

- `docs/codebase/.codebase-scan.txt` (DIRECTORY TREE)
- `configs/fixtures/config_local_braunschweig.yml` (`run:`, `aliases:`)
- `git rev-parse --abbrev-ref HEAD` -> `feature/education-gravity-bs`
- `ls bavaria` -> absent; `git ls-files eqasim-data` -> 38 tracked files
- `braunschweig/REGION.md`, `CLAUDE.md`, `eqasim-data/DOWNLOAD_CHECKLIST_BS.md`

---

## Cross-repo addendum: population-synthesis refactor (popsimprep)

Added 2026-06-08. The refactor adds two PopulationSim-based population workflows
beside the existing IPF one. Source material lives in a second repo,
a sibling `popsimprep` checkout (`../popsimprep`).

### The current population stage in eqasim-bs (= the `simple_ipf_open` baseline)

The existing IPF workflow is the chain (alias targets, from
`configs/fixtures/config_local_braunschweig.yml`):

```
braunschweig.ipf.prepare      -> assembles census control margins (population,
                                  employment, licences, optional hh-size, optional
                                  joint age x size)  [braunschweig/ipf/prepare.py]
braunschweig.ipf.model        -> the IPF solver (4-6 margins, Dirichlet prior)
                                  [braunschweig/ipf/model.py]
braunschweig.ipf.attributed   -> stochastic-rounds weights, FORMS households
  (= data.census.filtered)       (chunking / age-aware composition), reactivates
                                  person attrs  [braunschweig/ipf/attributed.py,
                                  household_composition.py, joint_age_size.py]
  -> synthesis.population.sampled -> .income.selected -> spatial.home.zones
  -> locations.home -> spatial.home.locations -> spatial.primary.locations
  -> spatial.secondary.locations -> spatial.commute_distance
  -> synthesis.population.enriched  (income/cars/PT/licence/tenure/fleet enrichment)
  -> matsim.scenario.{population,households,vehicles,facilities}  (XML writers)
```

Spatial anchor = **Gemeinde (ARS-12)** for household formation; control margins at
**Kreis (ARS-5)**; home points sampled from ALKIS buildings density-weighted by the
Zensus **100 m** grid (`braunschweig/data/zensus_grid/population.py`). The current
pipeline does **not** synthesise at the 100 m grid level — the 100 m grid is only a
density weight for home-point sampling.

### popsimprep layout (the `popsim_open` / `popsim_mid` source)

```
popsimprep/
  PopSimPrep-StartHere-v2.ipynb   THE orchestration notebook (8 code cells; the
                                  whole prep + folder-generation workflow). Cell 2
                                  = the master manual-settings block.
  batch_run_popsim.py             Batch runner: split big regiostar folders by 1km
                                  cells, run PopulationSim per folder in parallel,
                                  merge outputs (581 lines).
  popsim/
    configs/ settings.yaml        PopulationSim run config (geographies
                                    WELT/STAAT/ZENSUS1km/ZENSUS100m; seed=STAAT).
            logging.yaml
            _prep3_controls.csv   THE live control definitions (44 rows: HH+POP
                                    totals, 18 age x sex cells, M/F totals; x2 geos).
            _prep3_controls_sample.csv  older alternate scheme example.
    prep_config.json              legacy/alternate config (BS cut) - NOT read by
                                    the notebook; useful as a config-schema seed.
    scripts/ validation.ipynb verification.yaml   stock PopSim validation.
  scripts/  verify.py             cross-folder integrity checker (hard-codes a
                                    FOREIGN absolute path - bug).
            validation_notebook.ipynb   project-specific downstream validator.
  docker/dev.Dockerfile           Python 3.11 + GDAL + gfortran/openblas + uv.
  inputs/  (gitignored)
    cells_1km_with_binneds.parquet                212,758 cells x 348 cols
    cells_100m_with_gender_backf_binneds_happyorphans.parquet  3,148,482 x 536
    MiD2023/  (RESTRICTED raw microdata - never commit)
  pyproject.toml                  declares pkg "popsimwrapper" but NO such dir
                                    exists (aspirational; logic is all notebook).
```

There is **no Python package yet** in popsimprep — the entire workflow is the one
notebook plus `batch_run_popsim.py`. The refactor's job is to turn this into a
clean, testable Python package (`popsimwrapper*` or a `braunschweig/popsim/`
subpackage) wired into a synpp stage.

### New spatial foundation

The two cell parquet files become the spatial backbone. 100 m -> 1 km nesting is
exact (explicit `GITTER_ID_1km` column, also derivable by flooring the INSPIRE
`CRS3035RES100m N/E` coords to 1000 m); every 1 km parent has <=100 children
(mean ~15); populations reconcile ~99.99 %. Edge cases: 1,314 childless 1 km cells
(`has_no_children`) and 45 `is_orphan` 100 m cells (parent absent from the 1 km
table). This nesting is what `batch_run_popsim.py` uses to batch PopulationSim runs.

Evidence: `popsimprep/PopSimPrep-StartHere-v2.ipynb`,
`popsimprep/batch_run_popsim.py`, `popsimprep/popsim/configs/settings.yaml`,
`popsimprep/inputs/*.parquet` (schema via pyarrow), `braunschweig/ipf/*.py`,
`configs/fixtures/config_local_braunschweig.yml` (alias map).

---

## Update 2026-06-10: implemented layout (popsim branch + merged cordon)

### New packages on `feature/population-method-workflows` (worktree popsim-g5)

```
braunschweig/population/   Method selector + contract
  methods.py               simple_ipf_open | popsim_open | popsim_mid constants
  selector.py              resolve_population_producer()
  config.py                fail-fast config validation (MiD only for popsim_mid)
  schema.py                unified persons/households output contract
braunschweig/popsim/       PopulationSim workflow (27 modules)
  stage.py                 synpp producer stage (replaces data.census.filtered)
  mid/                     orchestration package (split #267; pure facade
                           __init__.py -- helper library, NOT a synpp stage,
                           so no configure/execute/validate() -- + re-exports
                           of every submodule below); cells -> controls ->
                           seed -> batch_folders -> merge
    batch_folders.py       Batch FOLDER assembly + PopulationSim runner (NOT
                           the parent `batch.py` below -- one character apart
                           on purpose: that module bin-packs cells INTO
                           batches; this one writes each batch's PopulationSim
                           run-folder contents and invokes that runner)
    control_cells.py       control-cell loading, ZGB filtering, control totals
    csv_format.py          MiD CSV field-separator detection
    donor.py               MiD donor attribute + Wege (trip) table loading
    donor_stratification.py RegioStaR donor stratification (Phase 4B)
    kreis_controls.py      Tier-3 KREIS control tables + per-batch apportionment
    participation.py       participation-control seed derivation
    seed_loading.py        consistent MiD seed load + completed-donor projection
  cells.py prepared_cells.py control_spec.py controls.py seed.py
  batch.py merge.py        1-km-atomic bin-packing of cells INTO batches (the
                           parent module `mid/batch_folders.py` above calls
                           this one -- distinct module, distinct job); merge.py
                           does the cell-disjoint merge
  expand.py assembly.py attributes.py    households -> persons; MiD attr mapping
  sources/{base,mid,entd}.py             donor adapter Protocol + 2 sources
  trips.py trips_stage.py plan_validation.py   MiD Wege -> eqasim trips + repair
  commute_distance.py distance_distributions.py
  handoff.py               100m cell -> building round-robin assignment
  income.py                unified INKAR scaling + high_income >= 5000 EUR
  missing.py stratum.py enriched_adapter.py
```

Note: `popsim/stratum.py` (top-level, above; Phase-4A stratum-KEY mapping,
e.g. `cell_urban_class_from_rs7`) and `popsim/mid/donor_stratification.py`
(Phase-4B dominant-stratum + seed filtering) cover the same feature area
(RegioStaR donor stratification) but are distinct modules; the `mid`
submodule was named `stratum.py` until issue #267 renamed it to end an
exact-filename collision between the two.

Cache-neutrality of the `mid/` split: unlike the `secondary_chainsolvers` /
`enriched` stage packages (each with its own `validate()`), `mid` is a plain
helper library called from `stage.py`, not itself a synpp stage -- no synpp
stage currently content-hashes `mid`'s source, so the split from one module
into eight submodules cannot devalidate any cache entry. The pre-existing gap
this leaves untouched (`stage.py` has no `validate()` hashing its `mid`
helper, so editing `mid` alone never invalidates the cache) is a known
helper-trap scheduled to be closed when `popsim/stage.py` is split (issue
#267, module 3), which is expected to add that `validate()` over the whole
`mid` package.

New configs (worktree): `config_popsim_mid_braunschweig.yml`,
`config_popsim_open_braunschweig.yml`, `config_smoke_{simple_ipf,popsim_mid,
popsim_open}[_mini].yml`, helper `make_smoke_configs.py`, `validate_three_cases.py`.

### New packages on main (cordon, merged)

```
braunschweig/data/cordon/  config, demand, gates, gate_assignment, gate_entry,
                           pt_reachability, plans, external_points,
                           incommuter_origins, mode_balancer, network,
                           network_clip, validation, validation_output
braunschweig/data/         cordon_network.py cordon_gemeinden.py cordon_pt_gates.py
braunschweig/data/spatial/cordon.py        cordon polygon (ZGB + buffer)
braunschweig/synthesis/    cordon_gates.py incommuters.py incommuter_merge/_base.py
braunschweig/matsim/scenario/  population.py households.py vehicles.py facilities.py
                           (terminal concat wrappers injecting in-commuters)
braunschweig/matsim/simulation/cordon_subpopulation.py
braunschweig/analysis/cordon_validation.py
```

### Branch/worktree map (2026-06-10)

| Tree | Branch | Content |
|---|---|---|
| main checkout | **detached HEAD `fd7e335`** | popsim Phase 5g.5 state (mid-branch) |
| `.claude/worktrees/popsim-g5` | `feature/population-method-workflows` (`e6806b2`) | three-workflow refactor, 47 commits ahead of the detached HEAD |
| `.claude/worktrees/cordon-whole-region-gates` | merged (0 ahead of main) | obsolete |
| `.claude/worktrees/simwrapper` | `feature/simwrapper-dashboards` | dashboard export |
