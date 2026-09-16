# Resource budget and ceiling clamps (`braunschweig/resources.py`)

What the mechanism is, how to extend it safely, and the rule a maintainer must
never break. Why it exists and what was rejected is ADR-0126; its production
state is the Feature Registry record `resource_adaptive_config`.

## What it answers

One question, asked once per run: "how big is the machine this run landed on,
and what may this run use of it?" `detect_machine()` reads the machine
(`os.sched_getaffinity` for cores, `psutil` for memory, each with a logged
fallback and a hard `ResourceDetectionError` if neither source works);
`resolve_budget()` turns that into one `ResourceBudget` (cores, memory_gb)
after subtracting a fixed OS/driver reserve, or takes `EQASIM_CPU_BUDGET` /
`EQASIM_MEM_BUDGET` verbatim when the operator has set them. Every
resource-sensitive config key is resolved against that ONE budget, so two
keys can never disagree about how big the machine is.

## The rule that must never be broken: a resolved value never enters the config

`synpp` derives a stage's cache hash from that stage's DECLARED config
dependencies (see `braunschweig/cache_share.py`). If a resolved,
machine-dependent number were ever written back into the config or
`.merged_config.yml`, every machine would produce a different hash for the
same logical run, and the shared cache -- built once at real scale, reused by
every subsequent run -- would silently stop being shared. This is why
`resolve_*` functions return a `Resolution` (the effective value plus WHY) and
the `effective_*` wrapper functions (`effective_java_memory`,
`effective_popsim_workers`, `effective_processes`) are called at the point of
use inside each consumer, never used to rewrite `context.config(...)`. If you
are about to add a line that writes a detected or clamped value back into a
config dict, stop -- that line is the bug this rule exists to prevent.

## The operational-vs-result-affecting test, before you clamp anything

Not every resource-sensitive key may be clamped. Before adding a new
`resolve_*` function, read every consumer of the key and answer one question:
**does the effective value influence the result, or only the machinery that
produces it?**

- **Operational** (safe to clamp): the key sizes a mechanism whose outcome
  does not depend on the size chosen. `java_memory` only ever becomes an
  `-Xmx` argument. PopulationSim's `num_workers` submits one independent
  subprocess per batch folder with no seed depending on the worker index. The
  two pure `processes` read sites (`synthesis/population/matched.py`,
  `synthesis/population/spatial/secondary/locations.py`) split a workload
  across a pool with no worker-index-dependent seed either.
- **Result-affecting** (must never be clamped silently): the key decides a
  partition, a seed, or anything else a result is derived from. This is not
  hypothetical here -- it is exactly the defect ADR-0126 fixes. Before that
  fix, `braunschweig.chainsolvers.processes` (then undifferentiated from the
  shard count) fixed the person partition that
  `_derive_shard_seed(base_seed, shard_index)` seeds from, so the worker
  count silently decided the secondary-location realisation. The fix keeps
  the SHARD count (`braunschweig.chainsolvers.shards`) a separate, hashed,
  never-clamped config key, and only lets the WORKER count
  (`braunschweig.chainsolvers.processes`) be operational and volatile.

`matsim_threads` / `matsim_qsim_threads` sit in a third bucket: their effect
on results is genuinely unverified (issue #410), so neither answer above is
available yet. They are treated as result-affecting BY DEFAULT until proven
otherwise -- never clamped, only warned about when they exceed the budget.
When in doubt about a new key, do the same: default to "do not touch it",
not "it is probably fine".

## Reading a `Resolution`

Every `resolve_*` function returns a `Resolution(key, configured, effective,
origin, note)`. `origin` is one of:

- `"pinned"` -- the configured value fit the budget and is used verbatim
  (memory sizes are echoed byte-for-byte, never reformatted, so a
  sub-gigabyte pin like `"1500M"` is not silently rounded).
- `"clamped"` -- the configured value did not fit and was reduced; always
  logged at `WARNING` by the `effective_*` wrapper and carried into the run
  provenance.
- `"derived"` -- the key was left at its `auto` sentinel (`0`, `""`, or
  `"auto"`, see `is_auto()`) and the budget supplied the value outright.

`ResourceReport` (built once per run by `build_report()` in
`scripts/run_synpp.py`, from the RESOLVED config, so it cannot drift from
what the run actually does) collects every key's `Resolution` plus a list of
`Violation`s (`"warning"` logs and is recorded; `"error"` raises
`ResourceValidationError` and aborts the run before any stage executes). Its
`as_dict()` is what lands in `run_provenance_<stamp>.json` under
`"resources"`.

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
   a warning (everything else, including underuse).
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

## Known limitation (do not silently "fix" this without new evidence)

The chainsolver shard/worker split's machine-independence claim is verified
for the partition, the per-shard seeds and the recombination order using a
stateless fake `chainsolvers` module (the optional package is not installed
in the environment these tests were written in), and only through the real
process pool where the `fork` start method exists (Linux/CI, not Windows).
It has not been verified end-to-end against the real `carla_sample` solver.
See ADR-0126's Consequences section for the exact discharge this owes (a
server A/B at `shards: 62` comparing `processes: 8` against `processes: 62`)
before treating that claim as fully closed.
