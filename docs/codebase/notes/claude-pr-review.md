# Automatic Claude PR review

## What it is

`.github/workflows/claude-review.yml` runs an advisory code review by Claude on pull
requests to `main`. It posts one PR review with inline comments and keeps one summary
comment per PR up to date. It is **not** a required check: it never blocks a merge and
never replaces the [verification gate](../TESTING.md#required-agent-verification-gate).
Nothing here touches model code or scientific output. `TUBS-IVS/eqasim-java-bs` runs the
same mechanism with its own config and prompt and a copy of the script
(`.github/scripts/claude_review.py` there; change it here first, then copy it).

Prerequisite: the repository secret `ANTHROPIC_API_KEY`, a key of a **workspace** of the
Console organization (an organization-level key fails with "not scoped to a workspace").
Comments are posted with the workflow's `GITHUB_TOKEN` (as `github-actions[bot]`). PRs
from forks and from Dependabot get no Actions secrets and are skipped with a note in the
job summary. The gate config, prompt and script are read from the PR itself, so a PR can
change its own gate; all three are critical paths, which makes such a PR reviewed.

## Why `claude -p` and not `anthropics/claude-code-action`

The organization's API balance consists of the monthly API credits of a Claude Team plan.
Those credits cover the Claude API, the Agent SDK and `claude -p` run with an API key of
the linked Console organization, but not Claude Code; runs started by the Claude Code
GitHub Action count as Claude Code usage (Claude Help Center, "Monthly API credits for Max
and Team plans", article 17154008; Claude Platform docs, "API credits for Max and Team
plans"). The action identifies its traffic as such (`CLAUDE_CODE_ENTRYPOINT =
"claude-code-github-action"` in its `base-action/src/parse-sdk-options.ts`). On PR #497
every action run failed on its first API call with "Credit balance is too low" at 0 USD,
although the organization held 420 USD of these credits. The workflow therefore runs
`claude -p` itself. Should the balance ever be purchased credits instead, the action would
work again; the reasons above then no longer apply.

## The gate: two jobs, the first without a model

| Job | Calls the API | Does |
|---|---|---|
| `classify` | no | `scripts/claude_review.py classify` reads `git diff --numstat base...head`, the branch kind, labels and draft state, and decides `none` / `light` / `full` |
| `review` | yes, only if `classify` says so | installs the pinned Claude Code CLI, `claude_review.py review` (runs `claude -p`), `cost`, `post` |

The thresholds, paths, labels, models, turn and budget limits live in
`.github/claude-review.yml`, not in code. Its values are starting assumptions
(2026-10-23), not measured; re-tune them from the cost records below. Decision order
(`classify` in `scripts/claude_review.py`):

1. Label `skip-claude-review` -> no review.
2. Adding the label `claude-review` -> one full-tier review, also on drafts. Later pushes
   are gated normally, so a label left on the PR cannot bill every push.
3. A `labeled` event for any other label -> no review (otherwise adding `automerge`
   would start a paid review).
4. Draft PR -> no review.
5. Branch kind in `skip_kinds` (`docs/`) without a critical path -> no review.
6. Changed lines >= `full_min_lines`, or a critical path and >= `full_min_lines_critical`
   -> full tier.
7. A critical path, or >= `light_min_lines` lines, or >= `light_min_files` files -> light.
8. Otherwise no review.

Runs queue per PR and a running review is never cancelled: its cost is already spent
and is recorded only at the end. A newer push replaces a still-pending run. Label events
for other labels run in their own concurrency group, so they cannot displace a review.

Changed lines are added + deleted lines of files outside `ignored_paths` (generated
views, lock files, test baselines); binary files count as a file with 0 lines. Every
decision, including a skip, is printed with its reason to the log and the job summary.

## The review run and the posting

- `review` fills the placeholders of `.github/claude-review-prompt.md` and runs `claude -p`
  with the tier's `model`, `max_turns` and `max_budget_usd` (a hard cap per run), the JSON
  schema `FINDINGS_SCHEMA`, `--permission-mode dontAsk` and only the read tools of
  `review.allowed_tools` (Read, Grep, Glob, `gh pr diff`, `gh pr view`). The model cannot
  write files or post anything. The prompt goes in on stdin; the JSON result goes to a file.
- `post` turns the findings into one PR review (`event: COMMENT`) whose inline comments sit
  on new-side lines of the diff (`commentable_lines`), ordered by severity. GitHub rejects
  inline comments outside the diff, so such findings go into the summary under "Not on a
  changed line"; the inline/summary-only counts are logged, and a warning fires when every
  finding lies outside the diff. The summary comment carries a hidden marker and is edited in
  place on later runs; only a comment by `github-actions[bot]` with that marker is edited. A
  failed run (`is_error`) posts nothing and fails the step with the API's error text.
- `post --dry-run` prints both payloads and posts nothing, for local tests.

## Cost record

After every review, `claude_review.py cost` reads the `claude -p` JSON result and reports
`total_cost_usd`, input/output/cache tokens, turns and duration **as the CLI reported
them**. It writes a table to the job summary and uploads `claude-review-cost.json` as an
artifact (90 days). A missing record fails the step instead of reporting a zero. A run
that ended early (`subtype` other than `success`, e.g. `error_max_turns`, or `is_error`) is
marked as such with a workflow warning, and the error text of a failed run is printed:
its cost is real, its review incomplete.

To compare tiers, collect the artifacts of several runs, e.g.
`gh run download <run-id> -n claude-review-cost-pr<NN>-run<run-id>-1`.

## Rules for maintainers

- Change thresholds, paths, limits or tools in `.github/claude-review.yml` only; the
  decision table and the bounded-run test in `tests/test_claude_review.py` run against that
  committed file, so an edit that breaks the intended gate shows up as a failing case.
- Keep the review advisory until its findings have been checked against real PRs; making
  it a required check is a separate decision.
- Bump the pinned CLI version (`CLAUDE_CODE_VERSION` in the workflow) deliberately.
- Keep the model read-only: posting belongs in `post`, not in the model's tools.
