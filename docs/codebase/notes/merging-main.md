# Merging `origin/main` (`scripts/sync_main.py`, `git sync-main`)

Maintenance rules for bringing `main` into a branch and for the CI reuse of a green regression result. Why
both exist, the measured conflict counts and the rejected options are in ADR-0165; the verification duties
are in [TESTING.md](../TESTING.md#required-agent-verification-gate). Nothing here repeats those.

## Setup, once per clone

The config lives in the shared `.git/config`, so one setup covers every worktree of the clone:

```bash
git config alias.sync-main '!python scripts/sync_main.py'
```

The first run of `scripts/sync_main.py` registers the merge driver `generated-view` itself. Run the alias with
the project environment active (`documentation build` needs `pyyaml`; the script uses its own interpreter).

## Use

| Situation | Command |
|---|---|
| bring `main` in before `git pr`, or when GitHub reports a conflict | `git sync-main` |
| it stopped with "conflicts outside the generated views" (exit 2) | resolve, `git add <files>`, `python scripts/sync_main.py --finish` |
| the DAG check is too slow or synpp is missing | add `--no-dag` (CI's `docs.yml` uses it too) |

It refuses a working tree with uncommitted tracked changes and a merge already in progress. It never pushes.
At the end it says whether the merge changed the regression inputs (CI then runs the full suite) or not.

## Rules

- **Never hand-merge `docs/generated/*.md`.** Without the driver (a clone without the setup, or GitHub's web
  merge) they conflict as before: rebuild with `python -m braunschweig.documentation build`, never pick a side.
- **The kept side is temporary.** The driver keeps the branch's view only until the rebuild; check
  `D1-generated` fails for any view that was not rebuilt, so `documentation check` must show 0 FAIL after a merge.
- **The views are LF everywhere** (`.gitattributes`). A clone that still has CRLF copies from before the rule
  shows them as modified once; `documentation build` (or `git checkout -- docs/generated`) clears it.

## CI reuse of a green regression result

- `scripts/regression_input_key.py` prints the key of a commit; `tests.yml` looks it up in the Actions cache
  before installing the environment and stores it after a green run. A hit prints a notice and skips the suite;
  the job stays green.
- **Changing `REGRESSION_EXCLUDED_PREFIXES`** needs an ADR. Before adding a prefix, prove that no regression-only
  test reads it; `tests/test_regression_input_key.py` holds the guards and their reviewed exceptions
  (`NOT_A_READ`, `IMPORTS_DOCUMENTATION_WITHOUT_READING`).
- **A new test that reads `docs/generated/` or `docs/decisions/`** belongs in `docs.yml`'s pytest list, or the
  guard fails.
- **Force a full run** with the workflow's `workflow_dispatch` and `full_run: true`, e.g. after a runner-image or
  conda-channel change, which the key does not cover.
