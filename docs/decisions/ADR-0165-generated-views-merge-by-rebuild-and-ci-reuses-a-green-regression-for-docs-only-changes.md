# ADR-0165 · 2026-10-23 · Generated views merge by rebuild, and CI reuses a green regression result for documentation-only changes

- **Status:** accepted (implemented on `chore/merge-generated-views`)
- **Numbering:** 0165 checked free across all local and remote branches on 2026-10-23 (highest found: ADR-0164).
- **Amends:** ADR-0150 (where the final regression evidence comes from): a green regression result now also
  covers a later commit whose regression inputs are identical. The rest of ADR-0150 stands.
- **Owner decision (2026-10-23):** all three proposed points accepted ("alle drei punkte"): the merge driver
  with LF views, the one-step `git sync-main`, and the relaxed re-run for documentation-only changes.

## Context

- The eight views `docs/generated/*.md` are committed aggregations of the per-record registries
  (ADR-0077). Two branches that each add a record change the same lines: the counter line of `STATUS.md` and
  the last row of `DECISIONS.md`.
- Replaying the 55 merge commits with conflicts since 2026-08-01 (`git merge-tree` on both parents,
  2026-10-23): `docs/generated/STATUS.md` conflicted in 43, `DECISIONS.md` in 28, `RUNS.md` in 10, and in
  about 45 of the 55 merges the generated views were the ONLY conflicting files. None of these conflicts was a
  real disagreement; the resolution was always `documentation build`.
- `documentation build` writes LF; Windows clones with `core.autocrlf=true` check the views out with CRLF, so
  every rebuild also showed line-ending-only changes in views whose content was unchanged.
- After every merge of `origin/main` the full regression suite re-ran on both CI platforms (ADR-0150, TESTING.md
  step 6), also when the only difference to an already green commit was a generated view or an ADR text.

## Decision

1. **Merge driver.** `.gitattributes` assigns `docs/generated/*.md` the merge driver `generated-view`
   (`driver = true`: keep the current branch's side, no conflict markers) and `eol=lf`.
2. **One step.** `scripts/sync_main.py` (alias `git sync-main`) registers the driver in the clone's config,
   merges `origin/main` without committing, rebuilds all views from the merged registries, stages only the views
   whose content changed, commits the merge and runs `documentation check`. A conflict outside the views stops
   with the file list (exit 2); `--finish` completes the merge after the manual resolution.
3. **Reuse of a green regression result.** `scripts/regression_input_key.py` hashes `git ls-tree -r HEAD` without
   the paths under `docs/generated/` and `docs/decisions/`. The `regression` workflow stores a marker in the
   GitHub Actions cache under that key after a green run and skips the suite (job still green, with a notice) for
   a later commit with the same key on the same OS. `workflow_dispatch` with `full_run: true` forces a run.

## Why the kept side is not a silent fallback

The driver's kept side is never the final state: the rebuild replaces it, and check `D1-generated` fails locally
and in the `docs.yml` CI job for any view that differs from a fresh render. A merge done without
`sync_main.py` therefore cannot land a stale view unnoticed.

## Why the reuse is sound

- Only `docs/generated/` and `docs/decisions/` are excluded. A guard test (`tests/test_regression_input_key.py`)
  fails when a test outside the documentation tests run by `docs.yml` names one of these paths, and when a new test
  imports `braunschweig.documentation` without being reviewed. The documentation tests that do read them
  (`test_documentation_{schema,registry,checks}.py`) run in `docs.yml` on every push.
- Run manifests (`docs/runs/`), registries, notes and every other path stay in the key: regression tests read them.
- The key is computed from the commit (blob ids), so it is independent of line-ending conversion and the same on
  Linux and Windows; the cache key still carries the OS, so each platform needs its own green run.

## Rejected options

- **Hand-merging the views** (status quo): pure busywork, and a hand merge can keep one side's counter.
- **A driver that rebuilds inside git's merge:** the driver sees one file at a time, before the registries of the
  merge are complete; the rebuild must run on the merged tree.
- **`merge=union`:** concatenates both sides; the counter line then appears twice and table rows can duplicate.
- **Stop committing the views and build them in CI:** removes the conflict entirely, also for GitHub's own merge
  button, but the views would no longer be readable on GitHub for a branch; this is a larger change of ADR-0077
  and stays open.
- **Skipping the regression run by changed-path filters (`paths-ignore`):** a pull request's diff to `main`
  contains the branch's code, so a docs-only follow-up commit would still run; the key compares whole trees.
- **Excluding all of `docs/`:** regression tests read run manifests, registries and notes.

## Consequences

- A merge of `origin/main` is one command; the views no longer need manual handling.
- A docs-only follow-up commit (a run manifest is NOT docs-only in this sense; an ADR wording fix or a view
  rebuild is) and a merge of `main` that brought only ADRs and views reuse the green regression result.
- **Limits:** GitHub's merge button does not know local merge drivers; when `main` moved after the last sync,
  GitHub still reports a conflict and `git sync-main` + push is needed. The key does not cover external drift
  (runner image, conda channels); the next run with changed inputs or a `full_run` dispatch catches it. Caches
  unused for 7 days are evicted by GitHub, which only causes a normal full run.
- No model output changes.
