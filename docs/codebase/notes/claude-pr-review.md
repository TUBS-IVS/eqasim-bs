# Automatic Claude PR review

## What it is

This repository runs the advisory Claude PR review that TUBS-IVS shares in one public repository,
[TUBS-IVS/claude-pr-review](https://github.com/TUBS-IVS/claude-pr-review) (why central:
[ADR-0176](../../decisions/ADR-0176-the-claude-pr-review-lives-in-one-public-repository-called-at-a-version-tag.md)).
It posts one PR review with inline comments and keeps one summary comment per PR up to date. It is **not** a
required check: it never blocks a merge and never replaces the
[verification gate](../TESTING.md#required-agent-verification-gate). Nothing here touches model code or scientific
output.

The mechanism lives there, with its tests and the one description of how it works
([docs/how-it-works.md](https://github.com/TUBS-IVS/claude-pr-review/blob/main/docs/how-it-works.md)): the
model-free gate and its decision order, the three tiers, the rounds and their state block, the posting, the cost
record, the findings record for the session retro, and why it runs `claude -p` instead of the Claude Code GitHub
Action. The three tiers: [ADR-0175](../../decisions/ADR-0175-the-claude-pr-review-sends-structure-changes-and-large-changes-to-a-deep-tier.md);
the rounds: [ADR-0171](../../decisions/ADR-0171-the-claude-pr-review-reviews-in-rounds-verifies-fixes-and-stops-after-three.md).

## What this repository owns

| File | Holds |
|---|---|
| `.github/workflows/claude-review.yml` | The thin caller: the triggers, the concurrency block, the token permissions, and the call of `TUBS-IVS/claude-pr-review/.github/workflows/review.yml@v1` with only the secret `ANTHROPIC_API_KEY`. `on.pull_request.paths-ignore: ["docs/generated/**"]`: the bot PRs of the generated views (ADR-0168) create no run. |
| `.github/claude-review.yml` | The gate config: critical, structure and ignored paths, thresholds, rounds, tiers (model, turns, budget per run) and the two label names. |
| `.github/claude-review-prompt.md` | The prompt: read `CLAUDE.md` and every file in `.claude/rules/`, then check the diff against the PR template's "What & why", "Behaviour change?" and "Validation evidence". |
| `tests/test_claude_review_setup.py` | Checks the caller (a released major tag, the one secret, the permissions, `paths-ignore`, no label names) and the prompt's intent check. |

The gate config and the prompt are read from the PR itself, so a PR can change its own gate; both are critical
paths, which makes such a PR reviewed (the script is not read from the PR any more). So are `CLAUDE.md` and `.claude/rules/` (since
2026-10-28): they steer every agent session, the push rule and the verification gate included, so even a small edit
there is reviewed. `structure_paths` (what it covers: the comment above it in `.github/claude-review.yml`) is a short
list on purpose, so that adding a stage file in a topic package stays a full-tier change.

Prerequisite: the repository secret `ANTHROPIC_API_KEY`, a key of a **workspace** of the Console organization.
PRs from forks and from Dependabot get no Actions secrets and are skipped with a note in the job summary.

## Answer each finding

The maintainer's session retro (`~/.claude/retro`, outside this repository) learns from the findings an author
confirmed, and reads the outcome from the replies in each finding's review thread:

- **`Fixed ...`** (e.g. `Fixed in <sha>: <what changed>`) when the finding was right and is fixed;
- **`Rejected: <reason>`** (or `Not changed: ...`) when it is not a defect.

What the retro does with the replies, and what a reply-less finding counts as: "Learning from the findings" in
[docs/how-it-works.md](https://github.com/TUBS-IVS/claude-pr-review/blob/main/docs/how-it-works.md). With the desktop
app's Auto-fix on for a PR, the session fixes the reported findings and pushes the fix without a separate yes; the
scope of that standing exception is in `CLAUDE.md` ("Git and version control").

## Rules for maintainers

- Change thresholds, paths, limits, tools and labels in `.github/claude-review.yml` only. Its values are starting
  assumptions (each block names the date it was chosen); re-tune them from the cost records. The gate job checks the
  config and the prompt on every PR (`check-config`) and fails, before anything is spent, with one error line per
  problem.
- The label names live in the config only, never in the workflow (`tests/test_claude_review_setup.py` checks this).
- Change the mechanism (script, reusable workflow) in TUBS-IVS/claude-pr-review, never here; a release reaches this
  repository through `@v1` without a change here. To try an unreleased change, point the caller at a branch of the
  central repository on a test branch here ("Test a change before release" in its README); the setup test fails such
  a caller, so it cannot be merged by accident.
- Keep the review advisory until its findings have been checked against real PRs; making it a required check is a
  separate decision.
