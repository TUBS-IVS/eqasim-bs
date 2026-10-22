# Shared persistent stage-cache (`cache_share`, prime-on-launch)


Expensive **sampling-rate-independent** synpp stages (above all
`braunschweig.freight.extraction` -- the ~3 h published Java routing, run once per
trip category) are recomputed on every fresh run because synpp caches per
`working_directory`. `braunschweig.cache_share` + the `scripts/run_synpp.py`
launcher reuse them across runs/machines by **priming**: synpp stores each stage as
`<module>__<hash>.p` (+ `<module>__<hash>.cache/`) and re-validates `<hash>` on load,
so we never recompute synpp's hash -- we copy the artifacts and let synpp decide.

- `scripts/cache_share.py export --working-directory <wd> --store <store> --modules m1,m2`
  copies a stage's cache artifacts and native metadata sidecars into a shared store.
- `scripts/cache_share.py prime  --working-directory <wd> --store <store> --modules m1,m2 [--recompute m1]`
  copies the store's entries for the requested modules into a target working_directory.
- `run_synpp.py` calls `prime_from_config` BEFORE synpp runs, driven by config keys:
  `cache_share_enabled` (default **true**), `cache_share_store`
  (default `eqasim-data/cache_shared`), `cache_share_stages` (default = the freight
  chain), `cache_share_recompute` (default `[]`; `["*"]` = recompute all).
- `cache_share_metadata` (default **true**, ADR-0122) transports each copied entry's
  native metadata and merges only coherent dependency snapshots with exactly matching
  tracked creation runtimes (OS, architecture, Python and package versions). False retains
  artifact-only copying; fresh targets then recompute because synpp requires
  `pipeline.json`. Legacy stores safely miss until replaced by newly executed,
  tracked entries; exporting an old cache does not invent its creation environment.
  Existing target payloads are never given another
  run's metadata. See [metadata rules](../codebase/notes/performance-equivalence.md#shared-cache-correctness).
- `run_synpp.py` calls `export_to_store_from_config` AFTER a **successful** run (a
  failed run raises first, so it never seeds the store), copying `cache_share_stages`
  from the working_directory into the store. Gated by `cache_share_enabled` AND
  `cache_share_export` (default **true**; set false to prime-without-export on a
  throwaway config). The auto-export uses `cache_share.export(..., skip_existing=True)`
  so an entry already in the store is **never overwritten**; different configuration
  hashes are stored alongside. Module/input changes can retain a filename but make
  its metadata stale; explicit export refreshes that snapshot. The CLI `export` keeps its
  overwrite default (`skip_existing=False`).

A primed entry whose hash does NOT match the target config is **ignored by synpp and
recomputed** -- never a corruption, only a forgone speedup (logged as a miss; no
silent fallback). The store is gitignored and travels via the existing
`scripts/sync_data_to_server.ps1`. **Exclusion:** stages whose hash depends on
machine-variable config (e.g. an auto worker count `num_workers: 0` ->
`cpu_count - 2`) will not hit across machines -- pin a fixed integer there if you need
cross-machine reuse. `cache_share_enabled: false` makes the launcher a pure no-op for cache
sharing: the run still goes through `scripts/run_synpp.py`, so the deterministic stage-hash
patch (ADR-0105) stays installed, unlike a plain, unsupported `python -m synpp` run. Design:
`docs/superpowers/specs/2026-06-22-shared-stage-cache-design.md`. Tests:
`tests/test_cache_share.py`, `tests/test_cache_share_cli.py`,
`tests/test_run_synpp_prime.py`, `tests/test_cache_share_synpp.py`.

**Shareable-stage set + fixed work_dir (Tier A / B).** The two all-features server
configs share, beyond the freight chain, the 32 empirically verified
sampling- AND path-independent stages (identical synpp hash at 1% and 25%) plus
`braunschweig.popsim.stage` and `braunschweig.popsim.completed_donor`. Sharing
`popsim.stage` requires compatible configuration and the same
`braunschweig.population.popsim.work_dir`: 1% and 25% use
`eqasim-data/popsim_work_allfeat`, while 100% intentionally keeps
`eqasim-data/popsim_work_allfeat_opt` and different importance weights;
the stale-batch guard (`purge_stale_batches_on_config_change`) keeps a shared
work_dir safe on a config change. The MiD donor build (member completion +
weekend-plan match) is the `braunschweig.popsim.completed_donor` stage: it depends
on the MiD data, random seed and declared donor-construction options and helper
sources; it is independent of controls / sampling / work_dir. A compatible
snapshot can therefore be reused across scales. Export a completed run's stages to
the store with `python scripts/cache_share.py export ...`; future runs prime them.
Design: `docs/superpowers/specs/2026-06-22-tier-a-b-caching-design.md`.

**Output hashes, migration and coherence by output hash (ADR-0149, issue #468, Phase 3 and 4).**
`scripts/migrate_cache_metadata.py` records, for every cache entry it can prove, a content hash
of the stage output (`output_hash`) and the parents' output digests the child was built against
(`dependency_output_hashes`) in `pipeline.json`, and marks the entry `fork: {"format": 1}`; under
the vendored fork the launcher's `run()` writes the same fields for every stage it executes.
`braunschweig.cache_share.track_run` writes the envelope sidecar `<entry>.metadata.json` at run
time, copying the `pipeline.json` record, and `export` writes that sidecar into the store only
when the envelope's record equals the CURRENT `pipeline.json` record of the entry (and the runtime
fingerprint is present).

*Coherence by output hash (Phase 4).* `prime` copies the entries, merges the records of
compatible envelopes and then keeps only coherent ones. `braunschweig.cache_share._incoherence`
judges an entry against its parents in the target metadata; `_coherent` is "no reason".
There are five outcomes of an entry with parents, and the first failing parent decides:

| Outcome | Meaning | Result |
|---|---|---|
| output-hash match | both sides carry a digest and they are equal (the child's `dependency_output_hashes[parent]` equals the parent's `output_hash.digest`) | kept, a hit although the timestamps differ between runs |
| `output_hash_mismatch` | both sides carry a digest and they differ | dropped (recompute) |
| `legacy_timestamp_mismatch` | neither side carries a digest and the parent's `updated` differs from the timestamp the child recorded | dropped |
| `mixed_hashed_legacy` | exactly one side carries a digest, content cannot be compared | dropped |
| absent parent with a recorded digest | the parent is not in the target metadata (it is not shared, so a fresh directory lacks it) but the child's record carries `dependency_output_hashes[parent]` | kept; decided at run time (see below) |

An absent parent WITHOUT a recorded digest (`missing_parent`) is dropped (it would recompute for
certain); two legacy sides with equal timestamps are kept (the old rule). `_coherent` is never more permissive than the fork's
`braunschweig.synpp_fork.staleness.assess`; it can be stricter (for example a legacy parent older than
the child's snapshot), which is a safe miss, never a false hit. `prime` logs both sides of the decision:
`invalidated N incoherent dependency records (output-hash mismatch a, legacy timestamp mismatch b,
mixed hashed/legacy pair c, missing parent d)` and `kept N copied records (output-hash match x,
legacy equal timestamps y, no parents z, absent parent decided by digest at run time w)`. A hit is
therefore observable and not assumed; the explainer (`scripts/explain_stale_stages.py`) shows the
primed entries as `HIT` or as stale with the reason.

*A primed entry whose parent is recomputed in the target.* The run re-executes the absent parent,
and the fork compares its new output digest with the one the child recorded: equal keeps the child
(a hit), different recomputes it. A mismatch never becomes a hit. Before that run the explainer
shows such an entry as `STALE-IF`, not `HIT`: whether it is a hit is decided at run time by the
digest. `braunschweig.freight.extraction` is the known case: its unshared
`braunschweig.freight.extraction_tool` is rebuilt in a fresh directory. Since #474 the tool jar
builds byte-reproducibly, so a rebuild with the same inputs, JDK and Maven plugins reproduces the
recorded digest and `braunschweig.freight.extraction` is spared there; before #474 it always
recomputed. The decision text is ADR-0149 (R17-3, R17-2, decision 8).

*Envelope refresh (R17-1).* Under early cutoff, `track_run` keeps an existing envelope in step with a
record that a run changed (the envelope's original environment is kept; it never creates an envelope), so
`export` still sees envelope and record equal. The decision text is in
[ADR-0149, R17-1](../decisions/ADR-0149-vendored-synpp-fork-and-content-addressed-stage-validity.md).

*Known safe misses (R17-2; documented, not changed).*

- A migrated record differs from its envelope (the migration rewrote `pipeline.json`), so `export`
  copies the artifacts WITHOUT metadata and a `prime` from that store misses, until a later run under
  `track_run` has refreshed the envelope and an EXPLICIT `export` (`skip_existing=False`, the CLI
  default) shares the record again; the automatic export after a run uses `skip_existing=True` and
  never rewrites an entry already in the store.
- A store written before the semantic tokens (md5 module hashes) primes as a miss.
- `braunschweig.freight.extraction_tool` is not in `cache_share_stages`, while the dependent
  `braunschweig.freight.extraction` is shared. A primed `freight.extraction` record whose
  recorded tool digest differs from the target's existing tool entry is dropped as incoherent and
  recomputed; in a fresh directory the record is kept and decided at run time by the rebuilt
  jar's digest (see above). Since #474 both jars (tool and eqasim) build byte-reproducibly
  (ADR-0149 decision 8), so the digests differ only when the jar really differs (other inputs,
  another JDK or Maven plugin version) or when one side was built before #474; such a record
  recomputes once.

Status: implemented and proven by synthetic tests (`tests/test_cache_share_synpp.py`,
`tests/test_synpp_fork_early_cutoff.py`); the acceptance on a copy of the 100 % cache on the
server ran on 2026-10-22 as a smoke (`docs/runs/content-addressed-cache-acceptance-100pct-2026-10-22.yml`), so no hit rate on a real store is stated here. The decision
and the rulings are in ADR-0149, the loop and its log vocabulary in
`docs/codebase/notes/synpp-fork.md`.
