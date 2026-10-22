# ADR-0149 · 2026-10-21 · A vendored synpp fork and content-addressed stage validity

- **Status:** active, partly implemented. Phase 3 of #468 is in force with this record
  (semantic tokens, output hashes, fork skeleton, migration). Phase 4 (early cutoff in the
  fork's `run()`, `cache_share` coherence by output hash) is decided here but not
  implemented yet; until it lands the upstream timestamp rule still decides validity (see
  "Implementation status").
- **Numbering:** ADR-0149 is the next free id. Checked on 2026-10-21 across all local and
  remote branches: the highest record anywhere is ADR-0148.
- **Issue:** #468. **Relates to:** ADR-0105 (deterministic stage hashes), ADR-0136 (token
  covers the import closure), ADR-0147, ADR-0148 (freight tool jar, Phase 2 of #468).
- **Design:** `docs/superpowers/specs/2026-10-16-content-addressed-stage-cache-design.md`
  (local, gitignored) sections 2 to 7.

## Context

After a change, stages recompute that did not need to, because synpp 1.6.2 decides
validity from raw text and timestamps (spec section 2):

- **Raw-text hashes.** synpp's `get_stage_hash` is the md5 of the stage module's raw text,
  and the project's `validate()` tokens (ADR-0136) hash the raw text of whole import
  closures. A comment or docstring edit in any hashed module changes the hash. Closure
  sizes measured with `scripts/audit_synpp_helper_hash.py` on 2026-10-16: popsim 81
  modules, analysis_suite 85, chainsolver 56. A five-line comment in `escort.py` caused a
  7.5 h re-execution (issue #344, recorded in the maintainer's memory notes; no run
  manifest).
- **Timestamp cascade.** synpp's `run()` compares `meta[parent]["updated"]` with the time
  the child saw (step 4.6) and devalidates every descendant of a stale stage before
  anything executes (step 4.9). There is no early cutoff: a parent that recomputes to
  byte-identical output still recomputes all its descendants. Evidence in run manifests:
  `docs/runs/i409-passive-joint-surrogate-smoke-03101-2026-09-17.yml` (19 stages for five
  added keys), `docs/runs/freight-boundary-links-ab-1pct-2026-10-16.yml` (OFF arm, 42
  stages after one added popsim key), `docs/runs/participation-universe-controls-arm4-100pct-2026-09-08.yml`
  (popsim 6 h 44 plus everything after).
- **Primed caches never validate.** `braunschweig.cache_share._drop_incoherent_metadata`
  requires exact timestamp equality between a copied entry and its parents. That never
  holds across runs, so a primed entry fails steps 4.2 and 4.6 (memory note of the #436
  run, 2026-10-12: metadata kept for 0 of 112 copied entries, 91 stages devalidated,
  PopSim and freight among them).
- **Config keys rename downstream caches.** `hash_name` hashes every requested and every
  propagated config key (ADR-0105), so a new upstream key renames downstream cache files.
  This is accepted as a limit (content-addressed lookup, option C of the spec, is out of
  scope).
- The Java side of the same problem (an mtime-keyed eqasim jar, the freight extraction
  depending on it) is ADR-0148 and is not repeated here.

## Decision

**Option B of the spec: an in-repo vendored fork of two synpp functions plus project-side
changes.** synpp stays pinned to 1.6.2 (`environment.yml`).

1. **Vendored fork `braunschweig/synpp_fork/`.** `install()` (in
   `braunschweig/synpp_fork/__init__.py`) first calls `braunschweig.synpp_deterministic.install()`
   (ADR-0105 and its version guard stay), then replaces `synpp.pipeline.get_stage_hash` and
   `synpp.pipeline.run` (and the re-exported `synpp.run`) by the copies in
   `braunschweig/synpp_fork/pipeline.py`. The copy is pinned by `UPSTREAM_PIPELINE_SHA256`;
   each patched hunk is marked `# PATCH <name>` and listed in `PATCHES`. `scripts/run_synpp.py`
   and `braunschweig/documentation/dag.py` call `synpp_fork.install()`. A plain
   `python -m synpp` run carries none of this and stays unsupported.
2. **Semantic (AST) digests.** `braunschweig/synpp_tokens.py`: `semantic_module_digest` is
   the sha256 of `ast.dump` of the docstring-free syntax tree. Comments, blank lines,
   formatting and line numbers never reach the AST; constants, decorators, annotations,
   defaults and the helper tuples stay in. The fork's `get_stage_hash` uses it for the stage
   module hash. `source_token` builds the structured token
   `{"format": 2, "python": "3.10", "modules": {dotted_name: digest}}` for every
   source-hashing stage; an import or source failure raises `RuntimeError` naming the module.
   `"python"` is the Python minor version, because `ast.dump` output is version specific:
   a Python minor upgrade is a deliberate one-time recompute. `TOKEN_FORMAT` is bumped with
   any change of the digest algorithm; `tests/test_synpp_tokens.py` pins the digest of a
   fixed snippet so that a change cannot go unnoticed.
3. **Output content hash.** `braunschweig/synpp_fork/output_hash.py` `hash_stage_output`
   returns `{"format": 1, "digest", "pickle", "files", "n_files", "bytes"}`: sha256 of the
   pickle and of the sorted per-file manifest of the stage's cache directory, read in 1 MiB
   chunks, minus the files matched by the stage's optional `_OUTPUT_HASH_EXCLUDE` globs.
   Anything unreadable, a missing artifact or a symlink inside the cache directory raises
   (never "no hash"). A non-deterministic pickle can only produce "changed", never a false
   hit.
4. **Validity by output hash and early cutoff (decided; Phase 4).** A child's validity is
   decided by its parents' output digests (`dependency_output_hashes`) instead of their
   timestamps; a parent that recomputes to an identical digest leaves its children valid
   (spec 4.4). Entries without the `fork` marker keep the legacy timestamp rule, and the
   fork logs every legacy-rule application per stage. If the registry has an ephemeral
   stage the fork logs a warning and runs the upstream eager rule for the whole run.
5. **`cache_share` coherence by output hash (decided; Phase 4).** A copied entry is kept
   when its dependencies' recorded output digests equal those of the target metadata (both
   sides carry hashes) or, for two legacy sides, when the timestamps are equal as today;
   mixed pairs are dropped (spec 4.5).
6. **Stale-stage explainer.** `scripts/explain_stale_stages.py` and the launcher
   pre-check (Phase 1, PR #469) report per stage `HIT`, `STALE <reasons>` and so on, and
   gate a run on `--expect` / `synpp_expected_stale_stages`. Both read the same
   `braunschweig/synpp_fork/staleness.py` `assess`.
7. **Migration of existing caches.** `scripts/migrate_cache_metadata.py` rewrites a
   working directory's `pipeline.json` without recomputing anything. Per entry:
   1. module hash: the stored md5 is replaced by the semantic digest only if it is proven
      (see Ruling G below);
   2. token: replaced by the structured token only if proven;
   3. `output_hash`: computed from the files on disk for every entry whose artifacts exist;
   4. `dependency_output_hashes`: filled from the parents' digests, only where the
      timestamp rule says the child already saw the parent's current output (otherwise the
      child stays stale, the safe direction);
   5. `fork: {"format": 1}` is set only where steps 1 to 3 passed or did not apply.
   Every other entry is left byte-for-byte unchanged and counted `kept_legacy`, with one log
   line naming the reason. The rewrite is atomic (`pipeline.json.new`, backup
   `pipeline.json.bk`). An entry that already carries `fork` or `output_hash` is left
   alone (re-running is idempotent).

Decisions made while implementing Phase 3 (maintainer rulings in the working ledger, folded
into this record):

- **Ruling A, structured composite tokens.** Stages whose token also covers non-source
  input (five stages: `braunschweig.parking.zones_stage`, `braunschweig.data.vrb.zone_polygons`,
  `braunschweig.matsim.simulation.prepare`, `braunschweig.synthesis.commute_day.home_office_donors_stage`,
  `braunschweig.synthesis.outside_tour_modes.stage`) return the structured token plus their
  non-source part under its own key (for example `"files"`). `staleness.token_difference`
  names a differing non-module top-level key instead of "format".
- **Ruling E, scope of the refactor.** All 30 source-hashing stages of the audit
  (`scripts/audit_synpp_helper_hash.py`, measured 2026-10-21 at base `205a0fb7`) now call
  `synpp_tokens.source_token`; the spec's "26" was the count of 2026-10-16.
  `tests/test_token_refactor_coverage.py` takes its stage set from the audit and pins the count
  at 30, so a stage added or dropped silently fails the test. The audit recognises
  `synpp_tokens.source_token` from the AST and one in-module marker
  (`_SYNPP_TOKEN_EXEMPTION` in `braunschweig/synpp_tokens.py`) replaces a second exemption
  mechanism.
- **Ruling F, fork marker only after a proof.** See migration step 5.
- **Ruling G, the migration proves against a pre-refactor revision.** Refactoring the 30
  stage modules changed their text, so a stored md5 no longer equals the md5 of the current
  file; proving against the current text would leave exactly the 30 most expensive stages
  stale. The migration therefore proves an entry against `LEGACY_SOURCE_REV =
  205a0fb7152ea75c2ac48f2afc0b05b43e99ce15` (origin/main before the refactor; overridable
  with `--legacy-source-rev`), read with `git show`. Module hash: stored md5 equals md5 of the
  current text, or equals md5 of the text at that revision and the module is
  refactor-equivalent, that is, its semantic digest at the revision and now are equal after
  removing the module-level `validate` function and the imports of exactly `hashlib`,
  `inspect`, `importlib` and `braunschweig.synpp_tokens`. Token: the legacy `validate()`
  of the revision is replayed (executed in a scratch namespace in which `inspect.getsource`
  returns the revision's text for every module that differs) and compared with the stored
  string; on equality and refactor-equivalence of every module the replay hashed, the current
  token is stored. Entries built from any other revision are reported as mismatch (genuinely
  stale, spec-conform). The migration therefore needs git with the revision available.
- **Ruling H, loud hint.** `staleness.format_report` prints a warning when stored module
  hashes are 32-hex legacy md5 values and names the migration script.

## Implementation status

| Part | State |
|---|---|
| Semantic digests and structured tokens in all 30 stages (decision 2) | in force with this record |
| Output hash module (decision 3) | in force; computed by the migration |
| Fork skeleton: `install()`, vendored `get_stage_hash` (semantic) and `run()`, SHA and version guards | in force; `run()` is still the upstream loop, so only `get_stage_hash` and the deterministic propagation differ from synpp 1.6.2 |
| Explainer and launcher pre-check (decision 6) | in force since PR #469 |
| Migration (decision 7) | in force; mandatory once per existing cache (see Consequences) |
| Early cutoff and validity by output hash in `run()` (decision 4) | **pending (Phase 4)** |
| `cache_share` coherence by output hash (decision 5) | **pending (Phase 4)** |

## Relation to ADR-0105 and ADR-0136

Both stay in force. ADR-0105's patch of `process_stages` (deterministic implicit-config
propagation) is installed first by the fork and is part of every stage hash. ADR-0136's
closure gate (`scripts/audit_synpp_helper_hash.py` and its tests) still checks that every
stage token covers its import closure; only the digest inside the token changes from raw
text to the semantic digest, and the audit recognises the new call. This record replaces
the timestamp-based and raw-text validity assumptions of those two ADRs (a comment is not a
change; a parent's modification time is not a content signal) from the phase in which each
part lands (see Implementation status).

## Rejected alternatives

(spec section 7)

- **Keep synpp untouched (option A).** Fixes the raw-text hashes only; the cascade and the
  prime problem are properties of `run()`.
- **Content-addressed cache keys now (option C).** Would also solve the renaming by new
  config keys, but changes the cache layout and every tool that globs `<stage>__*.p`;
  deferred until B has run in production. The metadata is laid out so C can be added later.
- **A real fork repository `TUBS-IVS/synpp`.** Cleaner packaging, but a second repository, CI
  and a server environment rebuild for two functions; revisit if the fork grows.
- **Base the fork on synpp 1.7.3.** Nothing changes upstream in cache validation (spec
  section 2, checked 2026-10-16); the deterministic patch tracks 1.6.2 internals; an upgrade
  is its own task.
- **Bytecode hashing instead of the AST.** `co_linetable` makes comment insertions change
  the digest; stripping it recursively is fiddlier and gains nothing.
- **Runtime-traced executed-function sets.** More precise than the static closure but needs a
  tracing harness in production code and a learning run per stage; kept as a future option.
- **Hash only the freight-relevant classes of the eqasim jar.** Replaced by the tool jar
  (ADR-0148).

## Consequences

- **Migration is mandatory once per existing cache, after merging this change and before
  the first launch.** Without it every stage reads `module_hash_changed` (md5 replaced by
  the AST digest) and recomputes, PopulationSim and the MATSim chain included. Run
  `scripts/migrate_cache_metadata.py` with `--dry-run`, then for real, then
  `scripts/explain_stale_stages.py ... --expect ""` (README "Reusing an existing cache").
  The migration needs git and the revision `205a0fb7` (`git fetch origin main`).
- **Migrate with the production interpreter** (the pinned `eqasim` environment, Python 3.10):
  the module hash (`ast.dump`) and the token's `python` field depend on the Python minor
  version; a migration under another minor makes every entry recompute (safe but costly).
- **A shared store built before this change stays a miss.** `braunschweig.cache_share.export`
  writes an entry's metadata sidecar only when the envelope written by `track_run` at run time
  equals the current `pipeline.json` record; after migration the record differs, so a migrated
  entry exports without metadata and `prime` treats it as a miss (recompute, never a wrong
  hit). Re-exporting after the migration does not change this, because `track_run` rewrites an
  envelope only for entries a run recomputed. Refreshing shared envelopes by output hash is
  Phase 4 work (decision 5).
- **Hashing cost.** The migration reads every output file of every `pipeline.json` entry,
  including old config variants that are no longer in the current graph; the migration logs
  files and bytes per entry. No full-cache run time is measured yet (acceptance pending).
- **Python minor upgrade = one deliberate recompute** of every source-hashing stage (the
  token records the minor version); the explainer shows it as a token difference.
- **Fork re-base obligation.** When synpp is upgraded, `braunschweig/synpp_fork/pipeline.py`
  must be re-copied from the new `synpp/pipeline.py`, `UPSTREAM_PIPELINE_SHA256` updated and
  the patches re-applied (`docs/codebase/notes/synpp-fork.md`). `install()` logs a warning
  when the installed file differs from the pinned build.
- **A change of the digest algorithm bumps `TOKEN_FORMAT`** together with the pinned golden
  digest, which invalidates every stored token on purpose.
- **Entries of other revisions stay legacy.** An entry that cannot be proven (built from
  another revision, different Python, or edited code) is kept legacy and decided by the
  timestamp rule; it recomputes if its code really changed.
- **Stated limits (spec section 1).** A stage whose own `configure()` gains a config key
  still recomputes; a new upstream config key still renames the downstream cache files.
  A stage that reads paths outside its parents' cache directories is not covered by output
  hashes, as today.

## Evidence

Tests committed with the change (no scientific claim; they pin the mechanics):

- `tests/test_synpp_tokens.py`: comment, docstring and formatting edits keep the digest,
  body and constant edits change it, golden digest pin
  (`test_golden_digest_of_a_fixed_snippet_is_pinned`), exact token shape, missing deferred
  module and source-less helper raise naming the module, `legacy_source_token` equals the old
  md5 loop.
- `tests/test_synpp_fork_output_hash.py`: determinism and file counts, exclude globs,
  missing artifacts, unreadable files and directories raise, symlinks inside a cache
  directory raise, a symlinked cache directory hashes like the real one, global sort order.
- `tests/test_synpp_fork_install.py`: pinned upstream SHA, `install()` patches `run` and
  `get_stage_hash`, refuses an unsupported synpp version and patches nothing, warns on a
  drifted build, a comment edit in a stage module does not devalidate it.
- `tests/test_token_refactor_coverage.py`: the audit's source-hashing set is exactly 30,
  each calls the shared builder, none keeps a private md5 or source loop, every `validate()`
  reads every helper tuple its module defines, and the legacy token of every pure-loop
  stage is still reproducible.
- `tests/test_migrate_cache_metadata.py`: the migration proofs (module hash, replayed token,
  refactor equivalence), a migrated legacy cache executes nothing under the fork's `run()`,
  composite and off-registry tokens, parent digests filled only where the child saw the
  parent's current output, missing git revision raises, the explainer's migration hint,
  dry run writes nothing and `write_meta` is atomic and keeps a backup.
- Phase 1 tests of the explainer and its launcher gate: `tests/test_synpp_fork_staleness.py`
  (differential test against `synpp.run`), `tests/test_explain_stale_stages.py`,
  `tests/test_run_synpp_explain.py`, `tests/test_synpp_fork_registry.py`.

Measured on 2026-10-21 during implementation (scratch checks, not committed as tests):

- Audit: 30 source-hashing stages at base `205a0fb7`.
- The legacy tokens of all 30 stages were reproduced by replaying revision `205a0fb7`;
  refactor-equivalence of the 30 stage modules against that revision holds (30 of 30). The
  reviewer of the token refactor (Task 13) re-computed the equivalence independently.
- Real stage registry (1 % and 100 % configs) with synthetic legacy `pipeline.json` records:
  107 of 107 entries migrated. State: **synthetic**, not a real cache.

Acceptance on a real cache: **server acceptance on felix: pending (maintainer go).** It
is a `cp -a` copy of the 100 % cache, never the original; a run manifest
`docs/runs/cache-migration-100pct-<date>.yml` records it when done. Until then the claim
"zero recompute after migration" is a design property proven on synthetic records and
the replay checks above, not an observation on a production cache.
