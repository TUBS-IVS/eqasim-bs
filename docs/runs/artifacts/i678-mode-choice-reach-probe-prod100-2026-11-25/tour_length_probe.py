"""Read-only probe on prod100_20261031 (uncut population): how long are the home-based tours of residents that the
mode choice's tour length filter (maximumLength 6) excludes? Counts tours without an ``outside`` activity by number
of trips. Model output of one run, NOT a validation.

Usage on felix: python tour_length_probe.py ~/prod100_20261031/cache
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frozen_scope_probe import HOME, OUTSIDE, POPULATION_STEM, iter_persons, tours_of  # noqa: E402

UNCUT = Path(sys.argv[1]).expanduser() / (POPULATION_STEM + ".cache") / "population.xml.gz"


def main() -> int:
    lengths = collections.Counter()
    n_tours = 0
    for _person_id, _subpopulation, activities, trips in iter_persons(UNCUT):
        if not trips or any((a[3] or "").startswith("ic_") for a in activities):
            continue
        tour_of = tours_of(activities, len(trips))
        for tour in range(tour_of[-1] + 1):
            indices = [i for i, t in enumerate(tour_of) if t == tour]
            members = activities[indices[0]:indices[-1] + 2]
            if any(a[0] == OUTSIDE for a in members):
                continue
            n_tours += 1
            lengths[min(len(indices), 15)] += 1
    print("resident home-based tours without an outside activity (uncut):", n_tours)
    over = sum(v for k, v in lengths.items() if k > 6)
    print("tours with more than 6 trips:", over)
    cumulative = 0
    for length in sorted(k for k in lengths if k > 6):
        cumulative += lengths[length]
        label = f"{length}+" if length == 15 else str(length)
        print(f"  {label:>3} trips: {lengths[length]:>6} tours, cumulative {100.0 * cumulative / over:5.1f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
