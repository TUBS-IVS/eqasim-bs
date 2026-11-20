# ADR-0200 · 2026-11-18 · PopulationSim keeps only the positive weight rows and takes bit-identical fast paths, as opt-in fork settings

- **Status:** accepted (implemented on branch `chore/i656-popsim-batch-memory` and in fork release `0.10.0+bs.2`; owner, 2026-11-17: a separate issue and a separate pull request for the run time and memory of a PopulationSim batch, with the rule that an optimisation leaves the result byte-identical or makes it better by a measure fixed before the comparison). The production values of the two options are not argued here: the run manifest [`popsim-run-performance-2026-11-18`](../runs/popsim-run-performance-2026-11-18.yml) owns that outcome.
- **Numbering:** ADR-0200 is the next free id; checked on 2026-11-18 across all local and remote branches and the open pull requests.
- **Issue:** #656. Fork base: [ADR-0197](ADR-0197-the-populationsim-fork-branches-from-tag-v0-10-0.md); earlier fork options: [ADR-0199](ADR-0199-populationsim-fork-with-fractional-weights-and-proportional-draw.md).

## Context

- The resource gate sized every PopulationSim batch worker at its 30 GiB default, so the production run of 2026-10-31 ran two batches at a time ([ADR-0126](ADR-0126-resource-adaptive-configuration.md), `configs/overlays/test_100pct.yml`, run manifest [`prod100-java-heap-driver-rss-2026-11-04`](../runs/prod100-java-heap-driver-rss-2026-11-04.yml)). Memory, not cores, limits how many batches run in parallel.
- Upstream PopulationSim stores, for every sub-geography, one weight row per seed household group and zone. With the full MiD seed pool and 100 m cells almost all of these rows are zero (rows kept against rows in total: the run manifest).
- Run time went into work whose result is known in advance: the numba balancers evaluate an exponential for every row and control although a control's incidence takes few distinct values, the seed-level integerizer sets every coefficient of its model including the zeros, and the household expansion builds draw lists for every group, used or not.

## Decision

1. **Two opt-in fork settings, default off.** `STORE_POSITIVE_WEIGHT_ROWS_ONLY` keeps only the rows with a positive weight in the stored weight tables of the sub-geographies; `FAST_PATHS` switches on faster implementations of the three steps above. With both off the fork runs upstream's code paths. What each setting does, its counted fallbacks and its limits are documented in the fork README (section "Run time and memory") on [`bs/main`](https://github.com/TUBS-IVS/populationsim/tree/bs/main), not here.
2. **Identity is the acceptance rule.** An option may be switched on in production only if the synthetic population is byte-identical with it on and off. The fork tests this on its example; the run manifest tests it on real batches in both rounding modes and against the committed hashes of ladder arm 2. A change that alters a result bit is not part of these options.
3. **Tracked keys.** `braunschweig.population.popsim.store_positive_weight_rows_only` and `.fast_paths` (meaning: `braunschweig/popsim/stage/config_keys.py`) are written into every batch settings file and enter the batch signature. An option that is on needs fork version `0.10.0+bs.2` or later: an older fork would ignore the unknown setting silently, so the stage refuses it (`braunschweig.popsim.folders.require_fork_version_for_run_time_options`).
4. **No silent fallback.** The fast balancer kernels fall back to the original kernel, call by call, in counted cases. The stage sums the fork's summary line over the completed batches, logs the primary-vs-fallback rate and warns when a batch reports no fast call (`braunschweig.popsim.stage.fast_path_rate`).
5. **Bit-identity of the fast balancer kernels is a tested property of an environment.** The kernels are compiled with `fastmath`, so the equality with the original kernels is shown by the fork's tests for a given numba and LLVM version and CPU, not by construction. Rule for maintainers: after a change of `environments/populationsim` or of the run machine, run the fork's `tests/test_bs_balancer_fast_path.py` in that environment before a run with `fast_paths` on ([populationsim-fork.md](../codebase/notes/populationsim-fork.md)).
6. **Worker memory and worker count are configuration.** `braunschweig.population.popsim.worker_memory_gb` and the `num_workers` ceiling of the 100 % overlay are set from the peaks the run manifest measured, with their reasoning next to the values. The code default of 30 GiB stays: without the option a worker still needs it. The worker count cannot change results (docstring of `braunschweig.resources.resolve_popsim_workers`).

## Rejected options

- **Sparse reductions, parallel loops or an iteration cap in the balancers, or float32 weights:** each changes the order or precision of floating-point sums and with it result bits; excluded by the acceptance rule.
- **Skipping the update for a zero incidence inside the existing kernels:** tried during development and dropped (no committed measurement): it gave no speed-up and changed the convergence measure; the lookup-table kernels replaced it.
- **Parallelism inside one batch:** the batches already run in parallel; memory was the limit, not cores.
- **Lowering the 30 GiB default in code:** it would be wrong for every run with the option off.
- **Dropping the dense weight tables without a setting:** the stored tables are part of upstream's output; an opt-in setting keeps the default equal to upstream.

## Consequences

- The checkpoint store of a batch shrinks with the option on; the batch diagnostics read both kinds of store alike (check in the run manifest).
- With the option on, a stored weight table of a sub-geography no longer holds the zero rows. A run must be resumed with the value it was started with (fork README).
- More batches fit into memory at once; the worker count of the full rerun is tuned in #281 from the measured peaks.
- The batch signature includes the two options, so switching one recomputes the existing work dirs.

## Evidence

- Run manifest [`popsim-run-performance-2026-11-18`](../runs/popsim-run-performance-2026-11-18.yml): pre-registered checks and results (identity in six pairs, reference to arm 2, fast-path rate, diagnostics, run time, memory, store size). Three batches on one machine; not a proof for every input.
- The fork's test suite at the evidence commit in three environments: `fork_test_suites.txt` of [`popsim-ladder-arm3-2026-11-18`](../runs/popsim-ladder-arm3-2026-11-18.yml).
