# Run resource recorder (`braunschweig/monitoring/`)

What it is, how the pieces fit, and the rules a maintainer must keep. Why it exists
and which alternatives were rejected is ADR-0100; its production state is the
Feature Registry record `run_resource_recorder`.

## The four modules, in dependency order

| Module | Owns | Depends on |
|---|---|---|
| `process_tree.py` | reading `/proc`: tree membership, tree CPU, per-process `VmHWM`/RSS/threads/state/IO | stdlib only (`psutil` optional, off-Linux) |
| `sampler.py` | one flat sample dict: tree aggregates + system RAM/swap/load/disk/IO + kernel events + log liveness and stage tag | `process_tree` |
| `recorder.py` | the JSONL file, the background thread, the config keys, the no-op when disabled | `sampler`, `summary` |
| `summary.py` | reducing a finished series to manifest-ready fields and markdown | `braunschweig.progress` (duration formatting) |

Consumers: `scripts/run_synpp.py` (every run, via `recorder.record_from_config`),
`scripts/monitor_run.py` (attach to a run already in flight, or summarise a series),
and `matsim/runtime/process_watchdog.py` (the CPU reader only).

## Rules that are not obvious from the code

- **The tree CPU total is not monotonic.** It sums the processes ALIVE at that
  instant, so an exiting worker takes its accumulated CPU out of the sum. Never
  difference it naively: `summary.py` accounts per pid where process rows exist and
  otherwise sums only the POSITIVE increments, and states which in
  `cpu_accounting`. Anything new that consumes the series must make the same
  distinction.
- **Count threads, never walk them.** `num_threads` comes from each process' own
  `stat` line. Adding `/proc/<pid>/task/*` CPU times to the process' own
  double-counts the same work. The thread total stays a separate signal because
  oversubscription is its own failure class here (see `braunschweig/parallelism.py`).
- **Three verdicts, not two.** A step is `progress`, `no_progress` or
  `unmeasurable`. Only `no_progress` may become a stall claim; an unreadable tree is
  never one. Likewise `cpu_seconds: null` means "not measured" and `0` means
  "measured, nothing happened" -- the first end-to-end smoke caught exactly this
  conflation.
- **Every sample names its source.** `proc` (primary), `psutil` (only where `/proc`
  is absent), `unavailable:<reason>`. If you add a reader, add its source string; a
  value without a named provenance is not acceptable in this series.
- **The recorder may never end the run.** `sample_once` catches everything, counts
  it, logs the first failure once, and returns `None`. `failed_sample_count` travels
  into the summary so a thin series is visibly thin rather than quietly reassuring.
- **The summary must exist even when the run dies.** It is written from a `finally`
  in `record_in_background`; rows are flushed per sample. Do not move the summary
  write into the success path.
- **`proc_root` is injectable everywhere.** That is what makes the `/proc` reader
  testable on Windows (`tests/fake_proc.py` builds a fake tree with realistic
  proc(5) field offsets) and what lets `scripts/monitor_run.py --proc-root` point at
  a container's `/proc`. Keep it threaded through new readers.
- **Per-stage wall clock here is sample-bounded.** It is measured between a stage's
  first and last sample, so it is short by up to one interval at each end. The exact
  per-stage durations belong to `braunschweig.analysis.runtime`, which parses the log
  timestamps; this module adds the resource dimension that the log cannot carry. Both
  read the same synpp markers (`Executing stage`, `Finished running`) whose format is
  owned by `braunschweig/logging_setup.py`.

## Config keys (base config only, `configs/base_bs.yml`)

`monitoring_enabled` (default true, and pinned ON for every scale by
`tests/test_configs_composed.py`), `monitoring_interval_seconds` (30),
`monitoring_include_process_rows` (true -- false keeps aggregates only, for a very
long run), `monitoring_kernel_events` (true -- false skips the `dmesg` probe),
`monitoring_output_directory` (defaults to `<working_directory>/monitoring`).

## Reading a run afterwards

```
python scripts/monitor_run.py summarize <working_directory>/monitoring/resource_series_<ts>.jsonl
```

prints the markdown block a run manifest wants (peak per-worker RSS with its pid and
cmdline tag, per-stage wall/CPU/efficiency, disk high-water mark, kernel events,
measured no-progress spans) and writes it next to the series as `.summary.md` and
`.summary.json`.
