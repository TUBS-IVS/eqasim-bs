"""Sum the outcome numbers of replay_methods.py per method and parent level (issue #646).

Reads one or more CSVs written by replay_methods.py and prints, and writes as CSV, one line per parent
level and method over the instances that every method has: the number of instances, how many took a
fallback (parent outcome other than ``simultaneous``), and the sums of the numeric columns; the share of
conserved rows is weighted by the rows of each instance. Engineering reading.

Usage: python summarise_methods.py <out_csv> <replay_csv> [<replay_csv> ...]
"""
import collections
import csv
import sys

SUMS = ["seconds", "conserve_deviation", "parent_shift_sum", "cell_distance_weighted", "cell_distance",
        "target_distance", "household_total_deviation", "distinct_rows", "integer_households", "rows"]
FIELDS = ["level", "method", "instances", "fallback_instances", "rows_conserved_share", *SUMS]


def level_of(instance: str) -> str:
    """The parent level of an instance name ``<number>_<level>_<zone>``."""
    return instance.split("_", 1)[1].split("_", 1)[0]


def main() -> int:
    out_path, sources = sys.argv[1], sys.argv[2:]
    by_instance = collections.defaultdict(dict)
    for name in sources:
        with open(name, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                by_instance[(name, row["instance"])][row["method"]] = row
    methods = sorted({m for v in by_instance.values() for m in v})
    complete = {k: v for k, v in by_instance.items() if all(m in v for m in methods)}
    print("methods %s; instances with all of them: %d of %d" % (methods, len(complete), len(by_instance)))
    rows = []
    for level in sorted({level_of(k[1]) for k in complete}) + ["ALL"]:
        keys = [k for k in complete if level == "ALL" or level_of(k[1]) == level]
        for method in methods:
            records = [complete[k][method] for k in keys]
            n_rows = sum(float(r["rows"]) for r in records)
            line = {"level": level, "method": method, "instances": len(records),
                    "fallback_instances": sum(r["outcome"] != "simultaneous" for r in records),
                    "rows_conserved_share": "%.6f" % (
                        sum(float(r["rows_conserved_share"]) * float(r["rows"]) for r in records) / n_rows)}
            for name in SUMS:
                line[name] = "%.2f" % sum(float(r[name] or 0) for r in records)
            rows.append(line)
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    for line in rows:
        print("  ".join("%s=%s" % (name, line[name]) for name in FIELDS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
