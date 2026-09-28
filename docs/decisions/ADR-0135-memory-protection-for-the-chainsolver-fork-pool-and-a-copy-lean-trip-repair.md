# ADR-0135 · 2026-09-28 · Memory protection for the chainsolver fork pool and a copy-lean trip repair

- **Status:** active
- **Numbering:** ADR-0135 is the next free id. Checked on 2026-09-28 across ALL local and remote
  branches (`git ls-tree docs/decisions/` for every ref): `main` holds up to ADR-0133, the unmerged
  branches hold ADR-0130/ADR-0131 (`codex/external-fare-data`), ADR-0132
  (`codex/regional-pt-fares`) and ADR-0134 (`chore/i434-test-suite-followup`).
- **Issue:** none. The three defects were fixed directly on the user's instruction of 2026-09-28.
- **Supersedes / amends:** amends ADR-0126 point 5 (the chainsolver memory bound): its measured
  basis moves from `docs/runs/chainsolver-worker-private-memory-2026-09-17.yml` to
  `docs/runs/chainsolver-pool-memory-2026-09-28.yml`.

## Context

The 100 % production run of 2026-09-25 on the felix server (94.28 GiB) ran into the memory limit
twice. All figures below are from `docs/runs/chainsolver-pool-memory-2026-09-28.yml`.

1. **`braunschweig.popsim.trips_stage`** drove the driver to 86.32 GiB of RSS; 10.14 GiB were left.
   The peak falls into the second `PlanValidator.repair_trips` pass.
2. **`braunschweig.synthesis.locations.secondary_chainsolvers`**, auto-sized with 0.86 GB per
   worker against a 37.55 GB driver, forked 56 workers. Their private memory reached at least
   1.039 GiB each, driver plus pool exceeded the machine, and the kernel OOM killer ended the
   DRIVER first. 52 workers survived it, re-parented to init, and held their memory for two days.

The restart with 16 workers measured the pool cleanly: 1.088 GiB private per worker at the peak,
and from the first pool sample to that peak the private memory per worker grew by 0.618 GiB while
the mean worker RSS grew by 0.093 GiB. A page copied on write replaces a page the worker already
maps, only an allocation grows its RSS, so most of the growth is driver pages the workers copied.

The root causes:

- **The garbage collector copies the driver's heap.** CPython's cyclic collector writes into the
  header of every container object it visits. A forked worker's full collection visits every
  object it inherited from the driver, so every page holding one becomes a private copy. The cost
  grows with the driver's heap, which is why the 2026-09-17 basis (drivers of 19.6-30.1 GB) was
  too low for the 36-38 GiB drivers of this run.
- **Nothing marks the driver as the process that must survive.** The OOM killer ranks processes by
  their footprint, and a forked worker's footprint counts the pages it shares with the driver.
- **Idle pool workers never notice their driver's death.** A `concurrent.futures` worker blocks on
  the call-queue pipe, whose write end every forked sibling inherited, so it never closes.
- **`repair_trips` holds about seven copies of the trip table at once.** On the committed test
  fixture the old code peaked at 7.02 copies above its input. The sources: a `.copy()` before each
  sort that already returns a new frame; a `reset_index` copy after it; the time-repaired and the
  sorted frames kept alive through the closure append; and `hts.fix_trip_times` and
  `hts.compute_activity_duration` shifting the WHOLE table while reading four columns.

## Decision

1. **Freeze the driver heap while workers are forked.**
   `pool_protection.frozen_heap_for_fork` (`gc.freeze()`) wraps every executor generation of
   `_run_shards_with_recovery` and is released afterwards. Frozen objects are skipped by every
   collection, in the workers and in the driver, which is the use the CPython documentation of
   `gc.freeze` gives. `tests/test_chainsolver_pool_protection.py` shows, with a real fork, that a
   worker's full collection copies more than 20 MB of a 400,000-object parent heap without the
   freeze and less than a tenth of that with it.
2. **A pool worker is the kernel's first OOM victim.** Each worker raises its `oom_score_adj` to
   1000. Losing a worker costs only the shards of its executor generation, which are retried.
3. **A pool worker exits when its driver is gone.** A daemon thread compares `os.getppid()` with the
   driver's pid every 5 s and ends the worker with `os._exit`.
   Protections 2 and 3 apply only when the initializer receives the driver's pid, i.e. inside a pool
   worker. The serial path calls the same initializer inside the driver itself.
4. **A retry generation runs with half the workers** (`RETRY_WORKER_DIVISOR`, never below one). A
   lost worker is almost always an OOM kill, and a retry at the same size meets the same limit.
5. **`braunschweig.chainsolvers.worker_memory_gb: 1.09`** in `configs/base_bs.yml`. This is the
   measured 1.088 GiB, rounded up. The code default in `braunschweig.resources` stays 0.86 on
   purpose: `resources.py` and `parallelism.py` are hashed by the PopulationSim stage (630 min of
   wall clock in attempt 1). Editing either would re-run PopulationSim for a change confined to
   this pool. The base config is the active-state truth, so the canonical run uses 1.09. For the
   same reason the protections live in the stage package (`pool_protection.py`), not in
   `braunschweig.parallelism`.
6. **Every run measures its pools.** `braunschweig.monitoring.summary.pool_memory_episodes`
   reports each stage's worker-pool private memory, and the copy-on-write signature next to it, in
   the summary every run writes. This matters most for the next production run: it measures the
   per-worker footprint WITH the freeze, and only that measurement may lower 1.09.
7. **`repair_trips` keeps its output and drops the copies.**
   - The redundant copies and `reset_index` are replaced by `sort_values(ignore_index=True)`.
   - Intermediate frames are deleted as soon as they have served.
   - The two eqasim helpers run on the columns they read, and their results are written back.

   The committed fixture now peaks below four copies (was 7.02). An A/B comparison on five
   synthetic tables (up to 160 columns, with and without `trip_id`, with a capping empirical dwell
   model) gave identical output frames and reports. A guard test pins that the narrow
   `fix_trip_times` call equals the full-table one on chains that reach each of its branches.

## Rejected alternatives

- **Sizing the pool after the shard frames are built.** The argument that the driver grows between
  the sizing line and the fork came from a unit mix-up (kB/10^6 read as GiB). Measured in one unit,
  the driver did not grow: 36.37 GiB at the sizing line, 36.34 GiB in the first pool sample.
- **`prctl(PR_SET_PDEATHSIG)` via `ctypes`.** It fires when the forking THREAD exits and needs
  `ctypes` on Linux. The `getppid` watch is plain Python and also catches a driver that died before
  the initializer ran.
- **`multiprocessing.parent_process().is_alive()`.** Its sentinel pipe's write end is inherited by
  every sibling forked later, so it does not report a dead driver reliably.
- **`gc.disable()` in the workers.** Freezing only the inherited objects keeps the workers'
  collection of their own garbage.
- **Sizing from free memory (`MemAvailable`).** ADR-0126 decided against scaling off free
  capacity. The halving retry covers what the static estimate cannot foresee.
- **Setting `worker_memory_gb` to the pre-growth plateau now,** on the expectation that the freeze
  removes the growth. The freeze's effect in production is unmeasured, and a guessed bound is a
  fabricated reference. The next run measures it (point 6).
- **Narrowing the trip table.** `build_trip_table` preserves all 209 MiD Wege columns on every
  synthetic trip. One copy of the 3,629,255-row table is therefore about 6 GiB (an estimate:
  about 224 columns of 8 bytes). Narrowing it to the
  columns the repair and the downstream stages use is the larger lever. It needs an audit of every
  downstream consumer of those columns first, because a stage that quietly falls back when a
  column is missing would change results unnoticed. It is left to a follow-up.

## Consequences

- **Results do not change.**
  - The pool changes decide which process holds or loses memory and how many processes run the
    fixed shards. Shards and seeds are untouched.
  - The repair changes are bit-identical (A/B and tests above).
- **Cache.** The next run recomputes `braunschweig.popsim.trips_stage` (it hashes
  `plan_validation`), the chainsolver stage (it hashes `parallel_solving` and `pool_protection`) and
  everything downstream. PopulationSim stays cached.
- **Auto sizing on felix.** At a 36.4 GiB driver the pool gets floor((86.28 - 36.4) / 1.09) = 45
  workers. Before, it got 58, and 56 had already exhausted the machine.
- **ASSUMPTION:** at 100 % the trips_stage peak should fall well below the 86.32 GiB. The
  repair's share of that peak shrinks from about seven to fewer than four copies of a table of
  about 6 GiB. It is an estimate until the next run's summary measures it.
- **Monitoring.** Every run summary now carries the pool lines. They are the evidence for the next
  change to either worker-memory bound.

## Evidence

- `docs/runs/chainsolver-pool-memory-2026-09-28.yml`
- Tests:
  - `tests/test_chainsolver_pool_protection.py`: the protections, the halving and the wiring, with
    real fork tests on Linux.
  - `tests/test_monitoring_pool_memory.py`: the pool-memory reduction.
  - `tests/test_popsim_plan_validation_memory.py`: the copy bound and the narrow-helper guard.
- `braunschweig/synthesis/locations/secondary_chainsolvers/pool_protection.py`
- `docs/codebase/notes/chainsolver-fork-pool-memory.md`
