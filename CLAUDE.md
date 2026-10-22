# CLAUDE.md

## Project context

MATSim + eqasim scientific transport-simulation research. Treat the codebase as research software: correctness, reproducibility, traceability, clarity, and maintainability outrank quick but fragile solutions. Follow MATSim/eqasim style and structure closely; implementations must be professional, modular, documented, and suitable for scientific use.

## Where the rules live

This file holds only what EVERY session needs. Topic rules live in `.claude/rules/` and load when a file matching their `paths:` frontmatter is read or edited (ADR-0143): `coding-standards.md` (all code and configs) and `tests.md` (writing tests). Their headings were moved verbatim from here, so a comment citing `CLAUDE.md "<heading>"` finds it there. Add a rule here only if every session needs it.

## Model documentation governance (read this first)

Information is maintained ONCE, machine-readable, and rendered into views. The truth hierarchy (code + synpp DAG > resolved canonical production config `configs/base_bs.yml` + `configs/overlays/test_100pct.yml` > registries and ADRs > generated views), the maintenance duties and the checker vocabularies are in `docs/DOCUMENTATION_GOVERNANCE.md` (ADR-0077). Always in force:

- **`docs/generated/*.md` are NEVER edited manually:** `python -m braunschweig.documentation build`, then `... check` must show 0 FAIL.
- **GitHub issues** on `TUBS-IVS/eqasim-bs` are the ONLY backlog. Never claim validation without reference + run + comparison evidence in a run manifest `docs/runs/<run_id>.yml`.
- **Retired** (pointer stubs; content in `docs/archive/`): `PROJECT_STATUS.md`, `PROJECT_BACKLOG.md`, the `RUNS.md` ledger, the monolithic `docs/DECISIONS.md`, the readiness register `docs/readiness/`. Never resurrect them or any parallel STATUS-style state.
- **Non-authoritative:** `SESSION_LOG.md` (gitignored) = local session narrative; Claude memory (`~/.claude/.../memory/`) = durable lessons; `docs/superpowers/{specs,plans}/` (gitignored) = per-feature designs; `docs/features/*.md` = per-feature scientific method, no live state.

**Shape rule — one fact, one file, one owner.** Every home for PER-INSTANCE facts is one-file-per-record (the three registries, ADRs, run manifests, `docs/codebase/notes/<slug>.md` for ONE module/package/mechanism), so concurrent branches never write the same file. Binding consequences:
- **Shared prose files are curated overviews, never append targets.** `docs/codebase/{ARCHITECTURE,STRUCTURE,CONCERNS,TESTING,...}.md` describe the whole system and LINK to records; "append a section about the thing I just did" belongs in a record (seven parallel #267 branches appending to `ARCHITECTURE.md` conflicted unavoidably: the SHAPE was wrong).
- **No fact lives in two files.** A second place gets a link, not a copy.
- **Reference code by symbol, never by line number** (path + function/constant, never `model.py:307`): line pins rot, and `braunschweig/documentation/checks.py` cannot check its own references because it IS the gate.

**Where a new fact goes:** stage exists/changed → Stage Registry (+ `... dag` if the graph changed) · feature semantics/state → Feature Registry · dataset → Data Registry + `scripts/verify_braunschweig_inputs.py` + README · WHY a choice was made, incl. rejected options → ADR · what a run produced or proved → run manifest · work still to do → GitHub issue · module/package layout, or a rule maintainers must follow → `docs/codebase/notes/<slug>.md` · public setup contract → README. Every substantial PR → `documentation build` + `check` (the PR template carries this checklist). A chat summary, a PR body or `SESSION_LOG.md` is never a fact's only home. Never invent history: `unknown` is a valid value; convergence ≠ validation; a smoke ≠ validation.

**Parallel branches:** merge `origin/main` into your branch before `git pr`, and let CI test the pushed merge; if `main` moves before merge, merge it again and push — a green run against a stale base says nothing about the merge state (#284 + #287 turned `main` red exactly this way, each green on its own). While sibling branches are open, do not edit a shared prose file at all.

**Working discipline (one task, fully closed before the next):** the canonical feature workflow is in `CONTRIBUTING.md` (brainstorm -> plan -> worktree -> TDD -> verify -> review -> `git pr` -> record). A branch is either merged-and-deleted or explicitly parked in a GitHub issue — never just left lying around. Subagents: every dispatch names its model and a tool-call budget, per `docs/codebase/notes/subagent-budget.md`.

**Mandatory at `/close` (end of every session):** step 9 of `CONTRIBUTING.md` (registries/ADRs/run manifests for what happened, rebuild + check the generated docs, `SESSION_LOG.md`, GitHub Project board), the fix-now-or-issue triage for newly discovered work (quick fixes listed in `SESSION_LOG.md`, larger work proposed as issues), and the triage check in `docs/codebase/notes/issue-and-pr-conventions.md` ("Triage check"). Where the session-retro automation of `docs/codebase/notes/agent-process-automation.md` is installed, it covers these duties through proposals.

**PRs ALWAYS via `git pr`** (a local alias pinned to base `TUBS-IVS/eqasim-bs`, the fork — never the `eqasim-org/eqasim-bavaria` upstream, which the GitHub web UI defaults to). Recreate it on a new machine with
`git config alias.pr '!gh pr create --repo TUBS-IVS/eqasim-bs --base main'`.
A PR mirrors its issue (branch `<kind>/i<issue>-<slug>`, labels, `(closes #NN)` title, `**Stages:**` line) per `docs/codebase/notes/issue-and-pr-conventions.md`.

No size budgets. CLAUDE.md, MEMORY.md and SESSION_LOG.md have no KB or entry limit, and nothing is ever shortened, archived, or left unwritten just to hit a number. Keep them lean the only way that is safe: one fact in one place, dedupe before adding, topic rules in path-scoped `.claude/rules/`, and archive at /close what actually merged or was parked.

## Language policy

All code must be written in English: class, method, variable, package and file names, comments, JavaDoc, log messages, commit messages, configuration descriptions, test names, and documentation inside the repository. German may only be used in external text outputs when explicitly requested. Code, comments, and technical documentation must remain English. Chat responses to the user are in German.

## Tests

**Mandatory for every coding agent:** the
[required verification gate](docs/codebase/TESTING.md#required-agent-verification-gate)
(`python scripts/run_tests.py`, `--check` at session start; no direct-pytest, patched,
skipped or other-code-state substitutes; evidence recorded in the handoff/PR; both CI
platforms before merge). Locally, run only the tests the change affects, including its
data-gated tests; the full suite runs in CI on the PR, never as parallel local runs
(ADR-0150). How tests are written: `.claude/rules/tests.md`.

## Fallback transparency (no silent fallbacks) — MANDATORY

Silent fallbacks are a recurring source of hidden bugs here: a stage's primary
(real-data / proper) method quietly fails for some or all items, a fallback catches it,
the pipeline runs and the tests stay green — but the intended method never worked.
That is unacceptable for research software. For **every** code path with a fallback
(nearest-neighbour fill, whole-region pool, "rda"/"random" solver fallback,
scalar-default-when-map-missing, default-when-data-absent, except/try recovery, etc.),
existing ones included (add missing instrumentation whenever you touch a stage):

1. **Make the fallback observable.** Count and `log` (info/warn) PRIMARY vs FALLBACK
   items as an explicit rate, e.g. `"[stage] primary 9842/10000 (98.4%), fallback 158 (1.6%)"`.
   Never let a fallback fire silently.
2. **Treat a high fallback rate as a failure signal.** Most/all items on the fallback
   (above a configurable threshold, especially ~100%) almost always means the primary
   method is broken (a format mismatch, an empty join, a wrong key): surface it loudly
   (`warn`, or `raise` where a high rate cannot be scientifically defensible) and add
   the rate to the per-run validation summary where one exists.
3. **Test the primary method, not just the fallback.** A green test that only exercises
   (or silently tolerates) the fallback proves nothing; assert that the PRIMARY path is
   taken on representative input and that the fallback rate stays below an expected bound.
4. **When adding or reviewing ANY stage with a fallback, verify primary-method
   coverage** and wire in the rate logging — proactively across the model, not only
   when a bug appears.

## No invented reference values; convergence is not validation — MANDATORY

Two related failure modes are strictly forbidden because they silently fabricate
scientific claims:

1. **Never invent or assert "target" / "reference" / "ground-truth" values.** A reference
   value (a modal split, a mean distance, a rate to compare against) may only be stated if
   it is traceable to a committed source in the repo (a pinned CSV under `eqasim-data/.../`,
   a documented table, a cited external publication with the figure). Otherwise say so and
   label the number an **assumption** ("ASSUMPTION: ...", with the reasoning), never an
   established target. Do not carry numbers from chat / prompt context into a results report
   as validated references. Comparing model output to a made-up target and calling the fit
   "excellent" is a fabricated result.
2. **Convergence (stability) is NOT validation (matching reality).** The eqasim mode-share
   termination criterion (`eqasim:termination`, `ModeShareTracker`) stops the MATSim run when
   the modal split **stops changing** (smoothed change `< threshold`, default 0.001); it has
   **no real-world reference shares** and says **nothing** about whether the equilibrium
   matches observed behaviour. Report "the run converged (mode shares stabilised, change below
   threshold)"; never "hit the target" / "calibrated to the data" unless the realised shares
   were actually compared to a committed observed reference.

When unsure whether a number is a real reference or an assumption, treat it as an
assumption and flag it. Cautious, honest, traceable reporting always wins over a
confident-sounding but unsupported claim (see "Research reporting").

## Research reporting

When generating analysis outputs, include enough information to reproduce the result: every figure or table should be traceable to its input data, scenario, processing script, parameters, and date or run identifier. Do not overstate results; use cautious scientific language and report limitations where relevant. Distinguish clearly between observed data, modeled data, assumptions, and derived indicators.

## Git and version control

**Never run `git push` without explicit user confirmation.** Committing locally is fine;
every push to any branch or remote, `origin/main` included, needs an explicit "yes" /
"push it" / equivalent asked for each time (a prior confirmation does not authorise later pushes).

**Newly discovered work: fix it now, or issue it.** Triage every finding that surfaces mid-session:
- **Quick fix -> fix it now, no issue.** Small and self-contained: one concern, a few files, about an hour or less, no
  owner decision, no change to scientific results beyond correcting an evident bug. Bugs go through
  `superpowers:systematic-debugging` (root cause first, failing test first). Fix it on the current branch if it belongs
  to the current work, else on a small `fix/` or `chore/` branch with a PR that needs no issue. Say it in one line to the
  user, update the record that owns any changed fact, and list it in `SESSION_LOG.md` at `/close`.
- **Larger work -> issue, proposed first.** Multi-step or multi-hour work, a design or owner decision, a change of model
  behaviour or scientific results, missing data or external input, or anything that cannot be done now: PROPOSE it; only
  after explicit confirmation open a GitHub issue — ALWAYS in the fork `TUBS-IVS/eqasim-bs` (never the
  `eqasim-org/eqasim-bavaria` upstream) — typed, labelled, titled and opened with the plain-language head (`In short`,
  `## Problem`, `## Why it matters`, `## Done when`) exactly per `docs/codebase/notes/issue-and-pr-conventions.md`.
- **Unsure?** Ask in one line: "fix now or issue?". No issue for anything that was fixed in the same session.
All issues, PRs, and the Project board live on the fork only.

**Working hygiene (reasons and incidents: `docs/codebase/notes/git-working-hygiene.md`):**
- **One worktree per TASK, not per session:** `git worktree add -b <branch> .claude/worktrees/<task> origin/main`.
- **After any `git rebase`, check `git log --oneline -1` IS your commit before amending** ("Successfully rebased" also prints when it was DROPPED as already-upstream).
- **Stage explicit paths, never `git add -A` or a directory**; verify with `git show --stat HEAD`. Committed `eqasim-data/` reference tables need a deliberate `git add -f` per the `.gitignore` allowlist.
- **The `.git` is shared across worktrees:** a local branch may already be pushed with a PR open (`git ls-remote --heads origin <branch>`, `gh pr list --head <branch> --state all`); never `git switch` in a directory you do not own.
- **A worktree has no gitignored data:** pass `--raw <main-checkout path>` and `--out-dir <scratch>`, copy back only the intended file, diff regenerated siblings to prove the change was additive.
- **Merging a PR is the user's action**; `gh pr merge` is blocked deliberately.

Keep commits focused. Commit messages must be in English and explain the change and its purpose: use clear messages (e.g. `Add validation for missing freight carrier capacities`), avoid unclear ones (e.g. `fix`, `update`, `changes`). Full example lists: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#commit-message-examples).

## Preferred response behavior for Claude

How to approach code changes (inspect first, never guess APIs, state whether outputs may change) is in `.claude/rules/coding-standards.md` "Code changes".

**Answers in chat must be scientifically sound AND immediately understandable — both, never one at the cost of the other.** Simplify the packaging, not the content. Keep the substance every time: the actual numbers with their units, where each figure comes from, the distinction between observed data, model output and assumption, and any caveat that would change the user's decision (see "No invented reference values", "Research reporting"). Present it for a reader who has thirty seconds: the result first, then the short reason. Short sentences, plain words, no jargon where a normal word exists, and no narration of your own process (which tools ran, which files were opened, what was tried and dropped). Anything longer than a few lines becomes a handful of bullets or a small table, never a wall of prose, with the numbers, paths, and decisions easy to spot. Derivations, method details, and full evidence belong in the files the answer links to (registry record, ADR, run manifest), not in the chat. If a result cannot be said simply without becoming wrong, say it precisely and add one plain sentence that explains it. Chat language is German per the language policy above.

## Non negotiable rules

- All code and comments must be in English.
- All scientific assumptions must be explicit.
- All relevant parameters must be configurable.
- All output must be traceable.
- All non trivial logic must be documented.
- All important processing steps must be logged.
- All input data must be validated.
- All changes must preserve scientific credibility.
- Completeness, consistency, reproducibility, and clarity are mandatory.
- Every answer in chat must be scientifically sound and, at the same time, short and immediately understandable.
