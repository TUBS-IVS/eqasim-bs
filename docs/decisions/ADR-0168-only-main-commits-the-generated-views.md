# ADR-0168 · 2026-10-23 · Only main commits the generated views; pull requests leave them at main's state

- **Status:** accepted (implemented on `chore/generated-views-no-github-conflicts`; the GitHub dry run is recorded in the pull request)
- **Numbering:** 0168 checked free across all local and remote branches and all worktrees on 2026-10-23
  (ADR-0166 and ADR-0167 are taken by open work in other worktrees).
- **Amends:** ADR-0165 (generated views merge by rebuild): the local merge driver and `git sync-main` stay, but a
  branch no longer rebuilds and commits the views; `sync_main.py` sets them to main's state instead. It also amends
  ADR-0077 in one point: the views are committed by main's workflow, not by each pull request. The rest of both stands.
- **Owner decision (2026-10-23):** design (a) with the bot pull request ("Ja, setz Design (a) mit dem Bot-PR-Workflow um").

## Context

- ADR-0165 made local merges of `origin/main` clean with the merge driver `generated-view`. GitHub's mergeability
  check and merge button do not run custom merge drivers (a driver is defined in the clone's git config, which GitHub
  does not have). On 2026-10-23, with the driver on `main`, PR #503 and PR #504 were both CONFLICTING on GitHub; the
  only files both changed against `main` were `docs/generated/{STATUS,FEATURES,DECISIONS,RUNS}.md`. PR #501 had
  turned CONFLICTING the same way right after PR #500 merged, although its local merge was clean.
- Every sibling merge therefore cost every other open pull request one more `git sync-main` + push round.

## Decision

1. **Pull requests do not change `docs/generated/`.** `scripts/check_generated_views_untouched.py` (step in
   `docs.yml` on every pull request, except the bot's own branch) fails when the pull request changes a view against its
   merge base. A branch that does not change a view has no diff there, so neither git nor GitHub can conflict there.
2. **Main rebuilds the views.** `.github/workflows/generated-views.yml` runs on every push to `main`: it rebuilds the
   views, runs `documentation check --strict-views` and, when a view changed, force-pushes the rebuild to
   `bot/generated-views`, opens a pull request and merges it at once with the workflow token
   (`--match-head-commit`). The main ruleset requires a pull request with 0 approvals, so a direct bot push would be
   refused; the repository allows Actions to create pull requests.
3. **Check D1-generated** renders every view as before, but a stale view is WARN by default (the expected state on a
   branch) and FAIL only with `--strict-views`. A missing view still FAILs.
4. **`git sync-main`** merges `origin/main` and sets the views to the merged main's state (`git restore
   --source=MERGE_HEAD`); when the branch is already up to date it still resets views the branch changed itself (a
   branch from before this ADR). The merge driver stays so that such branches merge without conflict markers.
5. Local `documentation build` stays the way to read fresh views on a branch; the rebuilt files are never committed.

## Why this is not a silent fallback

- The guard makes a committed view a CI failure with the fix command, not a tolerated drift.
- A stale view on a branch is reported as WARN on every `documentation check`; on main the workflow's strict check
  fails before anything lands, and a failed bot merge turns the workflow run red. Between a merge and the bot merge
  (about one to two minutes) main's views are stale; that is visible as D1 WARN in `docs.yml` on main.

## Rejected options

- **`merge=union` in `.gitattributes`:** built-in, but it duplicates the counter line and table rows (already rejected
  in ADR-0165); whether GitHub applies it is not documented, and the only public report found (2016) says it does not.
- **Bot pushes directly to `main`:** refused by the ruleset (pull request required); a bypass needs a ruleset or
  deploy-key change, a security setting that stays the owner's.
- **(b) Do not commit the views at all** (build on demand / CI artifact): about thirty relative links in `README.md`,
  `docs/ONBOARDING.md`, `docs/MODEL_OVERVIEW.md`, `AGENTS.md` and the codebase overviews would dangle on GitHub.
- **(c) One generated file per record:** the views are aggregations (the counter line of `STATUS.md`, sorted tables);
  shared lines remain wherever records are summed up or ordered.

## Consequences

- Sibling pull requests that each add an ADR, a run manifest or a registry record no longer conflict on GitHub; only
  real conflicts in sources remain.
- A pull request's diff never shows the views; reviewers read the registries and records it changes.
- Open pull requests from before this ADR fail the guard once; `git sync-main` + push fixes them.
- Pushes by the workflow token start no workflow, so the bot merge does not re-run this workflow, `docs.yml` or the
  regression suite (its key excludes `docs/generated/` anyway, ADR-0165).
- No model output changes.
