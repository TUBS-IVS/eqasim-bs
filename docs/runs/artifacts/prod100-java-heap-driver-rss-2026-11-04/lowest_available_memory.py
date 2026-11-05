"""The recorder samples with the lowest available memory (read-only; docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml).

Usage: python3 lowest_available_memory.py <resource_series_*.jsonl> [<count>]

Prints the <count> (default 3) samples with the lowest ``memory_available_kb`` of one recorder series, in time
order: timestamp, stage, available memory, swap in use, tree RSS and every process of the tree with its RSS.
GiB = recorder kB / (1024 * 1024). ``memory_available_kb`` is the kernel's MemAvailable: free memory plus the
page cache that can be reclaimed, so a value near zero means the machine had nothing left to give.
"""
import json
import sys

KIB_PER_GIB = 1024.0 * 1024.0


def main(path, count):
    rows = []
    with open(path) as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("memory_available_kb") is not None:
                rows.append(row)
    lowest = sorted(rows, key=lambda row: row["memory_available_kb"])[:count]
    print("%d samples in the series, %d lowest by available memory:" % (len(rows), len(lowest)))
    for row in sorted(lowest, key=lambda row: row["timestamp"]):
        print("-- %s stage %s: available %.2f GiB of %.2f GiB, swap in use %.2f GiB of %.2f GiB, tree RSS %.2f GiB" % (
            row["timestamp"], (row.get("stage") or "-").split("__")[0], row["memory_available_kb"] / KIB_PER_GIB,
            row["memory_total_kb"] / KIB_PER_GIB, row["swap_used_kb"] / KIB_PER_GIB,
            row["swap_total_kb"] / KIB_PER_GIB, row["tree_rss_kb"] / KIB_PER_GIB))
        for process in row["processes"]:
            print("   pid %-8s ppid %-8s rss %8.3f GiB  %s" % (
                process["pid"], process["ppid"], process["rss_kb"] / KIB_PER_GIB, (process["tag"] or "")[:60]))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
