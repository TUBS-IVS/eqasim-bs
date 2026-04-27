# Changelog

## v0.1.0-bs (2026-04-27) — first regional release

First tagged release of the **eqasim-bs** Braunschweig fork, produced by
the `refactor/braunschweig-clean-fork` branch on top of
[`eqasim-bavaria`](https://github.com/eqasim-org/eqasim-bavaria) `b20fbe6`.

- **Region:** locked to ZGB-8 (ARS prefixes 03101, 03102, 03103, 03151,
  03153, 03154, 03157, 03158).
- **New region-neutral package** [`eqasim_common/`](eqasim_common/)
  hosting shared OSM, gravity-distance, spatial codes, and location
  synthesis helpers (Phase 2 of the refactor plan).
- **New `braunschweig/` package** consolidating IPF, location, gravity,
  enrichment, and MATSim simulation code that was previously fenced
  inside `bavaria/`. Stage names migrated from `bavaria.*` to
  `braunschweig.*`; aliases retained only where the BS DAG still
  consumes leaf modules from upstream (`bavaria.data.spatial.iris`,
  `bavaria.data.population.raw`, `bavaria.data.mvg.zones`).
- **New configs** [`config_local_braunschweig.yml`](config_local_braunschweig.yml)
  (1 %), `_10pct.yml`, `_25pct.yml`, plus `config_dryrun_braunschweig.yml`
  (0.1 % CI smoke) and `config_gravity_only_braunschweig.yml`
  (calibration). Seed `1234`, gravity slope `-0.065`.
- **Reconciled data inventory** ([`eqasim-data/DOWNLOAD_CHECKLIST_BS.md`](eqasim-data/DOWNLOAD_CHECKLIST_BS.md),
  [`scripts/verify_braunschweig_inputs.py`](scripts/verify_braunschweig_inputs.py)):
  DESTATIS 12411-0018 + urbistat for population, BA Pendleratlas CSVs
  for OD, Zensus 2022 5000H-2001 for households, BBSR INKAR
  Haushaltseinkommen for income, MiD 2023 Großraum-Braunschweig CDFs
  for commute distance, ALKIS / ATKIS preprocessed parquets for
  buildings & landuse, OSM POIs preprocessed parquet, RegioStaR-7 and
  Zensus 100 m grid auto-download.
- **Quality playbook** ([`quality/QUALITY.md`](quality/QUALITY.md) +
  four `RUN_*.md` protocols + Council-of-Three spec audit).
- **Test suite** rewritten around BS configs:
  `tests/braunschweig/test_stages.py` (12 unit tests),
  `tests/test_braunschweig_data.py` (data loaders),
  `tests/test_smoke_1pct.py` (1 % end-to-end, opt-in via
  `EQASIM_BS_RUN_PIPELINE=1`). Gate: 65 passed, 4 skipped.
- **Baselines** locked under [`plan/baselines/`](plan/baselines/)
  (1 % smoke metrics + the five YAML configs).
- **Documentation:** new [`AGENTS.md`](AGENTS.md), populated
  [`docs/codebase/`](docs/codebase) directory (stack, structure,
  architecture, conventions, integrations, testing, concerns).

### Deferred / known limitations

- Java MATSim package keeps `org.eqasim.bavaria.*` namespace
  (Decision D-1c).
- The 11 IPF / gravity bugs documented in
  [`docs/codebase/CONCERNS.md`](docs/codebase/CONCERNS.md) are tracked
  for a follow-up branch (Decision D-5).
- `bavaria/` directory retained for the leaf modules listed above and
  for upstream cherry-pick compatibility (Decision D-3).

## Pre-release history (Bavaria fork, prior to v0.1.0-bs)

The entries below are inherited from
[`eqasim-bavaria`](https://github.com/eqasim-org/eqasim-bavaria) and
describe features developed during the Bavaria → Braunschweig
migration. They are kept for traceability; refer to git log for the
exact authorship and dates.

- feat(ipf): symmetric-Dirichlet seed prior (TASK-011). New config
  ``bavaria.ipf.dirichlet_prior_strength`` (default ``0.0`` ≡
  bit-identical legacy behaviour). When > 0, α pseudo-counts are
  added uniformly to every IPF seed cell after the age-class prior,
  preventing very sparse Gemeinden (rural Goslar/Helmstedt) from
  collapsing weights to ~0 before the margins lift them. The IPF
  iteration is unchanged.
- feat(ipf): optional Kreis × hh_size × employed joint margin
  (TASK-010, 4-way IPF infrastructure). New flag
  ``bavaria.ipf.use_employment_margin`` (default ``false``;
  precondition: ``use_household_size_margin`` is also enabled). When
  on, a long-form CSV at
  ``bavaria.ipf.employment_by_hhsize_path`` (columns
  ``departement_id, hh_size, employed, weight``) supplies the joint
  targets; if no path is configured the stage falls back to an
  outer-product proxy derived from the existing employment- and
  hh_size-margins (smoke test). Default off — legacy pipelines
  unaffected.
- feat(data): INKAR multi-indicator full-panel loader (TASK-014).
  New stage ``braunschweig.data.inkar.full_panel`` reads any number
  of BBSR INKAR ``E_*.xls`` exports (config
  ``braunschweig.inkar_panel: {<indicator>: <relpath>, ...}``) and
  joins them on the 5-digit Kennziffer. Default config is empty so
  the stage is a no-op; smoke test reuses the shipped
  ``E_Haushaltseinkommen.xls`` to verify schema parity with the
  single-indicator loader.
- feat(data): Bundesagentur für Arbeit "Pendler nach
  Wirtschaftsabschnitten" loader (TASK-015). New stage
  ``braunschweig.data.ba.pendler_detailed`` reads a long-form CSV
  with columns ``home_kreis;work_kreis;sector;flow``. Companion
  pinned downloader ``scripts/download_ba_pendler_detailed.py``
  (BA portal requires manual session — script verifies SHA-256 of a
  user-placed file and supports ``--update-checksums``). Default
  ``braunschweig.ba_pendler_detailed_path: null`` keeps the stage a
  no-op until data is provided.
- feat(data): INSPIRE 100 m landuse spatial-prior loader (TASK-012).
  New stage ``braunschweig.data.inspire.landuse`` reads a
  preprocessed Copernicus 100 m GeoParquet (EPSG:3035) keyed on
  ``cell_id, class``. Feature-flagged behind
  ``braunschweig.use_landuse_prior`` (default ``false``); when off
  or input parquet missing, the stage returns an empty
  GeoDataFrame so downstream consumers can guard via the flag.
- feat(locations): density-weighted home-location candidates
  (TASK-003). New stage ``braunschweig.locations.home`` wraps
  ``bavaria.locations.home`` and, when
  ``braunschweig.home_density_weighting: true``, multiplies each
  building's area weight by the spatially-joined Zensus 2022 100 m
  ``einwohner`` of the cell containing its centroid. Per-Gemeinde
  rescale preserves inter-Gemeinde totals so the upstream
  household-allocation step (``synthesis.population.spatial.home.zones``)
  is untouched. Buildings outside any populated cell keep the pure
  area weight (einwohner=0 → fallback factor 1.0). Wired via alias
  ``synthesis.locations.home.locations`` in all three
  ``config_local_braunschweig*.yml`` (1 %, 10 %, 25 %) with the flag
  defaulted to ``true``. New tests
  ``TestDensityWeightedHome::{test_density_off_returns_unchanged,
  test_density_on_redistributes_within_commune}`` exercise both code
  paths via a stub delegate + synthetic 3-cell grid.
- feat(data): BBSR/BMV RegioStaR-7 Gemeindetypen loader (TASK-004).
  New stage ``braunschweig.data.bbsr.regiostar`` parses the BMV
  reference file (sheet ``ReferenzGebietsstand2020``) and yields one
  row per ZGB-8 Gemeinde with ``commune_id, ars5, name, regiostar7,
  regiostar17, regiostar_gem7``. Pinned to SHA-256
  ``550da569…3a04e6`` (7 709 894 B). New downloader
  ``scripts/download_regiostar.py`` (analogous to
  ``download_zensus_grid.py``). Auxiliary stage
  ``braunschweig.synthesis.population.regiostar`` joins the type onto
  persons via home commune_id for downstream stratification (TASK-008
  mode-choice MNL). New tests
  ``tests/test_braunschweig_data.py::TestRegioStarLoader`` (SHA-256 +
  ZGB-8 coverage 126 Gemeinden, all RegioStaR7 codes 72..77).
- docs(gravity): clarify Pendleratlas vs MiD discrepancy. The BA
  Pendleratlas dataset by definition contains **only cross-Kreis
  commuters** (0/48 340 OD pairs are intra-Kreis — see
  ``scripts/check_pendler_intra.py``), so the flow-weighted mean
  distance implied by the GLM (~46 km on ZGB-8) is a *conditional*
  cross-Kreis mean and **not directly comparable** to MiD's commute
  mean (20.7 km, P13 ``mittel`` for ZGB-Gesamt). MiD's lower mean is
  driven by the inclusion of intra-Kreis commuters which dominate
  short trips, plus MiD's single-day diary design which under-counts
  infrequent long-distance Wochenpendler. β = −0.065 governs only the
  within-(orig, dest)-Kreis Gemeinde-pair spread; the intra/cross
  share is pinned by BA-Atlas Kreis totals through ``bavaria.ipf``.
  Methodological notes added to top of
  ``scripts/calibrate_gravity_decay.py``.
- feat(validation): MiD `mittel` reference column added to
  ``scripts/validate_bs_10pct/metrics.py::commute_distance_summary``.
  The summary table now carries ``mid_mean_km, deviation_km,
  deviation_pct`` per home Kreis (ZGB-Gesamt 20.7 km, range 13.7
  Salzgitter → 29.4 Goslar). New diagnostic script
  ``scripts/inspect_mid_p13.py`` prints band-midpoint approximations
  alongside MiD ``mittel`` for cross-checking.
- feat(data): Zensus 2022 100 m population grid loader (TASK-002).
  New stage ``braunschweig.data.zensus_grid.population`` reads the
  official Zensus 2022 grid (3.1 M populated cells, EPSG:3035) from
  parquet chunks distributed via the ``z22data`` mirror under
  dl-de/by-2-0 and clips to the dissolved ZGB-8 bounding box (default
  buffer 200 m). Returns ``GeoDataFrame[grid_id, einwohner, geometry]``
  (~70 k cells, ~1.64 M inhabitants in ZGB-8 bbox). Companion
  downloader ``scripts/download_zensus_grid.py`` pins SHA-256 for both
  ``population_100m.parquet`` (9.9 MB) and ``grid_100m.parquet``
  (1.4 MB). Regression tests in
  ``tests/test_braunschweig_data.py::TestZensusGridLoader``.
- feat(gravity): Poisson-GLM distance-decay calibration (TASK-001).
  ``scripts/calibrate_gravity_decay.py`` fits log E[flow_ij] = α_O +
  γ_D + β d_ij to BA Pendleratlas Kreis-pair flows (939 ZGB-touching
  pairs within 250 km). Pure MLE → **β = −0.0650 ± 0.0002** (z=-342,
  log-L improvement +82 % vs default −0.18). Script also exposes a
  joint-loss diagnostic J(β)=−logL+λ(M_pred(β)−M_MiD)² with FE re-fit
  (BA-implied mean ~46 km on Kreis aggregates, not directly comparable
  to MiD’s person-trip 12.6 km). Configs ``config_local_braunschweig*
  .yml`` set ``gravity_slope: -0.065``. **Note:** ``braunschweig.gravity
  .model`` post-IPFs synthesised Gemeinde flows against BA Kreis totals,
  so β only redistributes within (origin Kreis, destination Kreis)
  cells; the validator’s ``commute_distance`` KPI is sampled from
  MiD-P13 by home-Kreis (see ``braunschweig/synthesis/spatial/
  commute_distance.py``) and therefore independent of β by design.
  Calibration JSON: ``eqasim-data/cache_bs/calibration/gravity_beta.json``.
- feat(validation): bootstrap confidence intervals (TASK-006). New module
  ``scripts/validate_bs_10pct/bootstrap.py`` performs per-Kreis stratified
  household resampling (n_replicates=200, seed=20260426), pre-aggregates
  per-HH count/distance vectors and computes 2.5 / 50 / 97.5 percentiles +
  mean/std for 13 KPIs (trips_per_person, mean_distance_km,
  daily_distance_km, 4× mode shares, 6× purpose shares). Vectorised
  (~8 s for 200 reps on 10 pct cache). Output added to ``report.json`` under
  key ``bootstrap_ci``.
- feat(validation): apply H1 R-D reporting-only fix to
  ``scripts/validate_bs_10pct/metrics.py::mode_share_by_purpose`` (already
  applied in ``purpose_mix``). Both functions now relabel return-home legs
  with ``preceding_purpose`` so destination purpose distributions align
  with MiD's `Wegezweck` convention. Effect on 10 % cache: synth `home`
  42.4 % → 0.7 %, `leisure` 14.8 % → 25.6 %, `work` 13.8 % → 22.8 %. NO
  synthesis change. Plan TASK-005 (`plan/feature-bs-model-improvements-1.md`).
- fix(bavaria): defensive divide-by-zero guards in ``bavaria/ipf/prepare.py``
  — ``_build_household_size_margin`` now raises ``RuntimeError`` if any
  ``size_total`` is non-positive (instead of silently producing inf/NaN
  weights). Top-level license rescaling at end of ``execute`` raises if
  ``df_licenses_kreis["weight"].sum() <= 0``. Both guards never trip on
  validated input data (1pct/10pct/25pct runs unchanged) but harden the
  pipeline before 100 %-scale production runs.
- fix(bs): silent NaN in household income for hh_size 5/6 (~12 % of HHs)
  — `bavaria/synthesis/population/enriched.py` mapped IPF size-5/6 onto a
  non-existent ``"5+"`` key in the Braunschweig 6-bin MiD H4 reference,
  causing ~33 k persons to receive the median fallback. Replaced inline
  map with adaptive ``_build_income_size_map`` that auto-detects 5-bin
  (Bavaria GENESIS) vs 6-bin (Braunschweig MiD) schemes and raises on
  unknown layouts. Hard ``RuntimeError`` if any post-groupby income NaN
  remains. 4 regression tests in
  ``tests/test_braunschweig_data.py::TestHouseholdDistributions``.
- feat(bs): multi-level post-stage validation for production sign-off
  - **post-IPF margin check** (``bavaria/ipf/model.py``): after
    convergence, compares achieved per-cell weight sums to targets;
    raises if any cell deviates by more than
    ``bavaria.ipf.margin_validation_tolerance`` (default ``0.01``).
    Separate hard-zero-target violation check
    (threshold = max(1, 1e-6 × total weight)). Lists 5 worst offenders
    on failure. 1 % run shows max deviation 0.93 %.
  - **post-enrichment control block**
    (``bavaria/synthesis/population/enriched.py``): NaN guards on
    8 critical columns (``household_size``, ``household_income``,
    ``high_income``, car/bike/PT availability, vehicle counts);
    per-bin hh_size deviation vs Zensus reference (raises if max |Δ|
    > 5 pp); ``household_income_eur`` range check [100, 20 000];
    summary print of achieved shares.
- feat(bs): export ``household_income_eur`` (continuous INKAR-scaled
  income) as optional column in ``households.csv``.
- fix(synthesis/output): residency flag detection no longer hard-codes
  ``is_munich_resident``; auto-detects any ``is_*_resident`` column,
  prefers fork-specific (Braunschweig writes ``is_bs_resident``).
- feat(bs): add per-commune household-size IPF margin (Zensus 2022 1000A-2081)
  - new loader `braunschweig.data.census.households_type` reads
    `Personen × HSHGR2 × HSHTP1` per Gemeinde (SafeMosaic ``e``-flagged values
    treated as valid, ``-`` and ``.`` as zero);
  - new loader `braunschweig.data.census.households_size_age` reads the 4-way
    cube 1000A-3082 (kept as descriptive reference; not used in IPF because the
    ZGB coverage is too sparse);
  - extends `bavaria.ipf.prepare` and `bavaria.ipf.model` with a fifth IPF
    margin (commune × hh_size, six bins ``1..5, 6+``) gated behind
    ``bavaria.ipf.use_household_size_margin`` (default ``false``;
    Bavaria runs are bit-identical when off);
  - hard-zero target: ``age < bavaria.minimum_age.one_person_household``
    cannot live in a 1-person household;
  - per-commune Zensus targets are rescaled to the population total to
    guarantee IPF feasibility despite the Zensus/DESTATIS Stichtag mismatch;
  - `bavaria.ipf.attributed` and `bavaria.synthesis.population.enriched`
    propagate the IPF-assigned ``household_size`` and skip the
    regions-aggregated post-hoc draw;
  - **household-formation pass** in ``bavaria.ipf.attributed``
    (``_form_households``): stochastic-rounds the IPF cell weights to
    integer person counts, deterministically shuffles within each
    ``(commune_id, hh_size)`` bucket, and chunks the persons into
    households of size N, dropping ≤ N−1 trailing persons per bucket
    (≤ 0.07 % of total persons in ZGB). Output is sorted by
    ``household_id`` to satisfy ``synthesis.population.sampled`` invariants;
  - 5-bin household-income table is mapped onto the 6-bin IPF schema by
    folding ``5`` and ``6+`` onto the income table's ``5+`` bin;
  - flag enabled in `config_local_braunschweig{_10pct,}.yml` only;
  - **end-to-end validation (1 % run)**: ZGB-wide synth-vs-Zensus
    HH-size shares within max |Δ| = 0.48 pp (was −16 to −33 pp);
    children-in-1P-HH = 0 by construction.
- feat: add municipality information to households and activities
- chore: update to `eqasim-java` commit `ece4932`
- feat: vehicles and vehicle types are now always generated
- feat: read vehicles data from zip files
- feat : option parameter to remove filtering for requesting departements in hts
- fix: secondary location model used same random seed in every parallel thread
- feat: add a new method for attributing income to housholds using the bhepop2 package
- fix: fixed special case in repairing ENTD for completely overlapping trips
- feat: make it possible to disable the test run of MATSim before writing everything out
- feat: check availability of open data sources for every PR
- feat: make statistical matching attribute list configurable
- feat: add urban type classifiation (unité urbaine)
- feat: functionality to make use of INSEE population projection data
- update: don't remove households with people not living/studying in Île-de-France anymore to be more consistent with other use cases
- fix bug where always one household_id existed twice
- Fix read order when exploring files using `glob`
- Modes are only written now to `trips.csv` if `mode_choice` is activated
- Update to `eqasim-java` commit `7cbe85b`
- Adding optional `eqasim-java`-based mode choice step using the `mode_choice` configuration option
- Make use of building information (housing) and addresses that are attached to them for home locatio assignment
- Make use of National Address Database (BAN)
- Further simplify handling of BD-TOPO by avoiding matching of very specific file names
- Fix: Segfault in statistical matching caused by `numba` in recent versions
- Increase reproducibility for BD-TOPO by requiring user to dump the IGN files in 7z'ed GPKG format into one central folder for `bdtopo22`
- Fix: Correctly treat non-movers in CEREMA EDGT for Lyon
- Fix: Properly treat non-movers in EDGT Lyon ADISP data
- Configure directory for GTFS and then auto-detect contained zip files
- Added integration tests for Windows
- Updated conda environment based entirely on *conda-forge*
- Use national census data to ease creation of scenarios other than IDF
- Make various inputs with long source names folder-based (OSM, BD-TOPO, IRIS, ...)
- Read input data directly from ZIP archives instead of requiring the user to unpack the files
- Update documentation for non-IDF use cases to updated data sets
- Update: Make use of INSEE RP 2019, BPE 2021, Filosofi 2019, IRIS 2021
- Make use of BD-TOPO building database for home locations
- Remove BD-TOPO address database
- Make use of georeferenced SIRENE provided by INSEE
- Update documentation for the required versions of Java and Maven
- Updated Github workflow with more reuse of existing actions
- Update synpp to `1.5.1`
- Fix: Handle commas in coordinates in BPE
- Fix: Make types consistent for mode recognition in ENTD
- Fix: Properly treat non-movers in EDGT 44
- Fix: Avoid duplicate persons in same households
- Add option to export detailed link geometries
- Fix: Arbitrary order of week days in merged GTFS
- Use BPE 2021 instead of BPE 2019
- Update configuration files for Lyon, Nantes, Corsica
- Add a basic sample based vehicle fleet generation tailored for use with the `emissions` matsim contrib
- Fixing socioprofessional category for Nantes and Lyon (Cerema)
- Fix documentation and processing for Nantes GTFS
- Add law status of the SIRENE enterprises for down-stream freight models (this requires both SIREN and SIRET data as input!)
- Update handling of invalid values on the nubmer of employees in SIRENE
- Add alternative source for EDGT Lyon (and set it as default/recommended source)
- Add euclidean distance to Nantes/Lyon GTFS output
- Fix GTFS schedules without transfer times
- Added stage to write out the full merged GTFS feed: `data.gtfs.output`
- Bugfix: Sometimes bug in converting GTFS coordinates (esp. Lyon / Nantes)
- Fixing output stages
- Add output stages for SIRENE and the selected HTS
- Add output prefix to non-MATSim output files as well
- Add code and documentation for Nantes use case
- Bugfix: Generate `meta.json` when code was not cloned but downloaded directly
- Use `eqasim-java:1.3.1`
- Make choice of branch and version of pt2matsim more flexible
- Improve handling of Osmosis on Windows
- Add stages to process EDGT for Lyon

**1.2.0**

- Update code and data to BPE 2019 (verison for 2018 is not available anymore)
- Add additional spatial standard output: `homes.gpkg` and `commutes.gpkg`
- Updated documentation for BD-TOPO
- By default, load SIRENE directly from `zip` file instead of `csv`
- Bugfix: Make sure df_trips are sorted properly in `synthesis.population.trips`
- Bugfix: Do not execute "urban" attribute imputation twice
- Bugfix: Do not consider *inactive* enterprises from SIRENE
- Update analysis scripts
- Remove CRS warnings
- Bugfix: Handle case if very last activity chain in population ends with tail
- Speed up and improve testing
- Improve analysis output for ENTD
- Update to `eqasim-java:1.2.0` to support tails and "free" activity chains
- Allow for activity chains that do not start and end at home
- Improve handling of education attribute in ENTD

**1.1.0**

- Update to `synpp:1.3.1`
- Use addresses for home locations (from BD-TOPO)
- Use enterprise addresses for work locations (from SIRENE + BD-TOPO)
- Add SIRENE and BD-TOPO data sets
- Update to `eqasim-java:1.1.0` and MATSim 12
- Preparation to use Corisca scenario (see config_corsica.yml) as unit test input in `eqasim-java`
- Several auto-fixes for malformatted GTFS schedules (mainly Corsica)
- Make jar output optional and use proper prefix
- Bugfix: Fixing bug where stop times where discarded in GTFS cutting
- Add documentation for Lyon and Toulouse
- Define stage to output HTS reference data
- Make prefix of MATSim output files configurable
- Cut GTFS schedules to the scenario area automatically
- Make possible to merge multiple GTFS files automatically
- Automatically convert, filter and merge OSM data before using it in pt2matsim. This requires that `osmosis` is available in the run environment.
- Provide calibrated Île-de-France/Paris eqasim simulation for 5% sample
- Make use of `isUrban` attribute from eqasim `1.0.6`
- Update to eqasim `1.0.6`
- Make GTFS date configurable
- Use synpp 1.2.2 to fix Windows directory regeneration issue
- Make pipeline configurable for other departments and regions, add documentation
- BC: Make use of INSEE zone summary data (`codes_2017`)
- Add configuration parameters to filter for departments and regions
- Fixed destinations that have coordinates outside of their municipality
- Make error message for runtime dependencies more verbose
- Switch default instructions to Anaconda

**1.0.0**

- Fixed dependency issue for ENTD scenario
- Initial public version of the pipeline
