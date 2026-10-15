# ADR-0143 · 2026-10-15 · Topic rules move from CLAUDE.md to path-scoped `.claude/rules/`

- **Status:** accepted (maintainer request, 2026-10-15)
- **Numbering:** ADR-0143 is the next free id. Checked on 2026-10-15 across all remote
  branches: the highest record anywhere is ADR-0142.
- **Issue:** none (agent-tooling hygiene change, like `chore/git-working-hygiene`).

## Context

- `CLAUDE.md` is injected into every Claude Code session and every turn. On `main` at
  `ce4c1f42` it was 32,205 bytes (git blob, LF). The maintainer observed that starting a
  session already consumes a noticeable share of the usage limit; `CLAUDE.md` is the
  largest repository-side part of that fixed prefix.
- Most of its sections are only relevant while code, configs or tests are being written
  (MATSim/eqasim and Java style, configuration, units, output tables, geospatial,
  logging, error handling, review checklist, test design). Sessions that only read runs,
  triage issues, write ADRs or answer questions paid for them anyway.
- Several always-loaded paragraphs restated facts that already have an owner:
  the truth-hierarchy list (`docs/DOCUMENTATION_GOVERNANCE.md`), the verification gate
  (`docs/codebase/TESTING.md`), the issue and PR format
  (`docs/codebase/notes/issue-and-pr-conventions.md`) and the git-hygiene incidents
  (`docs/codebase/notes/git-working-hygiene.md`), against the shape rule "no fact lives in
  two files".
- Claude Code loading semantics, checked on 2026-10-15 against the official memory
  documentation (https://code.claude.com/docs/en/memory):
  - `.claude/rules/**/*.md` files are discovered recursively; a rule without `paths:`
    frontmatter loads at launch like `CLAUDE.md`.
  - A rule with `paths:` (YAML list of globs, brace expansion allowed) loads when Claude
    uses Read, Write or Edit on a matching file, or reads one with a single-file `cat` /
    `head`.
  - `@path` imports in `CLAUDE.md` are expanded at launch and "don't reduce its context
    cost"; a plain Markdown link is not loaded by any documented mechanism.
  - The documentation recommends "under 200 lines per CLAUDE.md file".

## Decision

1. `CLAUDE.md` keeps only what every session needs: project context, documentation
   governance and the shape rule, language policy, the verification-gate pointer,
   fallback transparency, no invented reference values, research reporting, git and
   working hygiene, chat-answer behaviour and the non-negotiable rules.
2. Code-related sections move verbatim, headings included, to
   `.claude/rules/coding-standards.md` (`paths`: `**/*.py`, `**/*.java`, `**/*.ipynb`,
   `**/pom.xml`, `configs/**/*.yml`). The code-modification paragraph of "Preferred
   response behavior for Claude" joins its "Code changes" section.
3. The test-design rules of ADR-0134 move verbatim to `.claude/rules/tests.md` (`paths`:
   `tests/**`, `**/test_*.py`, `**/*_test.py`, `**/conftest.py`, `**/src/test/**`,
   `pytest.ini`, `scripts/run_tests.py`). This relocates ADR-0134 decision 1; the rules
   themselves are unchanged. `AGENTS.md` and `docs/codebase/TESTING.md` link to the new file.
4. Paragraphs that restated an owned fact become one-line pointers to their owner.
5. `.gitignore` keeps `.claude/` machine-local except `.claude/rules/`, which is tracked
   so every checkout, worktree and the server see the same rules.
6. Code comments citing `CLAUDE.md "<heading>"` are left unchanged: the headings were moved
   verbatim, and editing comments in synpp stage modules would change their stage hashes
   (ADR-0136) and invalidate caches for no behavioural reason.

## Rejected alternatives

- **`@path` imports from `CLAUDE.md`:** imported files load at launch, so the context cost
  stays the same.
- **Plain links from `CLAUDE.md` to `docs/codebase/*.md`:** nothing loads a linked file
  automatically, so a rule would apply only when the agent happens to open it.
- **Shortening the rules themselves:** contradicts the `CLAUDE.md` rule that nothing is
  shortened just to hit a number; content was moved or deduplicated, not dropped.
- **A separate Java/MATSim rule file:** the repository tracks no production Java (Java
  lives in the eqasim-java fork), and the MATSim/eqasim sections also govern the Python
  pipeline; a split would load the same files under the same globs.

## Consequences

- `CLAUDE.md` shrinks from 32,205 to about 14.5 kB (sizes recorded in the PR). Sessions
  that touch no code, config or test file no longer load the topic rules.
- A session that reads any Python file loads `coding-standards.md` (about 11.5 kB) on
  first touch, so coding sessions save only the deduplicated part.
- A rule loads on the first matching Read/Write/Edit, not before: an agent that creates a
  new file without reading a matching one first sees the rule only after that write.
- A new rule belongs in `CLAUDE.md` only if every session needs it; otherwise in a
  path-scoped rule.

## Evidence

- `CLAUDE.md`, `.claude/rules/coding-standards.md`, `.claude/rules/tests.md`, `.gitignore`
  on branch `chore/claude-md-path-scoped-rules`.
- Claude Code memory documentation, https://code.claude.com/docs/en/memory (read 2026-10-15).
