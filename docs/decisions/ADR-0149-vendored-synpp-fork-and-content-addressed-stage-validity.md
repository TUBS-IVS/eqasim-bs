# ADR-0149 · 2026-10-21 · A vendored synpp fork and content-addressed stage validity

- **Status:** active; Phase 3 and Phase 4 of #468 are in force; the server acceptance of both phases on a copy of the
  100 % cache is done (`docs/runs/cache-migration-100pct-2026-10-22.yml`, `docs/runs/content-addressed-cache-acceptance-100pct-2026-10-22.yml`).
  Phase 3: semantic tokens, output hashes, fork skeleton, migration. Phase 4 (PR for #468
  Phase 4): early cutoff in the fork's `run()` and `cache_share` coherence by output hash.
  The server acceptance on a copy of the 100 % cache ran on 2026-10-22 (see "Implementation
  status" and "Evidence"); it is a smoke, not a validation. Amended 2026-10-22 by decision 8
  (byte-reproducible jar builds, #474).
- **Numbering:** ADR-0149 is the next free id. Checked on 2026-10-21 across all local and
  remote branches: the highest record anywhere is ADR-0148.
- **Issue:** #468; decision 8: #474. **Relates to:** ADR-0105 (deterministic stage hashes), ADR-0136 (token
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
4. **Validity by output hash and early cutoff (Phase 4, implemented).** A child's validity
   is decided by its parents' output digests (`dependency_output_hashes`) instead of their
   timestamps; a parent that recomputes to an identical digest leaves its children valid
   (spec 4.4). The rules as implemented in `braunschweig/synpp_fork/pipeline.py` `run()` are
   under "Decisions made while implementing Phase 4" (R16-1 to R16-7). A child whose own
   record carries no digest of a re-executed parent recomputes (upstream's behaviour), and the
   fork logs every legacy-rule application per stage. If the registry has an ephemeral stage
   the fork logs a warning and runs the upstream eager rule for the whole run.
5. **`cache_share` coherence by output hash (Phase 4, implemented).** A copied entry is kept
   when its dependencies' recorded output digests equal those of the target metadata (both
   sides carry hashes) or, for two legacy sides, when the timestamps are equal as before;
   mixed pairs are dropped (spec 4.5; `braunschweig.cache_share._incoherence`, `_coherent`,
   `_drop_incoherent_metadata`). A parent that is absent from the target metadata keeps the
   record if the record carries a digest for it (R17-3); only a record without that digest is
   dropped as `missing_parent`. The envelope refresh (R17-1) and the known safe misses (R17-2)
   are listed below.
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
8. **Byte-reproducible jar builds (#474, amended 2026-10-22).** Early cutoff spares the
   consumers of a jar stage only if a rebuild from the same inputs reproduces the stage's
   output hash. Both Maven-built jar stages now do, in any working directory:
   `braunschweig.freight.extraction_tool` (its consumer `braunschweig.freight.extraction`) and
   `matsim.runtime.eqasim` (the `matsim.*` chain). The two measured causes and their fixes:
   1. The jar, source and shade plugins stamped every entry they write with the build time,
      so every rebuild changed the bytes. Fix: a fixed Maven `project.build.outputTimestamp`
      (`OUTPUT_TIMESTAMP`, an arbitrary fixed instant): rendered into the tool pom
      (`braunschweig/freight/extraction_tool.py` `POM_TEMPLATE`) and passed as `-D` on the
      eqasim build's command line (`matsim/runtime/eqasim.py` `REPRODUCIBLE_BUILD_ARGUMENT`),
      so the fork `TUBS-IVS/eqasim-java-bs` needs no change.
   2. Maven's incremental-build state `target/maven-status/maven-compiler-plugin/*/*/inputFiles.lst`
      lists the absolute path of every compiled source file, so the output also differed
      between working directories. It is build bookkeeping no consumer reads; both stage
      modules declare `_OUTPUT_HASH_EXCLUDE = ("*/target/maven-status/*",)`. The jars and the
      rest of the cache directory stay hashed.
   The measurements, including the arithmetic that explains the byte counts of the Phase 4
   acceptance, are in `docs/runs/reproducible-jar-builds-local-2026-10-22.yml`.

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

Decisions made while implementing Phase 4 (controller rulings of the working ledger, folded
into this record):

- **R16-1, in-memory runs.** With `working_directory=None` nothing is cached and every stage
  executes; no output hash is recorded or read there (a stage's digest is used only when the
  record carries one). Early cutoff does not apply, and the run says so instead of warning
  about a disabled early cutoff.
- **R16-2, child-side comparison (replaces the spec's `previous_digest`).** Spec section 4.4
  compares a re-executed parent's new digest with its `previous_digest`. As implemented, a
  child of a re-executed parent `p` is spared **only if the child's own record carries
  `dependency_output_hashes[p]` and it equals the new digest** of `p`. The child's record is
  the exact claim about what it was computed from; the parent's previous digest is only a
  proxy for it, so a child can never be spared on a proxy (spec 4.9: a mismatch is never a
  hit). A child without a recorded digest of `p` recomputes (a legacy record, upstream's
  behaviour, logged) with the reason `parent_output_unrecorded:<p>`; a different digest gives
  `parent_output_changed:<p>`. `braunschweig/synpp_fork/staleness.py` `assess` mirrors this per
  edge (`records_parent_digest`), so the explainer stays exact.
- **R16-3, `EARLY_CUTOFF_ACTIVE` and the STALE-IF count.** `staleness.EARLY_CUTOFF_ACTIVE` is
  `True`: it describes the fork's `run()`. The launcher note in
  `scripts/run_synpp.py` `explain_before_run` ("N further stage(s) will recompute") counts the
  certain recomputes, plus every `STALE-IF` stage only while early cutoff does not apply
  (`EARLY_CUTOFF_ACTIVE` false, or an ephemeral stage in the registry).
- **Eager rule for ephemeral registries.** A registry holding an uncached ephemeral stage runs
  upstream's eager devalidation of every descendant for the whole run (reason
  `ancestor_stale (eager rule)`). `assess` marks every `STALE-IF` stage of such a registry
  `certain_recompute` and sets `eager_rule`; `format_report` says why.
- **`validate()` up front for every cached stage (upstream step 4.8 parity).** Before anything
  executes, `validate()` runs for every non-ephemeral stage, including a stage without a
  `pipeline.json` record and including in-memory runs, so an input-checking `validate()` aborts
  the run at the start and not at the stage's turn hours later (`staleness.assess`, and the
  in-memory branch of `run()`).
- **R16-6, `_OUTPUT_HASH_EXCLUDE` checked before the stage executes.** A malformed value raises
  `TypeError` before execution, not only inside `hash_stage_output` afterwards.
- **R16-7, run summary.** The log ends with the executed stages, the stages spared by early
  cutoff and, per executed stage, its `stale_reasons` (including reasons added during the run);
  `pipeline.json` records `stale_reasons` per executed entry.
- **R17-1, the envelope follows a refreshed record.** A spared child's record changes during a
  run (`dependencies[parent]` is set to the parent's new `updated`; the recorded digest is equal
  by definition and stays) while its `updated` stays.
  `braunschweig.cache_share.track_run` rewrites the `metadata` of an EXISTING valid envelope
  when it differs from the record, keeping the envelope's `environment` (provenance is the
  runtime that created the artifact); it never creates an envelope. An entry without a prior
  envelope stays unknown.
- **R17-2, known safe misses (documented, not changed).** `export` drops a migrated record's
  metadata until a later run under `track_run` has refreshed its envelope; a store written
  before the semantic tokens (md5 module hashes) primes as a miss;
  `braunschweig.freight.extraction_tool` is unshared next to the shared
  `braunschweig.freight.extraction`, so a primed `freight.extraction` record whose tool digest
  differs from the target's tool entry is dropped as incoherent (recompute, safe). In a FRESH
  directory there is no tool entry, so the record is kept (R17-3), the tool jar is rebuilt
  there, and the record is spared or recomputed at run time by the rebuilt jar's digest. Until
  decision 8 (#474) no jar build reproduced its digest, so the run always recomputed
  `braunschweig.freight.extraction` there (server scenario S4 measured the rebuilt jar's changed
  digest; the recompute itself was not exercised), and early cutoff could never spare the
  consumers of a rebuilt jar, the eqasim jar
  (`matsim.runtime.eqasim`) included. Since decision 8 a rebuild from the same inputs with the
  same JDK and Maven plugins reproduces the digest; a record written against a jar built
  before decision 8 carries that build's digest and recomputes once.
- **R17-3, an absent parent is decided at run time by digest.** Spec section 1 criterion 4 and
  section 4.5 ask that "the dependency is in the registry", not in the target metadata. A
  copied record whose parent is absent from the target metadata (a parent that is not shared,
  so absent from a fresh directory) is therefore **kept** if it carries
  `dependency_output_hashes[parent]` for every absent parent. The run re-executes the absent
  parent (`not_in_meta`) and the fork compares the new digest with the one the child recorded
  (R16-2): equal spares the child, different recomputes it, so a mismatch never becomes a
  hit. Before the run the explainer shows such a child as `STALE-IF`, not `HIT` and not
  `RECOMPUTE`. A record without a digest for an absent parent would recompute for certain and
  is dropped as `missing_parent`. `prime` logs the kept-with-absent-parent count as its own
  basis. Present parents keep the rules above (hash match, legacy equal timestamps, mixed pair
  dropped).

## Implementation status

| Part | State |
|---|---|
| Semantic digests and structured tokens in all 30 stages (decision 2) | in force with this record |
| Output hash module (decision 3) | in force; computed by the migration |
| Fork skeleton: `install()`, vendored `get_stage_hash` (semantic) and `run()`, SHA and version guards | in force (Phase 3); with Phase 4 `run()` decides child validity by output hash |
| Explainer and launcher pre-check (decision 6) | in force since PR #469 |
| Migration (decision 7) | in force; mandatory once per existing cache (see Consequences) |
| Early cutoff and validity by output hash in `run()` (decision 4), incl. the Phase 4 rulings | in force (Phase 4, PR for #468 Phase 4); server acceptance done (S2, X2) |
| `cache_share` coherence by output hash, envelope refresh, absent-parent rule (decision 5, R17-1, R17-3) | in force (Phase 4, PR for #468 Phase 4); server acceptance done (S4, X4) |
| Server acceptance of Phase 3 (migration) and Phase 4 on a copy of the 100 % cache (scenarios in "Evidence") | **done 2026-10-22**: `cache-migration-100pct-2026-10-22` and `content-addressed-cache-acceptance-100pct-2026-10-22`; both are smokes |
| Byte-reproducible jar builds (decision 8, #474) | in force with the PR for #474; proven on the workstation and on felix (`reproducible-jar-builds-local-2026-10-22`, `reproducible-jar-builds-felix-2026-10-22`); a run with the consumers cached after a rebuild is open |

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
- **Decision 8: set `project.build.outputTimestamp` in the fork's poms.** Works as well, but
  needs a change and a release in the second repository `TUBS-IVS/eqasim-java-bs`; the
  command-line property reaches every module of the reactor build without one.
- **Decision 8: the source's commit date as the timestamp.** The eqasim stage copies the tree
  without `.git` and the tool build has no repository; a commit date would also change on a
  rebase that leaves the content unchanged.
- **Decision 8: delete `target/maven-status` after the build.** Equivalent for the digest, but
  edits Maven's build tree behind its back; the declared exclude is visible in the stage module
  and goes through the one hashing code path.
- **Decision 8: hash only the jar of a jar stage.** Would also spare the consumers after a
  comment-only Java edit (the copied source tree is part of `matsim.runtime.eqasim`'s output),
  but `_OUTPUT_HASH_EXCLUDE` has no "all but" form and the issue asked only for rebuilds from
  unchanged inputs.

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
  hit). With Phase 4 (R17-1) the next run under `track_run` rewrites the `metadata` of an
  existing valid envelope that differs from the record, so a later EXPLICIT `export`
  (`skip_existing=False`, the CLI default) shares the migrated record again; the automatic
  export after a run uses `skip_existing=True` and never rewrites an entry already in the
  store. An entry without an envelope stays unknown, and a store built before the semantic
  tokens (md5 module hashes) still primes as a miss.
- **Hashing cost.** The migration reads every output file of every `pipeline.json` entry,
  including old config variants that are no longer in the current graph; the migration logs
  files and bytes per entry. The hashing time on the felix copy of the 100 % cache is in `cache-migration-100pct-2026-10-22`
  (one observation, not a benchmark). No full-run overhead of the early cutoff is measured.
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
- **Decision 8 costs one recompute.** It changes the code of both jar stages, so both rebuild
  once, and the first reproducible jar differs from the last non-reproducible one: their
  consumers recompute once (`braunschweig.freight.extraction`, about 3 h per its stage record,
  and the `matsim.*` chain).
- **Limits of decision 8.** Reproducible means the same inputs, the same JDK and the same Maven
  plugins and dependencies; a JDK update can change the class files and then rightly
  recomputes the consumers. The build's time zone does not matter (a UTC build gave the same
  tool jar as a Europe/Berlin build); the platform does, by line endings, so the builds are
  reproducible per platform, and a cache moved between Windows and Linux would see a changed jar
  digest and recompute the consumers (safe). A Java source edit changes `matsim.runtime.eqasim`'s output even
  when the jar bytes stay the same (a comment-only edit), because its cache directory holds the
  copied source tree.

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

Acceptance on a real cache: **server acceptance on felix, done 2026-10-22**, on a `cp -a` copy of the 100 %
cache (never the original), recorded in `docs/runs/cache-migration-100pct-2026-10-22.yml`. The acceptance criterion was
set to A == B (own-reason stale set under the legacy revision 205a0fb7 versus under the branch head after
the migration), because the 100 % cache dates from 2026-08-21 and is older than that revision, so entries
built by older code are genuinely stale. Result: the two stale sets are identical (counts, reasons and the
migration counters are in the manifest). So "zero recompute after migration" holds in the form "the migration
recomputes nothing the old code would not", not as "0 stale". Many entries stay legacy because their stored
module hash matches neither the current source nor the legacy revision. Wording defect found on this run: the
explainer's legacy md5 warning also fired after a migration; reworded in the same PR (the launcher keeps
logging that line at ERROR, see `scripts/run_synpp.py` `explain_before_run`).

Phase 4 tests committed on the branch (synthetic pipelines in `tmp_path`, no scientific claim;
they pin the mechanics):

- `tests/test_synpp_fork_early_cutoff.py` (real synpp runs under the fork):
  `test_parent_token_change_with_identical_output_spares_the_child`,
  `test_parent_output_change_devalidates_the_child_without_renaming_it` (the parent's output
  depends on a payload file, not on a config key, so only the run-time rule can devalidate),
  `test_a_two_parent_child_executes_when_any_parent_output_changed`,
  `test_early_cutoff_keeps_a_whole_chain_cached_and_says_so`,
  `test_a_devalidated_middle_stage_with_unchanged_output_cuts_the_cascade_off`,
  `test_a_legacy_child_of_a_re_executed_parent_recomputes_and_is_logged`,
  `test_the_child_s_own_recorded_digest_decides_not_the_parent_s_previous_one` (R16-2),
  `test_an_ephemeral_registry_runs_upstream_s_eager_rule_for_the_whole_run`,
  `test_assess_and_run_agree_on_whether_the_eager_rule_applies`,
  `test_a_validate_that_raises_for_a_new_stage_aborts_the_run_before_any_stage_executes`,
  `test_an_in_memory_run_validates_every_stage_before_anything_executes`,
  `test_a_malformed_output_exclusion_fails_before_any_stage_executes`.
- `tests/synpp_pristine.py` is the one shared definition of synpp 1.6.2's pristine `run()`; the
  upstream-differential tests (`tests/test_synpp_fork_staleness.py`) compare `assess` with it,
  and the fork assertions compare the executed set with the own-stale set plus the `STALE-IF`
  stages whose ancestor output really changed.
- `tests/test_cache_share_synpp.py`: `test_prime_from_another_run_is_a_hit_when_output_hashes_match`
  (the prime-across-runs test), `test_prime_drops_a_child_whose_parent_output_differs_and_logs_the_reason`,
  `test_drop_counts_each_incoherence_reason`, `test_prime_logs_how_many_copied_records_were_kept_and_why`,
  `test_envelope_follows_the_record_a_spared_child_refreshes` (R17-1),
  `test_envelope_refresh_never_invents_provenance_for_unknown_entries`,
  `test_primed_child_with_an_unshared_parent_is_decided_by_digest_equal_output` and
  `test_primed_child_with_an_unshared_parent_recomputes_when_the_parent_output_differs` (R17-3),
  `test_record_without_a_digest_for_an_absent_parent_is_dropped_as_missing_parent`,
  `test_the_explainer_shows_a_primed_child_with_an_unshared_parent_as_stale_if`,
  `test_a_later_run_refreshes_the_envelope_of_a_migrated_record`, and the scenario
  `older_target_parent_other_output` of `test_shared_dependency_chain`.

Phase 4 server scenarios, **run on 2026-10-22** on the migrated copy of the 100 % cache and recorded in
`docs/runs/content-addressed-cache-acceptance-100pct-2026-10-22.yml` (classification `smoke`, no scientific claim). The scenario text below is
the plan as written; the results and the deviations are listed after it. (1) a comment edit in a stage helper, explainer shows
nothing stale besides run targets; (2) a behaviour-neutral edit of a helper body, explainer shows
the one directly affected stage stale and its descendants `STALE-IF`, the run logs
`early cutoff kept N stages cached`; (3) a copied Java tree for `eqasim_source_path`, no freight
stage stale; (4) a fresh working directory primed from `cache_share_store`: a primed entry whose
parents are all shared is `HIT` with no `legacy timestamp rule` lines for fork-written entries; a
primed entry with an unshared parent (R17-3) is `STALE-IF` before the run and is spared or
recomputed at run time by the parent's digest, and `braunschweig.freight.extraction` is expected
to recompute because the rebuilt `braunschweig.freight.extraction_tool` jar has other bytes
(R17-2). No statement about hit rates, saved hours or run times on a real cache is made anywhere in the
documentation; the manifest records mechanics only.

Results of the Phase 4 scenarios (`content-addressed-cache-acceptance-100pct-2026-10-22`; the numbers, the stage choice, the harness rulings and the
failed first attempt are in the manifest, not repeated here):

- Comment edits and a copied Java tree change nothing (S1, S3); the explainer marks a body edit as stale for
  exactly the edited stage with its children `STALE-IF` (S2).
- With a driver that does not force run targets (`rerun_required=False`) the early cutoff kept the unchanged
  children cached, on a trivial 4-byte parent output (X2, X4) and on a 20 MB migrated parent output (X5). The
  launcher always re-executes run targets, so production gains on non-target stages only.
- A prime of a fresh directory decided an absent parent by digest at run time (S4, X4: R17-3).
- Not exercised: the stage chain at scale (`escort.py`/chainsolver are `not_in_meta` in the copy), the primed
  entry with all parents shared, and the `braunschweig.freight.extraction` recompute (R17-2).
- **Measured evidence for the jar limit (R17-2).** Rebuilds of the eqasim jar and tool jar stages from identical
  inputs did not reproduce their output hash (byte counts in the manifest), so consumers of a rebuilt jar
  recompute, as stated in the known limits; reproducible jar builds were left to a follow-up, now decision 8.

Decision 8 (#474), evidence (`docs/runs/reproducible-jar-builds-local-2026-10-22.yml` on the Windows workstation
and `docs/runs/reproducible-jar-builds-felix-2026-10-22.yml` on felix; wiring proofs, not validations):

- Tests: `tests/jar_reproducibility.py` builds a jar stage twice through its `execute()` in two working
  directories of different path lengths and compares `hash_stage_output` with the stage's exclusions; the
  gated `test_execute_builds_a_byte_reproducible_jar` in `tests/test_freight_extraction_tool.py`
  (`EQASIM_BS_FREIGHT_TOOL_BUILD=1`) and `tests/test_eqasim_runtime_token.py` (`EQASIM_BS_EQASIM_SOURCE`) failed
  without the fix and pass with it. Always-on guards: `test_render_pom_fixes_the_output_timestamp`,
  `test_execute_passes_the_fixed_output_timestamp_to_maven` and `tests/test_jar_stage_output_hash.py` (the
  exclusion never matches a jar or a class file).
- The byte counts of the Phase 4 acceptance are explained by cause 2: the copy's working directory
  `cache_bs_100pct_allfeat_popsim` is 12 characters longer than `cache_i468p4_fresh`, and the fork at `4c1af4a`
  has 499 Java source files in `core` and `braunschweig` (main and test): 499 x 12 = 5988 bytes = 217807431 -
  217801443 for the eqasim stage, and 1 x 12 = 12 bytes for the tool's single source file.
- On felix (Maven 3.6.3, JDK 25.0.3+9, fork `ab49a956`) both gated tests pass as well. A Windows and a Linux build
  of the tool jar differ only in the line endings of the two text files Maven stores under `META-INF/maven/`
  (`pom.xml`, `pom.properties`); every class file and timestamp is equal.
- Not yet shown: a rebuild that leaves the consumers cached in a real run (issue #474, "Done when").
