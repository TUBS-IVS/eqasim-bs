# ADR-0181 · 2026-11-05 · The Claude PR review trims its run record and reads its tier limits from a secret

- **Status:** accepted (implemented in `TUBS-IVS/claude-pr-review` v1.3.0, its PRs #3 and #4, and here on
  `chore/claude-review-hide-cost`)
- **Numbering:** 0181 checked free across all local and remote branches and all issues on 2026-11-05.
- **Owner decision (2026-11-05):** nobody else is to see what the review costs, in any repository of the group; then
  the budgets per run too. Asked how to hide the budgets, the owner chose an organization secret that keeps the hard
  cap per run over dropping the cap.

## Context

- The review showed the API cost of every round in its summary comment (a cost column in the rounds table and the
  cost in the footer), kept it per round in the comment's hidden state block, and wrote it with the token counts to
  the log, the job summary and two artifacts (`claude-review-cost-*`, `claude-review-findings-*`). This repository and
  eqasim-java-bs are public, so all of these are public: the raw text of a comment, the log and job summary of a run
  and its artifacts can be read by anyone.
- The token counts give the cost at the public prices, so they are the cost as well.
- The budget per run of each tier (`claude -p --max-budget-usd`, ADR-0175) stood in every caller's public gate config
  and, as an upper bound, in the central code; the docs of both repositories and ADR-0171, ADR-0175 and ADR-0176
  quoted amounts.
- The round cap (ADR-0171) read the cost of each round from the state block: a round counted when it called the
  model, that is when its cost was above zero.

## Decision

1. **No output of a review shows its API cost** (TUBS-IVS/claude-pr-review v1.3.0; its `docs/how-it-works.md`, "Run
   record"). The summary comment has no cost column and gives the turns only. The state block records per round
   whether it called the model (`model_call`) instead of the amount, and drops the amount of a block written before.
   The run record (job summary, log line, artifact `claude-review-cost-*`, kept under its v1 name) gives status, tier,
   model, turns and duration. The findings record keeps the key `cost_usd`, always empty (schema 2 only adds fields).
2. **The budgets come from the organization secret `CLAUDE_REVIEW_BUDGETS`**, JSON with each tier's budget in USD,
   which each caller passes next to `ANTHROPIC_API_KEY`. No config, code, doc or output holds an amount. The gate
   (`check-config`) validates the secret without quoting a value and refuses a budget left in a config while the
   secret is set; only the gate and the review step get the secret, the model's own process does not. On GitHub
   Free an organization secret does not reach a private repository (GitHub Docs, "Using secrets in GitHub
   Actions"), so the four private repositories of the group hold a repository secret of the same name (set by the
   owner on 2026-11-05); this repository and eqasim-java-bs, both public, get the organization secret.
3. **A money amount in a text of the CLI** that the review publishes (the error of a failed run, the CLI's warnings in
   the log) is replaced by `[amount]`: a run cut off by its budget may name the budget.
4. **The docs drop the amounts they quoted;** ADR-0171, ADR-0175 and ADR-0176 say where one was removed.
5. **Where the cost is now:** the Anthropic Console, under the workspace of each repository's API key.

## Why this is not a silent fallback

- A caller that does not pass the secret yet still runs on the budgets of its config, so the cap holds, with a
  `::warning::` on every run; a tier without either budget fails the gate, so no run is unbounded.
- A state block written before v1.3.0 is read the same way (a cost above zero is a model call), so the round cap of an
  open PR still holds.

## Consequences

- Outputs from before keep their amounts: earlier revisions of summary comments (GitHub keeps the edit history of a
  comment), logs, job summaries and artifacts within their retention, and the git history of the docs. Deleting
  them is the owner's action.
- Tiers are re-tuned from the run records (status, turns) and the cost per workspace in the Console, no longer from
  a cost per run.
- The central code holds no upper bound for a budget any more: the secret, which only the owner sets, is the bound.
- Each repository's caller passes a second secret; a caller on an older version than v1.3.0 cannot pass it.

## Evidence

- `TUBS-IVS/claude-pr-review` v1.3.0 (tag on 98f80cd, the merge of its PR #4 after PR #3): its tests feed a result
  with a cost and made-up budgets through `check-config`, `review`, `outcome` and `post` and fail on any amount,
  token count or cost word in the public outputs; 132 tests pass on Linux and Windows.
- Test run from this repository before the release (README there, "Test a change before release"): TUBS-IVS/eqasim-bs
  #563, closed without merging, ran the central `main` at cf42116 at the full tier. The run completed (12 turns), took
  its budget from the secret, and its summary comment, state block, log and both artifacts held no amount.
- Here: `tests/test_claude_review_setup.py` (the caller passes both secrets; no budget in the gate config), and the
  central `check-config` passes this repository's config with the secret set.

## Rejected options

- **Drop the budget cap:** no secret needed, but a run would be bounded only by its turn limit and the job timeout; the
  owner chose to keep the cap.
- **Configuration variables (`vars`) instead of a secret:** GitHub's documentation of reusable workflows does not say
  whether a called workflow sees the caller's variables, and a variable is not masked in a log.
- **A repository secret for every caller:** six copies to keep equal; the organization secret holds the budgets once
  wherever GitHub lets it (the public repositories), and a repository secret stands in only where GitHub Free
  leaves no other way (the private ones).
- **Keep the budgets in the configs, as limits rather than spending:** the owner asked to remove them as well.
- **Drop the cost artifact and the key `cost_usd`:** that breaks the v1 contract with the session retro (artifact names,
  findings record fields only added), so it would be a new major version that each repository adopts in a PR of its
  own.
- **An encrypted per-run cost record in the artifacts:** more machinery than asked for; the Console has the cost.
