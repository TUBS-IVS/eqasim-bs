# RUN_SPEC_AUDIT.md — Council of Three Spec Audit for eqasim-bs

> Periodic, multi-model audit of the Braunschweig synthesis pipeline against
> its written specifications. Run before tagging a release, after a major
> refactor (e.g., the Phase 2 `eqasim_common/` extraction), or whenever a
> fitness-to-purpose scenario in [`QUALITY.md`](QUALITY.md) is questioned.

---

## Why three models?

Single-model audits miss bugs. Each frontier model has different blind
spots: model A may overlook numerical-stability issues but catch schema
drift; model B does the inverse. Three independent audits with
deliberately different priors — then a triage step that merges by
confidence — catches defects that any individual model alone would miss.

Required: three sessions in three independent models, never sharing
context. Recommended trio (any current frontier mix is acceptable):

- **Auditor 1:** GPT-5 / OpenAI o-series — strong on numerical and
  statistical reasoning. Good at IPF, gravity, distance-distribution
  audits.
- **Auditor 2:** Claude Opus / Sonnet — strong on architecture,
  cross-module invariants, schema and naming consistency.
- **Auditor 3:** Gemini Pro — strong on long-context structural reads
  and German-language source documents (BA-Statistik, Zensus PDFs).

If only two frontier models are available, run Auditor 3 as a follow-up
of one of the first two with a freshly-loaded context.

## The audit prompt (copy-paste)

Paste this into each model session verbatim. Substitute the bracketed
items where indicated.

---

> You are auditing the eqasim-bs synthesis pipeline against its written
> specification. You are one of three independent auditors — your output
> will be merged with the others.
>
> **Read in this order before producing any finding:**
>
> 1. `AGENTS.md` (root) — project bootstrap.
> 2. `quality/QUALITY.md` — fitness scenarios and theater rules.
> 3. `docs/codebase/STRUCTURE.md`, `ARCHITECTURE.md`, `CONVENTIONS.md`,
>    `INTEGRATIONS.md`, `TESTING.md`, `CONCERNS.md`.
> 4. `plan/refactor-eqasim-bs.md` — refactor decisions D-1..D-5
>    currently in force.
> 5. The source files under `braunschweig/`, `synthesis/`, `matsim/`,
>    `eqasim_common/`, and the active configs
>    `config_local_braunschweig*.yml` and `config_dryrun_braunschweig.yml`.
>
> **Guardrails (rejection-on-sight):**
>
> - Every finding must cite at least one `path/file.py:LINE` location.
> - Quote the relevant function body — do not paraphrase.
> - If a known bug is referenced in `docs/codebase/CONCERNS.md` (BUG-001..011),
>   do not refile it — note "see BUG-NNN" instead.
> - Do not flag style issues. Only flag code that is incorrect, missing,
>   or contradicts the spec.
> - If unsure, output `QUESTION:` not `BUG:`.
> - Do not propose fixes for files under `bavaria/` (frozen pending Phase 4
>   per Decision D-3).
>
> **Scrutiny areas (in priority order, each cite the source spec):**
>
> 1. **Determinism.** `synpp.config["random_seed"]` must reach every
>    stochastic stage. Cite the seed-propagation chain. The spec is
>    QUALITY.md §"Coverage Theater Prevention" + the locked baselines
>    in `plan/baselines/`.
> 2. **Margin convergence.** The IPF stage `braunschweig.ipf.model` must
>    respect `margin_validation_tolerance`. Cite the assertion. Cross-
>    reference the `use_household_size_margin` toggle and the additional
>    fifth-axis prepare path.
> 3. **OD scope.** `braunschweig.gravity.model` must produce flows scoped
>    to ZGB-8 (`bavaria.political_prefix`). Cite the scope filter and
>    the test that proves it.
> 4. **Leading-zero preservation.** All loaders that read German Kreis
>    codes must read them as strings or zfill to 5 characters. Audit
>    `braunschweig/data/census/*.py` and `braunschweig/data/spatial/*.py`.
> 5. **Encoding.** German source files (`.csv`, `.txt`) must be opened
>    with explicit `encoding=`. Default UTF-8 silently corrupts cp1252.
> 6. **Residency column.** `is_bs_resident` (not `is_munich_resident`)
>    must propagate from `braunschweig/synthesis/population/enriched.py`
>    through `synthesis/output.py` into `persons.csv` with two distinct
>    values. Cite the configurable column-name path if implemented; flag
>    a `BUG` if it is still hardcoded.
> 7. **Household-member integrity.** `synthesis/population/sampled.py`
>    must produce groups that match `household_size` exactly across the
>    1 %, 10 %, 25 % runs. Cite the splitting logic.
> 8. **Income duality.** Distinguish placeholder `household_income`
>    (always 0.0) from `household_income_eur` (real continuous income).
>    Flag any consumer that mixes the two.
> 9. **State-machine completeness.** Stage cache invalidation: when a
>    config knob changes, does the synpp cache invalidate? Cite the
>    `configure(context)` hash inputs for every BS-specific stage.
> 10. **Safeguards before irreversible work.** Before a 10 % / 25 % run
>     that takes hours, does the user see counts of the inputs being
>     loaded and the expected output sizes? Find the pre-flight log;
>     flag if absent.
>
> **Output format:**
>
> ```
> # Auditor <N> — <model name> — <date>
>
> ## CRITICAL findings
> - [BUG] file.py:LINE — title.
>   Spec violated: <quote QUALITY.md / spec doc>.
>   Evidence: <quoted code, ≤5 lines>.
>   Suggested fix: <one sentence>.
>
> ## HIGH findings
> ...
>
> ## MEDIUM findings
> ...
>
> ## QUESTIONS
> - [QUESTION] file.py:LINE — title.
>   Why I'm unsure: ...
>
> ## What I did NOT audit
> - <areas you skipped, with reason>
> ```
>
> Be specific. Be terse. Quote the code.

---

## Triage process

After all three auditors finish, merge the findings:

### Step 1 — Tabulate

For each unique finding, mark which auditors flagged it:

| ID | File:Line | Title | A1 | A2 | A3 | Severity (max) |
|----|-----------|-------|----|----|----|----------------|
| F001 | `braunschweig/data/census/pendler.py:120` | Aggregate row leak | BUG | — | BUG | BUG |
| F002 | `synthesis/output.py:88` | Hardcoded `is_munich_resident` | BUG | BUG | BUG | BUG |
| F003 | `braunschweig/ipf/model.py:340` | Missing tolerance assertion | — | BUG | QUESTION | BUG |

### Step 2 — Confidence levels

- **Triple-confirmed (all three flagged):** highest priority. Write a
  regression test, fix immediately on a feature branch, gate merge on
  the smoke baseline.
- **Double-confirmed:** investigate. Either confirm and fix, or document
  why the third auditor was right to skip it.
- **Single-flagged:** triage. Often correct (one auditor noticed what
  the others missed) but more often a false positive. Spend ≤30 min
  per finding — if it remains ambiguous, file as a `QUESTION` issue.
- **Auditor-only QUESTIONs:** convert to issues only if the question
  cannot be answered by re-reading the spec.

### Step 3 — Fix execution

Do **not** stuff every fix into one PR. Group by subsystem:

- One PR for `braunschweig/data/` findings.
- One PR for `braunschweig/ipf/` findings.
- One PR for `braunschweig/gravity/` and `braunschweig/locations/`.
- One PR for `synthesis/`, `matsim/` output findings.
- One PR for `tests/` and `quality/` findings.

Each PR:

1. Adds regression tests in `tests/test_regressions.py` first
   (red).
2. Applies the fix (green).
3. Re-runs `pytest tests/ -q` to confirm the baseline gate
   (`65 passed, 4 skipped` plus the new regression tests).
4. Re-runs the 1 % smoke. If a baseline number moves by more than
   2 %, update the baseline file with a justification logged in
   `plan/refactor-eqasim-bs.md`.

### Step 4 — Audit archive

Save raw auditor outputs to `quality/spec_audits/` with the filename
`audit_<YYYY-MM-DD>_<model>.md`. Save the merged triage table to
`quality/spec_audits/triage_<YYYY-MM-DD>.md`. These archives feed the
next audit cycle (drift detection).

## When to run

- **Before tagging a release** (e.g., `bs-1pct-v1.0`).
- **After a major refactor** (e.g., end of each Phase in
  `plan/refactor-eqasim-bs.md`).
- **When a fitness scenario is questioned** in code review.
- **After a public-data-source schema change** (Zensus, BA Pendleratlas,
  MiD).

Quarterly is a reasonable cadence even without trigger events.

## Empirical results (filled in over time)

| Date | Auditors | Findings (BUG / QUESTION) | False-positive rate |
|------|----------|---------------------------|---------------------|
| —    | (first run pending) | — | — |

Update this table after every audit so the team learns which model
catches which class of defects on this codebase.
