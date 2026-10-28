# ADR-0171 · 2026-10-28 · The Claude PR review reviews in rounds, verifies fixes and stops after three automatic rounds

- **Status:** accepted (implemented on `chore/i520-claude-review-rounds`, issue #520)
- **Numbering:** 0171 checked free across all local and remote branches on 2026-10-28.
- **Owner decision (2026-10-28):** the design was approved ("Ja, freigeben"). The success criterion stated the same
  day: bugs are found, they get fixed, the code has no errors, and we learn from it.

## Context

- The automatic review (`docs/codebase/notes/claude-pr-review.md`) reviewed every push as if it were the first: the
  full PR diff against `main`, at the full tier (Opus) whenever the PR is large or critical.
- Measured on 2026-10-23/24 from the cost artifacts `claude-review-cost-*` (20 records): PR #505 was reviewed 6 times
  (6.3 USD), PR #504 5 times, PR #512 4 times, all at the full tier; one run cost 0.4 to 1.8 USD. The records say
  what each run cost, not how many repeats found nothing new; that last part is not measured here.
- Since 2026-10-28 Auto-fix is the standing choice for every PR, so the loop finding -> fix push -> review is the
  normal case. Nothing checked whether a fix closed a finding, and the PR description (what it claims to change and to
  have validated) never reached the model.
- Bot PR #517 (generated views, ADR-0168) was created 12:57:14 and merged 12:57:17 UTC on 2026-10-24; its review run
  failed with no job.
- Source of the ideas: Anthropic's write-up of its own review process (claude.dev/blog/solving-code-review-at-anthropic,
  read 2026-10-28): diff-only re-review after the first pass, no repeated findings, a round cap, reading the PR's stated
  intent, tagging pre-existing findings, flagging the class of a bug.

## Decision

1. **State in the summary comment.** A hidden block in the bot's existing summary comment holds the rounds and
   findings (status `open` / `resolved` / `dismissed`). No artifact, branch or external store. `post` merges into it
   at write time.
2. **File-level interdiff.** A further round reviews only the file sections whose change against `main` differs from
   the last complete round (`git diff base...last` against `base...head`, normalised, so a merge of `main` changes
   nothing). A non-ancestor last commit falls back to the whole PR, logged; the round cap still applies, so a
   force-push after the last round cannot buy a fresh full review.
3. **Gate on further rounds.** Tier from the interdiff with the unchanged thresholds; at most
   `rereview.max_rounds` (3) automatic rounds that called the model, complete or not (otherwise a PR the model never
   reads in full would be reviewed on every push); nothing changed -> no run, unless findings are open and a file was
   reverted to `main` (a merge of `main` alone cannot fix anything, so it uses up no round); a change below
   every threshold with open findings -> `rereview.verify_tier` (light). The force label still reads the whole PR,
   full tier, uncounted, cap or not.
4. **Verification.** The model reports for every open finding whether it is resolved at this commit; resolved ones get
   a `Re-review at <sha12>:` reply and a resolved thread, open ones are listed and never re-posted (a guard drops
   duplicates). Only a round with `coverage_complete: true` advances the reviewed commit.
5. **Intent check.** The PR title and body go to the model as data in a fenced block; the prompt checks the diff
   against "What & why", "Behaviour change?" and "Validation evidence".
6. **Findings carry a scope** (`introduced` / `pre-existing`); the findings record becomes schema 2, additive only.
7. **Trigger hygiene.** `paths-ignore: ["docs/generated/**"]` stops runs for the bot PRs of the generated views.

## Why this is not a silent fallback

- The two fallbacks (summary without a state block; reviewed commit not an ancestor of head) print
  `[claude-review] fallback: ...`, reach the job summary and the summary comment; a result without
  `coverage_complete` gives a `::warning::`.
- The verify tier is a visible decision with its reason in the gate log, not a quiet downgrade.
- The cap is not silent: at the cap the gate logs `round cap: N automatic rounds; add label ... for another`, the
  summary comment says so and names the force label.

## Rejected options

- **A verifier-agent vote per finding:** doubles the cost; the fixed/rejected rate the retro already records should be
  looked at first.
- **A `REVIEW.md`:** the prompt, `CLAUDE.md` and `.claude/rules/` already carry that content; a second file would copy it.
- **A blocking review or human-approval tiers:** the review stays advisory; making it required is a separate decision of
  the owner.
- **State in an artifact or a branch:** the comment is the one place a human looks and it dies with the PR; artifacts
  expire and need a download step, a branch needs write access and cleanup.
- **A line-level interdiff:** hunk numbers shift on every merge of `main`; a file-level comparison of normalised
  sections cannot miss a changed file for that reason.
- **Suppressing label-event runs in the workflow:** that needs the label names in the workflow file as well, a second
  copy of a fact that lives in `.github/claude-review.yml`; the cost is a skipped free `classify` run.

## Follow-ups (own issues)

- An eval set: every confirmed finding becomes a case, and prompt, tool or model changes are scored against it.
- `tests.yml` / `docs.yml`: no run for the bot PRs of the generated views (same cause as #517).
- The eqasim-java-bs copy of the script, prompt and workflow.
- Optional retro use of `previous[].resolved` (bot-verified) as tentative evidence next to the author's `Fixed`.
