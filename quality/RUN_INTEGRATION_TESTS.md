# RUN_INTEGRATION_TESTS.md — Integration Test Protocol for eqasim-bs

> End-to-end verification that the Braunschweig synthesis pipeline runs from
> raw inputs to MATSim-ready CSVs and reproduces the locked baselines.
> Drop the prompt below into a fresh AI session, or follow the steps
> manually.

---

## Working directory

All commands assume the repository root
(`c:\Users\bienzeisler\Documents\GitHub\eqasim-bs` in this checkout) and
use **relative paths only**. PowerShell 5.1 is the supported shell.

## Bootstrap

The executing session must read these before running anything:

1. [`AGENTS.md`](../AGENTS.md) — environment activation, sampling-rate
   table, hard rules (no `rm -rf eqasim-data/`, no `git push --force`).
2. [`quality/QUALITY.md`](QUALITY.md) — fitness scenarios drive the
   quality gates below.
3. [`docs/codebase/INTEGRATIONS.md`](../docs/codebase/INTEGRATIONS.md) —
   external data sources (Zensus 2022, BA Pendleratlas, MiD 2023, INKAR,
   OSM, ALKIS).
4. [`plan/baselines/smoke_1pct_baseline.txt`](../plan/baselines/smoke_1pct_baseline.txt) —
   row counts and SHA-256 prefixes the smoke run must reproduce.

## Safety constraints

- **Never** run with `--no-verify`.
- **Never** delete `eqasim-data/cache_bs*/` without explicit user
  confirmation. They contain nested git checkouts (`eqasim-java`,
  `pt2matsim`).
- **Never** modify files under `eqasim-data/data/` — they are immutable
  inputs.
- **Never** commit anything from `eqasim-data/` other than the two
  `DOWNLOAD_CHECKLIST*.md` files.
- A 10 % run can take ~4 hours. Do not start one inside an interactive
  AI session unless the user explicitly requests it.

## Pre-flight checks

Run before any integration scenario:

```powershell
& "$env:LOCALAPPDATA\miniforge3\shell\condabin\conda-hook.ps1"; conda activate eqasim
python -c "import synpp, pandas, numpy, geopandas; print(synpp.__version__)"
Test-Path eqasim-data/data/braunschweig
Test-Path eqasim-data/cache_bs
git status --short
pytest tests/ -q
```

Pass criteria:
- Conda env activated, synpp version `1.5.1`.
- `eqasim-data/data/braunschweig/` exists (BS-specific inputs in place).
- `eqasim-data/cache_bs/` exists (warm cache; if absent, document a
  cold-cache scenario in the report).
- Working tree is clean *or* every dirty file is intentional and listed
  in the report.
- `pytest tests/ -q` returns the locked gate `65 passed, 4 skipped`.

If any pre-flight check fails, stop. Do not run the pipeline.

## Test matrix

The pipeline has three production sampling rates plus a CI dry-run:

| # | Run | Config | Cache | Output | Pass criterion |
|---|-----|--------|-------|--------|----------------|
| 1 | Dry-run    | `config_dryrun_braunschweig.yml` | `cache_bs_dryrun` | none (plan-only) | synpp prints `OK` for every stage; no exception. |
| 2 | 1 % smoke  | `config_local_braunschweig.yml` | `cache_bs` | `eqasim-data/output_bs/` | Reproduces `plan/baselines/smoke_1pct_baseline.txt` within ±2 % counts. Strict mode: byte-equal SHA-256 prefixes. |
| 3 | 10 % validation | `config_local_braunschweig_10pct.yml` | `cache_bs_10pct` | `eqasim-data/output_bs_10pct/` | `python -m scripts.validate_bs_10pct` exits 0; every fitness margin in QUALITY.md Scenarios 1–7 holds. |
| 4 | 25 % pre-release | `config_local_braunschweig_25pct.yml` | `cache_bs_25pct` | `eqasim-data/output_bs_25pct/` | Same gates as 10 %, plus `scripts.compare_25pct_vs_baseline` reports zero margin regression > 1 pp. |

Run #1 and #2 are mandatory. Run #3 is mandatory before tagging a
release. Run #4 is mandatory before merging into `main`.

## Execution UX

The executing AI session **must** report progress in three phases.

### Phase A — Plan (before any run)

Print a numbered table:

```
| # | Run        | Config                                     | Est. time | Will run? |
|---|------------|--------------------------------------------|-----------|-----------|
| 1 | Dry-run    | config_dryrun_braunschweig.yml             | ~2 min    | yes       |
| 2 | 1 % smoke  | config_local_braunschweig.yml              | ~10 min   | yes       |
| 3 | 10 %       | config_local_braunschweig_10pct.yml        | ~4 h      | skip (user did not request) |
| 4 | 25 %       | config_local_braunschweig_25pct.yml        | ~12 h     | skip      |
```

Wait for user confirmation before kicking off run #3 or #4.

### Phase B — Live progress

For each run, emit one line per major event:

```
⧗ Run 2 starting — 1 % smoke
✓ Run 2 stage data.census.zensus_grid (3.2s)
✓ Run 2 stage ipf.attributed (47.8s)
✗ Run 2 stage synthesis.locations.work — RuntimeError: ARS join lost 12 rows
```

Use `⧗` running, `✓` ok, `✗` failed. Never silently swallow stderr.

### Phase C — Summary

After the matrix completes, print a summary table:

```
| # | Run       | Result | Time   | Notes                          |
|---|-----------|--------|--------|--------------------------------|
| 1 | Dry-run   | ✓ pass | 1m54   |                                |
| 2 | 1 % smoke | ✓ pass | 9m42   | Reproduces baseline within ±2% |
| 3 | 10 %      | skip   | —      |                                |
| 4 | 25 %      | skip   | —      |                                |
```

Then a one-line recommendation: `RECOMMENDATION: merge` or
`RECOMMENDATION: block — Run 2 regressed beyond ±2 %`.

## Quality gates per run

Quality gates are derived from `quality/QUALITY.md` scenarios and the
locked baselines. Every gate cites the file/field that backs it.

### Run 1 (dry-run)

| Gate | Source | Pass criterion |
|------|--------|----------------|
| Stage graph reaches `synthesis.output` | synpp DAG | `python -m synpp config_dryrun_braunschweig.yml` exits 0. |
| No deprecation warnings raised at fail-level | `bavaria/_deprecation.py` | stderr contains zero `DeprecationWarning` flagged as error. |

### Run 2 (1 % smoke)

| Gate | Source | Pass criterion |
|------|--------|----------------|
| `households.csv` row count | baseline | `5,733 ± 2 %`; SHA-256 prefix `e3599e24bdeb` (strict mode). |
| `persons.csv` row count | baseline | `11,472 ± 2 %`; SHA-256 prefix `97537ad6650f` (strict). |
| `activities.csv` row count | baseline | `47,362 ± 2 %`; prefix `4eec94f187cc`. |
| `trips.csv` row count | baseline | `35,890 ± 2 %`; prefix `c004826ea0d2`. |
| `is_bs_resident` column non-trivial | QUALITY Scenario 1 | `df['is_bs_resident'].nunique() == 2`. |
| Household-member integrity | QUALITY Scenario 2 | For all `household_id`, `persons.groupby('household_id').size() == households['household_size']`. |
| Kreis ARS leading-zero preserved | QUALITY Scenario 3 | All ARS values match `^\d{5}$` and start with `03`. |
| Commute distance below ZGB cap | QUALITY Scenario 4 | 99th-percentile commute distance ≤ 200 km. |

The functional smoke test [`tests/test_smoke_1pct.py`](../tests/test_smoke_1pct.py)
codifies the first four gates. Run it with:

```powershell
$env:EQASIM_BS_RUN_PIPELINE = "1"
pytest tests/test_smoke_1pct.py -v
```

Strict mode: also set `$env:EQASIM_BS_STRICT_SMOKE = "1"`.

### Run 3 (10 % validation)

| Gate | Source | Pass criterion |
|------|--------|----------------|
| Zensus 2022 hh-size marginals | QUALITY Scenario 5 | Each household-size bin within ±2 pp of input share. |
| BA Pendleratlas Kreis-to-Kreis | QUALITY Scenario 6 | Per-Kreis flow totals reproduced within ±5 % (KL ≤ 0.02). |
| MiD 2023 trip-distance distribution | QUALITY Scenario 7 | Earth-mover distance against MiD reference ≤ 0.08. |
| Validation harness | scripts | `python -m scripts.validate_bs_10pct` exits 0. |

### Run 4 (25 % pre-release)

Same gates as Run 3, plus:

| Gate | Source | Pass criterion |
|------|--------|----------------|
| 25 % vs 10 % drift | scripts | `python -m scripts.compare_25pct_vs_baseline` reports no margin moved by more than 1 percentage point. |
| MATSim plan parsing | matsim | The Java `RunSimulation` smoke target consumes the generated `plans.xml.gz` without parsing errors. |

## Field reference table (for quality gates)

The quality-gate field names below were copied character-for-character
from the corresponding source on `2026-04-27`. Re-verify if a gate
appears to fail on a name lookup.

| Field | File | Type | Meaning |
|-------|------|------|---------|
| `household_id` | `synthesis/output.py` (households + persons) | int | Household identity, surrogate. |
| `household_size` | `braunschweig/synthesis/population/enriched.py` | int | Member count assigned at sampling. |
| `is_bs_resident` | `braunschweig/synthesis/population/enriched.py` | bool | True iff person resides inside ZGB-8. |
| `household_income` | `synthesis/income.py` | float (always 0.0 — placeholder) | Legacy column; see BUG-008. |
| `household_income_eur` | `braunschweig/synthesis/income.py` | float | Real continuous income in EUR/month. |
| `kreis_ars` | `braunschweig/data/spatial/zgb.py` | str (5-char) | Kreis ARS, leading zero preserved. |
| `orig_ars`, `dest_ars` | `braunschweig/data/census/pendler.py` | str (5-char) | Pendler OD endpoints. |
| `flow` | `braunschweig/data/census/pendler.py` | int | Number of commuters orig→dest. |
| `commute_distance_km` | `braunschweig/synthesis/spatial/commute_distance.py` | float | Sampled commute distance per worker. |

## Parallelism

Runs 1 and 2 share a cache directory family; do not run them in
parallel. Runs 3 and 4 use different caches and *can* be parallelised
on a machine with ≥32 GB RAM:

```powershell
Start-Job -Name run10 -ScriptBlock { python -m synpp config_local_braunschweig_10pct.yml }
Start-Job -Name run25 -ScriptBlock { python -m synpp config_local_braunschweig_25pct.yml }
Wait-Job run10, run25
Receive-Job run10, run25
```

Avoid this on a laptop — both processes will hit swap.

## Post-run verification

For every completed run:

1. Inspect the synpp log printed to stdout — `Stage failed` is fatal.
2. List the output directory: `Get-ChildItem eqasim-data/output_bs*/`.
3. For Run 2, run [`tests/test_smoke_1pct.py`](../tests/test_smoke_1pct.py)
   in strict mode to confirm baseline equality:
   ```powershell
   $env:EQASIM_BS_RUN_PIPELINE = "1"; $env:EQASIM_BS_STRICT_SMOKE = "1"
   pytest tests/test_smoke_1pct.py -v
   ```
4. For Runs 3 / 4, run the validation harness:
   ```powershell
   python -m scripts.validate_bs_10pct
   ```
5. Confirm `git status` shows no unintended changes under
   `eqasim-data/cache_bs*/` (the synpp caches are gitignored — if they
   appear, the `.gitignore` regressed).

## Reporting format

Submit results as Markdown using this skeleton:

```markdown
# Integration test report — <date>, <git SHA>

Pre-flight: <pass / fail with reason>

## Plan
<Phase A table>

## Results
<Phase C summary table>

## Failed gates
- Run <N> gate <name>: <observed> vs expected <expected>. Triage: <BUG-NNN / new finding>.

## Recommendation
<merge | block | re-run>
```

If the recommendation is `block`, file the failing gate as a finding in
the corresponding [`RUN_CODE_REVIEW.md`](RUN_CODE_REVIEW.md) cycle and
either fix or open an issue before re-running.
