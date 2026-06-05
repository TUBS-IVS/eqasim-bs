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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Per-stage synpp runtime CSV")
    parser.add_argument("--log", required=True, help="path to a timestamped run log")
    parser.add_argument("--output", required=True, help="output CSV path")
    parser.add_argument("--top", type=int, default=15, help="print the N slowest stages")
    args = parser.parse_args(argv)

    with open(args.log, "r", encoding="utf-8", errors="replace") as handle:
        df = parse_stage_runtimes(handle.read())
    df.to_csv(args.output, index=False)

    total = df["duration_s"].sum() if len(df) else 0.0
    print(f"[runtime] {len(df)} executed stages, total {total/60.0:.1f} min "
          f"-> {args.output}")
    for _, row in df.head(args.top).iterrows():
        print(f"  {row['duration_s']/60.0:7.2f} min  {row['stage_short']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
