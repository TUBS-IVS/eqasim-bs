"""Switch-off identity check of the loop placement (issue #679, task C7).

Proves that the OFF path did not change between two eqasim-bs commits: the OFF arm of 66da6ee0
(``--old-cache``/``--old-output``) and the OFF arm of the final commit (``--new-cache``/``--new-output``),
same seed and configuration (``secondary_loop_zero_displacement: false``).

Checks
- the stage result of ``braunschweig.synthesis.locations.secondary_chainsolvers`` (``df_locations``,
  ``df_convergence``) and of ``braunschweig.synthesis.commute_day.spatial_locations_day``: frame-equal
  (same columns, dtypes, row order, values; geometries compared as WKB);
- the stage folder of the secondary stage: same file names and bytes;
- the written population (every file of the synthesis output directory): byte-equal (SHA-256).
The pickle of a stage is the ``pipeline.json`` entry of that stage with the newest ``updated`` time (as in
``i679_ab_metrics.py``); the chosen keys are written to the output.

Usage (eqasim conda env):
    python i679_off_identity.py --old-cache A/cache --new-cache B/cache --old-output A/output \
        --new-output B/output --out identity.json
Exit code 0 when everything is identical, 4 otherwise.
"""
import argparse
import hashlib
import json
import os
import pickle
import sys

import pandas as pd

from i679_ab_metrics import resolve_stage

STAGES = (
    "braunschweig.synthesis.locations.secondary_chainsolvers",
    "braunschweig.synthesis.commute_day.spatial_locations_day",
)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def directory_digests(directory):
    out = {}
    for root, _dirs, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            out[os.path.relpath(path, directory)] = sha256(path)
    return out


def frame_identical(a, b):
    """(identical, reason) for two DataFrames / GeoDataFrames."""
    if list(a.columns) != list(b.columns):
        return False, f"columns differ: {list(a.columns)} vs {list(b.columns)}"
    if [str(t) for t in a.dtypes] != [str(t) for t in b.dtypes]:
        return False, f"dtypes differ: {list(map(str, a.dtypes))} vs {list(map(str, b.dtypes))}"
    if not a.index.equals(b.index):
        return False, "index differs"
    a, b = a.copy(), b.copy()
    for column in a.columns:
        if str(a[column].dtype) == "geometry":
            a[column] = a[column].to_wkb()
            b[column] = b[column].to_wkb()
    if not a.equals(b):
        differing = [c for c in a.columns if not a[c].equals(b[c])]
        return False, f"values differ in columns {differing}"
    if getattr(a, "crs", None) != getattr(b, "crs", None):
        return False, "crs differs"
    return True, "identical"


def load(cache, stage):
    path, key, _entry = resolve_stage(cache, stage)
    with open(path, "rb") as handle:
        return pickle.load(handle), key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--old-cache", required=True)
    parser.add_argument("--new-cache", required=True)
    parser.add_argument("--old-output", required=True)
    parser.add_argument("--new-output", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    result = {"stages": {}, "ok": True}
    for stage in STAGES:
        old, old_key = load(args.old_cache, stage)
        new, new_key = load(args.new_cache, stage)
        olds = old if isinstance(old, tuple) else (old,)
        news = new if isinstance(new, tuple) else (new,)
        parts = []
        for i, (a, b) in enumerate(zip(olds, news)):
            same, reason = frame_identical(a, b)
            parts.append({"part": i, "identical": same, "reason": reason, "rows": [int(len(a)), int(len(b))]})
            result["ok"] &= same
        result["stages"][stage] = {"old_key": old_key, "new_key": new_key, "parts": parts,
                                   "same_hash": old_key == new_key}
        if len(olds) != len(news):
            result["ok"] = False
        old_dir = os.path.join(args.old_cache, old_key + ".cache")
        new_dir = os.path.join(args.new_cache, new_key + ".cache")
        if os.path.isdir(old_dir) or os.path.isdir(new_dir):
            od = directory_digests(old_dir) if os.path.isdir(old_dir) else {}
            nd = directory_digests(new_dir) if os.path.isdir(new_dir) else {}
            same_dir = od == nd
            result["stages"][stage]["stage_folder_identical"] = same_dir
            result["stages"][stage]["stage_folder_files"] = sorted(nd)
            result["ok"] &= same_dir

    od = directory_digests(args.old_output)
    nd = directory_digests(args.new_output)
    files = sorted(set(od) | set(nd))
    result["output_files"] = {f: {"old": od.get(f), "new": nd.get(f), "identical": od.get(f) == nd.get(f)} for f in files}
    result["output_identical"] = all(v["identical"] for v in result["output_files"].values())
    result["ok"] &= result["output_identical"]

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print(json.dumps({k: v for k, v in result.items() if k != "output_files"}, indent=2, sort_keys=True))
    print("output files:", len(files), "identical:", result["output_identical"])
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    sys.exit(main())
