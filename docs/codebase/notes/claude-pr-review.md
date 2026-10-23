# Automatic Claude PR review

## What it is

`.github/workflows/claude-review.yml` runs an advisory code review by Claude on pull
requests to `main`. It posts inline comments and one summary comment that starts with
"Claude review (<tier> tier)". It is **not** a required check: it never blocks a merge
and never replaces the [verification gate](../TESTING.md#required-agent-verification-gate).
Nothing here touches model code or scientific output.

Prerequisite: the repository secret `ANTHROPIC_API_KEY`. Comments are posted with the
workflow's `GITHUB_TOKEN` (as `github-actions[bot]`), so no GitHub App is needed. PRs
from forks and from Dependabot get no Actions secrets and are skipped with a note in the
job summary. The gate config and script are read from the PR itself, so a PR can change
its own gate; both are critical paths, which makes such a PR reviewed.

## The gate: two jobs, the first without a model

| Job | Calls the API | Does |
|---|---|---|
| `classify` | no | `scripts/claude_review.py classify` reads `git diff --numstat base...head`, the branch kind, labels and draft state, and decides `none` / `light` / `full` |
| `review` | yes, only if `classify` says so | `anthropics/claude-code-action` (pinned by commit) with the tier's model and turn limit, then `claude_review.py cost` |

The thresholds, paths, labels and models live in `.github/claude-review.yml`, not in
code. Its values are starting assumptions (2026-10-23), not measured; re-tune them from
the cost records below. Decision order (`classify` in `scripts/claude_review.py`):

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
Each review edits its previous summary comment (`gh pr comment --edit-last`) instead of
adding a new one; inline comments accumulate.

Changed lines are added + deleted lines of files outside `ignored_paths` (generated
views, lock files, test baselines); binary files count as a file with 0 lines. Every
decision, including a skip, is printed with its reason to the log and the job summary.

## Cost record

After every review, `claude_review.py cost` reads the `result` message of the Claude
Code execution file (`$RUNNER_TEMP/claude-execution-output.json`, written by the action)
and reports `total_cost_usd`, input/output/cache tokens, turns and duration **as the API
reported them**. It writes a table to the job summary and uploads
`claude-review-cost.json` as an artifact (90 days). A missing record fails the step
instead of reporting a zero. A run that ended early (`subtype` other than `success`,
e.g. `error_max_turns`) is marked partial with a workflow warning: its cost is real,
its review incomplete.

To compare tiers, collect the artifacts of several runs, e.g.
`gh run download <run-id> -n claude-review-cost-pr<NN>-run<run-id>-1`.

## Rules for maintainers

- Change thresholds or paths in `.github/claude-review.yml` only; the decision table in
  `tests/test_claude_review.py` runs against that committed file, so an edit that breaks
  the intended gate shows up as a failing case to update deliberately.
- Keep the review advisory until its findings have been checked against real PRs; making
  it a required check is a separate decision.
- Bump the pinned action commit deliberately and note the version in the workflow comment.
