# ADR-0176 · 2026-11-04 · The Claude PR review lives in one public repository that every repository calls at a version tag

- **Status:** accepted (implemented in `TUBS-IVS/claude-pr-review` v1.0.0 and on `chore/claude-review-central-repo`);
  amended by ADR-0181 (no cost in any output, the budgets from a second secret)
- **Numbering:** 0176 checked free across all local and remote branches and all issues on 2026-11-04.
- **Origin (2026-11-04):** after one bug fix of the review script had to be made six times, the owner asked whether
  one central, referenced project for the review makes sense; this record is the design. The owner chose to do it
  without a GitHub issue.

## Context

- The automatic Claude PR review (`docs/codebase/notes/claude-pr-review.md`, ADR-0171, ADR-0175) ran in six
  repositories: here (`scripts/claude_review.py`, its tests `tests/test_claude_review.py`) and as a copy at
  `.github/scripts/claude_review.py` in `TUBS-IVS/eqasim-java-bs` (public) and in four private repositories of the
  group (not named here, as before). Each copy's header said: change it in eqasim-bs first, then copy it everywhere.
- The comment-id fix (`match_comment_ids` keyed on a `line` field that GitHub's listing of a review's comments does not
  return, so every findings record carried `comment_id` None) therefore needed six branches, one per repository
  (here: `fix/claude-review-comment-ids`).
- Only this repository tested the script and its gate config; the five copies ran a config and a prompt no test had
  read. Measured on 2026-11-04 by comparing each default branch's file with `gh api
  repos/TUBS-IVS/<repo>/contents/<path>`: the code of all five copies equalled this repository's `main` as of PR #536
  apart from a header comment, and the five workflow files differed only in comments and, for one of them, the base
  branch (`master`).
- The maintainer's session retro (`~/.claude/retro`, `retro_reviews.py`, outside this repository) finds the review runs
  of each repository by the workflow file name `claude-review.yml` and downloads the artifacts
  `claude-review-findings-*`; it reads the findings record (`schema: 2`) and the state block of the summary comment.

## Decision

1. **One public repository, `TUBS-IVS/claude-pr-review`,** holds the script `claude_review.py`, its tests with their
   own CI, and the description of the mechanism. It starts from this repository's script with the comment-id fix.
   It must be public: a private repository's workflows cannot be called from a public repository, and two of the six
   (this one and eqasim-java-bs) are public. The script was already public here; the gate configs and prompts of the
   private repositories stay in those repositories.
2. **A reusable workflow** (`.github/workflows/review.yml`, `on: workflow_call`) holds the two jobs of the review
   (model-free `classify`, then `review`) as they were. Both jobs fetch the script from the repository and commit of
   that workflow file (`job.workflow_repository`, `job.workflow_sha`) into the runner's temp directory, outside the
   reviewed checkout. Script and workflow therefore always come from the same version, whether a caller names `v1`, an
   exact tag or a test branch.
3. **Versions.** Releases are tagged `v1.0.0`, `v1.0.1`, ...; the major tag `v1` moves to the newest `v1.x.y`. Callers
   name `@v1`, so a fix released once reaches every repository at its next pull-request event. A change that breaks
   the config keys, the caller's interface or the findings record gets a new major version, which each repository
   adopts deliberately.
4. **Each repository keeps a thin caller** at the same path `.github/workflows/claude-review.yml`: its triggers (base
   branch, `paths-ignore`), the concurrency block unchanged, the token permissions, and one job that calls
   `TUBS-IVS/claude-pr-review/.github/workflows/review.yml@v1` and passes only the secret `ANTHROPIC_API_KEY` (since
   ADR-0181 also `CLAUDE_REVIEW_BUDGETS`). It
   also keeps its own gate config `.github/claude-review.yml`, prompt `.github/claude-review-prompt.md` and
   `.claude/rules/`. The script path leaves the configs' critical and structure paths; the caller stays both through
   `.github/workflows/**`.
5. **Unchanged contract:** the caller's file name, the artifact names `claude-review-findings-pr<NN>-run<id>-<attempt>`
   and `claude-review-cost-...`, the findings record `schema: 2`, the state marker of the summary comment, the bot's
   reply words and the label names (in the config only). Nothing in the gate rules, tiers, models, budgets or prompts
   changes.
6. **The gate checks every caller's config.** A new model-free command, `check-config`, runs first in the gate job and
   fails it, before anything is spent, when the config or prompt would make a broken or unbounded run: a missing tier,
   a budget above the owner's per-run cap (ADR-0175; ADR-0181 replaced this check: since then a tier without a
   budget, or a budget left in the config while the secret is set, fails it, and the code holds no upper bound), a
   tool beyond Read/Grep/Glob, an unknown prompt
   placeholder. It replaces the test that checked only this repository's committed config, and covers all six.
7. **Migration order:** eqasim-java-bs first, with a green review run on its migration pull request, then the four
   private repositories and this one. This repository keeps a small test of its own caller (pinned major tag, explicit
   secret, `paths-ignore` of the generated views); the 79 script tests move to the central repository.

## Why this is not a silent fallback

- When `job.workflow_repository` or `job.workflow_sha` is empty, the job fails with an error. It never falls back to
  `main` or to `v1`, which would run a different script than the workflow's version without saying so.
- Both jobs print the repository and commit of the script they run to the log and the job summary, so every review
  run names the exact code that produced it.
- A config or prompt that fails `check-config` fails the gate job with one error line per problem; no review runs.

## Security consequences

- A pull request can no longer change the script that reviews it (before, the script was read from the pull request
  itself). Config and prompt are still read from the pull request and stay critical paths.
- Only `ANTHROPIC_API_KEY` reaches the central code (since ADR-0181 also the budgets secret `CLAUDE_REVIEW_BUDGETS`,
  which is no credential). `secrets: inherit` would also hand it every other secret of the calling repository (one of
  the private repositories holds a token for its submodules).
- Whoever can move the `v1` tag changes the code that runs with the API key and with permission to comment in six
  repositories. A ruleset on the central repository lets only administrators create, move or delete `v*` tags
  ("Release tags: administrators only", set on 2026-11-04 with the owner's yes).

## Evidence

- First run of the central workflow: the migration pull request of eqasim-java-bs (TUBS-IVS/eqasim-java-bs #56, run
  41803438108, 2026-11-04). The gate fetched `TUBS-IVS/claude-pr-review@40fe49cb08f0` (so `job.workflow_sha`
  resolves in a called workflow), `check-config` passed, the gate chose the deep tier (1,703 changed lines), and the
  review (its cost is not published since 2026-11-05, the owner's decision) posted one inline finding whose comment id
  was matched (1/1; before the fix no record carried one) and uploaded both artifacts under their unchanged names;
  `gh run list --workflow claude-review.yml` lists the run as the retro expects. That finding argued for pinning a commit instead of `v1`, the rejected option below; its
  thread was answered with that reason.
- `check-config` passed on the gate configs and prompts of all six repositories before their migration.

## Rejected options

- **Keep the copies and sync them with a script or a bot:** still one pull request per repository and fix, and the
  copies can still drift.
- **A private central repository:** its workflows cannot be called from the two public repositories.
- **A composite action instead of a reusable workflow:** the job skeleton (two jobs, outputs, permissions, timeouts,
  artifact uploads) would stay copied in six repositories, and that skeleton changed in #521 and #536.
- **Pin each caller to an exact tag or commit:** the safest pin, but every fix would again need a pull request in each
  repository; the moving major tag plus the tag ruleset is the chosen trade-off.
- **A reusable workflow that fetches the script at a hard-coded `@v1`:** a caller pinned to an exact version or testing
  a branch would silently run another version of the script.
- **Concurrency in the called workflow:** GitHub documents that a called workflow sees the caller's `github` context
  and that a group shared by caller and called workflow deadlocks; the caller keeps today's block, so queueing and
  cancelling behave exactly as before.
- **Install the script as a Python package in each repository:** a second release channel next to the workflow, and
  this repository's conda environment would carry a dependency for a CI tool.
