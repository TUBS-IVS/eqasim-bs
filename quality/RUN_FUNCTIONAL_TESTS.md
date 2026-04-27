# RUN_FUNCTIONAL_TESTS.md — Functional Test Protocol for eqasim-bs

> Run this protocol whenever you need to confirm the Braunschweig pipeline
> still satisfies the fitness-to-purpose scenarios in
> [`QUALITY.md`](QUALITY.md). Faster than a full integration run, slower
> than `pytest -q`, and tied 1:1 to the documented quality scenarios.

---

## Working directory and environment

Repo root, PowerShell 5.1:

```powershell
cd c:\Users\bienzeisler\Documents\GitHub\eqasim-bs
& "$env:LOCALAPPDATA\miniforge3\shell\condabin\conda-hook.ps1"; conda activate eqasim
```

## What is "functional" on this project

Per [`QUALITY.md`](QUALITY.md), functional tests verify that the synthesised
ZGB-8 population reproduces:

- Zensus 2022 marginals (household size, age, sex, employment).
- BA Pendleratlas 2025 commuter flows (Kreis-to-Kreis OD totals).
- MiD 2023 trip-distance distribution.
- Determinism: identical seed → identical CSV outputs.
- MATSim-readable plans without manual data fixes.

Unit tests under [`tests/`](../tests/) check the building blocks; this
protocol covers the *outcome* layer.

## Test layers

The functional test suite is organised in three layers:

### Layer A — Stage-level unit tests (fast, always-on)

[`tests/braunschweig/test_stages.py`](../tests/braunschweig/test_stages.py)
covers 10 BS-specific stages with stubbed contexts. No live synpp DAG,
no cache, no I/O. Runs in <2 s. These tests are the safety net for the
small refactors during Phase 4.

```powershell
pytest tests/braunschweig/test_stages.py -v
```

Pass criterion: 12 tests pass, zero skipped.

### Layer B — Data-loader functional tests (medium, always-on)

[`tests/test_braunschweig_data.py`](../tests/test_braunschweig_data.py)
tests the BS data loaders against fixture CSVs that mirror the real
inputs. Runs in <10 s.

```powershell
pytest tests/test_braunschweig_data.py -v
```

Pass criterion: all tests in the module pass. The loader fixtures are
under [`tests/fixtures/braunschweig/`](../tests/fixtures/braunschweig/);
add a new fixture before adding a new loader test.

### Layer C — End-to-end smoke regression (gated, full pipeline)

[`tests/test_smoke_1pct.py`](../tests/test_smoke_1pct.py) runs
`config_local_braunschweig.yml` end-to-end and compares the output CSVs
to the locked baseline in
[`plan/baselines/smoke_1pct_baseline.txt`](../plan/baselines/smoke_1pct_baseline.txt).
Gated on the environment variables below to prevent CI from accidentally
launching a 10-minute job.

```powershell
$env:EQASIM_BS_RUN_PIPELINE = "1"
pytest tests/test_smoke_1pct.py -v
```

Strict mode (byte-equal SHA-256 prefixes, not ±2 % rows):

```powershell
$env:EQASIM_BS_RUN_PIPELINE = "1"
$env:EQASIM_BS_STRICT_SMOKE = "1"
pytest tests/test_smoke_1pct.py -v
```

Pass criterion (default): every output CSV is within ±2 % of the baseline
row count. Strict mode: row counts are exact and SHA-256 prefixes match
the baseline file character-for-character.

## Mapping: scenarios → tests

Every scenario in [`QUALITY.md`](QUALITY.md) §"Fitness-to-Purpose Scenarios"
must map to at least one functional test. Drift is treated as coverage
theater.

| Scenario | Layer A | Layer B | Layer C |
|----------|---------|---------|---------|
| 1. `is_bs_resident` column not silently empty | `TestSampleCounts` | — | smoke baseline `persons.csv` SHA |
| 2. Household-member integrity | `TestSampleCounts` | — | smoke baseline cross-check on `households.csv` ↔ `persons.csv` |
| 3. Leading zero on Kreis IDs | `TestDeriveKreisArs5`, `TestGemeindeToKreis` | loader tests for Pendler / RegioStaR | smoke output ARS column |
| 4. Commute distance over-cap | `TestCommuteOverride`, `TestCommuteDrawFromCdf` | — | smoke `trips.csv` 99th percentile |
| 5. Zensus household-size marginal | `TestIncomeSizeMap` (proxy via 6→5 bin map) | hh-size loader test | 10 % validation harness |
| 6. BA Pendler reproduction | `TestEvaluateGravity` | Pendler loader test | 10 % validation harness |
| 7. MiD trip-distance distribution | — | MiD loader test | 10 % validation harness |

Layer A + B run unconditionally; Layer C runs on the smoke gate; the
10 % validation harness is invoked from
[`RUN_INTEGRATION_TESTS.md`](RUN_INTEGRATION_TESTS.md).

## Coverage theater rejection

Any new test in `tests/` or `tests/braunschweig/` is rejected on review
if it matches one of these patterns (see [`QUALITY.md`](QUALITY.md)
§"Coverage Theater Prevention"):

- Asserts only `df is not None` or `len(df) > 0`.
- Asserts an IPF stage "converged" without checking margin tolerances.
- Counts output rows without asserting marginals.
- Mocks `context.stage(...)` with a hand-built DataFrame whose schema
  does not match the real upstream stage.
- Calls `gravity.execute(context)` and only checks "did not raise".

Every new test must explicitly state which QUALITY scenario it covers
in its docstring or class docstring.

## Pre-merge gate

```powershell
pytest tests/ -q
```

The locked gate is **`65 passed, 4 skipped`** (4 skipped = the opt-in
pipeline tests gated on `EQASIM_BS_RUN_PIPELINE`). Any deviation must
be explained in the PR.

When the PR touches a synpp stage or a data loader, also run the smoke
gate:

```powershell
$env:EQASIM_BS_RUN_PIPELINE = "1"
pytest tests/test_smoke_1pct.py -v
```

If the smoke output drifts beyond ±2 %, update
[`plan/baselines/smoke_1pct_baseline.txt`](../plan/baselines/smoke_1pct_baseline.txt)
**only** with a written justification logged in
[`plan/refactor-eqasim-bs.md`](../plan/refactor-eqasim-bs.md) and
explicit user confirmation.

## When a test fails

1. Read the assertion carefully — the BS tests assert against locked
   baselines or QUALITY scenarios, not against arbitrary code shapes.
2. If the failure is a baseline drift, do **not** rewrite the baseline.
   First check whether the change is intentional (config knob,
   refactored stage) or an accidental regression. The baseline-update
   procedure is in [`AGENTS.md`](../AGENTS.md) §"Hard rules".
3. If the failure is a Layer-A unit test, the offending stage was
   modified — re-read the stage source and confirm the new behaviour
   matches the QUALITY scenario for that stage.
4. If the failure is a Layer-C smoke regression, file a finding via
   [`RUN_CODE_REVIEW.md`](RUN_CODE_REVIEW.md) before re-running.

## Reporting format

After running this protocol manually, append to the PR description:

```markdown
## Functional tests

Layer A: <pass/fail counts>
Layer B: <pass/fail counts>
Layer C: <ran=yes/no — strict=yes/no — result>

Scenarios covered: <list of QUALITY scenarios touched>
Baseline drift: <none | row counts within ±X % | justified update>
```
