"""Compare the completed-donor outputs of the two smoke arms (PR #594, fine child bands).

Usage: python compare_arms.py <cache_fix> <cache_base> <repo_root>

Reads the cached ``braunschweig.popsim.completed_donor`` pickle of each arm and reports how
many donor persons changed their plan source, split by age, and how many 6-13-year-olds draw
a plan source from the other fine band (6-9 vs 10-13) in each arm.
"""
import glob
import pickle
import sys

import pandas as pd

sys.path.insert(0, sys.argv[3])


def load(cache_dir):
    paths = glob.glob(f"{cache_dir}/braunschweig.popsim.completed_donor__*.p")
    if len(paths) != 1:
        raise SystemExit(f"expected one completed_donor pickle in {cache_dir}, found {paths}")
    with open(paths[0], "rb") as handle:
        result = pickle.load(handle)
    persons = result.persons.reset_index(drop=True)
    return persons, result


def fine_band(age):
    return pd.cut(age, bins=[5, 9, 13], labels=["6_9", "10_13"])


def cross_band(persons):
    ages = persons.set_index(["H_ID", "P_ID"])["HP_ALTER"]
    ages = ages[~ages.index.duplicated()]
    source_age = ages.reindex(pd.MultiIndex.from_arrays([persons["source_H_ID"], persons["source_P_ID"]])).to_numpy()
    own = fine_band(persons["HP_ALTER"])
    donor = fine_band(pd.Series(source_age, index=persons.index))
    child = persons["HP_ALTER"].between(6, 13)
    foreign = (persons["source_H_ID"] != persons["H_ID"]) | (persons["source_P_ID"] != persons["P_ID"])
    crossed = child & foreign & donor.notna() & (own.astype(str) != donor.astype(str))
    return int((child & foreign).sum()), int(crossed.sum())


def main():
    fix, fix_result = load(sys.argv[1])
    base, base_result = load(sys.argv[2])
    if len(fix) != len(base):
        raise SystemExit(f"row counts differ: fix {len(fix)} vs base {len(base)}")
    key = ["H_ID", "P_ID"]
    if not (fix[key].to_numpy() == base[key].to_numpy()).all():
        raise SystemExit("person order differs between the arms; align on a stable key first")

    changed = (fix["source_H_ID"].to_numpy() != base["source_H_ID"].to_numpy()) | (
        fix["source_P_ID"].to_numpy() != base["source_P_ID"].to_numpy())
    age = fix["HP_ALTER"]
    print(f"donor persons: {len(fix)}")
    print(f"plan source changed: {int(changed.sum())} ({100 * changed.mean():.3f} %)")
    for label, mask in (("age 6-13", age.between(6, 13)), ("other ages", ~age.between(6, 13))):
        print(f"  {label}: {int((changed & mask).sum())} of {int(mask.sum())}")
    for name, frame in (("base", base), ("fix", fix)):
        n_foreign, n_crossed = cross_band(frame)
        print(f"{name}: 6-13-year-olds with a foreign plan source {n_foreign}, "
              f"of which across the 6-9 / 10-13 edge {n_crossed}")
    print("weekend report base:", base_result.weekend_report)
    print("weekend report fix: ", fix_result.weekend_report)
    print("diary report base:", base_result.diary_report)
    print("diary report fix: ", fix_result.diary_report)


if __name__ == "__main__":
    main()
