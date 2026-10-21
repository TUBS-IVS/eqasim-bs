# The `braunschweig/synpp_fork` package: explainer, vendored synpp functions and cache migration

One note for the whole package (ADR-0149, issue #468; design spec
`docs/superpowers/specs/2026-10-16-content-addressed-stage-cache-design.md`, local and
gitignored). It describes what exists after Phase 3: the explainer (Phase 1), the semantic
tokens, the output hash, the fork skeleton and the cache migration. **Not implemented yet
(Phase 4):** early cutoff in the fork's `run()` and `cache_share` coherence by output hash.
Today the fork's `run()` is still the upstream loop, so validity is still decided by the
upstream timestamp rule (`staleness.EARLY_CUTOFF_ACTIVE` is `False`).

## What the package owns today

| Module | Responsibility |
|---|---|
| `braunschweig/synpp_fork/__init__.py` (`install`, `PATCHES`, `UPSTREAM_PIPELINE_SHA256`) | Installs the project's synpp patches before a registry is built: first `braunschweig.synpp_deterministic.install()` (deterministic implicit-config propagation, ADR-0105, with its synpp version guard), then replaces `synpp.pipeline.get_stage_hash`, `synpp.pipeline.run` and the re-exported `synpp.run` by the copies in `pipeline.py`. Logs a warning when the installed `synpp/pipeline.py` differs from the pinned SHA. |
| `braunschweig/synpp_fork/pipeline.py` (`get_stage_hash`, `run`) | The vendored copy of synpp 1.6.2's `run()` and `get_stage_hash()`. Two patches, each marked `# PATCH <name>` in the source and named in `PATCHES`: `deterministic-propagation` (`process_stages` from `braunschweig.synpp_deterministic`) and `semantic-module-hash` (`get_stage_hash` digests the AST via `braunschweig.synpp_tokens.semantic_module_digest`). `run()` is otherwise the upstream loop. |
| `braunschweig/synpp_fork/output_hash.py` (`hash_stage_output`, `OUTPUT_HASH_FORMAT`) | Content hash of a stage's output (pickle plus every file of its cache directory). Used by the migration; the fork's `run()` does not call it yet (Phase 4). |
| `braunschweig/synpp_tokens.py` (`semantic_module_digest`, `source_token`, `legacy_source_token`, `TOKEN_FORMAT`) | Semantic source digests and the structured `validate()` token of the 30 source-hashing stages. Lives outside the package because the stage modules import it. |
| `braunschweig/synpp_fork/registry.py` (`load_settings`, `build_registry`) | Composes the base config (plus an optional overlay through `braunschweig.config_compose`), validates it (empty config or missing `run` list raise `ValueError` naming the file) and builds the synpp stage registry read-only, with the production stage hashes. |
| `braunschweig/synpp_fork/metadata.py` (`read_meta`, `write_meta`) | Reads and writes synpp's `pipeline.json` with synpp's own backup convention. |
| `braunschweig/synpp_fork/staleness.py` (`assess`, `format_report`, `unexpected`) | Explains, per stage, whether the cache entry will be reused and why not. Replicates synpp 1.6.2's own devalidation steps; a differential test (`tests/test_synpp_fork_staleness.py`) compares it with `synpp.run(..., verbose=True)["stale"]`. |

`scripts/migrate_cache_metadata.py` (see "Migrating a cache") is the only writer of `output_hash`
and `dependency_output_hashes` today.

The assessment never executes a stage and never writes to the working directory.
Ephemeral stages follow synpp 1.6.2: an uncached ephemeral stage that has dependents is outside
synpp's own checks, so it gets no reasons, is never unexpected and does not make its cached
children stale (it recomputes only when a descendant does).

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
| `STALE` | Stale for its own reasons; the bracket lists them (`required`, `not_in_meta`, `cache_missing`, `module_hash_changed`, `parents_changed`, `token_changed:<module>`, `parent_updated:<parent>`, `parent_output_changed:<parent>`, `meta_malformed:<what>`). For a stage with no cached variant the config keys that differ from the nearest cached variant are appended. |
| `RECOMPUTE` | Valid on its own, but an ancestor is stale and has no recorded output hash, so there is nothing to compare against: the stage recomputes for certain. Every entry written by synpp 1.6.2 is of this kind today, so descendants of a stale stage show `RECOMPUTE`. |
| `STALE-IF` | Valid on its own; an ancestor is stale but has a recorded output hash. Describes the FORK (Phase 4 early cutoff): it would reuse the stage if the re-executed ancestor's output is unchanged. synpp 1.6.2 recomputes every descendant of a stale stage, so while `staleness.EARLY_CUTOFF_ACTIVE` is False the label reads `recomputes under synpp 1.6.2; the fork would reuse it if ... output is unchanged`. A valid stage with its own recorded output hash is the cutoff point: its children are `STALE-IF` even when a stale ancestor further up has no hash. |
| `EPHEMERAL` | An ephemeral stage without cache artifacts. Not stale, not unexpected; synpp recomputes it only if a descendant runs. Whenever the registry has any ephemeral stage the report also prints `# WARNING: <n> ephemeral stage(s) in the registry; the fork disables early cutoff for such runs (spec 4.4)`. |
| `legacy timestamp rule for parents: ...` | Printed under a stage whose parent comparison used synpp's upstream timestamp rule because a recorded output hash was missing on either side. |

The summary line counts the groups separately. The `--json` output carries, per stage, the name
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
  recompute because an ancestor is stale` (the `RECOMPUTE` count), so a run target upstream of a
  large cached chain is not silent; the note never aborts.
- `dryrun: true` in the config skips the launcher assessment (logged): synpp returns before it
  devalidates anything.
- `STALE-IF` describes the fork, not synpp 1.6.2 (see the label table); the gate and
  `unexpected()` are unaffected by it.
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

The spec's `stale_reasons` field is not written yet (Phase 4).

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
- Anything else is a mismatch: the entry is left byte-for-byte unchanged (`kept_legacy`, one
  warning line with the reason) and the upstream timestamp rule keeps deciding it.

Output hashing covers every entry of `pipeline.json` that has both artifacts, including entries
of old config variants that are not in the current graph; this is the expensive part on large
caches. The migration logs file count and bytes per entry and the count totals at the end
(`migrated`, `kept_legacy`, `module_hash_mismatch`, `token_mismatch`, and so on).
`pipeline.json` is rewritten atomically with `pipeline.json.bk` kept
(`braunschweig.synpp_fork.metadata.write_meta`). Re-running is idempotent (`already_migrated`).

Status of the real-cache proof: tested with synthetic legacy records on the real stage registries
(107 of 107 entries migrated, 2026-10-21, not a real cache); the acceptance on a copy of the
100 % cache on felix is pending (maintainer go).
