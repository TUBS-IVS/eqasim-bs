# Shared persistent stage-cache (`cache_share`, prime-on-launch)


Expensive **sampling-rate-independent** synpp stages (above all
`braunschweig.freight.extraction` -- the ~3 h published Java routing, run once per
trip category) are recomputed on every fresh run because synpp caches per
`working_directory`. `braunschweig.cache_share` + the `scripts/run_synpp.py`
launcher reuse them across runs/machines by **priming**: synpp stores each stage as
`<module>__<hash>.p` (+ `<module>__<hash>.cache/`) and re-validates `<hash>` on load,
so we never recompute synpp's hash -- we copy the artifacts and let synpp decide.

- `scripts/cache_share.py export --working-directory <wd> --store <store> --modules m1,m2`
  copies a stage's cache artifacts into a shared store.
- `scripts/cache_share.py prime  --working-directory <wd> --store <store> --modules m1,m2 [--recompute m1]`
  copies the store's entries for the requested modules into a target working_directory.
- `run_synpp.py` calls `prime_from_config` BEFORE synpp runs, driven by config keys:
  `cache_share_enabled` (default **true**), `cache_share_store`
  (default `eqasim-data/cache_shared`), `cache_share_stages` (default = the freight
  chain), `cache_share_recompute` (default `[]`; `["*"]` = recompute all).
- `run_synpp.py` calls `export_to_store_from_config` AFTER a **successful** run (a
  failed run raises first, so it never seeds the store), copying `cache_share_stages`
  from the working_directory into the store. Gated by `cache_share_enabled` AND
  `cache_share_export` (default **true**; set false to prime-without-export on a
  throwaway config). The auto-export uses `cache_share.export(..., skip_existing=True)`
  so an entry already in the store is **never overwritten**; a different config/content
  has a different `<hash>` and is stored alongside. The CLI `export` keeps its
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
`tests/test_run_synpp_prime.py`.

**Shareable-stage set + fixed work_dir (Tier A / B).** The two all-features server
configs share, beyond the freight chain, the 32 empirically verified
sampling- AND path-independent stages (identical synpp hash at 1% and 25%) plus
`braunschweig.popsim.stage` and `braunschweig.popsim.completed_donor`. Sharing
`popsim.stage` requires a SINGLE fixed `braunschweig.population.popsim.work_dir`
(`eqasim-data/popsim_work_allfeat`) across all run configs so its hash is identical;
the stale-batch guard (`purge_stale_batches_on_config_change`) keeps a shared
work_dir safe on a config change. The MiD donor build (member completion +
weekend-plan match) is the `braunschweig.popsim.completed_donor` stage: it depends
only on the MiD data, the random seed, the seed day-filter, and the weekend-plan
flag -- NOT on controls / sampling / work_dir -- so it is computed once and reused
across ALL runs (including control-tier changes). Export a completed run's stages to
the store with `python scripts/cache_share.py export ...`; future runs prime them.
Design: `docs/superpowers/specs/2026-06-22-tier-a-b-caching-design.md`.
