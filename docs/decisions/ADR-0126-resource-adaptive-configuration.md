# ADR-0126 · 2026-09-16 · Scale resource-sensitive configuration off the machine's total allocation, never its free capacity

## Status

Active.

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
   reserve (`cores - 2`, `memory_gb - 8`, matching
   `parallelism.DEFAULT_CORE_RESERVE`) is subtracted to produce one
   `ResourceBudget` per run. `EQASIM_CPU_BUDGET` / `EQASIM_MEM_BUDGET` let an
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
   `processes` fixes the person-chunk PARTITION -- and then
   `random.randint(10000, size=processes)` -- `processes` also fixes how many
   seeds are drawn. Changing `processes` therefore changes which persons
   share a chunk and which seed each chunk gets, exactly the chainsolver
   defect this ADR otherwise fixes (point 4 below), just unfixed at these two
   sites. `processes` is therefore treated exactly like `matsim_threads` /
   `matsim_qsim_threads`: reported and warned about (the startup report warns
   when a pin sits below 75% of the core budget, i.e. under-uses the
   machine), never adjusted. The two consumers read
   `context.config("processes")` verbatim, unchanged from before this ADR.
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
   bit-for-bit on any machine, FOR A REAL POPULATION (`n_total > 1`). At
   exactly one unique person, `_make_person_shards` caps the shard count at
   `len(unique_persons) = 1` regardless of the configured 62, so that single
   shard is seeded via `_derive_shard_seed(base_seed, 0)` -- the same
   SeedSequence-derived formula every sharded shard uses -- and NOT via the
   plain `base_seed` the genuinely SERIAL path uses (taken only when
   `braunschweig.chainsolvers.shards` is explicitly configured to `1`; see
   the stage's own startup warning for that case). Production never runs at
   `n_total = 1`, so this does not affect the reproduction claim above; it is
   recorded so the claim is not overstated for a tiny fixture or smoke run.
   A run previously executed on a machine with a
   different core count (e.g. a developer laptop) produces a different --
   equally valid -- realisation once under this default; that is the
   intended, one-time correction, stated here rather than absorbed silently.

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
  measured for this ADR (94 GB total -> 86 GB budget, 30 GB/worker ->
  ceiling 2), every overlay that pins `num_workers: 3`
  (`configs/overlays/test.yml`, `test_1pct.yml`, `test_25pct.yml`,
  `test_100pct.yml`, `escort_reuse_5pct.yml`,
  `srv_location_reuse_5pct.yml`) is clamped 3 -> 2 (a ~33% drop), and
  `configs/overlays/smoke_kreis_control_fit.yml`, which pins `num_workers: 4`,
  is clamped 4 -> 2 (a ~50% drop). This is exactly the deviation the startup
  report and the run provenance are built to surface (never silently), but
  it should be read as an operational regression against the pre-ADR
  behaviour, not as a free correctness fix, and is expected in the
  outstanding real-machine smoke evidence (see the Feature Registry's
  `validation.note`).
- **The 30 GB/worker figure is traceable evidence (the 2026-07-10 OOM
  post-mortem); the 8 GB memory reserve subtracted before that bound is
  applied is NOT.** `DEFAULT_MEMORY_RESERVE_GB = 8.0` has no committed
  source and is the number that decides whether the clamp above fires at
  all: at `reserve = 8`, `3 x 30 = 90 > 86` clamps to 2, but at
  `reserve = 4` -- roughly what `configs/overlays/test_100pct.yml`'s own
  "~90 GB on the 128 GB box" budget implies for 3 workers on the machine
  this ADR measures -- `3 x 30 = 90 <= 90` would NOT have clamped. The value
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
- `matsim_threads` / `matsim_qsim_threads` are untouched; an oversubscribed
  run on a shrunk machine is slower, not silently wrong, and the mismatch is
  now visible as a startup warning instead of invisible, pending the tuning
  work tracked under issue #410.
- **Limitation, stated rather than buried.** The claim "secondary locations
  no longer depend on the machine" is verified for the shard partition, the
  per-shard seeds and the recombination order, and the production
  realisation is reproduced bit-for-bit at `shards: 62`
  (`tests/test_chainsolvers_parallel.py`, in particular
  `test_solve_chains_parallel_is_invariant_to_the_worker_count` and
  `test_a_single_worker_still_produces_the_sharded_realisation`). It is NOT
  verified end-to-end against the real solver: those tests inject a
  stateless fake `chainsolvers` module (the optional package is not
  installed in the environment these tests were written and run in) and
  exercise the actual process pool only where the `fork` start method is
  available (Linux/CI; Windows has none). Off the server -- e.g.
  `processes: 8` with `shards: 62` -- several shards now run sequentially
  inside one worker process; if the real `carla_sample` solver holds any
  process-global RNG state or cache outside the context it is explicitly
  passed, results could still depend on the worker count through that back
  door. The discharge this ADR owes and has not yet collected: a server A/B
  at `shards: 62` comparing `processes: 8` against `processes: 62`, asserting
  byte-identical secondary-location output against the real solver.

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
  `tests/test_resources_processes.py`, `tests/test_resources_report.py`,
  `tests/test_resources_run_start.py`, `tests/test_chainsolvers_parallel.py`
- Measured machine state (2026-09-16, `ssh felix`): `systemd-detect-virt` ->
  `kvm`; cgroup v2 `cpu.max` / `memory.max` absent; `nproc` =
  `os.cpu_count()` = `len(sched_getaffinity(0))` = 64; `free -g` total 94,
  available 92, swap 1.
