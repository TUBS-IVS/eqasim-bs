# ADR-0196 · 2026-11-16 · Timetable search loops prefetch their router tasks in one batch per loop

- **Status:** accepted (owner, 2026-11-16: approval of items F, G and H of the repository audit
  package H "performance, measure first", with two decisions: the router work files leave the stage
  output hash, and the strict "no route at all" check applies to the prefetched superset).
- **Numbering:** ADR-0196 is the next free id (ADR-0195 is taken by PR #624); checked on 2026-11-16 across all local and remote
  branches and the open pull requests.
- **Issue:** none; repository audit follow-up 2026-11, package H, items F (in-commuters), G (outside
  tours) and H (portal pt stays).

## Context

- `braunschweig.data.transit.connections.route_batch` starts the Java tool
  `RunBatchPublicTransportRouter` once per call. Every start loads network, schedule, vehicles and
  the eqasim injector before it routes. ASSUMPTION: this fixed load dominates the calls of the
  search loops, so fewer, larger calls are faster. It rests on the stage timing of production run
  prod100_20261031 taken for audit package H, which is not yet recorded in a run manifest; no
  wall time was measured for this change.
- Four production stages search for a timetable connection in sequential loops and called the
  router once per round: `incommuter_pt_connections.resolve_pt_days` (employee and student
  in-commuters; the employees run it once more per pt re-balancing round) once at the planned
  times, once per shift offset and once for the station re-draw;
  `outside_tour_modes.pt_connections.resolve_pt_tours` once at the planned times and once per
  offset; the portal stage once per direction for the planned gate choice
  (`gates.choose_pt_gates`) and once per direction and grid step in the two grid walks of
  `no_connection.resolve_no_connection`. The number of rounds per loop follows from the shift
  window and step config keys of each stage (`incommuter_pt_connections.shift_offsets_s`).
- None of these loops draws random numbers between rounds, and the Java worker
  (`BatchPublicTransportRouter.Worker.run`) routes each task from its origin, destination and
  departure time alone (no person, no random numbers; the identifier is only copied). Routing
  real production tasks once as one batch and once split into other batches gave identical trips
  and legs rows, stop ids included (run manifest
  [`pt-router-batch-invariance-2026-11-16`](../runs/pt-router-batch-invariance-2026-11-16.yml)).
- `route_batch` leaves the tasks and results of its LAST call (`router_tasks.csv`,
  `router_trips.csv`, `router_legs.csv`) in the stage's cache directory, and the Java tool writes
  the result rows per worker thread, so their row order depends on thread timing. The stage
  output hash (`braunschweig/synpp_fork/output_hash.py`) covered them.

## Decision

1. **Prefetch rule.** Before an offset or grid loop, the loop hands the SUPERSET of the router
   tasks it can ask to `braunschweig.data.transit.router_cache.PrefetchRouter.prefetched`, which
   routes them in one call; the loop then runs unchanged and each round is answered from that
   batch. The cache key is the task's five values (origin, destination, departure time) as exact
   floats, so a round reads exactly the answer the router gave for the task it asks. The superset
   is every still unresolved item at every offset (step, variant) that the item's OWN times allow;
   bounds that depend on other items' current times (an earlier shift moves a neighbour) are left
   to the loop. A task the loop asks outside the superset is routed as before, counted and logged
   at WARNING as a fallback. The planned-time call and the station re-draw keep their own call;
   the portal stage's `choose_pt_gates` is split into the candidate plan (`_gate_plan`,
   `pt_gate_router_tasks`) and the router decision so the portal superset can be listed without
   routing.
2. **Strict check on the superset** (owner decision). `route_batch` treats a call in which no task
   gets a route as a broken run. With the prefetch this check applies to the prefetched superset
   (and to every call that still reaches the router as a whole request), not to the narrowed rounds
   answered from it: a narrowed round with no route at all is then a normal outcome, as the outside
   tours already handled it (PR #505). This holds for the in-commuters and, by the same rule, for
   the portal stage's grid walks; the outside-tour superset is routed with the tolerance its
   narrowed rounds already had.
3. **Router work files leave the output hash** (owner decision). The four router stages
   (`synthesis.incommuters`, `synthesis.student_incommuters`, `synthesis.outside_tour_modes.stage`,
   `synthesis.portal_trips.stage`) declare `_OUTPUT_HASH_EXCLUDE` for `<work dir>/router_*.csv`.

## Rationale

- Python only: the alternative, one long-lived router process per stage, saves more starts but
  needs a new process protocol, a fork change of eqasim-java-bs and a rebuilt jar.
- Prefetch instead of rewriting the loops: the sequential decision code (earlier offsets first,
  current neighbour times) stays exactly as reviewed; only the router answers move earlier.
- A content key instead of the loops' identifiers: the loops reuse identifiers across offsets
  (`<person>:<i>:out` at every offset), so only the task values identify an answer.

## Consequences

- The decisions of the loops (actions, offsets, stations, gates, trips) are byte-identical to the
  per-round routing whenever the router's answer depends on the task only; the prefetch tests in
  `tests/test_pt_router_prefetch.py` compare both ways on a deterministic fake router and fail if a
  read answer was never routed. Expected router starts per loop drop from one per round to two
  (planned times + superset) or three (+ station re-draw); the portal stage to four.
- Larger batches: the superset holds tasks a round-by-round search would have skipped once an item
  was resolved (up to every unresolved item at every offset); they cost routing time but no start.
  Whether the saved starts outweigh that is the ASSUMPTION above; the next production run's
  `[pt-router cache]` lines and stage durations measure it.
- Exception to byte-identity: a run in which a narrowed round found no route at all used to abort
  with "the run is broken" and now continues (decision 2). Conversely, the strictly routed superset
  of the in-commuters and the portal walks also holds tasks a walk would filter out against its
  neighbours' current times: a run in which no superset task has a route and the walk would have
  asked none of them now stops as broken, where it ran on before (not seen; it needs a router
  without any route for every shifted task of a stage).
- The portal stage's guard that no routed leg uses a stop outside the cut timetable still checks
  the legs each round reads; the prefetched batch is not checked as a whole.
- A fallback (a task outside the superset) changes no answer, it only costs a router start: it is
  logged with its count and rate, but not added to the stage reports and has no threshold.
- The `pt_rebalance` report of the in-commuters keeps counting probe calls per round
  (`router_probe_calls`, ADR-0172); with the prefetch they are no longer router starts. The router
  starts are logged by `PrefetchRouter` (`[pt-router cache]` lines, a running stage total).
- The router work files hold the last call of a stage, now usually a prefetch batch; they remain
  debug aids, not outputs.

## Evidence

- Premise (the router's answer does not depend on the batch): run manifest
  [`pt-router-batch-invariance-2026-11-16`](../runs/pt-router-batch-invariance-2026-11-16.yml).
- Byte-identical decisions and the router starts per loop: `tests/test_pt_router_prefetch.py`
  (one test per item F, G, H) and `tests/test_pt_router_cache.py` (cache, fallback, strict check).
- The re-executed stages (helper hash, `scripts/audit_synpp_helper_hash.py`): the four router
  stages of Decision 3 re-execute for their changed code. Their output digest also changes once,
  because Decision 3 drops the work files from it, so the first run after this change also
  re-executes their whole downstream DAG (`parent_output_changed`); the early cutoff applies
  again from the run after.
