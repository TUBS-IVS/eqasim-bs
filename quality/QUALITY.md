# Quality Constitution: eqasim-bs (Braunschweig)

> **Status**: active (Phase 3.4 complete). The four `RUN_*.md` protocols
> ([`RUN_CODE_REVIEW.md`](RUN_CODE_REVIEW.md),
> [`RUN_FUNCTIONAL_TESTS.md`](RUN_FUNCTIONAL_TESTS.md),
> [`RUN_INTEGRATION_TESTS.md`](RUN_INTEGRATION_TESTS.md),
> [`RUN_SPEC_AUDIT.md`](RUN_SPEC_AUDIT.md)) operationalise the scenarios
> below. The functional test layers live in
> [`tests/braunschweig/test_stages.py`](../tests/braunschweig/test_stages.py),
> [`tests/test_braunschweig_data.py`](../tests/test_braunschweig_data.py),
> and [`tests/test_smoke_1pct.py`](../tests/test_smoke_1pct.py).

## Purpose

Quality on this project is anchored in three principles.

- **Deming — quality is built in, not inspected in.**  The synpp DAG, the
  `docs/codebase/` documents and this constitution are the inheritance
  mechanism.  Every AI session reads them before touching code so the bar is
  consistent across contributors and conversations.
- **Juran — fitness for use.**  Fitness for this project is not "tests pass".
  Fitness is *"the synthesised ZGB-8 population reproduces the documented
  Zensus 2022 marginals, BA Pendleratlas 2025 commuter flows and MiD 2023
  trip-distance distribution within agreed tolerances, deterministically given
  the configured seed, and the resulting MATSim plans run end-to-end without
  manual data fixes"*.
- **Crosby — quality is free.**  A 10 % synthesis run takes ~4 hours on a
  laptop.  Detecting a defect after MATSim simulation costs an order of
  magnitude more than catching it at the data-loader or IPF stage.  The
  upfront cost of margin assertions, schema checks and reproducibility tests
  is therefore strictly cheaper than the alternative.

## Coverage Targets

| Subsystem | Target | Why |
|-----------|--------|-----|
| `braunschweig/data/` (Zensus, BA, MiD, INKAR, OSM, ALKIS loaders) | 85 % | Schema drift on public data sources is the #1 silent-failure source.  See [CONCERNS BUG-006/007](docs/codebase/CONCERNS.md) — encoding mistakes and `"N.A."` rows already produced empty merges in production. |
| `braunschweig/gravity/` | 90 % | Core calibration logic with intra-Kreis synthesis, external commuter injection and IPF post-conditions.  Numerical bugs here bias commute distances by kilometres without raising. |
| `bavaria/ipf/` (used via aliases) | 80 % | Legacy code we cannot freely modify (CON-001).  Coverage focuses on regression detection rather than redesign. |
| `braunschweig/synthesis/` (vehicles, income, hh_type, commute_distance) | 85 % | Stochastic stages that drive downstream demand. |
| `synthesis/` and `matsim/` (region-neutral output) | 80 % | Read by Java MATSim; schema breakage stops simulation. |
| `eqasim_common/` (created in Phase 1) | 90 % | New code; no excuse for low coverage. |
| `scripts/` (one-off analyses, validation harness) | 60 % | Best-effort; prefer integration tests over per-script unit tests. |

Every percentage is non-negotiable until a documented scenario in
[`docs/codebase/CONCERNS.md`](docs/codebase/CONCERNS.md) is closed.  Increase
the target after closing a scenario; never decrease it without recording the
trade-off here.

## Coverage Theater Prevention

The following patterns count as **fake tests** on this project and must be
rejected in code review:

- Asserting `df is not None` or `len(df) > 0` without checking schema or
  values.  All BS data loaders can return non-empty DataFrames full of NaN
  (BUG-007).
- Asserting an IPF stage "converged" without asserting that the resulting
  weights satisfy every margin within the published tolerance (BUG-009).
- Counting output rows (`households.csv`, `persons.csv`) without asserting
  the marginal distributions match the inputs.  The smoke baseline locks
  *both* counts and content hashes for this reason.
- Mocking synpp `context.stage(...)` to return a constructed DataFrame
  whose schema differs from the real upstream stage's output.  Mock
  fixtures must be derived from real cache slices.
- Calling `gravity.execute(context)` and only checking it does not raise.
  The function must produce an OD matrix whose row/column sums match BA
  Pendleratlas totals to the published tolerance.
- Running pytest with the upstream IDF tests excluded *without* recording
  why each was excluded.  See Decision D-5 in
  [`plan/refactor-eqasim-bs.md`](plan/refactor-eqasim-bs.md).

## Fitness-to-Purpose Scenarios

Each scenario carries a requirement tag.  `formal` = explicit decision in
`plan/`, `inferred` = derived from defensive code, `user-confirmed` = stated
by a maintainer.  Verification commands assume the conda env `eqasim` is
activated and the `eqasim-data/cache_bs/` cache exists.

### Scenario 1: Residency flag mismatch silently empties output column

**Requirement tag:** `[Req: inferred — CONCERNS BUG-001]`

**What happened:** [`synthesis/output.py`](synthesis/output.py) hardcodes the
column name `is_munich_resident`, but the Braunschweig enrichment stage
emits `is_bs_resident`.  Without a guard, the CSV writer silently produces
either an empty column or drops the residency information altogether,
which downstream MATSim scenarios cannot detect.

**The requirement:** The output writer must accept the residency column
name from configuration, raise if the configured column is absent in the
incoming DataFrame, and the smoke run must produce a non-empty
`is_bs_resident` column with non-zero variance.

**How to verify:** `pytest tests/test_output_residency.py -v` (to be
added in Phase 3) plus `python -c "import pandas as pd;
df = pd.read_csv('eqasim-data/output_bs/persons.csv', sep=';');
assert df['is_bs_resident'].nunique() == 2"`.

### Scenario 2: Household-member grouping corruption

**Requirement tag:** `[Req: inferred — CONCERNS BUG-002]`

**What happened:**
[`synthesis/population/sampled.py`](synthesis/population/sampled.py) splits
person indices using counts that do not match the actual replication
factor, so households with 10+ members can contain persons drawn from two
or three different source households.  Mode-choice and travel demand
become incoherent at the household level.

**The requirement:** After sampling, every household ID's member count in
`persons.csv` must equal the `household_size` value in `households.csv`,
for 100 % of households across the 1 %, 10 % and 25 % runs.

**How to verify:** Functional test (Phase 3) that joins both output CSVs
and asserts equality of `groupby('household_id').size()` against
`households['household_size']`.

### Scenario 3: Leading zero loss on commune / Kreis IDs

**Requirement tag:** `[Req: inferred — CONCERNS BUG-003, BUG-006]`

**What happened:** German Kreis codes start with `03` (Niedersachsen).
Several loaders use `df["id"].astype(str)` which drops the leading zero,
shifting `03101` to `3101`.  Joins fall back to default distributions
silently, biasing commute distances by ~5 km.

**The requirement:** Every loader that produces a `commune_id`,
`kreis_id`, `ars5` or `ars12` column must guarantee five-character (Kreis)
or eight/twelve-character (Gemeinde, ARS12) zero-padded strings, verified
by a schema check in the loader and a regression test on the smoke run.

**How to verify:** Phase 3 functional test asserts
`df["kreis_id"].str.match(r"^03[0-9]{3}$").all()` for ZGB-8 outputs.

### Scenario 4: Reproducibility under fixed seed

**Requirement tag:** `[Req: formal — config_local_braunschweig.yml seed: 1234, plan/refactor-eqasim-bs.md Phase 4 verification]`

**What happened:** Hardcoded seed offsets (e.g. `91731` in
[`braunschweig/synthesis/population/enriched.py`](braunschweig/synthesis/population/enriched.py))
mean RNG state drifts when stage execution order changes.  Two runs with
the same configured seed can produce different vehicles and incomes if any
upstream IPF output ordering changes.

**The requirement:** Running the 1 % smoke configuration twice with the
same seed must produce byte-identical
`households.csv`, `persons.csv`, `activities.csv`, `trips.csv` (matching
the SHA-256 prefixes locked in [`plan/baselines/smoke_1pct_baseline.txt`](plan/baselines/smoke_1pct_baseline.txt)).

**How to verify:** `pytest tests/test_determinism.py` plus the validation
harness at `python -m scripts.validate_bs_10pct` re-running the smoke
config with `--seed 1234` and diffing hashes against the baseline file.

### Scenario 5: IPF "converged" but margins still violated

**Requirement tag:** `[Req: inferred — CONCERNS BUG-009]`

**What happened:** [`bavaria/ipf/model.py`](bavaria/ipf/model.py) declares
convergence when factor changes fall below tolerance, even if the resulting
weights leave margin sums far from the targets (common with infeasible
problem statements).  Downstream sampling consumes biased weights.

**The requirement:** After IPF declares convergence, every margin's
weighted sum must be within 0.1 % of its target; otherwise the run must
fail loudly with the violating margin reported.  This applies to every
IPF invocation: household size, age strata, sex, working-age employment,
household type.

**How to verify:** Functional test (Phase 3) re-runs the IPF stage on a
small fixture and asserts margin closeness; CI gate runs on every PR.

### Scenario 6: INKAR "N.A." rows silently empty income map

**Requirement tag:** `[Req: inferred — CONCERNS BUG-007]`

**What happened:**
[`braunschweig/data/inkar/household_income.py`](braunschweig/data/inkar/household_income.py)
runs `dropna()` after coercing `"N.A."` to NaN.  If a snapshot of INKAR
ships with `"N.A."` for the income column, every row drops, the
downstream `.map()` returns NaN for every household, and mode-choice
becomes income-blind without raising.

**The requirement:** Income loaders must assert that at least 95 % of
ZGB-8 Gemeinden have a non-null mean income after parsing, with the
diagnostic naming the offending Gemeinden.

**How to verify:** Phase 3 unit test with a synthetic INKAR file that
contains a mix of valid and `"N.A."` rows; loader must raise.

### Scenario 7: BA Pendleratlas commuter flows reproduced within tolerance

**Requirement tag:** `[Req: formal — config_local_braunschweig.yml gravity_slope: -0.065, plan/calibration-analysis-2025.md]`

**What happened:** The gravity model is calibrated to BA Pendleratlas
2025 inter-Kreis SvB flows by tuning `gravity_slope`.  Refactoring that
moves files between modules must not perturb the calibrated R² vs the
official BA flow totals.

**The requirement:** After Phase 4 verification, the synthesised OD
matrix must reproduce BA Pendleratlas inter-Kreis totals with R² ≥ 0.85
at 10 % sampling and ≥ 0.80 at 1 % sampling.

**How to verify:** `python -m scripts.analyze_commute_breakdown` reports
the R² values; baseline is locked under [`plan/baselines/`](plan/baselines/).

## AI Session Quality Discipline

1. Read this file *and* `docs/codebase/*.md` before touching code.
2. Run `pytest tests/ -v -k "not test_pipeline and not test_simulation and not test_determinism"` before marking any task complete.  The excluded tests are the 11 pre-existing IDF failures (Decision D-5); fix them out of scope until the refactor lands.
3. Add tests in the same commit as the code under test.  Edge cases live in [CONCERNS](docs/codebase/CONCERNS.md); cite the BUG-### number when adding a regression test.
4. Update this file when a new failure mode is discovered.  Never delete a fitness-to-purpose scenario; mark it "Closed in PR #N" instead.
5. End every session with a short "Quality Compliance" note: which scenarios you exercised, which baselines you re-ran, which TODOs from `docs/codebase/` you advanced.

## The Human Gate

The following decisions cannot be made by an AI session alone — record them in `plan/` and request human review:

- Adjusting any documented tolerance (R², margin closeness, sample size).
- Modifying the smoke baseline counts or hashes in [`plan/baselines/smoke_1pct_baseline.txt`](plan/baselines/smoke_1pct_baseline.txt).
- Changing the seed (`config_local_braunschweig.yml seed: 1234`) or the documented `gravity_slope`.
- Touching the Java MATSim package (`org.eqasim.bavaria.*`) — see Decision D-1c.
- Closing or rephrasing a fitness-to-purpose scenario, or downgrading a coverage target.
- Sharing or publishing data subject to the MiD 2023 sample 7555 access agreement.
