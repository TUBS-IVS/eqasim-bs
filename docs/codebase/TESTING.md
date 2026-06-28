# TESTING

> **Staleness note (2026-06-26):** reflects the 2026-06-08 state. Now 259 `test_*`
> files. Local caveat: the canonical pytest run is on the **server / `eqasim` conda
> env** — system Python 3.13 shadows the repo-local `matsim` namespace package, so
> synpp-layer tests fail locally but pass on the server (memory
> `reference-local-test-env-matsim-shadowing.md`). New test suites cover calibration
> (`test_calibration_*`, `test_circuity`, `test_detour_fit`,
> `test_distance_distributions_by_purpose`) and building potentials.

Test setup for `eqasim-bs`. Verified from the `tests/` listing, `environment.yml`,
`.github/workflows/tests.yml`, `CLAUDE.md`, and `AGENTS.md`.

## Framework and command

- **pytest 7.2.2** (`environment.yml`).
- No `pytest.ini` / `setup.cfg` / `pyproject.toml` / `conftest.py` present in the
  repo (verified) — pytest runs with defaults from the repo root.
- Run the full suite:
  ```powershell
  $env:PYTHONUTF8 = "1"; python -m pytest tests/ -v
  ```
  `PYTHONUTF8=1` matters because some reference data and German field names are
  non-ASCII (see the user-memory note "Pipeline conda env … needs PYTHONUTF8").
- CI runs `MKL_CBWR=AUTO pytest tests/` on ubuntu-latest and windows-latest
  (`.github/workflows/tests.yml`).
- AGENTS.md documents a fast-subset command that skips the slow pipeline tests:
  ```powershell
  pytest tests/ -v -k "not test_pipeline and not test_simulation and not test_determinism"
  ```

## Layout

`tests/` holds 25 top-level test modules plus `tests/braunschweig/` (`test_stages.py`),
`tests/baselines/`, `tests/testdata.py`, and `tests/__init__.py`. Tests group by
feature/subsystem:

- **MiD reference data:** `test_mid_reference_tables.py`, `test_mid_school_distance.py`,
  `test_mikrozensus_school_distance.py`
- **Gravity (work):** `test_gravity_ring_calibration.py`, `test_gravity_slope_config.py`,
  `test_regiostar_fill.py`
- **Education gravity:** `test_education_gravity_model.py`, `test_education_gravity_stage.py`,
  `test_education_validation.py`, `test_calibrate_education_slopes.py`,
  `test_school_typing.py`, `test_school_readers.py`, `test_school_facilities.py`,
  `test_university_facilities.py`, `test_kita_facilities.py`,
  `test_extract_nds_kitas.py`
- **Population / IPF:** `test_braunschweig_data.py`, `test_hh_size_margin.py`,
  `test_run_mid_validation.py`
- **Pipeline / determinism (opt-in, slow):** `test_pipeline.py`, `test_simulation.py`,
  `test_determinism.py`, `test_smoke_1pct.py`

## Strategy

- **Deterministic, small synthetic data.** Unit tests build tiny in-memory
  pandas/GeoPandas frames with fixed coordinates and a fixed seed rather than
  loading large external datasets (CLAUDE.md "Tests"; example
  `tests/test_education_gravity_stage.py` constructs a 5-person, 4-school GeoDataFrame).
- **Mocking:** `mock=5.1.0` is available in the env; no heavyweight HTTP/DB mocking
  layer is needed because the pipeline is file-based.
- **Opt-in heavy tests.** The full pipeline / simulation / determinism tests are
  gated behind an environment flag (`EQASIM_BS_RUN_PIPELINE=1` per README; the
  default `pytest tests/ -q` run reports "65 passed, 4 skipped"). AGENTS.md records
  a frozen baseline of 53 pass / 11 fail for the pre-refactor IDF-inherited suite —
  the two figures reflect different points in the project history; `[ASK USER]`
  which is the current expected gate.

## Environment caveat (calibration scripts)

Some calibration scripts that use NumPy linear algebra / GLM fitting
(`scripts/calibrate_gravity_per_rs7.py` Poisson GLM, SVD-based code) are reported
to crash in the local `eqasim` conda env due to a broken BLAS/LAPACK (reference
BLAS) build — the synpp **pipeline** itself runs fine, but GLM-based calibration
scripts do not (user-memory "eqasim env LAPACK broken"). The corresponding tests
(`test_gravity_ring_calibration.py`, `test_calibrate_education_slopes.py`) may be
affected on that env. This is an environment limitation, not a test-code defect.

## Evidence

- `tests/` directory listing (25 modules + `tests/braunschweig/`)
- `environment.yml` (`pytest=7.2.2`, `mock=5.1.0`)
- `.github/workflows/tests.yml` (`MKL_CBWR=AUTO pytest tests/`)
- `AGENTS.md` ("Day-to-day commands", "Current state": 53/11 baseline)
- `README.md` ("Test gate": 65 passed / 4 skipped, `EQASIM_BS_RUN_PIPELINE=1`)
- `CLAUDE.md` ("Tests" — deterministic synthetic data; per-feature test lists)
- user-memory `eqasim-env-lapack-broken.md`, `pipeline-conda-env.md`

---

## Cross-repo addendum: test strategy for the population-synthesis refactor

Added 2026-06-08. popsimprep currently has **no tests** (`pyproject.toml` declares
pytest + a `[tool.pytest.ini_options]` block but no `tests/` directory exists). The
refactor must add a test suite in the eqasim-bs deterministic-synthetic-data style.
Required coverage (from brief §10), grouped:

- **Config / workflow selection:** valid/invalid `population.method`; missing MiD
  path errors only when `popsim_mid` selected; MiD not required for
  `simple_ipf_open` / `popsim_open`; no silent fallback between workflows.
- **Data safety:** MiD folders + parquet ignored by Git; restricted paths absent
  from default configs; no restricted data written into a tracked output dir.
- **Spatial consistency (use tiny synthetic grids):** 100 m aggregates to 1 km;
  every 100 m cell has exactly one 1 km parent; `is_orphan` handled explicitly;
  totals consistent after aggregate/disaggregate.
- **Batching:** batches hold complete 1 km parents (1 km atomic); each 100 m cell
  in exactly one batch; deterministic batch input generation (split manifest sha1);
  merge does not duplicate households/persons; `(ZENSUS100m, H_ID)` global
  uniqueness; failed/missing batches detected.
- **PopulationSim integration:** expected input files generated per folder;
  expected output detected; batch logs preserved; a **minimal fixture that runs
  PopulationSim on a 2-3 cell toy problem** without large real data (gated/opt-in
  like the existing heavy pipeline tests, given the BLAS/LAPACK + subprocess needs).
- **Workflow behaviour:** `simple_ipf_open` preserves current IPF output (lock with
  a baseline / byte-identical test, mirroring eqasim-bs's existing
  `test_determinism.py` discipline); `popsim_open` runs without MiD; `popsim_mid`
  requires MiD only when selected; all three produce the harmonised output schema.
- **Output handoff & plausibility:** final files in the right quaSIM dir with
  consistent names; intermediate vs final separated; household/person counts,
  age/sex/income distributions, vehicle-ownership plausible; fixed seed reproducible.

Reuse eqasim-bs's existing `braunschweig/analysis/population_validation/` package
(PopulationSim-style control validation, already on main) as the plausibility/QC
layer rather than building a parallel validator.

Evidence: `popsimprep/pyproject.toml` (pytest declared, no tests dir),
brief §10, `tests/test_determinism.py`,
`braunschweig/analysis/population_validation/`.

---

## Update 2026-06-10: implemented popsim + cordon test coverage

### Popsim branch (worktree popsim-g5)

- **Selector/config:** `test_population_selector.py`, `test_population_config.py`,
  `test_popsim_open_config.py` (incl. the deliberate NON-alias of ENTD
  distance_distributions), `test_simple_ipf_open_baseline.py` (regression freeze).
- **Income unification:** `test_popsim_income_unified.py` (16 tests, INKAR scaling,
  high_income >= 5000 EUR, fallback rates), `test_population_income_attribute.py`.
- **Comparability:** `test_three_case_comparability.py` — schema/MATSim
  compatibility across all three methods.
- **Smoke harness:** `scripts/popsim_mid_smoke.py` (2 1-km parents through real
  PopulationSim), `config_smoke_{simple_ipf,popsim_mid,popsim_open}[_mini].yml`,
  `validate_three_cases.py`. Last result: popsim_open mini e2e EXITCODE 0
  (`smoke_popsim_open_mini_final.log`, 12/12 stages, secondary success 97.16 %).

### Cordon (merged to main)

18 `tests/test_cordon_*.py` modules + `tests/test_incommuters.py` (integration);
key regression guard `test_cordon_commuter_conservation.py`
(in-commuter count scales linearly with `sampling_rate`; in/out conservation).
