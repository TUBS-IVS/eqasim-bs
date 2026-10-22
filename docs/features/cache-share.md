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

**Output hashes and migration (ADR-0149, issue #468, state after Phase 3).**
`scripts/migrate_cache_metadata.py` records, for every cache entry it can prove, a content hash
of the stage output (`output_hash`) and the parents' output digests the child was built against
(`dependency_output_hashes`) in `pipeline.json`, and marks the entry `fork: {"format": 1}`.
These fields do NOT travel with a shared entry. `braunschweig.cache_share.track_run` writes the
envelope sidecar `<entry>.metadata.json` at run time, copying the `pipeline.json` record as it was
then. `export` writes that sidecar into the store only when the envelope's record equals the
CURRENT `pipeline.json` record of the entry (and the runtime fingerprint is present). The
migration rewrites the record (adds `output_hash` and `fork`, a longer `module_hash`), so for a
migrated entry the two differ: `export` copies the artifacts WITHOUT metadata, and a later
`prime` finds no record for it and treats the entry as a miss (the stage recomputes; safe, never
a wrong hit). `track_run` re-writes an envelope only for entries a run actually recomputed, so
re-exporting after a migration does not carry the migrated records either, and a record written
by a Phase 3 run carries no `output_hash`. Nothing reads the recorded fields for a decision yet:
coherence of a copied entry (`braunschweig.cache_share._drop_incoherent_metadata`) and the
validity of a child still follow the timestamp rule described above, so the limitation that a
primed entry must match its parents' timestamps exactly is unchanged until Phase 4. Refreshing
and validating shared envelopes by output hash is Phase 4 work (Task 17 of #468).

**Planned, not in force (Phase 4 of #468).** Decided in ADR-0149 and not implemented: a copied
entry will be kept when its dependencies' recorded output digests equal those of the target
metadata (two legacy sides keep the timestamp rule; mixed pairs are dropped), and a child
will stay valid when a re-executed parent reproduces its output hash (early cutoff). This
section is to be rewritten when that lands; the decision and its implementation status are
tracked in ADR-0149 and the contributor note `docs/codebase/notes/synpp-fork.md`.
