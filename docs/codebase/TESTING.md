# Testing

## Required agent verification gate

This is the canonical verification process for humans and coding agents;
AGENTS.md, CLAUDE.md, CONTRIBUTING.md and the PR checklist require it.

1. **At session start:** activate the locked project environment and run
   `python scripts/run_tests.py --check`. Linux/WSL2 uses the captured server
   snapshot; native Windows uses the Windows lock. Follow the
   [environment installation and refresh policy](notes/reproducible-environment.md).
   Diagnose a failed preflight before proceeding; do not work around it with
   import patches or a fresh unconstrained dependency solve.
2. **During implementation:** run affected tests through the same runner, using
   paths or `-k`. Preserve scientific invariants and measure repeated work before
   consolidating tests. A focused pass is development feedback, not the final gate.
3. **Before final handoff, push or PR:** integrate the current `origin/main` and run
   `python scripts/run_tests.py -q --durations=30 --junitxml=test-results.xml`
   on the final code state in server-mirrored Linux/WSL2. Use an isolated checkout
   on the server only when local Linux is unavailable; do not mutate its trusted
   environment or start a production simulation for this regression gate.
   Native Windows checks use the identical command. If a required platform is
   unavailable, report it explicitly; do not label that gate passed.
4. **Record evidence:** tested commit (and any uncommitted changes), OS, environment
   diagnostics, command, exit status, pass/skip/deselected counts and JUnit path.
   Explain unexpected skips. A skip is not a pass; direct pytest and results from
   another checkout or pre-merge code state cannot substitute for this gate.
   Later executable changes require affected checks again; broaden to the suite
   when their impact is not isolated. Documentation-only recording of results
   does not invalidate the executable-code evidence.
5. **Before merge:** both `regression` matrix jobs (Linux and Windows) and the
   documentation check must pass on the PR's latest commit. Inspect failures
   rather than disabling a platform or weakening assertions. CI provides the
   second platform when it cannot be run locally; pending CI is pending evidence.
6. **When scientific behavior or pipeline wiring changes:** additionally run the
   relevant real-data smoke with its preflight and record a run manifest. The
   regression suite does not replace that evidence. Never launch a production
   run merely to satisfy a regression check.

Keep the JUnit file as local/CI evidence (`test-results.xml` is gitignored).
The metadata-only documentation workflow intentionally uses direct pytest in
its minimal environment; it is a separate gate and never substitutes for the
scientific-stack regression workflow.

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
opt-in. Before execution it runs the existing input preflight with `--matsim`
and requires JDK 25 and Maven on PATH. Missing prerequisites fail the command.
`--pipeline --collect-only` can list the selected tests without these inputs.
Network extraction tools must also be installed for an actual MATSim run.
The default runner clears inherited pipeline opt-in flags; direct pytest retains
its original opt-in/skip semantics. The runner does not download data or configure
Java. A small-data regression pass
is not a real pipeline smoke or scientific validation.

Direct `python -m pytest tests/ -q` remains available for low-level diagnostics
with the previous opt-in semantics; it does not satisfy the required agent gate.
`pytest.ini` registers the marker and limits default discovery to
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
