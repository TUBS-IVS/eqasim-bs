"""End state of a recorder series (read-only; docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml).

Usage: python3 series_end_state.py <resource_series_*.jsonl>

Prints the sample count and time span of one recorder series, every change of the kernel's OOM-kill counter
(``oom_kill_count``, read from dmesg and cumulative since boot, so a value that never changes during the
series means the kernel killed nothing while it ran) and the last three samples: stage, available memory, swap
in use and every process of the tree with its RSS. GiB = recorder kB / (1024 * 1024).
"""
import json
import sys

KIB_PER_GIB = 1024.0 * 1024.0


def main(path):
    with open(path) as handle:
        rows = [json.loads(line) for line in handle]
    print("samples: %d, first %s, last %s" % (len(rows), rows[0]["timestamp"], rows[-1]["timestamp"]))
    previous = None
    for row in rows:
        count = row.get("oom_kill_count")
        if count != previous:
            print("oom_kill_count %s from sample %d at %s (stage %s)" % (
                count, row["sample_index"], row["timestamp"], (row.get("stage") or "-").split("__")[0]))
            previous = count
    for row in rows[-3:]:
        print("-- %s stage %s available %.2f GiB swap used %.2f GiB oom_kill_count %s processes %s" % (
            row["timestamp"], (row.get("stage") or "-").split("__")[0], row["memory_available_kb"] / KIB_PER_GIB,
            row["swap_used_kb"] / KIB_PER_GIB, row.get("oom_kill_count"), row["process_count"]))
        for process in row["processes"]:
            print("   pid %-8s rss %8.3f GiB %s" % (
                process["pid"], process["rss_kb"] / KIB_PER_GIB, (process["tag"] or "")[:70]))


if __name__ == "__main__":
    main(sys.argv[1])
