# ADR-0208 · 2026-11-26 · No GitHub Project board; the issues carry all work-item state

- **Status:** active
- **Numbering:** 0208 checked free across all remote branches on 2026-11-26 (highest taken: 0207 on a branch, 0205 on `main`).
- **Owner decision (2026-11-26, Felix, in the session):** "keine Board hier ever" (no board here, ever).

## Context

- `CONTRIBUTING.md` step 9, `CLAUDE.md` ("Mandatory at `/close`") and
  `docs/codebase/notes/issue-and-pr-conventions.md` required `/close` to keep the GitHub Project board
  "eqasim-bs" (TUBS-IVS project 3) in sync as a mirror of the issues.
- Measured on 2026-11-26 (`gh issue list --json projectItems`): of 45 open issues, 11 were on the board
  (10 `Todo`, 1 `In Progress`) and 34 were not, including almost every issue filed since #570. Nobody moves
  cards, so the board's status column only repeats open/closed, which the issue itself already holds.
- The repository has no workflow that adds issues to the board, and the `gh` token used by agents lacks the
  `project` scope by default, so the duty silently failed in most sessions.

## Decision

1. The GitHub Project board is not part of the workflow. `/close` and the feature workflow no longer sync it.
2. The issues are the single source of work-item state: native issue type plus the `prio:*`, `step:*` and
   `area:*` labels (`issue-and-pr-conventions.md`); a filtered issue list replaces any board view.

## Rationale

- One fact, one owner (ADR-0077): a mirror nobody maintains is a second, stale copy of the issue state.
- The conventions note already stated that "nobody has to open it to understand a work item".

## Rejected alternatives

- **Keep the board, automate it** (GitHub's built-in "Auto-add to project" and "Item closed -> Done"
  workflows plus a one-off back-fill of the 34 missing issues): REJECTED by the owner; it maintains a view
  nobody uses.

## Consequences

- The existing board can stay or be closed on GitHub; nothing in the repository depends on it.
- Text that says "so the board does not fill up" (CLAUDE.md, `agent-process-automation.md`, ADR-0170) means the
  open-issue list and stays valid.

## Evidence

- The counts above (2026-11-26, `main` @ `4a8a5fff`); the PR that introduced this record.
