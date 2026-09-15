# Testing

## One command on Linux, WSL2 and Windows

Activate the project environment described in [README](../../README.md#installation),
then run:

```bash
python scripts/run_tests.py --check
python scripts/run_tests.py
```

The runner uses the active Python executable, runs from the repository root and
sets UTF-8 for the test process. `--check` reports interpreter, platform, installed
package versions and the actual `matsim` import location. A wrong Python version,
missing required package or shadowed repository import fails with a diagnostic.
This is a preflight, not a proof of numerical-library correctness.

Pass normal pytest paths and filters to run focused checks:

```bash
python scripts/run_tests.py tests/test_population_passenger_availability.py -q
python scripts/run_tests.py -k passenger --durations=20
python scripts/run_tests.py -q --durations=30 --junitxml=test-results.xml
```

Test counts depend on the commit, parametrization and selection; collect them
instead of maintaining a number in this overview:

```bash
python scripts/run_tests.py --collect-only -q
```

## Regression and real-data boundaries

The default runner selects `not pipeline`: unit tests, small synthetic integration
checks and available committed reference-table checks. It does not promise that
every selected test is a pure unit test or has identical runtime. Tests needing
unavailable local inputs must explain their skip; a skip is not a pass.

The `pipeline` marker identifies real synthesis/MATSim runs in `test_pipeline`,
`test_determinism`, `test_simulation` and `test_smoke_1pct`. Run these explicitly:

```bash
python scripts/run_tests.py --pipeline -v
```

This selects only those tests and sets their existing `EQASIM_BS_RUN_PIPELINE=1`
opt-in. Their data/toolchain requirements still apply. MATSim requires JDK 25,
Maven and the configured network tools. Inspect skipped-test reasons with `-rs`.
The runner does not download data or configure Java. A small-data regression pass
is not a real pipeline smoke or scientific validation.

Direct `python -m pytest tests/ -q` remains supported with the previous opt-in
semantics. `pytest.ini` registers the marker and limits default discovery to
`tests/`; `tests/conftest.py` owns shared fixtures and logger cleanup.

## Test design

- Prefer small deterministic synthetic fixtures and real production helpers.
- Preserve conservation, assignment, error handling, primary/fallback-path,
  reproducibility and promised OFF-path checks.
- For identical inputs, check a coherent output contract in one test rather than
  rerunning the same export for each field. Keep distinct inputs and edge cases
  separately identifiable. Parametrization reduces repeated code, not test cases.
- Static source inspection is appropriate only for a specifically
  justified structural constraint; prefer observable behavior for logic.
- Never remove tests solely to meet a numeric budget. Measure durations first.
- PopulationSim control changes additionally require the specification checks and
  numerical smoke described in [CONTRIBUTING](../../CONTRIBUTING.md#after-touching-a-populationsim-control).

## Continuous integration

[The regression workflow](../../.github/workflows/tests.yml) targets `main` and
runs the shared command, recording the actual environment, slow-test durations
and a JUnit report. The separate [documentation workflow](../../.github/workflows/docs.yml)
runs the metadata gate without the scientific stack.

Use Linux/WSL2 as the canonical scientific runtime. Native Windows regression
checks remain useful for import, encoding, path and process portability. Environment
installation and locking are documented in
[reproducible-environment](notes/reproducible-environment.md).
