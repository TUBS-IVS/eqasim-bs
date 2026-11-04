"""Driver RSS per stage from a run resource recorder series (read-only; docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml).

Usage: python3 driver_rss_by_stage.py <resource_series_*.jsonl>

Reads one recorder series (braunschweig.monitoring, one JSON object per sample) and prints, for every run of
consecutive samples with the same stage name, the RSS of the ROOT process (the synpp driver) at the first
sample, its maximum and at the last sample, the lowest available memory seen and the largest process count.
All memory figures are GiB: the recorder reports kB (KiB), divided by 1024 * 1024, the unit that
braunschweig.resources calls "GB" and the JVM calls "G".
"""
import json
import sys

KIB_PER_GIB = 1024.0 * 1024.0


def root_rss_kb(row):
    for process in row.get("processes", []):
        if process.get("pid") == row.get("root_pid"):
            return process.get("rss_kb")
    return None


def main(path):
    groups = []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            stage = (row.get("stage") or "-").split("__")[0]
            sample = {
                "timestamp": row["timestamp"], "rss_kb": root_rss_kb(row),
                "available_kb": row.get("memory_available_kb"), "processes": row.get("process_count") or 0,
            }
            if groups and groups[-1]["stage"] == stage:
                groups[-1]["samples"].append(sample)
            else:
                groups.append({"stage": stage, "samples": [sample]})

    print("%-62s %-19s %6s %8s %8s %8s %9s %6s" % (
        "stage", "first sample", "n", "rss_first", "rss_max", "rss_last", "avail_min", "n_proc"))
    for group in groups:
        samples = group["samples"]
        rss = [s["rss_kb"] for s in samples if s["rss_kb"] is not None]
        available = [s["available_kb"] for s in samples if s["available_kb"] is not None]
        print("%-62s %-19s %6d %8.2f %8.2f %8.2f %9.1f %6d" % (
            group["stage"][:62], samples[0]["timestamp"], len(samples),
            (samples[0]["rss_kb"] or 0) / KIB_PER_GIB, (max(rss) if rss else 0) / KIB_PER_GIB,
            (samples[-1]["rss_kb"] or 0) / KIB_PER_GIB, (min(available) if available else 0) / KIB_PER_GIB,
            max(s["processes"] for s in samples)))


if __name__ == "__main__":
    main(sys.argv[1])
