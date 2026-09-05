# ADR-0105 · 2026-09-05 · Deterministic synpp stage hashes (patch of the implicit-config propagation)
- **Status:** active
- **Numbering:** ADR-0104 is claimed by the unpushed branch `feature/commute-day-state-phase-a`
  (checked across all local and remote refs on 2026-09-05); 0105 is the next free number.
- **Context:** Analysis-only re-runs against the shared 100 % cache `cache_bs_100pct_i329` on the
  run server devalidated and re-executed blocks of untargeted data/location/gravity stages three
  times (2026-09-03: 4 stages; 2026-09-04: 2; 2026-09-05: 14), recorded in the run manifest
  `commute-day-state-phase-a-2026-09-05` of branch `feature/commute-day-state-phase-a` (not yet
  merged at the time of writing), although code, config and input files were identical to the runs
  that had written the cache. The systematic investigation (`superpowers:systematic-debugging`,
  2026-09-05) excluded the module hash (md5 of the stage source: identical across `b7eed9a5`,
  `main` and the branch heads for every affected module), the validation tokens (input file sizes
  unchanged since June) and the config values (the two overlays differ only in the run list). The
  synpp metadata (`pipeline.json`) showed the actual difference: the SAME stage carried different
  sets of *implicit* config keys in different runs (e.g.
  `synthesis.population.spatial.primary.candidates` had all `braunschweig.population.popsim.*`
  keys on 2026-09-03 and none on 2026-09-05); the shared cache held nine hash variants of
  `braunschweig.popsim.stage` and five of `replacement_education_gravity` with byte-identical
  payloads (recorded in the same run manifest).
  Root cause in synpp 1.6.2 (`synpp/pipeline.py`, `process_stages`, block "Update configuration
  requirements based dependencies"): the pass that copies upstream config keys into downstream
  stages starts from `list(set_of_source_hashes)` and re-enqueues only `stage["downstream"][0]`.
  The propagated key set therefore depends on the iteration order of a set of md5 strings -- on
  Python's per-process string-hash randomisation (`PYTHONHASHSEED`) -- and on which downstream
  path is walked. Since that set enters `hash_name(...)`, the cache entry name is nondeterministic.
  Reproduced locally on `configs/base_bs.yml` + `configs/overlays/test_25pct.yml`: under
  `PYTHONHASHSEED` 1, 2 and 3 the unpatched build yields three different stage registries (the SET
  of stages that change and their propagated key counts vary with the code state), while the
  patched build is identical across seeds -- reproduced by
  `tests/test_synpp_deterministic.py::test_real_pipeline_hashes_are_identical_across_hash_seeds`.
  The same defect is the uncharacterised "propagation does not reach every consumer" of
  `docs/codebase/notes/synpp-config-propagation.md` (the `pt2matsim_version` crash of the
  2026-08-20 run).
- **Decision:** `braunschweig/synpp_deterministic.py` provides a copy of synpp 1.6.2's
  `process_stages` in which the propagation pass is replaced by a topological, all-edges,
  order-independent closure (`propagate_implicit_config`: Kahn order with sorted tie-breaks,
  upstream complete before downstream, conflicting values raise `ImplicitConfigConflict` instead
  of `assert`). `install()` monkeypatches `synpp.pipeline.process_stages` and refuses any synpp
  version other than the pinned `1.6.2` (`environment.yml`), because the copy tracks that
  version's internals. It is installed before synpp builds the graph in the two entry points the
  project owns: `scripts/run_synpp.py` (all production and gate runs) and
  `braunschweig.documentation.dag` (DAG snapshots). A plain `python -m synpp` run does not carry
  the patch and is therefore not a supported way to run the pipeline (README).
  `scripts/report_stage_hash_impact.py` computes the deterministic hashes for a config and
  compares them with a cache-directory listing, so the one-time re-hash cost is known before a
  run. Everything else about stage identity (identification hash, cycle detection, ephemeral
  handling, `hash_name`) is unchanged.
- **Consequences:**
  - Stage hashes are now identical across processes, machines and run lists, so an analysis-only
    re-run against an existing cache hits every unchanged upstream stage. The recurring
    "devalidated N untargeted stages" side effect ends.
  - **One-time cost:** the first patched run recomputes stages whose deterministic hash matches
    none of the existing cache variants. Measured with `report_stage_hash_impact.py` against the
    server's `cache_bs_100pct_i329` listing (config = the 2026-09-05 merged run config with the
    full production run list): 93 stages, 47 hits, 46 misses. The expensive population stages
    HIT (`braunschweig.popsim.stage` `0c6822f4...`, `braunschweig.popsim.completed_donor`); the
    misses are the location/gravity chain, `secondary_chainsolvers`, the MATSim scenario and
    simulation stages and the analysis stages -- stages that the nondeterminism had already
    recomputed repeatedly (three variants each of `secondary_chainsolvers` and
    `matsim.simulation.run` exist). After that run the entries stay stable.
  - The propagation is now complete: every non-volatile upstream option reaches every downstream
    stage. Options declared `volatile` are still excluded by design, so the rule of
    `docs/codebase/notes/synpp-config-propagation.md` (callers declare a helper's options
    themselves) stands.
  - `ImplicitConfigConflict` is a new failure mode: two upstream stages carrying different values
    for the same key under one downstream stage now fail the graph build instead of asserting.
    No conflict exists in the production configs (the patched graph builds for `test_25pct`,
    `test_100pct` and the server config).
  - Upstream: the defect should be reported to eqasim-org/synpp; the project keeps the local
    patch until a fixed release is pinned (not opened yet; proposed to the maintainer).
- **Rejected alternatives:**
  - *Pin `PYTHONHASHSEED=0` in the run wrapper.* Makes the order reproducible on one interpreter
    build but keeps the propagation incomplete and first-downstream-only, so a consumer can still
    miss an option, and hashes would change with any Python upgrade that alters set ordering.
  - *Run analysis stages only inside the original run's worktree/config.* Does not remove the
    nondeterminism -- the 2026-08-24 i329 run itself produced three hash variants of
    `gravity.model` and nine of `popsim.stage` across its phases.
  - *Fork synpp.* Heavier than a 200-line guarded copy for a single function; revisit if more
    patches accumulate.
- **Evidence:** `tests/test_synpp_deterministic.py` (order-independence on a diamond graph,
  conflict/cycle errors, version guard, and the real production graph hashing identically under
  two `PYTHONHASHSEED` values); server metadata and `cmp` results recorded in the run manifest
  `commute-day-state-phase-a-2026-09-05` of branch `feature/commute-day-state-phase-a` (not yet
  merged at the time of writing) (`status.notes`); this ADR's measured hit/miss table reproduced
  by `scripts/report_stage_hash_impact.py`.
