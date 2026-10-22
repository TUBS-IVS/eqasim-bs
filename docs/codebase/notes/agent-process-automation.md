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
`gh pr create` in an eqasim-bs checkout and allows the command only when both hold:

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
produces it. A command prefixed with `EQASIM_GATE_OVERRIDE="<reason>"` passes. Every
override is logged and counted, and repeated overrides lead to a proposal to fix or relax
the gate. The hook cannot see the chat, so CLAUDE.md's rule still applies: every push
needs the user's explicit yes.

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
- **Ledger and proposals.** The ledger, proposals and run packs live in
  `~/.claude/retro/eqasim-bs/`.
- **What is automatic.** The ledger, the counters, a `SESSION_LOG.md` entry and memory
  notes need no confirmation.
- **What needs a yes.** Anything that changes future behaviour needs one click from the
  user: a rule, test, skill, hook or gate change, a GitHub issue or board move, or
  archiving memory notes. Accepted repository changes are bundled into one `chore/retro-*`
  PR.
- **After a PR.** After `git pr`, the hook injects the CONTRIBUTING step 9 record
  checklist.

## Install on a new machine

1. Clone the private tooling repository to `~/.claude/retro/`.
2. Run `python ~/.claude/retro/bin/retro.py install`. This merges the hook entries
   into `~/.claude/settings.json`.
3. Check the result with `python ~/.claude/retro/bin/retro.py status`.

The hooks run under the system Python with the standard library only. They do not
depend on the `eqasim` environment.

## Relation to `/close`

`/close` stays available. Where the retro runs, the retro covers its record duties
through proposals, so a forgotten `/close` no longer loses the session's lessons.
