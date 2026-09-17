# Resource budget and ceiling clamps (`braunschweig/resources.py`)

What the mechanism is, how to extend it safely, and the rule a maintainer must
never break. Why it exists and what was rejected is ADR-0126; its production
state is the Feature Registry record `resource_adaptive_config`.

## What it answers

One question, asked once per run: "how big is the machine this run landed on,
and what may this run use of it?" `resolve_budget()` detects cores and memory
directly via `detect_cores()` (`os.sched_getaffinity`, falling back to
`os.cpu_count()`) and `detect_memory_gb()` (`psutil`, falling back to
`/proc/meminfo`) -- each with a logged fallback and a hard
`ResourceDetectionError` if neither source works -- and detects only the
quantity not already fixed by an explicit `EQASIM_CPU_BUDGET` /
`EQASIM_MEM_BUDGET` override. The result becomes one `ResourceBudget` (cores,
memory_gb) after subtracting a fixed OS/driver reserve, or the override is
taken verbatim. `detect_machine()` still exists and bundles the same two
detections into one `MachineResources` call, but `resolve_budget()` does not
call it any more (the env-override fix needed to detect only the
non-overridden quantity); as of this writing `detect_machine()` has no
production caller left at all -- only its own tests
(`tests/test_resources_detection.py`) exercise it directly.

Every key THIS MODULE resolves (`java_memory`,
`braunschweig.population.popsim.num_workers`, the reporting-only `processes`,
and -- since the 2026-09-17 amendment below -- `braunschweig.chainsolvers.processes`)
is resolved against that one budget, so those keys cannot disagree with each
other about how big the machine is. This was NOT always true: before the
amendment, `braunschweig.chainsolvers.processes` -- the chainsolver pool, the
largest process fan-out in the pipeline -- was resolved entirely outside this
module, through the now-deleted `parallelism.resolve_workers()` ->
`parallelism.available_cores()` -> `os.cpu_count()`, which ignored CPU
affinity and `EQASIM_CPU_BUDGET` entirely, so it COULD disagree with the rest
of the mechanism about how big the machine is on an affinity-restricted box.
That divergence is closed: the chainsolver pool now resolves through this
module's own `resolve_budget()`, exactly like every other key.

### The chainsolver worker pool is now memory-bounded too (2026-09-17 amendment)

`braunschweig.chainsolvers.processes` was, until 2026-09-17, the one
resource-sensitive key this module deliberately left CORE-bound only ("no
per-worker footprint has ever been measured for that stage, and inventing one
is forbidden", per the original design spec's Non-goals). That is no longer
the case: the run resource recorder (issue #350) recorded seven deduplicated
100% ZGB-8 executions of the chainsolver stage on the felix server
(2026-09-05 to 2026-09-09), and a post-hoc measurement over those recorder
series (`docs/runs/chainsolver-worker-private-memory-2026-09-17.yml`)
established the pool's MAXIMUM measured private per-worker memory footprint
at 0.86 GB (`DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB`), computed as
(available-memory drop during the stage, minus driver RSS growth, which was
0.0 GB in every run) divided by the 62 live workers.

**Per-worker RSS itself is NOT usable for this bound.** On the 125.78 GB
machine the summed worker RSS reads 1200-1860 GB across these runs, because
more than 95% of each forked worker's RSS is copy-on-write pages inherited
from the driver process at fork time, not memory the pool actually needs.
The available-memory DELTA is the only quantity that isolates the pool's real
private footprint, which is why the rule below subtracts a live measurement
rather than summing `ps`/`/proc` RSS across the pool.

`resolve_chainsolver_workers` / `effective_chainsolver_workers` bound the
pool by:

    workers = max(1, min(core_budget, floor((memory_budget_gb - driver_rss_gb_live) / worker_memory_gb)))

`driver_rss_gb_live` is THIS process's OWN live RSS at stage start
(`current_process_rss_gb`: psutil first, then `/proc/self/status VmRSS`,
logged at INFO which source was used; a WARNING is logged and the bound
falls back to core-only, `driver_rss_gb` treated as 0.0, when neither source
is available -- no silent fallback), measured fresh every run rather than
injected from config, because the worker pool is FORKED from the driver and
both compete for the same memory budget -- this is the run's OWN
deterministic state, not other users' contention, so subtracting it is
consistent with this module's "scale off the total allocation, not free
capacity" rule (ADR-0126), not an exception to it. The new config key
`braunschweig.chainsolvers.worker_memory_gb` (default 0.86, OPERATIONAL,
`volatile=True`) carries the measured constant; it sizes the pool, never the
partition, so it has no influence on any result.

**Where the bound bites, and what is NOT known.** On the current 94.28 GB
server (86.28 GB budget, 0.86 GB/worker) the memory term drops the pool below
the 62-worker core budget from a driver RSS of roughly **32.96 GB** upwards
(`86.28 - 62 x 0.86`). Whether any real run sits above that threshold is
**UNVERIFIED**: the code reads the driver's RSS at the FORK POINT inside
`_solve_problem_set`, and the run resource recorder never sampled that instant
-- it captured a before-stage baseline of 19-36 GB and a during-stage minimum
of 19.6-30.1 GB, and the ~33 GB threshold lies inside the first range. So do
not read "typical runs are unaffected" as established; read it as "at the low
end of what was recorded, the bound does not bind". The server smoke logs the
fork-point value and the ceiling it produced, and is the outstanding evidence.
See ADR-0126's point 5 amendment for
the full decision and its Consequences for the permanent cache-dependency
cost this adds to the chainsolver stage (`resources.py` is now a helper
module of BOTH the PopulationSim stage and the chainsolver stage, so any
future edit to this file, wording-only or not, invalidates both stages'
full-scale caches).

## The rule that must never be broken: a resolved value never enters the config

`synpp` derives a stage's cache hash from that stage's DECLARED config
dependencies (see `braunschweig/cache_share.py`). If a resolved,
machine-dependent number were ever written back into the config or
`.merged_config.yml`, every machine would produce a different hash for the
same logical run, and the shared cache -- built once at real scale, reused by
every subsequent run -- would silently stop being shared. This is why
`resolve_*` functions return a `Resolution` (the effective value plus WHY) and
the `effective_*` wrapper functions (`effective_java_memory`,
`effective_popsim_workers`, and -- since the 2026-09-17 amendment --
`effective_chainsolver_workers`: the three OPERATIONAL keys; there is no
`effective_processes`, see the worked example below) are called at the point
of use inside each consumer, never used to rewrite `context.config(...)`. If
you are about to add a line that writes a detected or clamped value back into
a config dict, stop -- that line is the bug this rule exists to prevent.

## The operational-vs-result-affecting test, before you clamp anything

Not every resource-sensitive key may be clamped. Before adding a new
`resolve_*` function, read every consumer of the key and answer one question:
**does the effective value influence the result, or only the machinery that
produces it?**

- **Operational** (safe to clamp): the key sizes a mechanism whose outcome
  does not depend on the size chosen. `java_memory` only ever becomes an
  `-Xmx` argument. PopulationSim's `num_workers` submits one independent
  subprocess per batch folder with no seed depending on the worker index.
  `braunschweig.chainsolvers.processes` joined this list on 2026-09-17: since
  ADR-0126's shard/worker split, the worker count only decides how many
  processes chew through the fixed, separately-hashed shard set
  (`braunschweig.chainsolvers.shards`), so sizing the pool by measured memory
  cannot change the secondary-location realisation either. That is the WHOLE
  list today -- three keys.
- **Result-affecting** (must never be clamped silently): the key decides a
  partition, a seed, or anything else a result is derived from. This is not
  hypothetical here -- it is exactly the defect ADR-0126 fixes, and the
  worked example is not the chainsolver alone. Before the fix,
  `braunschweig.chainsolvers.processes` (then undifferentiated from the
  shard count) fixed the person partition that
  `_derive_shard_seed(base_seed, shard_index)` seeds from, so the worker
  count silently decided the secondary-location realisation. The fix keeps
  the SHARD count (`braunschweig.chainsolvers.shards`) a separate, hashed,
  never-clamped config key, and only lets the WORKER count
  (`braunschweig.chainsolvers.processes`) be operational and volatile.
  **The two pure `processes` read sites belong in THIS bucket, not the
  operational one above.** `synthesis/population/matched.py`
  (`parallel_statistical_matching`) and
  `synthesis/population/spatial/secondary/locations.py` (`execute`) both do
  `np.array_split(<ids or frame>, processes)` -- `processes` fixes the
  person-chunk PARTITION -- immediately followed by one seed per chunk:
  `random.randint(10000, size = len(chunks))` in `matched.py` and
  `random.randint(10000, size = processes)` in `locations.py`. The two
  spellings are the same quantity (`np.array_split(..., processes)` always
  returns exactly `processes` chunks), so at both sites `processes` also fixes
  how many seeds are drawn, and which chunk gets which seed. Both are structurally
  identical to the chainsolver defect one paragraph up: a `processes` pin of
  4 on one machine and 8 on another produces a different partition and a
  different seed draw for the SAME configured `random_seed`, on a stage
  hash that never changes because `processes` is `volatile=True`. **An
  earlier draft of this branch classified these two sites as operational and
  clamped them; that was wrong, and the clamp was reverted (ADR-0126,
  Decision 3) before merge.** If you are looking for the canonical example
  of "read every consumer before you clamp", this is it -- the mistake was
  made once, on this very key, and caught only by re-reading the consumers
  the rule tells you to read.

`matsim_threads` / `matsim_qsim_threads` sit in a third bucket: their effect
on results is genuinely unverified (issue #410), so neither answer above is
available yet. They are treated as result-affecting BY DEFAULT until proven
otherwise -- never clamped, only warned about when they exceed the budget.
`processes` is now handled the same way, for the reason stated above: it is
NOT unverified (the two consumers are read and understood), it is
POSITIVELY KNOWN to be result-affecting, and it is warned about (below 75%
of the core budget) rather than clamped, using the same reporting mechanism.
When in doubt about a new key, do the same: default to "do not touch it",
not "it is probably fine".

## Reading a `Resolution`

Every `resolve_*` function returns a `Resolution(key, configured, effective,
origin, note)`. `origin` is one of:

- `"pinned"` -- the configured value fit the budget and is used verbatim
  (memory sizes are echoed byte-for-byte, never reformatted, so a
  sub-gigabyte pin like `"1500M"` is not silently rounded).
- `"clamped"` -- an OPERATIONAL key's configured value did not fit and was
  reduced. There are THREE such keys: `java_memory`,
  `braunschweig.population.popsim.num_workers` and -- since the 2026-09-17
  amendment -- `braunschweig.chainsolvers.processes`. Always logged at
  `WARNING` by the `effective_*` wrapper and carried into the run provenance.
- `"derived"` -- the key was left at its `auto` sentinel (`0`, `""`, or
  `"auto"`, see `is_auto()`) and the budget supplied the value outright.
- `"reported"` -- the value is stated but NOT applied by the resolver that
  produced it. **Two different situations carry this origin; do not read them
  as the same thing.**
  - `processes` (`resolve_processes`) is REPORTING-ONLY forever: its pin
    exceeds the budget and is NEVER reduced, because the key is
    result-affecting. `effective` equals `configured` verbatim and `note`
    states what the budget would have allowed.
  - `braunschweig.chainsolvers.processes` carries `"reported"` **only in
    `build_report`'s startup preview**, because its real ceiling also needs
    the live driver RSS, known only once the stage forks its pool. That key IS
    genuinely clamped at its point of use: `effective_chainsolver_workers`
    emits its own `"pinned"` / `"clamped"` / `"derived"` resolution there, and
    that one is the value the run used. A `"reported"` entry for this key means
    "not yet resolved", not "never adjusted"; the preview is an UPPER bound
    (`min(configured, core budget)`), never a claim about the final count.

  Never confuse either with `"clamped"` -- the final review of this branch
  found `ResourceReport` claiming `processes = 14 [clamped]` while the run
  actually used the pinned 32, which is exactly the false claim this origin
  value exists to prevent. The same review found the chainsolver preview
  reporting the core budget regardless of a configured pin, which is the
  mirror-image error (a true-looking number the run would not use).

`ResourceReport` (built once per run by `build_report()` in
`scripts/run_synpp.py`, from the RESOLVED config, so it cannot drift from
what the run actually does) collects every key's `Resolution` plus a list of
`Violation`s (`"warning"` logs and is recorded; `"error"` raises
`ResourceValidationError` and aborts the run before any stage executes). Its
`as_dict()` is what lands in `run_provenance_<stamp>.json` under
`"resources"`.

**There is exactly ONE failure channel.** A key whose configured value is
unusable at all -- negative, non-integral, unparseable -- becomes an
error-severity `Violation` too, not an exception: `build_report` catches the
resolver's `ValueError` and records it against the key at fault. Before that,
such a defect raised past `build_report` and past `enforce_report` and reached
the operator as a bare traceback from `scripts/run_synpp.py`, while a
machine-fit mismatch got a formatted message -- twelve distinct defects took
that second channel and none reached the gate. Resolution continues past a bad
key, so every unusable key is reported in one run rather than one per
correction. The single exception is `ResourceDetectionError`: with no detected
machine there is no budget and no report to build at all, so it still raises.
`EQASIM_CPU_BUDGET` / `EQASIM_MEM_BUDGET` likewise raise, because they are read
before any budget exists -- but with a message naming the variable, the value
and the accepted spelling.

## Adding a new resource key

1. Read every consumer of the key and classify it with the test above.
   Operational only, or the answer is "do not clamp it" (yet).
2. Add one named `resolve_<key>(configured, budget, ...)` function next to
   the existing ones, built on the shared `_resolve_ceiling` helper if the
   key is a plain count against a single ceiling (cores or a
   memory-derived worker count); write a dedicated resolver (like
   `resolve_java_memory`) if the unit or ceiling quantity differs.
3. Add an `effective_<key>(configured, machine=None, env=None)` wrapper that
   resolves the budget, calls the resolver, logs at `WARNING` when
   `origin != "pinned"`, and returns the effective value. Call THIS at the
   consumer's point of use -- never rewrite `context.config(...)`.
4. Add the key's resolution to `build_report()` so the startup log and the
   run provenance see it, and add a `Violation` there if an impossible
   configuration for this key deserves an abort (memory-bound cases) or only
   a warning (everything else, including underuse). **An abort may only fire
   where the figure behind it was measured.** `DEFAULT_POPSIM_WORKER_MEMORY_GB`
   is a 100%-scale measurement, and applying it at error severity to every
   sampling rate aborted both committed 1% popsim fixtures on a 32 GB machine --
   runs that worked before the gate existed. Gate an abort on the conditions
   that make the figure apply (here: the popsim method AND
   `sampling_rate >= 1.0`, or an explicit
   `braunschweig.population.popsim.worker_memory_gb` by which the operator
   asserts it), warn otherwise, and say in the message WHY it is only a warning.
   Never bridge the gap with an invented scale-to-memory relationship.
   Your resolver may raise `ValueError` for a value it cannot use at all;
   `build_report` converts that into an error-severity `Violation` against your
   key, so the message must name the key and the accepted spellings -- it is
   what the operator sees in the gate.
5. Add table-driven tests mirroring `tests/test_resources_budget.py`: a pin
   that fits is passed through verbatim, a pin that does not fit is clamped
   AND warns, and the auto sentinel is derived from the budget. Inject every
   machine-dependent reader; no test may read the real `cpu_count` or
   `/proc/meminfo` (see the reader-injection pattern in
   `tests/test_resources_detection.py`), so the suite stays deterministic on
   any machine, Windows included.
6. If the key is result-affecting, do NOT write a `resolve_*`/`effective_*`
   pair at all -- add it as a plain hashed config key instead (as
   `braunschweig.chainsolvers.shards` is), and let the startup report only
   WARN when it looks unsafe, exactly like `matsim_threads` /
   `matsim_qsim_threads`.

## Known limitations (do not silently "fix" these without new evidence)

**1. The shard/worker split against the real solver.** The
machine-independence claim is verified for the partition, the per-shard seeds
and the recombination order using a stateless fake `chainsolvers` module (the
optional package is not installed in the environment these tests were written
in), and only through the real process pool where the `fork` start method
exists (Linux/CI, not Windows). It has not been verified end-to-end against
the real `carla_sample` solver. The separate "reproduces the production
realisation bit-for-bit at `shards: 62`" claim is an INFERENCE from reading
the pre-change code (ADR-0126, Decision 4), not a test result -- no test
exercises 62 shards against a production artifact. See ADR-0126's Consequences
for the exact discharge this owes (a server A/B at `shards: 62` comparing
`processes: 8` against `processes: 62`), and for the code inspection of the
installed `chainsolvers` package that narrows, but does not close, the risk.

**2. The `processes` under-use warning advises changing a key whose caches do
not track it.** `build_report` warns when `processes` sits below 75% of the
core budget, but both read sites declare it `volatile = True`, so raising it
changes the statistical-matching and secondary-location realisations WITHOUT
invalidating those stages' caches. Changing the declarations would invalidate
two more full-scale caches and is out of scope; the warning text therefore
states the whole caveat instead. Do not "simplify" that text back to "raise the
pin if that is not intended".
