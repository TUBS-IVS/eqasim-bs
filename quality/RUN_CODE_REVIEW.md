# RUN_CODE_REVIEW.md — Code Review Protocol for eqasim-bs

> Use this protocol when reviewing a pull request, a refactor commit, or a
> single module on the Braunschweig synthesis pipeline. Drop the prompt
> below into a fresh AI session that has read access to this repository.

---

## Bootstrap (read first)

The reviewing session must read these before producing any finding:

1. [`AGENTS.md`](../AGENTS.md) — project overview, decisions, hard rules.
2. [`quality/QUALITY.md`](QUALITY.md) — fitness-to-purpose scenarios. Every
   finding's severity must reference one of the documented scenarios or be
   explicitly tagged `[off-spec]` with rationale.
3. [`docs/codebase/STRUCTURE.md`](../docs/codebase/STRUCTURE.md) — package
   layout. A finding that confuses `bavaria/`, `braunschweig/`, and
   `eqasim_common/` modules is invalid.
4. [`docs/codebase/CONCERNS.md`](../docs/codebase/CONCERNS.md) — known bugs
   BUG-001..011. **Do not re-report a known bug.** Reference it instead.
5. [`plan/refactor-eqasim-bs.md`](../plan/refactor-eqasim-bs.md) — refactor
   decisions D-1..D-5 in force. **D-5 (no bug fixes during behaviour-
   preserving relocation) is amended**: a finding that flags an inherited
   pattern as "needs fixing during the refactor" is invalid unless the
   pattern actively blocks the BS pipeline.

## Mandatory guardrails (no exceptions)

The following are **rejection-on-sight** in any AI-generated review:

- **Line numbers are mandatory.** Every finding must cite at least one
  `path/file.py:LINE` location. Findings without line numbers are
  filtered out before triage.
- **Read the function body, not the signature.** A finding that says
  "this function does not handle X" must either quote the function body
  to support the claim or be downgraded to a `QUESTION`.
- **Grep before claiming missing.** "There is no test for X" is invalid
  unless preceded by a search across `tests/`, `tests/braunschweig/`,
  and `analysis/`. Cite the search command.
- **No style findings.** Black/isort/flake8 disagreements are out of
  scope. Only flag code that is *incorrect*, not unstylish.
- **No bavaria/ findings unless the file is touched.** Per the refactor
  plan, `bavaria/` is frozen pending Phase 4 deletion. A finding on a
  bavaria/ file is only valid if the PR being reviewed modifies that
  file.
- **If unsure, mark `QUESTION:` not `BUG:`.** Confident-but-wrong
  findings poison the review. Hedge.
- **Ground every finding in a QUALITY.md scenario or fitness target.**
  Findings that have no link to a documented quality concern are
  marked `[off-spec]` and triaged last.

## Focus areas (mapped to architecture)

This pipeline has five subsystems with different review priorities:

### 1. Data loaders — `braunschweig/data/`

What to check:

- **Encoding.** All BA / Zensus / GENESIS loaders MUST pass `encoding=`
  explicitly. ANSI / cp1252 inputs are common; default UTF-8 silently
  corrupts German umlauts (BUG-007).
- **Leading-zero preservation.** Niedersachsen Kreis codes start with
  `03`. `astype(str)` on a numeric column drops the zero. Look for
  `dtype=str` on read or explicit zfill (BUG-003, BUG-006). The Pendler
  loader uses `r"\d{5}"` regex — that pattern is the BS standard.
- **Aggregate-row leakage.** The BA Pendler exports mix Kreis codes
  (`03101`) with Bundesland aggregates (`031xx`, `Übrige Kreise`). A
  plain `.str.len() == 5` filter accepts both. Look for `fullmatch(r"\d{5}")`.
- **Schema validation in `validate(context)`.** Every data loader stage
  should declare a `validate()` that returns a deterministic checksum
  or size — this is what synpp uses for caching.

Examples in scope:
- [`braunschweig/data/census/pendler.py`](../braunschweig/data/census/pendler.py)
- [`braunschweig/data/census/employment.py`](../braunschweig/data/census/employment.py)
- [`braunschweig/data/census/households_size_age.py`](../braunschweig/data/census/households_size_age.py)
- [`braunschweig/data/bbsr/regiostar.py`](../braunschweig/data/bbsr/regiostar.py)

### 2. IPF — `braunschweig/ipf/`

What to check:

- **Margin convergence.** `model.execute(context)` must assert that the
  output respects every active margin within
  `braunschweig.ipf.margin_validation_tolerance`. A test that only
  asserts "did not raise" is coverage theater (QUALITY.md §"Coverage
  Theater Prevention").
- **Dirichlet prior.** Default for `braunschweig.ipf.dirichlet_prior_strength`
  is `0.0`. Any change must come with a regression test on the smoke
  baseline (`plan/baselines/smoke_1pct_baseline.txt`).
- **Household-size axis.** The `use_household_size_margin` toggle adds a
  fifth IPF axis; the `prepare` stage must emit a frame with the right
  shape. Verify both shapes are exercised in
  [`tests/test_braunschweig_data.py`](../tests/test_braunschweig_data.py).
- **Stochastic rounding.** `attributed.execute(context)` must consume
  `random_seed` for reproducibility. A finding that the stage uses
  `np.random.*` without a seeded `RandomState` is a `BUG`.

### 3. Gravity & spatial — `braunschweig/gravity/`, `braunschweig/locations/`, `braunschweig/synthesis/spatial/`

What to check:

- **OD scope.** `gravity.model.execute(context)` must scope flows to the
  configured `bavaria.political_prefix` (the eight ZGB Kreise). Look
  for `df["dest_ars"].isin(scope)` patterns.
- **Friction matrix symmetry.** `evaluate_gravity` assumes the friction
  matrix is square and indexed identically to the population/employees
  vectors. A finding that an order mismatch can occur is valid.
- **Intra-Kreis synthesis.** `_synthesise_intra_kreis` must not double-
  count flows already present in the BA exports. Check the deduplication.
- **Commute-distance override.** `commute_distance.execute(context)`
  uses `cdfs.get(kreis, fallback_cdf)` — verify the fallback is the
  ZGB-aggregate CDF, not None.

### 4. Synthesis output — `synthesis/`, `braunschweig/synthesis/population/enriched.py`

What to check:

- **BS resident column.** Search for hardcoded `is_munich_resident`
  (BUG-001 inheritance). The BS pipeline emits `is_bs_resident`.
- **Household-member integrity.** After sampling, every `household_id`
  in `persons.csv` must have member count equal to the
  `household_size` in `households.csv` (BUG-002, QUALITY.md Scenario 2).
- **Income column duality.** `synthesis/income.py` is a placeholder
  emitting `household_income = 0.0`. The real continuous income lives
  in `household_income_eur`. A finding that confuses the two is valid.
- **Comment fences.** Phase 2 of the refactor introduced
  `# --- Inherited from eqasim-bavaria ---` and `# --- Braunschweig-
  specific ---` fences in merged modules. Lost fences in a PR are a
  `BUG`.

### 5. Tests — `tests/`, `tests/braunschweig/`

What to check:

- **StubContext usage.** New unit tests that need a synpp context must
  use the `StubContext` class (in `tests/test_braunschweig_data.py`
  and `tests/braunschweig/test_stages.py`). Reinventing it is a `BUG`.
- **Opt-in pipeline tests.** `tests/test_pipeline.py`,
  `tests/test_determinism.py`, `tests/test_simulation.py` and
  `tests/test_smoke_1pct.py` are gated on
  `EQASIM_BS_RUN_PIPELINE=1`. A test that runs synpp without that
  gate is a `BUG`.
- **No IDF region 10/11 fixtures.** Phase 3.1 deleted them. Any new
  reference to them is a `BUG`.

## Output format

Group findings by severity. Use this exact structure so triage scripts
can parse it:

```
## BUG findings
- [BUG] path/to/file.py:LINE — short title.
  Evidence: <quote function body, ≤5 lines>.
  Scenario: QUALITY.md §"<scenario name>" or [off-spec — explain].
  Suggested fix: <one sentence; code if trivial>.

## QUESTION findings
- [QUESTION] path/to/file.py:LINE — short title.
  Why I'm unsure: <one sentence>.
  How to resolve: <run X / read Y>.

## NIT findings (lowest priority; off-spec by definition)
- [NIT] path/to/file.py:LINE — short title.
```

A review with zero `BUG` findings is acceptable and often correct.

## Phase 2: Regression test for every BUG

After triage, write one regression test per confirmed BUG into
[`tests/test_regressions.py`](../tests/test_regressions.py) (create on
first use). Each test:

1. Reproduces the bug — must fail on the current `HEAD`.
2. Names the bug: `def test_BUG_NNN_<short_name>():`.
3. Cross-references the QUALITY scenario in a docstring.

Then commit the regression suite with the fix:

```text
fix(BS): <BUG-NNN one-line summary>
Adds tests/test_regressions.py::test_BUG_NNN_<short> covering
QUALITY.md §"<scenario>". Confirmed failing pre-fix, green post-fix.
```

Run [`pytest tests/test_regressions.py`](../tests/test_regressions.py) on
every PR after this protocol has been used at least once.

## Pre-merge checklist

- [ ] All `BUG` findings filed with line numbers and scenario links.
- [ ] All `BUG` findings have either a fix commit or an issue with
  scenario tag.
- [ ] `pytest tests/ -q` returns the documented gate (currently
  `65 passed, 4 skipped`).
- [ ] If the PR touches a synpp stage, `python -m synpp config_dryrun_braunschweig.yml`
  reaches `synthesis.output` without error.
- [ ] If the PR touches data loaders, the BS smoke run reproduces the
  baseline counts in [`plan/baselines/smoke_1pct_baseline.txt`](../plan/baselines/smoke_1pct_baseline.txt)
  within ±2 % (or strict equality if `EQASIM_BS_STRICT_SMOKE=1`).
