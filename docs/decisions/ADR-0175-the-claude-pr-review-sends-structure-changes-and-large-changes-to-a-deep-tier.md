# ADR-0175 · 2026-11-02 · The Claude PR review sends structure changes and large changes to a deep tier

- **Status:** accepted (implemented on `ci/claude-review-deep-tier`)
- **Numbering:** 0175 checked free across all local and remote branches on 2026-11-02.
- **Owner decision (2026-11-02):** three tiers by weight of the change: a small change gets the light model, a medium
  or somewhat complex one the full model, a change to important structures or a very large one the deep model; every
  run is capped by a budget (the amount is not published since 2026-11-05, ADR-0181). Applied the same way in every
  repository that runs a copy of this review.

## Context

- The gate (`docs/codebase/notes/claude-pr-review.md`, ADR-0171) had two tiers, decided by changed lines and critical
  paths only: light (`>= 150` lines, `>= 8` files or any critical path) and full (`>= 800` lines, or a critical path
  with `>= 300` lines). Anything smaller was not reviewed.
- Line counts say little about how far-reaching a change is: renaming a pipeline module or adding a config overlay can
  be a few lines and still change how the whole repository fits together, while a long new analysis script stays local.
- A temporary workflow step had run every tier on one model until 2026-10-31; it is inert since then and is removed here.

## Decision

1. **Three tiers** in `.github/claude-review.yml`: `light`, `full` and `deep`, each with its own model, turn limit and a
   hard per-run budget (`claude -p --max-budget-usd`; a test kept every tier at or below the owner's cap). Since
   2026-11-05 the budgets come from an organization secret and are not published (ADR-0181).
2. **Structure paths.** A new list `structure_paths` names the backbone of the repository: agent rules, base config and
   overlays, the pipeline machinery, the simulation setup, the shared eqasim code, the documentation gate, the test and
   run entry points, the workflows and this gate. Every structure path is also critical.
3. **Deep** when a structure file is added, deleted, renamed or copied (from `git diff --name-status -M`), when
   `>= 5` structure files or `>= 300` lines in them change, or when the change is large anywhere (`>= 1000` lines or
   `>= 25` files). Adding the force label also asks for a deep review: whoever adds it wants the most thorough one.
4. **Full** for any other change to a critical path, `>= 150` lines or `>= 8` files (the former light thresholds).
5. **Light** for every other change with a counted line or file; only a change of ignored files alone is skipped.
6. **Further rounds** use the same rules on the interdiff; a structure change there is one that appeared since the last
   complete round (`interdiff` records it), so a file added in round 1 does not make every later round deep.
7. The review job may run 45 minutes instead of 30: deep runs of up to 19 minutes were seen, and a job cut off by the
   timeout loses its cost record.

All thresholds are ASSUMPTIONS chosen on 2026-11-02, to be re-tuned from the run records and the cost in the
Anthropic Console (since 2026-11-05 no run record carries a cost, ADR-0181).

## Why this is not a silent fallback

- Every decision names its rule in the gate log and the job summary, e.g. `deep: structure changed: added
  synthesis/population/x.py` or `full: critical path touched`.
- Without the name-status file the gate still decides by size and paths, but the workflow always passes it and a test
  checks that it does.

## Rejected options

- **A cheap model as triage** (reads the file list and the PR description, picks the tier): smarter in principle, but
  it costs a call per push, decides differently on the same input and cannot be tested against the committed config.
- **Deep for every critical path:** the critical list is broad (`braunschweig/**`); most PRs would go deep.
- **Structure paths per topic package** (e.g. every stage folder): adding a stage file is routine work here and would
  send most feature PRs to the deep tier.
- **No review for small changes:** the owner wants every change looked at; the light tier is cheap and capped.
