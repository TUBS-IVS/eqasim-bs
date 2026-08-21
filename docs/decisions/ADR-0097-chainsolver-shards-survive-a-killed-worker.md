# ADR-0097 · 2026-08-21 · Chainsolver shards run through an executor that reports a killed worker, and lost shards are retried

- **Status:** active
- **Context:** The night run of 2026-08-20 stopped inside
  `braunschweig.synthesis.locations.secondary_chainsolvers` after 7.6 h of stage
  time, with `shard 61/62 done` as its last line, and then sat at load 0.02 with no
  CPU accumulation until it was killed by hand. Two independent causes chain into
  that, both established rather than assumed (issue #344):
  1. **Trigger — memory exhaustion from a concurrent workload.** The kernel
     OOM-killer removed 7 processes between 23:20:22 and 23:26:54 (3 × python at
     ~20 GB, 4 × populationsim at 20-30 GB anon-rss) on a 125 GB machine. A
     *second* run had started at 22:35:54 in the worktree `wt_i227_smoke`
     (PopulationSim, `num_workers` 8) while the 62-worker chainsolver stage of the
     analysis run (started 17:29:46) was in flight. The second run corroborates the
     same event from its own side: its log records `exit code 137` (128+9, SIGKILL)
     and ends with `loaded 4/8 batch outputs (4 missing)`. Each run had completed
     on its own hours earlier, which is exactly why the same stage with the same
     hash had succeeded at 13:19.
  2. **Amplifier — `multiprocessing.Pool.imap_unordered` never notices a worker
     killed mid-task.** Pool respawns the dead worker to keep the pool size, but
     the task that died with it is never re-queued and its result never arrives, so
     the consuming loop waits for it indefinitely. Reproduced on the run server's
     own interpreter: 4 tasks, worker 0 removed with SIGKILL → 3 results delivered,
     then an indefinite hang (killed after 25 s); `ProcessPoolExecutor` raises
     `BrokenProcessPool` on the identical scenario and exits. The `n-1` pattern is
     precisely the production pattern (61 of 62).
  It is therefore neither a race nor a deadlock, and the 63 parked processes were
  not evidence of one: `parallel_solving` used `processes=len(tasks)`, i.e. one
  worker per shard, so a worker that delivered its single shard is idle by design.
- **Decision:** Consume shard results through
  `concurrent.futures.ProcessPoolExecutor` and retry only the shards whose results
  are missing, in a fresh executor, up to
  `braunschweig.chainsolvers.shard_attempts` (default 3, i.e. two retries; 1
  disables the retry and fails on the first lost worker).
  - **Determinism is preserved by construction, not by test alone.** Recovery
    never touches the shard definitions: the person slices and the per-shard seeds
    (`_derive_shard_seed(base_seed, shard_index)`) are unchanged and results are
    still recombined in shard-index order, so a retried shard reproduces
    bit-identical output. The parallel result remains a different (equally valid)
    Monte-Carlo realisation than the serial path, exactly as before.
  - **Only `BrokenProcessPool` is recovered from.** An exception raised INSIDE a
    shard is a real defect and propagates unchanged, so the retry loop can never
    mask a bug. Pinned by a test.
  - **The recovery is loud.** Each retry logs how many workers died, which shard
    indices they held, and how many shards are already done and will NOT be
    recomputed (project rule on fallback transparency). A silent retry would hide
    the memory pressure that caused it, which is the information the operator
    actually needs.
  - **Rejected: a static memory cap on the pool.** The shard count IS the worker
    count, so lowering the pool size makes workers process several shards
    sequentially; total work is unchanged and wall clock scales roughly inversely.
    Halving parallelism for headroom would turn the pipeline's longest stage from
    7.5 h into ~15 h, permanently, to guard against an operator mistake that costs
    ~7 min (one shard of 62) once this decision is in place. If headroom becomes a
    recurring problem, a check that fails FAST before starting, or admission
    control across concurrent runs, is the right shape — not a permanent
    parallelism tax.
  - **Rejected: keeping Pool and adding a per-result timeout.** Shard runtimes vary
    by a large factor, so any timeout safe for the slowest shard is long enough to
    waste hours, and it cannot distinguish a slow shard from a dead worker. The
    executor detects the death exactly, with no threshold to tune.
  - **Rejected: warn-only.** The failure mode being fixed is a silent stall; a log
    line nobody reads at 03:00 does not end it.
- **Consequences:**
  - The stage is devalidated (`parallel_solving` feeds the `validate()` token), so
    it re-executes once. That costs nothing here: its result was never produced in
    the affected run, and the stage must run again regardless.
  - **The trigger is not fixed and cannot be fixed here.** Two heavy runs on one
    box will still exhaust memory; this decision only makes the consequence cost
    ~7 min instead of 7.6 h, and makes it visible. The operational rule — do not
    start a second heavy run while a 100 % stage is in flight — stands on its own.
  - A shard that dies in every attempt raises a `RuntimeError` naming the shard
    indices and pointing at the kernel log, so the diagnosis starts where the
    evidence is instead of at a silent process tree.
- **Verification:** `tests/test_chainsolver_worker_death.py` — six orchestration
  tests through an injected executor factory (delivery, retry-only-the-missing,
  bounded attempts, task-exception passthrough, progress, and a source pin against
  reintroducing `imap_unordered(`), plus one integration test that runs the real
  `ProcessPoolExecutor` over a worker which really dies (`os._exit`) and asserts the
  recovery. That integration test needs the fork start method, so it SKIPS on
  Windows and was run explicitly on the Linux run server (7 passed there, in an
  isolated worktree at `origin/main`). No production run has exercised the recovery
  yet.
