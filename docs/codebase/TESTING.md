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
2. **During implementation and before push:** run the tests affected by the change
   through the same runner, using paths, node ids or `-k`. Preserve scientific
   invariants and measure repeated work before consolidating tests. This focused
   local run is the whole local duty; a local full suite is not required (ADR-0150).
   A selection by module name misses the repository-wide contract scans, because
   they find their subjects in the source and name no module. When a change touches
   a stage's `configure` or `execute`, reads configuration, or adds a helper that
   calls `eqasim.run`, `java.run` or `pt2matsim.run`, add
   `tests/test_execute_context_config_contract.py`,
   `tests/test_runtime_config_declares.py` and
   `tests/test_audit_synpp_helper_hash.py` to the focused run. #481 went red in CI
   on the first two although its focused local run had passed.
3. **Data-gated tests run locally.** CI carries no restricted input data, so tests
   that declare such a dependency skip there
   ([test-data-dependencies](notes/test-data-dependencies.md)). When the change
   touches code such tests cover, run exactly those tests locally on a checkout that
   carries the data and report their pass and skip counts. A worktree carries no
   gitignored data unless it is linked in.
4. **Final-code evidence comes from CI.** Merge the current `origin/main`, push the
   branch and open the PR (`git pr`). The `regression` workflow then runs
   `python scripts/run_tests.py -q --durations=30 --junitxml=test-results.xml` on
   Linux (the captured server snapshot, as in WSL) and on Windows, in parallel on
   GitHub's machines. Do not start local full suites to replace it. Only when CI
   cannot run, run that command locally in Linux/WSL2, report it as the substitute,
   and never label a missing platform passed.
5. **Record evidence:** in the PR, name the focused local command with its pass/skip
   counts (data-gated tests included where step 3 applies) and link the CI run.
   A skip is not a pass. Explain unexpected skips; compare CI's skip count with
   the previous run of `main` when it changes. The shared runner also writes local
   evidence to `.claude/gate/tests-<platform>.json`; the local push gate shows it
   as information ([agent-process-automation](notes/agent-process-automation.md)).
   Documentation-only recording or clarification of the process and results
   does not invalidate the executable-code evidence.
6. **Before merge:** both `regression` matrix jobs (Linux and Windows) and the
   documentation check must pass on the PR's latest commit. Inspect failures
   rather than disabling a platform or weakening assertions. Pending CI is pending
   evidence. If `main` moved since the last CI run, merge `origin/main` into the
   branch (`git sync-main`, [merging-main](notes/merging-main.md)) and push again:
   re-running a workflow does not pick up a newer `main`. A green `regression` result
   counts for a later commit whose regression inputs are identical (only
   `docs/generated/` or `docs/decisions/` differ); the workflow then reuses it instead
   of re-running the suite (ADR-0165).
7. **When scientific behavior or pipeline wiring changes:** additionally run the
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

A caller's `-m` expression is combined with the selected pipeline boundary
using `and`; it narrows that selection and cannot enable a real pipeline in
the default regression mode.

```bash
python scripts/run_tests.py tests/test_population_passenger_availability.py -q
python scripts/run_tests.py -k passenger --durations=20
python scripts/run_tests.py -q --durations=30 --junitxml=test-results.xml
```

The runner passes `-q --tb=short` to pytest for every selection, test paths and
filters included, so a failure prints a short traceback instead of pytest's default
long one (the `-q` in the examples above is optional). A verbosity option of your own
(`-v`, `-vv`, `--verbose`, `-q`, `--quiet`, `--verbosity`) replaces `-q`, because pytest
sums `-v` and `-q`; `--tb=<style>` replaces `--tb=short`. Both defaults change only
the printed output, never which tests run. Why short output matters:
[subagent-budget](notes/subagent-budget.md).

Parallel runs are optional development feedback. With `pytest-xdist` installed,
`python scripts/run_tests.py -n 8 -q` runs the same selection in eight worker
processes (3:19 instead of 6:53 min on the Windows development machine,
2026-09-25). `pytest-xdist` is not part of the locked environments: the Linux
lock is a captured server snapshot and changes only with a new one (see
[reproducible-environment](notes/reproducible-environment.md)), so the required
gate stays the serial command. Keep tests parallel-safe: write only below
`tmp_path`, depend on no test order, and remember that session fixtures run once
per worker.

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
All four pipeline tests resolve `java`, `mvn`, `osmosis` and `osmconvert` from
the active PATH and derive `java_home` from that Java executable. Their local
fixture-specific binary paths are replaced in memory; scientific settings and
baselines are unchanged. Put the actual JDK 25 and extraction tools on PATH on
either platform; missing tools fail explicitly.
The default runner clears inherited pipeline opt-in flags; direct pytest retains
its original opt-in/skip semantics. The runner does not download data or configure
Java. A small-data regression pass
is not a real pipeline smoke or scientific validation.

Direct pytest is reserved for debugging the shared runner itself and the named
metadata-only documentation workflow. Normal focused diagnostics must use the
runner; direct pytest does not satisfy the required agent gate.
`pytest.ini` registers the marker and limits default discovery to
`tests/`; `tests/conftest.py` owns shared fixtures and logger cleanup.

## Test design

The binding rules (failure modes first, highest useful level, no reflex tests,
behaviour over implementation, one contract per test, cheap by construction, what
must always stay covered) live in [.claude/rules/tests.md](../../.claude/rules/tests.md) and are not
repeated here. Operational notes:

- Prefer small deterministic synthetic fixtures and real production helpers.
- Keep distinct inputs and edge cases separately identifiable; parametrization
  reduces repeated code, not test cases.
- Measure before and after any speed or consolidation change
  (`--durations=30`, the JUnit report); a removed test names the test that
  still catches its failure.
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
[reproducible-environment](notes/reproducible-environment.md). Validate locally
against the WSL mirror before calling a branch green, and read SKIP counts, not
only failures, on data-gated tests — see
[linux-parity-testing](notes/linux-parity-testing.md).
