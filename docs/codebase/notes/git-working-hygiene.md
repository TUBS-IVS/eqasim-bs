# Git working hygiene: worktrees, staging, rebase, and the shared `.git`

Rules a contributor (human or agent) must follow when working in this repository,
each one written after it cost real time. `CLAUDE.md` carries the short imperative
form; this note carries the incidents and the reasoning, so the rules can be
questioned rather than merely obeyed.

## One worktree per TASK, not per session

`git worktree add -b <branch> .claude/worktrees/<task> origin/main`, one per piece
of work, removed or parked when the work lands.

The tempting shortcut is to reuse the worktree already open, because it is warm
and its caches are primed. On 2026-08-20/21 five branches were pushed through the
single worktree `i240-ownership-grid-1km` — the 100 % run analysis, the escort doc
remainder (#274), the licence references (#322) and two run-manifest branches.
Two distinct failures came directly out of that:

**A data-writing script ran in the main checkout.** The worktree does not contain
the gitignored raw microdata (see [worktree data parity](#worktree-data-parity)
below), so a `cd` fell back to the main repository and
`scripts/extract_srv_kreis_tables.py` rewrote seven committed CSVs there — on the
*user's* branch, while they were working on it. Nothing was lost, but only
because a `git status` happened to be run afterwards. The setup did not prevent
it; a habit did.

**`git commit --amend` rewrote somebody else's merge commit.** With five branches
passing through one worktree, `HEAD` was not where it was assumed to be: a
`git rebase origin/main` had dropped the commit as already-upstream (its content
had been merged meanwhile), leaving `HEAD` on `origin/main`, and the subsequent
`--amend` folded the change into a merge commit, which was then pushed.

## After any rebase, verify `HEAD` before amending

`git rebase` prints `Successfully rebased and updated refs/heads/<branch>` in two
very different situations: when your commits were replayed, and when they were
dropped because their content already exists upstream. In the second case `HEAD`
is whatever the upstream tip is — someone else's commit.

So: after a rebase, run `git log --oneline -1` and confirm the subject is *your*
commit before `--amend`, `reset --soft`, or anything else that rewrites it. If it
is not, create the change again on a fresh branch off `origin/main` rather than
trying to repair the rewritten history.

## Stage explicit paths, never directories

`git add -A` and `git add <dir>` sweep in whatever else is lying around — another
agent's uncommitted files, scratch output, rewritten data files. Stage the paths
you edited, by name, and verify with `git show --stat HEAD` after committing that
exactly those files are in the commit. Files under `eqasim-data/` are ignored by
design and reach a commit only through a deliberate `git add -f` against the
allowlist documented in `.gitignore`.

## The `.git` directory is shared, so local branches are visible

Every worktree shares one object store and one set of refs with the main
checkout. A branch created in a worktree is therefore visible to anyone working
in the repository, who may legitimately push it and open a pull request for it —
this happened on 2026-08-21 with PRs #338 and #340, both raised from branches that
had only ever been committed locally.

Consequences: before assuming a branch is unpushed, check
`git ls-remote --heads origin <branch>` and `gh pr list --head <branch> --state all`.
Before assuming an ADR number or filename is free, check what other worktrees
hold. And never `git switch` in a directory you do not own — `HEAD` and the index
are per-directory, and the other party's work is mid-flight.

## Worktree data parity

A fresh worktree contains only tracked files. Everything gitignored — raw
microdata, caches, run outputs — is absent, which silently changes what a script
can do: a test suite may pass vacuously, and a data script may write to the wrong
place after a fallback `cd`.

When a script must read gitignored inputs, pass them explicitly and send the
output somewhere harmless:

```
python scripts/extract_srv_kreis_tables.py \
    --raw <main-checkout>/eqasim-data/data/braunschweig/srv/srv2023_raw \
    --out-dir <scratch-dir>
```

then copy only the intended new file into the worktree, and diff the regenerated
siblings against the committed ones (EOL-insensitively) to prove the change was
additive. Never let such a script write with its default `--out-dir` from a
worktree.

## Pushes and pull requests

Every push needs the user's explicit confirmation, each time; a prior "yes" does
not authorise the next push. Pull requests go through `git pr`, which pins the
base to the fork `TUBS-IVS/eqasim-bs` — the GitHub web UI defaults to the
`eqasim-org/eqasim-bavaria` upstream, which is never the intended target.

Merging a pull request is the user's action, not the agent's: the permission layer
blocks `gh pr merge`, and that is deliberate rather than an obstacle to work
around.
