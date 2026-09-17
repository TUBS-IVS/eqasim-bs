# ADR-0126 · 2026-09-16 · Scale resource-sensitive configuration off the machine's total allocation, never its free capacity

- **Status:** active; amended 2026-09-17 by point 5 (memory-bounded chainsolver worker pool)

## Context

The run server (`ssh felix`) is a KVM virtual machine whose CPU/RAM allocation
changes depending on who provisions it, and `configs/base_bs.yml` pins several
resource-sensitive values (`processes`, `java_memory`, `matsim_threads`,
`matsim_qsim_threads`) for one allocation that is never revisited when the VM
is resized. The box was resized DOWN from the 128 GB the configs still
assumed (`configs/overlays/test_100pct.yml` still documents a "~90 GB on the
128 GB box" budget), and the drift was already live: `java_memory: 100G`
exceeded the machine's actual physical RAM.

Measured on the run server on 2026-09-16 (`ssh felix`):

```
systemd-detect-virt        -> kvm
cgroup v2 cpu.max           -> absent (no container CPU limit)
cgroup v2 memory.max         -> absent (no container memory limit)
nproc = os.cpu_count()
    = len(sched_getaffinity(0)) -> 64
free -g                     -> total 94   available 92   swap 1
```

There is no cgroup or container layer masking the allocation, and CPU
affinity equals the core count, so `sched_getaffinity` and `/proc/meminfo`
report the VM's true allocation: detection is meaningful. Nothing in the
pipeline had ever compared a pinned value to the machine it actually ran on,
so the mismatch was invisible until a run failed.

**On the memory figure.** `free -g` prints whole gibibytes and truncates, so
its `total 94` is a rounded reading of this box, not its exact size. The
traceable exact figure is the run resource recorder's `memory_total_kb`, which
reads **94.28 GB** for the 2026-09-15 and 2026-09-17 series under
`/home/felix/i409_runs/*` and
`/home/felix/eqasim-bs/eqasim-data/cache_i385_smoke/monitoring/` (recorded in
`docs/runs/chainsolver-worker-private-memory-2026-09-17.yml`). This document,
`braunschweig/resources.py` and the tests use **94.28 GB total / 86.28 GB
budget** consistently; the `free -g` line above is kept as the 2026-09-16
observation it is.

A second, related defect surfaced while designing the fix:
`braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py`
partitioned the secondary-location solve into as many person shards as
worker processes, and each shard seeds its own random stream from
`(random_seed, shard_index)`. Because `braunschweig.chainsolvers.processes`
carried the sentinel `0` in the canonical config (auto-scale to
`cpu_count - 2`), the realised shard count -- and therefore the
secondary-location output -- silently depended on the machine's core count,
and a cached artifact keyed on the sentinel `0` was indistinguishable across
machines that produced it with different worker counts. Making the rest of
the configuration adapt to the machine would have made this defect strictly
worse (every clamp would newly change worker counts that used to be fixed by
hand), so it is fixed as part of this decision rather than left for later.

## Decision

1. **Scale off the machine's TOTAL allocation, detected once per run.**
   `braunschweig/resources.py` detects cores (`os.sched_getaffinity`, falling
   back to `os.cpu_count()` with the fallback logged) and total memory
   (`psutil.virtual_memory().total`, falling back to `/proc/meminfo
   MemTotal`, again logged), raising `ResourceDetectionError` if neither
   source of a quantity is available -- a guessed value would silently size
   every worker pool and JVM heap of the run wrongly. A fixed OS/driver
   reserve (`cores - 2`, `memory_gb - 8`, `resources.DEFAULT_CORE_RESERVE` /
   `resources.DEFAULT_MEMORY_RESERVE_GB`) is subtracted to produce one
   `ResourceBudget` per run. The core reserve historically matched
   `parallelism.DEFAULT_CORE_RESERVE`, but that constant was deleted in this
   change along with `parallelism.resolve_workers` (no production caller
   survived the switch), so there is no second mechanism left to stay aligned
   with. `EQASIM_CPU_BUDGET` / `EQASIM_MEM_BUDGET` let an
   operator override the detected budget explicitly (e.g. to deliberately
   share a box), taken verbatim with no further reserve subtracted.
2. **A configured value is a ceiling, not a target -- for OPERATIONAL keys
   only.** An operational key is used verbatim when it fits the budget, and
   clamped down -- logged as a deviation and recorded in the run provenance --
   when it does not. The RESOLVED value never enters the config file or
   `.merged_config.yml`; resolution happens in code at the point of use
   (`matsim/runtime/java.py` and `data/osm/osmosis.py` for `java_memory`;
   `braunschweig/popsim/stage/__init__.py`
   (`_read_batching_and_scope_config`) for
   `braunschweig.population.popsim.num_workers`). `processes` is deliberately
   NOT in this list (see point 3): `synthesis/population/matched.py` and
   `synthesis/population/spatial/secondary/locations.py` read
   `context.config("processes")` unchanged, with no resolution step at all.
   Because the config value is untouched, the synpp
   stage hash -- derived from each stage's declared config dependencies,
   `braunschweig/cache_share.py` -- does not change, and the shared cache
   survives a machine resize. This is why clamping is free: no cache is
   invalidated by it.
3. **Only OPERATIONAL keys may be clamped.** `java_memory` only ever becomes
   `-Xmx`; PopulationSim's `num_workers` submits one independent subprocess
   per batch folder with no seed depending on the worker index -- both are
   operational and are clamped. **`processes` is NOT operational and is NOT
   clamped.** An earlier draft of this decision classified the two pure
   `processes` read sites (`synthesis/population/matched.py`,
   `synthesis/population/spatial/secondary/locations.py`) as safe to clamp,
   on the claim that they "split a matching/solve workload across a pool with
   no worker-index-dependent seed". That claim does not survive reading the
   consumers: both sites do `np.array_split(<ids or frame>, processes)` --
   `processes` fixes the person-chunk PARTITION -- and then draw one seed per
   chunk, `synthesis/population/matched.py` as
   `random.randint(10000, size = len(chunks))` and
   `synthesis/population/spatial/secondary/locations.py` as
   `random.randint(10000, size = processes)`. The two spellings are the same
   quantity (`len(chunks) == processes`, since the chunks come straight from
   `np.array_split(..., processes)`), so at BOTH sites `processes` also fixes
   how many seeds are drawn. Changing `processes` therefore changes which persons
   share a chunk and which seed each chunk gets, exactly the chainsolver
   defect this ADR otherwise fixes (point 4 below), just unfixed at these two
   sites. `processes` is therefore treated exactly like `matsim_threads` /
   `matsim_qsim_threads`: reported and warned about (the startup report warns
   when a pin sits below 75% of the core budget, i.e. under-uses the
   machine), never adjusted. The two consumers read
   `context.config("processes")` verbatim, unchanged from before this ADR.
   **That under-use warning fires on EVERY production run and is expected
   output.** `configs/base_bs.yml` pins `processes: 32` against a 62-core
   budget on the run server, i.e. below the 75% threshold, so every run logs
   `[resources] processes is pinned to 32 but 62 cores are budgeted ...`. It is
   the mechanism reporting a deliberate pin, not a defect; nobody should act on
   it. It is named as expected output here, in
   `docs/registry/features/resource_adaptive_config.yml`'s expected-smoke list,
   and in the `configs/base_bs.yml` comment itself.
   **AMENDMENT (2026-09-17): the cache mismatch behind that warning is RESOLVED,
   not merely stated.** Both read sites used to declare `processes` with
   `volatile = True`, carried in from upstream eqasim-france #438 (ported in
   `79f7f492`), whose comment calls the key an "execution detail, not scientific
   config". A survey of all four declarations showed that premise holds at
   exactly two of them and fails at exactly the other two -- and the split is
   mechanical: **the only two files that declare `processes` volatile are the
   only two that hand it to `np.array_split`.** Measured on the read sites' own
   arithmetic: moving from `processes: 8` to `processes: 32` changes the drawn
   seed of **19 of 20 persons**, because the partition decides which persons
   share a chunk and each chunk is solved with its own seed. Since a volatile
   key is excluded from the stage hash, raising the pin changed the
   statistical-matching and secondary-location realisations while the cache
   served the old ones.

   `synthesis/population/matched.py` and
   `synthesis/population/spatial/secondary/locations.py` therefore now declare
   `processes` HASHED, deliberately diverging from upstream at these two sites
   (each carries a comment saying so and why). The two MATSim stages that only
   forward the value as a `--threads` / `numOfThreads` argument keep
   `volatile=True`: there the upstream premise is correct, and hashing them
   would cost recomputes for nothing. `braunschweig/resources.py`'s under-use
   warning no longer carries the caveat, because it is no longer true.

   **Cost, stated:** those two stages' hashes change once, and thereafter every
   change to `processes` recomputes them. That recompute is the correct
   behaviour -- it is the price of a changed scientific realisation, not a
   regression. Results at a fixed `processes` are unchanged.
   `tests/test_processes_hash_coverage.py` pins the classification and derives
   it from the source, so a stage that starts partitioning in future cannot
   quietly inherit the wrong flag.
   `matsim_threads` and `matsim_qsim_threads` are excluded from clamping for
   an independent reason: their effect on results is unverified and MATSim
   parallelisation is known to scale poorly on this server (issue #410). They
   keep their configured values (56 / 16) unconditionally; the startup report
   only WARNS when they exceed the budget, because oversubscription is slow,
   not wrong, and silently adjusting a key whose effect on results is
   unverified would risk changing science without anyone deciding to.
4. **Separate the chainsolver SHARD count from its WORKER count.** A new
   config key `braunschweig.chainsolvers.shards` (default **62**, hashed,
   i.e. a change invalidates the stage cache as it must) fixes the person
   partition and therefore every shard's derived seed --
   `_make_person_shards` already took the shard count as a parameter, so the
   fix is to pass it the configured shard count instead of the worker count.
   `braunschweig.chainsolvers.processes` keeps deciding how many processes
   chew through that fixed shard set, and is now declared `volatile=True`
   (operational, no influence on the result). The default 62 is not
   invented: it is the worker count every production run on the 64-core
   server used (`available_cores(reserve=2) = 64 - 2 = 62`), independently
   recorded in two committed comments (`parallel_solving.py`'s "one of 62"
   and the stage's own `configure()`) before this change existed --
   pinning 62 therefore reproduces the existing production realisation
   bit-for-bit on any machine, FOR A REAL POPULATION (`n_total > 1`).
   **That reproduction claim is an INFERENCE from reading the pre-change code,
   not a measured or tested result.** The argument is: before this change the
   pool size came from `parallelism.resolve_workers(0)` =
   `available_cores(reserve=2)` = `os.cpu_count() - 2` = 62 on the 64-core
   server, the shard count WAS the pool size, and `_make_person_shards` /
   `_derive_shard_seed` are unchanged -- so a run pinned to `shards: 62`
   partitions and seeds identically to every past production run on that box.
   No test verifies it (none could without a production artifact to compare
   against), and no artifact comparison has been run; it is argued here so a
   reader can check the argument, not asserted as evidence.
   `tests/test_chainsolvers_parallel.py::test_shard_tasks_are_invariant_to_the_worker_count_and_pool_follows_workers`
   pins the structural half of it -- that the shard tasks depend only on the
   shard count and the pool size follows the worker count -- on any platform.
   At
   exactly one unique person, `_make_person_shards` caps the shard count at
   `len(unique_persons) = 1` regardless of the configured 62, so that single
   shard is seeded via `_derive_shard_seed(base_seed, 0)` -- the same
   SeedSequence-derived formula every sharded shard uses -- and NOT via the
   plain `base_seed` the genuinely SERIAL path uses (taken only when
   `braunschweig.chainsolvers.shards` is explicitly configured to `1`; see
   the stage's own startup warning for that case). Production never runs at
   `n_total = 1`, so this does not affect the reproduction claim above; it is
   recorded so the claim is not overstated for a tiny fixture or smoke run.
   More generally, `_make_person_shards` caps the shard count at
   `len(unique_persons)` for ANY population smaller than the configured shard
   count, so a fixture, smoke or single-Kreis pass runs FEWER shards than
   configured and produces the realisation for that smaller number. The cap is
   deterministic (it depends on the population, not on the machine), so
   reproducibility holds -- but it silently adjusts a result-determining key, so
   `_solve_problem_set` now prints the EFFECTIVE shard count in its headline
   line and warns, naming `braunschweig.chainsolvers.shards`, whenever it
   differs from the configured one. The headline previously asserted the
   configured value, e.g. "parallel, 62 shards / 8 workers" for a pass that
   actually ran 10 shards.
   A run previously executed on a machine with a
   different core count (e.g. a developer laptop) produces a different --
   equally valid -- realisation once under this default; that is the
   intended, one-time correction, stated here rather than absorbed silently.

5. **AMENDMENT (2026-09-17): the chainsolver worker pool IS now memory-bounded,
   on measured grounds.** The "Non-goals" section of the design spec this ADR
   implements (`docs/superpowers/specs/2026-09-16-resource-adaptive-config-design.md`)
   stated that the chainsolver pool's worker count "stays CPU-derived, as
   today" because "no per-worker footprint has ever been measured for that
   stage, and inventing one is forbidden (CLAUDE.md)"; `braunschweig/resources.py`'s
   own `build_report` docstring and `DEFAULT_CORE_RESERVE` comment made the
   same claim in code. That premise no longer holds: the run resource recorder
   (issue #350) recorded seven deduplicated 100% ZGB-8 executions of
   `braunschweig.synthesis.locations.secondary_chainsolvers` on the felix
   server (2026-09-05 to 2026-09-09), and a post-hoc measurement over those
   recorder series (`docs/runs/chainsolver-worker-private-memory-2026-09-17.yml`)
   establishes the pool's MAXIMUM measured private per-worker memory footprint
   at 0.86 GB (`braunschweig.resources.DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB`),
   computed as (available-memory drop during the stage, minus driver RSS
   growth, which was 0.0 GB in every run) divided by the 62 live workers.
   Per-worker RSS itself is NOT usable for this bound: on the 125.78 GB
   machine the summed worker RSS reads 1200-1860 GB, because more than 95% of
   each forked worker's RSS is copy-on-write pages inherited from the driver
   at fork time, not memory the pool actually needs.
   `braunschweig.resources.resolve_chainsolver_workers` /
   `effective_chainsolver_workers` now bound the pool by
   `workers = max(1, min(core_budget, floor((memory_budget_gb -
   driver_rss_gb_live) / worker_memory_gb)))`, called from the stage's
   `_solve_problem_set` at stage start. `driver_rss_gb_live` is THIS process's
   own live RSS at that moment (`current_process_rss_gb`, psutil first, then
   `/proc/self/status VmRSS`, logged at INFO which source was used; a WARNING
   is logged and the bound falls back to core-only when neither is available
   -- CLAUDE.md: no silent fallbacks), because the worker pool is forked from
   the driver and both compete for the same budget; this is the run's own
   deterministic state, not other users' contention, so it is consistent with
   Decision 1's "scale off the total allocation, not free capacity". The new
   config key `braunschweig.chainsolvers.worker_memory_gb` (default 0.86,
   OPERATIONAL, `volatile=True`) carries the measured figure; it sizes the
   pool, never the partition, so it has no influence on any result.
   **Where the bound actually binds, and what is NOT known about it.** On the
   server measured for the original decision above (94.28 GB total, 86.28 GB
   budget, 0.86 GB/worker), the memory term drops the pool below the 62-worker
   core budget from a driver RSS of roughly **32.96 GB** upwards
   (`86.28 - 62 x 0.86`): at ~25 GB the arithmetic yields 71 workers and cores
   still cap it at 62 (unchanged); at 36 GB it yields 58. Whether any real run
   sits above or below that threshold is **UNVERIFIED**, because the quantity
   the code reads was never sampled: `effective_chainsolver_workers` is called
   inside `_solve_problem_set`, after `plans_for_cs` is built and immediately
   before the pool is forked, while the run resource recorder captured a
   BEFORE-stage baseline driver RSS (19-36 GB across the seven runs) and a
   DURING-stage minimum (19.6-30.1 GB) -- neither is the fork-point value. The
   binding threshold of ~33 GB lies inside the recorded baseline range, so at
   the low end of that range the bound would not have throttled a historical
   run and at its top it would have (to 58 workers). This ADR does not resolve
   that by picking the convenient number: the ambiguity is recorded as its own
   row in `docs/runs/chainsolver-worker-private-memory-2026-09-17.yml`, and the
   outstanding evidence is the server smoke, whose
   `[resources] braunschweig.chainsolvers.processes: ... driver RSS <measured>
   GB ... -> ceiling <n> workers` line logs the fork-point value directly. The
   decision to apply the bound does not depend on resolving it: a bound that
   never binds costs nothing, and one that binds prevents an overcommit. Stated
   precisely, that overcommit is of the run's own BUDGET, not of the machine:
   an unbounded 62-worker pool at a 36 GB driver would want
   `36 + 62 x 0.86 = 89.3 GB`, which exceeds the 86.28 GB budget but still fits
   inside the 94.28 GB the box physically has. It eats the OS/page-cache reserve
   rather than killing the run outright -- the reserve exists so that a second,
   unannounced run on the same box does not turn that into the 2026-08-20
   kernel OOM kill (ADR-0097).
   This ALSO reverses the "deliberately excluded / unmeasured"
   framing this ADR, `docs/codebase/notes/resource-budget.md` and
   `braunschweig/resources.py`'s own `build_report` docstring and
   `DEFAULT_CORE_RESERVE` comment previously carried: those documents stated
   the pool's footprint was "unmeasured" and the pool was resolved "entirely
   outside this module" via `parallelism.resolve_workers` -- that is no longer
   true (`parallelism.resolve_workers` has been removed outright, having lost
   its only production caller), and this ADR says so plainly rather than
   leaving the earlier, now-incorrect statement standing.
   **This adopts, on measured grounds, the memory cap ADR-0097 explicitly
   rejected** ("Rejected: a static memory cap on the pool", citing "the shard
   count IS the worker count, so lowering the pool size makes workers process
   several shards sequentially" as the reason a cap would cost wall clock for
   no benefit). Decision 4 above is exactly what removed that premise: since
   the shard/worker split, the shard count and the worker count are
   independent, so a memory-bounded pool that falls below the shard count
   simply runs more shards per worker sequentially -- costing wall clock only
   when the bound actually bites, never a permanent, unconditional tax. See
   ADR-0097's Status entry for its own acknowledgement of this supersession.

## Rejected alternative

Scaling off CURRENTLY FREE resources (contention-aware sizing: "how much of
the box is idle right now") was considered and rejected. Two runs started
close together would each observe "everything is free" at the moment they
measure, and both would size their worker pools and JVM heaps against the
(apparently) whole machine, jointly overcommitting it the instant both
became busy -- the exact failure class of the 2026-08-20 kernel OOM kill
(ADR-0097), where a second, unrelated PopulationSim run competing for memory
got seven processes killed mid-run, including one of the chainsolver stage's
62 shards, after that stage had already run 7.6 h. Free-resource scaling also
means no two runs of the identical configuration are comparably dimensioned:
the same config on the same physical machine could size itself completely
differently between two invocations depending on unrelated concurrent load,
which is worse for reproducibility than a fixed-allocation clamp, not better.
The decision (2026-09-16) is therefore to scale off the machine's TOTAL
allocation, detected once at the start of a run and held fixed for its
duration, and to accept that a run sharing the box with unannounced
concurrent load can still be memory-pressured -- exactly the risk the
existing shard-retry mechanism (ADR-0097) exists to survive, not something
this decision tries to prevent by watching load.

## Consequences

- The `java_memory` and `popsim.num_workers` clamps change no config value
  and no stage hash: a run on a smaller machine reuses the identical shared
  cache a run on the full-size server would produce. This part of the change
  is free in cache terms. `processes` is NOT in this list (point 3 above):
  it is not clamped at all, so it contributes nothing to discuss here in
  cache terms either -- it was never resolved into a config-independent
  value in the first place.
- **The `popsim.num_workers` clamp is not free operationally: it is a real,
  material drop in parallelism on the most expensive stage.** On the server
  measured for this ADR (94.28 GB total -> 86.28 GB budget, 30 GB/worker ->
  ceiling 2), all SEVEN overlays that pin `num_workers: 3`
  (`configs/overlays/test.yml`, `test_1pct.yml`, `test_25pct.yml`,
  `test_100pct.yml`, `test_matsim.yml`, `escort_reuse_5pct.yml`,
  `srv_location_reuse_5pct.yml`) are clamped 3 -> 2 (a ~33% drop), and
  `configs/overlays/smoke_kreis_control_fit.yml`, which pins `num_workers: 4`,
  is clamped 4 -> 2 (a ~50% drop). This is exactly the deviation the startup
  report and the run provenance are built to surface (never silently), but
  it should be read as an operational regression against the pre-ADR
  behaviour, not as a free correctness fix, and is expected in the
  outstanding real-machine smoke evidence (see the Feature Registry's
  `validation.note`).
- **The 30 GB/worker figure may only ABORT a run at the scale it was measured
  at.** It comes from a 100%-scale post-mortem, and applying it at error
  severity to every sampling rate aborted both committed 1% PopulationSim
  fixtures (`configs/fixtures/config_popsim_mid_braunschweig.yml`,
  `config_smoke_popsim_mid_mini.yml`) before any stage ran on a developer
  machine below ~38 GB -- runs that worked before this gate existed.
  `build_report` therefore raises an ERROR only when the run selects a
  PopulationSim method AND either is configured at `sampling_rate >= 1.0` or
  sets `braunschweig.population.popsim.worker_memory_gb` explicitly (the
  operator asserting the figure applies to this run). Otherwise it WARNS, with
  the caveat spelled out in the message. A missing `sampling_rate` counts as
  "not 100 %". No scale-to-memory relationship is assumed or invented: the
  per-worker footprint below 100% is simply unmeasured, and the gate says so
  instead of extrapolating (CLAUDE.md: no invented reference values).
- **The 30 GB/worker figure is traceable evidence (the 2026-07-10 OOM
  post-mortem); the 8 GB memory reserve subtracted before that bound is
  applied is NOT.** `DEFAULT_MEMORY_RESERVE_GB = 8.0` has no committed
  source and is the number that decides whether the clamp above fires at
  all: at `reserve = 8`, `3 x 30 = 90 > 86.28` clamps to 2, but at
  `reserve = 4` -- roughly what `configs/overlays/test_100pct.yml`'s own
  "~90 GB on the 128 GB box" budget implies for 3 workers on the machine
  this ADR measures -- `3 x 30 = 90 <= 90.28` would NOT have clamped. The value
  is labelled an ASSUMPTION at its definition in `braunschweig/resources.py`
  (CLAUDE.md "No invented reference values") and is not yet measured; a
  server measurement of the OS/driver/page-cache footprint next to a real
  run is the outstanding evidence that would let this reserve be tightened
  or confirmed without guessing.
- The chainsolver shard/worker split is NOT free: it adds one new hashed key
  to `braunschweig.synthesis.locations.secondary_chainsolvers`'s
  `configure()`, so that stage and everything downstream of it recompute
  once on the first run after this change lands. At full population scale
  this is a multi-hour, one-time recompute; every run after that is
  cache-stable again. This is accepted because it buys permanent
  cross-machine reproducibility for a mechanism that previously had none,
  but it is a real cost and is recorded as such, not implied to be free
  alongside the operational clamps above.
- **The PopulationSim stage's cache dependency on `braunschweig/resources.py`
  is ALSO not free, and not only once.** `braunschweig/popsim/stage/__init__.py`
  adds `"braunschweig.resources"` to `_DEFERRED_HELPER_MODULE_NAMES`, which
  feeds that stage's `validate()` source-hash token (see that function's
  docstring for the covered-boundary rule this follows). Landing this ADR
  therefore invalidates the full-scale PopulationSim cache once, exactly
  like the chainsolver shard key above -- but unlike that one-time cost,
  this dependency is PERMANENT: any future edit to `resources.py`, including
  a log-message wording change or a docstring-only edit with no behavioural
  effect, changes that file's source text and therefore invalidates the
  100% PopulationSim cache again, because `inspect.getsource` hashes the
  whole file, not a semantic diff. This is a deliberate consequence of the
  repository's documented import-site boundary rule (a direct dependency is
  hashed by import site, not by whether it can change the stage's result;
  see `validate()`'s docstring), not an oversight -- but it means
  `braunschweig/resources.py` should be treated with the same "this file is
  expensive to touch" discipline as any other module already in that stage's
  covered set, and a future contributor changing it should expect the next
  full popsim run to recompute.
- **AMENDMENT (2026-09-17): the chainsolver stage now carries the identical
  permanent cache dependency on `braunschweig/resources.py`.** Since the
  memory-bound amendment (point 5 above), `secondary_chainsolvers/__init__.py`
  imports `braunschweig.resources` at module level and lists it in
  `_HELPER_MODULES`, exactly like the popsim stage's `_DEFERRED_HELPER_MODULE_NAMES`
  entry -- any future edit to `resources.py`, including a wording-only change,
  now invalidates BOTH the full-scale PopulationSim cache and the full-scale
  chainsolver cache. This is the same deliberate, permanent cost the bullet
  above already accepts for PopulationSim, now doubled to a second stage; it
  is recorded here rather than left implicit.
- **AMENDMENT (2026-09-17): whether the chainsolver bound would ever have bound
  in practice is UNVERIFIED, and the smoke is the evidence that settles it.**
  On the 94.28 GB / 86.28 GB-budget server this ADR measures, with 0.86
  GB/worker, the memory term drops the pool below the 62-worker core budget
  from a driver RSS of about **32.96 GB** upwards (`86.28 - 62 x 0.86`); at
  ~25 GB the pool is still core-bound at 62, at 36 GB it is 58. The code reads
  the driver's RSS at the FORK POINT inside `_solve_problem_set`, and the run
  resource recorder sampled neither that instant nor anything equivalent to it:
  it recorded a BEFORE-stage baseline of 19-36 GB and a DURING-stage minimum of
  19.6-30.1 GB across the seven runs in
  `docs/runs/chainsolver-worker-private-memory-2026-09-17.yml`. The ~33 GB
  threshold sits INSIDE the recorded baseline range -- at its low end the bound
  would not have throttled a historical run, at its top it would have (to 58
  workers). An earlier wording of this bullet called the 58-worker case "a
  worked hypothetical for a driver heavier than any of the seven measured"
  while simultaneously sourcing the 36 GB figure from the observed baseline
  range; that is self-contradictory and is retracted. The honest statement is
  the one above: the bound's practical effect on past runs is unknown, and the
  server smoke -- which logs the fork-point driver RSS and the resulting
  ceiling -- is the outstanding evidence. The sampling-point ambiguity is
  recorded as its own row in the run manifest.
- `matsim_threads` / `matsim_qsim_threads` are untouched; an oversubscribed
  run on a shrunk machine is slower, not silently wrong, and the mismatch is
  now visible as a startup warning instead of invisible, pending the tuning
  work tracked under issue #410.
- **AMENDMENT (2026-09-17): the startup gate has exactly one failure channel.**
  A machine-FIT mismatch became a `Violation` that `enforce_report` rendered
  into an actionable message naming the key, but a config-VALUE defect
  (negative, non-integral, unparseable) raised out of the resolver, past
  `build_report`, past `enforce_report` and out of `scripts/run_synpp.py` as a
  bare traceback. Reproduced on a 64-core / 94.28 GB machine: **twelve distinct
  defects took the exception channel and none reached the gate**, across
  `java_memory`, `processes`, `braunschweig.chainsolvers.processes`,
  `braunschweig.population.popsim.num_workers` and both `*_worker_memory_gb`
  keys. `build_report` now converts a resolver's `ValueError` into an
  error-severity `Violation` against the key at fault and keeps resolving the
  remaining keys, so an operator sees every unusable key in one run instead of
  rediscovering the next after each correction; the gate's header is
  correspondingly "The run cannot start with the resolved configuration",
  which is true of both kinds. Two cases stay exceptions on purpose, because
  they happen before a budget exists and so cannot become a `Violation`:
  `ResourceDetectionError` (no machine, therefore no report at all) and an
  unusable `EQASIM_CPU_BUDGET` / `EQASIM_MEM_BUDGET` -- the latter two now name
  the variable, the value and the accepted spelling, and reject non-positive
  values instead of silently becoming a 1-core budget while the log reported
  "machine: 0 cores". The same investigation fixed a misattribution: with
  `braunschweig.chainsolvers.processes` unset, `build_report` falls back to the
  global `processes`, and the message named the chainsolver key the operator
  had never set.
- **Limitation, stated rather than buried.** The claim "secondary locations
  no longer depend on the machine" is verified for the shard partition, the
  per-shard seeds and the recombination order
  (`tests/test_chainsolvers_parallel.py`, in particular
  `test_shard_partition_depends_only_on_the_shard_count` and
  `test_shard_tasks_are_invariant_to_the_worker_count_and_pool_follows_workers`,
  the platform-independent one that pins the split itself).
  **The separate "reproduces the production realisation bit-for-bit at
  `shards: 62`" claim is an inference from reading the pre-change code
  (Decision 4), NOT a test result.** An earlier wording of this bullet cited
  `test_solve_chains_parallel_is_invariant_to_the_worker_count` and
  `test_a_single_worker_still_produces_the_sharded_realisation` as its
  verification; they do not verify it. Both run at `n_shards = 8` over 50
  synthetic persons against a stateless fake `chainsolvers` module (the
  optional package is not installed in the environment they were written in),
  neither exercises 62 shards, neither compares against a production artifact,
  and both are fork-gated (skipped on Windows). What they DO establish is
  worker-count invariance at a fixed shard count, on that fake, where `fork`
  exists -- which is worth having, and is all they are cited for now.
  The end-to-end claim against the real solver is NOT verified. Off the
  server -- e.g. `processes: 8` with `shards: 62` -- several shards run
  sequentially inside one worker process; if the real `carla_sample` solver
  held any process-global RNG state or cache outside the context it is
  explicitly passed, results could still depend on the worker count through
  that back door. The discharge this ADR owes and has not yet collected: a
  server A/B at `shards: 62` comparing `processes: 8` against `processes: 62`,
  asserting byte-identical secondary-location output against the real solver.
- **Code inspection of the installed `chainsolvers` package (supporting, NOT a
  substitute for the A/B above).** Reading the version installed in the project
  environment: the run's randomness is carried by `RunnerContext.rng`, a
  `numpy.random.Generator` built once per `cs.setup(rng_seed=...)` in
  `run.py::_normalize_rng` and handed to each solver instance through
  `_instantiate_solver`. There is **no module-level RNG, no `lru_cache` and no
  other module-level mutable state anywhere in the package**. Worker REUSE is
  therefore state-free in the real package, not only in the test fake -- the
  "back door" the limitation above names has no visible opening in this version:
  nothing survives from one `setup()` to the next inside a reused process.
  Three unseeded `numpy.random.default_rng()` fallbacks do exist and are stated
  rather than glossed over, because none is module-level state:
  `scoring_selection.Selector.select` falls back per CALL when it is passed no
  rng (pre-existing, explicitly out of scope for this branch);
  `solvers/dp.py`'s constructor falls back per INSTANCE when constructed without
  one (unreachable through `_instantiate_solver`, which always passes a
  Generator); and `run.py::_normalize_rng` itself falls back when called with
  neither `rng` nor `rng_seed`. The third is unreachable from this project:
  `parallel_solving.py::_solve_person_shard` always calls `cs.setup` with
  `rng_seed=int(shard_seed)`, so `_normalize_rng` always takes its
  `rng_seed is not None` branch (`np.random.default_rng(int(rng_seed))`), never
  the bare `np.random.default_rng()` fallback. They can make a run
  non-reproducible; they cannot make it depend on the worker count. This is an
  inspection of a pinned third-party version at
  one point in time, so it cannot discharge the A/B: it does not cover a future
  version of the package, anything the solver reaches through its own
  dependencies, or a difference the fake hides. It is recorded so the residual
  risk is known to be small and WHY, not to close the item.

## Evidence

- Design spec: `docs/superpowers/specs/2026-09-16-resource-adaptive-config-design.md`
- Contributor note: `docs/codebase/notes/resource-budget.md`
- Feature registry: `docs/registry/features/resource_adaptive_config.yml`
- Prior incident this decision explicitly avoids repeating: ADR-0097
  (2026-08-20 kernel OOM kill)
- Deferred tuning, out of scope here: issue #410 (`matsim_threads` /
  `matsim_qsim_threads`)
- Tests: `tests/test_resources_detection.py`, `tests/test_resources_budget.py`,
  `tests/test_resources_java_memory.py`, `tests/test_resources_popsim_workers.py`,
  `tests/test_resources_processes.py`, `tests/test_resources_report.py`
  (including the scale gate and the two committed popsim fixtures),
  `tests/test_resources_run_start.py`, `tests/test_chainsolvers_parallel.py`,
  `tests/test_resources_chainsolver_workers.py` (point 5 amendment),
  `tests/test_run_synpp_arity.py::test_main_hands_the_resource_report_to_the_provenance_record`
  and `tests/test_run_provenance.py` (the run-start contract and the
  `"resources"` embedding),
  `tests/test_passive_joint_two_pass.py::test_solve_problem_set_obtains_its_worker_count_from_resources`
  and its two effective-shard-count cases
- Measured machine state (2026-09-16, `ssh felix`): `systemd-detect-virt` ->
  `kvm`; cgroup v2 `cpu.max` / `memory.max` absent; `nproc` =
  `os.cpu_count()` = `len(sched_getaffinity(0))` = 64; `free -g` total 94,
  available 92, swap 1 -- `free -g` truncates to whole gibibytes; the exact
  total is 94.28 GB (see Context and the run manifest).
- Measured chainsolver worker-pool memory (2026-09-17 amendment, point 5):
  `docs/runs/chainsolver-worker-private-memory-2026-09-17.yml` (seven
  deduplicated 100% ZGB-8 executions, felix, 2026-09-05 to 2026-09-09).
