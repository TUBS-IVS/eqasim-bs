"""Per-stage synpp runtime analysis.

Parses a timestamped pipeline log into per-stage wall-clock durations, so the
slowest synpp stages (e.g. secondary locations / location choice, gravity) are
visible and resource/algorithm settings can be tuned with evidence.

The synpp log lines carry an ISO timestamp when the pipeline is launched via
``scripts/run_synpp.py`` (which sets a logging format with ``asctime``). Each
executed stage logs ``Executing stage <name> ...`` at its start and
``Finished running <name>.`` at its end; cached stages only log
``Loading cache for <name>`` and are (correctly) not timed.

CLI::

    python -m braunschweig.analysis.runtime --log <run.log> --output stage_runtime.csv
"""
from __future__ import annotations

import argparse
import io
import os
import re
from datetime import datetime

import pandas as pd

_TS = r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
_EXEC_RE = re.compile(_TS + r".*Executing stage (\S+)")
_FINISH_RE = re.compile(_TS + r".*Finished running (\S+?)\.?\s*$")
_HASH_SUFFIX_RE = re.compile(r"__[0-9a-f]+$")

_COLUMNS = ["stage", "stage_short", "start", "end", "duration_s"]


def _parse_ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")


def parse_stage_runtimes(log_text: str) -> pd.DataFrame:
    """Return per-executed-stage durations from a timestamped pipeline log.

    Columns: ``stage`` (full synpp id incl. hash), ``stage_short`` (hash stripped),
    ``start``, ``end`` (datetimes), ``duration_s`` (float). Sorted by duration
    descending. Stages that only loaded from cache are not included.
    """
    starts: dict[str, datetime] = {}
    rows = []
    for line in log_text.splitlines():
        m = _EXEC_RE.search(line)
        if m:
            starts[m.group(2)] = _parse_ts(m.group(1))
            continue
        m = _FINISH_RE.search(line)
        if m and m.group(2) in starts:
            name = m.group(2)
            start = starts.pop(name)
            end = _parse_ts(m.group(1))
            rows.append({
                "stage": name,
                "stage_short": _HASH_SUFFIX_RE.sub("", name),
                "start": start,
                "end": end,
                "duration_s": (end - start).total_seconds(),
            })
    df = pd.DataFrame(rows, columns=_COLUMNS)
    if len(df):
        df = df.sort_values("duration_s", ascending=False).reset_index(drop=True)
    return df


def parse_load_samples(csv_text: str) -> pd.DataFrame:
    """Parse the load-sampler CSV (ts, cpu_pct, mem_used_gb, nproc) into a frame
    with a parsed ``ts`` datetime column. ``cpu_pct`` is the busy share across all
    cores (0..100); ``nproc`` the core count."""
    df = pd.read_csv(io.StringIO(csv_text))
    df["ts"] = pd.to_datetime(df["ts"], format="%Y-%m-%dT%H:%M:%S")
    return df


def stage_utilization(stage_df: pd.DataFrame, samples_df: pd.DataFrame) -> pd.DataFrame:
    """Enrich per-stage runtimes with CPU/RAM utilization from load samples.

    For each stage window [start, end], aggregates the samples whose timestamp
    falls inside it: ``cpu_pct_mean``/``cpu_pct_max`` (% of all cores) and
    ``cores_busy_mean`` = mean(cpu_pct/100 * nproc) -- so a stage pinned to one
    core shows ``cores_busy_mean`` ~ 1 (the single-core smell), and ``mem_gb_max``.
    Stages with no samples in their window get NaN.
    """
    rows = []
    for _, st in stage_df.iterrows():
        window = samples_df[(samples_df["ts"] >= st["start"])
                            & (samples_df["ts"] <= st["end"])]
        rec = st.to_dict()
        if len(window):
            cores_busy = window["cpu_pct"] / 100.0 * window["nproc"]
            rec["cpu_pct_mean"] = float(window["cpu_pct"].mean())
            rec["cpu_pct_max"] = float(window["cpu_pct"].max())
            rec["cores_busy_mean"] = float(cores_busy.mean())
            rec["mem_gb_max"] = float(window["mem_used_gb"].max())
        else:
            rec["cpu_pct_mean"] = rec["cpu_pct_max"] = float("nan")
            rec["cores_busy_mean"] = rec["mem_gb_max"] = float("nan")
        rows.append(rec)
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Per-stage synpp runtime CSV")
    parser.add_argument("--log", required=True, help="path to a timestamped run log")
    parser.add_argument("--output", required=True, help="output CSV path")
    parser.add_argument("--samples", default=None,
                        help="load-sampler CSV (default: <log>_samples.csv if present)")
    parser.add_argument("--top", type=int, default=15, help="print the N slowest stages")
    args = parser.parse_args(argv)

    with open(args.log, "r", encoding="utf-8", errors="replace") as handle:
        df = parse_stage_runtimes(handle.read())

    # Enrich with CPU/RAM utilization if a load-sampler CSV is available, so
    # single-core stages (cores_busy ~ 1) are visible alongside durations.
    samples_path = args.samples
    if samples_path is None:
        guess = os.path.splitext(args.log)[0] + "_samples.csv"
        samples_path = guess if os.path.exists(guess) else None
    has_util = False
    if samples_path and os.path.exists(samples_path) and len(df):
        with open(samples_path, "r", encoding="utf-8", errors="replace") as handle:
            df = stage_utilization(df, parse_load_samples(handle.read()))
        has_util = True

    df.to_csv(args.output, index=False)

    total = df["duration_s"].sum() if len(df) else 0.0
    print(f"[runtime] {len(df)} executed stages, total {total/60.0:.1f} min "
          f"-> {args.output}")
    for _, row in df.head(args.top).iterrows():
        util = f"  ~{row['cores_busy_mean']:.0f} cores" if has_util and row.get("cores_busy_mean") == row.get("cores_busy_mean") else ""
        print(f"  {row['duration_s']/60.0:7.2f} min  {row['stage_short']}{util}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
