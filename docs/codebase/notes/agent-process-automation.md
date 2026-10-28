# Agent process automation: push-gate evidence and the session retro

## What it is

Two repository writers record what was verified, so a mechanical push gate can check
it instead of trusting a chat claim. Everything else is personal Claude Code tooling of
the maintainer and lives outside the repository. Nothing here changes model code or
scientific output.

## The evidence files (in the repository)

Both files land in `<checkout>/.claude/gate/`, which the existing `.claude/*` gitignore
covers. Each checkout and worktree has its own.

| File | Written by | Content |
|---|---|---|
| `tests-<platform>.json` (`linux`, `windows`) | `scripts/run_tests.py`, after every pytest run (`record_test_evidence`) | `head`, `dirty`, `warning`, `platform`, `python`, `command`, `scope` (`full` / `focused`), `exit`, `passed` / `failed` / `errors` / `skipped`, `junitxml`, `started_at`, `finished_at` |
| `docs-check.json` | `python -m braunschweig.documentation check` (`_record_check_evidence`) | `head`, `dirty`, `warning`, `fail_count`, `warn_count`, `no_dag`, `command`, `finished_at` |

Rules shared by both (helpers in `braunschweig/gate_evidence.py`):

- **One file per platform.** A Windows run never overwrites the WSL run's evidence.
- **`dirty` ignores line endings.** It comes from the exit code of
  `git diff --ignore-cr-at-eol --quiet HEAD` (`git_state`). Inside WSL, a Windows checkout
  shows thousands of CRLF-only changes, and `git status` needs 20+ seconds there
  ([linux-parity-testing](linux-parity-testing.md)). Untracked files do not count, so a
  run that passes only because of an uncommitted new file still records `dirty: false`.
- **A changed tree is not evidence.** The runner records HEAD and `dirty` before and after
  pytest. If either changed during the run, `dirty` becomes `null` and `warning` says so.
- **`null` means "not evidence".** If `git` fails, `head` / `dirty` are `null` and
  `warning` says why.
- **`scope` is `full` only for the whole default suite** (`run_scope`). The check is
  fail-closed: only reporting and parallelism options keep a run `full`, namely `-q`, `-v`,
  `-l`, `-s`, `-r…`, `-n`, `-p`, `-W`, `--tb`, `--durations`, `--junitxml`, `--dist` and
  `--color`. Anything else makes it `focused`. That includes test paths, node ids,
  `-x`/`-k`/`-m` (also bundled as `-xq`), `-o`, `-c`, `--fixtures`, `--pipeline` and a set
  `PYTEST_ADDOPTS`.
- **The runner always records JUnit counts.** Without a caller `--junitxml`, it writes
  `.claude/gate/junit-<platform>.xml`. It deletes that file before the run, so the counts
  always come from this run. If pytest dies before writing a report, the counts are
  `null`.
- **Probe runs can opt out.** `--no-evidence` writes nothing. The runner's own
  fresh-process tests use it, so they never replace a checkout's real evidence.
- **Writer failures never change exit codes.** A writer error is printed, and the test
  or check exit code is returned unchanged.

## What the push gate requires

The gate is a personal `PreToolUse` hook. It intercepts `git push`, `git pr` and
`gh pr create` in an eqasim-bs checkout and allows the command only when both hold
(a push in another repository, such as eqasim-java-bs, passes untouched). The checkout is
the one the push runs in (`retro_gate.target_dir`): the session directory, moved by every
`cd` / `Set-Location` / `pushd` before the push, then by the push's own `git -C`. A
`git -C` of any other git command does not count, because it never moves the shell (until
2026-10-28 it did: a `git -C <eqasim-bs> show` that only read a file made the gate judge
eqasim-bs for a push in the eqasim-java-bs clone).

1. `docs-check.json` has `head == HEAD`, `dirty == false`, `fail_count == 0`,
   `no_dag == false`. CI's `--no-dag` mode skips the DAG-freshness check, so its output
   is not enough.
2. `origin/main` is an ancestor of `HEAD` (after a non-interactive `git fetch origin main`,
   which updates the remote-tracking ref). If the fetch fails or times out (offline), the
   gate shows a note and does not block.

The gate does not require test evidence. The full suite runs in CI on the PR, on Linux
and Windows, and its green result on the latest commit is the final-code evidence
(ADR-0150, [verification gate](../TESTING.md#required-agent-verification-gate)).
`tests-linux.json` and `tests-windows.json` are shown as information when present.

If the gate blocks, its message names each missing item and the exact command that
produces it. If the gate is wrong (it judged the wrong checkout, or the evidence cannot be
produced), the agent stops and shows the block to the user (`retro_gate.OVERRIDE_ADVICE`);
it never sets an override on its own. A command prefixed with
`EQASIM_GATE_OVERRIDE="<reason>"` passes the gate, but it needs the user's explicit yes to
the override itself: approving a push does not approve bypassing the gate. Claude Code's
auto-mode classifier denied every override an agent set on its own, 3 of 3 attempts
(2026-10-22 twice, 2026-10-28; checked in the local session transcripts), two of them while
the user had approved the push. An override is counted only once the push has run (the
retro's `post-tool-use` hook; a call the classifier denies never gets there), and repeated
overrides lead to an issue to fix or relax the gate. The hook cannot see the chat, so
CLAUDE.md's rule still applies: every push needs the user's explicit yes.

## The session retro (personal tooling)

- **Location.** The tooling lives in `~/.claude/retro/`, a private git repository that is
  not shared. Its hooks are registered in the user settings `~/.claude/settings.json`,
  not in the repository, because the repository's `.claude/*` is gitignored and
  worktrees do not carry it. Each hook checks its working directory itself and does
  nothing outside the configured project roots.
- **Flow.** At the next session start, a background subagent runs the `retro` skill
  (`~/.claude/skills/retro/`) over transcripts that have gone idle:
  1. A script extracts cited trouble spots.
  2. One cheap judge model classifies them.
  3. A script records the results.
- **Ledger and runs.** The ledger, run packs, usage counters, archive and outbox live in
  `~/.claude/retro/eqasim-bs/`; every run is committed there, so the memory directory and
  the learned files have a history (the memory directory itself is not versioned).
- **Everything is automatic (ADR-0170).** Nothing waits for a decision. A deterministic
  promoter lints each lesson the judge proposes and lands it at once, or rejects it and
  writes nothing:

  | Lesson | Lands as |
  |---|---|
  | a new mistake | a memory note (`feedback-*.md`, index line of at most 90 characters) |
  | a mistake seen in 2 sessions | a personal rule `~/.claude/rules/learned-*.md` (at most 25 lines; loads in every session) |
  | a recurring mistake a pattern can catch | a guard that WARNS on the matching command or file; learned guards never block |
  | a procedure done by hand 3 times | a personal skill `~/.claude/skills/learned-*/` |
  | LARGER work that needs code or a repository change (a test, a real hook, a run manifest, a CI review rule, unfiled larger work, per CLAUDE.md "Newly discovered work") | a GitHub issue on the fork, filed without asking: the open issues are searched first and a duplicate gets a comment instead; at most two per run; titles and bodies are redacted. A quick fix never gets an issue: the judge names it in the run summary instead, so the board does not fill up |

  The promoter only touches what the retro wrote itself; hand-written files are never
  changed.
- **Bounded library.** Each learned member (note, rule, skill, guard) is counted: read
  (`view`) and relevant again (`use`: invoked, fired, or its lesson seen again). A new
  member has a probation of 50 retro'd sessions (guards 30); after it, a member with neither
  view nor use moves to the archive, and beyond the capacity (80 notes, 30 rules, 20
  skills, 20 guards) the lowest-use ones go first. While `MEMORY.md` is over 150 lines or
  25 KB, further mature notes go too (Claude Code loads only 200 lines / 25 KB). These
  numbers are assumptions, tuned in `config.json` under `autopilot`.
- **Archive, never delete.** An archived member is moved, not deleted, and comes back by
  itself when its mistake recurs, or with `retro.py restore <name>`. A note absorbed by a
  rule or by a merge records what absorbed it. The 44 notes an older version deleted on
  2026-10-23 are archive records now: 18 with the full text recovered from transcripts,
  26 with only their one-line summary.
- **Curator.** Every 20 retro'd sessions, one background pass over the whole library folds
  near-duplicates into one member, after a snapshot.
- **Informed, never asked.** The next session start reports once what landed, was archived,
  revived, filed or rejected, and repeats health warnings (an issue that cannot be filed,
  `MEMORY.md` over its load limit) until they are resolved. `retro.py metrics` shows the
  library's health.
- **After a PR.** After `git pr`, the hook injects the CONTRIBUTING step 9 record
  checklist.

## Install on a new machine

1. Clone the private tooling repository to `~/.claude/retro/`.
2. Run `python ~/.claude/retro/bin/retro.py migrate-autopilot` once (idempotent).
3. Run `python ~/.claude/retro/bin/retro.py install`. This merges the hook entries
   into `~/.claude/settings.json`.
4. Check the result with `python ~/.claude/retro/bin/retro.py status`.

The hooks run under the system Python with the standard library only. They do not
depend on the `eqasim` environment.

## Relation to `/close`

`/close` stays available. Where the retro runs, the retro covers its record duties
automatically, so a forgotten `/close` no longer loses the session's lessons.
