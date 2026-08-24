# ADR-0100 · 2026-08-24 · Every pipeline run records its own resource time series, and one shared definition decides whether a process tree is working

- **Status:** active
- **Context:** Both incidents of the 2026-08-23/24 100 % run were *measurement*
  failures, not pipeline failures (issue #350):
  1. **A healthy stage was killed as deadlocked.** `secondary_chainsolvers` had
     `shard 61/62 done` as its last log line, and a hand check with
     `ps -C python --sort=-rss | head -6` showed frozen CPU times. With ~62 forked
     workers that sample showed the six *biggest-RSS* processes, which were
     finished-by-design idle workers, while the computing ones sat outside the
     window. The stage had in fact completed (`shard 62/62 done`, cache written
     07:40:41) three minutes before the kill. The cost was small only because synpp
     had already written the cache.
  2. **The datum needed to tune `num_workers` was lost with the processes.** The
     open question of issue #281 is the PEAK memory of one PopulationSim worker;
     folklore says 25-30 GB and the only hard number is a 29.9 GB OOM-kill log
     line. `/proc/<pid>/status VmHWM` answers it exactly -- but only while the
     process lives, and by the time the question was asked the batching phase was
     over and every worker was gone.
  Both are the same gap: long server runs were judged and tuned from snapshots
  taken by hand, after the fact, with nothing retained. What already existed did not
  close it: `scripts/monitor_run_health.sh` is a one-shot snapshot with no history,
  `braunschweig.analysis.runtime` derives per-stage WALL clock from the log but
  carries no resource dimension, `braunschweig/provenance.py` records the run's
  inputs once at launch, and the Java hang watchdog (ADR-0095) measured a SINGLE
  process, so a JVM that forks helpers looked idle while the helpers computed.
- **Decision:** A resource recorder runs with EVERY pipeline invocation and writes a
  retained time series into the run's own directory; the whole-tree CPU number it
  uses becomes the single definition of "is this tree doing work" in the repository.
  - **Recording is wired into the launcher, not into a stage.** `scripts/run_synpp.py`
    wraps the run in `braunschweig.monitoring.recorder.record_from_config`, so the
    series spans every stage, every forked worker and the cache export, and nobody
    has to remember to start a monitor. Default ON per the project feature-flag
    policy; `monitoring_enabled: false` makes it a no-op that leaves no trace.
  - **The series is the artifact, and it survives the run.** One JSONL row per
    sample, flushed per sample, in
    `<working_directory>/monitoring/resource_series_<timestamp>.jsonl`, plus a JSON
    and a markdown summary written from a `finally`. A killed or failed run is
    precisely the run whose resource record is wanted, so it keeps everything up to
    its last sample.
  - **Whole-tree CPU, and threads counted rather than walked.** CPU is summed over
    every process in the tree -- a number no busy worker can fall outside of --
    while `num_threads` is read from each process' own `stat` line. Adding
    `/proc/<pid>/task/*` CPU times to the process' own would double-count the same
    work; the thread total is reported separately instead, because thread
    oversubscription is a documented failure class here (~4000 threads, libc
    segfaults, 12 lost PopulationSim batches).
  - **The Java hang watchdog consumes the same reader** rather than carrying its own
    copy, so the two cannot drift apart. For a childless process the tree total
    equals the process' own, so no existing verdict changes; the pre-existing
    watchdog tests stay green unchanged, and a test pins that a busy child of an
    idle parent now reads as working where the old per-process reader called it
    idle. The import is done inside the function, keeping the package dependency
    direction (`braunschweig` imports `matsim`, never the reverse) intact.
  - **CPU is accounted per pid, because the tree total is NOT monotonic.** A worker
    that exits takes its accumulated CPU time out of the sum, so summing raw
    differences would report negative work and a drop would look like a stall. Where
    the series carries per-process rows each pid contributes what it gained while
    observed; otherwise only the positive tree increments are summed, and the
    `cpu_accounting` field states which of the two produced the number.
  - **"Unmeasurable" is a third verdict, never folded into "not working".** A step
    with no CPU signal at all is reported as unmeasurable and is not a stall, an
    unreadable tree yields `cpu_seconds: null` rather than 0, and every sample names
    its own source (`proc` / `psutil` / `unavailable:<reason>`). The first
    end-to-end smoke found both of the first two defects, which is exactly the class
    of silent-zero this project forbids.
  - **Rejected: a live dashboard.** The value here is the RETAINED series, which
    answers questions asked hours later; a live view answers only the question being
    asked right now, and the operational snapshot
    (`scripts/monitor_run_health.sh`) already covers that. An unmerged
    `feature/runcontrol-gui` branch does have a live vitals collector -- a different
    job, and it can consume these primitives instead of duplicating them.
  - **Rejected: a new dependency.** `/proc` reads plus `shutil.disk_usage` cover
    everything on the run server; `psutil` (already present, optional) is used only
    where `/proc` does not exist, i.e. Windows development machines, and the sample
    says so when it is.
  - **Rejected: making the recorder decide.** It records; it never kills, restarts
    or throttles anything. A sampling failure is counted, logged once and reported in
    the summary (`failed_sample_count`), so a thin series cannot be mistaken for a
    quiet machine -- but it can never end the run it measures.
  - **Rejected: recursive `du` of the working directory per sample.** Walking a
    cache directory of tens of GB every 30 s would make the monitor part of the
    load. Free space per configured filesystem (`working_directory`, `output_path`)
    is cheap and answers the ENOSPC question the pipeline actually has.
- **Consequences:** Every run from now on carries `monitoring/` next to its outputs,
  and a run manifest can cite a measured peak per-worker RSS, per-stage wall/CPU
  split, CPU efficiency, disk high-water mark, thread peak and kernel OOM/segfault
  counts instead of prose reconstructed afterwards. The `num_workers` question of
  issue #281 becomes answerable from a committed measurement rather than folklore --
  but only after a real server run: the `/proc` reader is covered by unit tests
  against a fake `/proc` root and the psutil path by an end-to-end smoke on Windows,
  and no Linux 100 % run has been recorded with it yet. Sampling costs one `/proc`
  pass per interval (default 30 s) plus one `dmesg` call, which is negligible
  against a multi-hour run but is not zero, and it can be switched off per run.
  Per-stage wall clock in the summary is measured between the first and last SAMPLE
  of a stage, so it is short by up to one interval at each end; the exact per-stage
  wall clock remains `braunschweig.analysis.runtime`'s, derived from the log.
