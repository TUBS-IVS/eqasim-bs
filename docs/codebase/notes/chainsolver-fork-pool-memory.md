# Chainsolver fork pool: memory rules

## What it is

The chainsolver stage (`braunschweig.synthesis.locations.secondary_chainsolvers`) runs the
pipeline's only `fork` process pool (`parallel_solving._run_shards_with_recovery`). Every worker
shares the driver's whole address space copy-on-write. `pool_protection.py` holds the three
protections of ADR-0135. The measurements behind them are in
`docs/runs/chainsolver-pool-memory-2026-09-28.yml`.

| Protection | Side | Defect it closes |
|---|---|---|
| `frozen_heap_for_fork` (`gc.freeze`) | driver, around every executor generation | a worker's garbage collection copied the driver's object pages (0.62 GiB private growth against 0.09 GiB RSS growth per worker) |
| `prefer_oom_kill_of_current_process` (`oom_score_adj` 1000) | worker initializer | the kernel killed the DRIVER, which ends the stage; a killed worker only costs retried shards |
| `exit_when_parent_dies` (`getppid` watch, 5 s) | worker initializer | 52 workers outlived the killed driver for two days |

A retry generation after a lost worker runs with half the workers
(`parallel_solving.RETRY_WORKER_DIVISOR`).

## Rules that are not obvious from the code

- **The worker protections are for pool workers only.** `_init_chain_worker` applies them only when
  it gets `pool_parent_pid`. The serial path calls the same initializer INSIDE the driver. There the
  OOM score would make the driver the first victim, and the parent watch would end the driver
  together with its shell. Never pass the pid on the serial path.
- **Keep the freeze around the fork, not around the work.** Every executor generation forks at its
  first `submit`, so the freeze must span the whole `_run_shards_with_recovery` loop. It releases in
  a `finally` and leaves an earlier freeze by someone else in place (`gc.unfreeze` releases
  everything).
- **A worker's RSS is not its cost.** Under fork it counts every page shared with the driver.
  Measure a pool with `braunschweig.monitoring.summary.pool_memory_episodes` (every run summary
  carries its lines), never by summing worker RSS.
- **Change `worker_memory_gb` only on a measurement.** 1.09 in `configs/base_bs.yml` is the maximum
  measured WITHOUT the freeze. Lower it only with the pool line of a run that had the freeze, via a
  run manifest.
- **Keep this pool's changes out of `braunschweig.parallelism` and `braunschweig.resources`.** Both
  are hashed by the PopulationSim stage (630 min at 100 %). An edit there, even a comment, re-runs
  PopulationSim. This is also why the code default of `worker_memory_gb` in `resources.py` still
  reads 0.86 while the base config carries 1.09.
- **The tests that prove the protections need Linux.** The fork, `/proc/self/smaps_rollup` and
  `/proc/self/oom_score_adj` tests skip on Windows. Run `tests/test_chainsolver_pool_protection.py`
  on the Linux mirror before trusting a change here (`docs/codebase/notes/linux-parity-testing.md`).
