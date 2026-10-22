# The `braunschweig/synpp_fork` package: explainer, vendored synpp functions and cache migration

One note for the whole package (ADR-0149, issue #468; design spec
`docs/superpowers/specs/2026-10-16-content-addressed-stage-cache-design.md`, local and
gitignored). It describes the package after Phase 4: the explainer (Phase 1), the semantic
tokens, the output hash, the fork skeleton and the cache migration (Phase 3), and the content-addressed
validity of Phase 4: early cutoff in the fork's `run()` (child validity by parent output hash) and
`cache_share` coherence by output hash. `staleness.EARLY_CUTOFF_ACTIVE` is `True`. The server
acceptance of Phase 4 on a copy of the 100 % cache is **pending**; the behaviour below is proven by
synthetic tests only (`tests/test_synpp_fork_early_cutoff.py`, `tests/test_cache_share_synpp.py`).

## What the package owns today

| Module | Responsibility |
|---|---|
| `braunschweig/synpp_fork/__init__.py` (`install`, `PATCHES`, `UPSTREAM_PIPELINE_SHA256`) | Installs the project's synpp patches before a registry is built: first `braunschweig.synpp_deterministic.install()` (deterministic implicit-config propagation, ADR-0105, with its synpp version guard), then replaces `synpp.pipeline.get_stage_hash`, `synpp.pipeline.run` and the re-exported `synpp.run` by the copies in `pipeline.py`. Logs a warning when the installed `synpp/pipeline.py` differs from the pinned SHA. |
| `braunschweig/synpp_fork/pipeline.py` (`get_stage_hash`, `run`) | The vendored copy of synpp 1.6.2's `run()` and `get_stage_hash()`. Three patches, each marked `# PATCH <name>` in the source and named in `PATCHES`: `deterministic-propagation` (`process_stages` from `braunschweig.synpp_deterministic`), `semantic-module-hash` (`get_stage_hash` digests the AST via `braunschweig.synpp_tokens.semantic_module_digest`) and `content-addressed-validity` (the `run()` loop, see "The `run()` loop under the fork"). |
| `braunschweig/synpp_fork/output_hash.py` (`hash_stage_output`, `OUTPUT_HASH_FORMAT`) | Content hash of a stage's output (pickle plus every file of its cache directory). Used by the migration and by the fork's `run()` after every executed stage. |
| `braunschweig/synpp_tokens.py` (`semantic_module_digest`, `source_token`, `legacy_source_token`, `TOKEN_FORMAT`) | Semantic source digests and the structured `validate()` token of the 30 source-hashing stages. Lives outside the package because the stage modules import it. |
| `braunschweig/synpp_fork/registry.py` (`load_settings`, `build_registry`) | Composes the base config (plus an optional overlay through `braunschweig.config_compose`), validates it (empty config or missing `run` list raise `ValueError` naming the file) and builds the synpp stage registry read-only, with the production stage hashes. |
| `braunschweig/synpp_fork/metadata.py` (`read_meta`, `write_meta`) | Reads and writes synpp's `pipeline.json` with synpp's own backup convention. |
| `braunschweig/synpp_fork/staleness.py` (`assess`, `format_report`, `unexpected`) | Explains, per stage, whether the cache entry will be reused and why not. Replicates synpp 1.6.2's own devalidation steps and is the function the fork's `run()` calls for the stages that are stale for their own reasons, so the pre-run report and the run cannot disagree. Differential tests (`tests/test_synpp_fork_staleness.py`) compare it with synpp 1.6.2's pristine `run()` (`tests/synpp_pristine.py`) and with the fork. |

`scripts/migrate_cache_metadata.py` (see "Migrating a cache") writes `output_hash` and
`dependency_output_hashes` for existing entries; the fork's `run()` writes them for every stage it
executes from then on.

The assessment never executes a stage and never writes to the working directory.
Ephemeral stages follow synpp 1.6.2: an uncached ephemeral stage that has dependents is outside
synpp's own checks, so it gets no reasons, is never unexpected and does not make its cached
children stale (it recomputes only when a descendant does).

## The `run()` loop under the fork

`braunschweig/synpp_fork/pipeline.py` `run()` is synpp 1.6.2's `run()` with the patch
`content-addressed-validity` (ADR-0149 "Decisions made while implementing Phase 4"). In order:

1. **Own reasons, before anything executes.** `staleness.assess` decides which stages are stale for
   their own reasons (required target, not in `pipeline.json`, cache files missing, module hash,
   parent set, validation token). It calls `validate()` of every non-ephemeral stage first, a stage
   without a record included, as synpp's own step 4.8 does; an input-checking `validate()` that
   raises aborts the run before the first stage executes. An in-memory run
   (`working_directory=None`) calls `validate()` up front the same way, then executes everything
   (nothing is cached, no early cutoff, logged as such).
2. **No blanket devalidation.** Upstream's step 4.9 (devalidate every descendant of a stale stage)
   is gone. The log reads `Stale before execution: N stages, decided for their own reasons`
   with one line per stage and its reasons.
3. **Execute in order, then hash.** After a stage executes, `hash_stage_output` records its
   `output_hash`. A malformed `_OUTPUT_HASH_EXCLUDE` raises before the stage executes.
4. **Per-child decision.** For each child of the re-executed parent that is valid so far, the fork
   compares the parent's new digest with the digest the CHILD recorded for that parent
   (`dependency_output_hashes[parent]` in the child's own record):
   - equal: the child is spared, its record is refreshed (`dependencies`,
     `dependency_output_hashes`), log `Output of <parent> unchanged: <child> not devalidated by it`;
   - different: the child is devalidated, reason `parent_output_changed:<parent>`;
   - no digest recorded (a legacy record): the child recomputes as under upstream, reason
     `parent_output_unrecorded:<parent>`, logged as `legacy timestamp rule for ...`.
   The spec's `previous_digest` comparison is replaced by this child-side comparison (R16-2); a
   child is never spared on anything but its own recorded claim. A child with two parents is
   spared only if no parent devalidates it.
5. **Eager rule for ephemeral registries.** If an uncached ephemeral stage exists, the whole run
   applies upstream's eager rule: every descendant of a stale stage is marked stale up front,
   reason `ancestor_stale (eager rule)`; an ephemeral parent of a stale stage gets
   `ephemeral_parent_of_stale_stage`. Without a working directory the reason of every stage is
   `no_working_directory`.
6. **Progress and summary.** `Pipeline progress: n/m known stale (p%)` (m can grow while stages
   devalidate children; the prefix `Pipeline progress` is kept for
   `scripts/monitor_run_health.sh`). The run ends with
   `Executed N stages; early cutoff kept M stages cached (descendants of stale stages that did not execute)`,
   then `- executed <hash> [<reasons>]` per executed stage (its `stale_reasons`, including reasons
   added mid-run) and `- kept cached <hash>` per spared stage. `pipeline.json` stores
   `stale_reasons` per executed entry.

Output hashing cost per stage is logged (`Output hash for <hash>: <files> files, <bytes> bytes, <s> s`);
no full-run overhead has been measured on a real cache (server acceptance pending).

## Running the explainer

```
python scripts/explain_stale_stages.py configs/base_bs.yml configs/overlays/test_100pct.yml \
    [--working-directory <dir>] [--expect stage[,stage...]|@file] [--json out.json]
```

Exit 0: no stage is stale for an own reason outside `--expect` (run targets, which synpp
re-executes on every run, never count). Exit 2: at least one is. Exit 1: error.

### Labels

| Label | Meaning |
|---|---|
| `HIT` | The cache entry is valid and will be reused. |
| `STALE` | Stale for its own reasons; the bracket lists them (`required`, `not_in_meta`, `cache_missing`, `module_hash_changed`, `parents_changed`, `token_changed:<module>`, `parent_updated:<parent>`, `parent_output_changed:<parent>`, `meta_malformed:<what>`). At run time `run()` adds `parent_output_changed:<parent>`, `parent_output_unrecorded:<parent>`, `ancestor_stale (eager rule)`, `ephemeral_parent_of_stale_stage` and `no_working_directory` to a stage it devalidates mid-run (see "The `run()` loop under the fork"). For a stage with no cached variant the config keys that differ from the nearest cached variant are appended. |
| `RECOMPUTE` | Valid on its own, but it will recompute for certain: (a) a stale parent is not recorded in its own `dependency_output_hashes`, so there is nothing to compare against (every entry written by synpp 1.6.2 is of this kind), or (b) the registry holds an uncached ephemeral stage and the eager rule applies to the whole run. The bracket names the ancestors and the reason (`no recorded output hash cuts the cascade off` or `eager rule, ephemeral stages in the registry`). |
| `STALE-IF` | Valid on its own; an ancestor is stale and the child's record carries that ancestor's output digest. The fork's early cutoff decides at run time: `[if output of <ancestor> changes]`. The stage is reused if the re-executed ancestor reproduces its output digest. With `staleness.EARLY_CUTOFF_ACTIVE` set to False the label would instead read `recomputes under synpp 1.6.2; the fork would reuse it if ... output is unchanged` (tests and labels only; the constant is `True`). A valid stage whose own record carries the digest of its stale parent is the cutoff point for everything behind it. |
| `EPHEMERAL` | An ephemeral stage without cache artifacts. Not stale, not unexpected; synpp recomputes it only if a descendant runs. Whenever the registry has any uncached ephemeral stage the report also prints `# WARNING: <n> ephemeral stage(s) in the registry; the fork disables early cutoff for such runs (spec 4.4)`, and every `STALE-IF` stage reads `RECOMPUTE` (eager rule). |
| `legacy timestamp rule for parents: ...` | Printed under a stage whose parent comparison used synpp's upstream timestamp rule because a recorded output hash was missing on either side. |

At run time the launcher log (`scripts/run_synpp.py`) additionally prints the progress line `Pipeline progress: n/m known stale (p%)`: "known" because executing a stage can still devalidate children. The summary line of the report counts the groups separately. The `--json` output carries, per stage, the name
lists (`stale_if`, `unhashed_ancestors`) and the parallel hash lists (`stale_if_hashes`,
`unhashed_ancestor_hashes`; names are ambiguous for two variants of one stage), plus
`ephemeral_uncached`.

## Configuration keys

Both keys live in the `config:` block of `configs/base_bs.yml` and are read by
`scripts/run_synpp.py` (`explain_before_run`), which logs the report with the `[explain]`
prefix after cache priming and before synpp starts.

| Key | Default | Meaning |
|---|---|---|
| `synpp_explain_before_run` | `true` | Run and log the assessment before every launch; `false` skips it (logged). |
| `synpp_expected_stale_stages` | `[]` | Stage NAMES allowed to be stale for own reasons. **Empty list = no gate** (the report is only logged). A non-empty list is a gate: any other stage stale for its own reasons aborts the launch (exit code 2) before any stage executes. A config without `working_directory` raises a `RuntimeError`. |

### `--expect` versus the launcher list

The two gates differ on the empty case, on purpose:

- Launcher `synpp_expected_stale_stages: []` means **no gate**: a default launch must never
  be blocked by an assessment nobody configured.
- Explainer `--expect ""` means **nothing may be stale** (besides run targets): the flag was
  given explicitly, so the caller asks for a strict check. Without `--expect` the explainer
  is a pure report (exit 0).

## Limits

- Only own reasons gate; `RECOMPUTE` and `STALE-IF` stages are reported but never counted
  as unexpected, because they follow from a stale ancestor. With a non-empty
  `synpp_expected_stale_stages` the launcher also logs `[explain] note: <k> further stage(s) will
  recompute because an ancestor is stale`, so a run target upstream of a large cached chain is
  not silent; the note never aborts. `<k>` counts the `RECOMPUTE` stages; a `STALE-IF` stage is
  counted only while early cutoff does not apply (an ephemeral stage in the registry, or
  `EARLY_CUTOFF_ACTIVE` False), because otherwise early cutoff may spare it.
- `dryrun: true` in the config skips the launcher assessment (logged): synpp returns before it
  devalidates anything.
- `STALE-IF` is a prediction: whether the stage is spared depends on the ancestor's output at run
  time (see the label table); the gate and `unexpected()` are unaffected by it. A rebuilt Java
  jar changes its output digest (the eqasim jar and the freight tool jar are not byte-reproducible
  across builds), so early cutoff cannot spare the consumers of a rebuilt jar.
- The report predicts what synpp 1.6.2 would do; it does not validate model results
  (a cache hit is not evidence about scientific correctness).

## `pipeline.json` fields

synpp's own fields are unchanged. A migrated entry gains (spec 4.4):

| Field | Meaning |
|---|---|
| `module_hash` | Same key, new value: the 64-hex sha256 semantic digest (`semantic_module_digest`) instead of synpp's 32-hex md5 of the raw text. |
| `validation_token` | Same key; for source-hashing stages the structured dict `{"format": 2, "python": "3.10", "modules": {dotted name: digest}}` (a composite stage adds its non-source part under its own key). |
| `output_hash` | `{"format": 1, "digest", "pickle", "files", "n_files", "bytes"}` from `hash_stage_output`. `digest` is the sha256 over `pickle` and `files`. |
| `dependency_output_hashes` | `{parent hash: parent output digest}`; filled only where the timestamp rule says the child saw the parent's current output. |
| `fork` | `{"format": 1}`, set only when the migration proved the entry (Ruling F). An entry without it is a legacy entry. |

The fork's `run()` also writes `stale_reasons` (the reasons the stage executed, a list of the
strings above) for every entry it executes.

## Token format and the golden digest

`braunschweig.synpp_tokens.TOKEN_FORMAT` is part of every structured token. The digest is the
sha256 of `ast.dump(tree, annotate_fields=True, include_attributes=False)` after removing the
docstring of the module and of every class and function body; comments, blank lines and
formatting never reach the AST, constants, decorators, annotations and defaults stay in.
`tests/test_synpp_tokens.py` (`test_golden_digest_of_a_fixed_snippet_is_pinned`) pins the digest
of a fixed snippet for Python 3.10. **Any change that moves that digest must bump
`TOKEN_FORMAT` and update the pin together**, otherwise stored tokens would silently stay
valid. `ast.dump` differs between Python minors, which is why the token carries `"python"`: a
minor upgrade recomputes every source-hashing stage once, and the explainer shows it as a token
difference. The golden test skips on a minor other than the pinned one.

The coverage test `tests/test_token_refactor_coverage.py` takes the stage set from
`scripts/audit_synpp_helper_hash.py` and pins its size (30 on 2026-10-21); adding or removing a
source-hashing stage means updating that number deliberately together with
`docs/codebase/notes/synpp-helper-hash-audit.md`.

## The `_OUTPUT_HASH_EXCLUDE` hook

A stage module may declare `_OUTPUT_HASH_EXCLUDE = ("*.log", "logfile*")`: `fnmatch` globs (case
sensitive on every platform) on the path relative to the stage's `<hash>.cache` directory, for
files whose content changes without meaning. It must be a tuple of strings; anything else makes
`hash_stage_output` raise `TypeError`. It is empty everywhere today; add entries on evidence. A
symlink inside a cache directory raises even when a glob would exclude it; the cache directory
itself may be a symlink. An unreadable file or directory raises, never "no hash".

## Re-basing the copy when synpp is upgraded

1. Install the new synpp in the pinned environment and read the diff between the installed
   `synpp/pipeline.py` and the copy in `braunschweig/synpp_fork/pipeline.py` (the copy keeps
   upstream's code except at the `# PATCH` hunks).
2. Re-copy `run()` and `get_stage_hash()` from the new file and re-apply every patch listed in
   `braunschweig.synpp_fork.PATCHES`. Re-check that `braunschweig.synpp_deterministic` still
   matches the new `process_stages`.
3. Update `UPSTREAM_PIPELINE_SHA256` (`upstream_pipeline_sha256()` computes it) and the version
   check in `braunschweig.synpp_deterministic`. Run `tests/test_synpp_fork_install.py`,
   `tests/test_synpp_fork_staleness.py` (differential test against the installed synpp) and the
   full suite.
4. Record the upgrade in an ADR. A different upstream SHA triggers a warning at every
   `install()` and fails `test_upstream_pipeline_sha_is_pinned`.

## Migrating a cache

Mandatory once per existing cache after updating to this version, before the first launch.
Otherwise every stage reads `module_hash_changed`; `staleness.format_report` then prints a
warning that N entries carry a legacy md5 module hash and names
`scripts/migrate_cache_metadata.py`.

```
python scripts/migrate_cache_metadata.py configs/base_bs.yml configs/overlays/test_100pct.yml --working-directory <dir> --dry-run
python scripts/migrate_cache_metadata.py configs/base_bs.yml configs/overlays/test_100pct.yml --working-directory <dir>
python scripts/explain_stale_stages.py configs/base_bs.yml configs/overlays/test_100pct.yml --working-directory <dir> --expect ""
```

Options: `--legacy-source-rev <rev>` (default `LEGACY_SOURCE_REV` in the script) and
`--legacy-source-repo <dir>`. Exit 0 also when entries were kept legacy (they are reported);
exit 1 on any error, in which case `pipeline.json` is never written.

**The proof (Ruling G).** The 30 stage modules were edited by the token refactor, so a stored md5
no longer equals the md5 of the current file. The migration therefore proves each entry against
revision `LEGACY_SOURCE_REV = 205a0fb7152ea75c2ac48f2afc0b05b43e99ce15` read with `git show`. Git
and the revision must be available (`git fetch origin main`); otherwise the script raises, naming
the revision.

- Module hash: the stored md5 equals the md5 of the current text, or equals the md5 of the text
  at the revision and the module is refactor-equivalent (`refactor_equivalent`: equal semantic
  digests after removing the module-level `def validate` and the imports of exactly `hashlib`,
  `inspect`, `importlib` and `braunschweig.synpp_tokens`).
- Token: the revision's `validate()` is replayed with `inspect.getsource` answering the
  revision's text for every module that differs. Equal to the stored string, with
  refactor-equivalent helper modules, proves the token, and the current `validate()` value is
  stored.
- The refactor-equivalence of the 30 stage modules is a migration-time property: the migration
  proves it per entry, there is no committed test for it, because legitimate later edits must
  turn into `kept_legacy`. A further edit to a stage module before an existing cache is
  migrated makes that stage recompute once.
- Anything else is a mismatch: the entry is left byte-for-byte unchanged (`kept_legacy`, one
  warning line with the reason) and the upstream timestamp rule keeps deciding it.

Output hashing covers every entry of `pipeline.json` that has both artifacts, including entries
of old config variants that are not in the current graph; this is the expensive part on large
caches. The migration logs file count, bytes and hashing seconds per entry and the count totals at
the end (`migrated`, `kept_legacy`, `module_hash_mismatch`, `token_mismatch`, and so on).
`pipeline.json` is rewritten atomically with `pipeline.json.bk` kept
(`braunschweig.synpp_fork.metadata.write_meta`). Re-running is idempotent (`already_migrated`).

Status of the real-cache proof: tested with synthetic legacy records on the real stage registries
(107 of 107 entries migrated, 2026-10-21, not a real cache); the acceptance on a copy of the
100 % cache on felix, including the Phase 4 early-cutoff scenarios (ADR-0149 "Evidence"), is
pending (maintainer go).
