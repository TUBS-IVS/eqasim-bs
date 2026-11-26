"""Prune a COPIED synpp cache to the stage variants the explain report of the run names (#678 1 % comparison).

Usage: python i678_ab_prune.py EXPLAIN_JSON CACHE_DIR [--delete]

Keeps every entry whose name has no "__" (not a stage variant) and every variant whose "<stage>__<hash>" is the
current hash, a stale_if hash or an unhashed-ancestor hash of some stage in the report (scripts/explain_stale_stages.py
--json of the run's own commit and overlay); lists, or with --delete removes, the other stage variants. Purpose: the
source cache held many variants of earlier runs and two full copies did not fit on the disk. Only for a run-owned
copy, never for another run's cache.
"""
import json
import os
import shutil
import sys


def size(path):
    if os.path.islink(path) or os.path.isfile(path):
        return os.lstat(path).st_size
    return sum(os.lstat(os.path.join(root, f)).st_size for root, _, files in os.walk(path) for f in files)


def stem_of(name):
    for suffix in (".metadata.json", ".cache", ".p"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def main():
    report, cache = sys.argv[1], sys.argv[2]
    delete = "--delete" in sys.argv
    with open(report, encoding="utf-8") as handle:
        assessments = json.load(handle)
    keep = set()
    for a in assessments.values():
        keep.add(a["hash"])
        keep.update(a.get("stale_if_hashes") or [])
        keep.update(a.get("unhashed_ancestor_hashes") or [])
    kept = removed = 0
    n_kept = n_removed = 0
    for name in sorted(os.listdir(cache)):
        path = os.path.join(cache, name)
        if "__" not in name or stem_of(name) in keep:
            kept += size(path)
            n_kept += 1
            continue
        removed += size(path)
        n_removed += 1
        if delete:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
    verb = "removed" if delete else "would remove"
    print(f"stage hashes named: {len(keep)}; kept {n_kept} entries {kept / 2**30:.1f} GiB; "
          f"{verb} {n_removed} entries {removed / 2**30:.1f} GiB")


if __name__ == "__main__":
    main()
