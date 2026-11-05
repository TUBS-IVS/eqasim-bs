"""Summary of the MATSim MemoryObserver lines of one or more logs (read-only; docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml).

Usage: python3 memory_observer_stats.py <log> [<log> ...]

Line shape: ``... MemoryObserver:43 used RAM: 13334 MB  free: 4937 MB  total: 18272 MB  max: 88064 MB``
(MB = MiB, the JVM's unit). ``used`` includes garbage the JVM has not collected yet, so its maximum is an
UPPER bound of the live set, not the live set itself. Per file: the number of samples, the maximum heap
(-Xmx) the JVM reported, the maximum of used and total, and the used percentiles in MiB and GiB.
"""
import re
import sys

PATTERN = re.compile(
    r"MemoryObserver:\d+ used RAM: (\d+) MB\s+free: (\d+) MB\s+total: (\d+) MB\s+max: (\d+) MB")


def percentile(sorted_values, fraction):
    index = min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def main(paths):
    for path in paths:
        used, total, max_heap = [], [], set()
        with open(path, errors="replace") as handle:
            for line in handle:
                match = PATTERN.search(line)
                if match:
                    used.append(int(match.group(1)))
                    total.append(int(match.group(3)))
                    max_heap.add(int(match.group(4)))
        name = path.split("/")[-1]
        if not used:
            print("%s: no MemoryObserver lines" % name)
            continue
        ordered = sorted(used)
        print("%s: n=%d, max heap %s MiB, used max %d MiB (%.1f GiB), total max %d MiB, used p50 %d MiB "
              "(%.1f GiB), p90 %d MiB (%.1f GiB), p99 %d MiB (%.1f GiB)" % (
                  name, len(used), sorted(max_heap), ordered[-1], ordered[-1] / 1024.0, max(total),
                  percentile(ordered, 0.5), percentile(ordered, 0.5) / 1024.0,
                  percentile(ordered, 0.9), percentile(ordered, 0.9) / 1024.0,
                  percentile(ordered, 0.99), percentile(ordered, 0.99) / 1024.0))


if __name__ == "__main__":
    main(sys.argv[1:])
