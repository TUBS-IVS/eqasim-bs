#!/usr/bin/env python3
"""Share of seed persons whose plan source changed between two completed-donor results (A1 smoke).

Reads two pickled results of the stage braunschweig.popsim.completed_donor (its ``.persons`` frame) and
compares, per seed person (H_ID, P_ID), the plan source columns. Prints and writes one JSON with the counts
of all persons, students (P_TAET 10) and the other persons whose source differs. Read-only.

Caveat: the two results may differ by more than the student key (code changes between the two code states);
the comparison counts every difference and does not attribute it.

    python compare_plan_sources.py <before.p> <after.p> <out.json>
"""
from __future__ import annotations

import json
import pickle
import sys

STUDENT_TAET = 10
SOURCE_COLUMNS = ("source_H_ID", "source_P_ID")


def persons_of(path):
    with open(path, "rb") as handle:
        result = pickle.load(handle)
    return result.persons


def main(argv) -> int:
    before, after = persons_of(argv[1]), persons_of(argv[2])
    key = ["H_ID", "P_ID"]
    columns = [c for c in SOURCE_COLUMNS if c in before.columns and c in after.columns]
    if not columns:
        raise SystemExit("no plan source column in both frames")
    merged = before[key + columns + ["P_TAET"]].merge(after[key + columns], on=key, how="inner",
                                                       suffixes=("_before", "_after"), validate="1:1")
    changed = None
    for column in columns:
        differs = merged[column + "_before"].ne(merged[column + "_after"])
        changed = differs if changed is None else (changed | differs)
    student = merged["P_TAET"].eq(STUDENT_TAET)
    out = {"n_persons_before": int(len(before)), "n_persons_after": int(len(after)),
           "n_persons_compared": int(len(merged)), "source_columns": columns,
           "n_changed": int(changed.sum()), "n_students": int(student.sum()),
           "n_students_changed": int((changed & student).sum()),
           "n_other_persons": int((~student).sum()), "n_other_persons_changed": int((changed & ~student).sum())}
    out["share_other_persons_changed"] = out["n_other_persons_changed"] / out["n_other_persons"]
    out["share_students_changed"] = out["n_students_changed"] / out["n_students"] if out["n_students"] else None
    with open(argv[3], "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, sort_keys=True)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
