# ADR-0095 · 2026-08-20 · CPU-accumulation hang watchdog for the pipeline's Java subprocesses

- **Status:** active
- **Context:** The 100 % run of 2026-08-20
  (`docs/runs/100pct-allfeat-i240-2026-08-20.yml`) finished MATSim's controler at
  16:18:55 -- every output including all five SimWrapper dashboards on disk -- and
  then kept the JVM alive at ~0 % CPU until it was killed manually at 17:25.
  `jstack` proved the cause: SimWrapper dashboard generation reads CSV through
  tablesaw, whose univocity-parsers backend starts a NON-DAEMON
  `"unVocity-parsers input reading thread"` and never marks it as a daemon; left
  unclosed it parks in `FixedInstancePool.allocate` and `DestroyJavaVM` waits for
  it as long as the process lives. Verified independently at the bytecode level:
  `javap` on univocity-parsers 2.8.4 and 2.9.1 shows
  `new Thread(runnable, "unVocity-parsers input reading thread")` followed by
  `start()` with no `setDaemon(true)` in between.
  The upstream defect was subsequently pinned down from the sources and
  **reproduced minimally** (2026-08-21): `tech.tablesaw.io.csv.CsvReader.read`
  skips `parser.stopParsing()` whenever the caller supplied a `Reader`
  (`if (options.source().reader() == null)`, commented "let the client close it")
  -- but the client never sees the parser, and on the partial-column-types path the
  reader actually being parsed is tablesaw's own one over its `bytesCache`, not the
  client's. `ConcurrentCharLoader.stopReading()` is the only thing that unparks the
  loader (it interrupts the thread), while `FixedInstancePool.allocate()` loops
  `wait(50)` for as long as the bucket pool is full, so an abandoned loader parks
  forever. A/B on identical input with an identical mid-file parse abort, the only
  difference being the source kind: `File` source -> JVM exits (code 0); `Reader`
  source -> JVM never exits, `unVocity-parsers input reading thread` alive in
  TIMED_WAITING. Closing the client's reader in try-with-resources does NOT help
  (the thread waits on the bucket pool, not on the reader), so no fix is available
  at the MATSim call site -- which is precisely why the pipeline needs a guard of
  its own. Note the leak requires the parse to end while input is still unread: a
  read that reaches EOF terminates the loader by itself.
  The Java side now terminates explicitly (`System.exit` in
  `org.eqasim.braunschweig.RunSimulation`, TUBS-IVS/eqasim-java-bs#23, merged as
  `03c1d680`), but that guard covers only our own entry point and only this one
  leak. The pipeline itself had no limit whatsoever: `matsim.runtime.java.run`
  waited on `subprocess.check_call`, so `matsim.simulation.run` never returned,
  synpp stalled, and -- because the stage was never cached -- a naive re-run would
  have repeated 2.5 h of simulation and hung at exactly the same point. Issue #330,
  follow-up 2.
- **Decision:** Guard every Java subprocess the pipeline spawns with a watchdog
  that judges the process by **CPU-time accumulation**, not by wall clock
  (`matsim/runtime/process_watchdog.py`, wired into `matsim.runtime.java.run`).
  A process that stays alive but gains less than `java_hang_min_cpu_seconds`
  (default 1.0) of CPU time across a full `java_hang_timeout_s` window (default
  900 s, i.e. the guard is ON by default per the project feature-flag policy) is
  declared hung, sent SIGTERM, escalated to SIGKILL after a grace period, and the
  resulting non-zero return code raises at the call site. `java_hang_timeout_s: 0`
  disables the guard.
  - **Why CPU time and not a wall-clock timeout.** A legitimate 100 % MATSim run
    occupies the JVM for hours, so a wall-clock limit must either be so generous
    that it detects a hang only after most of a working day, or it risks killing a
    healthy multi-hour run and destroying real work. A hung JVM is trivially
    distinguishable: it accumulates no CPU time at all. This is the same signal
    that actually diagnosed the incident (the machine sat at load 0.02 while the
    process stayed alive) and the lesson recorded from it -- judge a silent phase
    by CPU accumulation, not by log recency. The discrimination is wide, not
    marginal: even a single-threaded, I/O-bound writer accumulates far more than
    one CPU second per 15-minute window, while a parked thread accumulates
    essentially nothing.
  - **Rejected: warn-only.** A log line alone does not stop the stall; nobody
    reads a warning at 03:00, and the failure mode this addresses is precisely a
    run that silently occupies the server for hours. The point of the guard is to
    convert a silent hang into a `CalledProcessError`, which synpp reports.
  - **Rejected: an observer thread wired only into `matsim.simulation.run`.**
    That would have kept the cache blast radius to the stages a re-run needs
    anyway (see consequences), but it must locate the java child process
    indirectly instead of owning it, and it would leave every other Java call
    (`matsim.simulation.prepare` alone takes ~1 h at 100 %) unguarded. The guard
    belongs where the subprocess is spawned.
  - **Rejected: parsing `/proc/<pid>/stat` to avoid a new dependency.** It works
    on the run server but leaves every non-Linux developer machine unguarded and
    adds platform branching. `psutil` (7.2.2, already present in both the local
    and the server `eqasim` env) is now declared in `environment.yml` instead.
  - **No silent fallback.** When the CPU counter cannot be read at all (psutil
    missing, access denied, process already gone) the watchdog does NOT fall back
    to a wall-clock kill: it degrades to inert and logs that the call is
    unprotected -- once, not per sample. An unguarded run is then visible in the
    log rather than silently pretending to be guarded.
  - **Config declared volatile.** `java_hang_timeout_s` and
    `java_hang_min_cpu_seconds` are declared with `volatile = True`, so they stay
    out of the synpp stage hash: an operational guard has no influence on any
    result, and raising the timeout must not force a 2.5 h re-run. The price is
    that synpp does not propagate volatile options to downstream stages, so every
    stage whose `execute()` reaches `java.run` must declare them itself. That is
    the #229 crash class (`"Config option ... is not requested"`), which bit this
    very run in `matsim.scenario.supply.*`; it is guarded by a discovery-based
    test that scans for `eqasim.run(` / `pt2matsim.run(` callers rather than by a
    hand-maintained list, so a new caller is covered on arrival.
- **Consequences:**
  - **Cache invalidation, measured not guessed.** Editing the
    `matsim.runtime.java` stage module devalidates it and, through synpp's
    parent-update rule, its 22 transitive dependents. Durations from the
    2026-08-20 run: `simulation.prepare` 1 h 02, `scenario.population` 10 min,
    `supply.osm` 3:41, `supply.processed` 3:47, `scenario.vehicles` 2:49,
    `incommuters` 2:38, everything else under a minute -- roughly **1.5 h of extra
    work, once**, on top of the 2.5 h MATSim stage that the pending verification
    re-run needs anyway. The 7.5 h `secondary_chainsolvers` stage and the whole
    population-synthesis chain are NOT downstream of `matsim.runtime.java` and
    stay cached.
  - Behaviour with the guard disabled is identical to the previous
    `check_call`: wait for the process, raise on a non-zero return code.
  - A process killed by the watchdog may have written complete outputs already
    (that is exactly what happened on 2026-08-20), so the failure message points
    at the stage output directory and asks for a `jstack` capture before a retry.
    The guard makes the failure loud; it does not decide whether the partial work
    is usable.
  - The upstream leak is untouched. Reporting it remains issue #330 follow-up 1,
    and the proof above names the two places a real fix can live: tablesaw
    (`stopParsing()` must be unconditional; only `reader.close()` belongs behind
    the ownership check) and univocity (mark the loader thread as a daemon). The
    MATSim analysis commands cannot fix it at the call site while they need the
    `Reader` overload to read `.csv.gz`.
- **Verification owed:** the watchdog's arming and its inert path are covered by
  unit tests (`tests/test_java_hang_watchdog.py`, deterministic clock and fake
  process); a real hang has NOT been re-provoked end to end. The pending 100 %
  re-run is the first execution that exercises the armed watchdog against a live
  JVM.
