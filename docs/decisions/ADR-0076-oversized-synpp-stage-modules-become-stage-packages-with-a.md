# ADR-0076 — Oversized synpp stage modules become stage PACKAGES with a `validate()` source token (2026-08-13, PR #268 MERGED)

- **Context:** `braunschweig/synthesis/locations/secondary_chainsolvers.py` had grown to 5327
  lines (~70 functions, 14 sections) — far outside the small-focused-modules convention and
  hard to review. Naive extraction into helper modules would have armed the known synpp cache
  trap: `get_stage_hash` = md5 over ONLY the stage module's own source, so helper-only changes
  silently reuse stale cached stage output on partial reruns.
- **Decision:** (1) Convert the stage file into a **package with the same synpp module path**
  (`secondary_chainsolvers/__init__.py` = stage; `inspect.getsource` of a package yields its
  `__init__`), keeping config aliases and every external import working via a full re-export
  facade. (2) Split sections into 13 single-responsibility submodules (largest 800 lines),
  one pure-move commit each, `git diff --color-moved` reviewed. (3) Close the cache trap with
  the synpp 1.6.2 module-level **`validate(context)` hook** (token stored with the cached
  output and compared per run, synpp `pipeline.py` 779–782): both `secondary_chainsolvers`
  and `secondary_candidates` return md5 over all `_HELPER_MODULES` sources, so helper-only
  edits recompute exactly like stage-file edits. (4) Mutable worker globals (`_WORKER_*`)
  are delegated live via module-level `__getattr__` (PEP 562) — a static re-export would
  freeze the import-time value. (5) `execute()` decomposed ~600→~360 lines into named steps
  with identical call and RNG-draw order. This is the template for the remaining oversized
  modules (issue #267).
- **Consequences:** Behavior-preserving (server suite on the PR head: 3585 passed / 30
  skipped / 0 failed; local full-suite failure sets identical to main, deltas explained by
  worktree data parity). The changed stage hash devalidates the chainsolver stage +
  downstream once on the next partial rerun; old cache entries stay keyed under the old
  hash, so a rollback restores validity. One source-inspection test moved with its block
  (guarded-writer invariant unchanged).

> **Live status note.** This log is the retrospective *why*. For the current state of every feature
> (merged / flag-on / infra-only / open PR), always defer to [PROJECT_STATUS.md](../PROJECT_STATUS.md)
> and `git log`; where this log and those disagree, `CLAUDE.md` and git win.
