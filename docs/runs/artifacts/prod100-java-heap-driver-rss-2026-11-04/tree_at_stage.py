"""Process tree at the first and the last sample of a stage (read-only; docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml).

Usage: python3 tree_at_stage.py <resource_series_*.jsonl> <stage name prefix>

Prints, for the first and the last recorder sample whose stage name starts with the given prefix, the process
count, the tree RSS, the available memory and every process of the tree (pid, parent pid, state, RSS, the
first 110 characters of its command line). GiB = recorder kB / (1024 * 1024).
"""
import json
import sys

KIB_PER_GIB = 1024.0 * 1024.0


def main(path, stage_prefix):
    rows = []
    with open(path) as handle:
        for line in handle:
            row = json.loads(line)
            if (row.get("stage") or "").startswith(stage_prefix):
                rows.append(row)
    print("samples of %s: %d" % (stage_prefix, len(rows)))
    for label, row in (("first", rows[0]), ("last", rows[-1])):
        print("-- %s sample %s, %d processes, tree RSS %.2f GiB, memory available %.1f GiB" % (
            label, row["timestamp"], row["process_count"], row["tree_rss_kb"] / KIB_PER_GIB,
            row["memory_available_kb"] / KIB_PER_GIB))
        for process in row["processes"]:
            print("   pid %-8s ppid %-8s state %s rss %8.3f GiB  %s" % (
                process["pid"], process["ppid"], process["state"], process["rss_kb"] / KIB_PER_GIB,
                (process["tag"] or "")[:110]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
