# ADR-0150 · 2026-10-22 · GitHub CI is the full regression gate; local runs are focused

- **Status:** accepted (maintainer decision in chat, 2026-10-22). Amends ADR-0123 decision 6
  (final-code evidence) and step 3 of the
  [required verification gate](../codebase/TESTING.md#required-agent-verification-gate).
- **Numbering:** ADR-0150 is the next free id. Checked on 2026-10-22 across all local and
  remote branches: the highest record anywhere is ADR-0149.
- **Issue:** none (process change decided by the maintainer; branch `chore/ci-is-the-test-gate`).
- **Relates to:** ADR-0123 (reproducible test environments), the push gate described in
  `docs/codebase/notes/agent-process-automation.md`.

## Context

Until now the gate required a full local regression run on the final code state in
Linux/WSL2 before every push or PR, after merging `origin/main`. The CI workflow
(`.github/workflows/tests.yml`, job `regression`) then ran the same suite again on
`ubuntu-24.04` and `windows-latest` before merge. Agents also ran the full suite on
native Windows locally, although the gate never required it.

- **The local Linux run duplicates the CI Linux job.** The CI Linux job installs the same
  captured server snapshot (`environments/server-linux-64.lock` plus
  `environments/requirements-server-linux-64.txt`) that the WSL mirror reproduces, and runs
  the identical command `python scripts/run_tests.py -q --durations=30 --junitxml=test-results.xml`.
- **It blocks the development machine.** On 2026-10-22 an agent session ran four full
  suites in parallel (two branches, each on Windows and WSL) after a merge of `origin/main`.
  The agent estimated 1.5 to 3 hours for them; that duration was not measured. The only
  recorded serial duration is 6:53 min on native Windows (TESTING.md, 2026-09-25).
  Parallel suites on one machine compete for CPU and memory, so their wall time is
  not comparable to a serial run.
- **CI runs on GitHub's machines,** both platforms in parallel, each job capped at
  30 minutes (`timeout-minutes: 30`).

## Decision

1. **The full regression suite runs in CI, not locally.** A PR's `regression` jobs (Linux
   and Windows) and the documentation check, green on the PR's latest commit, are the
   final-code evidence. A pending CI run is pending evidence, never a pass.
2. **Locally, agents run focused tests only:** `python scripts/run_tests.py --check` at
   session start, and the tests affected by the change through the same runner (paths,
   node ids or `-k`). A local full run is optional. It is only expected when CI cannot run,
   and then it must be reported as the substitute.
3. **Data-gated tests stay a local duty.** CI has no restricted input data (MiD 2023, SrV,
   KBA tables and others), so tests that declare such a dependency skip there
   ([test-data-dependencies](../codebase/notes/test-data-dependencies.md)). When a change
   touches code that such tests cover, run exactly those tests locally on a checkout that
   carries the data, and report their pass and skip counts. A git worktree carries no
   gitignored data unless it is linked in, so the former local full run in a worktree
   skipped these tests as well.
4. **The base must be current at merge time.** CI on a `pull_request` event tests the PR
   merged with `main` as of that event. Re-running a workflow does not pick up a newer
   `main`. If `main` has moved since the last CI run, merge `origin/main` into the branch
   and push again, so CI tests the current merge state. This keeps the lesson of #284 and
   #287 (each green alone, `main` red together).
5. **The local push gate no longer requires `tests-linux.json`.** It still requires the
   documentation-check evidence and a merged `origin/main`. The runner keeps writing test
   evidence, which the gate shows as information.

## Rejected options

- **Keep the local WSL run, drop only local Windows runs.** Smaller change, but it still
  blocks the development machine for a run that CI repeats with the same environment
  and command.
- **Make the local gate faster with `pytest-xdist`.** `pytest-xdist` is not part of the
  locked environments, and the Linux lock changes only with a new server snapshot.
- **CI only, no local tests at all.** Every red test would surface only after a push,
  which makes the edit-test loop slow. Focused local runs stay mandatory.

## Consequences

- Development machines stay free during the gate; both platforms run in parallel on CI.
- A red test can now surface after a push. That is harmless on a feature branch, because
  merging still requires green CI on the latest commit.
- Branch protection on `main` ("require status checks" and "require branches to be up to
  date") would enforce decisions 1 and 4 mechanically. It is a repository setting and
  remains the owner's call; it was not changed by this decision.
- The local push gate (`~/.claude/retro/bin/retro_gate.py`, personal tooling) was changed
  in the same step.
