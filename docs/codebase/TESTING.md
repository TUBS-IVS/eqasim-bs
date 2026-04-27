# Testing Patterns

## Core Sections (Required)

### 1) Test Stack and Commands

- **Primary test framework**: `pytest 7.2.2`
- **Assertion/mocking tools**: `mock 5.1.0` (unittest.mock), `pytest` fixtures
- **Commands**:

```bash
# Run all tests
pytest tests/ -v

# Run unit tests only (exclude integration tests)
pytest tests/ -v -k "not pipeline and not simulation"

# Run with coverage
pytest tests/ --cov=braunschweig --cov=bavaria --cov-report=term-missing

# Run specific test file
pytest tests/test_braunschweig_data.py -v

# Run with output capture disabled (see print() calls)
pytest tests/ -v -s
```

### 2) Test Layout

- **Test file placement pattern**: Co-located in [tests/](tests/) directory, parallel to source.
- **Naming convention**: `test_<focus>.py` (e.g. `test_braunschweig_data.py`, `test_hh_size_margin.py`).
- **Setup files**:
  - [tests/__init__.py](tests/__init__.py) — package marker
  - [tests/testdata.py](tests/testdata.py) — fixture factory for creating temporary test data (used by [tests/test_pipeline.py](tests/test_pipeline.py))
- **Where they run**: GitHub Actions CI ([.github/workflows/tests.yml](.github/workflows/tests.yml)) on Ubuntu + Windows; local via `pytest tests/`.

### 3) Test Scope Matrix

| Scope | Covered? | Typical target | Notes |
|-------|----------|----------------|-------|
| **Unit** | YES | Data loader schema normalization, ID fixing (zero-fill), type casting, CSV parsing | [tests/test_braunschweig_data.py](tests/test_braunschweig_data.py): regex filters, age mappings, CDF builders, ID normalisation (~20 tests) |
| **Integration** | PARTIAL | IPF margin constraints, gravity calibration, location sampling | [tests/test_hh_size_margin.py](tests/test_hh_size_margin.py): 1% smoke run end-to-end (requires full cache); [tests/test_pipeline.py](tests/test_pipeline.py): Île-de-France IDF-derived tests (11 fail pre-existing; not BS-specific) |
| **E2E** | NO | Full 1%/10%/25% runs with MATSim simulation | Validation harness [scripts/validate_bs_10pct/](scripts/validate_bs_10pct/) serves as E2E regression guard (17 plots + JSON KPIs); not in pytest yet |
| **RNG reproducibility** | YES | Determinism of stochastic stages (IPF, sampling, RNG streams) | [tests/test_determinism.py](tests/test_determinism.py): runs pipeline twice with same seed, compares output |

### 4) Mocking and Isolation Strategy

- **Main mocking approach**: Dependency injection + fixture-based test data (no `@patch` decorator).
  - Unit tests pass mock DataFrames as function arguments (e.g. test a loader's `_load_region_distribution()` with a small CSV fixture).
  - Integration tests use pytest fixtures (tmpdir) to create isolated cache/output directories.

- **Isolation guarantees**:
  - Each test gets a fresh `tmpdir` (no cross-test pollution).
  - Synpp cache is scoped to test (default `working_directory` = test's tmpdir).
  - No global state; all RNG seeded per test.

- **Common failure modes in tests**:
  - **File path issues**: Tests assume `REPO_ROOT` correctly set (see [tests/test_braunschweig_data.py](tests/test_braunschweig_data.py#L37-L41)); if test run from wrong cwd, file paths fail.
  - **Missing data files**: Test data is created by [tests/testdata.py](tests/testdata.py); if it's incomplete, downstream loaders fail.
  - **RNG ordering**: If synpp stage order changes, RNG state drifts → runs with same seed produce different results (BUG-005). Tests catch this but investigation is tedious.

### 5) Coverage and Quality Signals

- **Coverage tool + threshold**: `pytest --cov=braunschweig --cov=bavaria --cov-report=term-missing` via pytest-cov plugin.
- **Current reported coverage**: [TODO] Baseline to be recorded in Phase 1. Suggested thresholds: 80% for `braunschweig/*`, 60% for legacy `bavaria/*`.
- **Known gaps/flaky areas**:
  - Upstream IDF tests ([tests/test_pipeline.py](tests/test_pipeline.py), [tests/test_simulation.py](tests/test_simulation.py)): 11 tests fail (pre-existing, Decision D-5: no fix in refactor Phase 0).
  - MATSim integration ([tests/test_simulation.py](tests/test_simulation.py)): Requires Java + Maven; slow. Runs on CI only.
  - Determinism tests ([tests/test_determinism.py](tests/test_determinism.py)): Sensitive to RNG ordering + Python version.

### 6) Evidence

- [tests/test_braunschweig_data.py](tests/test_braunschweig_data.py#L1-L50) — unit test exemplar
- [tests/test_hh_size_margin.py](tests/test_hh_size_margin.py) — integration test exemplar
- [tests/testdata.py](tests/testdata.py) — pytest fixture factory
- [.github/workflows/tests.yml](.github/workflows/tests.yml) — CI/CD test runner (pytest + MKL_CBWR environment setup)
- [plan/baselines/](plan/baselines/) — stored baseline outputs for regression detection

## Extended Sections (Optional)

### Test Hierarchy & Regression Strategy

1. **Smoke tests** (unit): ~20 quick tests in [tests/test_braunschweig_data.py](tests/test_braunschweig_data.py), ~5 min total runtime. Gate for every commit.
2. **Margin tests** (integration): [tests/test_hh_size_margin.py](tests/test_hh_size_margin.py), ~1% end-to-end synthesis. Gate for every PR.
3. **Validation harness** (E2E): `python -m scripts.validate_bs_10pct`, ~4 hours. Run nightly or pre-release. Produces [plan/baselines/](plan/baselines/) JSON for KPI regression detection.
4. **MATSim simulation** (E2E): Full 25% run + MATSim events. Run pre-release only (24+ hours on CI).

### Baseline Snapshots

- **pytest_baseline.txt**: Baseline test output (pass/fail counts) to detect regressions.
- **smoke_1pct_baseline.txt**: Baseline for 1% smoke run KPIs (population count, trips/person, etc.).
- Location: [plan/baselines/](plan/baselines/)
- Update strategy: Lock at Phase 0 (Decision D-5); new baselines only after Phase 3 (refactor complete + baseline re-run).

---
