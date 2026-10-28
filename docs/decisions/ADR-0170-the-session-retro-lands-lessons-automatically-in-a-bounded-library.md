# ADR-0170 · 2026-10-28 · The session retro lands lessons automatically in a bounded, self-cleaning library

- **Status:** accepted (the personal tooling `~/.claude/retro` runs it; this repository records the contract in
  CLAUDE.md and `docs/codebase/notes/agent-process-automation.md`)
- **Numbering:** 0170 checked free across all local and remote branches on 2026-10-28 (highest taken: ADR-0169; no
  open pull request).
- **Amends:** CLAUDE.md "No size budgets" (replaced by "Size caps"), the issue-first part of "Newly discovered work"
  (the retro files issues without asking), and the tier-2 approvals of the session retro (2026-10-22, the "What needs
  a yes" list in the automation note).
- **Owner decision (2026-10-28):** "ich will das das alles automatisch passiert und ich nicht zustimmen muss im
  Hintergrund"; caps from autoharness adopted and "No size budgets" removed from CLAUDE.md; learned rules personal
  and active at once; issues "alles automatisch, irgendwie smart"; learned hooks only warn; the 44 deleted notes go
  into a restorable archive and revive on recurrence.

## Context

- Since 2026-10-22 the retro judged finished sessions automatically, but every behaviour change (rule, test, skill,
  hook, issue, archiving) waited for a yes in chat. All six proposals (P0001-P0006) were decided by hand; a lesson that
  waits does not prevent the next occurrence.
- The archive step accepted as P0003/P0006 on 2026-10-23 called `unlink()` on 44 memory notes and kept only one-line
  summaries in `ARCHIVE.md`. The memory directory is not versioned. A probe on 2026-10-28 over 451 session transcripts
  found a full copy for 18 of them; 26 bodies are lost. Its criterion (no Read in 60 days) also ignored that Claude
  Code loads the `MEMORY.md` index line into every session and reads a note's body only on demand.
- Claude Code loads only the first 200 lines or 25 KB of `MEMORY.md` and advises keeping a CLAUDE.md under 200 lines
  (code.claude.com/docs/en/memory, read 2026-10-28). Today: `MEMORY.md` 113 lines / 12 KB, CLAUDE.md 161 lines.
- autoharness (github.com/tigerless-labs/autoharness, MIT, read at commit 697cf5e) runs the same kind of loop for
  Claude Code skills without approvals: a deterministic promoter as the only writer, a usage-based lifecycle
  (probation, graduation review, capacity contention), archive instead of delete, a periodic curator, and a run account
  shown at the next session start.

## Decision

1. **No approvals.** Every lesson that crosses its threshold (2 sessions for a mistake, 3 for a procedure) and passes a
   deterministic lint lands at once; a rejected one writes nothing and its reason is shown at the next session start.
   The session start informs and never asks.
2. **Where lessons land:** memory notes (index line at most 90 characters: rejected, never truncated); personal rules
   `~/.claude/rules/learned-*.md` (at most 25 non-blank lines; they load in every session); personal skills
   `~/.claude/skills/learned-*/`; warn-only guards for patterns a regex catches; everything that needs code or a
   repository change becomes a GitHub issue on the fork. Only self-authored files are ever written.
3. **Smart issues:** the outbox searches the open issues first and comments on a duplicate instead of filing a second
   one, files at most two per run, redacts secrets, personal data and home paths, and keeps a failed filing pending and
   reported until it goes through.
4. **Bounded library:** probation of 50 retro'd sessions (guards 30); after it, no view and no use means archive;
   beyond the capacity (80 notes, 30 rules, 20 skills, 20 guards) the lowest use rate goes first; while `MEMORY.md` is
   above 150 lines or 25 KB, further mature notes go. The clock counts retro'd sessions, not days. All numbers except
   Claude Code's load limit are assumptions (autoharness defaults or chosen headroom), configurable.
5. **Archive, never delete:** archiving moves the member; it returns by itself when its lesson is sighted again with
   high confidence, or by `retro.py restore`. Every run commits the archive and a mirror of the memory directory to
   the retro's git repository. The 44 deleted notes become archive records (18 with recovered bodies).
6. **Curator:** every 20 retro'd sessions one background pass folds near-duplicates, after a snapshot.

## Why this is not a silent fallback

- Every rejection, archive, revival and filing is in the run account shown at the next session start and in
  `runs/<job>/applied.json`; failures of the automatic tail and of the outbox are repeated at every session start until
  resolved; `retro.py metrics` shows the funnel and the library's health.
- Nothing is deleted, so a wrong archive decision costs one `retro.py restore`, not a lesson.

## Rejected options

- **Keep the one-click approvals:** the owner rejected them; the history shows they stall learning.
- **Install the autoharness plugin itself:** it supports only Linux and macOS (`python3` on PATH), would run a second
  learning system beside the retro and learns only skills, not notes, rules, guards or issues.
- **Learned rules in the repository via automatic pull requests:** a rule would act only after a merge, and the push
  needs the owner's yes per push (CLAUDE.md); the owner chose the personal layer. A rule the CI review must enforce
  becomes an issue instead.
- **Blocking learned hooks:** a wrong learned block could stall every session; blocking stays with hand-written hooks.
- **Mid-session reflection every N tool calls (autoharness):** needs a headless `claude -p`, which failed here with an
  expired OAuth session from inside a tool; the idle-transcript trigger stays.
- **No caps ("No size budgets"):** an index past Claude Code's load limit drops its tail silently for every session.

## Consequences

- A lesson acts from the next session on, without anyone's attention; the owner sees one line per run.
- Personal rules and skills live outside the repository: other contributors and the CI review do not see them. A
  lesson that must hold for everyone needs an issue and a repository change.
- The library stays bounded; low-value members leave and come back when they matter again.
