# The `braunschweig/synpp_fork` package: the stale-stage explainer and its launcher gate

One note for the whole package; later phases of the content-addressed stage cache
(`docs/superpowers/specs/2026-10-16-content-addressed-stage-cache-design.md`, local and
gitignored) extend it. This note describes what exists today (Phase 1), not the design.

## What the package owns today

| Module | Responsibility |
|---|---|
| `braunschweig/synpp_fork/__init__.py` (`install`) | Installs the project's synpp patches before a registry is built. Today this is only `braunschweig.synpp_deterministic` (deterministic implicit-config propagation, ADR-0105). The vendored `run()` / `get_stage_hash()` of later phases are not here yet. |
| `braunschweig/synpp_fork/registry.py` (`load_settings`, `build_registry`) | Composes the base config (plus an optional overlay through `braunschweig.config_compose`), validates it (empty config or missing `run` list raise `ValueError` naming the file) and builds the synpp stage registry read-only, with the production stage hashes. |
| `braunschweig/synpp_fork/metadata.py` (`read_meta`, `write_meta`) | Reads and writes synpp's `pipeline.json` with synpp's own backup convention. |
| `braunschweig/synpp_fork/staleness.py` (`assess`, `format_report`, `unexpected`) | Explains, per stage, whether the cache entry will be reused and why not. Replicates synpp 1.6.2's own devalidation steps; a differential test (`tests/test_synpp_fork_staleness.py`) compares it with `synpp.run(..., verbose=True)["stale"]`. |

The assessment never executes a stage and never writes to the working directory.
Ephemeral stages are not modelled; they are over-reported as stale (the safe direction).

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
| `STALE-IF` | Valid on its own; an ancestor is stale but has a recorded output hash. Recomputes only if the re-executed ancestor's output differs (early cutoff, a later phase). |
| `legacy timestamp rule for parents: ...` | Printed under a stage whose parent comparison used synpp's upstream timestamp rule because a recorded output hash was missing on either side. |

The summary line counts the four groups separately.

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
  as unexpected, because they follow from a stale ancestor.
- The report predicts what synpp 1.6.2 would do; it does not validate model results
  (a cache hit is not evidence about scientific correctness).
