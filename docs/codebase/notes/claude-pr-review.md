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
change its own gate; all three are critical paths, which makes such a PR reviewed. So are
`CLAUDE.md` and `.claude/rules/` (since 2026-10-28): they steer every agent session, the
push rule and the verification gate included, so even a small edit there is reviewed.

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
| `classify` | no | `scripts/claude_review.py state` and `interdiff` (see "Rounds"), then `classify`: reads `git diff --numstat base...head`, the branch kind, labels, draft state, the state and the interdiff numstat, and decides `none` / `light` / `full` plus the outputs `mode`, `diff_source`, `round` |
| `review` | yes, only if `classify` says so | installs the pinned Claude Code CLI, recomputes `state` and `interdiff` (no artifact travels between the jobs), `claude_review.py review` (runs `claude -p`), `cost`, `post` |

The thresholds, paths, labels, models, turn and budget limits live in
`.github/claude-review.yml`, not in code. Its values are starting assumptions
(2026-10-23), not measured; re-tune them from the cost records below. Decision order
(`classify` in `scripts/claude_review.py`; the first-round order, a PR without a complete
round on record, is below, the further-round order is in "Rounds"):

1. Label `skip-claude-review` -> no review.
2. Adding the label `claude-review` -> one full-tier review of the whole PR, also on drafts
   and whatever the round cap says (the force label, see "Rounds"). Later pushes
   are gated normally, so a label left on the PR cannot bill every push.
3. A `labeled` event for any other label -> no review (otherwise adding `automerge`
   would start a paid review).
4. Draft PR -> no review.
5. Branch kind in `skip_kinds` (`docs/`) without a critical path -> no review.
6. Changed lines >= `full_min_lines`, or a critical path and >= `full_min_lines_critical`
   -> full tier.
7. A critical path, or >= `light_min_lines` lines, or >= `light_min_files` files -> light.
8. Otherwise no review.

Runs queue per PR and event kind, and a running review is never cancelled: its cost is
already spent and is recorded only at the end. A newer push replaces a still-pending push
run. Every label event runs in a concurrency group of its own label, so a triage label
cannot displace a review, and a pending forced review is not displaced by a later push
(a Claude review finding on PR #497: with one shared group, the push replaced the pending
forced run and the push run was then gated normally).

Changed lines are added + deleted lines of files outside `ignored_paths` (generated
views, lock files, test baselines); binary files count as a file with 0 lines. Every
decision, including a skip, is printed with its reason to the log and the job summary.

## Rounds

Why the review works in rounds: [ADR-0171](../../decisions/ADR-0171-the-claude-pr-review-reviews-in-rounds-verifies-fixes-and-stops-after-three.md).

**State.** The bot's summary comment carries a second hidden block, `STATE_MARKER`
(`<!-- claude-pr-review:state <json> -->`), written by `post` (`encode_state`, which escapes
`>` as `>` so the JSON never contains `-->`) and read by `decode_state`. Schema 1:
`rounds` (`round`, `sha`, `tier`, `run_id`, `automatic`, `complete`, `cost_usd`, `findings`)
and `findings` (`id` = `<sha12>-<index>`, `path`, `line`, `severity`, `scope`, `title`,
`comment_id`, `status`, and for open ones `body` and `author_note`; caps `TITLE_CHARS`,
`BODY_CHARS`, `NOTE_CHARS`). `status` is `open`, `resolved` (a later round verified the fix)
or `dismissed` (`refresh_state`: a thread reply starting with `REJECTED_PREFIXES`, or a thread
resolved by a human; the bot's own `Re-review at ...` reply never dismisses, and only `open`
findings change status). Only `open` findings go to the model. `post` re-reads the comment
and merges rounds and findings (`merge_state`) before writing, so a run for a commit that is no
longer the head still records its findings but leaves the visible summary text alone.

**Commands, both model-free.** `state --repo --pr --out` reads the comment and the threads of
open findings and writes `state.json`. `interdiff --config --state --base-sha --head-sha
--out-dir` compares `git diff base...last` and `git diff base...head` per file section
(`split_sections`; `index` lines and hunk numbers are normalised away, so a merge of `main`
shifts nothing) and writes `interdiff.diff`, `.numstat` and `.json` (changed, unchanged and
reverted paths; a renamed file is changed). `last` is the last round with `complete: true`
(`last_complete_sha`). `classify` takes `--state`, `--interdiff-json`, `--interdiff-numstat`.

**Decision order of a further round** (a state with a complete round, a push-like event):
skip label, other label events and drafts as in the first round; then
`rereview.max_rounds` automatic complete rounds reached -> none (`round cap`); nothing changed
and no open finding -> none; nothing changed but open findings -> `rereview.verify_tier`
with no diff (the model verifies at head); otherwise the tier of the interdiff numstat with
the normal thresholds and critical paths, raised to `rereview.verify_tier` when that is none
but findings are open. The `docs/` branch rule applies as in the first round.

**The force label** reads the whole PR again: a full-tier round over the complete PR diff
(`mode` `forced`, `diff_source` `pr`), whatever the cap and the interdiff, recorded with
`automatic: false` and not counted against the cap. It still gets the open findings.

**Fallbacks, both logged with `[claude-review]`, in the job summary and the summary comment.**
- Summary without a state block (or a corrupt one): `fallback: no state block in the existing
  summary; first-round review, earlier findings unknown`; the state starts empty.
- `last` not an ancestor of head (rebase, force-push): `fallback: reviewed commit <sha12> is
  not an ancestor of head (rebase or force-push?); the whole PR is reviewed`; the round counter
  and the open findings are kept.
- A result without `coverage_complete` is counted as complete, with a `::warning::`; a result
  with `coverage_complete: false` is recorded with `complete: false` and does not advance
  the reviewed commit.

**The run.** `review` also gets `--state`, `--interdiff-dir`, `--diff-source`, `--mode`,
`--round` and `GH_TOKEN`: it fetches the PR title and body (`gh api`) and `review_prompt`
puts them in a four-backtick fenced block headed "PR description" (data, not instructions;
a fence inside the body cannot close it, and `build_prompt` fills values in one pass, so a
placeholder in the body stays text). The prompt asks for the intent check (does the diff do
what "What & why" says, does "Behaviour change?" match, does "Validation evidence" name
evidence the diff could have produced), the open findings (do not report them again; for each
say resolved or not in `previous`), and the class of a bug. The schema `FINDINGS_SCHEMA`
adds `scope` per finding (`introduced` / `pre-existing`), `previous` and `coverage_complete`.

**Posting.** New findings are posted as before with the prefix `**HIGH: title**` or
`**HIGH (pre-existing): title**`; a new finding is dropped as `duplicate of <id>` when an
open finding has the same path and the same normalised title or the same line and severity.
A previous finding reported resolved gets a reply `Re-review at <sha12>: no longer present.
<note>` and its thread is resolved (GraphQL `resolveReviewThread`); one reported still open
is only listed in the summary under "Still open from earlier rounds"; the summary shows a
rounds table and, at the cap, names the force label. A bot reply never starts with
"Fixed", "Rejected" or "Not changed": those are the retro's outcome words.

**Retro compatibility.** The findings record has `schema: 2` and adds `round`, `scope` per
finding and `previous: [{id, comment_id, resolved, note}]`; no field is renamed or removed.

**Trigger hygiene.** `on.pull_request.paths-ignore: ["docs/generated/**"]` in the workflow:
the bot PRs of the generated views (ADR-0168) create no run. Label events still run the free
`classify` job; suppressing them in the workflow would copy the label names into it.

## The review run and the posting

- `review` fills the placeholders of `.github/claude-review-prompt.md` and runs `claude -p`
  with the tier's `model`, `max_turns` and `max_budget_usd` (a hard cap per run), the JSON
  schema `FINDINGS_SCHEMA`, `--bare` (authentication strictly by `ANTHROPIC_API_KEY`, never a
  Claude plan login; no user hooks, plugins, memory or automatic CLAUDE.md loading; the
  prompt names the rule files to read), `--permission-mode dontAsk` and only the read tools of
  `review.allowed_tools` (Read, Grep, Glob). The model has no shell, cannot write files and
  cannot post anything. The script builds the PR diff itself (`git diff base...head` in CI,
  which has no API size limit; `gh pr diff` without `--base-sha`), drops the file sections
  that match `ignored_paths` (logged by count and path; a PR with only ignored files makes no
  model call), and writes it to `.claude/review-pr.diff` inside the checkout. The model pages
  through a numbered copy (`.claude/review-pr.diff.numbered`, `number_diff`): every added
  and context line carries its new-file line number, which is the `line` the model must
  cite. A local test run (2026-10-23) showed that with `gh pr diff` as a tool the model piped
  it into grep/awk, which the read-only tool list denied, and reviewed only part of the diff;
  the first CI runs on the plain diff file cited line numbers of the diff file itself
  (`claude_review.py:723` in a 650-line script), so every finding landed off the diff. Git
  output is split on `\n` only (`git_lines`): `str.splitlines()` also splits on characters
  such as ` ` that can occur inside a source line. The prompt goes in on stdin; the
  JSON result goes to a file.
- `post` turns the findings into one PR review (`event: COMMENT`) whose inline comments sit
  on new-side lines of the diff (`commentable_lines`, which follows the line counts of each
  hunk header, so an added line starting with `++ ` is content, not a file header), ordered
  by severity. GitHub rejects inline comments outside the diff, so such findings go into the
  summary under "Not on a changed line"; the inline/summary-only counts are logged, and a
  warning fires when every finding lies outside the diff. The summary comment carries a
  hidden marker; only a comment by `github-actions[bot]` with that marker is edited
  (`upsert_summary`). A forced and a push review can post at the same time, so after writing,
  the oldest summary keeps the current body and any duplicate is deleted. A run whose commit
  is no longer the PR head leaves an existing summary alone, so a slower review of an older
  commit cannot replace the newer one; its inline comments are still posted. A failed run
  (`is_error`) posts nothing and fails the step with the API's error text. The diff is read
  with `errors="replace"`: a non-UTF-8 byte (e.g. in a Latin-1 data file) must not crash
  the review.
  On a further round the model reads the numbered interdiff (`diff_source` `interdiff`,
  whole file sections at head line numbers) or no diff at all (`none`: only open findings
  to verify); the posting then follows "Rounds".
- `post --dry-run` prints both payloads and posts nothing, for local tests.

## Learning from the findings (session retro)

`post --record` writes `claude-review-findings.json`, uploaded per run as the artifact
`claude-review-findings-pr<NN>-run<id>-<attempt>` (90 days): every finding with severity,
path, line, title, body, placement and, for an inline comment, its GitHub comment id
(`match_comment_ids`). The maintainer's personal session retro (`~/.claude/retro`,
`retro_reviews.py`, outside this repository) downloads these records from both repositories,
reads each inline finding's thread and feeds the findings to its judge, which matches them
against the lessons it already knows (memory notes, ledger). Only a finding the author
**confirmed** counts towards a lesson; two confirmed sightings on different PRs make the
retro propose a rule (`.claude/rules/<topic>.md`) for a one-click yes. Since the prompt makes
the review read every file in `.claude/rules/`, an accepted rule then guides both the next
author and the next review.

The outcome is read from the replies in the finding's review thread, so answer each one:

- **`Fixed ...`** (e.g. `Fixed in <sha>: <what changed>`) when the finding was right and is
  fixed: a confirmed defect, it counts;
- **`Rejected: <reason>`** (or `Not changed: ...`) when it is not a defect: it never counts;
- no such reply: `resolved` / `open`, recorded as tentative evidence only. A summary-only
  finding has no thread and stays `unknown`.

With the desktop app's Auto-fix on for a PR, the session fixes the reported findings and
pushes the fix without a separate yes; the scope of that standing exception is in
`CLAUDE.md` ("Git and version control").

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
- The new keys (`rereview.max_rounds`, `rereview.verify_tier`) and the label names live in
  `.github/claude-review.yml` only; the workflow file must not contain the label names
  (a test checks this) and the code reads them from the config.
- A bot reply never starts with "Fixed", "Rejected" or "Not changed"; the verification
  reply starts with `Re-review at <sha12>:`. The state block never contains `-->`.
- Findings record fields are only ever added (`schema` 2), never renamed: the retro reads them.
- Keep the review advisory until its findings have been checked against real PRs; making
  it a required check is a separate decision.
- Bump the pinned CLI version (`CLAUDE_CODE_VERSION` in the workflow) deliberately.
- Keep the model read-only: posting belongs in `post`, not in the model's tools.
