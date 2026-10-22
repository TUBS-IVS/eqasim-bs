"""Compare the own-reason stale sets of two explainer --json reports (#468 migration acceptance).

A stage counts as stale for own reasons when its "reasons" list is non-empty (the explainer's STALE lines);
"required" alone (a run target, re-executed on every run under rerun_required) is listed separately.
Prints both sets, their difference in both directions, and the reasons of every stage in either set.
Exit 0 when the two own-reason sets are equal, 1 otherwise.
Usage: python3 compare_stale_sets_i468p4.py <A.json> <B.json>
"""
import json
import sys

a_path, b_path = sys.argv[1:3]
reports = [json.load(open(path, encoding="utf-8")) for path in (a_path, b_path)]


def own_stale(report):
    return {(name, entry["hash"]) for name, entry in report.items()
            if entry["reasons"] and entry["reasons"] != ["required"]}


def required_only(report):
    return {(name, entry["hash"]) for name, entry in report.items() if entry["reasons"] == ["required"]}


sets = [own_stale(r) for r in reports]
print(f"stages: A {len(reports[0])}, B {len(reports[1])}")
print(f"own-reason stale (excluding required-only): A {len(sets[0])}, B {len(sets[1])}")
print(f"required-only: A {len(required_only(reports[0]))}, B {len(required_only(reports[1]))}")
print(f"stale in A only: {len(sets[0] - sets[1])}")
for name, stage_hash in sorted(sets[0] - sets[1]):
    print(f"  A-only {name} {stage_hash} {reports[0][name]['reasons']}")
print(f"stale in B only: {len(sets[1] - sets[0])}")
for name, stage_hash in sorted(sets[1] - sets[0]):
    print(f"  B-only {name} {stage_hash} {reports[1][name]['reasons']}")
print("stale in both (reasons A | B):")
for name, stage_hash in sorted(sets[0] & sets[1]):
    print(f"  {name}: {reports[0][name]['reasons']} | {reports[1][name]['reasons']}")
sys.exit(0 if sets[0] == sets[1] else 1)
