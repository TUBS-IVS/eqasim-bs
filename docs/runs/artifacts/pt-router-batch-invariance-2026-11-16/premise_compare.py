"""Compare router output of one batch against split batches, row by row as text (audit H, F-H premise)."""
import csv
import sys


def read(prefix):
    with open(prefix + ".trips.csv") as f:
        r = csv.DictReader(f)
        trips = {row["identifier"]: tuple(sorted(row.items())) for row in r}
    legs = {}
    with open(prefix + ".legs.csv") as f:
        for row in csv.DictReader(f):
            legs.setdefault(row["identifier"], []).append(tuple(sorted(row.items())))
    return trips, {k: sorted(v) for k, v in legs.items()}


def tasks(prefix):
    with open(prefix + ".csv") as f:
        return [row["identifier"] for row in csv.DictReader(f)]


all_trips, all_legs = read("all")
n_tasks = len(tasks("all"))
print("one batch: %d tasks, %d trips rows, %d tasks with legs, %d legs rows"
      % (n_tasks, len(all_trips), len(all_legs), sum(len(v) for v in all_legs.values())))
bad = 0
for prefix in sys.argv[1:]:
    ids = tasks(prefix)
    trips, legs = read(prefix)
    diff_trips = [i for i in ids if trips.get(i) != all_trips.get(i)]
    diff_legs = [i for i in ids if legs.get(i, []) != all_legs.get(i, [])]
    n_routed = sum(1 for i in ids if i in trips)
    print("%s: %d tasks, %d with a trips row, trips differing %d, legs differing %d"
          % (prefix, len(ids), n_routed, len(diff_trips), len(diff_legs)))
    for i in (diff_trips + diff_legs)[:3]:
        print("  example", i, trips.get(i), all_trips.get(i))
    bad += len(diff_trips) + len(diff_legs)
print("RESULT", "IDENTICAL" if bad == 0 else "DIFFERENT (%d)" % bad)
